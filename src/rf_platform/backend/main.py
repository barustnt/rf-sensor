from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rf_platform.backend.api.v1 import (
    alerts,
    analyses,
    captures,
    events,
    health,
    logs,
    query,
    sensors,
    storage,
)
from rf_platform.backend.db.session import create_engine, create_sessionmaker
from rf_platform.backend.services.artifacts import FilesystemArtifactStore
from rf_platform.backend.services.ingestion import publish_pending_outbox
from rf_platform.common.broker import NatsEventBus
from rf_platform.common.config import Settings, get_settings
from rf_platform.common.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    configure_logging()
    log = get_logger("rf_platform.api")
    app.state.settings = settings
    app.state.engine = create_engine(settings)
    app.state.sessionmaker = create_sessionmaker(app.state.engine)
    app.state.artifact_store = FilesystemArtifactStore(settings)
    app.state.event_bus = NatsEventBus(settings)
    try:
        await app.state.event_bus.connect()
    except Exception as exc:  # readiness reports dependency state; startup should not lose captures
        log.warning("nats_startup_unavailable", error=exc.__class__.__name__)
    try:
        async with app.state.sessionmaker() as session:
            await publish_pending_outbox(session, app.state.event_bus)
    except Exception as exc:  # database may not be migrated yet
        log.warning("outbox_startup_flush_skipped", error=exc.__class__.__name__)
    yield
    await app.state.event_bus.close()
    await app.state.engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="RF Intelligence Platform",
        version="0.1.0",
        lifespan=lifespan,
    )
    cors_origins = [item.strip() for item in settings.cors_origins.split(",") if item.strip()]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH"],
            allow_headers=["Authorization", "Content-Type", "X-Sensor-Token"],
        )
    for router in [
        health.router,
        sensors.router,
        captures.router,
        analyses.router,
        events.router,
        alerts.router,
        logs.router,
        storage.router,
        query.router,
    ]:
        app.include_router(router)
    return app


def cli() -> None:
    settings = get_settings()
    uvicorn.run(
        "rf_platform.backend.main:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    asyncio.run(asyncio.to_thread(cli))
