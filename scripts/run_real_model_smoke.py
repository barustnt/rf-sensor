from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rf_platform.common.config import get_settings  # noqa: E402
from rf_platform.common.time import utc_now  # noqa: E402
from rf_platform.contracts.analysis import AnalysisRequest  # noqa: E402
from rf_platform.preprocessing.atheer_hann import preprocess_iq  # noqa: E402
from rf_platform.worker.rfgpt.local import LocalVLLMRFGPTAdapter  # noqa: E402


def deterministic_iq(sample_rate: int = 20_000_000) -> np.ndarray:
    rng = np.random.default_rng(20260825)
    n = 512 * 512 * 4
    t = np.arange(n, dtype=np.float64) / sample_rate
    noise = rng.normal(0, 2e-4, n) + 1j * rng.normal(0, 2e-4, n)
    carrier = 4e-3 * np.exp(2j * np.pi * -2_000_000 * t)
    pulsed = 2e-3 * (np.sin(2 * np.pi * 29 * t) > 0.4) * np.exp(2j * np.pi * 4_500_000 * t)
    return (noise + carrier + pulsed + 2e-3).astype(np.complex64)


async def main() -> int:
    settings = get_settings()
    if settings.rfgpt_adapter != "vllm":
        print("Set RF_RFGPT_ADAPTER=vllm for the real-model smoke test.", file=sys.stderr)
        return 2
    output_dir = ROOT / ".data" / "m3-smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "atheer-hann-v1-canonical.png"
    result = preprocess_iq(
        deterministic_iq(),
        sample_rate=20_000_000,
        center_frequency_hz=2_440_000_000,
        gain_db=30.0,
        band_prior="manual_smoke",
    )
    image_path.write_bytes(result.png_bytes)

    adapter = LocalVLLMRFGPTAdapter(settings)
    health = await adapter.health()
    print(json.dumps({"health": health.model_dump(mode="json")}, indent=2), flush=True)
    if not health.ready:
        return 3

    started = utc_now()
    analysis = await adapter.analyze(
        AnalysisRequest(
            job_id="manual-real-model-smoke",
            capture_id="manual-real-model-smoke",
            artifact_keys=["m3-smoke/atheer-hann-v1-canonical.png"],
            artifact_paths=[image_path],
            sensor_id="manual-smoke-sensor",
            capture_started_at_utc=started,
            center_frequency_hz=2_440_000_000,
            sample_rate_sps=20_000_000,
            bandwidth_hz=20_000_000,
            gain_db=30.0,
            profile_id="manual_smoke",
            preprocessing_version=result.pipeline_id,
            prompt_version="technology-detection-v1",
        )
    )
    (output_dir / "raw_response.json").write_text(analysis.raw_response, encoding="utf-8")
    (output_dir / "analysis_result.json").write_text(
        analysis.model_dump_json(indent=2), encoding="utf-8"
    )
    print(analysis.model_dump_json(indent=2), flush=True)
    print(f"latency_ms={analysis.latency_ms}", flush=True)
    print(f"raw_response_path={output_dir / 'raw_response.json'}", flush=True)
    print(f"analysis_result_path={output_dir / 'analysis_result.json'}", flush=True)
    if analysis.status != "succeeded" or not analysis.parser_valid:
        print(
            "RF-GPT response was transported but did not satisfy the JSON schema.", file=sys.stderr
        )
        return 4
    print("REAL MODEL SMOKE PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
