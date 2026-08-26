from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from rf_platform.backend.db import models
from rf_platform.contracts.analysis import (
    AnalysisResult,
    ModelIdentity,
    TechnologyFinding,
)
from rf_platform.worker.correlation import correlate_result
from rf_platform.worker.rfgpt.local import SEMANTIC_INCONSISTENCY


class _ScalarResult:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value


class _FakeCorrelationSession:
    def __init__(self, analysis_id: str) -> None:
        self.analysis_id = analysis_id
        self.events: list[models.Event] = []
        self.evidence_rows: list[models.EventEvidence] = []
        self.alerts: list[models.AlertRow] = []

    async def execute(self, _statement: object) -> _ScalarResult:
        for evidence in self.evidence_rows:
            if evidence.target_type == "analysis" and evidence.target_id == self.analysis_id:
                event = next(
                    (item for item in self.events if item.event_id == evidence.event_id),
                    None,
                )
                return _ScalarResult(event)
        return _ScalarResult(None)

    def add(self, row: object) -> None:
        if isinstance(row, models.Event):
            self.events.append(row)
        elif isinstance(row, models.EventEvidence):
            self.evidence_rows.append(row)
        elif isinstance(row, models.AlertRow):
            self.alerts.append(row)
        else:  # pragma: no cover - defensive test helper
            raise AssertionError(f"unexpected row: {row!r}")

    async def flush(self) -> None:
        return None


def _capture() -> Any:
    return SimpleNamespace(
        capture_id="capture-1",
        sensor_id="laptop-b210-001",
        correlation_id="corr-1",
        started_at_utc=datetime(2026, 8, 26, tzinfo=UTC),
        ended_at_utc=datetime(2026, 8, 26, 0, 0, 1, tzinfo=UTC),
    )


def _result(
    *,
    analysis_id: str = "analysis-1",
    status: str = "succeeded",
    parser_valid: bool = True,
    adapter: str = "vllm",
    version: str = "Qwen2.5-VL-7B-rfa-wtr-v2-joint",
    technologies: list[TechnologyFinding] | None = None,
    overall_assessment: str = "RF technology observation.",
    quality_flags: list[str] | None = None,
) -> AnalysisResult:
    now = datetime(2026, 8, 26, tzinfo=UTC)
    return AnalysisResult(
        analysis_id=analysis_id,
        capture_id="capture-1",
        model=ModelIdentity(
            name="rfgpt",
            version=version,
            adapter=adapter,
            prompt_version="technology-detection-primary-v4",
        ),
        status=cast(Any, status),
        started_at_utc=now,
        completed_at_utc=now,
        latency_ms=1,
        technologies=technologies
        if technologies is not None
        else [
            TechnologyFinding(
                label="chirp",
                model_score=None,
                observation="Unverified RF observation.",
                evidence=["capture_id:capture-1"],
            )
        ],
        signals=[],
        overall_assessment=overall_assessment,
        quality_flags=quality_flags or [],
        parser_valid=parser_valid,
        raw_response="{}",
        preprocessing_version="atheer-hann-v1",
        inference_parameters={},
    )


@pytest.mark.asyncio
async def test_correlation_rejects_parser_failed_results() -> None:
    result = _result(status="parser_failed", parser_valid=False, quality_flags=["parser_failed"])
    session = _FakeCorrelationSession(result.analysis_id)

    event = await correlate_result(cast(Any, session), cast(Any, _capture()), result)

    assert event is None
    assert session.events == []
    assert session.alerts == []


@pytest.mark.asyncio
async def test_correlation_rejects_parser_invalid_results() -> None:
    result = _result(parser_valid=False)
    session = _FakeCorrelationSession(result.analysis_id)

    event = await correlate_result(cast(Any, session), cast(Any, _capture()), result)

    assert event is None
    assert session.events == []
    assert session.alerts == []


@pytest.mark.asyncio
async def test_correlation_rejects_no_signal_or_semantically_inconsistent_results() -> None:
    for result in [
        _result(overall_assessment="no signal present"),
        _result(quality_flags=["no_signal"]),
        _result(quality_flags=["parser_failed", SEMANTIC_INCONSISTENCY]),
    ]:
        session = _FakeCorrelationSession(result.analysis_id)
        event = await correlate_result(cast(Any, session), cast(Any, _capture()), result)
        assert event is None
        assert session.events == []
        assert session.alerts == []


@pytest.mark.asyncio
async def test_real_vllm_event_summary_uses_model_provenance_and_unverified_wording() -> None:
    result = _result()
    session = _FakeCorrelationSession(result.analysis_id)

    event = await correlate_result(cast(Any, session), cast(Any, _capture()), result)

    assert event is not None
    assert "Unverified rfgpt model observation" in event.summary
    assert "adapter=vllm" in event.summary
    assert "version=Qwen2.5-VL-7B-rfa-wtr-v2-joint" in event.summary
    assert "chirp on sensor laptop-b210-001" in event.summary
    assert "Mock RF-GPT" not in event.summary
    assert session.alerts[0].reason == (
        "An unverified structured model finding triggered the configured "
        "technology-observation rule."
    )


@pytest.mark.asyncio
async def test_mock_event_summary_is_derived_from_adapter_identity() -> None:
    result = _result(adapter="mock", version="mock-v1")
    session = _FakeCorrelationSession(result.analysis_id)

    event = await correlate_result(cast(Any, session), cast(Any, _capture()), result)

    assert event is not None
    assert "adapter=mock" in event.summary
    assert "version=mock-v1" in event.summary
    assert "Mock RF-GPT" not in event.summary


@pytest.mark.asyncio
async def test_event_alert_and_evidence_correlation_is_idempotent_for_analysis() -> None:
    result = _result(analysis_id="analysis-idempotent")
    session = _FakeCorrelationSession(result.analysis_id)

    first = await correlate_result(cast(Any, session), cast(Any, _capture()), result)
    second = await correlate_result(cast(Any, session), cast(Any, _capture()), result)

    assert first is not None
    assert second is first
    assert len(session.events) == 1
    assert len(session.alerts) == 1
    assert len(session.evidence_rows) == 3
    assert [(row.target_type, row.target_id) for row in session.evidence_rows] == [
        ("sensor", "laptop-b210-001"),
        ("capture", "capture-1"),
        ("analysis", "analysis-idempotent"),
    ]
