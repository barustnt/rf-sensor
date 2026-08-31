# RF Intelligence Platform

Milestones 0-6 implement a simulated end-to-end RF intelligence slice, operational dashboard
hardening, canonical Atheer/Hann preprocessing, a local vLLM RF-GPT adapter, a receive-only
USRP B210 sensor adapter, a separate Ask RF presentation interface, and safe UAE multi-band
receive-only scan planning with coverage accounting. Real Pluto+ support remains out of scope.

## Environment

Use the Conda environment named `rf-intel`; do not install dependencies into `base`.

```bash
conda env update -f environment.yml --prune
conda activate rf-intel
cp .env.example .env
```

For local development the example `.env` values point PostgreSQL, NATS, and mock RF-GPT to
loopback services. Update the values if the central host changes; no Python code changes are
required.

## Common commands

```bash
make help
make infra-up
make migrate
make seed
make api
make worker
make sensor-sim
make PYTHON='conda run -n rf-b210 python' b210-local-smoke  # requires B210 hardware
make PYTHON='conda run -n rf-intel python' scan-plan       # dry-run, no hardware/API/vLLM
make PYTHON=/path/to/rf-b210/python b210-scan              # explicit allowlist required
make dashboard
make ask-rf
make demo
conda run -n rf-intel python scripts/run_milestone2_acceptance.py
conda run -n rf-intel python scripts/run_real_model_smoke.py  # requires a running local vLLM
make check
make infra-down
```

`make demo` runs the Milestone 1 simulated acceptance flow and stops before real hardware or
real RF-GPT work. `scripts/run_milestone2_acceptance.py` exercises the Milestone 2 operational
dashboard and reliability acceptance flow, including disposable backup/restore verification.
Docker-backed acceptance tests use a unique `rf-sensor-test-*` Compose project, dynamic localhost
ports, and test-only volumes; guarded cleanup may use `down -v` only for that ephemeral project and
never for the operational `rf-sensor` project or `rf-sensor_rf_postgres_data` volume.

## Runtime boundaries

Sensors talk only to the API. The dashboard talks only to the API. The worker consumes durable
JetStream jobs and writes validated model observations to PostgreSQL. RF-GPT is hidden behind an
adapter boundary: the deterministic mock adapter remains available as `mock-v1`, and Milestone 3
adds an optional local vLLM adapter configured entirely through environment variables.

## Operations

Retention is report-only and does not delete data. PostgreSQL and artifact backup/restore steps
are documented in `docs/backup-restore.md`. Local RF-GPT/vLLM launch and smoke-test procedures are
documented in `docs/rfgpt-runtime.md`.
Receive-only B210 setup and acceptance are documented in `docs/b210-sensor.md`. UAE scan
profiles, dry-run planning, backpressure, and coverage semantics are documented in
`docs/scan-profiles.md`. The audience split between the technical Command Center on port 7860 and
Ask RF on port 7861 is documented in `docs/ask-rf.md`.

## Safety notes

This MVP is receive-only and metadata-focused. It stores spectrogram artifacts and structured
RF observations; it does not decode or retain communications payloads, identify people, or claim
proof of cheating. RF-GPT-like output is displayed as a model observation, not verified ground
truth. Ask RF uses only already stored, trusted API data and does not call vLLM or RF-GPT. Experimental
scan profiles can establish monitored coverage for technical operators, but do not create definitive
presentation technology conclusions without explicit operator acceptance or independent validation.
