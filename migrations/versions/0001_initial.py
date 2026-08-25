"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-25 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sensors",
        sa.Column("sensor_id", sa.String(length=128), primary_key=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("node_type", sa.String(length=64), nullable=False, server_default="edge_sensor"),
        sa.Column("adapter", sa.String(length=64), nullable=False),
        sa.Column("location", sa.JSON(), nullable=False),
        sa.Column("groups", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("desired_profile", sa.String(length=128), nullable=True),
        sa.Column("active_profile", sa.String(length=128), nullable=True),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("software_version", sa.String(length=64), nullable=False),
        sa.Column("last_source_ip", sa.String(length=128), nullable=True),
        sa.Column("last_hostname", sa.String(length=255), nullable=True),
        sa.Column("registered_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("operational_status", sa.String(length=32), nullable=False, server_default="registered"),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_table(
        "capture_profiles",
        sa.Column("row_id", sa.String(length=36), primary_key=True),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False, server_default="1.0"),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("profile_id", "version", name="uq_capture_profile_version"),
    )
    op.create_table(
        "sensor_heartbeats",
        sa.Column("heartbeat_id", sa.String(length=36), primary_key=True),
        sa.Column("sensor_id", sa.String(length=128), sa.ForeignKey("sensors.sensor_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("timestamp_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("active_profile", sa.String(length=128), nullable=False),
        sa.Column("disk", sa.JSON(), nullable=False),
        sa.Column("spool", sa.JSON(), nullable=False),
        sa.Column("system", sa.JSON(), nullable=False),
        sa.Column("radio", sa.JSON(), nullable=False),
        sa.Column("last_capture_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clock_offset_ms", sa.Float(), nullable=True),
        sa.Column("received_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sensor_id", "sequence", name="uq_sensor_heartbeat_sequence"),
    )
    op.create_index("ix_sensor_heartbeats_sensor_timestamp", "sensor_heartbeats", ["sensor_id", "timestamp_utc"])
    op.create_index("ix_sensor_heartbeats_timestamp", "sensor_heartbeats", ["timestamp_utc"])
    op.create_table(
        "captures",
        sa.Column("capture_id", sa.String(length=36), primary_key=True),
        sa.Column("sensor_id", sa.String(length=128), sa.ForeignKey("sensors.sensor_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("radio", sa.JSON(), nullable=False),
        sa.Column("preprocessing", sa.JSON(), nullable=False),
        sa.Column("dsp_metrics", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="accepted"),
        sa.Column("metadata_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_captures_time", "captures", ["started_at_utc"])
    op.create_index("ix_captures_sensor_time", "captures", ["sensor_id", "started_at_utc"])
    op.create_index("ix_captures_profile_time", "captures", ["profile_id", "started_at_utc"])
    op.create_index("ix_captures_session_time", "captures", ["session_id", "started_at_utc"])
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(length=36), primary_key=True),
        sa.Column("capture_id", sa.String(length=36), sa.ForeignKey("captures.capture_id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("backend", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("retention_class", sa.String(length=64), nullable=False, server_default="ordinary"),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("capture_id", "kind", "sha256", name="uq_artifact_capture_kind_sha"),
    )
    op.create_table(
        "dsp_observations",
        sa.Column("observation_id", sa.String(length=36), primary_key=True),
        sa.Column("capture_id", sa.String(length=36), sa.ForeignKey("captures.capture_id", ondelete="CASCADE"), nullable=False),
        sa.Column("algorithm", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "analysis_jobs",
        sa.Column("job_id", sa.String(length=36), primary_key=True),
        sa.Column("capture_id", sa.String(length=36), sa.ForeignKey("captures.capture_id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("capture_id", "model_name", "model_version", "prompt_version", name="uq_analysis_job_model_prompt"),
    )
    op.create_index("ix_analysis_jobs_status_available", "analysis_jobs", ["status", "available_at_utc"])
    op.create_table(
        "model_runs",
        sa.Column("analysis_id", sa.String(length=36), primary_key=True),
        sa.Column("job_id", sa.String(length=36), sa.ForeignKey("analysis_jobs.job_id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("capture_id", sa.String(length=36), sa.ForeignKey("captures.capture_id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("adapter", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("structured_result", sa.JSON(), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=False),
        sa.Column("parser_valid", sa.Boolean(), nullable=False),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "model_findings",
        sa.Column("finding_id", sa.String(length=36), primary_key=True),
        sa.Column("analysis_id", sa.String(length=36), sa.ForeignKey("model_runs.analysis_id", ondelete="CASCADE"), nullable=False),
        sa.Column("capture_id", sa.String(length=36), sa.ForeignKey("captures.capture_id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="technology"),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("model_score", sa.Float(), nullable=True),
        sa.Column("observation", sa.Text(), nullable=False),
        sa.Column("frequency_start_hz", sa.Integer(), nullable=True),
        sa.Column("frequency_end_hz", sa.Integer(), nullable=True),
        sa.Column("started_at_offset_ms", sa.Integer(), nullable=True),
        sa.Column("ended_at_offset_ms", sa.Integer(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_model_findings_label", "model_findings", ["label"])
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=36), primary_key=True),
        sa.Column("schema_version", sa.String(length=16), nullable=False, server_default="1.0"),
        sa.Column("event_kind", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sensor_ids", sa.JSON(), nullable=False),
        sa.Column("capture_ids", sa.JSON(), nullable=False),
        sa.Column("analysis_ids", sa.JSON(), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("rule_id", sa.String(length=128), nullable=True),
        sa.Column("rule_version", sa.String(length=64), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_events_time", "events", ["started_at_utc", "ended_at_utc"])
    op.create_table(
        "event_evidence",
        sa.Column("evidence_id", sa.String(length=36), primary_key=True),
        sa.Column("event_id", sa.String(length=36), sa.ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "alerts",
        sa.Column("alert_id", sa.String(length=36), primary_key=True),
        sa.Column("event_id", sa.String(length=36), sa.ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_id", sa.String(length=128), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("thresholds", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("acknowledged_by", sa.String(length=128), nullable=True),
        sa.Column("acknowledged_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "annotations",
        sa.Column("annotation_id", sa.String(length=36), primary_key=True),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("timestamp_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "system_events",
        sa.Column("system_event_id", sa.String(length=36), primary_key=True),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("sensor_id", sa.String(length=128), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("timestamp_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_system_events_timestamp", "system_events", ["timestamp_utc"])
    op.create_table(
        "outbox_events",
        sa.Column("outbox_id", sa.String(length=36), primary_key=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at_utc", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outbox_events_status", "outbox_events", ["status", "created_at_utc"])


def downgrade() -> None:
    for table in [
        "outbox_events",
        "system_events",
        "annotations",
        "alerts",
        "event_evidence",
        "events",
        "model_findings",
        "model_runs",
        "analysis_jobs",
        "dsp_observations",
        "artifacts",
        "captures",
        "sensor_heartbeats",
        "capture_profiles",
        "sensors",
    ]:
        op.drop_table(table)
