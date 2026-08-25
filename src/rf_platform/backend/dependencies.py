from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rf_platform.backend.services.artifacts import FilesystemArtifactStore
from rf_platform.common.broker import NatsEventBus
from rf_platform.common.config import Settings, get_settings
from rf_platform.common.security import constant_time_equal


def settings_dependency() -> Settings:
    return get_settings()


def sessionmaker_dependency(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.sessionmaker


async def db_session(
    factory: async_sessionmaker[AsyncSession] = Depends(sessionmaker_dependency),
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session


def artifact_store_dependency(request: Request) -> FilesystemArtifactStore:
    return request.app.state.artifact_store


def event_bus_dependency(request: Request) -> NatsEventBus:
    return request.app.state.event_bus


async def require_sensor_auth(
    settings: Settings = Depends(settings_dependency),
    authorization: str | None = Header(default=None),
    x_sensor_token: str | None = Header(default=None),
) -> None:
    token = x_sensor_token
    if token is None and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    expected = settings.require_sensor_token().get_secret_value()
    if not token or not constant_time_equal(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid sensor credentials",
        )
