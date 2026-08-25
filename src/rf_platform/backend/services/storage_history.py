from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.backend.services.artifacts import FilesystemArtifactStore
from rf_platform.common.config import Settings
from rf_platform.common.time import utc_now

MIN_POINTS_FOR_TREND = 3


def _number_as_int(value: object) -> int:
    if isinstance(value, int | float | str):
        return int(value)
    raise TypeError(f"expected numeric storage value, got {type(value).__name__}")


def _number_as_float(value: object) -> float:
    if isinstance(value, int | float | str):
        return float(value)
    raise TypeError(f"expected numeric storage value, got {type(value).__name__}")


def _severity_for_used_percent(settings: Settings, used_percent: float | None) -> str | None:
    if used_percent is None:
        return None
    if used_percent >= settings.storage_critical_used_percent:
        return "critical"
    if used_percent >= settings.storage_warning_used_percent:
        return "warning"
    return None


def _bytes_per_second(points: list[models.StorageSnapshot], attr: str) -> float | None:
    if len(points) < MIN_POINTS_FOR_TREND:
        return None
    newest = points[0]
    oldest = points[-1]
    newest_value = getattr(newest, attr)
    oldest_value = getattr(oldest, attr)
    if newest_value is None or oldest_value is None:
        return None
    elapsed = (newest.timestamp_utc - oldest.timestamp_utc).total_seconds()
    if elapsed <= 0:
        return None
    return float(newest_value - oldest_value) / elapsed


def summarize_trend(points: list[models.StorageSnapshot]) -> dict[str, Any]:
    if len(points) < MIN_POINTS_FOR_TREND:
        return {
            "status": "unknown",
            "reason": f"need at least {MIN_POINTS_FOR_TREND} samples",
            "samples": len(points),
            "free_bytes_per_hour": None,
            "time_to_full_seconds": None,
        }
    slope = _bytes_per_second(points, "free_bytes")
    if slope is None:
        return {
            "status": "unknown",
            "reason": "samples do not include comparable free-byte values",
            "samples": len(points),
            "free_bytes_per_hour": None,
            "time_to_full_seconds": None,
        }
    newest = points[0]
    if slope >= 0 or newest.free_bytes is None:
        return {
            "status": "stable_or_freeing",
            "samples": len(points),
            "free_bytes_per_hour": round(slope * 3600, 2),
            "time_to_full_seconds": None,
        }
    time_to_full = int(newest.free_bytes / abs(slope)) if slope < 0 else None
    return {
        "status": "filling",
        "samples": len(points),
        "free_bytes_per_hour": round(slope * 3600, 2),
        "time_to_full_seconds": time_to_full,
    }


async def record_central_storage_snapshot(
    session: AsyncSession,
    settings: Settings,
    store: FilesystemArtifactStore,
) -> models.StorageSnapshot:
    summary = store.storage_summary()
    snapshot = models.StorageSnapshot(
        target_type="central",
        target_id="laptop-all-in-one",
        label="Laptop (all-in-one)",
        total_bytes=_number_as_int(summary["disk_total_bytes"]),
        free_bytes=_number_as_int(summary["disk_free_bytes"]),
        used_percent=_number_as_float(summary["disk_used_percent"]),
        artifact_bytes=_number_as_int(summary["artifact_bytes"]),
        spool_bytes=None,
        pending_items=None,
        source="platform_storage_endpoint",
        context={"file_count": summary["file_count"], "backend": summary["backend"]},
        timestamp_utc=utc_now(),
    )
    session.add(snapshot)
    severity = _severity_for_used_percent(settings, snapshot.used_percent)
    if severity:
        session.add(
            models.SystemEvent(
                severity=severity,
                service="backend",
                event_type="central_storage_warning",
                message=f"Central storage used percent is {snapshot.used_percent:.1f}",
                sensor_id=None,
                correlation_id=None,
                context={"target_id": snapshot.target_id, "used_percent": snapshot.used_percent},
                timestamp_utc=snapshot.timestamp_utc,
            )
        )
    return snapshot


def snapshot_from_heartbeat(heartbeat: models.SensorHeartbeatRow) -> models.StorageSnapshot:
    disk = heartbeat.disk or {}
    spool = heartbeat.spool or {}
    return models.StorageSnapshot(
        target_type="sensor",
        target_id=heartbeat.sensor_id,
        label=heartbeat.sensor_id,
        total_bytes=disk.get("total_bytes"),
        free_bytes=disk.get("free_bytes"),
        used_percent=disk.get("used_percent"),
        artifact_bytes=None,
        spool_bytes=spool.get("pending_bytes"),
        pending_items=spool.get("pending_items"),
        source="sensor_heartbeat",
        context={"heartbeat_sequence": heartbeat.sequence},
        timestamp_utc=heartbeat.timestamp_utc,
    )


async def get_storage_history(
    session: AsyncSession,
    target_type: str | None = None,
    target_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    stmt = select(models.StorageSnapshot).order_by(desc(models.StorageSnapshot.timestamp_utc))
    if target_type:
        stmt = stmt.where(models.StorageSnapshot.target_type == target_type)
    if target_id:
        stmt = stmt.where(models.StorageSnapshot.target_id == target_id)
    rows = list((await session.execute(stmt.limit(limit).offset(offset))).scalars())
    items = [snapshot_to_dict(row) for row in rows]
    trend_by_target: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{row.target_type}:{row.target_id}"
        trend_by_target.setdefault(
            key, {"target_type": row.target_type, "target_id": row.target_id}
        )
    for value in trend_by_target.values():
        history = list(
            (
                await session.execute(
                    select(models.StorageSnapshot)
                    .where(
                        models.StorageSnapshot.target_type == value["target_type"],
                        models.StorageSnapshot.target_id == value["target_id"],
                    )
                    .order_by(desc(models.StorageSnapshot.timestamp_utc))
                    .limit(10)
                )
            ).scalars()
        )
        value["trend"] = summarize_trend(history)
    return {
        "items": items,
        "count": len(items),
        "limit": limit,
        "offset": offset,
        "trends": trend_by_target,
    }


def snapshot_to_dict(row: models.StorageSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": row.snapshot_id,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "label": row.label,
        "total_bytes": row.total_bytes,
        "free_bytes": row.free_bytes,
        "used_percent": row.used_percent,
        "artifact_bytes": row.artifact_bytes,
        "spool_bytes": row.spool_bytes,
        "pending_items": row.pending_items,
        "source": row.source,
        "context": row.context,
        "timestamp_utc": row.timestamp_utc.isoformat(),
    }


def warning_from_snapshot(settings: Settings, row: models.StorageSnapshot) -> dict[str, Any] | None:
    severity = _severity_for_used_percent(settings, row.used_percent)
    if not severity:
        return None
    return {
        "severity": severity,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "message": f"{row.label} storage is {row.used_percent:.1f}% used",
    }
