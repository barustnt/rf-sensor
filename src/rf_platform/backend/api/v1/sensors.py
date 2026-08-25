from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.api.v1.pagination import (
    clamp_limit_offset,
    paged_response,
    parse_optional_utc,
)
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
from rf_platform.common.time import utc_now
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
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    adapter: str | None = None,
    desired_profile: str | None = None,
    session: AsyncSession = Depends(db_session),
    settings: Settings = Depends(settings_dependency),
) -> dict[str, object]:
    limit, offset = clamp_limit_offset(limit, offset)
    stmt = select(models.Sensor)
    if adapter:
        stmt = stmt.where(models.Sensor.adapter == adapter)
    if desired_profile:
        stmt = stmt.where(models.Sensor.desired_profile == desired_profile)
    if status:
        rows = list((await session.execute(stmt.order_by(models.Sensor.sensor_id))).scalars())
        filtered = []
        for sensor in rows:
            item = sensor_to_dict(sensor, settings.offline_after_seconds)
            if item.get("operational_status") == status:
                filtered.append(item)
        return paged_response(filtered[offset : offset + limit], len(filtered), limit, offset)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    result = await session.execute(
        stmt.order_by(models.Sensor.sensor_id).limit(limit).offset(offset)
    )
    sensors = [
        sensor_to_dict(sensor, settings.offline_after_seconds) for sensor in result.scalars()
    ]
    return paged_response(sensors, int(total), limit, offset)


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
    actor = payload.get("actor", "operator")
    try:
        sensor = await update_desired_profile(session, sensor_id, desired_profile)
        session.add(
            models.SystemEvent(
                severity="info",
                service="backend",
                event_type="sensor_desired_profile_updated",
                message=f"Desired profile updated for sensor {sensor_id}",
                sensor_id=sensor_id,
                correlation_id=None,
                context={
                    "desired_profile": desired_profile,
                    "config_version": sensor.config_version,
                    "actor": actor,
                },
                timestamp_utc=utc_now(),
            )
        )
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
    offset: int = 0,
    start_utc: str | None = None,
    end_utc: str | None = None,
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    limit, offset = clamp_limit_offset(limit, offset)
    stmt = select(models.SensorHeartbeatRow).where(models.SensorHeartbeatRow.sensor_id == sensor_id)
    if start := parse_optional_utc(start_utc):
        stmt = stmt.where(models.SensorHeartbeatRow.timestamp_utc >= start)
    if end := parse_optional_utc(end_utc):
        stmt = stmt.where(models.SensorHeartbeatRow.timestamp_utc < end)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    result = await session.execute(
        stmt.order_by(desc(models.SensorHeartbeatRow.timestamp_utc)).limit(limit).offset(offset)
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
    return paged_response(items, int(total), limit, offset)
