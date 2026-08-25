from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from rf_platform.backend.db.base import Base
from rf_platform.common.ids import new_id
from rf_platform.common.time import utc_now


def _json_default() -> dict[str, Any]:
    return {}


def _list_default() -> list[Any]:
    return []


class Sensor(Base):
    __tablename__ = "sensors"

    sensor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    node_type: Mapped[str] = mapped_column(String(64), default="edge_sensor")
    adapter: Mapped[str] = mapped_column(String(64))
    location: Mapped[dict[str, Any]] = mapped_column(JSON, default=_json_default)
    groups: Mapped[list[Any]] = mapped_column(JSON, default=_list_default)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=_json_default)
    desired_profile: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active_profile: Mapped[str | None] = mapped_column(String(128), nullable=True)
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    software_version: Mapped[str] = mapped_column(String(64))
    last_source_ip: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    registered_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    operational_status: Mapped[str] = mapped_column(String(32), default="registered")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SensorHeartbeatRow(Base):
    __tablename__ = "sensor_heartbeats"
    __table_args__ = (
        UniqueConstraint("sensor_id", "sequence", name="uq_sensor_heartbeat_sequence"),
        Index("ix_sensor_heartbeats_sensor_timestamp", "sensor_id", "timestamp_utc"),
        Index("ix_sensor_heartbeats_timestamp", "timestamp_utc"),
    )

    heartbeat_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sensor_id: Mapped[str] = mapped_column(ForeignKey("sensors.sensor_id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))
    active_profile: Mapped[str] = mapped_column(String(128))
    disk: Mapped[dict[str, Any]] = mapped_column(JSON)
    spool: Mapped[dict[str, Any]] = mapped_column(JSON)
    system: Mapped[dict[str, Any]] = mapped_column(JSON)
    radio: Mapped[dict[str, Any]] = mapped_column(JSON)
    last_capture_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    clock_offset_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    received_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CaptureProfileRow(Base):
    __tablename__ = "capture_profiles"
    __table_args__ = (UniqueConstraint("profile_id", "version", name="uq_capture_profile_version"),)

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(32), default="1.0")
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Capture(Base):
    __tablename__ = "captures"
    __table_args__ = (
        Index("ix_captures_time", "started_at_utc"),
        Index("ix_captures_sensor_time", "sensor_id", "started_at_utc"),
        Index("ix_captures_profile_time", "profile_id", "started_at_utc"),
        Index("ix_captures_session_time", "session_id", "started_at_utc"),
    )

    capture_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sensor_id: Mapped[str] = mapped_column(ForeignKey("sensors.sensor_id", ondelete="RESTRICT"))
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128))
    profile_id: Mapped[str] = mapped_column(String(128))
    started_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    radio: Mapped[dict[str, Any]] = mapped_column(JSON)
    preprocessing: Mapped[dict[str, Any]] = mapped_column(JSON)
    dsp_metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(32), default="accepted")
    metadata_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("capture_id", "kind", "sha256", name="uq_artifact_capture_kind_sha"),
    )

    artifact_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    capture_id: Mapped[str] = mapped_column(ForeignKey("captures.capture_id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(64))
    backend: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(128))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    retention_class: Mapped[str] = mapped_column(String(64), default="ordinary")
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DSPObservation(Base):
    __tablename__ = "dsp_observations"

    observation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    capture_id: Mapped[str] = mapped_column(ForeignKey("captures.capture_id", ondelete="CASCADE"))
    algorithm: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(64))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (
        UniqueConstraint(
            "capture_id",
            "model_name",
            "model_version",
            "prompt_version",
            name="uq_analysis_job_model_prompt",
        ),
        Index("ix_analysis_jobs_status_available", "status", "available_at_utc"),
    )

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    capture_id: Mapped[str] = mapped_column(ForeignKey("captures.capture_id", ondelete="CASCADE"))
    model_name: Mapped[str] = mapped_column(String(128))
    model_version: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    available_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModelRun(Base):
    __tablename__ = "model_runs"

    analysis_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_jobs.job_id", ondelete="CASCADE"), unique=True
    )
    capture_id: Mapped[str] = mapped_column(ForeignKey("captures.capture_id", ondelete="CASCADE"))
    model_name: Mapped[str] = mapped_column(String(128))
    model_version: Mapped[str] = mapped_column(String(128))
    adapter: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(128))
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    structured_result: Mapped[dict[str, Any]] = mapped_column(JSON)
    raw_response: Mapped[str] = mapped_column(Text)
    parser_valid: Mapped[bool] = mapped_column(Boolean)
    started_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModelFinding(Base):
    __tablename__ = "model_findings"
    __table_args__ = (Index("ix_model_findings_label", "label"),)

    finding_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("model_runs.analysis_id", ondelete="CASCADE")
    )
    capture_id: Mapped[str] = mapped_column(ForeignKey("captures.capture_id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32), default="technology")
    label: Mapped[str] = mapped_column(String(128))
    model_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    observation: Mapped[str] = mapped_column(Text)
    frequency_start_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frequency_end_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at_offset_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ended_at_offset_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_time", "started_at_utc", "ended_at_utc"),)

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    event_kind: Mapped[str] = mapped_column(String(128))
    severity: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="open")
    started_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sensor_ids: Mapped[list[Any]] = mapped_column(JSON)
    capture_ids: Mapped[list[Any]] = mapped_column(JSON)
    analysis_ids: Mapped[list[Any]] = mapped_column(JSON)
    findings: Mapped[list[Any]] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[Any]] = mapped_column(JSON)
    rule_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EventEvidence(Base):
    __tablename__ = "event_evidence"

    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"))
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(128))
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AlertRow(Base):
    __tablename__ = "alerts"

    alert_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"))
    rule_id: Mapped[str] = mapped_column(String(128))
    rule_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="open")
    reason: Mapped[str] = mapped_column(Text)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidence: Mapped[list[Any]] = mapped_column(JSON)
    acknowledged_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    acknowledged_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Annotation(Base):
    __tablename__ = "annotations"

    annotation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(128))
    label: Mapped[str] = mapped_column(String(128))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(128))
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SystemEvent(Base):
    __tablename__ = "system_events"
    __table_args__ = (Index("ix_system_events_timestamp", "timestamp_utc"),)

    system_event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    severity: Mapped[str] = mapped_column(String(32))
    service: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    sensor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=_json_default)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_events_status", "status", "created_at_utc"),)

    outbox_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    subject: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class StorageSnapshot(Base):
    __tablename__ = "storage_snapshots"
    __table_args__ = (
        Index("ix_storage_snapshots_target_time", "target_type", "target_id", "timestamp_utc"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(128))
    label: Mapped[str] = mapped_column(String(255))
    total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    free_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    used_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    artifact_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    spool_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pending_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=_json_default)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RetentionReport(Base):
    __tablename__ = "retention_reports"

    report_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    report_only: Mapped[bool] = mapped_column(Boolean, default=True)
    policy: Mapped[dict[str, Any]] = mapped_column(JSON)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    items: Mapped[list[Any]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(128), default="operator")
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
