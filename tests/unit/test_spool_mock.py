from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image

from rf_platform.common.config import Settings
from rf_platform.contracts.analysis import AnalysisRequest
from rf_platform.sensor_agent.adapters.base import CaptureRequest
from rf_platform.sensor_agent.adapters.simulated import SimulatedSensorAdapter
from rf_platform.sensor_agent.profiles import load_profile
from rf_platform.sensor_agent.spool import DurableSpool
from rf_platform.worker.rfgpt.mock import MockRFGPTAdapter


def test_spool_atomic_ready_recovery(tmp_path: Path) -> None:
    settings = Settings(
        sensor_id="sensor-1",
        sensor_token="token",
        spool_root=tmp_path / "spool",
        simulated_fixture_path=Path("tests/fixtures/spectrograms/simulated_ble_like.png"),
    )
    adapter = SimulatedSensorAdapter(settings, output_dir=tmp_path / "captures")
    profile = load_profile("campus_general")

    async def run() -> None:
        await adapter.open()
        bundle = await adapter.capture(CaptureRequest(profile=profile))
        spool = DurableSpool(settings.spool_root, settings.spool_max_bytes)
        item = spool.put(bundle)
        assert item.path.name == bundle.envelope.capture_id
        recovered = DurableSpool(settings.spool_root, settings.spool_max_bytes).pending_items()
        assert len(recovered) == 1
        assert recovered[0].envelope.capture_id == bundle.envelope.capture_id
        Image.open(recovered[0].artifact_path).verify()

    asyncio.run(run())


def test_mock_rfgpt_returns_schema_valid_result() -> None:
    settings = Settings(sensor_token="token", rfgpt_model_version="mock-v1")
    adapter = MockRFGPTAdapter(settings)

    async def run() -> None:
        result = await adapter.analyze(
            AnalysisRequest(job_id="job", capture_id="capture", artifact_keys=["a/b.png"])
        )
        assert result.parser_valid is True
        assert result.technologies[0].label == "bluetooth-like"
        assert result.technologies[0].model_score is None

    asyncio.run(run())
