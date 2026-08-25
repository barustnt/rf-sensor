from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rf_platform.contracts.capture import CaptureEnvelope, CaptureProfile
from rf_platform.contracts.sensor import RadioHealth, SensorCapabilities


@dataclass(frozen=True)
class CaptureBundle:
    envelope: CaptureEnvelope
    artifact_path: Path


@dataclass(frozen=True)
class CaptureRequest:
    profile: CaptureProfile
    session_id: str | None = None


class SensorAdapter(Protocol):
    async def open(self) -> None: ...

    async def capabilities(self) -> SensorCapabilities: ...

    async def apply_profile(self, profile: CaptureProfile) -> None: ...

    async def capture(self, request: CaptureRequest) -> CaptureBundle: ...

    async def health(self) -> RadioHealth: ...

    async def close(self) -> None: ...
