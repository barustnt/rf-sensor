from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.api.v1.pagination import parse_optional_utc
from rf_platform.backend.dependencies import db_session, settings_dependency
from rf_platform.backend.services.coverage import coverage_report
from rf_platform.common.config import Settings
from rf_platform.common.scan_profiles import ScanProfileError, load_plan_from_settings

router = APIRouter(prefix="/api/v1", tags=["scan"])


@router.get("/scan-profiles")
async def scan_profiles(settings: Settings = Depends(settings_dependency)) -> dict[str, object]:
    try:
        plan = load_plan_from_settings(settings)
    except ScanProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return plan.as_dict(retune_settle_seconds=settings.scan_retune_settle_seconds)


@router.get("/coverage")
async def coverage(
    start_utc: str | None = None,
    end_utc: str | None = None,
    sensor_id: str | None = None,
    session: AsyncSession = Depends(db_session),
    settings: Settings = Depends(settings_dependency),
) -> dict[str, object]:
    try:
        return await coverage_report(
            session,
            settings,
            start_utc=parse_optional_utc(start_utc),
            end_utc=parse_optional_utc(end_utc),
            sensor_id=sensor_id,
        )
    except ScanProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
