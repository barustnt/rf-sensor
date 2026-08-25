from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from rf_platform.contracts._base import UtcDatetimeMixin, VersionedContract


class EvidenceRef(VersionedContract):
    target_type: Literal["capture", "analysis", "finding", "artifact", "event"]
    target_id: str


class RFEvent(UtcDatetimeMixin, VersionedContract):
    event_id: str
    event_kind: str
    severity: Literal["info", "warning", "critical"] = "info"
    started_at_utc: datetime
    ended_at_utc: datetime
    sensor_ids: list[str]
    capture_ids: list[str]
    analysis_ids: list[str]
    findings: list[dict]
    summary: str
    evidence: list[EvidenceRef]
    status: Literal["open", "acknowledged", "dismissed", "confirmed"] = "open"
    annotations: list[dict] = Field(default_factory=list)
    created_at_utc: datetime
    updated_at_utc: datetime

    @model_validator(mode="after")
    def validate_time_range(self) -> RFEvent:
        if self.ended_at_utc < self.started_at_utc:
            raise ValueError("ended_at_utc cannot be before started_at_utc")
        return self


class Alert(UtcDatetimeMixin, VersionedContract):
    alert_id: str
    rule_id: str
    rule_version: str
    event_id: str
    reason: str
    thresholds: dict
    status: Literal["open", "acknowledged", "dismissed", "confirmed"] = "open"
    acknowledged_by: str | None = None
    acknowledged_at_utc: datetime | None = None
    created_at_utc: datetime
    updated_at_utc: datetime
    evidence: list[EvidenceRef]
