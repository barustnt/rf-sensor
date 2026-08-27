from __future__ import annotations

from rf_platform.backend.db import models
from rf_platform.backend.services.coverage import capture_frequency_range
from rf_platform.common.band_compatibility import (
    BAND_INCOMPATIBLE,
    check_findings_band_compatibility,
)
from rf_platform.contracts.analysis import AnalysisResult

PARSER_FAILED = "parser_failed"


def validate_analysis_result(
    result: AnalysisResult, capture: models.Capture | None = None
) -> AnalysisResult:
    if result.status == "succeeded" and not result.parser_valid:
        raise ValueError("succeeded analysis must be parser_valid")
    if capture is not None and result.status == "succeeded" and result.parser_valid:
        compatibility = check_findings_band_compatibility(
            technologies=[item.model_dump(mode="json") for item in result.technologies],
            signals=[item.model_dump(mode="json") for item in result.signals],
            frequency_range_hz=capture_frequency_range(capture),
            profile_id=getattr(capture, "profile_id", None),
        )
        if compatibility.incompatible:
            return result.model_copy(
                update={
                    "status": "parser_failed",
                    "parser_valid": False,
                    "technologies": [],
                    "signals": [],
                    "quality_flags": [PARSER_FAILED, BAND_INCOMPATIBLE],
                    "overall_assessment": (
                        "Band compatibility check rejected impossible technology/frequency "
                        "combination; raw model response retained and no trusted findings "
                        "generated."
                    ),
                }
            )
    return result
