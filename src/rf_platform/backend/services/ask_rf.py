from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rf_platform.backend.db import models
from rf_platform.backend.services.coverage import (
    accepted_run_for_presentation,
    capture_frequency_range,
)
from rf_platform.common.band_compatibility import (
    BAND_INCOMPATIBLE,
    check_findings_band_compatibility,
    profile_matches_technology,
    profile_presentation_eligible,
    scan_profile_for_capture,
)
from rf_platform.common.config import Settings
from rf_platform.common.scan_profiles import ScanProfileSet, load_scan_profile_set
from rf_platform.common.time import (
    DEFAULT_DISPLAY_TIMEZONE,
    InterpretedInterval,
    ensure_utc,
    resolve_historical_interval,
)
from rf_platform.contracts.api import AskRFRequest, AskRFResponse, QueryInterval
from rf_platform.worker.semantic_markers import (
    SEMANTIC_INCONSISTENCY,
    has_no_signal_marker,
)

BLUETOOTH_RANGE_HZ = (2_402_000_000, 2_480_000_000)
DEFAULT_LIMITATIONS = [
    "AI-assisted RF observation—not independently confirmed ground truth.",
    (
        "Answers are based only on accepted stored observations already available through the "
        "platform API."
    ),
]
SUPPORTED_QUESTION_EXAMPLE = "What happened today at 10 AM?"

_TIME_HINT_RE = re.compile(
    r"\b(today|yesterday|morning|tonight|\d{4}-\d{2}-\d{2}|\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
    re.I,
)
_TECH_5G_RE = re.compile(r"\b(5g|5g\s*nr|nr)\b", re.I)
_LTE_RE = re.compile(r"\b(lte|4g)\b", re.I)
_BLUETOOTH_RE = re.compile(r"\b(bluetooth|ble)\b", re.I)
_WIFI_RE = re.compile(r"\b(wi[ -]?fi|wifi|wlan|802\.11)\b", re.I)
_ISM_RE = re.compile(r"\b(ism|srd)\b", re.I)
_PUNCTUATION_SPACE_RE = re.compile(r"\s+([?!.,;:])")
_WHITESPACE_RE = re.compile(r"\s+")
_HAPPEND_ALIAS_RE = re.compile(r"\bhappend\b")


@dataclass(frozen=True)
class AskRFRecord:
    capture_id: str
    analysis_id: str
    sensor_id: str
    sensor_adapter: str
    profile_id: str
    started_at_utc: datetime
    ended_at_utc: datetime
    location: dict[str, Any]
    frequency_start_hz: int | None
    frequency_end_hz: int | None
    technologies: list[dict[str, Any]]
    signals: list[dict[str, Any]]
    overall_assessment: str
    quality_flags: list[str]

    @property
    def technology_labels(self) -> list[str]:
        return [
            str(item.get("label", "")).strip() for item in self.technologies if item.get("label")
        ]

    @property
    def signal_labels(self) -> list[str]:
        return [str(item.get("label", "")).strip() for item in self.signals if item.get("label")]

    @property
    def has_findings(self) -> bool:
        return bool(self.technology_labels or self.signal_labels)

    @property
    def no_signal(self) -> bool:
        return has_no_signal_marker(self.overall_assessment, self.quality_flags)


@dataclass(frozen=True)
class AskRFDataset:
    real_capture_count: int
    rejected_result_count: int
    accepted_records: list[AskRFRecord]
    locations: list[dict[str, Any]]
    coverage_ranges_hz: list[tuple[int, int]]
    experimental_records: list[AskRFRecord] = field(default_factory=list)
    presentation_eligible_capture_count: int = 0
    unvalidated_capture_count: int = 0
    technology_unvalidated_counts: dict[str, int] = field(default_factory=dict)
    technology_coverage_counts: dict[str, int] = field(default_factory=dict)
    technology_presentation_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class QuestionIntent:
    kind: str
    technology: str | None = None


async def answer_ask_rf(
    session: AsyncSession,
    request: AskRFRequest,
    *,
    default_timezone: str = DEFAULT_DISPLAY_TIMEZONE,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> AskRFResponse:
    timezone = request.display_timezone or default_timezone
    normalized_question = normalize_question(request.question)
    interval = _resolve_interval(normalized_question, timezone, request.prior_context, now)
    intent = interpret_question(normalized_question)
    if intent.kind == "unsupported":
        return _unsupported_response(interval)
    if intent.kind == "time_period":
        return _time_period_response(interval)
    dataset = await load_presentation_dataset(session, interval, settings=settings)
    return build_answer(intent, interval, dataset)


def interpret_question(question: str) -> QuestionIntent:
    text = normalize_question(question)
    if not text:
        return QuestionIntent("unsupported")
    if "which time period" in text or "what time period" in text:
        return QuestionIntent("time_period")
    if _TECH_5G_RE.search(text):
        return QuestionIntent("technology", "5g")
    if _LTE_RE.search(text):
        return QuestionIntent("technology", "lte")
    if _BLUETOOTH_RE.search(text):
        return QuestionIntent("technology", "bluetooth")
    if _WIFI_RE.search(text):
        return QuestionIntent("technology", "wifi")
    if any(phrase in text for phrase in ("what happened", "technologies", "nearby", "unusual")):
        return QuestionIntent("summary")
    return QuestionIntent("unsupported")


def build_answer(
    intent: QuestionIntent,
    interval: InterpretedInterval,
    dataset: AskRFDataset,
) -> AskRFResponse:
    time_label = format_time_label(interval)
    location_label = format_location_label(dataset.locations)
    limitations = list(DEFAULT_LIMITATIONS)
    if interval.assumptions:
        limitations.extend(interval.assumptions)
    coverage = coverage_summary(dataset.coverage_ranges_hz)
    if coverage:
        limitations.append(coverage)

    if intent.technology in {"lte", "5g", "wifi"}:
        tech_count = dataset.technology_coverage_counts.get(intent.technology, 0)
        unvalidated_count = dataset.technology_unvalidated_counts.get(intent.technology, 0)
        presentation_count = dataset.technology_presentation_counts.get(intent.technology, 0)
        if tech_count == 0:
            return _not_monitored_response(interval, intent.technology)
        if unvalidated_count and presentation_count == 0:
            return _profile_not_validated_response(
                interval, intent.technology, time_label, location_label, limitations, dataset
            )

    if dataset.real_capture_count == 0:
        return _response(
            "no_data",
            (
                f"No sensor observations are available for {time_label}. The sensor may have been "
                "offline or not scheduled to capture during that period."
            ),
            interval,
            time_label,
            location_label,
            "No eligible real hardware captures were available in the interpreted interval.",
            limitations,
            dataset,
        )

    positive_records = records_with_positive_findings(dataset.accepted_records, intent)
    no_signal_records = [
        record
        for record in dataset.accepted_records
        if record.no_signal and not record.has_findings
    ]

    if positive_records and no_signal_records:
        return _partial_response(
            interval, time_label, location_label, limitations, dataset, intent=intent
        )
    if positive_records:
        labels = sorted(
            {label for record in positive_records for label in matching_labels(record, intent)}
        )
        label_text = human_label_list(labels)
        band_text = (
            " in the monitored 2.4 GHz range" if coverage_overlaps_bluetooth(dataset) else ""
        )
        if intent.technology == "bluetooth":
            lead = (
                f"Between {time_label}, the system observed {label_text} RF activity{band_text}. "
                "This may be relevant to the Bluetooth/BLE question, but it is not independently "
                "confirmed."
            )
        else:
            lead = (
                f"Between {time_label}, the system observed {label_text} RF activity{band_text}. "
                "This is an AI-assisted observation and has not been independently confirmed."
            )
        return _response(
            "observation",
            lead + "\n\nOnly accepted, internally consistent stored observations were used.",
            interval,
            time_label,
            location_label,
            evidence_text(dataset, accepted_count=len(positive_records)),
            limitations,
            dataset,
        )

    if no_signal_records:
        if intent.technology == "bluetooth":
            answer = (
                f"Between {time_label}, Bluetooth was not confirmed in the monitored portion of "
                "the 2.4 GHz band. The current captures cover only part of the full Bluetooth/BLE "
                "range, so this does not prove Bluetooth was absent from the complete band."
            )
        elif intent.technology in {"lte", "5g"}:
            label = "5G" if intent.technology == "5g" else "LTE"
            answer = (
                f"The system monitored configured {label} candidate ranges between {time_label}. "
                f"No {label} activity was confirmed in the accepted observations."
            )
        else:
            answer = (
                f"The system monitored part of the 2.4 GHz band between {time_label}. No signal "
                "or wireless technology was confirmed in the accepted observations."
            )
        return _response(
            "no_signal",
            answer,
            interval,
            time_label,
            location_label,
            evidence_text(dataset, accepted_count=len(no_signal_records)),
            limitations,
            dataset,
        )

    if dataset.rejected_result_count > 0 or dataset.accepted_records == []:
        return _partial_response(
            interval, time_label, location_label, limitations, dataset, intent=intent
        )

    return _response(
        "no_data",
        (
            f"No accepted observations are available for {time_label}. The system could not "
            "determine what was present from stored data."
        ),
        interval,
        time_label,
        location_label,
        evidence_text(dataset, accepted_count=0),
        limitations,
        dataset,
    )


async def load_presentation_dataset(
    session: AsyncSession, interval: InterpretedInterval, settings: Settings | None = None
) -> AskRFDataset:
    profile_set = _load_scan_profiles(settings)
    result = await session.execute(
        select(models.Capture, models.Sensor)
        .join(models.Sensor, models.Sensor.sensor_id == models.Capture.sensor_id)
        .where(
            models.Capture.started_at_utc >= interval.start_utc,
            models.Capture.started_at_utc < interval.end_utc,
        )
    )
    capture_sensor_rows = list(result.all())
    real_rows = [
        (capture, sensor)
        for capture, sensor in capture_sensor_rows
        if sensor.adapter != "simulated"
    ]
    real_capture_ids = [capture.capture_id for capture, _sensor in real_rows]
    sensor_by_capture = {capture.capture_id: sensor for capture, sensor in real_rows}
    capture_by_id = {capture.capture_id: capture for capture, _sensor in real_rows}

    accepted: list[AskRFRecord] = []
    experimental: list[AskRFRecord] = []
    rejected_count = 0
    unvalidated_count = 0
    if real_capture_ids:
        run_result = await session.execute(
            select(models.ModelRun, models.Capture, models.Sensor, models.AnalysisJob)
            .join(models.Capture, models.Capture.capture_id == models.ModelRun.capture_id)
            .join(models.Sensor, models.Sensor.sensor_id == models.Capture.sensor_id)
            .join(models.AnalysisJob, models.AnalysisJob.job_id == models.ModelRun.job_id)
            .where(models.ModelRun.capture_id.in_(real_capture_ids))
        )
        for run, capture, sensor, job in run_result.all():
            record = presentation_record_from_run(run, capture, sensor, job, profile_set)
            if record is None:
                experimental_record = _experimental_record_from_run(
                    run, capture, sensor, job, profile_set
                )
                if experimental_record is not None:
                    unvalidated_count += 1
                    experimental.append(experimental_record)
                else:
                    rejected_count += 1
            else:
                accepted.append(record)

    coverage_ranges: list[tuple[int, int]] = []
    presentation_eligible_capture_count = 0
    technology_unvalidated_counts = {"lte": 0, "5g": 0, "bluetooth": 0, "wifi": 0}
    technology_coverage_counts = {"lte": 0, "5g": 0, "bluetooth": 0, "wifi": 0}
    technology_presentation_counts = {"lte": 0, "5g": 0, "bluetooth": 0, "wifi": 0}
    for capture in capture_by_id.values():
        if frequency_range := capture_frequency_range(capture):
            coverage_ranges.append(frequency_range)
        profile = scan_profile_for_capture(profile_set, capture.profile_id)
        presentation_eligible = profile_presentation_eligible(profile, capture.profile_id)
        if presentation_eligible:
            presentation_eligible_capture_count += 1
        for technology in technology_coverage_counts:
            if _capture_matches_technology(capture, profile, technology):
                technology_coverage_counts[technology] += 1
                if not presentation_eligible:
                    technology_unvalidated_counts[technology] += 1
                else:
                    technology_presentation_counts[technology] += 1
    locations = [sensor.location for sensor in sensor_by_capture.values() if sensor.location]
    return AskRFDataset(
        real_capture_count=len(real_rows),
        rejected_result_count=rejected_count,
        accepted_records=accepted,
        locations=locations,
        coverage_ranges_hz=coverage_ranges,
        experimental_records=experimental,
        presentation_eligible_capture_count=presentation_eligible_capture_count,
        unvalidated_capture_count=unvalidated_count,
        technology_unvalidated_counts=technology_unvalidated_counts,
        technology_coverage_counts=technology_coverage_counts,
        technology_presentation_counts=technology_presentation_counts,
    )


def presentation_record_from_run(
    run: models.ModelRun,
    capture: models.Capture,
    sensor: models.Sensor,
    job: models.AnalysisJob,
    profile_set: ScanProfileSet | None = None,
) -> AskRFRecord | None:
    record = _validated_record_from_run(run, capture, sensor, job)
    if record is None:
        return None
    if not accepted_run_for_presentation(run, capture, sensor, job, profile_set):
        return None
    return record


def _validated_record_from_run(
    run: models.ModelRun,
    capture: models.Capture,
    sensor: models.Sensor,
    job: models.AnalysisJob,
) -> AskRFRecord | None:
    if sensor.adapter == "simulated":
        return None
    if run.adapter == "mock" or run.model_version == "mock-v1":
        return None
    if run.status != "succeeded" or not run.parser_valid:
        return None
    if job.status in {"failed", "deadletter"}:
        return None
    if job.error_category in {
        "model_configuration_mismatch",
        SEMANTIC_INCONSISTENCY,
        BAND_INCOMPATIBLE,
    }:
        return None
    structured = run.structured_result if isinstance(run.structured_result, dict) else {}
    technologies = _list_of_dicts(structured.get("technologies"))
    signals = _list_of_dicts(structured.get("signals"))
    overall = str(structured.get("overall_assessment") or "")
    quality_flags = [
        str(item) for item in structured.get("quality_flags", []) if isinstance(item, str)
    ]
    if SEMANTIC_INCONSISTENCY in quality_flags or BAND_INCOMPATIBLE in quality_flags:
        return None
    if (technologies or signals) and has_no_signal_marker(overall, quality_flags):
        return None
    frequency_range = capture_frequency_range(capture)
    return AskRFRecord(
        capture_id=capture.capture_id,
        analysis_id=run.analysis_id,
        sensor_id=capture.sensor_id,
        sensor_adapter=sensor.adapter,
        profile_id=capture.profile_id,
        started_at_utc=capture.started_at_utc,
        ended_at_utc=capture.ended_at_utc,
        location=sensor.location or {},
        frequency_start_hz=frequency_range[0] if frequency_range else None,
        frequency_end_hz=frequency_range[1] if frequency_range else None,
        technologies=technologies,
        signals=signals,
        overall_assessment=overall,
        quality_flags=quality_flags,
    )


def records_with_positive_findings(
    records: list[AskRFRecord], intent: QuestionIntent
) -> list[AskRFRecord]:
    return [record for record in records if matching_labels(record, intent)]


def matching_labels(record: AskRFRecord, intent: QuestionIntent) -> list[str]:
    labels = record.technology_labels + record.signal_labels
    pattern = _technology_pattern(intent.technology)
    if pattern is not None:
        return [label for label in labels if pattern.search(label)]
    return labels


def coverage_overlaps_bluetooth(dataset: AskRFDataset) -> bool:
    low, high = BLUETOOTH_RANGE_HZ
    return any(start <= high and end >= low for start, end in dataset.coverage_ranges_hz)


def coverage_summary(ranges: list[tuple[int, int]]) -> str | None:
    if not ranges:
        return None
    low, high = BLUETOOTH_RANGE_HZ
    overlapping = [(start, end) for start, end in ranges if start <= high and end >= low]
    if not overlapping:
        return "The accepted captures did not cover the 2.4 GHz Bluetooth/BLE range."
    min_start = min(start for start, _end in overlapping)
    max_end = max(end for _start, end in overlapping)
    if min_start > low or max_end < high:
        return (
            "Bluetooth/BLE coverage is partial; current captures cover only part of the full "
            "2.4 GHz band."
        )
    return "The accepted captures overlap the 2.4 GHz Bluetooth/BLE range."


def format_time_label(interval: InterpretedInterval) -> str:
    tz = ZoneInfo(interval.display_timezone)
    start = ensure_utc(interval.start_utc).astimezone(tz)
    end = ensure_utc(interval.end_utc).astimezone(tz)
    if start.date() == end.date():
        return f"{_clock_label(start)} and {_clock_label(end)} on {start:%B %-d, %Y}"
    return f"{start:%B %-d, %Y, %-I:%M %p} to {end:%B %-d, %Y, %-I:%M %p}"


def format_location_label(locations: list[dict[str, Any]]) -> str:
    labels = []
    for location in locations:
        parts = [
            str(location.get("site") or "").strip(),
            str(location.get("building") or "").strip(),
            str(location.get("room") or "").strip(),
        ]
        label = " / ".join(part for part in parts if part and part != "unknown")
        if label and label not in labels:
            labels.append(label)
    if not labels:
        return "monitored area"
    if len(labels) == 1:
        return labels[0]
    return f"{len(labels)} monitored areas"


def human_label_list(labels: list[str]) -> str:
    cleaned = [label.replace("_", "-").strip() for label in labels if label.strip()]
    if not cleaned:
        return "RF"
    if len(cleaned) == 1:
        label = cleaned[0]
        return label if label.endswith("-like") else f"{label}-like"
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


def evidence_text(dataset: AskRFDataset, *, accepted_count: int) -> str:
    parts = [
        f"Used {accepted_count} accepted presentation observation(s) from "
        f"{dataset.real_capture_count} real sensor collection(s)."
    ]
    if dataset.unvalidated_capture_count:
        experimental_count = _count_phrase(
            dataset.unvalidated_capture_count,
            "successful experimental technical-review result",
        )
        parts.append(f"Kept {experimental_count} out of presentation conclusions.")
    if dataset.rejected_result_count:
        parts.append(
            f"{_count_phrase(dataset.rejected_result_count, 'result').capitalize()} did not "
            "pass consistency checks."
        )
    if not dataset.unvalidated_capture_count and not dataset.rejected_result_count:
        parts.append("No stored result was set aside by presentation filtering.")
    return " ".join(parts)


def _profile_not_validated_response(
    interval: InterpretedInterval,
    technology: str,
    time_label: str,
    location_label: str,
    limitations: list[str],
    dataset: AskRFDataset,
) -> AskRFResponse:
    label = _technology_display_label(technology)
    experimental_text = _experimental_finding_text(
        dataset.experimental_records, QuestionIntent("technology", technology)
    )
    prefix = f"{experimental_text} " if experimental_text else ""
    validation_reason = "technology identification for the scan profile has not yet been validated"
    if dataset.rejected_result_count:
        validation_reason += (
            f", and {_count_phrase(dataset.rejected_result_count, 'result')} did not pass "
            "consistency checks"
        )
    return _response(
        "profile_not_validated",
        (
            f"{prefix}The system monitored part of this frequency range during {time_label}, but "
            f"{validation_reason}. "
            f"No reliable {label} conclusion can be provided."
        ),
        interval,
        time_label,
        location_label,
        evidence_text(dataset, accepted_count=len(dataset.accepted_records)),
        limitations
        + [
            "Experimental scan profiles can show that a range was captured, but they do not "
            "establish technology presence or absence in Ask RF."
        ],
        dataset,
    )


def _partial_response(
    interval: InterpretedInterval,
    time_label: str,
    location_label: str,
    limitations: list[str],
    dataset: AskRFDataset,
    *,
    intent: QuestionIntent | None = None,
) -> AskRFResponse:
    reasons = []
    if dataset.unvalidated_capture_count:
        reasons.append("the monitored profile remains experimental")
    if dataset.rejected_result_count:
        reasons.append(
            f"{_count_phrase(dataset.rejected_result_count, 'result')} did not pass "
            "consistency checks"
        )
    if reasons:
        caution = (
            "The system collected observations for this period, but "
            f"{_join_reasons(reasons)}. No reliable conclusion can be provided."
        )
    else:
        caution = (
            "The system collected observations for this period, but some results did not pass "
            "consistency checks. No reliable conclusion can be provided."
        )
    experimental_text = _experimental_finding_text(
        dataset.experimental_records, intent or QuestionIntent("summary")
    )
    answer = f"{experimental_text} {caution}" if experimental_text else caution
    return _response(
        "partial_data",
        answer,
        interval,
        time_label,
        location_label,
        evidence_text(dataset, accepted_count=len(dataset.accepted_records)),
        limitations,
        dataset,
    )


def _not_monitored_response(interval: InterpretedInterval, technology: str) -> AskRFResponse:
    time_label = format_time_label(interval)
    technology_label = _technology_display_label(technology)
    label = f"{technology_label} bands"
    answer = (
        f"The system did not monitor the configured {label} during {time_label}, so it cannot "
        f"determine whether {technology_label} activity was present."
    )
    return _response(
        "not_monitored",
        answer,
        interval,
        time_label,
        "monitored area",
        f"No configured {label} coverage is available in the current scan profiles.",
        DEFAULT_LIMITATIONS
        + [
            "Only explicitly enabled, real scan-profile captures can establish monitored coverage "
            "for technology-specific questions."
        ],
        None,
    )


def _unsupported_response(interval: InterpretedInterval) -> AskRFResponse:
    time_label = format_time_label(interval)
    return _response(
        "unsupported_question",
        (
            "I can answer questions about wireless activity, monitored technologies, and specific "
            "time periods. Try asking: ‘What happened today at 10 AM?’"
        ),
        interval,
        time_label,
        "monitored area",
        "The question did not match the supported deterministic Ask RF patterns.",
        DEFAULT_LIMITATIONS,
        None,
    )


def _time_period_response(interval: InterpretedInterval) -> AskRFResponse:
    time_label = format_time_label(interval)
    return _response(
        "observation",
        f"I’m using {time_label} for this question.",
        interval,
        time_label,
        "monitored area",
        "This time period comes from the current question or the previous Ask RF turn.",
        DEFAULT_LIMITATIONS,
        None,
    )


def _response(
    status: str,
    answer: str,
    interval: InterpretedInterval,
    time_label: str,
    location_label: str,
    evidence_explanation: str,
    limitations: list[str],
    dataset: AskRFDataset | None,
) -> AskRFResponse:
    return AskRFResponse(
        answer_status=status,  # type: ignore[arg-type]
        display_answer=answer,
        interpreted_interval=QueryInterval(
            start_utc=interval.start_utc,
            end_utc=interval.end_utc,
            display_timezone=interval.display_timezone,
            assumptions=interval.assumptions,
        ),
        time_label=time_label,
        location_label=location_label,
        evidence_explanation=evidence_explanation,
        limitations=limitations,
        follow_up_context={
            "start_utc": interval.start_utc.isoformat(),
            "end_utc": interval.end_utc.isoformat(),
            "display_timezone": interval.display_timezone,
            "location_label": location_label,
            "coverage_ranges_hz": dataset.coverage_ranges_hz if dataset else [],
        },
    )


def _resolve_interval(
    question: str,
    timezone: str,
    prior_context: dict[str, Any] | None,
    now: datetime | None,
) -> InterpretedInterval:
    normalized_question = normalize_question(question)
    if prior_context and not _TIME_HINT_RE.search(normalized_question):
        try:
            return InterpretedInterval(
                start_utc=ensure_utc(datetime.fromisoformat(str(prior_context["start_utc"]))),
                end_utc=ensure_utc(datetime.fromisoformat(str(prior_context["end_utc"]))),
                display_timezone=str(prior_context.get("display_timezone") or timezone),
                assumptions=["Reused the previous Ask RF time period for this follow-up."],
            )
        except (KeyError, TypeError, ValueError):
            pass
    return resolve_historical_interval(normalized_question, timezone, now)


def normalize_question(question: str) -> str:
    normalized = _PUNCTUATION_SPACE_RE.sub(r"\1", question.strip().lower())
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return _HAPPEND_ALIAS_RE.sub("happened", normalized)


def _clock_label(value: datetime) -> str:
    text = value.strftime("%-I:%M %p")
    return text.replace(":00", "")


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _successful_technical_only_experimental_result(
    run: models.ModelRun,
    capture: models.Capture,
    sensor: models.Sensor,
    job: models.AnalysisJob,
    profile_set: ScanProfileSet | None,
) -> bool:
    return _experimental_record_from_run(run, capture, sensor, job, profile_set) is not None


def _experimental_record_from_run(
    run: models.ModelRun,
    capture: models.Capture,
    sensor: models.Sensor,
    job: models.AnalysisJob,
    profile_set: ScanProfileSet | None,
) -> AskRFRecord | None:
    profile = scan_profile_for_capture(profile_set, capture.profile_id)
    if profile is None or profile_presentation_eligible(profile, capture.profile_id):
        return None
    record = _validated_record_from_run(run, capture, sensor, job)
    if record is None:
        return None
    compatibility = check_findings_band_compatibility(
        technologies=record.technologies,
        signals=record.signals,
        frequency_range_hz=capture_frequency_range(capture, profile_set),
        profile_id=capture.profile_id,
    )
    return None if compatibility.incompatible else record


def _experimental_finding_text(
    records: list[AskRFRecord], intent: QuestionIntent
) -> str | None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    pattern = _technology_pattern(intent.technology)
    for record in records:
        for finding in record.technologies:
            label = str(finding.get("label") or "").strip()
            if not label or (pattern is not None and not pattern.search(label)):
                continue
            technology = intent.technology or _canonical_technology(label)
            if technology is None:
                continue
            grouped.setdefault(technology, []).append(finding)
    if not grouped:
        return None

    technology, findings = sorted(
        grouped.items(), key=lambda item: (-len(item[1]), item[0])
    )[0]
    count_text = _count_phrase(len(findings), "internally consistent experimental result")
    text = (
        f"Experimental indication: {_technology_display_label(technology)}-like activity "
        f"appeared in {count_text}."
    )
    scores = [
        float(score)
        for finding in findings
        if (score := finding.get("model_score")) is not None
        and not isinstance(score, bool)
        and isinstance(score, (int, float))
        and isfinite(float(score))
        and 0 <= float(score) <= 1
    ]
    if scores:
        percentage = round(median(scores) * 100)
        text += (
            f" The median model-reported score was {percentage}%; this is not a calibrated "
            "probability."
        )
    return text


def _canonical_technology(label: str) -> str | None:
    for technology in ("lte", "5g", "bluetooth", "wifi", "ism"):
        pattern = _technology_pattern(technology)
        if pattern is not None and pattern.search(label):
            return technology
    return None


def _technology_pattern(technology: str | None) -> re.Pattern[str] | None:
    return {
        "lte": _LTE_RE,
        "5g": _TECH_5G_RE,
        "bluetooth": _BLUETOOTH_RE,
        "wifi": _WIFI_RE,
        "ism": _ISM_RE,
    }.get(technology)


def _technology_display_label(technology: str) -> str:
    return {
        "lte": "LTE",
        "5g": "5G",
        "bluetooth": "Bluetooth/BLE",
        "wifi": "Wi-Fi",
        "ism": "ISM/SRD",
    }.get(technology, technology.upper())


def _count_phrase(count: int, noun: str) -> str:
    if count == 1:
        return f"one {noun}"
    return f"{count} {noun}s"


def _join_reasons(reasons: list[str]) -> str:
    if len(reasons) <= 1:
        return reasons[0] if reasons else "stored observations need further review"
    return ", ".join(reasons[:-1]) + f", and {reasons[-1]}"


def _load_scan_profiles(settings: Settings | None) -> ScanProfileSet | None:
    if settings is None:
        return None
    try:
        return load_scan_profile_set(
            settings.scan_profile_config,
            expected_profile_set=settings.scan_profile_set,
        )
    except Exception:
        return None


def _capture_matches_technology(
    capture: models.Capture,
    profile: Any,
    technology: str,
) -> bool:
    if profile_matches_technology(profile, technology):
        return True
    frequency_range = capture_frequency_range(capture)
    if frequency_range is None:
        return False
    if technology == "bluetooth":
        return (
            frequency_range[0] <= BLUETOOTH_RANGE_HZ[1]
            and frequency_range[1] >= BLUETOOTH_RANGE_HZ[0]
        )
    return False


# Tiny import-time check for systems whose strftime lacks %-I (not expected on Linux).
try:  # pragma: no cover
    datetime(2026, 1, 1, tzinfo=UTC).strftime("%-I")
except ValueError:  # pragma: no cover

    def _clock_label(value: datetime) -> str:  # type: ignore[no-redef]
        return value.strftime("%I:%M %p").lstrip("0").replace(":00", "")
