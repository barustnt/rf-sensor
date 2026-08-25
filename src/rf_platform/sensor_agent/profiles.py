from __future__ import annotations

from pathlib import Path

import yaml

from rf_platform.contracts.capture import CaptureProfile
from rf_platform.contracts.sensor import SensorCapabilities


class ProfileError(ValueError):
    pass


def load_profile(profile_id: str, profile_dir: Path = Path("config/profiles")) -> CaptureProfile:
    path = profile_dir / f"{profile_id}.yml"
    if not path.exists():
        raise ProfileError(f"profile not found: {profile_id}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return CaptureProfile.model_validate(data)


def validate_profile_against_capabilities(
    profile: CaptureProfile,
    capabilities: SensorCapabilities,
) -> None:
    if profile.profile_id not in capabilities.supported_profiles:
        raise ProfileError(f"profile {profile.profile_id} not supported by sensor")
    if (
        capabilities.frequency_min_hz is not None
        and profile.radio.center_frequency_hz < capabilities.frequency_min_hz
    ):
        raise ProfileError("profile center frequency below sensor capability")
    if (
        capabilities.frequency_max_hz is not None
        and profile.radio.center_frequency_hz > capabilities.frequency_max_hz
    ):
        raise ProfileError("profile center frequency above sensor capability")
    if (
        capabilities.maximum_sample_rate_sps is not None
        and profile.radio.sample_rate_sps > capabilities.maximum_sample_rate_sps
    ):
        raise ProfileError("profile sample rate above sensor capability")
