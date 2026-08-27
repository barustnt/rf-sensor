from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import String, cast, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.api.v1.pagination import (
    clamp_limit_offset,
    paged_response,
    parse_optional_utc,
)
from rf_platform.backend.db import models
from rf_platform.backend.dependencies import db_session, event_bus_dependency
from rf_platform.backend.services.ingestion import (
    create_outbox_event,
    job_summary,
    publish_pending_outbox,
)
from rf_platform.common.band_compatibility import BAND_INCOMPATIBLE
from rf_platform.common.broker import ANALYSIS_REQUESTED, NatsEventBus
from rf_platform.common.time import utc_now

router = APIRouter(prefix="/api/v1", tags=["analyses"])

LIMITATION_NOTICE = "Model output is an RF-GPT observation, not verified ground truth."
NON_RETRYABLE_JOB_ERROR_CATEGORIES = {"semantic_inconsistency", BAND_INCOMPATIBLE}


def _job_retry_eligible(job: models.AnalysisJob) -> bool:
    return (
        job.status in {"failed", "deadletter"}
        and job.error_category not in NON_RETRYABLE_JOB_ERROR_CATEGORIES
    )


def _run_to_dict(run: models.ModelRun) -> dict[str, object]:
    structured = run.structured_result or {}
    return {
        "analysis_id": run.analysis_id,
        "job_id": run.job_id,
        "capture_id": run.capture_id,
        "model": {
            "name": run.model_name,
            "version": run.model_version,
            "adapter": run.adapter,
            "prompt_version": run.prompt_version,
        },
        "latency_ms": run.latency_ms,
        "status": run.status,
        "structured_result": structured,
        "technologies": structured.get("technologies", []),
        "signals": structured.get("signals", []),
        "overall_assessment": structured.get("overall_assessment"),
        "quality_flags": structured.get("quality_flags", []),
        "preprocessing_version": structured.get("preprocessing_version"),
        "inference_parameters": structured.get("inference_parameters", {}),
        "raw_response": run.raw_response,
        "parser_valid": run.parser_valid,
        "started_at_utc": run.started_at_utc.isoformat(),
        "completed_at_utc": run.completed_at_utc.isoformat(),
        "limitations": [LIMITATION_NOTICE],
    }


async def _analysis_detail(session: AsyncSession, run: models.ModelRun) -> dict[str, object]:
    findings = list(
        (
            await session.execute(
                select(models.ModelFinding).where(
                    models.ModelFinding.analysis_id == run.analysis_id
                )
            )
        ).scalars()
    )
    capture = await session.get(models.Capture, run.capture_id)
    sensor = await session.get(models.Sensor, capture.sensor_id) if capture else None
    artifacts = list(
        (
            await session.execute(
                select(models.Artifact).where(models.Artifact.capture_id == run.capture_id)
            )
        ).scalars()
    )
    evidence_rows = list(
        (
            await session.execute(
                select(models.EventEvidence).where(
                    models.EventEvidence.target_type == "analysis",
                    models.EventEvidence.target_id == run.analysis_id,
                )
            )
        ).scalars()
    )
    event_ids = [row.event_id for row in evidence_rows]
    events = (
        list(
            (
                await session.execute(
                    select(models.Event).where(models.Event.event_id.in_(event_ids))
                )
            ).scalars()
        )
        if event_ids
        else []
    )
    alerts = (
        list(
            (
                await session.execute(
                    select(models.AlertRow).where(models.AlertRow.event_id.in_(event_ids))
                )
            ).scalars()
        )
        if event_ids
        else []
    )
    annotations = list(
        (
            await session.execute(
                select(models.Annotation).where(
                    models.Annotation.target_id.in_([run.analysis_id, run.capture_id])
                )
            )
        ).scalars()
    )
    payload = _run_to_dict(run)
    payload["findings"] = [
        {
            "finding_id": item.finding_id,
            "kind": item.kind,
            "label": item.label,
            "model_score": item.model_score,
            "observation": item.observation,
            "frequency_start_hz": item.frequency_start_hz,
            "frequency_end_hz": item.frequency_end_hz,
        }
        for item in findings
    ]
    payload["capture"] = (
        {
            "capture_id": capture.capture_id,
            "sensor_id": capture.sensor_id,
            "profile_id": capture.profile_id,
            "started_at_utc": capture.started_at_utc.isoformat(),
            "ended_at_utc": capture.ended_at_utc.isoformat(),
            "radio": capture.radio,
            "preprocessing": capture.preprocessing,
        }
        if capture
        else None
    )
    payload["sensor"] = (
        {
            "sensor_id": sensor.sensor_id,
            "display_name": sensor.display_name,
            "location": sensor.location,
            "adapter": sensor.adapter,
            "software_version": sensor.software_version,
        }
        if sensor
        else None
    )
    payload["artifacts"] = [
        {
            "artifact_id": item.artifact_id,
            "kind": item.kind,
            "object_key": item.object_key,
            "mime_type": item.mime_type,
            "byte_size": item.byte_size,
            "sha256": item.sha256,
            "preview_url": f"/api/v1/captures/{run.capture_id}/artifacts/{item.artifact_id}",
        }
        for item in artifacts
    ]
    payload["linked_events"] = [
        {
            "event_id": event.event_id,
            "event_kind": event.event_kind,
            "status": event.status,
            "summary": event.summary,
        }
        for event in events
    ]
    payload["linked_alerts"] = [
        {
            "alert_id": alert.alert_id,
            "event_id": alert.event_id,
            "status": alert.status,
            "reason": alert.reason,
            "created_at_utc": alert.created_at_utc.isoformat(),
        }
        for alert in alerts
    ]
    payload["annotations"] = [
        {
            "annotation_id": item.annotation_id,
            "target_type": item.target_type,
            "target_id": item.target_id,
            "label": item.label,
            "comment": item.comment,
            "actor": item.actor,
            "timestamp_utc": item.timestamp_utc.isoformat(),
        }
        for item in annotations
    ]
    payload["readable_summary"] = {
        "headline": f"{len(findings)} structured finding(s) for capture {run.capture_id}",
        "technology_labels": [item.label for item in findings if item.kind == "technology"],
        "evidence": [f"capture:{run.capture_id}", f"analysis:{run.analysis_id}"],
    }
    return payload


@router.get("/analyses")
async def list_analyses(
    limit: int = 50,
    offset: int = 0,
    capture_id: str | None = None,
    sensor_id: str | None = None,
    profile_id: str | None = None,
    location: str | None = None,
    technology: str | None = None,
    status: str | None = None,
    model_version: str | None = None,
    prompt_version: str | None = None,
    start_utc: str | None = None,
    end_utc: str | None = None,
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    limit, offset = clamp_limit_offset(limit, offset)
    stmt = select(models.ModelRun)
    if sensor_id or profile_id or location or start_utc or end_utc:
        stmt = stmt.join(models.Capture, models.Capture.capture_id == models.ModelRun.capture_id)
    if location:
        stmt = stmt.join(models.Sensor, models.Sensor.sensor_id == models.Capture.sensor_id)
    if capture_id:
        stmt = stmt.where(models.ModelRun.capture_id == capture_id)
    if sensor_id:
        stmt = stmt.where(models.Capture.sensor_id == sensor_id)
    if profile_id:
        stmt = stmt.where(models.Capture.profile_id == profile_id)
    if location:
        stmt = stmt.where(cast(models.Sensor.location, String).ilike(f"%{location}%"))
    if status:
        stmt = stmt.where(models.ModelRun.status == status)
    if model_version:
        stmt = stmt.where(models.ModelRun.model_version == model_version)
    if prompt_version:
        stmt = stmt.where(models.ModelRun.prompt_version == prompt_version)
    if start := parse_optional_utc(start_utc):
        stmt = stmt.where(models.Capture.started_at_utc >= start)
    if end := parse_optional_utc(end_utc):
        stmt = stmt.where(models.Capture.started_at_utc < end)
    if technology:
        stmt = stmt.where(
            models.ModelRun.analysis_id.in_(
                select(models.ModelFinding.analysis_id).where(
                    models.ModelFinding.label == technology
                )
            )
        )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    result = await session.execute(
        stmt.order_by(desc(models.ModelRun.completed_at_utc)).limit(limit).offset(offset)
    )
    items = [_run_to_dict(run) for run in result.scalars()]
    return paged_response(items, int(total), limit, offset)


@router.get("/analyses/{analysis_id}")
async def get_analysis(
    analysis_id: str, session: AsyncSession = Depends(db_session)
) -> dict[str, object]:
    run = await session.get(models.ModelRun, analysis_id)
    if run is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    return await _analysis_detail(session, run)


@router.post("/analyses/jobs/{job_id}/retry")
async def retry_job(
    job_id: str,
    payload: dict[str, str] | None = None,
    session: AsyncSession = Depends(db_session),
    bus: NatsEventBus = Depends(event_bus_dependency),
) -> dict[str, object]:
    payload = payload or {}
    job = await session.get(models.AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not _job_retry_eligible(job):
        raise HTTPException(status_code=409, detail="job is not retryable")
    previous_status = job.status
    actor = payload.get("actor", "operator")
    job.status = "pending"
    job.available_at_utc = utc_now()
    job.updated_at_utc = utc_now()
    job.error_category = None
    job.error_message = None
    await create_outbox_event(
        session,
        ANALYSIS_REQUESTED,
        {
            "schema_version": "1.0",
            "job_id": job.job_id,
            "capture_id": job.capture_id,
            "model_name": job.model_name,
            "model_version": job.model_version,
            "prompt_version": job.prompt_version,
        },
    )
    session.add(
        models.Annotation(
            target_type="analysis_job",
            target_id=job_id,
            label="retry_requested",
            comment=payload.get("comment"),
            actor=actor,
            timestamp_utc=utc_now(),
        )
    )
    session.add(
        models.SystemEvent(
            severity="info",
            service="backend",
            event_type="job_retry_requested",
            message=f"Retry requested for job {job_id}",
            sensor_id=None,
            correlation_id=None,
            context={"job_id": job_id, "actor": actor, "previous_status": previous_status},
            timestamp_utc=utc_now(),
        )
    )
    await session.commit()
    published = await publish_pending_outbox(session, bus)
    return {
        "status": "pending",
        "job_id": job_id,
        "previous_status": previous_status,
        "outbox_published": published,
    }


@router.get("/jobs")
async def list_jobs(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    capture_id: str | None = None,
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    limit, offset = clamp_limit_offset(limit, offset)
    stmt = select(models.AnalysisJob)
    if status:
        stmt = stmt.where(models.AnalysisJob.status == status)
    if capture_id:
        stmt = stmt.where(models.AnalysisJob.capture_id == capture_id)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = list(
        (
            await session.execute(
                stmt.order_by(desc(models.AnalysisJob.created_at_utc)).limit(limit).offset(offset)
            )
        ).scalars()
    )
    items = [
        {
            "job_id": job.job_id,
            "capture_id": job.capture_id,
            "model_name": job.model_name,
            "model_version": job.model_version,
            "prompt_version": job.prompt_version,
            "status": job.status,
            "attempt_count": job.attempt_count,
            "available_at_utc": job.available_at_utc.isoformat(),
            "error_category": job.error_category,
            "error_message": job.error_message,
            "started_at_utc": job.started_at_utc.isoformat() if job.started_at_utc else None,
            "completed_at_utc": job.completed_at_utc.isoformat() if job.completed_at_utc else None,
            "retry_eligible": _job_retry_eligible(job),
        }
        for job in rows
    ]
    return paged_response(items, int(total), limit, offset)


@router.get("/jobs/summary")
async def get_jobs_summary(session: AsyncSession = Depends(db_session)) -> dict[str, object]:
    summary = await job_summary(session)
    total = await session.execute(select(func.count()).select_from(models.AnalysisJob))
    summary["total"] = total.scalar_one()
    latency_values = list(
        (
            await session.execute(
                select(models.ModelRun.latency_ms)
                .order_by(desc(models.ModelRun.completed_at_utc))
                .limit(500)
            )
        ).scalars()
    )
    if latency_values:
        sorted_values = sorted(latency_values)
        summary["latency_ms_p50"] = sorted_values[len(sorted_values) // 2]
        summary["latency_ms_p95"] = sorted_values[
            min(len(sorted_values) - 1, int(len(sorted_values) * 0.95))
        ]
    else:
        summary["latency_ms_p50"] = None
        summary["latency_ms_p95"] = None
    return summary
