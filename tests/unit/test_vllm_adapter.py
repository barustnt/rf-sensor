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
    NON_RF_FLAGS_REMOVED,
    RF_QUALITY_FLAGS,
    SEMANTIC_INCONSISTENCY,
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
        prompt_version="technology-detection-primary-v4",
    )


def _chat_response(content: str, finish_reason: str | None = "stop") -> dict[str, Any]:
    choice: dict[str, Any] = {"message": {"content": content}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return {"choices": [choice]}


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


async def _analyze_content(tmp_path: Path, content: str, finish_reason: str | None = "stop"):
    _mock_ready()
    raw = _chat_response(content, finish_reason=finish_reason)
    respx.post("http://vllm.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=raw)
    )
    result = await LocalVLLMRFGPTAdapter(_settings()).analyze(_request(_png(tmp_path / "a.png")))
    return result, raw


def test_vllm_default_generation_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RF_RFGPT_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("RF_RFGPT_REPETITION_PENALTY", raising=False)

    settings = _settings()
    assert settings.rfgpt_adapter == "vllm"
    assert settings.rfgpt_endpoint == "http://vllm.local/v1"
    assert settings.rfgpt_model_name == "rfgpt"
    assert settings.rfgpt_request_timeout_seconds == 300
    assert settings.rfgpt_temperature == 0.0
    assert settings.rfgpt_top_p == 1.0
    assert settings.rfgpt_repetition_penalty == 1.05
    assert settings.rfgpt_max_output_tokens == 224
    assert settings.worker_concurrency == 1


def test_vllm_environment_overrides_generation_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RF_RFGPT_REPETITION_PENALTY", "1.2")
    monkeypatch.setenv("RF_RFGPT_MAX_OUTPUT_TOKENS", "256")

    settings = _settings()
    assert settings.rfgpt_repetition_penalty == 1.2
    assert settings.rfgpt_max_output_tokens == 256


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
    assert result.model.prompt_version == "technology-detection-primary-v4"
    assert result.preprocessing_version == "atheer-hann-v1"
    assert result.inference_parameters["temperature"] == 0.0
    assert result.inference_parameters["top_p"] == 1.0
    assert result.inference_parameters["repetition_penalty"] == 1.05
    assert result.inference_parameters["max_output_tokens"] == 224
    assert result.inference_parameters["prompt_version"] == "technology-detection-primary-v4"
    assert result.inference_parameters["response_schema"] == "rfgpt_analysis_primary_v4"

    payload = json.loads(chat.calls.last.request.content)
    assert payload["model"] == "rfgpt"
    assert payload["temperature"] == 0.0
    assert payload["repetition_penalty"] == 1.05
    assert payload["max_tokens"] == 224
    user_content = payload["messages"][1]["content"]
    assert user_content[0]["type"] == "image_url"
    assert user_content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert user_content[1]["type"] == "text"
    prompt = user_content[1]["text"]
    assert "sensor-1" in prompt
    assert "atheer-hann-v1" in prompt
    assert "at most one item in technologies" in prompt
    assert "at most one item in signals" in prompt
    assert "no duplicate findings" in prompt
    assert "compact single-line JSON" in prompt
    assert "quality_flags may contain only these RF-only values" in prompt
    assert "technologies or signals contains" in prompt
    assert "any finding" in prompt
    assert "must not claim no signal" in prompt
    assert "Do not create contradictory findings" in prompt
    assert "Do not make non-RF attribution" in payload["messages"][0]["content"]
    assert "cheating" not in payload["messages"][0]["content"].lower()
    assert "at most one primary technology finding" in payload["messages"][0]["content"]
    assert "no Markdown" in payload["messages"][0]["content"]
    response_format = payload["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "rfgpt_analysis_primary_v4"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "technologies",
        "signals",
        "overall_assessment",
        "quality_flags",
    }
    technologies_schema = schema["properties"]["technologies"]
    signals_schema = schema["properties"]["signals"]
    assert technologies_schema["minItems"] == 0
    assert technologies_schema["maxItems"] == 1
    assert signals_schema["minItems"] == 0
    assert signals_schema["maxItems"] == 1
    assert technologies_schema["items"]["additionalProperties"] is False
    assert signals_schema["items"]["additionalProperties"] is False
    assert technologies_schema["items"]["properties"]["evidence"]["maxItems"] == 2
    assert signals_schema["items"]["properties"]["evidence"]["maxItems"] == 2
    assert technologies_schema["items"]["properties"]["evidence"]["items"]["maxLength"] == 160
    assert technologies_schema["items"]["properties"]["label"]["maxLength"] == 64
    assert signals_schema["items"]["properties"]["label"]["maxLength"] == 64
    assert technologies_schema["items"]["properties"]["observation"]["maxLength"] == 160
    assert signals_schema["items"]["properties"]["observation"]["maxLength"] == 160
    assert technologies_schema["items"]["properties"]["model_score"]["type"] == ["number", "null"]
    assert schema["properties"]["overall_assessment"]["maxLength"] == 240
    assert schema["properties"]["quality_flags"]["maxItems"] == 2
    assert schema["properties"]["quality_flags"]["items"]["maxLength"] == 80
    assert schema["properties"]["quality_flags"]["items"]["enum"] == list(RF_QUALITY_FLAGS)


@pytest.mark.asyncio
@respx.mock
async def test_live_no_signal_response_filters_non_rf_quality_flags(tmp_path: Path) -> None:
    content = json.dumps(
        {
            "technologies": [],
            "signals": [],
            "overall_assessment": "no signals present",
            "quality_flags": ["no_people", "no_cheating"],
        }
    )
    _mock_ready()
    raw = {
        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        "usage": {"completion_tokens": 35},
    }
    respx.post("http://vllm.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=raw)
    )
    result = await LocalVLLMRFGPTAdapter(_settings()).analyze(_request(_png(tmp_path / "a.png")))

    assert result.status == "succeeded"
    assert result.parser_valid is True
    assert result.technologies == []
    assert result.signals == []
    assert result.overall_assessment == "no signals present"
    assert result.quality_flags == [NON_RF_FLAGS_REMOVED]
    assert "no_people" not in result.quality_flags
    assert "no_cheating" not in result.quality_flags
    assert json.loads(result.raw_response) == raw


@pytest.mark.asyncio
@respx.mock
async def test_live_contradictory_payload_becomes_semantic_inconsistency(
    tmp_path: Path,
) -> None:
    content = json.dumps(
        {
            "technologies": [
                {
                    "label": "chirp",
                    "model_score": None,
                    "observation": "chirp signal",
                    "evidence": ["capture_id:d696c4ee-eb01-40a3-ab08-0962724cdcc3"],
                }
            ],
            "signals": [
                {
                    "label": "tone",
                    "observation": "tone carrier",
                    "frequency_start_hz": 2440000000,
                    "frequency_end_hz": 2440000000,
                    "evidence": ["capture_id:d696c4ee-eb01-40a3-ab08-0962724cdcc3"],
                }
            ],
            "overall_assessment": "no_signal",
            "quality_flags": [],
        }
    )
    raw = {
        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        "usage": {"completion_tokens": 35},
    }
    _mock_ready()
    respx.post("http://vllm.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=raw)
    )

    result = await LocalVLLMRFGPTAdapter(_settings()).analyze(_request(_png(tmp_path / "a.png")))

    assert result.status == "parser_failed"
    assert result.parser_valid is False
    assert result.technologies == []
    assert result.signals == []
    assert result.quality_flags == ["parser_failed", SEMANTIC_INCONSISTENCY]
    assert "Semantic inconsistency" in result.overall_assessment
    assert json.loads(result.raw_response) == raw


@pytest.mark.asyncio
@respx.mock
async def test_empty_findings_and_allowed_rf_quality_flags_are_valid(tmp_path: Path) -> None:
    content = json.dumps(
        {
            "technologies": [],
            "signals": [],
            "overall_assessment": "RF observation is limited by low SNR.",
            "quality_flags": ["low_snr", "uncertain"],
        }
    )
    result, _ = await _analyze_content(tmp_path, content)

    assert result.status == "succeeded"
    assert result.parser_valid is True
    assert result.technologies == []
    assert result.signals == []
    assert result.quality_flags == ["low_snr", "uncertain"]


@pytest.mark.asyncio
@respx.mock
async def test_empty_findings_with_no_signal_quality_flag_succeeds(tmp_path: Path) -> None:
    content = json.dumps(
        {
            "technologies": [],
            "signals": [],
            "overall_assessment": "No signal is observable in this RF snapshot.",
            "quality_flags": ["no_signal"],
        }
    )
    result, _ = await _analyze_content(tmp_path, content)

    assert result.status == "succeeded"
    assert result.parser_valid is True
    assert result.technologies == []
    assert result.signals == []
    assert result.quality_flags == ["no_signal"]


@pytest.mark.asyncio
@respx.mock
async def test_non_empty_findings_with_neutral_assessment_succeeds(tmp_path: Path) -> None:
    content = _valid_content()
    result, _ = await _analyze_content(tmp_path, content)

    assert result.status == "succeeded"
    assert result.parser_valid is True
    assert result.technologies[0].label == "rf-burst-like"
    assert result.signals == []


@pytest.mark.asyncio
@respx.mock
async def test_no_signal_quality_flag_plus_findings_fails(tmp_path: Path) -> None:
    payload = json.loads(_valid_content())
    payload["quality_flags"] = ["no_signal"]
    result, raw = await _analyze_content(tmp_path, json.dumps(payload))

    assert result.status == "parser_failed"
    assert result.parser_valid is False
    assert result.technologies == []
    assert result.signals == []
    assert result.quality_flags == ["parser_failed", SEMANTIC_INCONSISTENCY]
    assert json.loads(result.raw_response) == raw


@pytest.mark.asyncio
@respx.mock
async def test_unsupported_quality_flags_are_removed_deterministically(tmp_path: Path) -> None:
    content = json.dumps(
        {
            "technologies": [],
            "signals": [],
            "overall_assessment": "RF observation is limited.",
            "quality_flags": ["uncertain", "not_an_rf_flag"],
        }
    )
    result, _ = await _analyze_content(tmp_path, content)

    assert result.status == "succeeded"
    assert result.parser_valid is True
    assert result.quality_flags == ["uncertain", NON_RF_FLAGS_REMOVED]


@pytest.mark.asyncio
@respx.mock
async def test_evidence_and_quality_flags_do_not_trigger_substring_false_positive(
    tmp_path: Path,
) -> None:
    content = json.dumps(
        {
            "technologies": [
                {
                    "label": "rf-burst",
                    "model_score": None,
                    "observation": "Short RF burst visible.",
                    "evidence": ["capture_id:student-is-cheating"],
                }
            ],
            "signals": [],
            "overall_assessment": "RF-only observation.",
            "quality_flags": ["no_cheating"],
        }
    )
    result, raw = await _analyze_content(tmp_path, content)

    assert result.status == "succeeded"
    assert result.parser_valid is True
    assert result.technologies[0].label == "rf-burst"
    assert result.quality_flags == [NON_RF_FLAGS_REMOVED]
    assert json.loads(result.raw_response) == raw


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
    message = str(exc.value)
    assert "timeout_seconds=300" in message
    assert "max_output_tokens=224" in message
    assert "endpoint=http://vllm.local/v1" in message
    assert "model_name=rfgpt" in message
    assert "model_version=Qwen2.5-VL-7B-rfa-wtr-v2-joint" in message


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
async def test_finish_reason_length_is_truncated_parser_failure(tmp_path: Path) -> None:
    _mock_ready()
    raw = _chat_response('{"technologies":[', finish_reason="length")
    respx.post("http://vllm.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=raw)
    )
    result = await LocalVLLMRFGPTAdapter(_settings()).analyze(_request(_png(tmp_path / "a.png")))
    assert result.status == "parser_failed"
    assert result.parser_valid is False
    assert result.technologies == []
    assert result.signals == []
    assert result.quality_flags == ["parser_failed", "truncated_output"]
    assert "JSONDecodeError" not in result.quality_flags
    assert json.loads(result.raw_response) == raw


@pytest.mark.asyncio
@respx.mock
async def test_positive_prohibited_overall_assessment_is_parser_invalid(
    tmp_path: Path,
) -> None:
    content = json.dumps(
        {
            "technologies": [],
            "signals": [],
            "overall_assessment": "The student is cheating.",
            "quality_flags": [],
        }
    )
    result, raw = await _analyze_content(tmp_path, content)
    assert result.status == "parser_failed"
    assert result.parser_valid is False
    assert result.technologies == []
    assert result.signals == []
    assert "Parser failed" in result.overall_assessment
    assert json.loads(result.raw_response) == raw


@pytest.mark.asyncio
@respx.mock
async def test_positive_prohibited_finding_observation_is_parser_invalid(
    tmp_path: Path,
) -> None:
    content = json.dumps(
        {
            "technologies": [
                {
                    "label": "rf-burst",
                    "model_score": None,
                    "observation": "The person is cheating.",
                    "evidence": ["capture_id:capture-1"],
                }
            ],
            "signals": [],
            "overall_assessment": "RF-only observation.",
            "quality_flags": [],
        }
    )
    result, raw = await _analyze_content(tmp_path, content)
    assert result.status == "parser_failed"
    assert result.parser_valid is False
    assert result.technologies == []
    assert result.signals == []
    assert "Parser failed" in result.overall_assessment
    assert json.loads(result.raw_response) == raw


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update({"extra": "nope"}),
        lambda payload: payload.update({"technologies": [{}, {}]}),
        lambda payload: payload["technologies"].append(
            {
                "label": "x" * 65,
                "model_score": None,
                "observation": "RF observation.",
                "evidence": [],
            }
        ),
        lambda payload: payload["technologies"].append(
            {
                "label": "rf",
                "model_score": 1.5,
                "observation": "RF observation.",
                "evidence": [],
            }
        ),
        lambda payload: payload["signals"].append(
            {
                "label": "rf",
                "observation": "x" * 161,
                "frequency_start_hz": None,
                "frequency_end_hz": None,
                "evidence": [],
            }
        ),
        lambda payload: payload.update({"overall_assessment": "x" * 241}),
        lambda payload: payload.update({"quality_flags": ["low_snr", "uncertain", "extra"]}),
    ],
)
async def test_application_side_validation_enforces_schema_limits(
    tmp_path: Path,
    mutator: Any,
) -> None:
    payload: dict[str, Any] = {
        "technologies": [],
        "signals": [],
        "overall_assessment": "RF-only observation.",
        "quality_flags": [],
    }
    mutator(payload)
    result, raw = await _analyze_content(tmp_path, json.dumps(payload))
    assert result.status == "parser_failed"
    assert result.parser_valid is False
    assert result.technologies == []
    assert result.signals == []
    assert json.loads(result.raw_response) == raw


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
