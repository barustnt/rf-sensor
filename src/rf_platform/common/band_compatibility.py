from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rf_platform.common.scan_profiles import (
    B210_MAX_FREQUENCY_HZ,
    PRESENTATION_QUALIFICATION_STATES,
    ScanProfile,
    ScanProfileSet,
)

BAND_INCOMPATIBLE = "band_incompatible"
LEGACY_PRESENTATION_PROFILES = {"b210_2g4_demo"}
BLUETOOTH_FULL_RANGE_HZ = (2_400_000_000, 2_483_500_000)
WIFI_24_RANGE_HZ = (2_400_000_000, 2_483_500_000)
WIFI_5_RANGES_HZ = (
    (5_150_000_000, 5_350_000_000),
    (5_470_000_000, 5_875_000_000),
)
IMT_CANDIDATE_RANGES_HZ = (
    (694_000_000, 790_000_000),
    (791_000_000, 862_000_000),
    (880_000_000, 960_000_000),
    (1_427_000_000, 1_492_000_000),
    (1_710_000_000, 1_785_000_000),
    (1_805_000_000, 1_880_000_000),
    (1_920_000_000, 1_980_000_000),
    (2_110_000_000, 2_170_000_000),
    (1_980_000_000, 2_010_000_000),
    (2_170_000_000, 2_200_000_000),
    (2_300_000_000, 2_400_000_000),
    (2_496_000_000, 2_690_000_000),
    (3_300_000_000, 3_800_000_000),
)
ISM_SRD_RANGES_HZ = (
    (433_050_000, 434_790_000),
    (863_000_000, 870_000_000),
    (915_000_000, 921_000_000),
    (2_400_000_000, 2_483_500_000),
    (5_725_000_000, 5_875_000_000),
)
GENERIC_LABEL_PATTERNS = (
    re.compile(r"\b(cellular|wideband|ism|srd|rf activity|tone|chirp|carrier|signal)\b", re.I),
)


@dataclass(frozen=True)
class BandCompatibilityResult:
    compatible: bool
    severity: str
    reasons: tuple[str, ...] = ()

    @property
    def incompatible(self) -> bool:
        return not self.compatible


def ranges_overlap(
    first: tuple[int, int], second: tuple[int, int], *, tolerance_hz: int = 0
) -> bool:
    return first[0] <= second[1] + tolerance_hz and second[0] <= first[1] + tolerance_hz


def range_overlaps_any(
    frequency_range_hz: tuple[int, int],
    ranges: tuple[tuple[int, int], ...],
    *,
    tolerance_hz: int = 0,
) -> bool:
    return any(
        ranges_overlap(frequency_range_hz, item, tolerance_hz=tolerance_hz) for item in ranges
    )


def profile_presentation_eligible(
    profile: ScanProfile | None, profile_id: str | None = None
) -> bool:
    if profile_id in LEGACY_PRESENTATION_PROFILES:
        return True
    if profile is None:
        return False
    return (
        profile.presentation_policy == "presentation_eligible"
        and profile.qualification_state in PRESENTATION_QUALIFICATION_STATES
    )


def profile_matches_technology(profile: ScanProfile | None, technology: str) -> bool:
    if profile is None:
        return False
    families = set(profile.candidate_families)
    if technology == "bluetooth":
        return bool(families & {"bluetooth_classic", "ble"})
    if technology == "lte":
        return "lte_family" in families or "cellular" in families
    if technology == "5g":
        return "5g_nr_family" in families or "cellular" in families
    if technology == "wifi":
        return "wifi_wlan" in families
    return False


def scan_profile_for_capture(
    profile_set: ScanProfileSet | None, profile_id: str
) -> ScanProfile | None:
    if profile_set is None:
        return None
    return profile_set.profile_by_id().get(profile_id)


def check_findings_band_compatibility(
    *,
    technologies: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    frequency_range_hz: tuple[int, int] | None,
    profile_id: str | None = None,
) -> BandCompatibilityResult:
    if frequency_range_hz is None:
        return BandCompatibilityResult(True, "unknown", ("capture frequency range is unavailable",))
    reasons: list[str] = []
    for item in technologies + signals:
        label = str(item.get("label") or "")
        observation = str(item.get("observation") or "")
        reason = incompatible_reason(label, observation, frequency_range_hz, profile_id=profile_id)
        if reason:
            reasons.append(reason)
    if reasons:
        return BandCompatibilityResult(False, "reject", tuple(reasons))
    return BandCompatibilityResult(True, "accept", ())


def incompatible_reason(
    label: str,
    observation: str,
    frequency_range_hz: tuple[int, int],
    *,
    profile_id: str | None = None,
) -> str | None:
    text = " ".join((label, observation)).strip().lower().replace("_", "-")
    if not text:
        return None
    if any(pattern.search(text) for pattern in GENERIC_LABEL_PATTERNS) and not (
        _specific_protocol_terms(text)
    ):
        return None
    if (
        "mmwave" in text or "mm-wave" in text or "24 ghz" in text or "24.25" in text
    ) and frequency_range_hz[1] <= B210_MAX_FREQUENCY_HZ:
        return "5G mmWave cannot be claimed from a B210 sub-6 GHz capture"
    if ("bluetooth" in text or re.search(r"\bble\b", text)) and not ranges_overlap(
        frequency_range_hz, BLUETOOTH_FULL_RANGE_HZ, tolerance_hz=1_000_000
    ):
        return "Bluetooth/BLE is incompatible with the captured frequency range"
    if ("wi-fi" in text or "wifi" in text or "wlan" in text) and not (
        ranges_overlap(frequency_range_hz, WIFI_24_RANGE_HZ, tolerance_hz=1_000_000)
        or range_overlaps_any(frequency_range_hz, WIFI_5_RANGES_HZ, tolerance_hz=1_000_000)
    ):
        return "Wi-Fi/WLAN is incompatible with the captured frequency range"
    if "dvb-s" in text or "satellite" in text:
        if profile_id and "terrestrial" in profile_id.lower():
            return "Satellite claim is incompatible with the configured terrestrial profile"
        if frequency_range_hz[1] < 10_000_000_000:
            return (
                "DVB-S/S2 satellite claim is incompatible with the captured "
                "terrestrial-range frequency"
            )
    if re.search(r"\b(lte|4g|nr|5g)\b", text) and not range_overlaps_any(
        frequency_range_hz, IMT_CANDIDATE_RANGES_HZ, tolerance_hz=1_000_000
    ):
        return "LTE/5G-family claim is outside configured IMT candidate ranges"
    if ("ism" in text or "srd" in text) and not range_overlaps_any(
        frequency_range_hz, ISM_SRD_RANGES_HZ, tolerance_hz=1_000_000
    ):
        return "ISM/SRD claim is outside configured ISM/SRD candidate ranges"
    return None


def _specific_protocol_terms(text: str) -> bool:
    return bool(
        re.search(
            r"\b(bluetooth|ble|wi-?fi|wlan|lte|4g|5g|nr|dvb-s2?|satellite|mmwave|mm-wave)\b",
            text,
        )
    )
