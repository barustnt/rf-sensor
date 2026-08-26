from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.common.ids import new_id
from rf_platform.common.time import utc_now
from rf_platform.contracts.analysis import AnalysisResult
from rf_platform.worker.rules import RULE_ID, RULE_VERSION, should_create_event
from rf_platform.worker.semantic_markers import (
    result_has_no_signal_marker,
    result_is_semantically_inconsistent,
)


async def correlate_result(
    session: AsyncSession,
    capture: models.Capture,
    result: AnalysisResult,
) -> models.Event | None:
    labels = [finding.label for finding in result.technologies]
    if (
        result.status != "succeeded"
        or not result.parser_valid
        or not should_create_event(labels)
        or result_has_no_signal_marker(result)
        or result_is_semantically_inconsistent(result)
    ):
        return None
    existing = await _event_for_analysis(session, result.analysis_id)
    if existing is not None:
        return existing
    now = utc_now()
    evidence = [
        {"target_type": "sensor", "target_id": capture.sensor_id},
        {"target_type": "capture", "target_id": capture.capture_id},
        {"target_type": "analysis", "target_id": result.analysis_id},
    ]
    event = models.Event(
        event_id=new_id(),
        schema_version="1.0",
        event_kind="technology_observation",
        severity="info",
        status="open",
        started_at_utc=capture.started_at_utc,
        ended_at_utc=capture.ended_at_utc,
        sensor_ids=[capture.sensor_id],
        capture_ids=[capture.capture_id],
        analysis_ids=[result.analysis_id],
        findings=[finding.model_dump(mode="json") for finding in result.technologies],
        summary=(
            f"Unverified {result.model.name} model observation "
            f"(adapter={result.model.adapter}, version={result.model.version}): "
            f"{', '.join(labels)} on sensor {capture.sensor_id}."
        ),
        evidence=evidence,
        rule_id=RULE_ID,
        rule_version=RULE_VERSION,
        created_at_utc=now,
        updated_at_utc=now,
    )
    session.add(event)
    await session.flush()
    for item in evidence:
        session.add(
            models.EventEvidence(
                event_id=event.event_id,
                target_type=item["target_type"],
                target_id=item["target_id"],
                created_at_utc=now,
            )
        )
    session.add(
        models.AlertRow(
            event_id=event.event_id,
            rule_id=RULE_ID,
            rule_version=RULE_VERSION,
            status="open",
            reason=(
                "An unverified structured model finding triggered the configured "
                "technology-observation rule."
            ),
            thresholds={"minimum_findings": 1},
            evidence=evidence,
            created_at_utc=now,
            updated_at_utc=now,
        )
    )
    return event


async def _event_for_analysis(session: AsyncSession, analysis_id: str) -> models.Event | None:
    result = await session.execute(
        select(models.Event)
        .join(models.EventEvidence, models.EventEvidence.event_id == models.Event.event_id)
        .where(
            models.EventEvidence.target_type == "analysis",
            models.EventEvidence.target_id == analysis_id,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()
