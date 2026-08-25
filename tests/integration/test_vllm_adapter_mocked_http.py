from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from PIL import Image

from rf_platform.common.config import Settings
from rf_platform.contracts.analysis import AnalysisRequest
from rf_platform.worker.rfgpt.local import LocalVLLMRFGPTAdapter


@pytest.mark.asyncio
@respx.mock
async def test_local_vllm_adapter_against_mocked_openai_http_endpoint(tmp_path: Path) -> None:
    image_path = tmp_path / "canonical.png"
    Image.new("RGB", (512, 512), color=(10, 20, 30)).save(image_path, format="PNG")
    respx.get("http://127.0.0.1:8090/health").mock(return_value=httpx.Response(200, text="ok"))
    respx.get("http://127.0.0.1:8090/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "rfgpt"}]})
    )
    content = json.dumps(
        {
            "technologies": [],
            "signals": [
                {
                    "label": "wideband-energy",
                    "observation": "Visible RF energy is present without identity claims.",
                    "frequency_start_hz": None,
                    "frequency_end_hz": None,
                    "evidence": ["capture_id:capture-int"],
                }
            ],
            "overall_assessment": "Schema-valid RF observation only.",
            "quality_flags": ["mocked-http"],
        }
    )
    respx.post("http://127.0.0.1:8090/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )

    settings = Settings(
        sensor_token="token",
        rfgpt_adapter="vllm",
        rfgpt_endpoint="http://127.0.0.1:8090/v1",
        rfgpt_model_name="rfgpt",
        rfgpt_model_version="Qwen2.5-VL-7B-rfa-wtr-v2-joint",
        rfgpt_request_timeout_seconds=300,
        worker_concurrency=1,
    )
    result = await LocalVLLMRFGPTAdapter(settings).analyze(
        AnalysisRequest(
            job_id="job-int",
            capture_id="capture-int",
            artifact_keys=["canonical.png"],
            artifact_paths=[image_path],
            sensor_id="sensor-int",
            capture_started_at_utc=datetime(2026, 8, 25, tzinfo=UTC),
            center_frequency_hz=2_440_000_000,
            sample_rate_sps=20_000_000,
            bandwidth_hz=20_000_000,
            gain_db=30,
            profile_id="integration",
            preprocessing_version="atheer-hann-v1",
            prompt_version="technology-detection-v1",
        )
    )

    assert result.status == "succeeded"
    assert result.parser_valid is True
    assert result.signals[0].label == "wideband-energy"
    assert result.technologies == []
    assert result.preprocessing_version == "atheer-hann-v1"
