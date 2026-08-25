from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.api.v1.pagination import clamp_limit_offset
from rf_platform.backend.db import models
from rf_platform.backend.dependencies import (
    artifact_store_dependency,
    db_session,
    settings_dependency,
)
from rf_platform.backend.services.artifacts import FilesystemArtifactStore
from rf_platform.backend.services.storage_history import (
    get_storage_history,
    record_central_storage_snapshot,
    snapshot_to_dict,
    summarize_trend,
    warning_from_snapshot,
)
from rf_platform.common.config import Settings

router = APIRouter(prefix="/api/v1/platform", tags=["storage"])


@router.get("/storage")
async def storage_summary(
    store: FilesystemArtifactStore = Depends(artifact_store_dependency),
    session: AsyncSession = Depends(db_session),
    settings: Settings = Depends(settings_dependency),
) -> dict[str, object]:
    central_snapshot = await record_central_storage_snapshot(session, settings, store)
    latest = await session.execute(
        select(models.SensorHeartbeatRow)
        .order_by(desc(models.SensorHeartbeatRow.timestamp_utc))
        .limit(100)
    )
    seen: set[str] = set()
    spools = []
    warnings = []
    for hb in latest.scalars():
        if hb.sensor_id in seen:
            continue
        seen.add(hb.sensor_id)
        spools.append(
            {
                "sensor_id": hb.sensor_id,
                "spool": hb.spool,
                "disk": hb.disk,
                "timestamp_utc": hb.timestamp_utc.isoformat(),
            }
        )
        used_percent = hb.disk.get("used_percent") if hb.disk else None
        if used_percent is not None and used_percent >= settings.storage_warning_used_percent:
            warnings.append(
                {
                    "severity": "critical"
                    if used_percent >= settings.storage_critical_used_percent
                    else "warning",
                    "target_type": "sensor",
                    "target_id": hb.sensor_id,
                    "message": f"{hb.sensor_id} storage is {used_percent:.1f}% used",
                }
            )
    await session.commit()
    history_points = list(
        (
            await session.execute(
                select(models.StorageSnapshot)
                .where(
                    models.StorageSnapshot.target_type == "central",
                    models.StorageSnapshot.target_id == central_snapshot.target_id,
                )
                .order_by(desc(models.StorageSnapshot.timestamp_utc))
                .limit(10)
            )
        ).scalars()
    )
    central_warning = warning_from_snapshot(settings, central_snapshot)
    if central_warning:
        warnings.append(central_warning)
    return {
        "central": snapshot_to_dict(central_snapshot),
        "central_trend": summarize_trend(history_points),
        "sensor_spools": spools,
        "warnings": warnings,
    }


@router.get("/storage/history")
async def storage_history(
    target_type: str | None = None,
    target_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    limit, offset = clamp_limit_offset(limit, offset)
    return await get_storage_history(session, target_type, target_id, limit, offset)
