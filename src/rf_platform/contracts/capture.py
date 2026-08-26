from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from rf_platform.contracts._base import UtcDatetimeMixin, VersionedContract


class Schedule(VersionedContract):
    mode: Literal["continuous", "manual"] = "continuous"


class RadioSettings(VersionedContract):
    center_frequency_hz: int = Field(gt=0)
    sample_rate_sps: int = Field(gt=0)
    bandwidth_hz: int = Field(gt=0)
    gain_mode: Literal["manual", "auto"] = "manual"
    gain_db: float | None = None
    antenna: str | None = None
    hardware: dict[str, Any] = Field(default_factory=dict)


class CaptureTiming(VersionedContract):
    duration_ms: int = Field(gt=0)
    interval_ms: int = Field(gt=0)


class PreprocessingSettings(VersionedContract):
    pipeline_version: str
    fft_size: int = Field(gt=0)
    hop_size: int = Field(gt=0)
    window: str
    db_min: float | None = None
    db_max: float | None = None
    image_width_px: int = Field(gt=0)
    image_height_px: int = Field(gt=0)
    color_map: str
    include_axes: bool = False
    time_axis_direction: str = "left-to-right"
    frequency_axis_direction: str = "low-to-high"
    metadata: dict[str, Any] = Field(default_factory=dict)


class PreprocessingProfile(VersionedContract):
    pipeline_version: str
    fft_size: int = Field(gt=0)
    hop_size: int = Field(gt=0)
    window: str
    output_width_px: int = Field(gt=0)
    output_height_px: int = Field(gt=0)
    color_map: str
    include_axes: bool = False
    db_min: float | None = None
    db_max: float | None = None

    def to_capture_settings(self) -> PreprocessingSettings:
        return PreprocessingSettings(
            pipeline_version=self.pipeline_version,
            fft_size=self.fft_size,
            hop_size=self.hop_size,
            window=self.window,
            db_min=self.db_min,
            db_max=self.db_max,
            image_width_px=self.output_width_px,
            image_height_px=self.output_height_px,
            color_map=self.color_map,
            include_axes=self.include_axes,
        )


class RetentionSettings(VersionedContract):
    upload_spectrogram: bool = True
    upload_iq: Literal["never", "triggered", "always"] = "triggered"
    local_iq_ring_seconds: int = Field(ge=0)


class CaptureProfile(VersionedContract):
    profile_id: str
    description: str
    enabled: bool = True
    schedule: Schedule
    radio: RadioSettings
    capture: CaptureTiming
    preprocessing: PreprocessingProfile
    retention: RetentionSettings


class DSPMetrics(VersionedContract):
    noise_floor_db: float | None = None
    peak_power_db: float | None = None
    occupied_bandwidth_hz: float | None = None


class ArtifactDescriptor(VersionedContract):
    kind: Literal["spectrogram", "iq", "metadata"]
    filename: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)

    @field_validator("filename")
    @classmethod
    def reject_path_components(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {".", ".."} or ".." in value:
            raise ValueError("filename must be a safe basename")
        return value


class CaptureEnvelope(UtcDatetimeMixin, VersionedContract):
    capture_id: str
    sensor_id: str
    session_id: str | None = None
    correlation_id: str
    profile_id: str
    started_at_utc: datetime
    ended_at_utc: datetime
    radio: RadioSettings
    preprocessing: PreprocessingSettings
    dsp_metrics: DSPMetrics
    artifacts: list[ArtifactDescriptor]
    created_at_utc: datetime

    @model_validator(mode="after")
    def validate_time_range(self) -> CaptureEnvelope:
        if self.ended_at_utc <= self.started_at_utc:
            raise ValueError("ended_at_utc must be after started_at_utc")
        return self


class CaptureIngestResponse(VersionedContract):
    capture_id: str
    ingestion_status: Literal["accepted", "duplicate"]
    job_id: str
