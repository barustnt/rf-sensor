from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.api.v1.pagination import (
    clamp_limit_offset,
    paged_response,
    parse_optional_utc,
)
from rf_platform.backend.db import models
from rf_platform.backend.dependencies import db_session

router = APIRouter(prefix="/api/v1/events", tags=["events"])


def _event_to_dict(event: models.Event) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "schema_version": event.schema_version,
        "event_kind": event.event_kind,
        "severity": event.severity,
        "status": event.status,
        "started_at_utc": event.started_at_utc.isoformat(),
        "ended_at_utc": event.ended_at_utc.isoformat(),
        "sensor_ids": event.sensor_ids,
        "capture_ids": event.capture_ids,
        "analysis_ids": event.analysis_ids,
        "findings": event.findings,
        "summary": event.summary,
        "evidence": event.evidence,
        "rule_id": event.rule_id,
        "rule_version": event.rule_version,
        "created_at_utc": event.created_at_utc.isoformat(),
        "updated_at_utc": event.updated_at_utc.isoformat(),
    }


@router.get("")
async def list_events(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    severity: str | None = None,
    event_kind: str | None = None,
    sensor_id: str | None = None,
    capture_id: str | None = None,
    analysis_id: str | None = None,
    start_utc: str | None = None,
    end_utc: str | None = None,
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    limit, offset = clamp_limit_offset(limit, offset)
    stmt = select(models.Event)
    if status:
        stmt = stmt.where(models.Event.status == status)
    if severity:
        stmt = stmt.where(models.Event.severity == severity)
    if event_kind:
        stmt = stmt.where(models.Event.event_kind == event_kind)
    if start := parse_optional_utc(start_utc):
        stmt = stmt.where(models.Event.ended_at_utc >= start)
    if end := parse_optional_utc(end_utc):
        stmt = stmt.where(models.Event.started_at_utc < end)
    if sensor_id:
        stmt = stmt.where(
            models.Event.event_id.in_(
                select(models.EventEvidence.event_id).where(
                    models.EventEvidence.target_type == "sensor",
                    models.EventEvidence.target_id == sensor_id,
                )
            )
        )
    if capture_id:
        stmt = stmt.where(
            models.Event.event_id.in_(
                select(models.EventEvidence.event_id).where(
                    models.EventEvidence.target_type == "capture",
                    models.EventEvidence.target_id == capture_id,
                )
            )
        )
    if analysis_id:
        stmt = stmt.where(
            models.Event.event_id.in_(
                select(models.EventEvidence.event_id).where(
                    models.EventEvidence.target_type == "analysis",
                    models.EventEvidence.target_id == analysis_id,
                )
            )
        )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    result = await session.execute(
        stmt.order_by(desc(models.Event.started_at_utc)).limit(limit).offset(offset)
    )
    items = [_event_to_dict(event) for event in result.scalars()]
    return paged_response(items, int(total), limit, offset)


@router.get("/{event_id}")
async def get_event(
    event_id: str, session: AsyncSession = Depends(db_session)
) -> dict[str, object]:
    event = await session.get(models.Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    annotations = list(
        (
            await session.execute(
                select(models.Annotation).where(
                    models.Annotation.target_type == "event",
                    models.Annotation.target_id == event_id,
                )
            )
        ).scalars()
    )
    payload = _event_to_dict(event)
    payload["annotations"] = [
        {
            "annotation_id": item.annotation_id,
            "label": item.label,
            "comment": item.comment,
            "actor": item.actor,
            "timestamp_utc": item.timestamp_utc.isoformat(),
        }
        for item in annotations
    ]
    return payload
