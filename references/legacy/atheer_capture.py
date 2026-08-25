#!/usr/bin/env python3
"""
atheer_capture.py — over-the-air capture with measurement-grounded metadata.

Purpose
-------
Produce a real-capture dataset whose *directory layout and metadata table* mirror
the synthetic RF-GPT corpus (512x512 spectrogram + one metadata row per sample),
but whose labels are honest about their provenance.

The synthetic metadata (test_metadata.csv) carries generator ground truth:
per-user PRB sets, target SNR, DM-RS positions, SRS config, injected impairments.
None of that is recoverable from an over-the-air capture. What *is* recoverable
is measured by deterministic DSP here, and every column is tagged accordingly:

  MEASURED  - computed from the IQ/spectrogram (noise floor, occupied BW, SNR,
              duty cycle, cluster count). These are usable as evaluation ground
              truth for PAES attributes a1-a4.
  CONFIG    - what the radio was set to (fc, sample rate, gain, NFFT).
  PRIOR     - band-plan assumption, NOT an observation. `band_prior` says
              "2437 MHz is the Wi-Fi band", not "Wi-Fi is present". Combine with
              `signal_present` before ever treating it as a label (PAES a5).

Rendering conventions match the RF-GPT training pipeline: 512-point FFT, hop =
NFFT (no overlap), magnitude -> dB, clipped to a fixed dynamic range, resized to
512x512, viridis colormap, time on the horizontal axis and frequency on the
vertical axis, and **no axes, title, or colorbar** — anything drawn as text
inside the image is readable by the vision encoder and becomes a prompt-leakage
channel (cf. the PLR metric).

Two artifacts the synthetic pipeline never produced are removed before
measurement and before rendering:
  * the receiver DC/LO spike at the centre bin (looks like a narrowband CW
    emitter to a model that has never seen one);
  * the outer `EDGE_GUARD_FRAC` of the span, where the analog filter rolls off.

Usage
-----
    python3 atheer_capture.py --root ./dataset --target-gb 22
    python3 atheer_capture.py --root ./dataset --simulate --max-captures 12
    python3 atheer_capture.py --root ./dataset --iq subset --save-db-matrix

Splits are assigned per *session* (one full sweep = one session), not per
capture, so that near-duplicate consecutive captures cannot leak across splits.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

import matplotlib
import numpy as np
from PIL import Image

# ============================================================================
# CONFIG
# ============================================================================

# Rendering / analysis geometry (matches the synthetic corpus: 512x512, hop=nfft)
NFFT = 512
TIME_ROWS = 512
IMG_H = 512
IMG_W = 512

# Absolute dBFS window for the colormap. Fixed (not per-image auto-scaled) so
# brightness is comparable across captures — required for any SNR-class label to
# mean the same thing twice. Recorded per row so images stay reproducible.
VMIN_DBFS = -110.0
VMAX_DBFS = -20.0

# Measurement thresholds
DETECT_THRESH_DB = 6.0      # bin counts as occupied at noise_floor + this
NOISE_PCTL = 20             # percentile of the dB matrix taken as noise floor
DC_NOTCH_BINS = 3           # +/- bins around centre removed (LO leakage)
EDGE_GUARD_FRAC = 0.06      # fraction of span discarded at each edge
CLUSTER_MERGE_BINS = 4      # gaps smaller than this do not split a cluster
MIN_CLUSTER_BINS = 2        # clusters narrower than this are noise spikes

# PAES-aligned class boundaries
OCC_NARROW_MHZ = 5.0        # < 5 MHz  -> narrow
OCC_WIDE_MHZ = 15.0         # > 15 MHz -> wide
SNR_LOW_DB = 10.0           # < 10 dB  -> low
SNR_HIGH_DB = 20.0          # > 20 dB  -> high
DUTY_CONTINUOUS = 0.85
DUTY_BURST = 0.15

# Receiver
URI = "usb:3.3.5"
SAMPLE_RATE = int(20e6)
RX_BUFFER = int(2e6)        # 100 ms at 20 MS/s; needs >= NFFT*TIME_ROWS samples
MANUAL_GAIN_DB = 40         # AGC OFF. 'fast_attack' makes absolute level, and
                            # therefore SNR class, meaningless across captures.
RETUNE_SETTLE_S = 0.15
DISCARD_BUFFERS_AFTER_RETUNE = 1   # first buffer after a retune is stale

# Band plan. `prior` is a band-plan assumption, never an observation.
# 2.4 GHz is swept in 20 MHz steps because a 20 MHz span already covers ~10
# Bluetooth channels — stepping every 2 MHz was 40x redundant.
BANDS = [
    {"prior": "wifi_24",   "fc": [2412e6, 2432e6, 2452e6, 2472e6]},
    {"prior": "ism_24",    "fc": [2402e6, 2442e6, 2480e6]},
    {"prior": "lte",       "fc": [806e6, 1842e6, 2140e6, 2650e6]},
    {"prior": "nr_n78",    "fc": [3350e6, 3450e6, 3550e6, 3650e6, 3750e6]},
    # Quiet reference: characterises the receiver's own noise floor each session.
    # Keeps the SNR gate honest when the environment itself is silent.
    {"prior": "noise_ref", "fc": [1300e6]},
]

METADATA_FILE = "metadata.csv"

FIELDNAMES = [
    # identity
    "capture_uid", "session_id", "split", "file", "rel_path", "timestamp_utc",
    # PRIOR (band-plan assumption, not a measurement)
    "band_prior", "band_prior_source",
    # CONFIG
    "fc_hz", "sample_rate", "nfft", "hop", "time_rows", "img_h", "img_w",
    "rx_gain_db", "agc_mode", "vmin_dbfs", "vmax_dbfs",
    "dc_notch_bins", "edge_guard_frac", "analysis_bw_hz",
    # MEASURED
    "signal_present", "noise_floor_dbfs", "peak_dbfs", "snr_est_db", "snr_class",
    "occ_bw_hz", "occ_bw_mhz", "occupancy_class",
    "duty_cycle", "n_bursts", "temporal_class",
    "n_clusters", "isolation", "clipping_suspected",
    # structured
    "clusters_json", "rx_json", "iq_file", "db_matrix_file",
]


# ============================================================================
# DSP
# ============================================================================

def to_db(power: np.ndarray) -> np.ndarray:
    return 10.0 * np.log10(power + 1e-20)


def compute_spectrogram(samples: np.ndarray, nfft: int, time_rows: int) -> np.ndarray:
    """Linear-power spectrogram, shape (time_rows, nfft), hop = nfft."""
    frames = len(samples) // nfft
    if frames < time_rows:
        raise ValueError(
            f"need >= {nfft * time_rows} samples for {time_rows} rows at NFFT={nfft}, "
            f"got {len(samples)}"
        )
    win = np.hanning(nfft)
    x = samples[: frames * nfft].reshape(frames, nfft) * win
    X = np.fft.fftshift(np.fft.fft(x, axis=1), axes=1) / nfft
    p = np.abs(X) ** 2
    g = frames // time_rows
    p = p[: g * time_rows].reshape(time_rows, g, nfft).mean(axis=1)
    return p


def notch_dc(power: np.ndarray, half_width: int) -> np.ndarray:
    """Interpolate across the centre bins to kill the LO/DC spike."""
    if half_width <= 0:
        return power
    p = power.copy()
    c = p.shape[1] // 2
    lo, hi = c - half_width, c + half_width
    if lo - 1 < 0 or hi + 1 >= p.shape[1]:
        return p
    left = p[:, lo - 1][:, None]
    right = p[:, hi + 1][:, None]
    n = hi - lo + 1
    ramp = np.linspace(0.0, 1.0, n + 2)[1:-1][None, :]
    p[:, lo : hi + 1] = left * (1 - ramp) + right * ramp
    return p


def measure(power: np.ndarray, sample_rate: int) -> dict:
    """Deterministic PAES-style attributes from the spectrogram. No model involved."""
    n_bins = power.shape[1]
    guard = int(n_bins * EDGE_GUARD_FRAC)
    band = power[:, guard : n_bins - guard]
    bin_hz = sample_rate / n_bins
    analysis_bw = band.shape[1] * bin_hz

    band_db = to_db(band)
    noise_floor = float(np.percentile(band_db, NOISE_PCTL))
    peak = float(band_db.max())

    # Frequency profile: mean power over time, per bin.
    prof_db = to_db(band.mean(axis=0))
    occupied = prof_db > (noise_floor + DETECT_THRESH_DB)

    clusters = _clusters(occupied)
    n_occ = int(occupied.sum())
    signal_present = bool(clusters) and n_occ >= MIN_CLUSTER_BINS

    if signal_present:
        snr = float(np.percentile(prof_db[occupied], 90) - noise_floor)
    else:
        snr = 0.0

    occ_bw = n_occ * bin_hz
    occ_bw_mhz = occ_bw / 1e6

    # Temporal behaviour: in-band power per time row vs. a noise-only reference
    # over the same number of bins.
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
            "narrow" if occ_bw_mhz < OCC_NARROW_MHZ
            else "wide" if occ_bw_mhz > OCC_WIDE_MHZ
            else "medium"
        )
        snr_class = (
            "low" if snr < SNR_LOW_DB
            else "high" if snr > SNR_HIGH_DB
            else "medium"
        )
        isolation = "isolated" if len(clusters) == 1 else "overlapping"

    # Per-emitter stats. The scene-level temporal class above is dominated by
    # whichever cluster is widest; a faint burst next to a continuous carrier is
    # only visible here.
    cluster_meta = []
    for a, b in clusters:
        nb = b - a + 1
        rows_db = to_db(band[:, a : b + 1].sum(axis=1))
        ref_db = to_db(np.array([10 ** (noise_floor / 10) * nb]))[0]
        act = rows_db > (ref_db + DETECT_THRESH_DB)
        d = float(act.mean())
        cluster_meta.append({
            "start_bin": int(a),
            "stop_bin": int(b),
            "bw_hz": float(nb * bin_hz),
            "peak_dbfs": round(float(prof_db[a : b + 1].max()), 2),
            "snr_db": round(float(prof_db[a : b + 1].max() - noise_floor), 2),
            "duty_cycle": round(d, 4),
            "n_bursts": int(np.count_nonzero(np.diff(act.astype(int)) == 1)),
            "temporal_class": ("continuous" if d >= DUTY_CONTINUOUS
                               else "pulsed" if d >= DUTY_BURST else "burst"),
        })

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


def _clusters(occupied: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous runs of True, merging gaps < CLUSTER_MERGE_BINS."""
    idx = np.flatnonzero(occupied)
    if idx.size == 0:
        return []
    out, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > CLUSTER_MERGE_BINS:
            out.append((start, prev))
            start = i
        prev = i
    out.append((start, prev))
    return [(a, b) for a, b in out if (b - a + 1) >= MIN_CLUSTER_BINS]


def render(power: np.ndarray, path: str) -> None:
    """Bare 512x512 RGB viridis PNG. Time horizontal, frequency vertical, no text."""
    db = to_db(power).T                       # (freq, time)
    db = np.flipud(db)                        # low frequency at the bottom
    norm = np.clip((db - VMIN_DBFS) / (VMAX_DBFS - VMIN_DBFS), 0.0, 1.0)
    rgb = (matplotlib.colormaps["viridis"](norm)[..., :3] * 255).astype(np.uint8)
    Image.fromarray(rgb, mode="RGB").save(path, format="PNG")


# ============================================================================
# Radio
# ============================================================================

class PlutoSource:
    def __init__(self, uri: str, sample_rate: int, buffer_size: int, gain_db: int):
        import adi  # imported lazily so --simulate runs without pyadi-iio
        self.sdr = adi.Pluto(uri)
        self.sdr.sample_rate = int(sample_rate)
        self.sdr.rx_rf_bandwidth = int(sample_rate)
        self.sdr.rx_buffer_size = int(buffer_size)
        self.sdr.gain_control_mode_chan0 = "manual"
        self.sdr.rx_hardwaregain_chan0 = int(gain_db)
        self.agc_mode = "manual"

    def tune(self, fc: float) -> None:
        self.sdr.rx_lo = int(fc)
        time.sleep(RETUNE_SETTLE_S)
        for _ in range(DISCARD_BUFFERS_AFTER_RETUNE):
            self.sdr.rx()

    def read(self) -> np.ndarray:
        return self.sdr.rx() / 2048.0          # 12-bit full scale -> +/-1.0

    @property
    def gain_db(self) -> float:
        return float(self.sdr.rx_hardwaregain_chan0)

    def close(self) -> None:
        del self.sdr


class SimulatedSource:
    """Synthetic IQ for pipeline testing. Not for dataset generation."""

    def __init__(self, sample_rate: int, buffer_size: int, gain_db: int):
        self.sample_rate = sample_rate
        self.buffer_size = buffer_size
        self._gain = gain_db
        self.agc_mode = "simulated"
        self.rng = np.random.default_rng(0)
        self.fc = 0.0

    def tune(self, fc: float) -> None:
        self.fc = fc

    def read(self) -> np.ndarray:
        n = self.buffer_size
        t = np.arange(n) / self.sample_rate
        x = (self.rng.normal(0, 3e-4, n) + 1j * self.rng.normal(0, 3e-4, n))
        for off, bw, amp, duty in ((-4e6, 3e6, 6e-3, 1.0), (5e6, 8e5, 2e-3, 0.25)):
            sig = self._band_limited(n, bw, amp) * np.exp(2j * np.pi * off * t)
            if duty < 1.0:
                gate = (np.sin(2 * np.pi * 37 * t) > (1 - 2 * duty)).astype(float)
                sig = sig * gate
            x += sig
        x += 2e-3  # DC/LO leakage, so the notch has something to remove
        return x

    def _band_limited(self, n: int, bw: float, amp: float) -> np.ndarray:
        """White noise masked in the frequency domain to `bw` Hz around DC."""
        w = self.rng.normal(0, 1, n) + 1j * self.rng.normal(0, 1, n)
        W = np.fft.fft(w)
        f = np.fft.fftfreq(n, d=1.0 / self.sample_rate)
        W[np.abs(f) > bw / 2] = 0
        y = np.fft.ifft(W)
        return amp * y / (np.abs(y).std() + 1e-20)

    @property
    def gain_db(self) -> float:
        return float(self._gain)

    def close(self) -> None:
        pass


# ============================================================================
# Main
# ============================================================================

def split_for_session(session_id: int) -> str:
    m = session_id % 10
    return "val" if m == 8 else "test" if m == 9 else "train"


def dir_size(path: str) -> int:
    total = 0
    for dirpath, _, files in os.walk(path):
        for f in files:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                total += os.path.getsize(fp)
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="./dataset")
    ap.add_argument("--target-gb", type=float, default=22.0)
    ap.add_argument("--max-captures", type=int, default=0, help="0 = unlimited")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--uri", default=URI)
    ap.add_argument("--gain", type=int, default=MANUAL_GAIN_DB)
    ap.add_argument("--iq", choices=["none", "subset", "full"], default="none",
                    help="subset = first NFFT*TIME_ROWS samples as complex64")
    ap.add_argument("--save-db-matrix", action="store_true",
                    help="also store the 512x512 dB matrix as float16 .npy")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    os.makedirs(root, exist_ok=True)
    for b in BANDS:
        os.makedirs(os.path.join(root, b["prior"]), exist_ok=True)

    meta_path = os.path.join(root, METADATA_FILE)
    new_file = not os.path.exists(meta_path)
    meta_fh = open(meta_path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(meta_fh, fieldnames=FIELDNAMES)
    if new_file:
        writer.writeheader()
        meta_fh.flush()

    if args.simulate:
        src = SimulatedSource(SAMPLE_RATE, RX_BUFFER, args.gain)
    else:
        src = PlutoSource(args.uri, SAMPLE_RATE, RX_BUFFER, args.gain)

    target_bytes = int(args.target_gb * 1024 ** 3)
    total_bytes = dir_size(root)          # walked ONCE, then tracked incrementally
    session = 0
    n_captures = 0

    print(f"root         : {root}")
    print(f"target       : {args.target_gb:.2f} GB   (current {total_bytes/1024**3:.2f} GB)")
    print(f"source       : {'simulated' if args.simulate else args.uri}")
    print(f"gain / AGC   : {src.gain_db:g} dB / {src.agc_mode}\n")

    try:
        while total_bytes < target_bytes:
            split = split_for_session(session)
            print(f"--- session {session}  [{split}] ---")
            for band in BANDS:
                for fc in band["fc"]:
                    src.tune(fc)
                    samples = src.read()

                    p = compute_spectrogram(samples, NFFT, TIME_ROWS)
                    p = notch_dc(p, DC_NOTCH_BINS)
                    m = measure(p, SAMPLE_RATE)

                    stamp = datetime.now(timezone.utc)
                    uid = hashlib.sha1(
                        f"{band['prior']}{fc}{session}{stamp.isoformat()}".encode()
                    ).hexdigest()[:12]
                    stem = f"{band['prior']}_{int(fc/1e6)}_{session:04d}_{uid}"
                    subdir = os.path.join(root, band["prior"])

                    png = f"{stem}.png"
                    render(p, os.path.join(subdir, png))
                    written = os.path.getsize(os.path.join(subdir, png))

                    iq_name = ""
                    if args.iq != "none":
                        iq_name = f"{stem}.npy"
                        chunk = (samples if args.iq == "full"
                                 else samples[: NFFT * TIME_ROWS])
                        np.save(os.path.join(subdir, iq_name),
                                chunk.astype(np.complex64))
                        written += os.path.getsize(os.path.join(subdir, iq_name))

                    dbm_name = ""
                    if args.save_db_matrix:
                        dbm_name = f"{stem}_db.npy"
                        np.save(os.path.join(subdir, dbm_name),
                                to_db(p).astype(np.float16))
                        written += os.path.getsize(os.path.join(subdir, dbm_name))

                    row = {
                        "capture_uid": uid,
                        "session_id": session,
                        "split": split,
                        "file": png,
                        "rel_path": f"{band['prior']}/{png}",
                        "timestamp_utc": stamp.isoformat(timespec="seconds"),
                        "band_prior": band["prior"],
                        "band_prior_source": "uae_band_plan_assumption",
                        "fc_hz": int(fc),
                        "sample_rate": SAMPLE_RATE,
                        "nfft": NFFT,
                        "hop": NFFT,
                        "time_rows": TIME_ROWS,
                        "img_h": IMG_H,
                        "img_w": IMG_W,
                        "rx_gain_db": src.gain_db,
                        "agc_mode": src.agc_mode,
                        "vmin_dbfs": VMIN_DBFS,
                        "vmax_dbfs": VMAX_DBFS,
                        "dc_notch_bins": DC_NOTCH_BINS,
                        "edge_guard_frac": EDGE_GUARD_FRAC,
                        "iq_file": iq_name,
                        "db_matrix_file": dbm_name,
                        "rx_json": json.dumps({
                            "front_end": "adalm-pluto-revC",
                            "driver": "pyadi-iio",
                            "uri": args.uri if not args.simulate else "simulated",
                            "rx_rf_bandwidth": SAMPLE_RATE,
                            "buffer_size": RX_BUFFER,
                            "settle_s": RETUNE_SETTLE_S,
                            "antenna": "TODO",       # fill in: model, polarisation
                            "location": "TODO",      # fill in: room / node id
                            "cal_offset_db": None,   # reserved: G(f,gain) + AF(f)
                        }),
                    }
                    row.update(m)
                    writer.writerow(row)
                    meta_fh.flush()

                    total_bytes += written
                    n_captures += 1
                    print(f"  {stem}  {m['occupancy_class']:>6} "
                          f"{m['temporal_class']:>10} "
                          f"snr={m['snr_est_db']:>6.1f}dB "
                          f"bw={m['occ_bw_mhz']:>6.2f}MHz "
                          f"clusters={m['n_clusters']}")

                    if args.max_captures and n_captures >= args.max_captures:
                        raise KeyboardInterrupt
                    if total_bytes >= target_bytes:
                        raise KeyboardInterrupt

            session += 1
            if session % 25 == 0:            # cheap drift correction
                total_bytes = dir_size(root)

    except KeyboardInterrupt:
        print("\n[!] stopping")
    finally:
        meta_fh.close()
        src.close()

    print(f"\ncaptures : {n_captures}")
    print(f"size     : {total_bytes / 1024**3:.2f} GB")
    print(f"metadata : {meta_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
