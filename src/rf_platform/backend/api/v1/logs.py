from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.backend.dependencies import db_session

router = APIRouter(prefix="/api/v1", tags=["logs"])


@router.get("/logs")
async def list_logs(
    limit: int = 100,
    severity: str | None = None,
    service: str | None = None,
    sensor_id: str | None = None,
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    limit = min(max(limit, 1), 500)
    stmt = select(models.SystemEvent).order_by(desc(models.SystemEvent.timestamp_utc)).limit(limit)
    if severity:
        stmt = stmt.where(models.SystemEvent.severity == severity)
    if service:
        stmt = stmt.where(models.SystemEvent.service == service)
    if sensor_id:
        stmt = stmt.where(models.SystemEvent.sensor_id == sensor_id)
    result = await session.execute(stmt)
    items = [
        {
            "system_event_id": event.system_event_id,
            "timestamp_utc": event.timestamp_utc.isoformat(),
            "severity": event.severity,
            "service": event.service,
            "event_type": event.event_type,
            "message": event.message,
            "sensor_id": event.sensor_id,
            "correlation_id": event.correlation_id,
            "context": event.context,
        }
        for event in result.scalars()
    ]
    return {"items": items, "count": len(items)}


@router.post("/annotations")
async def create_annotation(
    payload: dict[str, str],
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    required = {"target_type", "target_id", "label", "actor"}
    if missing := required - payload.keys():
        return {"status": "rejected", "missing": sorted(missing)}
    annotation = models.Annotation(
        target_type=payload["target_type"],
        target_id=payload["target_id"],
        label=payload["label"],
        comment=payload.get("comment"),
        actor=payload["actor"],
    )
    session.add(annotation)
    await session.commit()
    return {"status": "accepted", "annotation_id": annotation.annotation_id}
