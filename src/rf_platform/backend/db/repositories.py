from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models


async def get_sensor(session: AsyncSession, sensor_id: str) -> models.Sensor | None:
    return await session.get(models.Sensor, sensor_id)


async def get_capture(session: AsyncSession, capture_id: str) -> models.Capture | None:
    return await session.get(models.Capture, capture_id)


async def get_job(session: AsyncSession, job_id: str) -> models.AnalysisJob | None:
    return await session.get(models.AnalysisJob, job_id)


async def get_job_for_capture(
    session: AsyncSession,
    capture_id: str,
    model_name: str,
    model_version: str,
    prompt_version: str,
) -> models.AnalysisJob | None:
    result = await session.execute(
        select(models.AnalysisJob).where(
            models.AnalysisJob.capture_id == capture_id,
            models.AnalysisJob.model_name == model_name,
            models.AnalysisJob.model_version == model_version,
            models.AnalysisJob.prompt_version == prompt_version,
        )
    )
    return result.scalar_one_or_none()
