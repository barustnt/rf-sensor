from __future__ import annotations

from rf_platform.common.config import Settings
from rf_platform.contracts.analysis import AnalysisRequest, AnalysisResult, ModelHealth


class LocalRFGPTAdapter:
    """Milestone 3 placeholder boundary for a real RF-GPT integration."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def health(self) -> ModelHealth:
        return ModelHealth(
            adapter="local",
            ready=False,
            model_name=self.settings.rfgpt_model_name,
            model_version=self.settings.rfgpt_model_version,
            message="Real RF-GPT invocation details are required in Milestone 3.",
        )

    async def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        raise NotImplementedError(
            "Local RF-GPT integration is out of scope for Milestones 0-1. "
            "Record invocation details, prompt/output schema, and model environment first."
        )
