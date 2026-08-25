# RF preprocessing

Milestone 3 promotes the approved Atheer reference pipeline into production code as
`rf_platform.preprocessing.atheer_hann` with pipeline ID `atheer-hann-v1`.

The canonical reference remains:

- file: `references/legacy/atheer_capture.py`
- SHA-256: `2b44a61b64e0aaceb64b538b8b7b5b41bdc1f5c6aff41f9513a3ca49c094312e`

Do not modify the reference file. Golden tests compare the production implementation against it.

## Pipeline `atheer-hann-v1`

Input is complex IQ from any sensor adapter. Hardware acquisition is not part of this module.

Exact behavior:

1. Require at least `512 * 512` complex samples.
2. `NFFT = 512`.
3. `TIME_ROWS = 512`.
4. Hop is `512` samples with no overlap.
5. Window is `np.hanning(512)` / Hann.
6. Use `np.fft.fft(..., axis=1)`.
7. Apply `np.fft.fftshift(..., axes=1)`.
8. Divide the FFT by `NFFT`.
9. Convert to linear power as `abs(FFT) ** 2`.
10. Group all available frames into `floor(frame_count / 512)` frames per output row and
    mean-reduce each group exactly as the reference.
11. Apply the center DC/LO notch by interpolating over 3 bins on each side of center plus
    the center bin.
12. Deterministic measurements use the configured edge guard (`EDGE_GUARD_FRAC = 0.06`) only
    for measurement calculations.
13. The rendered model image is **not cropped** by the edge guard.
14. Convert power to dB as `10 * log10(power + 1e-20)`.
15. Render with fixed range `-110` to `-20 dBFS`.
16. Transpose the dB matrix, then vertically flip it.
17. Time is horizontal, left to right.
18. Frequency is vertical, with low frequency at the bottom.
19. Use Viridis RGB.
20. Output exactly a 512x512 PNG.
21. Render with no axes, title, text, margins, interpolation, or color bar.

The production module returns:

- the notched 512x512 linear-power matrix;
- deterministic measurement values;
- provenance metadata including the reference SHA, pipeline ID, orientation, dB range, window,
  FFT, hop, DC notch, and edge-guard usage;
- lossless PNG bytes.

## Why Hann is intentional

The Atheer reference is the approved baseline for the current local RF-GPT integration. It uses
Hann (`np.hanning`), so Milestone 3 intentionally preserves Hann even though earlier RF-GPT
descriptions mention Blackman. Blackman may be tested later as a separately versioned pipeline
and must never silently replace `atheer-hann-v1`.

## Golden compatibility tests

`tests/unit/test_atheer_preprocessing.py`:

- generates deterministic complex IQ;
- runs both the legacy reference and production pipeline;
- compares numeric power matrices exactly;
- compares deterministic measurement dictionaries exactly;
- compares final PNG pixels exactly.

These tests fail if orientation, normalization, colormap, window, FFT, hop, clipping, frame
grouping, or the DC notch behavior changes.
