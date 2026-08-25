from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
from PIL import Image

PIPELINE_ID = "atheer-hann-v1"
REFERENCE_SHA256 = "2b44a61b64e0aaceb64b538b8b7b5b41bdc1f5c6aff41f9513a3ca49c094312e"

NFFT = 512
TIME_ROWS = 512
IMG_H = 512
IMG_W = 512
VMIN_DBFS = -110.0
VMAX_DBFS = -20.0

DETECT_THRESH_DB = 6.0
NOISE_PCTL = 20
DC_NOTCH_BINS = 3
EDGE_GUARD_FRAC = 0.06
CLUSTER_MERGE_BINS = 4
MIN_CLUSTER_BINS = 2

OCC_NARROW_MHZ = 5.0
OCC_WIDE_MHZ = 15.0
SNR_LOW_DB = 10.0
SNR_HIGH_DB = 20.0
DUTY_CONTINUOUS = 0.85
DUTY_BURST = 0.15


@dataclass(frozen=True)
class AtheerPreprocessingResult:
    pipeline_id: str
    power: np.ndarray
    measurements: dict[str, Any]
    metadata: dict[str, Any]
    png_bytes: bytes


def to_db(power: np.ndarray) -> np.ndarray:
    return 10.0 * np.log10(power + 1e-20)


def compute_spectrogram(
    samples: np.ndarray,
    nfft: int = NFFT,
    time_rows: int = TIME_ROWS,
) -> np.ndarray:
    """Linear-power Hann spectrogram with Atheer reference grouping semantics."""
    frames = len(samples) // nfft
    if frames < time_rows:
        raise ValueError(
            f"need >= {nfft * time_rows} samples for {time_rows} rows at NFFT={nfft}, "
            f"got {len(samples)}"
        )
    win = np.hanning(nfft)
    x = samples[: frames * nfft].reshape(frames, nfft) * win
    spectrum = np.fft.fftshift(np.fft.fft(x, axis=1), axes=1) / nfft
    power = np.abs(spectrum) ** 2
    group_size = frames // time_rows
    return power[: group_size * time_rows].reshape(time_rows, group_size, nfft).mean(axis=1)


def notch_dc(power: np.ndarray, half_width: int = DC_NOTCH_BINS) -> np.ndarray:
    """Interpolate over center DC/LO bins exactly as the Atheer reference does."""
    if half_width <= 0:
        return power
    notched = power.copy()
    center = notched.shape[1] // 2
    lo, hi = center - half_width, center + half_width
    if lo - 1 < 0 or hi + 1 >= notched.shape[1]:
        return notched
    left = notched[:, lo - 1][:, None]
    right = notched[:, hi + 1][:, None]
    width = hi - lo + 1
    ramp = np.linspace(0.0, 1.0, width + 2)[1:-1][None, :]
    notched[:, lo : hi + 1] = left * (1 - ramp) + right * ramp
    return notched


def measure(power: np.ndarray, sample_rate: int) -> dict[str, Any]:
    """Deterministic Atheer measurement calculations.

    The edge guard is applied only to deterministic measurements. The rendered PNG
    uses the full notched matrix and is never silently cropped.
    """
    n_bins = power.shape[1]
    guard = int(n_bins * EDGE_GUARD_FRAC)
    band = power[:, guard : n_bins - guard]
    bin_hz = sample_rate / n_bins
    analysis_bw = band.shape[1] * bin_hz

    band_db = to_db(band)
    noise_floor = float(np.percentile(band_db, NOISE_PCTL))
    peak = float(band_db.max())

    prof_db = to_db(band.mean(axis=0))
    occupied = prof_db > (noise_floor + DETECT_THRESH_DB)

    clusters = _clusters(occupied)
    n_occ = int(occupied.sum())
    signal_present = bool(clusters) and n_occ >= MIN_CLUSTER_BINS

    snr = float(np.percentile(prof_db[occupied], 90) - noise_floor) if signal_present else 0.0

    occ_bw = n_occ * bin_hz
    occ_bw_mhz = occ_bw / 1e6

    if signal_present:
        row_db = to_db(band[:, occupied].sum(axis=1))
        noise_ref_db = to_db(np.array([10 ** (noise_floor / 10) * n_occ]))[0]
        active = row_db > (noise_ref_db + DETECT_THRESH_DB)
        duty = float(active.mean())
        n_bursts = int(np.count_nonzero(np.diff(active.astype(int)) == 1))
        if duty >= DUTY_CONTINUOUS:
            temporal = "continuous"
        elif duty >= DUTY_BURST:
            temporal = "pulsed"
        else:
            temporal = "burst"
    else:
        duty, n_bursts, temporal = 0.0, 0, "none"

    if not signal_present:
        occupancy, snr_class, isolation = "none", "none", "none"
    else:
        occupancy = (
            "narrow"
            if occ_bw_mhz < OCC_NARROW_MHZ
            else "wide"
            if occ_bw_mhz > OCC_WIDE_MHZ
            else "medium"
        )
        snr_class = "low" if snr < SNR_LOW_DB else "high" if snr > SNR_HIGH_DB else "medium"
        isolation = "isolated" if len(clusters) == 1 else "overlapping"

    cluster_meta = []
    for start, stop in clusters:
        nb = stop - start + 1
        rows_db = to_db(band[:, start : stop + 1].sum(axis=1))
        ref_db = to_db(np.array([10 ** (noise_floor / 10) * nb]))[0]
        active = rows_db > (ref_db + DETECT_THRESH_DB)
        duty_cycle = float(active.mean())
        cluster_meta.append(
            {
                "start_bin": int(start),
                "stop_bin": int(stop),
                "bw_hz": float(nb * bin_hz),
                "peak_dbfs": round(float(prof_db[start : stop + 1].max()), 2),
                "snr_db": round(float(prof_db[start : stop + 1].max() - noise_floor), 2),
                "duty_cycle": round(duty_cycle, 4),
                "n_bursts": int(np.count_nonzero(np.diff(active.astype(int)) == 1)),
                "temporal_class": (
                    "continuous"
                    if duty_cycle >= DUTY_CONTINUOUS
                    else "pulsed"
                    if duty_cycle >= DUTY_BURST
                    else "burst"
                ),
            }
        )

    return {
        "analysis_bw_hz": float(analysis_bw),
        "signal_present": int(signal_present),
        "noise_floor_dbfs": round(noise_floor, 2),
        "peak_dbfs": round(peak, 2),
        "snr_est_db": round(snr, 2),
        "snr_class": snr_class,
        "occ_bw_hz": round(occ_bw, 1),
        "occ_bw_mhz": round(occ_bw_mhz, 3),
        "occupancy_class": occupancy,
        "duty_cycle": round(duty, 4),
        "n_bursts": n_bursts,
        "temporal_class": temporal,
        "n_clusters": len(clusters),
        "isolation": isolation,
        "clipping_suspected": int(peak > -1.0),
        "clusters_json": json.dumps(cluster_meta),
    }


def render_png(power: np.ndarray, path: str | Path | BytesIO) -> None:
    """Render a bare 512x512 RGB Viridis PNG, preserving Atheer orientation."""
    db = to_db(power).T
    db = np.flipud(db)
    norm = np.clip((db - VMIN_DBFS) / (VMAX_DBFS - VMIN_DBFS), 0.0, 1.0)
    rgb = (matplotlib.colormaps["viridis"](norm)[..., :3] * 255).astype(np.uint8)
    if rgb.shape != (IMG_H, IMG_W, 3):
        raise ValueError(f"expected {(IMG_H, IMG_W, 3)} RGB image, got {rgb.shape}")
    Image.fromarray(rgb, mode="RGB").save(path, format="PNG")


def render_png_bytes(power: np.ndarray) -> bytes:
    buffer = BytesIO()
    render_png(power, buffer)
    return buffer.getvalue()


def preprocess_iq(
    samples: np.ndarray,
    sample_rate: int,
    *,
    center_frequency_hz: int | None = None,
    gain_db: float | None = None,
    band_prior: str | None = None,
) -> AtheerPreprocessingResult:
    samples = np.asarray(samples, dtype=np.complex64)
    power = notch_dc(compute_spectrogram(samples, NFFT, TIME_ROWS), DC_NOTCH_BINS)
    measurements = measure(power, sample_rate)
    metadata = {
        "pipeline_id": PIPELINE_ID,
        "reference": "references/legacy/atheer_capture.py",
        "reference_sha256": REFERENCE_SHA256,
        "nfft": NFFT,
        "hop": NFFT,
        "time_rows": TIME_ROWS,
        "window": "hann",
        "window_function": "np.hanning",
        "fft_shift": True,
        "fft_divisor": NFFT,
        "linear_power": "abs(fft) ** 2",
        "frame_grouping": "floor(frames / time_rows), mean over group",
        "dc_notch_bins_each_side": DC_NOTCH_BINS,
        "edge_guard_frac": EDGE_GUARD_FRAC,
        "edge_guard_usage": "measurements_only_not_render_crop",
        "db_min": VMIN_DBFS,
        "db_max": VMAX_DBFS,
        "color_map": "viridis",
        "image_width_px": IMG_W,
        "image_height_px": IMG_H,
        "time_axis_direction": "left-to-right",
        "frequency_axis_direction": "low-frequency-at-bottom",
        "transpose_then_vertical_flip": True,
        "sample_rate_sps": sample_rate,
        "center_frequency_hz": center_frequency_hz,
        "gain_db": gain_db,
        "band_prior": band_prior,
    }
    return AtheerPreprocessingResult(
        pipeline_id=PIPELINE_ID,
        power=power,
        measurements=measurements,
        metadata=metadata,
        png_bytes=render_png_bytes(power),
    )


def _clusters(occupied: np.ndarray) -> list[tuple[int, int]]:
    idx = np.flatnonzero(occupied)
    if idx.size == 0:
        return []
    out: list[tuple[int, int]] = []
    start, prev = idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > CLUSTER_MERGE_BINS:
            out.append((int(start), int(prev)))
            start = i
        prev = i
    out.append((int(start), int(prev)))
    return [(a, b) for a, b in out if (b - a + 1) >= MIN_CLUSTER_BINS]
