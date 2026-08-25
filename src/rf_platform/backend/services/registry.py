from __future__ import annotations

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.backend.services.storage_history import snapshot_from_heartbeat
from rf_platform.common.time import utc_now
from rf_platform.contracts.sensor import DesiredState, SensorHeartbeat, SensorRegistration


def request_source_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def register_sensor(
    session: AsyncSession,
    registration: SensorRegistration,
    source_ip: str | None,
) -> models.Sensor:
    now = utc_now()
    sensor = await session.get(models.Sensor, registration.sensor_id)
    if sensor is None:
        sensor = models.Sensor(
            sensor_id=registration.sensor_id,
            display_name=registration.display_name,
            node_type=registration.node_type,
            adapter=registration.adapter,
            location=registration.location.model_dump(mode="json"),
            groups=registration.groups,
            capabilities=registration.capabilities.model_dump(mode="json"),
            desired_profile=(
                registration.capabilities.supported_profiles[0]
                if registration.capabilities.supported_profiles
                else None
            ),
            active_profile=None,
            config_version=1,
            software_version=registration.software_version,
            last_source_ip=source_ip,
            last_hostname=registration.hostname,
            registered_at_utc=registration.registered_at_utc,
            last_seen_at_utc=now,
            created_at_utc=now,
            updated_at_utc=now,
            operational_status="online",
        )
        session.add(sensor)
    else:
        sensor.display_name = registration.display_name
        sensor.adapter = registration.adapter
        sensor.location = registration.location.model_dump(mode="json")
        sensor.groups = registration.groups
        sensor.capabilities = registration.capabilities.model_dump(mode="json")
        sensor.software_version = registration.software_version
        sensor.last_source_ip = source_ip
        sensor.last_hostname = registration.hostname
        sensor.last_seen_at_utc = now
        sensor.updated_at_utc = now
        sensor.operational_status = "online"
    session.add(
        models.SystemEvent(
            severity="info",
            service="backend",
            event_type="sensor_registered",
            message=f"Sensor {registration.sensor_id} registered",
            sensor_id=registration.sensor_id,
            correlation_id=None,
            context={"hostname": registration.hostname, "source_ip": source_ip},
            timestamp_utc=now,
        )
    )
    return sensor


async def record_heartbeat(session: AsyncSession, heartbeat: SensorHeartbeat) -> None:
    now = utc_now()
    sensor = await session.get(models.Sensor, heartbeat.sensor_id)
    if sensor is None:
        raise LookupError(f"unknown sensor: {heartbeat.sensor_id}")
    existing = await session.execute(
        select(models.SensorHeartbeatRow).where(
            models.SensorHeartbeatRow.sensor_id == heartbeat.sensor_id,
            models.SensorHeartbeatRow.sequence == heartbeat.sequence,
        )
    )
    if existing.scalar_one_or_none() is None:
        heartbeat_row = models.SensorHeartbeatRow(
            sensor_id=heartbeat.sensor_id,
            sequence=heartbeat.sequence,
            timestamp_utc=heartbeat.timestamp_utc,
            status=heartbeat.status,
            active_profile=heartbeat.active_profile,
            disk=heartbeat.disk.model_dump(mode="json"),
            spool=heartbeat.spool.model_dump(mode="json"),
            system=heartbeat.system.model_dump(mode="json"),
            radio=heartbeat.radio.model_dump(mode="json"),
            last_capture_utc=heartbeat.last_capture_utc,
            clock_offset_ms=heartbeat.clock_offset_ms,
            received_at_utc=now,
        )
        session.add(heartbeat_row)
        session.add(snapshot_from_heartbeat(heartbeat_row))
    sensor.last_seen_at_utc = heartbeat.timestamp_utc
    sensor.active_profile = heartbeat.active_profile
    sensor.operational_status = heartbeat.status
    sensor.updated_at_utc = now
    session.add(
        models.SystemEvent(
            severity="info",
            service="backend",
            event_type="sensor_heartbeat",
            message=f"Heartbeat from {heartbeat.sensor_id}",
            sensor_id=heartbeat.sensor_id,
            correlation_id=None,
            context={"sequence": heartbeat.sequence},
            timestamp_utc=now,
        )
    )


def derive_operational_status(sensor: models.Sensor, offline_after_seconds: int) -> str:
    if sensor.last_seen_at_utc is None:
        return "offline"
    age = (utc_now() - sensor.last_seen_at_utc).total_seconds()
    if age > offline_after_seconds:
        return "offline"
    if sensor.operational_status == "degraded":
        return "degraded"
    return "online"


def sensor_to_dict(sensor: models.Sensor, offline_after_seconds: int) -> dict[str, object]:
    status = derive_operational_status(sensor, offline_after_seconds)
    age = None
    if sensor.last_seen_at_utc:
        age = max(0.0, (utc_now() - sensor.last_seen_at_utc).total_seconds())
    return {
        "sensor_id": sensor.sensor_id,
        "display_name": sensor.display_name,
        "adapter": sensor.adapter,
        "location": sensor.location,
        "groups": sensor.groups,
        "capabilities": sensor.capabilities,
        "desired_profile": sensor.desired_profile,
        "active_profile": sensor.active_profile,
        "config_version": sensor.config_version,
        "software_version": sensor.software_version,
        "source_ip": sensor.last_source_ip,
        "hostname": sensor.last_hostname,
        "last_seen_at_utc": sensor.last_seen_at_utc.isoformat()
        if sensor.last_seen_at_utc
        else None,
        "last_heartbeat_age_seconds": age,
        "operational_status": status,
        "last_error": sensor.last_error,
    }


async def desired_state(session: AsyncSession, sensor_id: str) -> DesiredState:
    sensor = await session.get(models.Sensor, sensor_id)
    if sensor is None:
        raise LookupError(f"unknown sensor: {sensor_id}")
    return DesiredState(
        sensor_id=sensor.sensor_id,
        desired_profile=sensor.desired_profile or "campus_general",
        config_version=sensor.config_version,
    )
