from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.backend.dependencies import db_session
from rf_platform.common.time import resolve_historical_interval
from rf_platform.contracts.api import (
    QueryEvidence,
    QueryInterval,
    QueryRequest,
    QueryResponse,
    QuerySummary,
)

router = APIRouter(prefix="/api/v1", tags=["query"])


@router.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest, session: AsyncSession = Depends(db_session)
) -> QueryResponse:
    interval = resolve_historical_interval(request.question, request.timezone)
    capture_stmt = select(models.Capture).where(
        models.Capture.started_at_utc >= interval.start_utc,
        models.Capture.started_at_utc < interval.end_utc,
    )
    if request.sensor_ids:
        capture_stmt = capture_stmt.where(models.Capture.sensor_id.in_(request.sensor_ids))
    captures = list((await session.execute(capture_stmt)).scalars())
    capture_ids = [capture.capture_id for capture in captures]
    analyses = []
    if capture_ids:
        analyses = list(
            (
                await session.execute(
                    select(models.ModelRun).where(models.ModelRun.capture_id.in_(capture_ids))
                )
            ).scalars()
        )
    events = list(
        (
            await session.execute(
                select(models.Event).where(
                    models.Event.started_at_utc < interval.end_utc,
                    models.Event.ended_at_utc >= interval.start_utc,
                )
            )
        ).scalars()
    )
    evidence: list[QueryEvidence] = []
    for capture in captures[:20]:
        evidence.append(
            QueryEvidence(
                target_type="capture",
                target_id=capture.capture_id,
                summary=f"Capture from {capture.sensor_id} using profile {capture.profile_id}",
            )
        )
    for run in analyses[:20]:
        labels = [item.get("label") for item in run.structured_result.get("technologies", [])]
        label_text = ", ".join(str(label) for label in labels if label)
        evidence.append(
            QueryEvidence(
                target_type="analysis",
                target_id=run.analysis_id,
                summary=f"Model observation labels: {label_text}",
            )
        )
    for event in events[:20]:
        evidence.append(
            QueryEvidence(target_type="event", target_id=event.event_id, summary=event.summary)
        )
    if analyses or events:
        answer = (
            f"Found {len(captures)} capture(s), {len(analyses)} RF-GPT-like analysis result(s), "
            f"and {len(events)} event(s) in the interpreted interval."
        )
    else:
        answer = "No RF observations were found in the interpreted interval."
    limitations = [
        "RF-GPT output is a model observation, not verified ground truth.",
        "Milestone 1 query parsing is deterministic and supports only documented phrase patterns.",
    ]
    return QueryResponse(
        answer=answer,
        interpreted_interval=QueryInterval(
            start_utc=interval.start_utc,
            end_utc=interval.end_utc,
            display_timezone=interval.display_timezone,
            assumptions=interval.assumptions,
        ),
        summary=QuerySummary(
            capture_count=len(captures), analysis_count=len(analyses), event_count=len(events)
        ),
        evidence=evidence,
        limitations=limitations,
    )
