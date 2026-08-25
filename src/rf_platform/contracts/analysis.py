from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from rf_platform.contracts._base import UtcDatetimeMixin, VersionedContract


class ModelIdentity(VersionedContract):
    name: str
    version: str
    adapter: str
    prompt_version: str


class TechnologyFinding(VersionedContract):
    label: str
    model_score: float | None = Field(default=None, ge=0, le=1)
    observation: str
    evidence: list[str]


class SignalFinding(VersionedContract):
    label: str
    observation: str
    frequency_start_hz: int | None = None
    frequency_end_hz: int | None = None
    evidence: list[str] = Field(default_factory=list)


class AnalysisResult(UtcDatetimeMixin, VersionedContract):
    analysis_id: str
    capture_id: str
    model: ModelIdentity
    status: Literal["succeeded", "failed", "parser_failed"]
    started_at_utc: datetime
    completed_at_utc: datetime
    latency_ms: int = Field(ge=0)
    technologies: list[TechnologyFinding]
    signals: list[SignalFinding] = Field(default_factory=list)
    overall_assessment: str
    quality_flags: list[str] = Field(default_factory=list)
    parser_valid: bool
    raw_response: str

    @model_validator(mode="after")
    def validate_time_range(self) -> AnalysisResult:
        if self.completed_at_utc < self.started_at_utc:
            raise ValueError("completed_at_utc cannot be before started_at_utc")
        return self


class AnalysisRequest(VersionedContract):
    job_id: str
    capture_id: str
    artifact_keys: list[str]
    prompt_version: str = "technology-detection-v1"


class ModelHealth(VersionedContract):
    adapter: str
    ready: bool
    model_name: str
    model_version: str
    message: str | None = None
