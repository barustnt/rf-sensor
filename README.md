# RF Intelligence Platform

Milestones 0-2 implement a simulated end-to-end RF intelligence slice plus operational
dashboard hardening: simulated sensor, FastAPI backend, PostgreSQL, NATS JetStream,
filesystem artifacts, a mock RF-GPT worker, Gradio dashboard, historical query API,
filtering/pagination, audited operator actions, retry controls, storage trends, metrics,
report-only retention, and backup/restore procedures. Real Pluto+, USRP B210, and real RF-GPT
integration remain out of scope.

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
make dashboard
make demo
conda run -n rf-intel python scripts/run_milestone2_acceptance.py
make check
make infra-down
```

`make demo` runs the Milestone 1 simulated acceptance flow and stops before real hardware or
real RF-GPT work. `scripts/run_milestone2_acceptance.py` exercises the Milestone 2 operational
dashboard and reliability acceptance flow, including disposable backup/restore verification.

## Runtime boundaries

Sensors talk only to the API. The dashboard talks only to the API. The worker consumes durable
JetStream jobs and writes validated model observations to PostgreSQL. RF-GPT is hidden behind an
adapter boundary; the current milestones use only the deterministic mock adapter with version
`mock-v1`.

## Operations

Retention is report-only in Milestone 2 and does not delete data. PostgreSQL and artifact
backup/restore steps are documented in `docs/backup-restore.md`.

## Safety notes

This MVP is receive-only and metadata-focused. It stores spectrogram artifacts and structured
RF observations; it does not decode or retain communications payloads, identify people, or claim
proof of cheating. RF-GPT-like output is displayed as a model observation, not verified ground
truth.
