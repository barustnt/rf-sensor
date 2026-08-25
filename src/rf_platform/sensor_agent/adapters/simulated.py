from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

from rf_platform import __version__
from rf_platform.common.config import Settings
from rf_platform.common.ids import new_id
from rf_platform.common.time import utc_now
from rf_platform.contracts.capture import ArtifactDescriptor, CaptureEnvelope, DSPMetrics
from rf_platform.contracts.sensor import RadioHealth, SensorCapabilities
from rf_platform.sensor_agent.adapters.base import CaptureBundle, CaptureRequest


class SimulatedSensorAdapter:
    def __init__(self, settings: Settings, output_dir: Path | None = None) -> None:
        self.settings = settings
        self.output_dir = output_dir or settings.spool_root / "simulated-captures"
        self.active_profile = settings.sensor_profile
        self.opened = False

    async def open(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.opened = True

    async def capabilities(self) -> SensorCapabilities:
        return SensorCapabilities(
            frequency_min_hz=None,
            frequency_max_hz=None,
            maximum_sample_rate_sps=None,
            rx_channels=1,
            supported_profiles=[
                "campus_general",
                "campus_2g4_coexistence",
                "exam_ble",
                "calibration",
                "device_experiment",
            ],
        )

    async def apply_profile(self, profile) -> None:  # type: ignore[no-untyped-def]
        self.active_profile = profile.profile_id

    async def capture(self, request: CaptureRequest) -> CaptureBundle:
        if not self.opened:
            await self.open()
        profile = request.profile
        started = utc_now()
        width = profile.preprocessing.output_width_px
        height = profile.preprocessing.output_height_px
        fixture_path = self.settings.simulated_fixture_path
        path = self.output_dir / f"{new_id()}-spectrogram.png"
        if fixture_path and fixture_path.exists():
            Image.open(fixture_path).convert("RGB").resize((width, height)).save(path)
            matrix = np.asarray(Image.open(path).convert("L"))
        else:
            rng = np.random.default_rng(
                abs(hash((profile.profile_id, started.isoformat()))) % (2**32)
            )
            matrix = rng.normal(35, 8, size=(height, width)).clip(0, 255)
            for _ in range(10):
                x = int(rng.integers(0, width))
                y0 = int(rng.integers(0, height - 30))
                matrix[y0 : y0 + 30, max(0, x - 2) : min(width, x + 3)] += rng.uniform(80, 150)
            matrix = matrix.clip(0, 255).astype(np.uint8)
            rgb = np.stack([matrix // 2, matrix, 255 - matrix // 3], axis=2)
            Image.fromarray(rgb, mode="RGB").save(path)
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        ended = utc_now()
        capture_id = new_id()
        envelope = CaptureEnvelope(
            capture_id=capture_id,
            sensor_id=self.settings.sensor_id,
            session_id=request.session_id,
            correlation_id=new_id(),
            profile_id=profile.profile_id,
            started_at_utc=started,
            ended_at_utc=ended,
            radio=profile.radio,
            preprocessing=profile.preprocessing.to_capture_settings(),
            dsp_metrics=DSPMetrics(
                noise_floor_db=float(np.percentile(matrix, 10)) - 140.0,
                peak_power_db=float(np.max(matrix)) - 120.0,
                occupied_bandwidth_hz=None,
            ),
            artifacts=[
                ArtifactDescriptor(
                    kind="spectrogram",
                    filename="spectrogram.png",
                    mime_type="image/png",
                    size_bytes=len(data),
                    sha256=sha,
                )
            ],
            created_at_utc=utc_now(),
        )
        return CaptureBundle(envelope=envelope, artifact_path=path)

    async def health(self) -> RadioHealth:
        return RadioHealth(connected=self.opened, last_error=None)

    async def close(self) -> None:
        self.opened = False


def software_version() -> str:
    return __version__
