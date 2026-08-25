# RF preprocessing

Milestone 1 uses a deterministic simulated spectrogram generator that writes a 512x512 Viridis PNG
and records complete preprocessing parameters in each capture envelope. The settings are
profile-driven and include pipeline version, FFT size, hop size, window, image size, color map,
axis visibility, and dB clipping fields.

The legacy Pluto capture behavior is preserved under `references/legacy/atheer_capture.py` only as
reference. Real RF-GPT compatibility must be confirmed in Milestone 3 before any real adapter is
marked complete.
