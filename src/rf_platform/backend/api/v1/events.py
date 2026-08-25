from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    limit: int = 50, session: AsyncSession = Depends(db_session)
) -> dict[str, object]:
    limit = min(max(limit, 1), 500)
    result = await session.execute(
        select(models.Event).order_by(desc(models.Event.started_at_utc)).limit(limit)
    )
    items = [_event_to_dict(event) for event in result.scalars()]
    return {"items": items, "count": len(items)}


@router.get("/{event_id}")
async def get_event(
    event_id: str, session: AsyncSession = Depends(db_session)
) -> dict[str, object]:
    event = await session.get(models.Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return _event_to_dict(event)
