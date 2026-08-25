from __future__ import annotations

from rf_platform.common.config import Settings
from rf_platform.worker.rfgpt.base import RFGPTAdapter
from rf_platform.worker.rfgpt.local import LocalRFGPTAdapter, LocalVLLMRFGPTAdapter
from rf_platform.worker.rfgpt.mock import MockRFGPTAdapter


def create_adapter(settings: Settings) -> RFGPTAdapter:
    if settings.rfgpt_adapter == "mock":
        return MockRFGPTAdapter(settings)
    if settings.rfgpt_adapter == "local":
        return LocalRFGPTAdapter(settings)
    if settings.rfgpt_adapter == "vllm":
        return LocalVLLMRFGPTAdapter(settings)
    raise ValueError(f"unsupported RF-GPT adapter: {settings.rfgpt_adapter}")
