from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.backend.dependencies import db_session
from rf_platform.common.time import utc_now

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


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
    limit: int = 50, session: AsyncSession = Depends(db_session)
) -> dict[str, object]:
    limit = min(max(limit, 1), 500)
    result = await session.execute(
        select(models.AlertRow).order_by(desc(models.AlertRow.created_at_utc)).limit(limit)
    )
    items = [_alert_to_dict(alert) for alert in result.scalars()]
    return {"items": items, "count": len(items)}


@router.patch("/{alert_id}")
async def patch_alert(
    alert_id: str,
    payload: dict[str, str],
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    alert = await session.get(models.AlertRow, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    status = payload.get("status")
    if status not in {"acknowledged", "dismissed", "confirmed"}:
        raise HTTPException(status_code=400, detail="unsupported alert status")
    alert.status = status
    alert.acknowledged_by = payload.get("actor", "operator")
    alert.acknowledged_at_utc = utc_now()
    alert.updated_at_utc = utc_now()
    session.add(
        models.SystemEvent(
            severity="info",
            service="backend",
            event_type="alert_status_updated",
            message=f"Alert {alert_id} set to {status}",
            sensor_id=None,
            correlation_id=None,
            context={"alert_id": alert_id, "status": status},
            timestamp_utc=utc_now(),
        )
    )
    await session.commit()
    return _alert_to_dict(alert)
