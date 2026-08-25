from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.backend.dependencies import db_session
from rf_platform.backend.services.ingestion import job_summary
from rf_platform.common.time import utc_now

router = APIRouter(prefix="/api/v1", tags=["analyses"])


def _run_to_dict(run: models.ModelRun) -> dict[str, object]:
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
        "structured_result": run.structured_result,
        "raw_response": run.raw_response,
        "parser_valid": run.parser_valid,
        "started_at_utc": run.started_at_utc.isoformat(),
        "completed_at_utc": run.completed_at_utc.isoformat(),
    }


@router.get("/analyses")
async def list_analyses(
    limit: int = 50,
    capture_id: str | None = None,
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    limit = min(max(limit, 1), 500)
    stmt = select(models.ModelRun).order_by(desc(models.ModelRun.completed_at_utc)).limit(limit)
    if capture_id:
        stmt = stmt.where(models.ModelRun.capture_id == capture_id)
    result = await session.execute(stmt)
    items = [_run_to_dict(run) for run in result.scalars()]
    return {"items": items, "count": len(items)}


@router.get("/analyses/{analysis_id}")
async def get_analysis(
    analysis_id: str, session: AsyncSession = Depends(db_session)
) -> dict[str, object]:
    run = await session.get(models.ModelRun, analysis_id)
    if run is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    findings = (
        await session.execute(
            select(models.ModelFinding).where(models.ModelFinding.analysis_id == analysis_id)
        )
    ).scalars()
    payload = _run_to_dict(run)
    payload["findings"] = [
        {
            "finding_id": item.finding_id,
            "kind": item.kind,
            "label": item.label,
            "model_score": item.model_score,
            "observation": item.observation,
        }
        for item in findings
    ]
    return payload


@router.post("/analyses/jobs/{job_id}/retry")
async def retry_job(job_id: str, session: AsyncSession = Depends(db_session)) -> dict[str, object]:
    job = await session.get(models.AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status not in {"failed", "deadletter"}:
        raise HTTPException(status_code=409, detail="only failed or dead-letter jobs are retryable")
    job.status = "pending"
    job.available_at_utc = utc_now()
    job.updated_at_utc = utc_now()
    job.error_category = None
    job.error_message = None
    session.add(
        models.SystemEvent(
            severity="info",
            service="backend",
            event_type="job_retry_requested",
            message=f"Retry requested for job {job_id}",
            sensor_id=None,
            correlation_id=None,
            context={"job_id": job_id},
            timestamp_utc=utc_now(),
        )
    )
    await session.commit()
    return {"status": "pending", "job_id": job_id}


@router.get("/jobs/summary")
async def get_jobs_summary(session: AsyncSession = Depends(db_session)) -> dict[str, object]:
    summary = await job_summary(session)
    total = await session.execute(select(func.count()).select_from(models.AnalysisJob))
    summary["total"] = total.scalar_one()
    return summary
