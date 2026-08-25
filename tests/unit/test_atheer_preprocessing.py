from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import numpy as np
from PIL import Image

from rf_platform.preprocessing import atheer_hann


def _legacy_module() -> ModuleType:
    path = Path("references/legacy/atheer_capture.py")
    spec = importlib.util.spec_from_file_location("legacy_atheer_capture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _deterministic_iq(sample_rate: int = 20_000_000) -> np.ndarray:
    rng = np.random.default_rng(12345)
    n = atheer_hann.NFFT * atheer_hann.TIME_ROWS * 5 + 17
    t = np.arange(n, dtype=np.float64) / sample_rate
    noise = rng.normal(0, 2e-4, n) + 1j * rng.normal(0, 2e-4, n)
    tones = 6e-3 * np.exp(2j * np.pi * -3_500_000 * t) + 3e-3 * np.exp(2j * np.pi * 2_200_000 * t)
    burst_gate = (np.sin(2 * np.pi * 41 * t) > 0.35).astype(np.float64)
    burst = 2e-3 * burst_gate * np.exp(2j * np.pi * 6_100_000 * t)
    dc = np.full(n, 2e-3, dtype=np.complex128)
    return (noise + tones + burst + dc).astype(np.complex64)


def test_reference_sha256_matches_approved_baseline() -> None:
    actual = hashlib.sha256(Path("references/legacy/atheer_capture.py").read_bytes()).hexdigest()
    assert actual == atheer_hann.REFERENCE_SHA256


def test_atheer_hann_numeric_measurements_and_pixels_match_reference(tmp_path: Path) -> None:
    legacy = _legacy_module()
    iq = _deterministic_iq()

    legacy_power = legacy.compute_spectrogram(iq, legacy.NFFT, legacy.TIME_ROWS)
    production_power = atheer_hann.compute_spectrogram(iq)
    np.testing.assert_array_equal(production_power, legacy_power)

    legacy_notched = legacy.notch_dc(legacy_power, legacy.DC_NOTCH_BINS)
    production_notched = atheer_hann.notch_dc(production_power)
    np.testing.assert_array_equal(production_notched, legacy_notched)

    legacy_measurements = legacy.measure(legacy_notched, legacy.SAMPLE_RATE)
    production = atheer_hann.preprocess_iq(iq, sample_rate=legacy.SAMPLE_RATE)
    assert production.measurements == legacy_measurements
    np.testing.assert_array_equal(production.power, legacy_notched)

    legacy_path = tmp_path / "legacy.png"
    production_path = tmp_path / "production.png"
    legacy.render(legacy_notched, str(legacy_path))
    atheer_hann.render_png(production.power, production_path)

    legacy_pixels = np.asarray(Image.open(legacy_path).convert("RGB"))
    production_pixels = np.asarray(Image.open(production_path).convert("RGB"))
    Path(tmp_path / "from_bytes.png").write_bytes(production.png_bytes)
    bytes_pixels = np.asarray(Image.open(tmp_path / "from_bytes.png").convert("RGB"))

    assert legacy_pixels.shape == (512, 512, 3)
    assert production_pixels.shape == (512, 512, 3)
    np.testing.assert_array_equal(production_pixels, legacy_pixels)
    np.testing.assert_array_equal(bytes_pixels, legacy_pixels)


def test_metadata_records_pipeline_orientation_and_edge_guard_provenance() -> None:
    result = atheer_hann.preprocess_iq(_deterministic_iq(), sample_rate=20_000_000)
    metadata = result.metadata
    assert metadata["pipeline_id"] == "atheer-hann-v1"
    assert metadata["window_function"] == "np.hanning"
    assert metadata["fft_shift"] is True
    assert metadata["dc_notch_bins_each_side"] == 3
    assert metadata["edge_guard_usage"] == "measurements_only_not_render_crop"
    assert metadata["time_axis_direction"] == "left-to-right"
    assert metadata["frequency_axis_direction"] == "low-frequency-at-bottom"
    assert metadata["transpose_then_vertical_flip"] is True
