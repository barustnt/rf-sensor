from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.common.band_compatibility import (
    BAND_INCOMPATIBLE,
    check_findings_band_compatibility,
    profile_presentation_eligible,
    scan_profile_for_capture,
)
from rf_platform.common.config import Settings
from rf_platform.common.scan_profiles import (
    ScanProfileSet,
    build_scan_plan,
    load_scan_profile_set,
)
from rf_platform.common.time import ensure_utc, utc_now
from rf_platform.worker.semantic_markers import SEMANTIC_INCONSISTENCY, has_no_signal_marker

RANGE_TOLERANCE_HZ = 2_000
PENDING_JOB_STATUSES = {"pending", "running"}


def merge_ranges(
    ranges: list[tuple[int, int]], *, tolerance_hz: int = RANGE_TOLERANCE_HZ
) -> list[tuple[int, int]]:
    cleaned = sorted((int(start), int(end)) for start, end in ranges if end > start)
    if not cleaned:
        return []
    merged = [cleaned[0]]
    for start, end in cleaned[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + tolerance_hz:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def coverage_width_hz(ranges: list[tuple[int, int]]) -> int:
    return sum(end - start for start, end in merge_ranges(ranges))


def capture_frequency_range(
    capture: models.Capture, profile_set: ScanProfileSet | None = None
) -> tuple[int, int] | None:
    radio = getattr(capture, "radio", None) or {}
    raw_hardware = radio.get("hardware")
    hardware: dict[str, Any] = raw_hardware if isinstance(raw_hardware, dict) else {}
    center = hardware.get("actual_center_frequency_hz") or radio.get("center_frequency_hz")
    bandwidth = hardware.get("actual_bandwidth_hz") or radio.get("bandwidth_hz")
    if center is None or bandwidth is None:
        return None
    try:
        center_hz = int(round(float(center)))
        bandwidth_hz = int(round(float(bandwidth)))
    except (TypeError, ValueError):
        return None
    if center_hz <= 0 or bandwidth_hz <= 0:
        return None
    half = bandwidth_hz // 2
    start = center_hz - half
    end = center_hz + half
    profile = scan_profile_for_capture(profile_set, capture.profile_id) if profile_set else None
    if profile is not None:
        start = max(start, profile.start_frequency_hz)
        end = min(end, profile.end_frequency_hz)
    return (start, end) if end > start else None


def accepted_run_for_presentation(
    run: models.ModelRun,
    capture: models.Capture,
    sensor: models.Sensor,
    job: models.AnalysisJob,
    profile_set: ScanProfileSet | None = None,
) -> bool:
    if sensor.adapter == "simulated":
        return False
    if run.adapter == "mock" or run.model_version == "mock-v1":
        return False
    if run.status != "succeeded" or not run.parser_valid:
        return False
    if job.status in {"failed", "deadletter"}:
        return False
    if job.error_category in {
        "model_configuration_mismatch",
        SEMANTIC_INCONSISTENCY,
        BAND_INCOMPATIBLE,
    }:
        return False
    structured = run.structured_result if isinstance(run.structured_result, dict) else {}
    technologies = _list_of_dicts(structured.get("technologies"))
    signals = _list_of_dicts(structured.get("signals"))
    overall = str(structured.get("overall_assessment") or "")
    quality_flags = [
        str(item) for item in structured.get("quality_flags", []) if isinstance(item, str)
    ]
    if SEMANTIC_INCONSISTENCY in quality_flags or BAND_INCOMPATIBLE in quality_flags:
        return False
    if (technologies or signals) and has_no_signal_marker(overall, quality_flags):
        return False
    frequency_range = capture_frequency_range(capture, profile_set)
    compatibility = check_findings_band_compatibility(
        technologies=technologies,
        signals=signals,
        frequency_range_hz=frequency_range,
        profile_id=capture.profile_id,
    )
    if compatibility.incompatible:
        return False
    profile = scan_profile_for_capture(profile_set, capture.profile_id) if profile_set else None
    return profile_presentation_eligible(profile, capture.profile_id)


def analysis_state_for_capture(
    run: models.ModelRun | None,
    job: models.AnalysisJob | None,
    accepted_for_presentation: bool,
) -> str:
    if job is None:
        return "analysis_pending"
    if job.status in PENDING_JOB_STATUSES:
        return "analysis_pending"
    if run is None:
        return "analysis_rejected" if job.status in {"failed", "deadletter"} else "analysis_pending"
    if accepted_for_presentation:
        return "accepted_observation"
    if run.status == "succeeded" and run.parser_valid:
        return "experimental_identification"
    return "analysis_rejected"


async def load_profile_set_from_settings(settings: Settings) -> ScanProfileSet:
    return load_scan_profile_set(
        settings.scan_profile_config, expected_profile_set=settings.scan_profile_set
    )


async def coverage_report(
    session: AsyncSession,
    settings: Settings,
    *,
    start_utc: Any | None = None,
    end_utc: Any | None = None,
    sensor_id: str | None = None,
) -> dict[str, Any]:
    end = ensure_utc(end_utc) if end_utc is not None else utc_now()
    start = ensure_utc(start_utc) if start_utc is not None else end - timedelta(hours=1)
    profile_set = await load_profile_set_from_settings(settings)
    plan = build_scan_plan(profile_set, enabled_profile_ids=settings.scan_enabled_profile_ids)
    stmt = (
        select(models.Capture, models.Sensor)
        .join(models.Sensor, models.Sensor.sensor_id == models.Capture.sensor_id)
        .where(models.Capture.started_at_utc >= start, models.Capture.started_at_utc < end)
    )
    if sensor_id:
        stmt = stmt.where(models.Capture.sensor_id == sensor_id)
    rows = list((await session.execute(stmt)).all())
    captures = [(capture, sensor) for capture, sensor in rows if sensor.adapter != "simulated"]
    capture_ids = [capture.capture_id for capture, _sensor in captures]
    runs_by_capture: dict[str, models.ModelRun] = {}
    jobs_by_capture: dict[str, models.AnalysisJob] = {}
    if capture_ids:
        job_rows = list(
            (
                await session.execute(
                    select(models.AnalysisJob).where(models.AnalysisJob.capture_id.in_(capture_ids))
                )
            ).scalars()
        )
        jobs_by_capture = {job.capture_id: job for job in job_rows}
        run_rows = list(
            (
                await session.execute(
                    select(models.ModelRun).where(models.ModelRun.capture_id.in_(capture_ids))
                )
            ).scalars()
        )
        runs_by_capture = {run.capture_id: run for run in run_rows}

    profile_rows: dict[str, dict[str, Any]] = {}
    captured_by_profile: dict[str, list[tuple[int, int]]] = defaultdict(list)
    accepted_by_profile: dict[str, list[tuple[int, int]]] = defaultdict(list)
    pending_by_profile: dict[str, list[tuple[int, int]]] = defaultdict(list)
    rejected_by_profile: dict[str, list[tuple[int, int]]] = defaultdict(list)
    experimental_by_profile: dict[str, list[tuple[int, int]]] = defaultdict(list)
    latest_capture_by_profile: dict[str, str] = {}
    latest_accepted_by_profile: dict[str, str] = {}

    for capture, sensor in captures:
        frequency_range = capture_frequency_range(capture, profile_set)
        if frequency_range is None:
            continue
        profile_id = capture.profile_id
        captured_by_profile[profile_id].append(frequency_range)
        latest_capture_by_profile[profile_id] = capture.started_at_utc.isoformat()
        run = runs_by_capture.get(capture.capture_id)
        job = jobs_by_capture.get(capture.capture_id)
        accepted = bool(
            run and job and accepted_run_for_presentation(run, capture, sensor, job, profile_set)
        )
        state = analysis_state_for_capture(run, job, accepted)
        if state == "accepted_observation":
            accepted_by_profile[profile_id].append(frequency_range)
            latest_accepted_by_profile[profile_id] = run.completed_at_utc.isoformat() if run else ""
        elif state == "analysis_pending":
            pending_by_profile[profile_id].append(frequency_range)
        elif state == "experimental_identification":
            experimental_by_profile[profile_id].append(frequency_range)
        else:
            rejected_by_profile[profile_id].append(frequency_range)

    plan_slice_counts = plan.slice_counts()
    all_profile_ids = (
        set(plan_slice_counts)
        | set(captured_by_profile)
        | {p.profile_id for p in profile_set.profiles}
    )
    for profile_id in sorted(all_profile_ids):
        profile = scan_profile_for_capture(profile_set, profile_id)
        required_ranges = [
            (item.coverage_start_hz, item.coverage_end_hz)
            for item in plan.slices
            if item.profile_id == profile_id
        ]
        captured = merge_ranges(captured_by_profile.get(profile_id, []))
        accepted_ranges = merge_ranges(accepted_by_profile.get(profile_id, []))
        missing = missing_ranges(required_ranges, captured)
        nominal_width = (
            (profile.end_frequency_hz - profile.start_frequency_hz)
            if profile
            else coverage_width_hz(required_ranges)
        )
        captured_width = coverage_width_hz(captured)
        accepted_width = coverage_width_hz(accepted_ranges)
        profile_rows[profile_id] = {
            "profile_id": profile_id,
            "display_name": profile.display_name if profile else profile_id,
            "qualification_state": profile.qualification_state if profile else "legacy",
            "presentation_policy": profile.presentation_policy
            if profile
            else "presentation_eligible",
            "presentation_eligible": profile_presentation_eligible(profile, profile_id),
            "candidate_families": list(profile.candidate_families) if profile else [],
            "required_slice_count": plan_slice_counts.get(profile_id, 0),
            "captured_range_count": len(captured),
            "accepted_range_count": len(accepted_ranges),
            "captured_coverage_percent": _percent(captured_width, nominal_width),
            "accepted_coverage_percent": _percent(accepted_width, nominal_width),
            "hardware_captured_ranges_hz": [list(item) for item in captured],
            "accepted_observation_ranges_hz": [list(item) for item in accepted_ranges],
            "analysis_pending_ranges_hz": [
                list(item) for item in merge_ranges(pending_by_profile.get(profile_id, []))
            ],
            "analysis_rejected_ranges_hz": [
                list(item) for item in merge_ranges(rejected_by_profile.get(profile_id, []))
            ],
            "experimental_identification_ranges_hz": [
                list(item) for item in merge_ranges(experimental_by_profile.get(profile_id, []))
            ],
            "missing_ranges_hz": [list(item) for item in missing],
            "complete_hardware_coverage": bool(required_ranges) and not missing,
            "latest_real_capture_utc": latest_capture_by_profile.get(profile_id),
            "last_accepted_analysis_utc": latest_accepted_by_profile.get(profile_id),
        }

    backlog = await sensor_job_backlog(session, sensor_id=sensor_id)
    return {
        "schema_version": "1.0",
        "profile_set_id": profile_set.profile_set_id,
        "profile_set_version": profile_set.version,
        "interval": {"start_utc": start.isoformat(), "end_utc": end.isoformat()},
        "sensor_id": sensor_id,
        "enabled_profile_ids": list(plan.enabled_profile_ids),
        "planned_slice_count": len(plan.slices),
        "profiles": list(profile_rows.values()),
        "backlog": backlog,
        "scanner_state": {
            "max_inflight_jobs": settings.scan_max_inflight_jobs,
            "backpressure_state": "paused"
            if backlog["inflight"] >= settings.scan_max_inflight_jobs
            else "clear",
        },
    }


def missing_ranges(
    required: list[tuple[int, int]], captured: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    captured = merge_ranges(captured)
    missing: list[tuple[int, int]] = []
    for start, end in required:
        cursor = start
        for cap_start, cap_end in captured:
            if cap_end < cursor + RANGE_TOLERANCE_HZ:
                continue
            if cap_start > end - RANGE_TOLERANCE_HZ:
                break
            if cap_start > cursor + RANGE_TOLERANCE_HZ:
                missing.append((cursor, min(cap_start, end)))
            cursor = max(cursor, cap_end)
            if cursor >= end - RANGE_TOLERANCE_HZ:
                break
        if cursor < end - RANGE_TOLERANCE_HZ:
            missing.append((cursor, end))
    return merge_ranges(missing)


async def sensor_job_backlog(
    session: AsyncSession, *, sensor_id: str | None = None
) -> dict[str, Any]:
    stmt = select(models.AnalysisJob.status, models.Capture.sensor_id).join(
        models.Capture, models.Capture.capture_id == models.AnalysisJob.capture_id
    )
    if sensor_id:
        stmt = stmt.where(models.Capture.sensor_id == sensor_id)
    rows = list((await session.execute(stmt)).all())
    counts: dict[str, Any] = {
        "pending": 0,
        "running": 0,
        "retry_pending": 0,
        "succeeded": 0,
        "failed": 0,
        "deadletter": 0,
    }
    for status, _sid in rows:
        key = str(status)
        if key in counts:
            counts[key] += 1
    counts["inflight"] = counts["pending"] + counts["running"] + counts["retry_pending"]
    counts["sensor_id"] = sensor_id
    return counts


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(min(100.0, max(0.0, (numerator / denominator) * 100.0)), 3)
