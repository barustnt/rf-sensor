from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rf_platform.backend.db import models
from rf_platform.backend.services.artifacts import FilesystemArtifactStore
from rf_platform.common.broker import ANALYSIS_COMPLETED, DEADLETTER, EVENT_CREATED, NatsEventBus
from rf_platform.common.config import Settings
from rf_platform.common.time import utc_now
from rf_platform.contracts.analysis import AnalysisRequest
from rf_platform.worker.correlation import correlate_result
from rf_platform.worker.rfgpt.base import RFGPTAdapter
from rf_platform.worker.rfgpt.local import RFGPTAdapterError
from rf_platform.worker.validation import validate_analysis_result


class WorkerProcessor:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        store: FilesystemArtifactStore,
        bus: NatsEventBus,
        adapter: RFGPTAdapter,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.store = store
        self.bus = bus
        self.adapter = adapter

    async def process_payload(self, payload: dict[str, Any]) -> str:
        job_id = str(payload["job_id"])
        async with self.sessionmaker() as session:
            job = await session.get(models.AnalysisJob, job_id)
            if job is None:
                return "missing"
            existing_run = (
                await session.execute(
                    select(models.ModelRun).where(models.ModelRun.job_id == job_id)
                )
            ).scalar_one_or_none()
            if job.status == "succeeded" and existing_run is not None:
                return "duplicate"
            capture = await session.get(models.Capture, job.capture_id)
            if capture is None:
                await self._fail_job(session, job, "permanent_input_failure", "capture not found")
                return "failed"
            artifacts = list(
                (
                    await session.execute(
                        select(models.Artifact).where(
                            models.Artifact.capture_id == capture.capture_id,
                            models.Artifact.kind == "spectrogram",
                        )
                    )
                ).scalars()
            )
            if not artifacts:
                await self._fail_job(session, job, "permanent_input_failure", "spectrogram missing")
                return "failed"
            artifact = artifacts[0]
            if not self.store.verify_existing(
                artifact.object_key, artifact.sha256, artifact.byte_size
            ):
                await self._fail_job(
                    session, job, "permanent_input_failure", "artifact hash mismatch"
                )
                session.add(
                    models.SystemEvent(
                        severity="critical",
                        service="worker",
                        event_type="artifact_hash_mismatch",
                        message=f"Artifact verification failed for capture {capture.capture_id}",
                        sensor_id=capture.sensor_id,
                        correlation_id=capture.correlation_id,
                        context={"job_id": job.job_id, "artifact_id": artifact.artifact_id},
                        timestamp_utc=utc_now(),
                    )
                )
                await session.commit()
                return "failed"
            job.status = "running"
            job.started_at_utc = utc_now()
            job.attempt_count += 1
            job.updated_at_utc = utc_now()
            await session.commit()

        artifact_path = self.store.open(artifact.object_key)
        request = AnalysisRequest(
            job_id=job_id,
            capture_id=str(payload["capture_id"]),
            artifact_keys=[artifact.object_key],
            artifact_paths=[artifact_path],
            sensor_id=capture.sensor_id,
            capture_started_at_utc=capture.started_at_utc,
            center_frequency_hz=capture.radio.get("center_frequency_hz"),
            sample_rate_sps=capture.radio.get("sample_rate_sps"),
            bandwidth_hz=capture.radio.get("bandwidth_hz"),
            gain_db=capture.radio.get("gain_db"),
            profile_id=capture.profile_id,
            preprocessing_version=capture.preprocessing.get("pipeline_version"),
            prompt_version=str(payload.get("prompt_version", "technology-detection-v1")),
        )
        try:
            result = validate_analysis_result(await self.adapter.analyze(request))
        except RFGPTAdapterError as exc:
            await self._record_adapter_failure(
                sessionmaker=self.sessionmaker, job_id=job_id, exc=exc
            )
            return "failed"
        except Exception as exc:
            async with self.sessionmaker() as session:
                job = await session.get(models.AnalysisJob, job_id)
                if job is not None:
                    category = "model_failure"
                    if job.attempt_count >= self.settings.worker_max_attempts:
                        job.status = "deadletter"
                        await self.bus.publish(DEADLETTER, {"job_id": job_id, "error": str(exc)})
                    else:
                        job.status = "failed"
                    job.error_category = category
                    job.error_message = f"{exc.__class__.__name__}: {exc}"
                    job.completed_at_utc = utc_now()
                    job.updated_at_utc = utc_now()
                    session.add(
                        models.SystemEvent(
                            severity="error",
                            service="worker",
                            event_type="analysis_failed",
                            message=f"Analysis job {job_id} failed",
                            sensor_id=None,
                            correlation_id=None,
                            context={"job_id": job_id, "error": job.error_message},
                            timestamp_utc=utc_now(),
                        )
                    )
                    await session.commit()
            return "failed"

        async with self.sessionmaker() as session:
            job = await session.get(models.AnalysisJob, job_id)
            capture = await session.get(models.Capture, result.capture_id)
            if job is None or capture is None:
                return "missing"
            existing_run = (
                await session.execute(
                    select(models.ModelRun).where(models.ModelRun.job_id == job_id)
                )
            ).scalar_one_or_none()
            if existing_run is None:
                session.add(
                    models.ModelRun(
                        analysis_id=result.analysis_id,
                        job_id=job.job_id,
                        capture_id=result.capture_id,
                        model_name=result.model.name,
                        model_version=result.model.version,
                        adapter=result.model.adapter,
                        prompt_version=result.model.prompt_version,
                        latency_ms=result.latency_ms,
                        status=result.status,
                        structured_result=result.model_dump(mode="json"),
                        raw_response=result.raw_response,
                        parser_valid=result.parser_valid,
                        started_at_utc=result.started_at_utc,
                        completed_at_utc=result.completed_at_utc,
                        created_at_utc=utc_now(),
                    )
                )
                await session.flush()
                trusted_findings = result.technologies if result.parser_valid else []
                for finding in trusted_findings:
                    session.add(
                        models.ModelFinding(
                            analysis_id=result.analysis_id,
                            capture_id=result.capture_id,
                            kind="technology",
                            label=finding.label,
                            model_score=finding.model_score,
                            observation=finding.observation,
                            created_at_utc=utc_now(),
                        )
                    )
                event = (
                    await correlate_result(session, capture, result)
                    if result.status == "succeeded" and result.parser_valid
                    else None
                )
            else:
                event = None
            if result.status == "succeeded" and result.parser_valid:
                job.status = "succeeded"
                job.error_category = None
                job.error_message = None
                event_type = "analysis_completed"
                severity = "info"
                message = f"Analysis job {job.job_id} completed"
                outcome = "succeeded"
            else:
                job.status = "failed"
                job.error_category = "parser_failure"
                job.error_message = "RF-GPT output failed constrained JSON parsing"
                event_type = "analysis_parser_failed"
                severity = "error"
                message = f"Analysis job {job.job_id} produced parser-invalid output"
                outcome = "failed"
            job.completed_at_utc = result.completed_at_utc
            job.updated_at_utc = utc_now()
            session.add(
                models.SystemEvent(
                    severity=severity,
                    service="worker",
                    event_type=event_type,
                    message=message,
                    sensor_id=capture.sensor_id,
                    correlation_id=capture.correlation_id,
                    context={
                        "analysis_id": result.analysis_id,
                        "capture_id": capture.capture_id,
                        "parser_valid": result.parser_valid,
                        "preprocessing_version": result.preprocessing_version,
                        "inference_parameters": result.inference_parameters,
                    },
                    timestamp_utc=utc_now(),
                )
            )
            await session.commit()
            if result.status == "succeeded" and result.parser_valid:
                await self.bus.publish(
                    ANALYSIS_COMPLETED,
                    {
                        "schema_version": "1.0",
                        "job_id": job.job_id,
                        "analysis_id": result.analysis_id,
                    },
                )
            if event is not None:
                await self.bus.publish(
                    EVENT_CREATED,
                    {
                        "schema_version": "1.0",
                        "event_id": event.event_id,
                        "analysis_id": result.analysis_id,
                    },
                )
        return outcome

    async def _record_adapter_failure(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        job_id: str,
        exc: RFGPTAdapterError,
    ) -> None:
        async with sessionmaker() as session:
            job = await session.get(models.AnalysisJob, job_id)
            if job is None:
                return
            if exc.retryable and job.attempt_count >= self.settings.worker_max_attempts:
                job.status = "deadletter"
                await self.bus.publish(DEADLETTER, {"job_id": job_id, "error": str(exc)})
            else:
                job.status = "failed" if exc.retryable else "deadletter"
                if not exc.retryable:
                    await self.bus.publish(DEADLETTER, {"job_id": job_id, "error": str(exc)})
            job.error_category = exc.category
            job.error_message = f"{exc.__class__.__name__}: {exc}"
            job.completed_at_utc = utc_now()
            job.updated_at_utc = utc_now()
            session.add(
                models.SystemEvent(
                    severity="error",
                    service="worker",
                    event_type="analysis_failed",
                    message=f"Analysis job {job_id} failed",
                    sensor_id=None,
                    correlation_id=None,
                    context={
                        "job_id": job_id,
                        "error": job.error_message,
                        "category": exc.category,
                        "retryable": exc.retryable,
                    },
                    timestamp_utc=utc_now(),
                )
            )
            await session.commit()

    async def _fail_job(
        self,
        session: AsyncSession,
        job: models.AnalysisJob,
        category: str,
        message: str,
    ) -> None:
        job.status = "failed"
        job.error_category = category
        job.error_message = message
        job.completed_at_utc = utc_now()
        job.updated_at_utc = utc_now()
        await session.commit()


def decode_message(data: bytes) -> dict[str, Any]:
    return json.loads(data.decode("utf-8"))
