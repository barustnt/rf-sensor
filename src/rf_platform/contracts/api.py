from __future__ import annotations

from datetime import datetime
from typing import Any

from rf_platform.contracts._base import UtcDatetimeMixin, VersionedContract


class HealthResponse(VersionedContract):
    status: str
    components: dict[str, Any] = {}


class QueryRequest(VersionedContract):
    question: str
    timezone: str = "Asia/Dubai"
    sensor_ids: list[str] = []
    location: str | None = None


class QueryInterval(UtcDatetimeMixin, VersionedContract):
    start_utc: datetime
    end_utc: datetime
    display_timezone: str
    assumptions: list[str] = []


class QuerySummary(VersionedContract):
    capture_count: int
    analysis_count: int
    event_count: int


class QueryEvidence(VersionedContract):
    target_type: str
    target_id: str
    summary: str


class QueryResponse(VersionedContract):
    answer: str
    interpreted_interval: QueryInterval
    summary: QuerySummary
    evidence: list[QueryEvidence]
    limitations: list[str]
