from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
import respx

from rf_platform.ask_rf.api_client import AskRFApiClient
from rf_platform.ask_rf.main import ASK_RF_CSS, render_answer, reset_conversation, submit_question
from rf_platform.backend.services.ask_rf import (
    AskRFDataset,
    AskRFRecord,
    QuestionIntent,
    _not_monitored_response,
    _resolve_interval,
    answer_ask_rf,
    build_answer,
    interpret_question,
    normalize_question,
    presentation_record_from_run,
)
from rf_platform.common.config import Settings
from rf_platform.common.time import InterpretedInterval
from rf_platform.contracts.api import AskRFRequest, AskRFResponse, QueryInterval


def _interval() -> InterpretedInterval:
    return InterpretedInterval(
        start_utc=datetime(2026, 8, 26, 6, 0, tzinfo=UTC),
        end_utc=datetime(2026, 8, 26, 7, 0, tzinfo=UTC),
        display_timezone="Asia/Dubai",
        assumptions=[],
    )


def _record(
    *,
    labels: list[str] | None = None,
    overall: str = "RF observation only.",
    flags: list[str] | None = None,
    coverage: tuple[int, int] = (2_430_000_000, 2_450_000_000),
) -> AskRFRecord:
    return AskRFRecord(
        capture_id="capture-1",
        analysis_id="analysis-1",
        sensor_id="laptop-b210-001",
        sensor_adapter="b210",
        profile_id="b210_2g4_demo",
        started_at_utc=_interval().start_utc,
        ended_at_utc=_interval().start_utc + timedelta(seconds=1),
        location={"site": "campus", "room": "lab"},
        frequency_start_hz=coverage[0],
        frequency_end_hz=coverage[1],
        technologies=[
            {
                "label": label,
                "model_score": None,
                "observation": "RF-only observation.",
                "evidence": ["capture_id:capture-1"],
            }
            for label in (labels or [])
        ],
        signals=[],
        overall_assessment=overall,
        quality_flags=flags or [],
    )


def _dataset(
    *,
    records: list[AskRFRecord] | None = None,
    real_capture_count: int = 1,
    rejected_result_count: int = 0,
    coverage: list[tuple[int, int]] | None = None,
) -> AskRFDataset:
    records = records or []
    return AskRFDataset(
        real_capture_count=real_capture_count,
        rejected_result_count=rejected_result_count,
        accepted_records=records,
        locations=[{"site": "campus", "room": "lab"}] if real_capture_count else [],
        coverage_ranges_hz=coverage if coverage is not None else [(2_430_000_000, 2_450_000_000)],
    )


def _run_payload(
    *,
    adapter: str = "vllm",
    model_version: str = "Qwen2.5-VL-7B-rfa-wtr-v2-joint",
    status: str = "succeeded",
    parser_valid: bool = True,
    structured: dict[str, Any] | None = None,
) -> Any:
    return SimpleNamespace(
        analysis_id="analysis-1",
        capture_id="capture-1",
        job_id="job-1",
        adapter=adapter,
        model_version=model_version,
        status=status,
        parser_valid=parser_valid,
        structured_result=structured
        if structured is not None
        else {
            "technologies": [],
            "signals": [],
            "overall_assessment": "No signals present.",
            "quality_flags": [],
        },
    )


def _capture() -> Any:
    return SimpleNamespace(
        capture_id="capture-1",
        sensor_id="laptop-b210-001",
        profile_id="b210_2g4_demo",
        started_at_utc=_interval().start_utc,
        ended_at_utc=_interval().start_utc + timedelta(seconds=1),
        radio={
            "center_frequency_hz": 2_440_000_000,
            "bandwidth_hz": 20_000_000,
            "hardware": {
                "actual_center_frequency_hz": 2_440_000_000,
                "actual_bandwidth_hz": 20_000_000,
            },
        },
    )


def _sensor(adapter: str = "b210") -> Any:
    return SimpleNamespace(adapter=adapter, location={"site": "campus", "room": "lab"})


def _job(status: str = "succeeded", error_category: str | None = None) -> Any:
    return SimpleNamespace(status=status, error_category=error_category)


def test_observation_answer_is_plain_language() -> None:
    response = build_answer(
        QuestionIntent("summary"),
        _interval(),
        _dataset(records=[_record(labels=["chirp"])]),
    )

    assert response.answer_status == "observation"
    assert "observed chirp-like RF activity" in response.display_answer
    assert "not been independently confirmed" in response.display_answer
    assert "model run" not in response.display_answer.lower()


def test_no_signal_answer_is_distinct_from_no_data() -> None:
    no_signal = build_answer(
        QuestionIntent("summary"),
        _interval(),
        _dataset(records=[_record(overall="No signals present.")]),
    )
    no_data = build_answer(QuestionIntent("summary"), _interval(), _dataset(real_capture_count=0))

    assert no_signal.answer_status == "no_signal"
    assert "No signal or wireless technology was confirmed" in no_signal.display_answer
    assert no_data.answer_status == "no_data"
    assert "No sensor observations are available" in no_data.display_answer


def test_partial_rejected_data_answer() -> None:
    response = build_answer(
        QuestionIntent("summary"),
        _interval(),
        _dataset(records=[], real_capture_count=1, rejected_result_count=1),
    )

    assert response.answer_status == "partial_data"
    assert "did not pass consistency checks" in response.display_answer


def test_mixed_accepted_observations_are_cautious() -> None:
    response = build_answer(
        QuestionIntent("summary"),
        _interval(),
        _dataset(
            records=[
                _record(labels=["chirp"], overall="RF observation only."),
                _record(overall="No signals present."),
            ]
        ),
    )

    assert response.answer_status == "partial_data"
    assert "No reliable conclusion" in response.display_answer


def test_bluetooth_partial_band_language() -> None:
    response = build_answer(
        QuestionIntent("technology", "bluetooth"),
        _interval(),
        _dataset(records=[_record(overall="No signals present.")]),
    )

    assert response.answer_status == "no_signal"
    assert "Bluetooth was not confirmed in the monitored portion" in response.display_answer
    assert "does not prove Bluetooth was absent" in response.display_answer


@pytest.mark.parametrize("technology", ["lte", "5g"])
def test_lte_and_5g_are_not_monitored(technology: str) -> None:
    response = _not_monitored_response(_interval(), technology)

    assert response.answer_status == "not_monitored"
    assert "did not monitor" in response.display_answer
    assert "cannot determine" in response.display_answer


def test_unsupported_question_response() -> None:
    response = build_answer(
        QuestionIntent("unsupported"), _interval(), _dataset(real_capture_count=0)
    )
    # Unsupported questions are normally short-circuited by the endpoint.
    # The lower-level answer builder still remains safe.
    assert response.answer_status == "no_data"


def test_mock_simulated_parser_invalid_failed_and_contradictory_records_are_excluded() -> None:
    valid_structured = {
        "technologies": [],
        "signals": [],
        "overall_assessment": "No signals present.",
        "quality_flags": [],
    }
    contradictory = {
        "technologies": [
            {
                "label": "chirp",
                "model_score": None,
                "observation": "chirp transmission",
                "evidence": ["capture_id:b1380292-ec86-46b9-ac62-1dcc219e19d8"],
            }
        ],
        "signals": [],
        "overall_assessment": "RF observation only; no signals.",
        "quality_flags": [],
    }

    assert (
        presentation_record_from_run(
            _run_payload(adapter="mock", structured=valid_structured), _capture(), _sensor(), _job()
        )
        is None
    )
    assert (
        presentation_record_from_run(
            _run_payload(model_version="mock-v1", structured=valid_structured),
            _capture(),
            _sensor(),
            _job(),
        )
        is None
    )
    assert (
        presentation_record_from_run(
            _run_payload(structured=valid_structured), _capture(), _sensor("simulated"), _job()
        )
        is None
    )
    assert (
        presentation_record_from_run(
            _run_payload(parser_valid=False, structured=valid_structured),
            _capture(),
            _sensor(),
            _job(),
        )
        is None
    )
    assert (
        presentation_record_from_run(
            _run_payload(structured=valid_structured), _capture(), _sensor(), _job(status="failed")
        )
        is None
    )
    assert (
        presentation_record_from_run(
            _run_payload(structured=valid_structured),
            _capture(),
            _sensor(),
            _job(error_category="model_configuration_mismatch"),
        )
        is None
    )
    assert (
        presentation_record_from_run(
            _run_payload(structured=contradictory), _capture(), _sensor(), _job()
        )
        is None
    )


def test_final_valid_real_no_signal_record_is_included() -> None:
    record = presentation_record_from_run(_run_payload(), _capture(), _sensor(), _job())

    assert record is not None
    assert record.no_signal is True
    assert record.has_findings is False


def test_findings_with_neutral_assessment_succeed() -> None:
    structured = {
        "technologies": [
            {
                "label": "chirp",
                "model_score": None,
                "observation": "RF-only chirp observation.",
                "evidence": ["capture_id:capture-1"],
            }
        ],
        "signals": [],
        "overall_assessment": "RF observation only.",
        "quality_flags": [],
    }

    record = presentation_record_from_run(
        _run_payload(structured=structured), _capture(), _sensor(), _job()
    )

    assert record is not None
    assert record.technology_labels == ["chirp"]


def test_follow_up_reuses_prior_interval_and_new_question_resets_context() -> None:
    prior = {
        "start_utc": "2026-08-25T06:00:00+00:00",
        "end_utc": "2026-08-25T07:00:00+00:00",
        "display_timezone": "Asia/Dubai",
    }
    interval = _resolve_interval("Was it Bluetooth?", "Asia/Dubai", prior, None)

    assert interval.start_utc.isoformat() == "2026-08-25T06:00:00+00:00"
    assert "Reused" in interval.assumptions[0]
    assert reset_conversation() == ("", "", "", {}, "")


def test_automatic_asia_dubai_timezone_setting() -> None:
    settings = Settings()
    assert settings.display_timezone == "Asia/Dubai"


def test_question_normalization_supports_happened_typo_and_spacing() -> None:
    questions = [
        "What happened today at 8 AM?",
        "what happened today at 8 AM ?",
        "  what   happend   today at 8 AM ?  ",
    ]
    now = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)
    intervals = [_resolve_interval(question, "Asia/Dubai", None, now) for question in questions]
    answers = []
    for question, interval in zip(questions, intervals, strict=True):
        answers.append(
            build_answer(
                interpret_question(question),
                interval,
                _dataset(records=[_record(overall="No signals present.")]),
            )
        )

    assert normalize_question(questions[-1]) == "what happened today at 8 am?"
    assert {interval.start_utc for interval in intervals} == {
        datetime(2026, 8, 26, 4, 0, tzinfo=UTC)
    }
    assert {interval.end_utc for interval in intervals} == {datetime(2026, 8, 26, 5, 0, tzinfo=UTC)}
    assert {interpret_question(question).kind for question in questions} == {"summary"}
    assert {answer.answer_status for answer in answers} == {"no_signal"}


@pytest.mark.asyncio
async def test_unrelated_question_remains_unsupported() -> None:
    response = await answer_ask_rf(
        cast(Any, None),
        AskRFRequest(question="Can you identify the owner of this device?"),
        now=datetime(2026, 8, 26, 0, 0, tzinfo=UTC),
    )

    assert response.answer_status == "unsupported_question"
    assert "I can answer questions about wireless activity" in response.display_answer


def test_rendered_answer_hides_json_and_technical_identifiers() -> None:
    response = AskRFResponse(
        answer_status="observation",
        display_answer="Between 10:00 and 11:00 AM, the system observed chirp-like RF activity.",
        interpreted_interval=QueryInterval(
            start_utc=_interval().start_utc,
            end_utc=_interval().end_utc,
            display_timezone="Asia/Dubai",
            assumptions=[],
        ),
        time_label="August 26, 2026, between 10 AM and 11 AM",
        location_label="campus / lab",
        evidence_explanation="Used 1 accepted observation from 1 real hardware capture.",
        limitations=["AI-assisted RF observation—not independently confirmed ground truth."],
        follow_up_context={"analysis_id": "a3c875f4-94ac-4247-990d-a04b983f3fdf"},
    )

    question_html, answer_html, details, _context = render_answer(
        response, "What happened today at 10 AM?"
    )
    visible = question_html + answer_html + details

    assert "schema_version" not in visible
    assert "a3c875f4-94ac-4247-990d-a04b983f3fdf" not in visible
    assert "model version" not in visible.lower()
    assert "parser" not in visible.lower()
    assert "{" not in visible


@pytest.mark.parametrize(
    "status",
    [
        "observation",
        "no_signal",
        "no_data",
        "partial_data",
        "not_monitored",
        "unsupported_question",
        "unavailable",
    ],
)
def test_plain_language_rendering_for_every_answer_status(status: str) -> None:
    response = AskRFResponse(
        answer_status=cast(Any, status),
        display_answer="The system could not determine a verified result from stored observations.",
        interpreted_interval=QueryInterval(
            start_utc=_interval().start_utc,
            end_utc=_interval().end_utc,
            display_timezone="Asia/Dubai",
            assumptions=[],
        ),
        time_label="August 26, 2026, between 10 AM and 11 AM",
        location_label="monitored area",
        evidence_explanation="Used accepted observations only.",
        limitations=["AI-assisted RF observation—not independently confirmed ground truth."],
        follow_up_context={},
    )

    _question, answer, details, _context = render_answer(response, "Question?")
    assert "The system could not determine" in answer
    assert "Used accepted observations only" in details
    assert "schema_version" not in answer + details


@respx.mock
def test_ask_rf_api_client_health_and_query() -> None:
    respx.get("http://api.local/health/ready").mock(
        return_value=httpx.Response(200, json={"status": "ok", "components": {}})
    )
    route = respx.post("http://api.local/api/v1/ask-rf/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "answer_status": "no_signal",
                "display_answer": "No signal was confirmed.",
                "interpreted_interval": {
                    "schema_version": "1.0",
                    "start_utc": _interval().start_utc.isoformat(),
                    "end_utc": _interval().end_utc.isoformat(),
                    "display_timezone": "Asia/Dubai",
                    "assumptions": [],
                },
                "time_label": "August 26, 2026, between 10 AM and 11 AM",
                "location_label": "monitored area",
                "evidence_explanation": "Used accepted observations only.",
                "limitations": [],
                "follow_up_context": {"start_utc": _interval().start_utc.isoformat()},
            },
        )
    )
    client = AskRFApiClient(Settings(platform_url="http://api.local", api_timeout_seconds=1))

    assert client.ready() is True
    response = client.query("Was Bluetooth observed?", {"start_utc": "previous"})

    assert response.answer_status == "no_signal"
    payload = route.calls.last.request.read().decode()
    assert "Was Bluetooth observed?" in payload
    assert "Asia/Dubai" in payload


@respx.mock
def test_api_unavailable_behavior_is_friendly() -> None:
    respx.get("http://api.local/health/ready").mock(side_effect=httpx.ConnectError("no api"))
    client = AskRFApiClient(Settings(platform_url="http://api.local", api_timeout_seconds=1))

    assert client.ready() is False

    class FailingClient:
        display_timezone = "Asia/Dubai"

        def query(self, _question: str, _context: dict[str, Any] | None = None) -> AskRFResponse:
            raise httpx.ConnectError("hidden technical detail")

    _q, answer, details, context = submit_question(
        cast(AskRFApiClient, FailingClient()), "What happened today?", {}
    )

    assert "temporarily unavailable" in answer
    assert "hidden technical detail" not in answer + details
    assert context == {}


def test_ask_rf_css_enforces_readable_light_theme() -> None:
    assert "background: #ffffff !important" in ASK_RF_CSS
    assert ".askrf-title" in ASK_RF_CSS and "color: #06152b !important" in ASK_RF_CSS
    assert ".askrf-heading" in ASK_RF_CSS and "color: #0f172a !important" in ASK_RF_CSS
    assert ".askrf-supporting" in ASK_RF_CSS and "color: #1f2937 !important" in ASK_RF_CSS
    assert ".askrf-answer" in ASK_RF_CSS and "color: #111827 !important" in ASK_RF_CSS
    assert ".askrf-labels" in ASK_RF_CSS and "color: #334155 !important" in ASK_RF_CSS
    assert ".askrf-question" in ASK_RF_CSS and "color: #075985 !important" in ASK_RF_CSS
    assert ".askrf-disclosure" in ASK_RF_CSS and "color: #0f172a !important" in ASK_RF_CSS


def test_ask_rf_component_source_hides_textbox_label_and_styles_light_controls() -> None:
    source = Path("src/rf_platform/ask_rf/main.py").read_text(encoding="utf-8")

    assert "label=None" in source
    assert "show_label=False" in source
    assert "askrf-primary" in source
    assert "askrf-secondary" in source
    assert "askrf-example" in source
    assert "background: #ffffff !important" in ASK_RF_CSS
    assert "border: 1px solid #bfdbfe" in ASK_RF_CSS
    assert "color: #0f172a !important" in ASK_RF_CSS


def test_ask_rf_css_wraps_responsively_without_fixed_overflow() -> None:
    assert "overflow-x: hidden" in ASK_RF_CSS
    assert "flex-wrap: wrap" in ASK_RF_CSS
    assert "max-width: 100%" in ASK_RF_CSS
    assert "@media (max-width: 760px)" in ASK_RF_CSS
    assert "white-space: normal" in ASK_RF_CSS


def test_command_center_no_longer_contains_ask_rf_tab() -> None:
    source = Path("src/rf_platform/dashboard/main.py").read_text(encoding="utf-8")
    assert 'gr.Tab("Ask RF")' not in source
    assert "RF Command Center" in source


def test_ask_rf_source_contains_no_operational_controls_or_vllm_invocation() -> None:
    ask_rf_files = Path("src/rf_platform/ask_rf").rglob("*.py")
    haystack = "\n".join(path.read_text(encoding="utf-8") for path in ask_rf_files)
    forbidden = [
        "retry_job",
        "update_alert",
        "RF_SENSOR_TOKEN",
        "database_url",
        "LocalVLLM",
        "RFGPTAdapter",
        "sensor_agent",
        "spectrogram",
        "raw_response",
    ]
    for term in forbidden:
        assert term not in haystack
    assert "@media" in ASK_RF_CSS
    assert "#ffffff" in ASK_RF_CSS
