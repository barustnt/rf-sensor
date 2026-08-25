from __future__ import annotations

import json
import time

from rf_platform.common.config import Settings
from rf_platform.common.ids import new_id
from rf_platform.common.time import utc_now
from rf_platform.contracts.analysis import (
    AnalysisRequest,
    AnalysisResult,
    ModelHealth,
    ModelIdentity,
    TechnologyFinding,
)


class MockRFGPTAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def health(self) -> ModelHealth:
        return ModelHealth(
            adapter="mock",
            ready=True,
            model_name=self.settings.rfgpt_model_name,
            model_version=self.settings.rfgpt_model_version,
            message="deterministic mock adapter ready",
        )

    async def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        started = utc_now()
        t0 = time.perf_counter()
        finding = TechnologyFinding(
            label="bluetooth-like",
            model_score=None,
            observation=(
                "Synthetic spectrogram contains short hopping-like energy bursts in the 2.4 GHz "
                "band. This is a mock RF-GPT observation for pipeline validation only."
            ),
            evidence=[f"capture_id:{request.capture_id}"],
        )
        raw = {
            "technologies": [finding.model_dump(mode="json")],
            "overall_assessment": "Model-generated observation, not independently confirmed.",
        }
        completed = utc_now()
        return AnalysisResult(
            analysis_id=new_id(),
            capture_id=request.capture_id,
            model=ModelIdentity(
                name=self.settings.rfgpt_model_name,
                version=self.settings.rfgpt_model_version,
                adapter="mock",
                prompt_version=request.prompt_version,
            ),
            status="succeeded",
            started_at_utc=started,
            completed_at_utc=completed,
            latency_ms=max(1, int((time.perf_counter() - t0) * 1000)),
            technologies=[finding],
            signals=[],
            overall_assessment="Model-generated observation, not independently confirmed.",
            quality_flags=[],
            parser_valid=True,
            raw_response=json.dumps(raw, sort_keys=True),
            preprocessing_version=request.preprocessing_version,
            inference_parameters={
                "adapter": "mock",
                "prompt_version": request.prompt_version,
                "model_version": self.settings.rfgpt_model_version,
                "preprocessing_version": request.preprocessing_version,
            },
        )
