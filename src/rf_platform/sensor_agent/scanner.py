from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from rf_platform.common.config import Settings
from rf_platform.common.ids import new_id
from rf_platform.common.logging import get_logger
from rf_platform.common.scan_profiles import ScanPlan, ScanProfileError, load_plan_from_settings
from rf_platform.sensor_agent.service import SensorService

logger = get_logger("rf_platform.sensor.scan")
SleepFn = Callable[[float], Awaitable[None]]


class ScanBackpressureError(RuntimeError):
    pass


def _object_to_int(value: object, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    if isinstance(value, bytes):
        try:
            return int(value.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            return default
    try:
        return value.__index__()  # type: ignore[attr-defined]
    except AttributeError:
        return default


class B210ScanRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        service: SensorService | None = None,
        plan: ScanPlan | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        if settings.sensor_adapter != "b210":
            raise RuntimeError("RF_SENSOR_ADAPTER=b210 is required for B210 scanning")
        self.settings = settings
        self.service = service or SensorService(settings)
        self.plan = plan or load_plan_from_settings(settings)
        self.sleep = sleep
        self.stop_requested = asyncio.Event()
        self.cycle_index = 0

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, self.request_stop)
            except NotImplementedError:  # pragma: no cover - platform specific
                signal.signal(signum, lambda _sig, _frame: self.request_stop())

    def request_stop(self) -> None:
        self.stop_requested.set()
        logger.info("scan_stop_requested", sensor_id=self.settings.sensor_id)

    async def run(
        self, *, one_cycle: bool = False, max_slices: int | None = None
    ) -> dict[str, Any]:
        if not self.plan.slices:
            logger.warning("scan_plan_empty", sensor_id=self.settings.sensor_id)
            return {"cycles_completed": 0, "captures_attempted": 0, "captures_uploaded": 0}
        await self.service.register()
        totals = {"cycles_completed": 0, "captures_attempted": 0, "captures_uploaded": 0}
        while not self.stop_requested.is_set():
            cycle = await self._run_cycle(max_slices=max_slices)
            totals["captures_attempted"] += cycle["captures_attempted"]
            totals["captures_uploaded"] += cycle["captures_uploaded"]
            totals["cycles_completed"] += 1
            self.cycle_index += 1
            if one_cycle or self.stop_requested.is_set():
                break
            if self.settings.scan_cycle_interval_seconds:
                await self.sleep(self.settings.scan_cycle_interval_seconds)
        return totals

    async def _run_cycle(self, *, max_slices: int | None = None) -> dict[str, int]:
        cycle_id = f"scan-{new_id()}"
        limit = max_slices or self.settings.scan_max_slices_per_cycle
        slices = list(self.plan.slices[:limit] if limit else self.plan.slices)
        attempted = 0
        uploaded = 0
        logger.info(
            "scan_cycle_started",
            sensor_id=self.settings.sensor_id,
            cycle_id=cycle_id,
            slice_count=len(slices),
            profile_set_id=self.plan.profile_set.profile_set_id,
        )
        for scan_slice in slices:
            if self.stop_requested.is_set():
                break
            await self._wait_for_backpressure_clear()
            await self._wait_for_local_spool_clear()
            if self.settings.scan_retune_settle_seconds:
                await self.sleep(self.settings.scan_retune_settle_seconds)
            profile = scan_slice.to_capture_profile(self.plan.profile_set)
            try:
                logger.info(
                    "scan_slice_capture_start",
                    sensor_id=self.settings.sensor_id,
                    cycle_id=cycle_id,
                    profile_id=scan_slice.profile_id,
                    slice_index=scan_slice.slice_index,
                    center_frequency_hz=scan_slice.center_frequency_hz,
                    bandwidth_hz=scan_slice.capture_bandwidth_hz,
                )
                item = await self.service.capture_profile_to_spool(
                    profile, validate_capabilities=True
                )
                attempted += 1
                uploads = await self.service.upload_pending()
                uploaded += sum(
                    1 for result in uploads if result.get("capture_id") == item.envelope.capture_id
                )
                logger.info(
                    "scan_slice_uploaded",
                    sensor_id=self.settings.sensor_id,
                    cycle_id=cycle_id,
                    capture_id=item.envelope.capture_id,
                    profile_id=scan_slice.profile_id,
                    slice_index=scan_slice.slice_index,
                    uploaded_count=len(uploads),
                )
                await self.service.send_heartbeat()
            except Exception as exc:
                logger.warning(
                    "scan_slice_failed",
                    sensor_id=self.settings.sensor_id,
                    cycle_id=cycle_id,
                    profile_id=scan_slice.profile_id,
                    slice_index=scan_slice.slice_index,
                    error=exc.__class__.__name__,
                    message=str(exc),
                )
                await self.sleep(self.settings.scan_failure_cooldown_seconds)
        logger.info(
            "scan_cycle_completed",
            sensor_id=self.settings.sensor_id,
            cycle_id=cycle_id,
            captures_attempted=attempted,
            captures_uploaded=uploaded,
        )
        return {"captures_attempted": attempted, "captures_uploaded": uploaded}

    async def _wait_for_backpressure_clear(self) -> None:
        while not self.stop_requested.is_set():
            try:
                backlog = await self.service.job_backlog()
            except httpx.HTTPError as exc:
                logger.warning(
                    "scan_backpressure_api_unavailable",
                    sensor_id=self.settings.sensor_id,
                    error=exc.__class__.__name__,
                )
                await self.sleep(self.settings.scan_failure_cooldown_seconds)
                continue
            inflight = _object_to_int(backlog.get("inflight", 0))
            if inflight < self.settings.scan_max_inflight_jobs:
                return
            logger.info(
                "scan_backpressure_paused",
                sensor_id=self.settings.sensor_id,
                inflight=inflight,
                max_inflight=self.settings.scan_max_inflight_jobs,
            )
            await self.sleep(self.settings.scan_backpressure_poll_seconds)

    async def _wait_for_local_spool_clear(self) -> None:
        while not self.stop_requested.is_set():
            pending = _object_to_int(self.service.spool.status().get("pending_items", 0))
            if pending == 0:
                return
            try:
                await self.service.upload_pending()
            except Exception as exc:
                logger.warning(
                    "scan_spool_upload_wait",
                    sensor_id=self.settings.sensor_id,
                    pending_items=pending,
                    error=exc.__class__.__name__,
                )
                await self.sleep(self.settings.scan_failure_cooldown_seconds)
                continue


def dry_run_plan(
    settings: Settings, *, max_slices: int | None = None, verbose: bool = False
) -> dict[str, Any]:
    try:
        plan = load_plan_from_settings(settings, max_slices=max_slices)
    except ScanProfileError:
        raise
    return plan.as_dict(retune_settle_seconds=settings.scan_retune_settle_seconds, verbose=verbose)
