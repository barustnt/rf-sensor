from __future__ import annotations

import base64
import json
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from rf_platform.common.config import Settings
from rf_platform.common.ids import new_id
from rf_platform.common.logging import get_logger
from rf_platform.common.time import utc_now
from rf_platform.contracts.analysis import (
    AnalysisRequest,
    AnalysisResult,
    ModelHealth,
    ModelIdentity,
    SignalFinding,
    TechnologyFinding,
)
from rf_platform.worker.semantic_markers import (
    SEMANTIC_INCONSISTENCY,
    has_no_signal_marker,
)

PROMPT_VERSION = "technology-detection-primary-v4"
RESPONSE_SCHEMA_NAME = "rfgpt_analysis_primary_v4"
RF_QUALITY_FLAGS = (
    "no_signal",
    "low_snr",
    "uncertain",
    "interference",
    "clipping_suspected",
    "limited_bandwidth",
)
NON_RF_FLAGS_REMOVED = "non_rf_flags_removed"
PARSER_FAILED = "parser_failed"
MAX_TECHNOLOGIES = 1
MAX_SIGNALS = 1
MAX_EVIDENCE_ITEMS = 2
MAX_LABEL_LENGTH = 64
MAX_OBSERVATION_LENGTH = 160
MAX_OVERALL_ASSESSMENT_LENGTH = 240
MAX_QUALITY_FLAGS = 2
MAX_QUALITY_FLAG_LENGTH = 80
logger = get_logger("rf_platform.rfgpt.local")

SYSTEM_PROMPT = """You are RF-GPT running inside a local authorized RF monitoring system.
Return compact single-line JSON only: no Markdown, no preamble, no trailing prose.
Treat the spectrogram as one RF observation, not proof of identity.
Report at most one primary technology finding and at most one primary signal finding.
Do not duplicate findings.
Do not make non-RF attribution, identity, ownership, or behavioral conclusions. If evidence is
insufficient, use empty arrays and concise RF limitations instead of inventing labels or scores."""

USER_PROMPT_TEMPLATE = """Analyze the attached lossless PNG RF spectrogram as one image.

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

Respond with one compact single-line JSON object only, matching this shape:
{{
  "technologies": [
    {{
      "label": "one primary candidate technology or observable RF class",
      "model_score": null,
      "observation": "concise RF-only evidence",
      "evidence": ["capture_id:{capture_id}"]
    }}
  ],
  "signals": [
    {{
      "label": "one primary signal descriptor",
      "observation": "concise RF-only evidence",
      "frequency_start_hz": null,
      "frequency_end_hz": null,
      "evidence": ["capture_id:{capture_id}"]
    }}
  ],
  "overall_assessment": "RF observation only; include limitations.",
  "quality_flags": []
}}

Rules: use at most one item in technologies and at most one item in signals; no duplicate findings;
observations must be concise; quality_flags may contain only these RF-only values:
no_signal, low_snr, uncertain, interference, clipping_suspected, limited_bandwidth. Use null for
model_score unless calibrated; do not invent confidence scores, payload contents, or non-RF
attribution, identity, ownership, or behavioral conclusions. If technologies or signals contains
any finding, overall_assessment and quality_flags must not claim no signal. If no signal is
observable, technologies and signals must both be empty and quality_flags should contain no_signal.
overall_assessment should be a concise natural-language RF assessment, not a bare quality-flag
token. Do not create contradictory findings merely to fill the schema."""

RF_GPT_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "technologies": {
            "type": "array",
            "minItems": 0,
            "maxItems": MAX_TECHNOLOGIES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_LABEL_LENGTH,
                    },
                    "model_score": {
                        "type": ["number", "null"],
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "observation": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_OBSERVATION_LENGTH,
                    },
                    "evidence": {
                        "type": "array",
                        "minItems": 0,
                        "maxItems": MAX_EVIDENCE_ITEMS,
                        "items": {"type": "string", "maxLength": MAX_OBSERVATION_LENGTH},
                    },
                },
                "required": ["label", "model_score", "observation", "evidence"],
            },
        },
        "signals": {
            "type": "array",
            "minItems": 0,
            "maxItems": MAX_SIGNALS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_LABEL_LENGTH,
                    },
                    "observation": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_OBSERVATION_LENGTH,
                    },
                    "frequency_start_hz": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                    },
                    "frequency_end_hz": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                    },
                    "evidence": {
                        "type": "array",
                        "minItems": 0,
                        "maxItems": MAX_EVIDENCE_ITEMS,
                        "items": {"type": "string", "maxLength": MAX_OBSERVATION_LENGTH},
                    },
                },
                "required": [
                    "label",
                    "observation",
                    "frequency_start_hz",
                    "frequency_end_hz",
                    "evidence",
                ],
            },
        },
        "overall_assessment": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_OVERALL_ASSESSMENT_LENGTH,
        },
        "quality_flags": {
            "type": "array",
            "minItems": 0,
            "maxItems": MAX_QUALITY_FLAGS,
            "items": {
                "type": "string",
                "maxLength": MAX_QUALITY_FLAG_LENGTH,
                "enum": list(RF_QUALITY_FLAGS),
            },
        },
    },
    "required": ["technologies", "signals", "overall_assessment", "quality_flags"],
}

RFGPT_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": RESPONSE_SCHEMA_NAME,
        "strict": True,
        "schema": RF_GPT_ANALYSIS_SCHEMA,
    },
}


class RFGPTAdapterError(RuntimeError):
    category = "model_failure"
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        category: str | None = None,
        retryable: bool | None = None,
        raw_response: str | None = None,
    ) -> None:
        super().__init__(message)
        if category is not None:
            self.category = category
        if retryable is not None:
            self.retryable = retryable
        self.raw_response = raw_response


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
            raw_response=body,
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


class SemanticInconsistencyError(ValueError):
    """Structured model payload is internally inconsistent."""


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
            return self._unready(
                self._timeout_message(
                    "health check",
                    timeout_seconds=self.settings.rfgpt_health_timeout_seconds,
                ),
                "model_timeout",
                started,
            )
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
            raise VLLMTimeoutError(
                self._timeout_message(
                    "chat completion request",
                    timeout_seconds=self.settings.rfgpt_request_timeout_seconds,
                )
            ) from exc
        except httpx.ConnectError as exc:
            raise VLLMConnectionError("vLLM endpoint is unavailable") from exc
        except httpx.HTTPError as exc:
            raise RFGPTAdapterError(f"vLLM request failed: {exc.__class__.__name__}") from exc
        raw_response = response.text
        if response.status_code >= 400:
            raise VLLMHTTPError(response.status_code, raw_response)
        completed = utc_now()
        latency_ms = max(1, int((time.perf_counter() - t0) * 1000))
        content, finish_reason = self._message_content_and_finish_reason(raw_response)
        if finish_reason == "length":
            logger.warning(
                "rfgpt_parser_failed",
                job_id=request.job_id,
                capture_id=request.capture_id,
                error="TruncatedOutput",
                message="Model output reached the completion-token limit",
            )
            return self._parser_failed_result(
                request=request,
                raw_response=raw_response,
                started_at_utc=started,
                completed_at_utc=completed,
                latency_ms=latency_ms,
                quality_flags=[PARSER_FAILED, "truncated_output"],
                overall_assessment=(
                    "Model output reached the completion-token limit; raw response retained and "
                    "no trusted findings generated."
                ),
            )
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
                raise VLLMTimeoutError(
                    self._timeout_message(
                        "health/model discovery",
                        timeout_seconds=self.settings.rfgpt_health_timeout_seconds,
                    )
                ) from exc
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
            quality_flags = _trusted_quality_flags(payload["quality_flags"])
            if _contains_prohibited_claim(payload):
                raise ValueError("model output contained prohibited non-RF assertion")
            _validate_semantic_consistency(technologies, signals, overall, quality_flags)
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
            logger.warning(
                "rfgpt_parser_failed",
                job_id=request.job_id,
                capture_id=request.capture_id,
                error=exc.__class__.__name__,
                message=str(exc),
            )
            quality_flags = [PARSER_FAILED, exc.__class__.__name__]
            overall_assessment = (
                "Parser failed; raw model response retained and no trusted findings generated."
            )
            if isinstance(exc, SemanticInconsistencyError):
                quality_flags = [PARSER_FAILED, SEMANTIC_INCONSISTENCY]
                overall_assessment = (
                    "Semantic inconsistency: no-signal marker conflicts with non-empty findings; "
                    "raw model response retained and no trusted findings generated."
                )
            return self._parser_failed_result(
                request=request,
                raw_response=raw_response,
                started_at_utc=started_at_utc,
                completed_at_utc=completed_at_utc,
                latency_ms=latency_ms,
                quality_flags=quality_flags,
                overall_assessment=overall_assessment,
            )

    def _parser_failed_result(
        self,
        *,
        request: AnalysisRequest,
        raw_response: str,
        started_at_utc: datetime,
        completed_at_utc: datetime,
        latency_ms: int,
        quality_flags: list[str],
        overall_assessment: str,
    ) -> AnalysisResult:
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
            overall_assessment=overall_assessment,
            quality_flags=quality_flags,
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
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": user_prompt},
                    ],
                },
            ],
            "temperature": self.settings.rfgpt_temperature,
            "top_p": self.settings.rfgpt_top_p,
            "repetition_penalty": self.settings.rfgpt_repetition_penalty,
            "max_tokens": self.settings.rfgpt_max_output_tokens,
            "response_format": deepcopy(RFGPT_RESPONSE_FORMAT),
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
            "response_schema": RESPONSE_SCHEMA_NAME,
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
    def _message_content_and_finish_reason(raw_response: str) -> tuple[str, str | None]:
        try:
            payload = json.loads(raw_response)
            choices = payload["choices"]
            choice = choices[0]
            finish_reason = choice.get("finish_reason")
            content = choice["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise VLLMMalformedResponseError(
                "vLLM chat completion response is malformed",
                raw_response=raw_response,
            ) from exc
        if not isinstance(content, str):
            raise VLLMMalformedResponseError(
                "vLLM message content is not text",
                raw_response=raw_response,
            )
        return content, str(finish_reason) if finish_reason is not None else None

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
        host = parts.hostname or ""
        netloc = f"{host}:{parts.port}" if parts.port is not None else host
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))

    def _timeout_message(self, operation: str, *, timeout_seconds: float | int) -> str:
        return (
            f"vLLM {operation} timed out; timeout_seconds={timeout_seconds}; "
            f"max_output_tokens={self.settings.rfgpt_max_output_tokens}; "
            f"endpoint={self._redacted_endpoint()}; "
            f"model_name={self.settings.rfgpt_model_name}; "
            f"model_version={self.settings.rfgpt_model_version}"
        )

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
    extra = set(payload) - required
    if extra:
        raise ValueError(f"model JSON contained unsupported keys: {sorted(extra)}")

    _validate_finding_list(
        name="technologies",
        items=payload["technologies"],
        max_items=MAX_TECHNOLOGIES,
        required_keys={"label", "model_score", "observation", "evidence"},
        allow_model_score=True,
        allow_frequency=False,
    )
    _validate_finding_list(
        name="signals",
        items=payload["signals"],
        max_items=MAX_SIGNALS,
        required_keys={
            "label",
            "observation",
            "frequency_start_hz",
            "frequency_end_hz",
            "evidence",
        },
        allow_model_score=False,
        allow_frequency=True,
    )
    _validate_string(
        "overall_assessment",
        payload["overall_assessment"],
        min_length=1,
        max_length=MAX_OVERALL_ASSESSMENT_LENGTH,
    )
    flags = payload["quality_flags"]
    if not isinstance(flags, list):
        raise ValueError("quality_flags must be a list")
    if len(flags) > MAX_QUALITY_FLAGS:
        raise ValueError(f"quality_flags must contain at most {MAX_QUALITY_FLAGS} items")
    for index, flag in enumerate(flags):
        _validate_string(
            f"quality_flags[{index}]",
            flag,
            min_length=1,
            max_length=MAX_QUALITY_FLAG_LENGTH,
        )


def _contains_prohibited_claim(payload: dict[str, Any]) -> bool:
    return any(_text_contains_prohibited_claim(text) for text in _semantic_assertion_text(payload))


def _validate_finding_list(
    *,
    name: str,
    items: Any,
    max_items: int,
    required_keys: set[str],
    allow_model_score: bool,
    allow_frequency: bool,
) -> None:
    if not isinstance(items, list):
        raise ValueError(f"{name} must be a list")
    if len(items) > max_items:
        raise ValueError(f"{name} must contain at most {max_items} item(s)")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{name}[{index}] must be an object")
        missing = required_keys - item.keys()
        if missing:
            raise ValueError(f"{name}[{index}] missing required keys: {sorted(missing)}")
        extra = set(item) - required_keys
        if extra:
            raise ValueError(f"{name}[{index}] contained unsupported keys: {sorted(extra)}")
        _validate_string(
            f"{name}[{index}].label",
            item["label"],
            min_length=1,
            max_length=MAX_LABEL_LENGTH,
        )
        _validate_string(
            f"{name}[{index}].observation",
            item["observation"],
            min_length=1,
            max_length=MAX_OBSERVATION_LENGTH,
        )
        _validate_evidence(f"{name}[{index}].evidence", item["evidence"])
        if allow_model_score:
            _validate_model_score(f"{name}[{index}].model_score", item["model_score"])
        if allow_frequency:
            start = _validate_optional_frequency(
                f"{name}[{index}].frequency_start_hz",
                item["frequency_start_hz"],
            )
            end = _validate_optional_frequency(
                f"{name}[{index}].frequency_end_hz",
                item["frequency_end_hz"],
            )
            if start is not None and end is not None and start > end:
                raise ValueError(f"{name}[{index}] frequency_start_hz exceeds frequency_end_hz")


def _validate_string(name: str, value: Any, *, min_length: int, max_length: int) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) < min_length:
        raise ValueError(f"{name} must not be empty")
    if len(value) > max_length:
        raise ValueError(f"{name} exceeds maximum length {max_length}")


def _validate_evidence(name: str, value: Any) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    if len(value) > MAX_EVIDENCE_ITEMS:
        raise ValueError(f"{name} must contain at most {MAX_EVIDENCE_ITEMS} items")
    for index, item in enumerate(value):
        _validate_string(
            f"{name}[{index}]",
            item,
            min_length=1,
            max_length=MAX_OBSERVATION_LENGTH,
        )


def _validate_model_score(name: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number or null")
    if not 0 <= float(value) <= 1:
        raise ValueError(f"{name} must be between 0 and 1")


def _validate_optional_frequency(name: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer or null")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _trusted_quality_flags(raw_flags: list[Any]) -> list[str]:
    trusted: list[str] = []
    removed = False
    for item in raw_flags:
        flag = str(item)
        if flag in RF_QUALITY_FLAGS:
            if flag not in trusted:
                trusted.append(flag)
        else:
            removed = True
    if removed and len(trusted) < MAX_QUALITY_FLAGS:
        trusted.append(NON_RF_FLAGS_REMOVED)
    return trusted[:MAX_QUALITY_FLAGS]


def _validate_semantic_consistency(
    technologies: list[TechnologyFinding],
    signals: list[SignalFinding],
    overall_assessment: str,
    quality_flags: list[str],
) -> None:
    if (technologies or signals) and has_no_signal_marker(overall_assessment, quality_flags):
        raise SemanticInconsistencyError(
            "no-signal marker conflicts with non-empty technology or signal findings"
        )


def _semantic_assertion_text(payload: dict[str, Any]) -> list[str]:
    texts = [str(payload.get("overall_assessment", ""))]
    for collection_name in ("technologies", "signals"):
        collection = payload.get(collection_name, [])
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict):
                continue
            for field in ("label", "observation"):
                if field in item:
                    texts.append(str(item[field]))
    return texts


def _text_contains_prohibited_claim(text: str) -> bool:
    normalized = " ".join(text.lower().replace("-", " ").split())
    if not normalized:
        return False
    identity_terms = [
        "student is",
        "person is",
        "individual is",
        "candidate is",
        "identified person",
        "person identified",
        "student identified",
        "device owner",
        "owner is",
        "belongs to",
        "owned by",
        "identity is",
    ]
    if any(term in normalized for term in identity_terms):
        return True
    if "cheater" in normalized:
        return True
    if "cheating" in normalized:
        negative_terms = ("no cheating", "not cheating", "without cheating")
        return not any(term in normalized for term in negative_terms)
    return False
