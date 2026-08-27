from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from rf_platform.backend.services.coverage import (
    accepted_run_for_presentation,
    analysis_state_for_capture,
    capture_frequency_range,
    coverage_width_hz,
    merge_ranges,
    missing_ranges,
)
from rf_platform.common.band_compatibility import (
    BAND_INCOMPATIBLE,
    check_findings_band_compatibility,
    incompatible_reason,
    profile_presentation_eligible,
)
from rf_platform.common.scan_profiles import ScanProfile, ScanProfileSet, build_scan_plan
from rf_platform.contracts.analysis import AnalysisResult, ModelIdentity, TechnologyFinding
from rf_platform.worker.validation import validate_analysis_result


def _accepted_profile(profile_id: str = "uae_shared_2400_2483_5") -> ScanProfile:
    return ScanProfile(
        profile_id=profile_id,
        display_name="Accepted 2.4 GHz profile",
        start_frequency_hz=2_400_000_000,
        end_frequency_hz=2_483_500_000,
        capture_bandwidth_hz=20_000_000,
        sample_rate_sps=20_000_000,
        slice_step_hz=18_000_000,
        slice_overlap_hz=2_000_000,
        gain_db=30.0,
        antenna="RX2",
        sample_count=1_048_576,
        priority=1,
        enabled=False,
        candidate_families=("wifi_wlan", "bluetooth_classic", "ble", "ism_srd"),
        qualification_state="operator_accepted",
        presentation_policy="presentation_eligible",
        regulatory_source_note="unit test",
        known_limitations=("unit test",),
    )


def _profile_set(profile: ScanProfile | None = None) -> ScanProfileSet:
    return ScanProfileSet(
        schema_version="1.0",
        profile_set_id="unit-test-set",
        version="1",
        display_name="Unit Test",
        source_notes=(),
        profiles=(profile or _accepted_profile(),),
    )


def _capture(
    *,
    profile_id: str = "uae_shared_2400_2483_5",
    sensor_id: str = "sensor-1",
    center: int = 2_440_000_000,
    bandwidth: int = 20_000_000,
    actual_center: int | None = 2_441_000_000,
    actual_bandwidth: int | None = 18_000_000,
) -> Any:
    hardware: dict[str, Any] = {}
    if actual_center is not None:
        hardware["actual_center_frequency_hz"] = actual_center
    if actual_bandwidth is not None:
        hardware["actual_bandwidth_hz"] = actual_bandwidth
    now = datetime(2026, 8, 26, tzinfo=UTC)
    return SimpleNamespace(
        capture_id="capture-1",
        sensor_id=sensor_id,
        profile_id=profile_id,
        started_at_utc=now,
        ended_at_utc=now + timedelta(seconds=1),
        radio={
            "center_frequency_hz": center,
            "bandwidth_hz": bandwidth,
            "hardware": hardware,
        },
    )


def _run(
    *,
    adapter: str = "vllm",
    model_version: str = "Qwen2.5-VL-7B-rfa-wtr-v2-joint",
    status: str = "succeeded",
    parser_valid: bool = True,
    structured: dict[str, Any] | None = None,
) -> Any:
    return SimpleNamespace(
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


def _sensor(adapter: str = "b210") -> Any:
    return SimpleNamespace(adapter=adapter)


def _job(status: str = "succeeded", error_category: str | None = None) -> Any:
    return SimpleNamespace(status=status, error_category=error_category)


def test_merge_ranges_and_missing_edges_are_deterministic() -> None:
    ranges = [(100, 200), (198, 260), (5_000, 5_100)]

    assert merge_ranges(ranges, tolerance_hz=2) == [(100, 260), (5_000, 5_100)]
    assert coverage_width_hz(ranges) == 260
    assert missing_ranges(
        [(1_000_000, 5_000_000)], [(1_200_000, 3_000_000), (3_200_000, 5_000_000)]
    ) == [(1_000_000, 1_200_000), (3_000_000, 3_200_000)]


def test_capture_frequency_range_uses_actual_hardware_and_intersects_profile() -> None:
    assert capture_frequency_range(_capture(), _profile_set()) == (2_432_000_000, 2_450_000_000)
    assert capture_frequency_range(
        _capture(
            center=2_405_000_000, bandwidth=20_000_000, actual_center=None, actual_bandwidth=None
        ),
        _profile_set(),
    ) == (2_400_000_000, 2_415_000_000)


def test_presentation_filter_excludes_simulated_mock_invalid_and_unvalidated_profiles() -> None:
    profile_set = _profile_set()
    assert accepted_run_for_presentation(_run(), _capture(), _sensor(), _job(), profile_set)
    assert not accepted_run_for_presentation(
        _run(), _capture(), _sensor("simulated"), _job(), profile_set
    )
    assert not accepted_run_for_presentation(
        _run(adapter="mock"), _capture(), _sensor(), _job(), profile_set
    )
    assert not accepted_run_for_presentation(
        _run(parser_valid=False), _capture(), _sensor(), _job(), profile_set
    )
    assert not accepted_run_for_presentation(
        _run(), _capture(), _sensor(), _job(status="deadletter"), profile_set
    )

    experimental_profile = _accepted_profile(profile_id="experimental")
    experimental_profile = ScanProfile(
        **{
            **experimental_profile.__dict__,
            "qualification_state": "experimental",
            "presentation_policy": "technical_only",
        }
    )
    assert (
        profile_presentation_eligible(experimental_profile, experimental_profile.profile_id)
        is False
    )
    assert not accepted_run_for_presentation(
        _run(),
        _capture(profile_id="experimental"),
        _sensor(),
        _job(),
        _profile_set(experimental_profile),
    )


def test_analysis_state_distinguishes_pending_rejected_experimental_and_accepted() -> None:
    assert analysis_state_for_capture(None, None, False) == "analysis_pending"
    assert analysis_state_for_capture(None, _job(status="failed"), False) == "analysis_rejected"
    assert analysis_state_for_capture(_run(), _job(), False) == "experimental_identification"
    assert analysis_state_for_capture(_run(), _job(), True) == "accepted_observation"


@pytest.mark.parametrize(
    ("label", "frequency", "expected"),
    [
        ("Bluetooth", (433_050_000, 434_790_000), "Bluetooth/BLE"),
        ("Wi-Fi", (940_000_000, 960_000_000), "Wi-Fi/WLAN"),
        ("DVB-S2", (98_000_000, 118_000_000), "DVB-S/S2"),
        ("5G mmWave", (3_400_000_000, 3_420_000_000), "mmWave"),
    ],
)
def test_band_consistency_rejects_impossible_protocol_claims(
    label: str, frequency: tuple[int, int], expected: str
) -> None:
    reason = incompatible_reason(label, "model observation", frequency)
    assert reason is not None
    assert expected.lower() in reason.lower()


def test_band_consistency_allows_lte_nr_overlap_and_generic_activity() -> None:
    assert incompatible_reason("LTE", "cellular activity", (3_400_000_000, 3_420_000_000)) is None
    assert incompatible_reason("5G NR", "cellular activity", (3_400_000_000, 3_420_000_000)) is None
    assert (
        incompatible_reason("wideband activity", "generic RF activity", (433_050_000, 434_790_000))
        is None
    )
    assert (
        incompatible_reason("ISM activity", "generic SRD observation", (433_050_000, 434_790_000))
        is None
    )


def test_worker_validation_rejects_incompatible_result_without_losing_raw_response() -> None:
    now = datetime(2026, 8, 26, tzinfo=UTC)
    result = AnalysisResult(
        analysis_id="analysis-1",
        capture_id="capture-1",
        model=ModelIdentity(
            name="rfgpt",
            version="Qwen2.5-VL-7B-rfa-wtr-v2-joint",
            adapter="vllm",
            prompt_version="technology-detection-primary-v4",
        ),
        status="succeeded",
        started_at_utc=now,
        completed_at_utc=now,
        latency_ms=1,
        technologies=[
            TechnologyFinding(
                label="Bluetooth",
                model_score=None,
                observation="Bluetooth observation",
                evidence=["capture_id:capture-1"],
            )
        ],
        signals=[],
        overall_assessment="RF observation only.",
        quality_flags=[],
        parser_valid=True,
        raw_response="raw payload",
        preprocessing_version="atheer-hann-v1",
        inference_parameters={},
    )

    checked = validate_analysis_result(
        result,
        capture=_capture(
            profile_id="uae_srd_433_05_434_79",
            center=433_920_000,
            bandwidth=1_740_000,
            actual_center=433_920_000,
            actual_bandwidth=1_740_000,
        ),
    )

    assert checked.status == "parser_failed"
    assert checked.parser_valid is False
    assert checked.technologies == []
    assert checked.quality_flags == ["parser_failed", BAND_INCOMPATIBLE]
    assert checked.raw_response == "raw payload"


def test_complete_coverage_requires_every_planned_slice() -> None:
    profile = _accepted_profile()
    plan = build_scan_plan(_profile_set(profile), enabled_profile_ids=profile.profile_id)
    required = [(item.coverage_start_hz, item.coverage_end_hz) for item in plan.slices]
    assert len(required) == 5

    assert missing_ranges(required, required[:-1])[-1][1] == profile.end_frequency_hz
    assert missing_ranges(required, required) == []


def test_check_findings_band_compatibility_returns_reject_result() -> None:
    result = check_findings_band_compatibility(
        technologies=[{"label": "Wi-Fi", "observation": "WLAN"}],
        signals=[],
        frequency_range_hz=(940_000_000, 960_000_000),
    )

    assert result.incompatible is True
    assert result.severity == "reject"


def test_coverage_required_ranges_use_full_profile_even_when_manual_scan_was_limited() -> None:
    profile = _accepted_profile()
    plan = build_scan_plan(_profile_set(profile), enabled_profile_ids=profile.profile_id)
    required = [(item.coverage_start_hz, item.coverage_end_hz) for item in plan.slices]
    first_two_manual_slices = required[:2]

    assert coverage_width_hz(first_two_manual_slices) == 38_000_000
    assert profile.width_hz == 83_500_000
    assert missing_ranges(required, first_two_manual_slices) == [(2_438_000_000, 2_483_500_000)]
