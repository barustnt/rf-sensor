from __future__ import annotations

from rf_platform.contracts.analysis import AnalysisResult


def validate_analysis_result(result: AnalysisResult) -> AnalysisResult:
    if result.status == "succeeded" and not result.parser_valid:
        raise ValueError("succeeded analysis must be parser_valid")
    return result
