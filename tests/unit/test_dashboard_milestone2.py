from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import httpx
import respx

from rf_platform.common.config import Settings
from rf_platform.dashboard.api_client import DashboardApiClient
from rf_platform.dashboard.tabs.outputs import render_output_detail


def _settings() -> Settings:
    return Settings(sensor_token="token", platform_url="http://api.local")


@respx.mock
def test_dashboard_api_client_sends_filters_and_pagination() -> None:
    route = respx.get("http://api.local/api/v1/analyses").mock(
        return_value=httpx.Response(200, json={"items": [], "count": 0, "total": 0})
    )
    client = DashboardApiClient(_settings())

    client.outputs(
        sensor_id="sensor-1",
        profile_id="campus_general",
        location="lab",
        technology="bluetooth-like",
        model_version="mock-v1",
        prompt_version="technology-detection-v1",
        status="succeeded",
        start_utc="2026-08-25T00:00:00Z",
        end_utc="2026-08-26T00:00:00Z",
        limit=25,
        offset=50,
    )

    params = route.calls.last.request.url.params
    assert params["sensor_id"] == "sensor-1"
    assert params["profile_id"] == "campus_general"
    assert params["location"] == "lab"
    assert params["technology"] == "bluetooth-like"
    assert params["model_version"] == "mock-v1"
    assert params["prompt_version"] == "technology-detection-v1"
    assert params["status"] == "succeeded"
    assert params["start_utc"] == "2026-08-25T00:00:00Z"
    assert params["end_utc"] == "2026-08-26T00:00:00Z"
    assert params["limit"] == "25"
    assert params["offset"] == "50"


@respx.mock
def test_dashboard_api_client_alert_and_retry_mutations_are_api_calls() -> None:
    alert = respx.patch("http://api.local/api/v1/alerts/alert-1").mock(
        return_value=httpx.Response(200, json={"alert_id": "alert-1", "status": "confirmed"})
    )
    retry = respx.post("http://api.local/api/v1/analyses/jobs/job-1/retry").mock(
        return_value=httpx.Response(200, json={"job_id": "job-1", "status": "pending"})
    )
    client = DashboardApiClient(_settings())

    assert (
        client.update_alert("alert-1", "confirmed", "operator", "reviewed")["status"] == "confirmed"
    )
    assert client.retry_job("job-1", "operator", "retry after fixture")["status"] == "pending"
    assert json.loads(alert.calls.last.request.content) == {
        "status": "confirmed",
        "actor": "operator",
        "comment": "reviewed",
    }
    assert json.loads(retry.calls.last.request.content) == {
        "actor": "operator",
        "comment": "retry after fixture",
    }


def test_render_output_detail_is_readable_and_includes_limitations() -> None:
    class FakeClient:
        base_url = "http://api.local"

        def output_detail(self, analysis_id: str) -> dict[str, object]:
            return {
                "analysis_id": analysis_id,
                "capture_id": "capture-1",
                "model": {
                    "name": "rfgpt",
                    "version": "mock-v1",
                    "adapter": "mock",
                    "prompt_version": "technology-detection-v1",
                },
                "latency_ms": 10,
                "parser_valid": True,
                "quality_flags": [],
                "capture": {
                    "sensor_id": "sensor-1",
                    "profile_id": "campus_general",
                    "started_at_utc": "2026-08-25T00:00:00+00:00",
                    "ended_at_utc": "2026-08-25T00:00:01+00:00",
                    "radio": {"center_frequency_hz": 2_440_000_000},
                    "preprocessing": {"pipeline_version": "v1"},
                },
                "sensor": {"location": {"room": "lab"}},
                "technologies": [
                    {
                        "label": "bluetooth-like",
                        "model_score": None,
                        "observation": "fixture observation",
                        "evidence": ["capture_id:capture-1"],
                    }
                ],
                "artifacts": [
                    {
                        "kind": "spectrogram",
                        "object_key": "sensor/capture/spectrogram.png",
                        "byte_size": 123,
                        "preview_url": "/api/v1/captures/capture-1/artifacts/artifact-1",
                    }
                ],
                "linked_events": [{"event_id": "event-1", "status": "open", "summary": "event"}],
                "linked_alerts": [{"alert_id": "alert-1", "status": "open", "reason": "rule"}],
                "annotations": [],
                "raw_response": "{}",
            }

    markdown = render_output_detail(cast(DashboardApiClient, FakeClient()), "analysis-1")

    assert "### Structured findings" in markdown
    assert "bluetooth-like" in markdown
    assert "mock-v1" in markdown
    assert "![Spectrogram preview]" in markdown
    assert "not verified ground truth" in markdown


def test_dashboard_package_uses_api_client_only() -> None:
    dashboard_files = Path("src/rf_platform/dashboard").rglob("*.py")
    haystack = "\n".join(path.read_text(encoding="utf-8") for path in dashboard_files)
    assert "sqlalchemy" not in haystack.lower()
    assert "backend.db" not in haystack.lower()
    assert "SensorService" not in haystack
