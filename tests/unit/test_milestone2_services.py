from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

from rf_platform.backend.db import models
from rf_platform.backend.services.metrics import percentile
from rf_platform.backend.services.storage_history import summarize_trend
from rf_platform.common.config import Settings


def test_mock_model_version_defaults_to_mock_v1() -> None:
    settings = Settings(sensor_token="token")
    assert settings.rfgpt_model_version == "mock-v1"


def test_storage_trend_requires_enough_samples() -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    point = SimpleNamespace(timestamp_utc=now, free_bytes=1_000)
    trend = summarize_trend(cast(list[models.StorageSnapshot], [point]))
    assert trend["status"] == "unknown"
    assert trend["time_to_full_seconds"] is None


def test_storage_trend_estimates_time_to_full_when_filling() -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    points = [
        SimpleNamespace(timestamp_utc=now, free_bytes=800),
        SimpleNamespace(timestamp_utc=now - timedelta(hours=1), free_bytes=900),
        SimpleNamespace(timestamp_utc=now - timedelta(hours=2), free_bytes=1_000),
    ]
    trend = summarize_trend(cast(list[models.StorageSnapshot], points))
    assert trend["status"] == "filling"
    assert trend["free_bytes_per_hour"] == -100.0
    assert trend["time_to_full_seconds"] == 28_800


def test_percentile_uses_sorted_recent_latency_values() -> None:
    assert percentile([200, 10, 100], 50) == 100
    assert percentile([200, 10, 100], 95) == 200
    assert percentile([], 95) is None
