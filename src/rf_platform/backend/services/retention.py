from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.common.config import Settings
from rf_platform.common.ids import new_id
from rf_platform.common.time import utc_now


def retention_policy(settings: Settings) -> dict[str, Any]:
    return {
        "report_only": settings.retention_report_only,
        "heartbeat_days": settings.retention_heartbeat_days,
        "capture_days": settings.retention_capture_days,
        "artifact_days": settings.retention_artifact_days,
        "log_days": settings.retention_log_days,
        "automatic_deletion": False,
    }


async def generate_retention_report(
    session: AsyncSession,
    settings: Settings,
    actor: str = "operator",
    persist: bool = True,
) -> dict[str, Any]:
    now = utc_now()
    policy = retention_policy(settings)
    items: list[dict[str, Any]] = []

    open_event_capture_ids: set[str] = set()
    event_rows = list(
        (
            await session.execute(
                select(models.Event).where(models.Event.status.in_(["open", "confirmed"]))
            )
        ).scalars()
    )
    for event in event_rows:
        open_event_capture_ids.update(str(item) for item in event.capture_ids)

    artifact_cutoff = now - timedelta(days=settings.retention_artifact_days)
    artifacts = list(
        (
            await session.execute(
                select(models.Artifact).where(models.Artifact.created_at_utc < artifact_cutoff)
            )
        ).scalars()
    )
    for artifact in artifacts:
        protected = (
            artifact.capture_id in open_event_capture_ids or artifact.retention_class != "ordinary"
        )
        if not protected:
            items.append(
                {
                    "target_type": "artifact",
                    "target_id": artifact.artifact_id,
                    "capture_id": artifact.capture_id,
                    "object_key": artifact.object_key,
                    "byte_size": artifact.byte_size,
                    "eligible": True,
                    "protected": False,
                    "reason": (
                        f"ordinary artifact older than {settings.retention_artifact_days} days"
                    ),
                    "would_delete": False,
                }
            )

    capture_cutoff = now - timedelta(days=settings.retention_capture_days)
    captures = list(
        (
            await session.execute(
                select(models.Capture).where(models.Capture.created_at_utc < capture_cutoff)
            )
        ).scalars()
    )
    for capture in captures:
        protected = capture.capture_id in open_event_capture_ids
        if not protected:
            items.append(
                {
                    "target_type": "capture",
                    "target_id": capture.capture_id,
                    "sensor_id": capture.sensor_id,
                    "eligible": True,
                    "protected": False,
                    "reason": f"capture metadata older than {settings.retention_capture_days} days",
                    "would_delete": False,
                }
            )

    heartbeat_cutoff = now - timedelta(days=settings.retention_heartbeat_days)
    heartbeat_rows = list(
        (
            await session.execute(
                select(models.SensorHeartbeatRow).where(
                    models.SensorHeartbeatRow.timestamp_utc < heartbeat_cutoff
                )
            )
        ).scalars()
    )
    for heartbeat in heartbeat_rows:
        items.append(
            {
                "target_type": "sensor_heartbeat",
                "target_id": heartbeat.heartbeat_id,
                "sensor_id": heartbeat.sensor_id,
                "eligible": True,
                "protected": False,
                "reason": f"heartbeat detail older than {settings.retention_heartbeat_days} days",
                "would_delete": False,
            }
        )

    log_cutoff = now - timedelta(days=settings.retention_log_days)
    log_rows = list(
        (
            await session.execute(
                select(models.SystemEvent).where(models.SystemEvent.timestamp_utc < log_cutoff)
            )
        ).scalars()
    )
    for row in log_rows:
        items.append(
            {
                "target_type": "system_event",
                "target_id": row.system_event_id,
                "eligible": True,
                "protected": False,
                "reason": f"system event older than {settings.retention_log_days} days",
                "would_delete": False,
            }
        )

    summary = {
        "eligible_items": len(items),
        "eligible_artifact_bytes": sum(
            int(item.get("byte_size", 0)) for item in items if item["target_type"] == "artifact"
        ),
        "protected_event_count": len(event_rows),
        "report_only": True,
    }
    report_id = new_id()
    report = {
        "report_id": report_id,
        "mode": "report-only",
        "delete_enabled": False,
        "policy": policy,
        "summary": summary,
        "items": items,
        "created_by": actor,
        "created_at_utc": now.isoformat(),
        "notice": "No data was deleted. Retention execution is report-only in Milestone 2.",
    }
    if persist:
        session.add(
            models.RetentionReport(
                report_id=report_id,
                report_only=True,
                policy=policy,
                summary=summary,
                items=items,
                created_by=actor,
                created_at_utc=now,
            )
        )
        session.add(
            models.SystemEvent(
                severity="info",
                service="backend",
                event_type="retention_report_generated",
                message="Retention report generated in report-only mode",
                sensor_id=None,
                correlation_id=None,
                context={"report_id": report_id, "eligible_items": len(items)},
                timestamp_utc=now,
            )
        )
        await session.commit()
    return report
