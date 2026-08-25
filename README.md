# RF Intelligence Platform

Milestones 0-1 implement a simulated end-to-end RF intelligence slice: simulated sensor,
FastAPI backend, PostgreSQL, NATS JetStream, filesystem artifacts, a mock RF-GPT worker,
Gradio dashboard, and historical query API. Real Pluto+, USRP B210, and real RF-GPT
integration are intentionally out of scope until later milestones.

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
make check
make infra-down
```

`make demo` runs the Milestone 1 simulated acceptance flow and stops before real hardware or
real RF-GPT work.

## Runtime boundaries

Sensors talk only to the API. The dashboard talks only to the API. The worker consumes durable
JetStream jobs and writes validated model observations to PostgreSQL. RF-GPT is hidden behind an
adapter boundary; Milestone 1 uses only the deterministic mock adapter.

## Safety notes

This MVP is receive-only and metadata-focused. It stores spectrogram artifacts and structured
RF observations; it does not decode or retain communications payloads, identify people, or claim
proof of cheating. RF-GPT-like output is displayed as a model observation, not verified ground
truth.
