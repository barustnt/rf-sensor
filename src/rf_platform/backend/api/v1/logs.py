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
from rf_platform.common.time import utc_now

router = APIRouter(prefix="/api/v1", tags=["logs"])


@router.get("/logs")
async def list_logs(
    limit: int = 100,
    offset: int = 0,
    severity: str | None = None,
    service: str | None = None,
    sensor_id: str | None = None,
    event_type: str | None = None,
    correlation_id: str | None = None,
    start_utc: str | None = None,
    end_utc: str | None = None,
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    limit, offset = clamp_limit_offset(limit, offset)
    stmt = select(models.SystemEvent)
    if severity:
        stmt = stmt.where(models.SystemEvent.severity == severity)
    if service:
        stmt = stmt.where(models.SystemEvent.service == service)
    if sensor_id:
        stmt = stmt.where(models.SystemEvent.sensor_id == sensor_id)
    if event_type:
        stmt = stmt.where(models.SystemEvent.event_type == event_type)
    if correlation_id:
        stmt = stmt.where(models.SystemEvent.correlation_id == correlation_id)
    if start := parse_optional_utc(start_utc):
        stmt = stmt.where(models.SystemEvent.timestamp_utc >= start)
    if end := parse_optional_utc(end_utc):
        stmt = stmt.where(models.SystemEvent.timestamp_utc < end)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    result = await session.execute(
        stmt.order_by(desc(models.SystemEvent.timestamp_utc)).limit(limit).offset(offset)
    )
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
    return paged_response(items, int(total), limit, offset)


@router.get("/annotations")
async def list_annotations(
    target_type: str | None = None,
    target_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    limit, offset = clamp_limit_offset(limit, offset)
    stmt = select(models.Annotation)
    if target_type:
        stmt = stmt.where(models.Annotation.target_type == target_type)
    if target_id:
        stmt = stmt.where(models.Annotation.target_id == target_id)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = list(
        (
            await session.execute(
                stmt.order_by(desc(models.Annotation.timestamp_utc)).limit(limit).offset(offset)
            )
        ).scalars()
    )
    items = [
        {
            "annotation_id": row.annotation_id,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "label": row.label,
            "comment": row.comment,
            "actor": row.actor,
            "timestamp_utc": row.timestamp_utc.isoformat(),
        }
        for row in rows
    ]
    return paged_response(items, int(total), limit, offset)


@router.post("/annotations")
async def create_annotation(
    payload: dict[str, str],
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    required = {"target_type", "target_id", "label", "actor"}
    if missing := required - payload.keys():
        raise HTTPException(status_code=400, detail={"missing": sorted(missing)})
    now = utc_now()
    annotation = models.Annotation(
        target_type=payload["target_type"],
        target_id=payload["target_id"],
        label=payload["label"],
        comment=payload.get("comment"),
        actor=payload["actor"],
        timestamp_utc=now,
    )
    session.add(annotation)
    session.add(
        models.SystemEvent(
            severity="info",
            service="backend",
            event_type="annotation_created",
            message=f"Annotation created for {annotation.target_type}:{annotation.target_id}",
            sensor_id=None,
            correlation_id=None,
            context={
                "target_type": annotation.target_type,
                "target_id": annotation.target_id,
                "label": annotation.label,
                "actor": annotation.actor,
            },
            timestamp_utc=now,
        )
    )
    await session.commit()
    return {"status": "accepted", "annotation_id": annotation.annotation_id}
