from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.dependencies import db_session, settings_dependency
from rf_platform.backend.services.metrics import operational_metrics
from rf_platform.backend.services.retention import generate_retention_report
from rf_platform.common.config import Settings

router = APIRouter(prefix="/api/v1/platform", tags=["operational"])


@router.get("/metrics")
async def metrics(
    request: Request,
    session: AsyncSession = Depends(db_session),
    settings: Settings = Depends(settings_dependency),
) -> dict[str, object]:
    request_metrics = getattr(request.app.state, "request_metrics", None)
    return await operational_metrics(session, settings, request_metrics)


@router.post("/retention/report")
async def retention_report(
    payload: dict[str, str] | None = None,
    session: AsyncSession = Depends(db_session),
    settings: Settings = Depends(settings_dependency),
) -> dict[str, object]:
    payload = payload or {}
    return await generate_retention_report(session, settings, payload.get("actor", "operator"))
