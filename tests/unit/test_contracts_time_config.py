from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from rf_platform.common.config import Settings
from rf_platform.common.time import resolve_historical_interval
from rf_platform.contracts.capture import (
    ArtifactDescriptor,
    CaptureEnvelope,
    DSPMetrics,
    PreprocessingSettings,
    RadioSettings,
)


def test_secret_redaction() -> None:
    settings = Settings(
        sensor_token="secret-token",
        database_url="postgresql+asyncpg://user:password@localhost/db",
    )
    redacted = settings.redacted()
    assert redacted["sensor_token"] == "***redacted***"
    assert redacted["database_url"] == "***redacted***"


def test_yesterday_at_11_pm_interval() -> None:
    now = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
    interval = resolve_historical_interval("What happened yesterday at 11 PM?", "Asia/Dubai", now)
    assert interval.start_utc.isoformat() == "2026-08-24T19:00:00+00:00"
    assert interval.end_utc.isoformat() == "2026-08-24T20:00:00+00:00"
    assert interval.assumptions


def test_capture_contract_rejects_naive_datetime() -> None:
    kwargs = dict(
        capture_id="a" * 36,
        sensor_id="sensor-1",
        correlation_id="corr",
        profile_id="campus_general",
        started_at_utc=datetime(2026, 8, 25, 0, 0),
        ended_at_utc=datetime(2026, 8, 25, 0, 1, tzinfo=UTC),
        radio=RadioSettings(center_frequency_hz=1, sample_rate_sps=1, bandwidth_hz=1),
        preprocessing=PreprocessingSettings(
            pipeline_version="v1",
            fft_size=512,
            hop_size=512,
            window="blackman",
            image_width_px=512,
            image_height_px=512,
            color_map="viridis",
        ),
        dsp_metrics=DSPMetrics(),
        artifacts=[
            ArtifactDescriptor(
                kind="spectrogram",
                filename="spectrogram.png",
                mime_type="image/png",
                size_bytes=1,
                sha256="0" * 64,
            )
        ],
        created_at_utc=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
    )
    with pytest.raises(ValidationError):
        CaptureEnvelope(**kwargs)


def test_artifact_descriptor_rejects_path_traversal() -> None:
    with pytest.raises(ValidationError):
        ArtifactDescriptor(
            kind="spectrogram",
            filename="../spectrogram.png",
            mime_type="image/png",
            size_bytes=1,
            sha256="0" * 64,
        )
