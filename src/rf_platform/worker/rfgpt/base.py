from __future__ import annotations

from typing import Protocol

from rf_platform.contracts.analysis import AnalysisRequest, AnalysisResult, ModelHealth


class RFGPTAdapter(Protocol):
    async def health(self) -> ModelHealth: ...

    async def analyze(self, request: AnalysisRequest) -> AnalysisResult: ...
