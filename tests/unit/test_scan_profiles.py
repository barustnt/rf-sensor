from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from rf_platform.common.config import Settings
from rf_platform.common.scan_profiles import (
    B210_MAX_FREQUENCY_HZ,
    ScanProfileError,
    build_scan_plan,
    load_scan_profile_set,
)
from rf_platform.sensor_agent.scanner import dry_run_plan

CATALOGUE = Path("config/scan-profiles/uae-b210-sub6-v1.toml")


def test_uae_catalogue_loads_and_is_disabled_by_default() -> None:
    profile_set = load_scan_profile_set(CATALOGUE, expected_profile_set="uae-b210-sub6-v1")
    plan = build_scan_plan(profile_set, enabled_profile_ids="")

    assert profile_set.profile_set_id == "uae-b210-sub6-v1"
    assert len(profile_set.profiles) == 25
    assert len(profile_set.unsupported_profiles) == 3
    assert all(not profile.enabled for profile in profile_set.profiles)
    assert plan.slices == ()
    assert "no captures will be planned" in plan.warnings[0].lower()


def test_explicit_allowlist_is_deterministic_and_estimates_overlap() -> None:
    profile_set = load_scan_profile_set(CATALOGUE)
    first = build_scan_plan(
        profile_set,
        enabled_profile_ids="uae_shared_2400_2483_5,uae_srd_433_05_434_79",
    )
    second = build_scan_plan(
        profile_set,
        enabled_profile_ids=["uae_shared_2400_2483_5", "uae_srd_433_05_434_79"],
    )

    assert [item.as_dict() for item in first.slices] == [item.as_dict() for item in second.slices]
    assert [item.profile_id for item in first.slices[:1]] == ["uae_shared_2400_2483_5"]
    assert first.slice_counts() == {"uae_shared_2400_2483_5": 5, "uae_srd_433_05_434_79": 1}
    estimates = first.estimates(retune_settle_seconds=0.1)
    assert estimates["slice_count"] == 6
    assert estimates["overlap_hz"] > 0
    assert estimates["inference_time_included"] is False
    shared = [item for item in first.slices if item.profile_id == "uae_shared_2400_2483_5"]
    assert shared[0].coverage_start_hz == 2_400_000_000
    assert shared[-1].coverage_end_hz == 2_483_500_000
    assert shared[1].requested_start_hz - shared[0].requested_start_hz == 18_000_000


def test_full_scannable_catalogue_slice_count_is_stable() -> None:
    profile_set = load_scan_profile_set(CATALOGUE)
    enabled = [profile.profile_id for profile in profile_set.profiles if profile.can_be_scanned]
    plan = build_scan_plan(profile_set, enabled_profile_ids=enabled)

    assert len(enabled) == 24
    assert len(plan.slices) == 145
    assert plan.estimates()["nominal_covered_bandwidth_hz"] == 2_372_740_000


def test_allowlist_rejects_unknown_and_regulatory_review_profiles() -> None:
    profile_set = load_scan_profile_set(CATALOGUE)

    with pytest.raises(ScanProfileError, match="not found"):
        build_scan_plan(profile_set, enabled_profile_ids="missing_profile")
    with pytest.raises(ScanProfileError, match="regulatory_review_required"):
        build_scan_plan(profile_set, enabled_profile_ids="uae_future_study_3800_4200")


def _write_catalogue(path: Path, body: str) -> Path:
    path.write_text(
        f"""
schema_version = "1.0"
profile_set_id = "test-set"
version = "1"
display_name = "Test"
source_notes = []

[defaults]
capture_bandwidth_hz = 20000000
sample_rate_sps = 20000000
slice_step_hz = 18000000
slice_overlap_hz = 2000000
gain_db = 10
antenna = "RX2"
sample_count = 1024
enabled = false
qualification_state = "experimental"
presentation_policy = "technical_only"
known_limitations = []

{body}
""",
        encoding="utf-8",
    )
    return path


def _profile(
    profile_id: str = "p1", *, start: int = 100_000_000, end: int = 130_000_000, extra: str = ""
) -> str:
    return f'''
[[profiles]]
profile_id = "{profile_id}"
display_name = "Profile {profile_id}"
start_frequency_hz = {start}
end_frequency_hz = {end}
priority = 1
candidate_families = ["wideband_activity"]
regulatory_source_note = "unit test"
{extra}
'''


def test_invalid_ranges_duplicates_and_b210_limits_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ScanProfileError, match="invalid frequency range"):
        load_scan_profile_set(
            _write_catalogue(tmp_path / "bad-range.toml", _profile(end=90_000_000))
        )

    with pytest.raises(ScanProfileError, match="duplicate scan profile ID"):
        load_scan_profile_set(
            _write_catalogue(
                tmp_path / "dupe.toml",
                _profile("p1") + _profile("p1", start=150_000_000, end=170_000_000),
            )
        )

    with pytest.raises(ScanProfileError, match="outside the B210 receive range"):
        load_scan_profile_set(
            _write_catalogue(tmp_path / "out.toml", _profile(end=B210_MAX_FREQUENCY_HZ + 1))
        )
    with pytest.raises(ScanProfileError, match="requested capture slice"):
        load_scan_profile_set(
            _write_catalogue(tmp_path / "edge.toml", _profile(start=70_000_000, end=75_000_000))
        )


def test_bandwidth_sample_rate_overlap_and_presentation_validation(tmp_path: Path) -> None:
    with pytest.raises(ScanProfileError, match="bandwidth exceeds sample rate"):
        load_scan_profile_set(
            _write_catalogue(
                tmp_path / "bw.toml",
                _profile(extra="capture_bandwidth_hz = 30000000\nsample_rate_sps = 20000000"),
            )
        )
    with pytest.raises(ScanProfileError, match="slice overlap"):
        load_scan_profile_set(
            _write_catalogue(tmp_path / "overlap.toml", _profile(extra="slice_overlap_hz = 1"))
        )
    with pytest.raises(ScanProfileError, match="cannot be presentation eligible"):
        load_scan_profile_set(
            _write_catalogue(
                tmp_path / "presentation.toml",
                _profile(extra='presentation_policy = "presentation_eligible"'),
            )
        )

    accepted = load_scan_profile_set(
        _write_catalogue(
            tmp_path / "accepted.toml",
            _profile(
                extra=(
                    'qualification_state = "operator_accepted"\n'
                    'presentation_policy = "presentation_eligible"'
                )
            ),
        )
    )
    assert accepted.profiles[0].presentation_eligible is True


def test_dry_run_plan_never_requires_hardware_api_or_vllm(tmp_path: Path) -> None:
    settings = Settings(
        sensor_adapter="b210",
        scan_profile_config=CATALOGUE,
        scan_enabled_profile_ids="uae_shared_2400_2483_5",
        scan_max_slices_per_cycle=2,
        scan_retune_settle_seconds=0.25,
    )

    plan = dry_run_plan(settings)

    assert plan["estimates"]["slice_count"] == 2
    assert plan["estimates"]["minimum_capture_only_cycle_duration_seconds"] > 0
    assert "vLLM" in " ".join(plan["notes"])


@pytest.mark.asyncio
async def test_read_only_scan_profiles_endpoint_contract() -> None:
    from rf_platform.backend.api.v1.scan import scan_profiles

    response = cast(
        dict[str, Any],
        await scan_profiles(
            Settings(
                scan_profile_config=CATALOGUE,
                scan_enabled_profile_ids="uae_shared_2400_2483_5",
                scan_retune_settle_seconds=0.1,
            )
        ),
    )

    assert response["schema_version"] == "1.0"
    assert response["profile_set"]["profile_set_id"] == "uae-b210-sub6-v1"
    assert response["estimates"]["slice_count"] == 5
    assert response["notes"][0].startswith("Dry-run")
