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

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

ALERT_STATUSES = {"open", "acknowledged", "dismissed", "confirmed"}
MUTATION_STATUSES = {"acknowledged", "dismissed", "confirmed"}


def _alert_to_dict(alert: models.AlertRow) -> dict[str, object]:
    return {
        "alert_id": alert.alert_id,
        "event_id": alert.event_id,
        "rule_id": alert.rule_id,
        "rule_version": alert.rule_version,
        "status": alert.status,
        "reason": alert.reason,
        "thresholds": alert.thresholds,
        "evidence": alert.evidence,
        "acknowledged_by": alert.acknowledged_by,
        "acknowledged_at_utc": alert.acknowledged_at_utc.isoformat()
        if alert.acknowledged_at_utc
        else None,
        "created_at_utc": alert.created_at_utc.isoformat(),
        "updated_at_utc": alert.updated_at_utc.isoformat(),
    }


@router.get("")
async def list_alerts(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    rule_id: str | None = None,
    event_id: str | None = None,
    start_utc: str | None = None,
    end_utc: str | None = None,
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    limit, offset = clamp_limit_offset(limit, offset)
    stmt = select(models.AlertRow)
    if status:
        stmt = stmt.where(models.AlertRow.status == status)
    if rule_id:
        stmt = stmt.where(models.AlertRow.rule_id == rule_id)
    if event_id:
        stmt = stmt.where(models.AlertRow.event_id == event_id)
    if start := parse_optional_utc(start_utc):
        stmt = stmt.where(models.AlertRow.created_at_utc >= start)
    if end := parse_optional_utc(end_utc):
        stmt = stmt.where(models.AlertRow.created_at_utc < end)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    result = await session.execute(
        stmt.order_by(desc(models.AlertRow.created_at_utc)).limit(limit).offset(offset)
    )
    items = [_alert_to_dict(alert) for alert in result.scalars()]
    return paged_response(items, int(total), limit, offset)


@router.patch("/{alert_id}")
async def patch_alert(
    alert_id: str,
    payload: dict[str, str],
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    alert = await session.get(models.AlertRow, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    new_status = payload.get("status")
    if new_status not in MUTATION_STATUSES:
        raise HTTPException(status_code=400, detail="unsupported alert status")
    actor = payload.get("actor", "operator")
    previous_status = alert.status
    now = utc_now()
    alert.status = new_status
    alert.acknowledged_by = actor
    alert.acknowledged_at_utc = now
    alert.updated_at_utc = now
    event = await session.get(models.Event, alert.event_id)
    if event is not None:
        event.status = new_status
        event.updated_at_utc = now
    session.add(
        models.Annotation(
            target_type="alert",
            target_id=alert_id,
            label=f"alert_{new_status}",
            comment=payload.get("comment"),
            actor=actor,
            timestamp_utc=now,
        )
    )
    session.add(
        models.SystemEvent(
            severity="info",
            service="backend",
            event_type="alert_status_updated",
            message=f"Alert {alert_id} set to {new_status}",
            sensor_id=None,
            correlation_id=None,
            context={
                "alert_id": alert_id,
                "event_id": alert.event_id,
                "status": new_status,
                "previous_status": previous_status,
                "actor": actor,
            },
            timestamp_utc=now,
        )
    )
    await session.commit()
    return _alert_to_dict(alert)
