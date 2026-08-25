from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.backend.dependencies import artifact_store_dependency, db_session
from rf_platform.backend.services.artifacts import FilesystemArtifactStore

router = APIRouter(prefix="/api/v1/platform", tags=["storage"])


@router.get("/storage")
async def storage_summary(
    store: FilesystemArtifactStore = Depends(artifact_store_dependency),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    latest = await session.execute(
        select(models.SensorHeartbeatRow)
        .order_by(desc(models.SensorHeartbeatRow.timestamp_utc))
        .limit(100)
    )
    seen: set[str] = set()
    spools = []
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
    return {"central": store.storage_summary(), "sensor_spools": spools}
