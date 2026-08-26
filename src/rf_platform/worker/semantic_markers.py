from __future__ import annotations

import re
from collections.abc import Iterable

from rf_platform.contracts.analysis import AnalysisResult

SEMANTIC_INCONSISTENCY = "semantic_inconsistency"

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NO_SIGNAL_NOUNS = {"signal", "signals"}
_NO_SIGNAL_FALSE_CONTINUATIONS = {
    "degradation",
    "interruption",
    "loss",
}


def has_no_signal_marker(
    overall_assessment: str | None,
    quality_flags: Iterable[str] = (),
) -> bool:
    """Return true when trusted model text clearly states no RF signal is present.

    Matching is token-boundary-aware so punctuation-delimited phrases such as
    "RF observation only; no signals." are detected without treating unrelated
    statements like "no signal loss" as absence-of-signal claims.
    """

    if text_has_no_signal_marker(overall_assessment):
        return True
    return any(text_has_no_signal_marker(flag) for flag in quality_flags)


def text_has_no_signal_marker(value: str | None) -> bool:
    tokens = _tokens(value)
    for index, token in enumerate(tokens[:-1]):
        if token != "no" or tokens[index + 1] not in _NO_SIGNAL_NOUNS:
            continue
        continuation = tokens[index + 2] if index + 2 < len(tokens) else None
        if continuation in _NO_SIGNAL_FALSE_CONTINUATIONS:
            continue
        return True
    return False


def result_has_no_signal_marker(result: AnalysisResult) -> bool:
    return has_no_signal_marker(result.overall_assessment, result.quality_flags)


def result_is_semantically_inconsistent(result: AnalysisResult) -> bool:
    return SEMANTIC_INCONSISTENCY in result.quality_flags or (
        bool(result.technologies or result.signals) and result_has_no_signal_marker(result)
    )


def _tokens(value: str | None) -> list[str]:
    if value is None:
        return []
    return _TOKEN_RE.findall(value.lower().replace("_", " ").replace("-", " "))
