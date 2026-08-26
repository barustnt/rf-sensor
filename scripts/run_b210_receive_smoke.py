from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT_SAMPLE_COUNT = 1_048_576
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rf_platform.common.config import get_settings  # noqa: E402
from rf_platform.common.logging import configure_logging  # noqa: E402
from rf_platform.sensor_agent.adapters.b210 import B210SensorAdapter  # noqa: E402
from rf_platform.sensor_agent.adapters.base import CaptureRequest  # noqa: E402
from rf_platform.sensor_agent.profiles import load_profile  # noqa: E402


async def main() -> int:
    configure_logging()
    settings = get_settings()
    if settings.sensor_adapter != "b210":
        print("Set RF_SENSOR_ADAPTER=b210 for the receive-only B210 smoke test.", file=sys.stderr)
        return 2
    if settings.b210_sample_count != ROOT_SAMPLE_COUNT:
        print(
            f"Set RF_B210_SAMPLE_COUNT={ROOT_SAMPLE_COUNT}; got {settings.b210_sample_count!r}.",
            file=sys.stderr,
        )
        return 2
    profile = load_profile(settings.sensor_profile)
    selected = {
        "sensor_id": settings.sensor_id,
        "profile_id": profile.profile_id,
        "device_args": settings.b210_device_args,
        "expected_serial": settings.b210_serial,
        "rx_channel": settings.b210_rx_channel,
        "antenna": settings.b210_antenna or profile.radio.antenna,
        "center_frequency_hz": settings.b210_center_frequency_hz
        or profile.radio.center_frequency_hz,
        "sample_rate_sps": settings.b210_sample_rate_sps or profile.radio.sample_rate_sps,
        "bandwidth_hz": settings.b210_bandwidth_hz or profile.radio.bandwidth_hz,
        "gain_db": settings.b210_gain_db
        if settings.b210_gain_db is not None
        else profile.radio.gain_db,
        "sample_count": settings.b210_sample_count,
        "cpu_sample_format": settings.b210_cpu_format,
        "wire_sample_format": settings.b210_wire_format,
        "receive_timeout_seconds": settings.b210_receive_timeout_seconds,
    }
    print("receive_only_b210_smoke_settings", json.dumps(selected, sort_keys=True), flush=True)
    adapter = B210SensorAdapter(settings)
    started = time.perf_counter()
    try:
        await adapter.open()
        print("streaming_started", flush=True)
        bundle = await adapter.capture(CaptureRequest(profile=profile))
    except Exception as exc:
        print(
            f"receive_only_b210_smoke_failed error={exc.__class__.__name__} message={exc} "
            f"elapsed_seconds={time.perf_counter() - started:.2f}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    finally:
        await adapter.close()
    artifact = bundle.artifact_path
    sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
    print("streaming_completed", flush=True)
    print(
        "actual_uhd_values", json.dumps(adapter.last_capture_metadata, sort_keys=True), flush=True
    )
    print(f"capture_id={bundle.envelope.capture_id}", flush=True)
    print(f"artifact_path={artifact}", flush=True)
    print(f"artifact_sha256={sha256}", flush=True)
    print(f"elapsed_seconds={time.perf_counter() - started:.2f}", flush=True)
    print("RECEIVE_ONLY_B210_SMOKE_PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
