from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.dependencies import db_session, event_bus_dependency
from rf_platform.common.broker import NatsEventBus
from rf_platform.contracts.api import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(status="ok", components={"process": "ok"})


@router.get("/health/ready", response_model=HealthResponse)
async def ready(
    request: Request,
    session: AsyncSession = Depends(db_session),
    bus: NatsEventBus = Depends(event_bus_dependency),
) -> HealthResponse:
    components = {}
    try:
        await session.execute(text("select 1"))
        components["postgresql"] = {"status": "ok"}
    except Exception as exc:  # pragma: no cover - dependency failure varies
        components["postgresql"] = {"status": "error", "error": exc.__class__.__name__}
    components["nats"] = await bus.health()
    components["artifact_store"] = {
        "status": "ok",
        "backend": request.app.state.settings.artifact_backend,
    }
    status = (
        "ok"
        if all(component.get("status") == "ok" for component in components.values())
        else "degraded"
    )
    return HealthResponse(status=status, components=components)
