from __future__ import annotations

import base64
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from rf_platform.common.config import Settings
from rf_platform.common.ids import new_id
from rf_platform.common.time import utc_now
from rf_platform.contracts.analysis import (
    AnalysisRequest,
    AnalysisResult,
    ModelHealth,
    ModelIdentity,
    SignalFinding,
    TechnologyFinding,
)

PROMPT_VERSION = "technology-detection-v1"
SYSTEM_PROMPT = """You are RF-GPT running inside a local authorized RF monitoring system.
Return only constrained JSON. Treat a spectrogram as one RF observation, not proof of identity.
Never identify a person, never claim cheating, and never infer payload contents. If evidence is
insufficient, say so in the JSON fields rather than inventing labels or confidence scores."""

USER_PROMPT_TEMPLATE = """Analyze the attached lossless PNG RF spectrogram.

Capture context:
- capture_id: {capture_id}
- sensor_id: {sensor_id}
- capture_started_at_utc: {capture_started_at_utc}
- center_frequency_hz: {center_frequency_hz}
- sample_rate_sps: {sample_rate_sps}
- bandwidth_hz: {bandwidth_hz}
- gain_db: {gain_db}
- profile_id: {profile_id}
- preprocessing_version: {preprocessing_version}

Respond with exactly one JSON object:
{{
  "technologies": [
    {{
      "label": "candidate technology label or observable RF class",
      "model_score": null,
      "observation": "short RF-only observation with visible spectrogram evidence",
      "evidence": ["capture_id:{capture_id}", "preprocessing:{preprocessing_version}"]
    }}
  ],
  "signals": [
    {{
      "label": "optional signal descriptor",
      "observation": "RF-only observation",
      "frequency_start_hz": null,
      "frequency_end_hz": null,
      "evidence": ["capture_id:{capture_id}"]
    }}
  ],
  "overall_assessment": "RF observation only; include limitations.",
  "quality_flags": []
}}

Use null for model_score unless the model explicitly provides a calibrated numeric value. Do not
invent confidence scores, people, device owners, identities, payloads, or disciplinary claims."""


class RFGPTAdapterError(RuntimeError):
    category = "model_failure"
    retryable = True

    def __init__(self, message: str, *, category: str | None = None, retryable: bool | None = None):
        super().__init__(message)
        if category is not None:
            self.category = category
        if retryable is not None:
            self.retryable = retryable


class VLLMTimeoutError(RFGPTAdapterError):
    category = "model_timeout"
    retryable = True


class VLLMConnectionError(RFGPTAdapterError):
    category = "model_unavailable"
    retryable = True


class VLLMHTTPError(RFGPTAdapterError):
    category = "model_http_error"

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(
            f"vLLM HTTP {status_code}",
            retryable=status_code >= 500,
        )
        self.status_code = status_code
        self.body = body[:500]


class VLLMMalformedResponseError(RFGPTAdapterError):
    category = "model_malformed_response"
    retryable = False


class VLLMModelMismatchError(RFGPTAdapterError):
    category = "model_mismatch"
    retryable = False


class PermanentInputError(RFGPTAdapterError):
    category = "permanent_input_failure"
    retryable = False


class LocalVLLMRFGPTAdapter:
    """Real local RF-GPT adapter using vLLM's OpenAI-compatible HTTP API."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.endpoint = str(settings.rfgpt_endpoint).rstrip("/")

    async def health(self) -> ModelHealth:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.rfgpt_health_timeout_seconds
            ) as client:
                health_response = await client.get(self._health_url())
                models_response = await client.get(self._openai_url("models"))
            health_ok = health_response.status_code == 200
            if models_response.status_code != 200:
                return ModelHealth(
                    adapter="vllm",
                    ready=False,
                    model_name=self.settings.rfgpt_model_name,
                    model_version=self.settings.rfgpt_model_version,
                    message=f"/v1/models returned HTTP {models_response.status_code}",
                    details={
                        "health_status_code": health_response.status_code,
                        "models_status_code": models_response.status_code,
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                    },
                )
            model_ids = self._model_ids(models_response.json())
            ready = health_ok and self.settings.rfgpt_model_name in model_ids
            return ModelHealth(
                adapter="vllm",
                ready=ready,
                model_name=self.settings.rfgpt_model_name,
                model_version=self.settings.rfgpt_model_version,
                message="local vLLM endpoint ready" if ready else "configured model not served",
                details={
                    "health_status_code": health_response.status_code,
                    "served_models": model_ids,
                    "endpoint": self._redacted_endpoint(),
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                },
            )
        except httpx.TimeoutException:
            return self._unready("vLLM health check timed out", "model_timeout", started)
        except httpx.ConnectError:
            return self._unready("vLLM endpoint is unavailable", "model_unavailable", started)
        except (httpx.HTTPError, json.JSONDecodeError, VLLMMalformedResponseError) as exc:
            return self._unready(
                f"vLLM health check failed: {exc.__class__.__name__}", "error", started
            )

    async def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        self._validate_request_context(request)
        image_path = self._single_png_path(request)
        data_url = self._png_data_url(image_path)
        await self._ensure_ready()
        started = utc_now()
        t0 = time.perf_counter()
        payload = self._chat_payload(request, data_url)
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.rfgpt_request_timeout_seconds
            ) as client:
                response = await client.post(self._openai_url("chat/completions"), json=payload)
        except httpx.TimeoutException as exc:
            raise VLLMTimeoutError("vLLM request timed out") from exc
        except httpx.ConnectError as exc:
            raise VLLMConnectionError("vLLM endpoint is unavailable") from exc
        except httpx.HTTPError as exc:
            raise RFGPTAdapterError(f"vLLM request failed: {exc.__class__.__name__}") from exc
        raw_response = response.text
        if response.status_code >= 400:
            raise VLLMHTTPError(response.status_code, raw_response)
        completed = utc_now()
        latency_ms = max(1, int((time.perf_counter() - t0) * 1000))
        content = self._message_content(raw_response)
        return self._analysis_from_content(
            request=request,
            raw_response=raw_response,
            model_content=content,
            started_at_utc=started,
            completed_at_utc=completed,
            latency_ms=latency_ms,
        )

    async def _ensure_ready(self) -> None:
        async with httpx.AsyncClient(timeout=self.settings.rfgpt_health_timeout_seconds) as client:
            try:
                health_response = await client.get(self._health_url())
                models_response = await client.get(self._openai_url("models"))
            except httpx.TimeoutException as exc:
                raise VLLMTimeoutError("vLLM health check timed out") from exc
            except httpx.ConnectError as exc:
                raise VLLMConnectionError("vLLM endpoint is unavailable") from exc
        if health_response.status_code != 200:
            raise VLLMHTTPError(health_response.status_code, health_response.text)
        if models_response.status_code != 200:
            raise VLLMHTTPError(models_response.status_code, models_response.text)
        try:
            model_ids = self._model_ids(models_response.json())
        except (json.JSONDecodeError, VLLMMalformedResponseError) as exc:
            raise VLLMMalformedResponseError("/v1/models response is malformed") from exc
        if self.settings.rfgpt_model_name not in model_ids:
            raise VLLMModelMismatchError(
                f"configured model {self.settings.rfgpt_model_name!r} not in served models"
            )

    def _analysis_from_content(
        self,
        *,
        request: AnalysisRequest,
        raw_response: str,
        model_content: str,
        started_at_utc: datetime,
        completed_at_utc: datetime,
        latency_ms: int,
    ) -> AnalysisResult:
        try:
            payload = _extract_json_object(model_content)
            _validate_structured_payload(payload)
            technologies = [
                TechnologyFinding.model_validate(item) for item in payload["technologies"]
            ]
            signals = [SignalFinding.model_validate(item) for item in payload["signals"]]
            overall = str(payload["overall_assessment"])
            quality_flags = [str(item) for item in payload["quality_flags"]]
            if _contains_prohibited_claim(payload):
                raise ValueError("model output contained prohibited identity/cheating claim")
            return AnalysisResult(
                analysis_id=new_id(),
                capture_id=request.capture_id,
                model=ModelIdentity(
                    name=self.settings.rfgpt_model_name,
                    version=self.settings.rfgpt_model_version,
                    adapter="vllm",
                    prompt_version=request.prompt_version,
                ),
                status="succeeded",
                started_at_utc=started_at_utc,
                completed_at_utc=completed_at_utc,
                latency_ms=latency_ms,
                technologies=technologies,
                signals=signals,
                overall_assessment=overall,
                quality_flags=quality_flags,
                parser_valid=True,
                raw_response=raw_response,
                preprocessing_version=request.preprocessing_version,
                inference_parameters=self._inference_parameters(request),
            )
        except Exception as exc:
            return AnalysisResult(
                analysis_id=new_id(),
                capture_id=request.capture_id,
                model=ModelIdentity(
                    name=self.settings.rfgpt_model_name,
                    version=self.settings.rfgpt_model_version,
                    adapter="vllm",
                    prompt_version=request.prompt_version,
                ),
                status="parser_failed",
                started_at_utc=started_at_utc,
                completed_at_utc=completed_at_utc,
                latency_ms=latency_ms,
                technologies=[],
                signals=[],
                overall_assessment=(
                    "Parser failed; raw model response retained and no trusted findings generated."
                ),
                quality_flags=["parser_failed", exc.__class__.__name__],
                parser_valid=False,
                raw_response=raw_response,
                preprocessing_version=request.preprocessing_version,
                inference_parameters=self._inference_parameters(request),
            )

    def _chat_payload(self, request: AnalysisRequest, data_url: str) -> dict[str, Any]:
        user_prompt = USER_PROMPT_TEMPLATE.format(
            capture_id=request.capture_id,
            sensor_id=request.sensor_id,
            capture_started_at_utc=(
                request.capture_started_at_utc.isoformat()
                if request.capture_started_at_utc
                else None
            ),
            center_frequency_hz=request.center_frequency_hz,
            sample_rate_sps=request.sample_rate_sps,
            bandwidth_hz=request.bandwidth_hz,
            gain_db=request.gain_db,
            profile_id=request.profile_id,
            preprocessing_version=request.preprocessing_version,
        )
        return {
            "model": self.settings.rfgpt_model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            "temperature": self.settings.rfgpt_temperature,
            "top_p": self.settings.rfgpt_top_p,
            "repetition_penalty": self.settings.rfgpt_repetition_penalty,
            "max_tokens": self.settings.rfgpt_max_output_tokens,
        }

    def _inference_parameters(self, request: AnalysisRequest) -> dict[str, Any]:
        return {
            "temperature": self.settings.rfgpt_temperature,
            "top_p": self.settings.rfgpt_top_p,
            "repetition_penalty": self.settings.rfgpt_repetition_penalty,
            "max_output_tokens": self.settings.rfgpt_max_output_tokens,
            "prompt_version": request.prompt_version,
            "system_prompt_version": PROMPT_VERSION,
            "user_prompt_version": PROMPT_VERSION,
            "model_version": self.settings.rfgpt_model_version,
            "preprocessing_version": request.preprocessing_version,
        }

    def _validate_request_context(self, request: AnalysisRequest) -> None:
        required = {
            "sensor_id": request.sensor_id,
            "capture_started_at_utc": request.capture_started_at_utc,
            "center_frequency_hz": request.center_frequency_hz,
            "sample_rate_sps": request.sample_rate_sps,
            "bandwidth_hz": request.bandwidth_hz,
            "profile_id": request.profile_id,
            "preprocessing_version": request.preprocessing_version,
        }
        missing = sorted(name for name, value in required.items() if value in {None, ""})
        if missing:
            raise PermanentInputError(f"analysis request missing context: {', '.join(missing)}")

    def _single_png_path(self, request: AnalysisRequest) -> Path:
        if len(request.artifact_paths) != 1:
            raise PermanentInputError("vLLM adapter requires exactly one local PNG artifact path")
        path = Path(request.artifact_paths[0])
        if not path.exists():
            raise PermanentInputError("spectrogram artifact file does not exist")
        return path

    @staticmethod
    def _png_data_url(path: Path) -> str:
        data = path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise PermanentInputError("vLLM adapter requires a lossless PNG artifact")
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    @staticmethod
    def _message_content(raw_response: str) -> str:
        try:
            payload = json.loads(raw_response)
            choices = payload["choices"]
            content = choices[0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise VLLMMalformedResponseError("vLLM chat completion response is malformed") from exc
        if not isinstance(content, str):
            raise VLLMMalformedResponseError("vLLM message content is not text")
        return content

    @staticmethod
    def _model_ids(payload: dict[str, Any]) -> list[str]:
        try:
            data = payload["data"]
            ids = [str(item["id"]) for item in data]
        except (KeyError, TypeError) as exc:
            raise VLLMMalformedResponseError("vLLM models response is malformed") from exc
        return ids

    def _health_url(self) -> str:
        parts = urlsplit(self.endpoint)
        path = parts.path.rstrip("/")
        if path.endswith("/v1"):
            path = path[: -len("/v1")]
        if path == "/v1":
            path = ""
        return urlunsplit((parts.scheme, parts.netloc, f"{path}/health", "", ""))

    def _openai_url(self, suffix: str) -> str:
        parts = urlsplit(self.endpoint)
        path = parts.path.rstrip("/")
        if not path.endswith("/v1"):
            path = f"{path}/v1" if path else "/v1"
        return urlunsplit((parts.scheme, parts.netloc, f"{path}/{suffix.lstrip('/')}", "", ""))

    def _redacted_endpoint(self) -> str:
        parts = urlsplit(self.endpoint)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    def _unready(self, message: str, category: str, started: float) -> ModelHealth:
        return ModelHealth(
            adapter="vllm",
            ready=False,
            model_name=self.settings.rfgpt_model_name,
            model_version=self.settings.rfgpt_model_version,
            message=message,
            details={
                "category": category,
                "endpoint": self._redacted_endpoint(),
                "latency_ms": int((time.perf_counter() - started) * 1000),
            },
        )


class LocalRFGPTAdapter(LocalVLLMRFGPTAdapter):
    """Backward-compatible alias for the Milestone 3 local vLLM adapter."""


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object found in model content")
    parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model JSON root must be an object")
    return parsed


def _validate_structured_payload(payload: dict[str, Any]) -> None:
    required = {"technologies", "signals", "overall_assessment", "quality_flags"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"model JSON missing required keys: {sorted(missing)}")
    if not isinstance(payload["technologies"], list):
        raise ValueError("technologies must be a list")
    if not isinstance(payload["signals"], list):
        raise ValueError("signals must be a list")
    if not isinstance(payload["overall_assessment"], str):
        raise ValueError("overall_assessment must be a string")
    if not isinstance(payload["quality_flags"], list):
        raise ValueError("quality_flags must be a list")


def _contains_prohibited_claim(payload: dict[str, Any]) -> bool:
    text = json.dumps(payload, sort_keys=True).lower()
    prohibited = [
        "cheating",
        "cheater",
        "student is",
        "person is",
        "identified person",
        "device owner",
    ]
    return any(term in text for term in prohibited)
