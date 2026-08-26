from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from rf_platform.backend.db import models
from rf_platform.common.config import Settings
from rf_platform.worker.consumer import PROMPT_VERSION, WorkerProcessor
from rf_platform.worker.main import run_worker


class _ScalarResult:
    def scalar_one_or_none(self) -> None:
        return None


class _FakeSession:
    def __init__(self, job: Any, capture: Any | None = None) -> None:
        self.job = job
        self.capture = capture
        self.commits = 0

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, model: type[object], _key: str) -> Any | None:
        if model is models.AnalysisJob:
            return self.job
        if model is models.Capture:
            return self.capture
        return None

    async def execute(self, _statement: object) -> _ScalarResult:
        return _ScalarResult()

    async def commit(self) -> None:
        self.commits += 1

    def add(self, _row: object) -> None:
        return None

    async def flush(self) -> None:
        return None


class _FakeSessionMaker:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    def __call__(self) -> _FakeSession:
        return self.session


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, subject: str, payload: dict[str, Any]) -> None:
        self.published.append((subject, payload))


class _CountingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, _request: object) -> object:
        self.calls += 1
        raise AssertionError("adapter should not be called by this test")


def _job(
    *,
    model_name: str = "rfgpt",
    model_version: str,
    prompt_version: str = PROMPT_VERSION,
) -> Any:
    return SimpleNamespace(
        job_id="job-1",
        capture_id="capture-1",
        model_name=model_name,
        model_version=model_version,
        prompt_version=prompt_version,
        status="pending",
        attempt_count=0,
        available_at_utc=None,
        error_category=None,
        error_message=None,
        started_at_utc=None,
        completed_at_utc=None,
        updated_at_utc=None,
    )


@pytest.mark.asyncio
async def test_database_readiness_failure_occurs_before_nats_subscription(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import rf_platform.worker.main as worker_main

    class FakeEngine:
        def __init__(self) -> None:
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    class FakeNatsEventBus:
        created = 0
        pull_calls = 0

        def __init__(self, _settings: Settings) -> None:
            FakeNatsEventBus.created += 1

        async def connect(self) -> None:
            raise AssertionError("NATS connect must not happen before database readiness")

        async def pull_messages(self, *_args: object, **_kwargs: object) -> list[object]:
            FakeNatsEventBus.pull_calls += 1
            return []

        async def close(self) -> None:
            return None

    engine = FakeEngine()

    async def fail_readiness(_sessionmaker: object) -> None:
        raise RuntimeError("InvalidPasswordError")

    monkeypatch.setattr(worker_main, "get_settings", lambda: Settings(artifact_root=tmp_path))
    monkeypatch.setattr(worker_main, "create_engine", lambda _settings: engine)
    monkeypatch.setattr(worker_main, "create_sessionmaker", lambda _engine: object())
    monkeypatch.setattr(worker_main, "check_database_ready", fail_readiness)
    monkeypatch.setattr(worker_main, "NatsEventBus", FakeNatsEventBus)

    with pytest.raises(RuntimeError, match="database readiness check failed"):
        await run_worker(once=True, idle_timeout_seconds=0)

    assert engine.disposed is True
    assert FakeNatsEventBus.created == 0
    assert FakeNatsEventBus.pull_calls == 0


@pytest.mark.asyncio
async def test_mismatching_job_model_identity_fails_without_invoking_adapter(
    tmp_path: Path,
) -> None:
    settings = Settings(
        sensor_token="token",
        artifact_root=tmp_path,
        rfgpt_adapter="vllm",
        rfgpt_model_name="rfgpt",
        rfgpt_model_version="Qwen2.5-VL-7B-rfa-wtr-v2-joint",
    )
    job = _job(model_version="mock-v1")
    adapter = _CountingAdapter()
    processor = WorkerProcessor(
        settings,
        cast(Any, _FakeSessionMaker(_FakeSession(job))),
        store=cast(Any, SimpleNamespace()),
        bus=cast(Any, _FakeBus()),
        adapter=cast(Any, adapter),
    )

    outcome = await processor.process_payload({"job_id": job.job_id, "capture_id": job.capture_id})

    assert outcome == "failed"
    assert job.status == "failed"
    assert job.error_category == "model_configuration_mismatch"
    assert "mock-v1" in job.error_message
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_matching_job_model_identity_proceeds_past_mismatch_guard(tmp_path: Path) -> None:
    settings = Settings(
        sensor_token="token",
        artifact_root=tmp_path,
        rfgpt_adapter="vllm",
        rfgpt_model_name="rfgpt",
        rfgpt_model_version="Qwen2.5-VL-7B-rfa-wtr-v2-joint",
    )
    job = _job(model_version="Qwen2.5-VL-7B-rfa-wtr-v2-joint")
    adapter = _CountingAdapter()
    processor = WorkerProcessor(
        settings,
        cast(Any, _FakeSessionMaker(_FakeSession(job, capture=None))),
        store=cast(Any, SimpleNamespace()),
        bus=cast(Any, _FakeBus()),
        adapter=cast(Any, adapter),
    )

    outcome = await processor.process_payload({"job_id": job.job_id, "capture_id": job.capture_id})

    assert outcome == "failed"
    assert job.error_category == "permanent_input_failure"
    assert job.error_message == "capture not found"
    assert adapter.calls == 0
