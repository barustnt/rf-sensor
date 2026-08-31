from __future__ import annotations

import json
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rf_platform.contracts.capture import (
    CaptureProfile,
    CaptureTiming,
    PreprocessingProfile,
    RadioSettings,
    RetentionSettings,
    Schedule,
)

B210_MIN_FREQUENCY_HZ = 70_000_000
B210_MAX_FREQUENCY_HZ = 6_000_000_000
B210_MAX_SAMPLE_RATE_SPS = 61_440_000
B210_MAX_ANALOG_BANDWIDTH_HZ = 56_000_000
VALID_QUALIFICATION_STATES = {
    "experimental",
    "operator_accepted",
    "independently_validated",
    "regulatory_review_required",
    "unsupported_hardware",
}
VALID_PRESENTATION_POLICIES = {"hidden", "technical_only", "presentation_eligible"}
PRESENTATION_QUALIFICATION_STATES = {"operator_accepted", "independently_validated"}


class ScanProfileError(ValueError):
    pass


@dataclass(frozen=True)
class ScanProfile:
    profile_id: str
    display_name: str
    start_frequency_hz: int
    end_frequency_hz: int
    capture_bandwidth_hz: int
    sample_rate_sps: int
    slice_step_hz: int
    slice_overlap_hz: int
    gain_db: float
    antenna: str
    sample_count: int
    priority: int
    enabled: bool
    candidate_families: tuple[str, ...]
    qualification_state: str
    presentation_policy: str
    regulatory_source_note: str
    known_limitations: tuple[str, ...] = ()

    @property
    def width_hz(self) -> int:
        return self.end_frequency_hz - self.start_frequency_hz

    @property
    def presentation_eligible(self) -> bool:
        return (
            self.presentation_policy == "presentation_eligible"
            and self.qualification_state in PRESENTATION_QUALIFICATION_STATES
        )

    @property
    def can_be_scanned(self) -> bool:
        return self.qualification_state not in {
            "unsupported_hardware",
            "regulatory_review_required",
        }

    def to_capture_profile(self, *, center_frequency_hz: int) -> CaptureProfile:
        duration_ms = max(1, math.ceil((self.sample_count / self.sample_rate_sps) * 1000.0))
        return CaptureProfile(
            profile_id=self.profile_id,
            description=self.display_name,
            enabled=True,
            schedule=Schedule(mode="continuous"),
            radio=RadioSettings(
                center_frequency_hz=center_frequency_hz,
                sample_rate_sps=self.sample_rate_sps,
                bandwidth_hz=self.capture_bandwidth_hz,
                gain_mode="manual",
                gain_db=self.gain_db,
                antenna=self.antenna,
            ),
            capture=CaptureTiming(
                duration_ms=duration_ms, interval_ms=duration_ms, sample_count=self.sample_count
            ),
            preprocessing=PreprocessingProfile(
                pipeline_version="atheer-hann-v1",
                fft_size=512,
                hop_size=512,
                window="hann",
                output_width_px=512,
                output_height_px=512,
                color_map="viridis",
                include_axes=False,
                db_min=-110,
                db_max=-20,
            ),
            retention=RetentionSettings(
                upload_spectrogram=True, upload_iq="never", local_iq_ring_seconds=0
            ),
        )

    def as_dict(self, *, selected: bool = False, slice_count: int | None = None) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "start_frequency_hz": self.start_frequency_hz,
            "end_frequency_hz": self.end_frequency_hz,
            "capture_bandwidth_hz": self.capture_bandwidth_hz,
            "sample_rate_sps": self.sample_rate_sps,
            "slice_step_hz": self.slice_step_hz,
            "slice_overlap_hz": self.slice_overlap_hz,
            "gain_db": self.gain_db,
            "antenna": self.antenna,
            "sample_count": self.sample_count,
            "priority": self.priority,
            "enabled": self.enabled,
            "selected_for_scan": selected,
            "candidate_families": list(self.candidate_families),
            "qualification_state": self.qualification_state,
            "presentation_policy": self.presentation_policy,
            "presentation_eligible": self.presentation_eligible,
            "regulatory_source_note": self.regulatory_source_note,
            "known_limitations": list(self.known_limitations),
            "slice_count": slice_count,
        }


@dataclass(frozen=True)
class UnsupportedProfile:
    profile_id: str
    display_name: str
    start_frequency_hz: int
    end_frequency_hz: int
    reason: str
    qualification_state: str = "unsupported_hardware"

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "start_frequency_hz": self.start_frequency_hz,
            "end_frequency_hz": self.end_frequency_hz,
            "qualification_state": self.qualification_state,
            "reason": self.reason,
            "presentation_eligible": False,
        }


@dataclass(frozen=True)
class ScanProfileSet:
    schema_version: str
    profile_set_id: str
    version: str
    display_name: str
    source_notes: tuple[str, ...]
    profiles: tuple[ScanProfile, ...]
    unsupported_profiles: tuple[UnsupportedProfile, ...] = ()

    def profile_by_id(self) -> dict[str, ScanProfile]:
        return {profile.profile_id: profile for profile in self.profiles}

    def as_dict(
        self, *, selected_ids: set[str] | None = None, slice_counts: dict[str, int] | None = None
    ) -> dict[str, Any]:
        selected_ids = selected_ids or set()
        slice_counts = slice_counts or {}
        return {
            "schema_version": self.schema_version,
            "profile_set_id": self.profile_set_id,
            "version": self.version,
            "display_name": self.display_name,
            "source_notes": list(self.source_notes),
            "profiles": [
                profile.as_dict(
                    selected=profile.profile_id in selected_ids,
                    slice_count=slice_counts.get(profile.profile_id),
                )
                for profile in self.profiles
            ],
            "unsupported_profiles": [profile.as_dict() for profile in self.unsupported_profiles],
        }


@dataclass(frozen=True)
class ScanSlice:
    profile_id: str
    slice_index: int
    center_frequency_hz: int
    capture_bandwidth_hz: int
    sample_rate_sps: int
    requested_start_hz: int
    requested_end_hz: int
    coverage_start_hz: int
    coverage_end_hz: int
    gain_db: float
    antenna: str
    sample_count: int
    qualification_state: str
    presentation_policy: str
    candidate_families: tuple[str, ...]

    @property
    def capture_duration_seconds(self) -> float:
        return self.sample_count / self.sample_rate_sps

    def to_capture_profile(self, profile_set: ScanProfileSet) -> CaptureProfile:
        profile = profile_set.profile_by_id()[self.profile_id]
        return profile.to_capture_profile(center_frequency_hz=self.center_frequency_hz)

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "slice_index": self.slice_index,
            "center_frequency_hz": self.center_frequency_hz,
            "capture_bandwidth_hz": self.capture_bandwidth_hz,
            "sample_rate_sps": self.sample_rate_sps,
            "requested_start_hz": self.requested_start_hz,
            "requested_end_hz": self.requested_end_hz,
            "coverage_start_hz": self.coverage_start_hz,
            "coverage_end_hz": self.coverage_end_hz,
            "gain_db": self.gain_db,
            "antenna": self.antenna,
            "sample_count": self.sample_count,
            "qualification_state": self.qualification_state,
            "presentation_policy": self.presentation_policy,
            "candidate_families": list(self.candidate_families),
        }


@dataclass(frozen=True)
class ScanPlan:
    profile_set: ScanProfileSet
    enabled_profile_ids: tuple[str, ...]
    slices: tuple[ScanSlice, ...]
    warnings: tuple[str, ...] = ()
    full_slice_counts: tuple[tuple[str, int], ...] = field(default_factory=tuple)

    @property
    def selected_profile_ids(self) -> set[str]:
        return set(self.enabled_profile_ids)

    @property
    def planned_profile_ids(self) -> set[str]:
        return {item.profile_id for item in self.slices}

    def selected_profiles(self) -> list[ScanProfile]:
        selected_ids = set(self.enabled_profile_ids)
        return [
            profile for profile in self.profile_set.profiles if profile.profile_id in selected_ids
        ]

    def full_slice_count_by_profile(self) -> dict[str, int]:
        return dict(self.full_slice_counts)

    def slice_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.slices:
            counts[item.profile_id] = counts.get(item.profile_id, 0) + 1
        return counts

    def estimates(self, *, retune_settle_seconds: float = 0.0) -> dict[str, Any]:
        selected = self.selected_profiles()
        configured_bandwidth = sum(profile.width_hz for profile in selected)
        requested = sum(item.capture_bandwidth_hz for item in self.slices)
        planned_union = _planned_union_coverage_hz(self.slices)
        duration = sum(
            item.capture_duration_seconds + retune_settle_seconds for item in self.slices
        )
        full_slice_count = sum(
            self.full_slice_count_by_profile().get(profile.profile_id, 0) for profile in selected
        )
        planned_slice_count = len(self.slices)
        plan_truncated = planned_slice_count < full_slice_count
        full_profile_coverage_complete = (
            bool(selected) and not plan_truncated and planned_union >= configured_bandwidth
        )
        return {
            "profile_count": len(selected),
            "slice_count": planned_slice_count,
            "full_profile_slice_count": full_slice_count,
            "planned_slice_count": planned_slice_count,
            "plan_truncated": plan_truncated,
            "nominal_covered_bandwidth_hz": planned_union,
            "planned_union_coverage_hz": planned_union,
            "configured_profile_bandwidth_hz": configured_bandwidth,
            "requested_capture_bandwidth_hz": requested,
            "overlap_hz": max(0, requested - planned_union),
            "full_profile_coverage_complete": full_profile_coverage_complete,
            "expected_capture_count_per_cycle": planned_slice_count,
            "minimum_capture_only_cycle_duration_seconds": round(duration, 3),
            "inference_time_included": False,
        }

    def as_dict(
        self, *, retune_settle_seconds: float = 0.0, verbose: bool = False
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "profile_set": {
                "schema_version": self.profile_set.schema_version,
                "profile_set_id": self.profile_set.profile_set_id,
                "version": self.profile_set.version,
                "display_name": self.profile_set.display_name,
            },
            "selected_profiles": [
                profile.as_dict(
                    selected=True,
                    slice_count=self.slice_counts().get(profile.profile_id, 0),
                )
                for profile in self.selected_profiles()
            ],
            "enabled_profile_ids": list(self.enabled_profile_ids),
            "slices": [item.as_dict() for item in self.slices],
            "estimates": self.estimates(retune_settle_seconds=retune_settle_seconds),
            "warnings": list(self.warnings),
            "notes": [
                (
                    "Dry-run and estimates do not access B210 hardware, the platform API, "
                    "workers, or vLLM."
                ),
                "Capture-only cycle duration excludes RF-GPT inference time.",
            ],
        }
        if verbose:
            payload["profile_set"] = self.profile_set.as_dict(
                selected_ids=self.selected_profile_ids,
                slice_counts=self.slice_counts(),
            )
        return payload

    def to_json(self, *, retune_settle_seconds: float = 0.0, verbose: bool = False) -> str:
        return json.dumps(
            self.as_dict(retune_settle_seconds=retune_settle_seconds, verbose=verbose),
            indent=2,
            sort_keys=True,
        )


def _planned_union_coverage_hz(slices: tuple[ScanSlice, ...]) -> int:
    ranges_by_profile: dict[str, list[tuple[int, int]]] = {}
    for item in slices:
        ranges_by_profile.setdefault(item.profile_id, []).append(
            (item.coverage_start_hz, item.coverage_end_hz)
        )
    return sum(_range_width_hz(ranges) for ranges in ranges_by_profile.values())


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    cleaned = sorted((int(start), int(end)) for start, end in ranges if end > start)
    if not cleaned:
        return []
    merged = [cleaned[0]]
    for start, end in cleaned[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _range_width_hz(ranges: list[tuple[int, int]]) -> int:
    return sum(end - start for start, end in _merge_ranges(ranges))


def parse_enabled_profile_ids(value: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    raw = value.split(",") if isinstance(value, str) else list(value)
    return tuple(dict.fromkeys(item.strip() for item in raw if item and item.strip()))


def load_scan_profile_set(path: Path, *, expected_profile_set: str | None = None) -> ScanProfileSet:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ScanProfileError(f"scan profile configuration not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ScanProfileError(f"invalid scan profile TOML: {exc}") from exc
    profile_set = _profile_set_from_data(data)
    validate_profile_set(profile_set)
    if expected_profile_set and profile_set.profile_set_id != expected_profile_set:
        raise ScanProfileError(
            f"scan profile set mismatch: expected {expected_profile_set!r}, "
            f"found {profile_set.profile_set_id!r}"
        )
    return profile_set


def _profile_set_from_data(data: dict[str, Any]) -> ScanProfileSet:
    defaults = data.get("defaults", {}) if isinstance(data.get("defaults", {}), dict) else {}
    profiles = tuple(_profile_from_data(item, defaults) for item in data.get("profiles", []))
    unsupported = tuple(
        _unsupported_from_data(item) for item in data.get("unsupported_profiles", [])
    )
    return ScanProfileSet(
        schema_version=str(data.get("schema_version", "1.0")),
        profile_set_id=str(data.get("profile_set_id", "")),
        version=str(data.get("version", "")),
        display_name=str(data.get("display_name", "")),
        source_notes=tuple(str(item) for item in data.get("source_notes", [])),
        profiles=profiles,
        unsupported_profiles=unsupported,
    )


def _profile_from_data(item: dict[str, Any], defaults: dict[str, Any]) -> ScanProfile:
    def get(name: str, default: Any = None) -> Any:
        return item[name] if name in item else defaults.get(name, default)

    return ScanProfile(
        profile_id=str(item.get("profile_id", "")),
        display_name=str(item.get("display_name", "")),
        start_frequency_hz=int(get("start_frequency_hz")),
        end_frequency_hz=int(get("end_frequency_hz")),
        capture_bandwidth_hz=int(get("capture_bandwidth_hz")),
        sample_rate_sps=int(get("sample_rate_sps")),
        slice_step_hz=int(get("slice_step_hz")),
        slice_overlap_hz=int(get("slice_overlap_hz")),
        gain_db=float(get("gain_db")),
        antenna=str(get("antenna")),
        sample_count=int(get("sample_count")),
        priority=int(get("priority", 1000)),
        enabled=bool(get("enabled", False)),
        candidate_families=tuple(str(value) for value in get("candidate_families", [])),
        qualification_state=str(get("qualification_state", "experimental")),
        presentation_policy=str(get("presentation_policy", "technical_only")),
        regulatory_source_note=str(get("regulatory_source_note", "")),
        known_limitations=tuple(str(value) for value in get("known_limitations", [])),
    )


def _unsupported_from_data(item: dict[str, Any]) -> UnsupportedProfile:
    return UnsupportedProfile(
        profile_id=str(item.get("profile_id", "")),
        display_name=str(item.get("display_name", "")),
        start_frequency_hz=int(item.get("start_frequency_hz", 0)),
        end_frequency_hz=int(item.get("end_frequency_hz", 0)),
        reason=str(item.get("reason", "unsupported by configured hardware")),
        qualification_state=str(item.get("qualification_state", "unsupported_hardware")),
    )


def validate_profile_set(profile_set: ScanProfileSet) -> None:
    if not profile_set.profile_set_id:
        raise ScanProfileError("scan profile set must include profile_set_id")
    seen: set[str] = set()
    for profile in profile_set.profiles:
        _validate_profile(profile)
        if profile.profile_id in seen:
            raise ScanProfileError(f"duplicate scan profile ID: {profile.profile_id}")
        seen.add(profile.profile_id)
    for unsupported in profile_set.unsupported_profiles:
        if unsupported.profile_id in seen:
            raise ScanProfileError(f"duplicate scan profile ID: {unsupported.profile_id}")
        seen.add(unsupported.profile_id)
        if unsupported.end_frequency_hz <= unsupported.start_frequency_hz:
            raise ScanProfileError(
                f"unsupported profile {unsupported.profile_id} has invalid range"
            )


def _validate_profile(profile: ScanProfile) -> None:
    if not profile.profile_id:
        raise ScanProfileError("scan profile missing profile_id")
    if profile.qualification_state not in VALID_QUALIFICATION_STATES:
        raise ScanProfileError(f"invalid qualification state for {profile.profile_id}")
    if profile.presentation_policy not in VALID_PRESENTATION_POLICIES:
        raise ScanProfileError(f"invalid presentation policy for {profile.profile_id}")
    if (
        profile.presentation_policy == "presentation_eligible"
        and profile.qualification_state not in PRESENTATION_QUALIFICATION_STATES
    ):
        raise ScanProfileError(
            f"profile {profile.profile_id} cannot be presentation eligible while "
            f"{profile.qualification_state}"
        )
    if profile.end_frequency_hz <= profile.start_frequency_hz:
        raise ScanProfileError(f"profile {profile.profile_id} has invalid frequency range")
    if (
        profile.start_frequency_hz < B210_MIN_FREQUENCY_HZ
        or profile.end_frequency_hz > B210_MAX_FREQUENCY_HZ
    ):
        raise ScanProfileError(f"profile {profile.profile_id} is outside the B210 receive range")
    positive = {
        "capture_bandwidth_hz": profile.capture_bandwidth_hz,
        "sample_rate_sps": profile.sample_rate_sps,
        "slice_step_hz": profile.slice_step_hz,
        "slice_overlap_hz": profile.slice_overlap_hz,
        "sample_count": profile.sample_count,
    }
    for name, value in positive.items():
        if value <= 0:
            raise ScanProfileError(f"profile {profile.profile_id} has non-positive {name}")
    if profile.capture_bandwidth_hz > B210_MAX_ANALOG_BANDWIDTH_HZ:
        raise ScanProfileError(f"profile {profile.profile_id} bandwidth exceeds B210 limit")
    if profile.sample_rate_sps > B210_MAX_SAMPLE_RATE_SPS:
        raise ScanProfileError(f"profile {profile.profile_id} sample rate exceeds B210 limit")
    if profile.capture_bandwidth_hz > profile.sample_rate_sps:
        raise ScanProfileError(f"profile {profile.profile_id} bandwidth exceeds sample rate")
    expected_overlap = profile.capture_bandwidth_hz - profile.slice_step_hz
    if expected_overlap < 0:
        raise ScanProfileError(f"profile {profile.profile_id} slice step exceeds bandwidth")
    if expected_overlap != profile.slice_overlap_hz:
        raise ScanProfileError(
            f"profile {profile.profile_id} slice overlap does not match bandwidth-step"
        )
    if not profile.candidate_families:
        raise ScanProfileError(f"profile {profile.profile_id} must list candidate families")
    for start in _slice_starts(profile):
        end = start + profile.capture_bandwidth_hz
        if start < B210_MIN_FREQUENCY_HZ or end > B210_MAX_FREQUENCY_HZ:
            raise ScanProfileError(
                f"profile {profile.profile_id} requested capture slice is outside "
                "the B210 receive range"
            )


def build_scan_plan(
    profile_set: ScanProfileSet,
    *,
    enabled_profile_ids: str | list[str] | tuple[str, ...] | None = None,
    max_slices: int | None = None,
) -> ScanPlan:
    enabled_ids = parse_enabled_profile_ids(enabled_profile_ids)
    profile_by_id = profile_set.profile_by_id()
    warnings: list[str] = []
    slices: list[ScanSlice] = []
    for profile_id in enabled_ids:
        profile = profile_by_id.get(profile_id)
        if profile is None:
            raise ScanProfileError(f"enabled scan profile not found: {profile_id}")
        if not profile.can_be_scanned:
            raise ScanProfileError(
                f"scan profile {profile_id} cannot be scanned while {profile.qualification_state}"
            )
    selected_profiles = sorted(
        [profile_by_id[profile_id] for profile_id in enabled_ids if profile_id in profile_by_id],
        key=lambda item: (item.priority, item.start_frequency_hz, item.profile_id),
    )
    if not enabled_ids:
        warnings.append(
            "No RF_SCAN_ENABLED_PROFILE_IDS were provided; no captures will be planned."
        )
    full_slice_counts: dict[str, int] = {}
    for profile in selected_profiles:
        expanded = _expand_profile(profile)
        full_slice_counts[profile.profile_id] = len(expanded)
        slices.extend(expanded)
    full_slice_count = len(slices)
    if max_slices is not None and max_slices > 0:
        slices = slices[:max_slices]
    if len(slices) < full_slice_count:
        warnings.append(
            f"Planned {len(slices)} of {full_slice_count} slices; this plan does not provide "
            "complete profile coverage."
        )
    return ScanPlan(
        profile_set=profile_set,
        enabled_profile_ids=enabled_ids,
        slices=tuple(slices),
        warnings=tuple(warnings),
        full_slice_counts=tuple(full_slice_counts.items()),
    )


def _expand_profile(profile: ScanProfile) -> list[ScanSlice]:
    starts = _slice_starts(profile)
    slices: list[ScanSlice] = []
    for index, start in enumerate(starts):
        end = start + profile.capture_bandwidth_hz
        slices.append(
            ScanSlice(
                profile_id=profile.profile_id,
                slice_index=index,
                center_frequency_hz=start + profile.capture_bandwidth_hz // 2,
                capture_bandwidth_hz=profile.capture_bandwidth_hz,
                sample_rate_sps=profile.sample_rate_sps,
                requested_start_hz=start,
                requested_end_hz=end,
                coverage_start_hz=max(profile.start_frequency_hz, start),
                coverage_end_hz=min(profile.end_frequency_hz, end),
                gain_db=profile.gain_db,
                antenna=profile.antenna,
                sample_count=profile.sample_count,
                qualification_state=profile.qualification_state,
                presentation_policy=profile.presentation_policy,
                candidate_families=profile.candidate_families,
            )
        )
    return slices


def _slice_starts(profile: ScanProfile) -> list[int]:
    if profile.width_hz <= profile.capture_bandwidth_hz:
        center = (profile.start_frequency_hz + profile.end_frequency_hz) // 2
        return [center - profile.capture_bandwidth_hz // 2]
    starts = []
    current = profile.start_frequency_hz
    last_start = profile.end_frequency_hz - profile.capture_bandwidth_hz
    while current < last_start:
        starts.append(current)
        current += profile.slice_step_hz
    starts.append(last_start)
    return list(dict.fromkeys(starts))


def load_plan_from_settings(settings: Any, *, max_slices: int | None = None) -> ScanPlan:
    profile_set = load_scan_profile_set(
        Path(settings.scan_profile_config), expected_profile_set=settings.scan_profile_set
    )
    limit = max_slices if max_slices is not None else settings.scan_max_slices_per_cycle
    return build_scan_plan(
        profile_set,
        enabled_profile_ids=settings.scan_enabled_profile_ids,
        max_slices=limit,
    )
