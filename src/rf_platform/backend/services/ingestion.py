from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.backend.services.artifacts import (
    ArtifactError,
    FilesystemArtifactStore,
    fingerprint_metadata,
)
from rf_platform.common.broker import ANALYSIS_REQUESTED, NatsEventBus
from rf_platform.common.config import Settings
from rf_platform.common.ids import new_id
from rf_platform.common.time import utc_now
from rf_platform.contracts.capture import CaptureEnvelope, CaptureIngestResponse

PROMPT_VERSION = "technology-detection-primary-v3"


@dataclass(frozen=True)
class IngestionResult:
    response: CaptureIngestResponse
    created: bool


class IngestionConflict(ValueError):
    pass


async def create_outbox_event(
    session: AsyncSession,
    subject: str,
    payload: dict[str, Any],
) -> models.OutboxEvent:
    row = models.OutboxEvent(
        subject=subject,
        payload=payload,
        status="pending",
        attempt_count=0,
        created_at_utc=utc_now(),
    )
    session.add(row)
    return row


async def publish_pending_outbox(
    session: AsyncSession,
    bus: NatsEventBus,
    limit: int = 50,
) -> int:
    result = await session.execute(
        select(models.OutboxEvent)
        .where(models.OutboxEvent.status == "pending")
        .order_by(models.OutboxEvent.created_at_utc)
        .limit(limit)
    )
    rows = list(result.scalars())
    published = 0
    for row in rows:
        try:
            await bus.publish(row.subject, row.payload)
            row.status = "published"
            row.published_at_utc = utc_now()
            row.attempt_count += 1
            row.error_message = None
            published += 1
        except Exception as exc:  # pragma: no cover - environment specific network failure
            row.attempt_count += 1
            row.error_message = f"{exc.__class__.__name__}: {exc}"
            break
    await session.commit()
    return published


async def ingest_capture(
    session: AsyncSession,
    settings: Settings,
    store: FilesystemArtifactStore,
    envelope: CaptureEnvelope,
    uploads: list[UploadFile],
) -> IngestionResult:
    sensor = await session.get(models.Sensor, envelope.sensor_id)
    if sensor is None:
        raise LookupError(f"unknown sensor: {envelope.sensor_id}")
    if not uploads:
        raise ArtifactError("at least one artifact upload is required")
    declared_by_name = {artifact.filename: artifact for artifact in envelope.artifacts}
    stored = []
    try:
        for upload in uploads:
            declared = declared_by_name.get(upload.filename or "")
            if declared is None:
                raise ArtifactError("uploaded artifact is not declared in metadata")
            stored.append(await store.store_upload(envelope, upload, declared))
        actual_descriptors = [item.descriptor for item in stored]
        normalized = envelope.model_copy(update={"artifacts": actual_descriptors})
        fingerprint = fingerprint_metadata(normalized, actual_descriptors)
        existing = await session.get(models.Capture, envelope.capture_id)
        existing_job: models.AnalysisJob | None = None
        if existing is not None:
            existing_job = await _get_or_create_job(session, settings, existing.capture_id)
            if existing.metadata_fingerprint != fingerprint:
                raise IngestionConflict("capture ID already exists with different content")
            return IngestionResult(
                response=CaptureIngestResponse(
                    capture_id=existing.capture_id,
                    ingestion_status="duplicate",
                    job_id=existing_job.job_id,
                ),
                created=False,
            )

        store.store_metadata(normalized)
        capture = models.Capture(
            capture_id=normalized.capture_id,
            sensor_id=normalized.sensor_id,
            session_id=normalized.session_id,
            correlation_id=normalized.correlation_id,
            profile_id=normalized.profile_id,
            started_at_utc=normalized.started_at_utc,
            ended_at_utc=normalized.ended_at_utc,
            radio=normalized.radio.model_dump(mode="json"),
            preprocessing=normalized.preprocessing.model_dump(mode="json"),
            dsp_metrics=normalized.dsp_metrics.model_dump(mode="json"),
            state="accepted",
            metadata_fingerprint=fingerprint,
            created_at_utc=normalized.created_at_utc,
            received_at_utc=utc_now(),
            updated_at_utc=utc_now(),
        )
        session.add(capture)
        await session.flush()
        for item in stored:
            session.add(
                models.Artifact(
                    capture_id=normalized.capture_id,
                    kind=item.descriptor.kind,
                    backend="filesystem",
                    object_key=item.object_key,
                    mime_type=item.descriptor.mime_type,
                    byte_size=item.descriptor.size_bytes,
                    sha256=item.descriptor.sha256,
                    retention_class="ordinary",
                    created_at_utc=utc_now(),
                )
            )
        await session.flush()
        job = await _get_or_create_job(session, settings, normalized.capture_id)
        await create_outbox_event(
            session,
            ANALYSIS_REQUESTED,
            {
                "schema_version": "1.0",
                "job_id": job.job_id,
                "capture_id": normalized.capture_id,
                "model_name": settings.rfgpt_model_name,
                "model_version": settings.rfgpt_model_version,
                "prompt_version": PROMPT_VERSION,
            },
        )
        session.add(
            models.SystemEvent(
                severity="info",
                service="backend",
                event_type="capture_accepted",
                message=f"Accepted capture {normalized.capture_id}",
                sensor_id=normalized.sensor_id,
                correlation_id=normalized.correlation_id,
                context={"job_id": job.job_id},
                timestamp_utc=utc_now(),
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            reread = await session.get(models.Capture, envelope.capture_id)
            if reread and reread.metadata_fingerprint == fingerprint:
                existing_job = await _get_or_create_job(session, settings, reread.capture_id)
                await session.commit()
                return IngestionResult(
                    response=CaptureIngestResponse(
                        capture_id=reread.capture_id,
                        ingestion_status="duplicate",
                        job_id=existing_job.job_id,
                    ),
                    created=False,
                )
            raise
        return IngestionResult(
            response=CaptureIngestResponse(
                capture_id=normalized.capture_id,
                ingestion_status="accepted",
                job_id=job.job_id,
            ),
            created=True,
        )
    except Exception:
        await session.rollback()
        raise


async def _get_or_create_job(
    session: AsyncSession,
    settings: Settings,
    capture_id: str,
) -> models.AnalysisJob:
    result = await session.execute(
        select(models.AnalysisJob).where(
            models.AnalysisJob.capture_id == capture_id,
            models.AnalysisJob.model_name == settings.rfgpt_model_name,
            models.AnalysisJob.model_version == settings.rfgpt_model_version,
            models.AnalysisJob.prompt_version == PROMPT_VERSION,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    job = models.AnalysisJob(
        job_id=new_id(),
        capture_id=capture_id,
        model_name=settings.rfgpt_model_name,
        model_version=settings.rfgpt_model_version,
        prompt_version=PROMPT_VERSION,
        status="pending",
        attempt_count=0,
        available_at_utc=utc_now(),
        created_at_utc=utc_now(),
        updated_at_utc=utc_now(),
    )
    session.add(job)
    await session.flush()
    return job


async def job_summary(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        select(models.AnalysisJob.status, func.count()).group_by(models.AnalysisJob.status)
    )
    counts = {status: count for status, count in result.all()}
    oldest = await session.execute(
        select(func.min(models.AnalysisJob.created_at_utc)).where(
            models.AnalysisJob.status == "pending"
        )
    )
    oldest_pending = oldest.scalar_one_or_none()
    return {
        "pending": counts.get("pending", 0),
        "running": counts.get("running", 0),
        "succeeded": counts.get("succeeded", 0),
        "failed": counts.get("failed", 0),
        "deadletter": counts.get("deadletter", 0),
        "oldest_pending_at_utc": oldest_pending.isoformat() if oldest_pending else None,
    }
