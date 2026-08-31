from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from rf_platform.common.config import Settings
from rf_platform.common.scan_profiles import build_scan_plan, load_scan_profile_set
from rf_platform.sensor_agent.scanner import B210ScanRunner

CATALOGUE = Path("config/scan-profiles/uae-b210-sub6-v1.toml")


class _FakeSpool:
    def __init__(self, pending_sequence: list[int] | None = None) -> None:
        self.pending_sequence = list(pending_sequence or [0])
        self.calls = 0

    def status(self) -> dict[str, int]:
        self.calls += 1
        if self.pending_sequence:
            return {"pending_items": self.pending_sequence.pop(0)}
        return {"pending_items": 0}


class _FakeService:
    def __init__(self, *, backlog: list[Any] | None = None, fail_capture: bool = False) -> None:
        self.spool = _FakeSpool()
        self.backlog = list(backlog or [{"inflight": 0}])
        self.fail_capture = fail_capture
        self.registered = 0
        self.heartbeat_count = 0
        self.capture_profiles: list[Any] = []
        self.upload_count = 0
        self.in_capture = False
        self.max_concurrent = 0
        self._next_capture_index = 0

    async def register(self) -> dict[str, object]:
        self.registered += 1
        return {"ok": True}

    async def job_backlog(self) -> dict[str, object]:
        if not self.backlog:
            return {"inflight": 0}
        item = self.backlog.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def capture_profile_to_spool(
        self, profile: Any, *, validate_capabilities: bool = True
    ) -> Any:
        if self.in_capture:
            raise AssertionError("scanner attempted concurrent capture")
        self.in_capture = True
        self.max_concurrent = max(self.max_concurrent, 1)
        try:
            if self.fail_capture:
                raise RuntimeError("hardware failed")
            self.capture_profiles.append(profile)
            capture_id = f"capture-{self._next_capture_index}"
            self._next_capture_index += 1
            return SimpleNamespace(envelope=SimpleNamespace(capture_id=capture_id))
        finally:
            self.in_capture = False

    async def upload_pending(self, delete_after_success: bool = True) -> list[dict[str, object]]:
        self.upload_count += 1
        return [{"capture_id": f"capture-{index}"} for index in range(self._next_capture_index)]

    async def send_heartbeat(self) -> dict[str, object]:
        self.heartbeat_count += 1
        return {"ok": True}


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "sensor_adapter": "b210",
        "sensor_id": "laptop-b210-001",
        "scan_profile_config": CATALOGUE,
        "scan_enabled_profile_ids": "uae_shared_2400_2483_5",
        "scan_retune_settle_seconds": 0.1,
        "scan_backpressure_poll_seconds": 0.2,
        "scan_failure_cooldown_seconds": 0.3,
        "scan_max_inflight_jobs": 1,
    }
    values.update(overrides)
    return Settings(**values)


def _plan(max_slices: int | None = 3):
    profile_set = load_scan_profile_set(CATALOGUE)
    return build_scan_plan(
        profile_set,
        enabled_profile_ids="uae_shared_2400_2483_5",
        max_slices=max_slices,
    )


@pytest.mark.asyncio
async def test_scanner_runs_one_cycle_in_deterministic_slice_order_and_one_capture_at_time() -> (
    None
):
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    service = _FakeService()
    runner = B210ScanRunner(_settings(), service=cast(Any, service), plan=_plan(3), sleep=sleep)

    totals = await runner.run(one_cycle=True)

    assert totals == {"cycles_completed": 1, "captures_attempted": 3, "captures_uploaded": 3}
    assert service.registered == 1
    assert service.max_concurrent == 1
    assert [profile.profile_id for profile in service.capture_profiles] == [
        "uae_shared_2400_2483_5",
        "uae_shared_2400_2483_5",
        "uae_shared_2400_2483_5",
    ]
    assert [profile.radio.center_frequency_hz for profile in service.capture_profiles] == [
        2_410_000_000,
        2_428_000_000,
        2_446_000_000,
    ]
    assert sleeps == [0.1, 0.1, 0.1]


@pytest.mark.asyncio
async def test_scanner_max_slice_limit_and_raw_iq_default() -> None:
    service = _FakeService()
    runner = B210ScanRunner(
        _settings(scan_max_slices_per_cycle=2), service=cast(Any, service), plan=_plan(5)
    )

    totals = await runner.run(one_cycle=True)

    assert totals["captures_attempted"] == 2
    assert len(service.capture_profiles) == 2
    assert all(profile.retention.upload_iq == "never" for profile in service.capture_profiles)
    assert _settings().b210_persist_raw_iq is False


@pytest.mark.asyncio
async def test_scanner_pauses_for_sensor_scoped_backpressure_then_resumes() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    service = _FakeService(backlog=[{"inflight": 1}, {"inflight": 0}, {"inflight": 0}])
    runner = B210ScanRunner(_settings(), service=cast(Any, service), plan=_plan(1), sleep=sleep)

    totals = await runner.run(one_cycle=True)

    assert totals["captures_attempted"] == 1
    assert sleeps[:2] == [0.2, 0.1]


@pytest.mark.asyncio
async def test_scanner_api_unavailable_waits_without_capturing_until_backlog_clears() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    service = _FakeService(
        backlog=[httpx.ConnectError("api down"), {"inflight": 0}],
    )
    runner = B210ScanRunner(_settings(), service=cast(Any, service), plan=_plan(1), sleep=sleep)

    totals = await runner.run(one_cycle=True)

    assert totals["captures_attempted"] == 1
    assert sleeps[:2] == [0.3, 0.1]


@pytest.mark.asyncio
async def test_scanner_hardware_failure_uses_bounded_cooldown_and_no_hot_retry() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    service = _FakeService(fail_capture=True)
    runner = B210ScanRunner(_settings(), service=cast(Any, service), plan=_plan(2), sleep=sleep)

    totals = await runner.run(one_cycle=True)

    assert totals == {"cycles_completed": 1, "captures_attempted": 0, "captures_uploaded": 0}
    assert sleeps == [0.1, 0.3, 0.1, 0.3]


@pytest.mark.asyncio
async def test_scanner_graceful_stop_finishes_current_cycle_iteration() -> None:
    service = _FakeService()
    runner = B210ScanRunner(_settings(), service=cast(Any, service), plan=_plan(3))
    original = service.capture_profile_to_spool

    async def capture_and_stop(profile: Any, *, validate_capabilities: bool = True) -> Any:
        result = await original(profile, validate_capabilities=validate_capabilities)
        runner.request_stop()
        return result

    service.capture_profile_to_spool = capture_and_stop  # type: ignore[method-assign]

    totals = await runner.run(one_cycle=False)

    assert totals["cycles_completed"] == 1
    assert totals["captures_attempted"] == 1
    assert len(service.capture_profiles) == 1


def test_scanner_refuses_non_b210_adapter() -> None:
    with pytest.raises(RuntimeError, match="RF_SENSOR_ADAPTER=b210"):
        B210ScanRunner(
            _settings(sensor_adapter="simulated"), service=cast(Any, _FakeService()), plan=_plan(1)
        )


@pytest.mark.asyncio
async def test_scanner_passes_dry_run_slice_sample_count_to_capture_profile() -> None:
    service = _FakeService()
    plan = _plan(1)
    runner = B210ScanRunner(_settings(), service=cast(Any, service), plan=plan)

    await runner.run(one_cycle=True)

    assert plan.slices[0].sample_count == 1_048_576
    assert service.capture_profiles[0].capture.sample_count == plan.slices[0].sample_count
    assert service.capture_profiles[0].capture.duration_ms == 53
