from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import String, cast, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.api.v1.pagination import (
    clamp_limit_offset,
    paged_response,
    parse_optional_utc,
)
from rf_platform.backend.db import models
from rf_platform.backend.dependencies import (
    artifact_store_dependency,
    db_session,
    event_bus_dependency,
    require_sensor_auth,
    settings_dependency,
)
from rf_platform.backend.services.artifacts import ArtifactError, FilesystemArtifactStore
from rf_platform.backend.services.ingestion import (
    IngestionConflict,
    ingest_capture,
    publish_pending_outbox,
)
from rf_platform.common.broker import NatsEventBus
from rf_platform.common.config import Settings
from rf_platform.contracts.capture import CaptureEnvelope, CaptureIngestResponse

router = APIRouter(prefix="/api/v1", tags=["captures"])


def _capture_to_dict(
    capture: models.Capture, artifacts: list[models.Artifact] | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "capture_id": capture.capture_id,
        "sensor_id": capture.sensor_id,
        "session_id": capture.session_id,
        "correlation_id": capture.correlation_id,
        "profile_id": capture.profile_id,
        "started_at_utc": capture.started_at_utc.isoformat(),
        "ended_at_utc": capture.ended_at_utc.isoformat(),
        "radio": capture.radio,
        "preprocessing": capture.preprocessing,
        "dsp_metrics": capture.dsp_metrics,
        "state": capture.state,
        "created_at_utc": capture.created_at_utc.isoformat(),
        "received_at_utc": capture.received_at_utc.isoformat(),
    }
    if artifacts is not None:
        payload["artifacts"] = [
            {
                "artifact_id": item.artifact_id,
                "kind": item.kind,
                "backend": item.backend,
                "object_key": item.object_key,
                "mime_type": item.mime_type,
                "byte_size": item.byte_size,
                "sha256": item.sha256,
            }
            for item in artifacts
        ]
    return payload


@router.post(
    "/captures",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CaptureIngestResponse,
    dependencies=[Depends(require_sensor_auth)],
)
async def create_capture(
    metadata: str = Form(...),
    artifacts: list[UploadFile] = File(...),
    session: AsyncSession = Depends(db_session),
    settings: Settings = Depends(settings_dependency),
    store: FilesystemArtifactStore = Depends(artifact_store_dependency),
    bus: NatsEventBus = Depends(event_bus_dependency),
) -> CaptureIngestResponse:
    try:
        envelope = CaptureEnvelope.model_validate(json.loads(metadata))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid capture metadata: {exc}") from exc
    try:
        result = await ingest_capture(session, settings, store, envelope, artifacts)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IngestionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ArtifactError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.created:
        await publish_pending_outbox(session, bus)
    return result.response


@router.get("/captures")
async def list_captures(
    limit: int = 50,
    offset: int = 0,
    sensor_id: str | None = None,
    location: str | None = None,
    profile_id: str | None = None,
    session_id: str | None = None,
    state: str | None = None,
    start_utc: str | None = None,
    end_utc: str | None = None,
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    limit, offset = clamp_limit_offset(limit, offset)
    stmt = select(models.Capture)
    if location:
        stmt = stmt.join(models.Sensor, models.Sensor.sensor_id == models.Capture.sensor_id)
    if sensor_id:
        stmt = stmt.where(models.Capture.sensor_id == sensor_id)
    if location:
        stmt = stmt.where(cast(models.Sensor.location, String).ilike(f"%{location}%"))
    if profile_id:
        stmt = stmt.where(models.Capture.profile_id == profile_id)
    if session_id:
        stmt = stmt.where(models.Capture.session_id == session_id)
    if state:
        stmt = stmt.where(models.Capture.state == state)
    if start := parse_optional_utc(start_utc):
        stmt = stmt.where(models.Capture.started_at_utc >= start)
    if end := parse_optional_utc(end_utc):
        stmt = stmt.where(models.Capture.started_at_utc < end)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    result = await session.execute(
        stmt.order_by(desc(models.Capture.started_at_utc)).limit(limit).offset(offset)
    )
    items = [_capture_to_dict(capture) for capture in result.scalars()]
    return paged_response(items, int(total), limit, offset)


@router.get("/captures/{capture_id}")
async def get_capture(
    capture_id: str, session: AsyncSession = Depends(db_session)
) -> dict[str, object]:
    capture = await session.get(models.Capture, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="capture not found")
    artifacts = (
        await session.execute(
            select(models.Artifact).where(models.Artifact.capture_id == capture_id)
        )
    ).scalars()
    return _capture_to_dict(capture, list(artifacts))


@router.get("/captures/{capture_id}/artifacts/{artifact_id}")
async def get_artifact(
    capture_id: str,
    artifact_id: str,
    session: AsyncSession = Depends(db_session),
    store: FilesystemArtifactStore = Depends(artifact_store_dependency),
) -> FileResponse:
    artifact = await session.get(models.Artifact, artifact_id)
    if artifact is None or artifact.capture_id != capture_id:
        raise HTTPException(status_code=404, detail="artifact not found")
    try:
        path = store.open(artifact.object_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact file not found") from exc
    return FileResponse(
        path, media_type=artifact.mime_type, filename=artifact.object_key.split("/")[-1]
    )
