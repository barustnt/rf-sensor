from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.backend.dependencies import db_session, require_sensor_auth, settings_dependency
from rf_platform.backend.services.control import update_desired_profile
from rf_platform.backend.services.registry import (
    desired_state,
    record_heartbeat,
    register_sensor,
    request_source_ip,
    sensor_to_dict,
)
from rf_platform.common.config import Settings
from rf_platform.contracts.sensor import DesiredState, SensorHeartbeat, SensorRegistration

router = APIRouter(prefix="/api/v1/sensors", tags=["sensors"])


@router.post("/register", dependencies=[Depends(require_sensor_auth)])
async def register(
    registration: SensorRegistration,
    request: Request,
    session: AsyncSession = Depends(db_session),
    settings: Settings = Depends(settings_dependency),
) -> dict[str, object]:
    sensor = await register_sensor(session, registration, request_source_ip(request))
    await session.commit()
    return sensor_to_dict(sensor, settings.offline_after_seconds)


@router.post("/{sensor_id}/heartbeat", dependencies=[Depends(require_sensor_auth)])
async def heartbeat(
    sensor_id: str,
    heartbeat_payload: SensorHeartbeat,
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    if heartbeat_payload.sensor_id != sensor_id:
        raise HTTPException(status_code=400, detail="sensor ID mismatch")
    try:
        await record_heartbeat(session, heartbeat_payload)
        await session.commit()
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "accepted", "sequence": heartbeat_payload.sequence}


@router.get("")
async def list_sensors(
    session: AsyncSession = Depends(db_session),
    settings: Settings = Depends(settings_dependency),
) -> dict[str, object]:
    result = await session.execute(select(models.Sensor).order_by(models.Sensor.sensor_id))
    sensors = [
        sensor_to_dict(sensor, settings.offline_after_seconds) for sensor in result.scalars()
    ]
    return {"items": sensors, "count": len(sensors)}


@router.get("/{sensor_id}")
async def get_sensor(
    sensor_id: str,
    session: AsyncSession = Depends(db_session),
    settings: Settings = Depends(settings_dependency),
) -> dict[str, object]:
    sensor = await session.get(models.Sensor, sensor_id)
    if sensor is None:
        raise HTTPException(status_code=404, detail="sensor not found")
    return sensor_to_dict(sensor, settings.offline_after_seconds)


@router.get("/{sensor_id}/desired-state", response_model=DesiredState)
async def get_desired_state(
    sensor_id: str, session: AsyncSession = Depends(db_session)
) -> DesiredState:
    try:
        return await desired_state(session, sensor_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{sensor_id}/desired-state")
async def put_desired_state(
    sensor_id: str,
    payload: dict[str, str],
    session: AsyncSession = Depends(db_session),
) -> DesiredState:
    desired_profile = payload.get("desired_profile")
    if not desired_profile:
        raise HTTPException(status_code=400, detail="desired_profile is required")
    try:
        sensor = await update_desired_profile(session, sensor_id, desired_profile)
        await session.commit()
        return DesiredState(
            sensor_id=sensor.sensor_id,
            desired_profile=sensor.desired_profile or desired_profile,
            config_version=sensor.config_version,
        )
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{sensor_id}/heartbeats")
async def list_heartbeats(
    sensor_id: str,
    limit: int = 50,
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    limit = min(max(limit, 1), 500)
    result = await session.execute(
        select(models.SensorHeartbeatRow)
        .where(models.SensorHeartbeatRow.sensor_id == sensor_id)
        .order_by(desc(models.SensorHeartbeatRow.timestamp_utc))
        .limit(limit)
    )
    items = [
        {
            "sequence": hb.sequence,
            "timestamp_utc": hb.timestamp_utc.isoformat(),
            "status": hb.status,
            "active_profile": hb.active_profile,
            "disk": hb.disk,
            "spool": hb.spool,
            "system": hb.system,
            "radio": hb.radio,
            "last_capture_utc": hb.last_capture_utc.isoformat() if hb.last_capture_utc else None,
        }
        for hb in result.scalars()
    ]
    return {"items": items, "count": len(items)}
