from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.common.time import utc_now


async def update_desired_profile(
    session: AsyncSession,
    sensor_id: str,
    desired_profile: str,
    actor: str = "operator",
) -> models.Sensor:
    sensor = await session.get(models.Sensor, sensor_id)
    if sensor is None:
        raise LookupError(f"unknown sensor: {sensor_id}")
    sensor.desired_profile = desired_profile
    sensor.config_version += 1
    sensor.updated_at_utc = utc_now()
    session.add(
        models.SystemEvent(
            severity="info",
            service="backend",
            event_type="desired_state_updated",
            message=f"Desired profile for {sensor_id} changed to {desired_profile}",
            sensor_id=sensor_id,
            correlation_id=None,
            context={
                "actor": actor,
                "desired_profile": desired_profile,
                "config_version": sensor.config_version,
            },
            timestamp_utc=utc_now(),
        )
    )
    return sensor
