from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from PIL import Image

from rf_platform.common.config import Settings
from rf_platform.contracts.analysis import AnalysisRequest
from rf_platform.worker.rfgpt.local import (
    LocalVLLMRFGPTAdapter,
    PermanentInputError,
    VLLMConnectionError,
    VLLMHTTPError,
    VLLMMalformedResponseError,
    VLLMModelMismatchError,
    VLLMTimeoutError,
    _extract_json_object,
)


def _settings(**overrides: Any) -> Settings:
    data: dict[str, Any] = {
        "sensor_token": "token",
        "rfgpt_adapter": "vllm",
        "rfgpt_endpoint": "http://vllm.local/v1",
        "rfgpt_model_name": "rfgpt",
        "rfgpt_model_version": "Qwen2.5-VL-7B-rfa-wtr-v2-joint",
        "rfgpt_request_timeout_seconds": 300,
        "worker_concurrency": 1,
    }
    data.update(overrides)
    return Settings(**data)


def _png(path: Path) -> Path:
    Image.new("RGB", (512, 512), color=(1, 2, 3)).save(path, format="PNG")
    return path


def _request(path: Path) -> AnalysisRequest:
    return AnalysisRequest(
        job_id="job-1",
        capture_id="capture-1",
        artifact_keys=["sensor/2026/08/25/capture-1/spectrogram.png"],
        artifact_paths=[path],
        sensor_id="sensor-1",
        capture_started_at_utc=datetime(2026, 8, 25, tzinfo=UTC),
        center_frequency_hz=2_440_000_000,
        sample_rate_sps=20_000_000,
        bandwidth_hz=20_000_000,
        gain_db=30.0,
        profile_id="campus_general",
        preprocessing_version="atheer-hann-v1",
        prompt_version="technology-detection-v1",
    )


def _chat_response(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


def _valid_content() -> str:
    return json.dumps(
        {
            "technologies": [
                {
                    "label": "rf-burst-like",
                    "model_score": None,
                    "observation": "Short RF-only burst structure is visible.",
                    "evidence": ["capture_id:capture-1"],
                }
            ],
            "signals": [],
            "overall_assessment": "Single RF observation; not independently confirmed.",
            "quality_flags": [],
        }
    )


def _mock_ready() -> None:
    respx.get("http://vllm.local/health").mock(return_value=httpx.Response(200, text="ok"))
    respx.get("http://vllm.local/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "rfgpt"}]})
    )


def test_vllm_configuration_values_are_environment_driven() -> None:
    settings = _settings(
        rfgpt_temperature=0,
        rfgpt_top_p=1,
        rfgpt_repetition_penalty=1,
        rfgpt_max_output_tokens=512,
    )
    assert settings.rfgpt_adapter == "vllm"
    assert settings.rfgpt_endpoint == "http://vllm.local/v1"
    assert settings.rfgpt_model_name == "rfgpt"
    assert settings.rfgpt_request_timeout_seconds == 300
    assert settings.worker_concurrency == 1


@pytest.mark.asyncio
@respx.mock
async def test_health_and_model_discovery_success() -> None:
    _mock_ready()
    health = await LocalVLLMRFGPTAdapter(_settings()).health()
    assert health.ready is True
    assert health.details["served_models"] == ["rfgpt"]


@pytest.mark.asyncio
@respx.mock
async def test_model_mismatch_is_explicit(tmp_path: Path) -> None:
    respx.get("http://vllm.local/health").mock(return_value=httpx.Response(200, text="ok"))
    respx.get("http://vllm.local/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "other"}]})
    )
    with pytest.raises(VLLMModelMismatchError):
        await LocalVLLMRFGPTAdapter(_settings()).analyze(_request(_png(tmp_path / "a.png")))


@pytest.mark.asyncio
@respx.mock
async def test_successful_vllm_response_records_prompt_and_preprocessing(tmp_path: Path) -> None:
    _mock_ready()
    chat = respx.post("http://vllm.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response(_valid_content()))
    )
    result = await LocalVLLMRFGPTAdapter(_settings()).analyze(_request(_png(tmp_path / "a.png")))

    assert result.status == "succeeded"
    assert result.parser_valid is True
    assert result.model.version == "Qwen2.5-VL-7B-rfa-wtr-v2-joint"
    assert result.preprocessing_version == "atheer-hann-v1"
    assert result.inference_parameters["temperature"] == 0.0
    assert result.inference_parameters["top_p"] == 1.0
    assert result.inference_parameters["repetition_penalty"] == 1.0
    assert result.inference_parameters["max_output_tokens"] == 512

    payload = json.loads(chat.calls.last.request.content)
    assert payload["model"] == "rfgpt"
    assert payload["temperature"] == 0.0
    assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    prompt = payload["messages"][1]["content"][0]["text"]
    assert "sensor-1" in prompt
    assert "atheer-hann-v1" in prompt
    assert "Never identify a person" in payload["messages"][0]["content"]


@pytest.mark.asyncio
@respx.mock
async def test_timeout_is_classified(tmp_path: Path) -> None:
    _mock_ready()
    respx.post("http://vllm.local/v1/chat/completions").mock(
        side_effect=httpx.TimeoutException("timeout")
    )
    with pytest.raises(VLLMTimeoutError) as exc:
        await LocalVLLMRFGPTAdapter(_settings()).analyze(_request(_png(tmp_path / "a.png")))
    assert exc.value.category == "model_timeout"
    assert exc.value.retryable is True


@pytest.mark.asyncio
@respx.mock
async def test_connection_failure_health_is_unready() -> None:
    respx.get("http://vllm.local/health").mock(side_effect=httpx.ConnectError("connection failed"))
    health = await LocalVLLMRFGPTAdapter(_settings()).health()
    assert health.ready is False
    assert health.details["category"] == "model_unavailable"


@pytest.mark.asyncio
@respx.mock
async def test_connection_failure_is_classified(tmp_path: Path) -> None:
    _mock_ready()
    respx.post("http://vllm.local/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("connection failed")
    )
    with pytest.raises(VLLMConnectionError) as exc:
        await LocalVLLMRFGPTAdapter(_settings()).analyze(_request(_png(tmp_path / "a.png")))
    assert exc.value.category == "model_unavailable"
    assert exc.value.retryable is True


@pytest.mark.asyncio
@respx.mock
async def test_http_failure_is_explicit(tmp_path: Path) -> None:
    _mock_ready()
    respx.post("http://vllm.local/v1/chat/completions").mock(
        return_value=httpx.Response(503, text="busy")
    )
    with pytest.raises(VLLMHTTPError) as exc:
        await LocalVLLMRFGPTAdapter(_settings()).analyze(_request(_png(tmp_path / "a.png")))
    assert exc.value.category == "model_http_error"
    assert exc.value.retryable is True


@pytest.mark.asyncio
@respx.mock
async def test_malformed_model_json_is_preserved_as_parser_invalid(tmp_path: Path) -> None:
    _mock_ready()
    raw = _chat_response("this is not json")
    respx.post("http://vllm.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=raw)
    )
    result = await LocalVLLMRFGPTAdapter(_settings()).analyze(_request(_png(tmp_path / "a.png")))
    assert result.status == "parser_failed"
    assert result.parser_valid is False
    assert result.technologies == []
    assert json.loads(result.raw_response) == raw


@pytest.mark.asyncio
@respx.mock
async def test_prohibited_claims_are_parser_invalid_and_untrusted(tmp_path: Path) -> None:
    _mock_ready()
    content = json.dumps(
        {
            "technologies": [
                {
                    "label": "disciplinary-claim",
                    "model_score": None,
                    "observation": "The person is cheating.",
                    "evidence": ["capture_id:capture-1"],
                }
            ],
            "signals": [],
            "overall_assessment": "The person is cheating.",
            "quality_flags": [],
        }
    )
    respx.post("http://vllm.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response(content))
    )
    result = await LocalVLLMRFGPTAdapter(_settings()).analyze(_request(_png(tmp_path / "a.png")))
    assert result.status == "parser_failed"
    assert result.parser_valid is False
    assert result.technologies == []
    assert result.signals == []
    assert "Parser failed" in result.overall_assessment


@pytest.mark.asyncio
@respx.mock
async def test_malformed_http_response_is_explicit(tmp_path: Path) -> None:
    _mock_ready()
    respx.post("http://vllm.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    with pytest.raises(VLLMMalformedResponseError):
        await LocalVLLMRFGPTAdapter(_settings()).analyze(_request(_png(tmp_path / "a.png")))


def test_extract_json_object_accepts_fenced_json() -> None:
    parsed = _extract_json_object('```json\n{"technologies": []}\n```')
    assert parsed == {"technologies": []}


def test_retry_classification_values() -> None:
    timeout = VLLMTimeoutError("timeout")
    mismatch = VLLMModelMismatchError("missing")
    permanent_input = PermanentInputError("missing image")
    assert timeout.category == "model_timeout"
    assert timeout.retryable is True
    assert mismatch.category == "model_mismatch"
    assert mismatch.retryable is False
    assert permanent_input.category == "permanent_input_failure"
    assert permanent_input.retryable is False
