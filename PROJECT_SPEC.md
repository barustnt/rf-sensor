# RF Intelligence Platform — Implementation Specification

**Document version:** 1.1  
**Status:** Approved starting specification  
**Primary repository:** `/home/user/rf-sensor` on the laptop  
**Current runtime target:** All central services on the laptop  
**Future runtime target:** Central services may move to the workstation after connectivity is available  
**Local time zone:** `Asia/Dubai`  
**Canonical storage time:** UTC

## 1. Execution directive for Agent Orchestrator

Read this document completely before creating code. Treat it as the source of truth for the initial implementation.

Agent Orchestrator is a **development tool only**. It may plan work, create worker branches or worktrees, implement code, run tests, and prepare commits. It must not be included as a runtime service, Python dependency, deployment component, API dependency, or dashboard component.

Start with **Milestone 0** and **Milestone 1 only**. Do not integrate the Pluto+, USRP B210, or real RF-GPT process until the simulated vertical slice passes all of its acceptance tests. The first working demonstration must use:

- a simulated RF sensor;
- the real backend API and PostgreSQL database;
- a durable NATS JetStream queue;
- a mock RF-GPT adapter that returns schema-valid results;
- the real Gradio dashboard and historical query path.

Implementation rules:

1. Never hardcode an IP address, password, token, host path, sensor location, or radio setting in application code.
2. Use stable sensor IDs. An IP address is a mutable connection detail, never a device identity.
3. Do not install packages into Conda `base` or the operating-system Python.
4. Keep all public contracts versioned and validated with Pydantic.
5. Preserve a clean boundary between sensor capture, transport, storage, inference, event generation, and presentation.
6. Make ingestion idempotent. Repeated delivery of the same capture must not create duplicate records or duplicate inference jobs.
7. Store all event timestamps in UTC. Convert to `Asia/Dubai` only at the presentation and natural-language query boundary.
8. Treat RF-GPT output as a model observation, not verified ground truth.
9. No radio transmission is part of this project. All SDR operation is receive-only.
10. Do not decode or retain communications payloads in the MVP. Store only the RF artifacts and metadata needed for authorized spectrum analysis.
11. Keep commits small and coherent. Run formatting, linting, typing, and tests before marking a worker task complete.
12. If a real RF-GPT interface detail is missing, leave it behind the adapter boundary and document the required integration information. Do not invent a command or proprietary API.

## 2. Project objective

Build an always-on, local-network RF intelligence platform that:

1. collects RF observations from heterogeneous sensor nodes;
2. moves normalized capture artifacts and metadata to the active central platform host, which is initially the laptop;
3. queues analysis jobs durably;
4. feeds compatible spectrograms and prompts to RF-GPT;
5. validates and stores the model output in a database;
6. correlates individual model observations into time-bounded events;
7. exposes health, storage, logs, jobs, RF-GPT results, and alerts in a web dashboard;
8. answers historical questions such as:
   - “What happened yesterday at 11 PM?”
   - “What technologies were observed around me?”
   - “Were there unusual BLE observations during the exam window?”
9. scales from the current two sensors to approximately 100 sensors without changing the sensor-to-platform contract.

The two initial use cases are:

- **Smart Exam Hall:** detect and investigate unusual or covert BLE activity during an authorized exam-window experiment using a purchased test device.
- **Smart Campus Multi-Technology Coexistence Monitor:** observe RF technology presence, occupancy, interference, and coexistence trends across campus locations.

The system must report evidence and confidence limitations. A BLE observation alone must never be presented as proof that a particular person or device is cheating.

## 3. Current deployment inventory

The following addresses describe the current lab network only. They must be stored in deployment configuration and may change later.

| Stable ID | Current host | Current IP | Connected hardware | Intended role |
|---|---|---:|---|---|
| `pi-pluto-001` | Raspberry Pi | `10.10.187.222` | Pluto+ | Edge RF sensor |
| `laptop-b210-001` | Laptop | `10.10.178.156` | USRP B210 and NVIDIA RTX 4090 Laptop GPU | Edge RF sensor; repository and Agent Orchestrator host; current API, broker, database, artifact storage, VLM/RF-GPT worker, and dashboard host |
| `rf-workstation-001` | Workstation | `10.10.180.45` | RF-GPT-capable compute | Future central host; currently unreachable from the laptop and not required by the initial build |

The observed laptop-to-workstation test currently has 100% packet loss. The initial system must therefore have **no runtime dependency on `10.10.180.45`**.

Use `127.0.0.1` for communication between services on the laptop. Remote sensors use a configurable laptop DNS name such as `rf-laptop.local` when local DNS or mDNS is available, or the current laptop address from deployment configuration. A network change must require a configuration update only, not a code change.

### 3.1 Initial single-host deployment mode

Run these components on the laptop:

- FastAPI ingestion, control, historical query, and dashboard APIs;
- PostgreSQL;
- NATS JetStream;
- filesystem artifact storage;
- Gradio dashboard;
- mock RF-GPT worker during Milestone 1;
- real VLM/RF-GPT worker after Milestone 1 acceptance;
- the local B210 sensor agent when its hardware milestone begins.

The Pi remains a remote edge sensor and uploads to the laptop over the lab network. Before Pi integration, verify Pi-to-laptop connectivity independently; laptop-to-workstation failure does not prove whether Pi-to-laptop works.

### 3.2 Future workstation migration

Keep central services portable so they can later move from the laptop to the workstation. Migration must require changing deployment inventory, service URLs, credentials, and data paths, not application code or sensor contracts. PostgreSQL, NATS, and artifact data must have documented backup/restore or transfer procedures before migration.

## 4. System boundary

### 4.1 Runtime components

```text
┌──────────────────────────────── Sensor fleet ────────────────────────────────┐
│                                                                              │
│  Pi + Pluto+             Laptop + B210              Future sensors           │
│  sensor agent            sensor agent               same contract            │
│       │                       │                           │                   │
│       ├─ capture/profile      ├─ capture/profile         ├─ capture/profile   │
│       ├─ local spool          ├─ local spool             ├─ local spool       │
│       └─ heartbeat/upload     └─ heartbeat/upload        └─ heartbeat/upload  │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ HTTPS/HTTP on trusted laboratory LAN
                                ▼
┌──────────────────── Laptop: initial all-in-one platform ──────────────────────┐
│                                                                              │
│  FastAPI ingestion/control API                                               │
│       ├──────────────► Artifact store                                         │
│       │                spectrograms, optional triggered IQ, metadata          │
│       ├──────────────► PostgreSQL                                             │
│       │                registry, jobs, outputs, events, health                │
│       └──────────────► NATS JetStream                                         │
│                              │                                               │
│                              ▼                                               │
│                    VLM/RF-GPT worker (concurrency 1 initially)                │
│                    preprocess → infer → validate                             │
│                              │                                               │
│                              ▼                                               │
│                    correlation and rules                                     │
│                              │                                               │
│                    PostgreSQL + event evidence                               │
│                              │                                               │
│                ┌─────────────┴─────────────┐                                 │
│                ▼                           ▼                                 │
│       Historical query service       Gradio dashboard                        │
│       evidence-backed answer         operations + RF outputs                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Required boundaries

- Sensors communicate with the central API; they never connect directly to PostgreSQL, NATS, RF-GPT, or the dashboard.
- The dashboard communicates with the backend API; it never connects directly to sensors or PostgreSQL.
- The API validates and accepts captures; it does not perform RF-GPT inference inside an HTTP request.
- The broker separates ingestion from inference so the platform can absorb bursts and retry safely.
- RF-GPT access is hidden behind an adapter so its invocation mechanism can change without changing the rest of the platform.
- Artifact storage is hidden behind an interface. The first implementation uses the laptop filesystem; a future workstation filesystem or S3-compatible store must be possible without changing capture records or sensor agents.
- Correlation and alert rules consume structured model findings, not presentation text.
- Physical co-location does not remove logical service boundaries. The B210 sensor agent still uploads through the API rather than writing directly to the database or model input directory.
- Limit VLM worker concurrency to one initially so inference cannot exhaust GPU memory or destabilize local capture and platform services.

## 5. Selected technology stack

Use the following unless a verified incompatibility is recorded in an architecture decision record.

| Concern | Initial choice |
|---|---|
| Language | Python 3.11 |
| Environment | Conda environment named `rf-intel` |
| Packaging | One `pyproject.toml`, installable package under `src/rf_platform` |
| API | FastAPI + Uvicorn |
| Contracts/settings | Pydantic v2 + pydantic-settings |
| Database | PostgreSQL |
| Database access | SQLAlchemy 2 async + asyncpg |
| Migrations | Alembic |
| Durable messaging | NATS JetStream with durable pull consumers and explicit acknowledgements |
| Artifact storage | Filesystem adapter first; S3-compatible adapter later |
| Dashboard | Gradio Blocks, API client only |
| Logging | Structured JSON logging with `structlog` |
| Resilience | `tenacity` with bounded exponential backoff and jitter |
| HTTP client | `httpx` |
| Serialization | `orjson` where supported |
| RF/DSP utilities | NumPy, SciPy, Pillow, Matplotlib |
| Host health | `psutil` |
| GPU health | NVML-compatible telemetry when the real VLM worker is enabled |
| Tests | pytest, pytest-asyncio, pytest-cov, httpx, respx |
| Quality | Ruff formatting/linting, mypy, pre-commit |
| Infrastructure | Docker Compose for PostgreSQL and NATS only during initial development |

The platform Python applications run in the `rf-intel` Conda environment during development. Do not require Docker access to Pluto+ or USRP hardware in the initial hardware integration.

If the working VLM already uses a separate Conda environment, do not reinstall or modify it during Milestones 0–1. Keep the platform in `rf-intel`, keep the VLM in its existing environment, and integrate them through the RF-GPT adapter—preferably a loopback HTTP endpoint. A configurable subprocess adapter using `conda run -n <environment>` is acceptable only if the model has no service interface and process lifecycle, timeouts, output capture, and GPU cleanup are tested.

## 6. Repository layout

Create this structure. Empty directories should contain a short README or be created when their first file is added.

```text
/home/user/rf-sensor/
├── PROJECT_SPEC.md
├── README.md
├── AGENTS.md
├── pyproject.toml
├── environment.yml
├── .env.example
├── .gitignore
├── Makefile
├── config/
│   ├── profiles/
│   │   ├── campus_general.yml
│   │   ├── campus_2g4_coexistence.yml
│   │   ├── exam_ble.yml
│   │   ├── calibration.yml
│   │   └── device_experiment.yml
│   └── sensors.example.yml
├── src/rf_platform/
│   ├── __init__.py
│   ├── common/
│   │   ├── config.py
│   │   ├── ids.py
│   │   ├── logging.py
│   │   └── time.py
│   ├── contracts/
│   │   ├── sensor.py
│   │   ├── capture.py
│   │   ├── analysis.py
│   │   ├── event.py
│   │   └── api.py
│   ├── sensor_agent/
│   │   ├── main.py
│   │   ├── service.py
│   │   ├── profiles.py
│   │   ├── spool.py
│   │   ├── health.py
│   │   ├── upload.py
│   │   └── adapters/
│   │       ├── base.py
│   │       ├── simulated.py
│   │       ├── pluto.py
│   │       └── b210.py
│   ├── backend/
│   │   ├── main.py
│   │   ├── dependencies.py
│   │   ├── api/v1/
│   │   │   ├── health.py
│   │   │   ├── sensors.py
│   │   │   ├── captures.py
│   │   │   ├── analyses.py
│   │   │   ├── events.py
│   │   │   ├── alerts.py
│   │   │   ├── logs.py
│   │   │   └── query.py
│   │   ├── services/
│   │   │   ├── ingestion.py
│   │   │   ├── registry.py
│   │   │   ├── control.py
│   │   │   └── artifacts.py
│   │   └── db/
│   │       ├── base.py
│   │       ├── models.py
│   │       ├── repositories.py
│   │       └── session.py
│   ├── worker/
│   │   ├── main.py
│   │   ├── consumer.py
│   │   ├── router.py
│   │   ├── validation.py
│   │   ├── correlation.py
│   │   ├── rules.py
│   │   └── rfgpt/
│   │       ├── base.py
│   │       ├── mock.py
│   │       └── local.py
│   └── dashboard/
│       ├── main.py
│       ├── api_client.py
│       └── tabs/
│           ├── overview.py
│           ├── sensors.py
│           ├── storage.py
│           ├── jobs.py
│           ├── outputs.py
│           ├── logs.py
│           ├── alerts.py
│           └── query.py
├── migrations/
├── deploy/
│   ├── docker-compose.infra.yml
│   ├── inventory.example.yml
│   ├── nats/nats-server.conf
│   └── systemd/
├── scripts/
│   ├── seed_demo.py
│   └── run_demo.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   └── fixtures/spectrograms/
├── docs/
│   ├── architecture.md
│   ├── contracts.md
│   ├── operations.md
│   ├── rf-preprocessing.md
│   └── ble-experiment.md
└── references/
    └── legacy/
        └── atheer_capture.py
```

`AGENTS.md` must summarize the build gates, quality commands, prohibited shortcuts, and the instruction that Agent Orchestrator is development-only. It must refer back to this document instead of duplicating the whole specification.

## 7. Conda environment and project dependencies

Create `environment.yml` with this baseline:

```yaml
name: rf-intel
channels:
  - conda-forge
dependencies:
  - python=3.11
  - pip
  - numpy
  - scipy
  - pillow
  - matplotlib
  - pip:
      - -e .[dev]
```

Define runtime and development dependencies in `pyproject.toml`. At minimum:

```text
Runtime:
fastapi
uvicorn[standard]
pydantic
pydantic-settings
sqlalchemy
asyncpg
alembic
nats-py
httpx
python-multipart
orjson
structlog
tenacity
psutil
pyyaml
numpy
scipy
pillow
matplotlib
gradio

Development:
pytest
pytest-asyncio
pytest-cov
respx
ruff
mypy
pre-commit
```

Add these console entry points:

```text
rf-api        -> rf_platform.backend.main:cli
rf-sensor     -> rf_platform.sensor_agent.main:cli
rf-worker     -> rf_platform.worker.main:cli
rf-dashboard  -> rf_platform.dashboard.main:cli
```

The Makefile must expose discoverable commands:

```text
make help
make install
make infra-up
make infra-down
make migrate
make seed
make api
make worker
make sensor-sim
make dashboard
make demo
make format
make lint
make typecheck
make test
make check
```

`make check` must run formatting validation, linting, type checking, and the test suite. Commands must fail with a nonzero exit code on failure.

## 8. Configuration model

Use environment variables with optional YAML deployment inventory. Provide safe examples in `.env.example`; never commit `.env`.

Required baseline settings:

```dotenv
RF_ENV=development
RF_TIMEZONE=Asia/Dubai

# Laptop-local services and the local B210 sensor use loopback.
RF_PLATFORM_URL=http://127.0.0.1:8000
RF_API_HOST=0.0.0.0
RF_API_PORT=8000
RF_DASHBOARD_HOST=0.0.0.0
RF_DASHBOARD_PORT=7860
RF_GRADIO_SHARE=false

RF_DATABASE_URL=postgresql+asyncpg://rf_platform:change-me@127.0.0.1:5432/rf_platform
RF_NATS_URL=nats://127.0.0.1:4222
RF_ARTIFACT_BACKEND=filesystem
RF_ARTIFACT_ROOT=.data/artifacts

RF_SENSOR_ID=laptop-b210-001
RF_SENSOR_TOKEN=change-me
RF_SENSOR_DISPLAY_NAME=Laptop B210 Sensor
RF_SENSOR_LOCATION=lab
RF_SENSOR_ADAPTER=simulated
RF_SENSOR_PROFILE=campus_general
RF_HEARTBEAT_INTERVAL_SECONDS=10
RF_OFFLINE_AFTER_SECONDS=30
RF_SPOOL_ROOT=.data/spool
RF_SPOOL_MAX_BYTES=10737418240

RF_RFGPT_ADAPTER=mock
RF_RFGPT_MODEL_NAME=rfgpt
RF_RFGPT_MODEL_VERSION=unknown
RF_RFGPT_ENDPOINT=http://127.0.0.1:8090
RF_RFGPT_CONDA_ENV=
RF_RFGPT_REQUEST_TIMEOUT_SECONDS=120
RF_WORKER_MAX_ATTEMPTS=5
RF_WORKER_CONCURRENCY=1
```

Rules:

- The application must validate configuration at startup and report missing required values clearly.
- Secret values must be redacted from logs and API responses.
- Relative data paths are acceptable in development. Production-like service files must use explicit configurable paths on the active central host.
- `deploy/inventory.example.yml` may show current IPs but must label them as examples and mutable values.
- Sensor registration records may include the current source IP and hostname for diagnostics, but neither may be used as the primary key.
- The Pi deployment overrides `RF_PLATFORM_URL` with `http://rf-laptop.local:8000` or the laptop's current configured LAN address. Do not put that address in Python code.
- PostgreSQL, NATS, and the VLM endpoint should remain on loopback in the single-host deployment. Only the API and, when desired, the dashboard are exposed to the trusted lab LAN.

## 9. Capture profiles

Capture behavior must be profile-driven. Do not bury frequency, gain, duration, FFT, or upload decisions in adapter code.

Every profile must include:

```yaml
schema_version: "1.0"
profile_id: campus_general
description: General authorized campus survey
enabled: true
schedule:
  mode: continuous
radio:
  center_frequency_hz: 2400000000
  sample_rate_sps: 20000000
  bandwidth_hz: 20000000
  gain_mode: manual
  gain_db: 30
capture:
  duration_ms: 500
  interval_ms: 1000
preprocessing:
  pipeline_version: rfgpt-compatible-v1
  fft_size: 512
  hop_size: 512
  window: blackman
  output_width_px: 512
  output_height_px: 512
  color_map: viridis
  include_axes: false
  db_min: null
  db_max: null
retention:
  upload_spectrogram: true
  upload_iq: triggered
  local_iq_ring_seconds: 60
```

The numeric radio values above are placeholders and must be validated against the hardware and the installed RF-GPT preprocessing before real collection.

Required initial profiles:

- `campus_general`: low-duty survey profile for broad operational testing.
- `campus_2g4_coexistence`: 2.4 GHz coexistence profile intended to observe Wi-Fi/BLE-like activity without decoding payloads.
- `exam_ble`: authorized exam-window profile with higher temporal coverage in the relevant BLE band.
- `calibration`: controlled baseline/noise-floor collection.
- `device_experiment`: labeled start/stop experiment for the purchased BLE test device.

The central platform stores the desired profile per sensor. Sensors poll desired state from the API, validate it locally against declared capabilities, apply it between captures, and report the active profile in the heartbeat. This pull model avoids inbound connections to sensors and continues to work when sensor IPs change.

## 10. Public data contracts

All contracts must contain `schema_version`. Use UUIDv7 if the selected library is stable and well tested; otherwise use UUID4. IDs are strings in JSON.

### 10.1 Sensor registration

```json
{
  "schema_version": "1.0",
  "sensor_id": "pi-pluto-001",
  "display_name": "Pi Pluto Sensor",
  "node_type": "edge_sensor",
  "adapter": "pluto",
  "location": {
    "site": "campus",
    "building": "unknown",
    "room": "lab",
    "coordinates": null
  },
  "groups": ["campus", "2g4-monitoring"],
  "capabilities": {
    "frequency_min_hz": null,
    "frequency_max_hz": null,
    "maximum_sample_rate_sps": null,
    "rx_channels": 1,
    "supported_profiles": ["campus_general", "campus_2g4_coexistence"]
  },
  "software_version": "0.1.0",
  "hostname": "pi-pluto",
  "registered_at_utc": "2026-08-25T00:00:00Z"
}
```

Capabilities must use values reported by and verified against the actual device; do not fill unknown hardware properties from memory.

### 10.2 Sensor heartbeat

```json
{
  "schema_version": "1.0",
  "sensor_id": "pi-pluto-001",
  "sequence": 42,
  "timestamp_utc": "2026-08-25T00:00:10Z",
  "status": "online",
  "active_profile": "campus_general",
  "disk": {
    "total_bytes": 128000000000,
    "free_bytes": 64000000000,
    "used_percent": 50.0
  },
  "spool": {
    "pending_items": 0,
    "pending_bytes": 0,
    "oldest_item_utc": null
  },
  "system": {
    "cpu_percent": 14.2,
    "memory_percent": 38.0,
    "process_uptime_seconds": 3600
  },
  "radio": {
    "connected": true,
    "last_error": null
  },
  "last_capture_utc": "2026-08-25T00:00:09Z",
  "clock_offset_ms": null
}
```

The backend derives `offline`, `degraded`, and `stale` states; a sensor cannot self-declare that its missing future heartbeats are healthy.

### 10.3 Capture envelope

```json
{
  "schema_version": "1.0",
  "capture_id": "string-uuid",
  "sensor_id": "pi-pluto-001",
  "session_id": "optional-experiment-or-survey-id",
  "correlation_id": "request-trace-id",
  "profile_id": "campus_2g4_coexistence",
  "started_at_utc": "2026-08-25T00:00:00Z",
  "ended_at_utc": "2026-08-25T00:00:00.500000Z",
  "radio": {
    "center_frequency_hz": 2400000000,
    "sample_rate_sps": 20000000,
    "bandwidth_hz": 20000000,
    "gain_mode": "manual",
    "gain_db": 30,
    "antenna": null
  },
  "preprocessing": {
    "pipeline_version": "rfgpt-compatible-v1",
    "fft_size": 512,
    "hop_size": 512,
    "window": "blackman",
    "db_min": -120.0,
    "db_max": -20.0,
    "image_width_px": 512,
    "image_height_px": 512,
    "color_map": "viridis",
    "include_axes": false,
    "time_axis_direction": "documented-value",
    "frequency_axis_direction": "documented-value"
  },
  "dsp_metrics": {
    "noise_floor_db": null,
    "peak_power_db": null,
    "occupied_bandwidth_hz": null
  },
  "artifacts": [
    {
      "kind": "spectrogram",
      "filename": "spectrogram.png",
      "mime_type": "image/png",
      "size_bytes": 0,
      "sha256": "hex-digest"
    }
  ],
  "created_at_utc": "2026-08-25T00:00:01Z"
}
```

The API accepts metadata plus one or more artifacts as a streaming multipart request. It must compute and verify size and SHA-256 server-side.

### 10.4 Analysis result

```json
{
  "schema_version": "1.0",
  "analysis_id": "string-uuid",
  "capture_id": "string-uuid",
  "model": {
    "name": "rfgpt",
    "version": "explicit-version-or-model-hash",
    "adapter": "mock",
    "prompt_version": "technology-detection-v1"
  },
  "status": "succeeded",
  "started_at_utc": "2026-08-25T00:00:02Z",
  "completed_at_utc": "2026-08-25T00:00:03Z",
  "latency_ms": 1000,
  "technologies": [
    {
      "label": "bluetooth-like",
      "model_score": null,
      "observation": "Structured summary of visible RF characteristics",
      "evidence": ["capture_id:string-uuid"]
    }
  ],
  "signals": [],
  "overall_assessment": "Model-generated observation, not independently confirmed.",
  "quality_flags": [],
  "parser_valid": true,
  "raw_response": "Original model response"
}
```

Do not invent a numeric confidence score when RF-GPT does not provide a calibrated one. If the model emits a value, name it `model_score`, store the model and prompt version, and present it as uncalibrated unless calibration has been measured.

### 10.5 Event and alert

An event represents one or more observations correlated across time and optionally across sensors. An alert represents a rule applied to events.

Required event fields:

- `event_id`, `schema_version`, `event_kind`, `severity`;
- `started_at_utc`, `ended_at_utc`;
- participating `sensor_ids`, `capture_ids`, and `analysis_ids`;
- structured findings and human-readable summary;
- evidence references;
- `status`: `open`, `acknowledged`, `dismissed`, or `confirmed`;
- annotation history and actor;
- created and updated timestamps.

Required alert fields:

- `alert_id`, `rule_id`, `rule_version`, `event_id`;
- reason and thresholds that fired;
- status and acknowledgment fields;
- timestamps and evidence references.

## 11. Artifact storage

The filesystem artifact adapter must use a deterministic layout:

```text
{artifact_root}/
└── {sensor_id}/
    └── {YYYY}/{MM}/{DD}/
        └── {capture_id}/
            ├── metadata.json
            ├── spectrogram.png
            ├── iq.c64                 # optional and normally absent
            └── analysis/
                └── {analysis_id}.json
```

Rules:

- Store only relative object keys in the database, never laptop or workstation absolute paths.
- Sanitize all path components and reject path traversal.
- Write uploads to a temporary file, verify size/hash/type, then atomically move into place.
- A database capture is not `accepted` until required artifacts are safely committed.
- A failed database transaction must not leave an artifact appearing valid. Reconciliation must be possible.
- Raw IQ is not continuously uploaded in the MVP. Keep a bounded local ring and upload IQ only for an authorized trigger, labeled experiment, or explicit diagnostic request.
- Implement configurable retention policies. Never silently delete an artifact that is referenced by an unresolved event or confirmed experiment.
- Track central-host artifact storage and each sensor’s spool storage separately. In the initial deployment, the laptop appears both as the central host and as the B210 sensor host; do not double-count the same physical disk in fleet totals.

## 12. Durable event flow

Configure a file-backed JetStream stream for subjects under `rf.>`.

Initial subjects:

```text
rf.capture.accepted.v1
rf.analysis.requested.v1
rf.analysis.completed.v1
rf.event.created.v1
rf.alert.created.v1
rf.sensor.health.v1
rf.deadletter.v1
```

Required behavior:

1. The ingestion service validates the request and artifact.
2. It creates or finds the capture using the globally unique `capture_id`.
3. It creates exactly one pending analysis job for the configured model/prompt combination.
4. It publishes `rf.analysis.requested.v1` only after the capture is committed.
5. A durable pull consumer receives the job.
6. The worker marks the job running, invokes the adapter, validates the result, and commits output.
7. The worker acknowledges the NATS message only after the database commit succeeds.
8. Redelivery is expected. Every processing step must be idempotent.
9. Retry transient failures with bounded exponential backoff and jitter.
10. After `RF_WORKER_MAX_ATTEMPTS`, store the failure and publish a dead-letter event.
11. A dashboard operator can inspect and explicitly retry a failed job. The retry is audited.

Do not promise exactly-once transport. Implement at-least-once delivery plus database idempotency.

## 13. Database design

Create Alembic migrations for these tables. Use timezone-aware timestamps and database constraints.

### 13.1 Core tables

- `sensors`
  - stable `sensor_id` primary key;
  - display name, adapter, location JSON, capabilities JSON, groups;
  - desired and active profile;
  - software version, last source IP, last hostname;
  - registration, last-seen, created, updated timestamps;
  - operational status and last error.
- `sensor_heartbeats`
  - primary key, sensor ID foreign key, sequence, timestamp;
  - disk/spool/system/radio measurements;
  - unique `(sensor_id, sequence)`;
  - indexes on `(sensor_id, timestamp desc)` and `timestamp`.
- `capture_profiles`
  - profile ID and version;
  - full validated YAML/JSON definition;
  - active flag and audit timestamps.
- `captures`
  - globally unique capture ID;
  - sensor/session/profile references;
  - capture interval and radio/preprocessing JSON;
  - DSP summary fields;
  - state, correlation ID, timestamps;
  - indexes on time, sensor/time, profile/time, session/time.
- `artifacts`
  - artifact ID, capture ID, kind, backend, relative object key;
  - MIME type, byte size, SHA-256, retention class;
  - unique `(capture_id, kind, sha256)`.
- `dsp_observations`
  - optional deterministic measurements such as peak/noise/occupancy;
  - algorithm and version must be stored.
- `analysis_jobs`
  - job ID, capture ID, model/prompt identity;
  - status, attempt count, available-at time, error category/message;
  - started/completed timestamps;
  - unique `(capture_id, model_name, model_version, prompt_version)`.
- `model_runs`
  - analysis ID, job/capture references;
  - model, adapter, prompt version, latency, status;
  - structured result, raw response, parser status, timestamps.
- `model_findings`
  - normalized technology/signal finding rows linked to a model run;
  - label, optional model score, observation, frequency/time fields.
- `events`
  - correlated event type, severity, status, time range, summary;
  - rule/correlation version and audit fields.
- `event_evidence`
  - many-to-many evidence links to capture, analysis, finding, or artifact.
- `alerts`
  - event/rule reference, status, reason, thresholds, timestamps.
- `annotations`
  - operator label, comment, actor, timestamp, target type/ID;
  - supports `confirmed`, `dismissed`, and experiment ground truth.
- `system_events`
  - queryable operational records such as sensor offline, upload failure,
    storage warning, job dead-letter, login/configuration action;
  - severity, service, sensor ID, correlation ID, structured context, timestamp.

### 13.2 Data-volume strategy

Design high-volume time tables so they can later be partitioned by time, but do not add partitioning complexity in the first migration. Add it only after measured table growth justifies it. The dashboard must use bounded time windows and pagination; it must never load an unbounded table.

### 13.3 Retention defaults

Make all values configurable. Suggested starting values for a lab demonstration:

- heartbeat detail: 30 days;
- structured captures and model metadata: 180 days;
- ordinary spectrogram artifacts: 30 days;
- confirmed experiment/event evidence: retained until explicitly archived;
- debug process logs: 14 days with rotation;
- raw IQ: local ring only unless explicitly promoted as evidence.

Retention execution must begin in report-only mode. It may report eligible items before automated deletion is enabled.

## 14. Backend API

Version all application endpoints under `/api/v1`.

### 14.1 Health

```text
GET /health/live
GET /health/ready
```

`live` checks the process. `ready` checks required dependencies such as PostgreSQL and NATS and returns component status without secrets.

### 14.2 Sensors

```text
POST /api/v1/sensors/register
POST /api/v1/sensors/{sensor_id}/heartbeat
GET  /api/v1/sensors
GET  /api/v1/sensors/{sensor_id}
GET  /api/v1/sensors/{sensor_id}/desired-state
PUT  /api/v1/sensors/{sensor_id}/desired-state
GET  /api/v1/sensors/{sensor_id}/heartbeats
```

The desired-state update is an operator action and must be audited. The sensor uses a monotonically increasing configuration version so it does not repeatedly apply an old profile.

### 14.3 Captures and analyses

```text
POST /api/v1/captures
GET  /api/v1/captures
GET  /api/v1/captures/{capture_id}
GET  /api/v1/captures/{capture_id}/artifacts/{artifact_id}
GET  /api/v1/analyses
GET  /api/v1/analyses/{analysis_id}
POST /api/v1/analyses/jobs/{job_id}/retry
GET  /api/v1/jobs/summary
```

`POST /captures` must:

- authenticate the sensor;
- stream multipart data rather than buffering an unlimited upload in memory;
- enforce configurable maximum file size and accepted types;
- validate metadata against the authenticated sensor;
- return `202 Accepted` with `capture_id`, ingestion status, and job ID;
- return the existing result for a safe duplicate capture ID;
- return `409 Conflict` if the same capture ID is reused with different content.

### 14.4 Events, alerts, logs, and storage

```text
GET   /api/v1/events
GET   /api/v1/events/{event_id}
GET   /api/v1/alerts
PATCH /api/v1/alerts/{alert_id}
POST  /api/v1/annotations
GET   /api/v1/logs
GET   /api/v1/platform/storage
```

List endpoints require server-side filtering, stable sorting, pagination, and time bounds. Common filters include sensor, location, profile, status, severity, technology, model version, prompt version, and UTC interval.

### 14.5 Historical query

```text
POST /api/v1/query
```

Request:

```json
{
  "question": "What happened yesterday at 11 PM?",
  "timezone": "Asia/Dubai",
  "sensor_ids": [],
  "location": null
}
```

Response:

```json
{
  "answer": "Evidence-backed concise answer",
  "interpreted_interval": {
    "start_utc": "...",
    "end_utc": "...",
    "display_timezone": "Asia/Dubai"
  },
  "summary": {
    "capture_count": 0,
    "analysis_count": 0,
    "event_count": 0
  },
  "evidence": [],
  "limitations": []
}
```

For Milestone 1, implement deterministic parsing for explicit ISO dates and a small documented set of phrases such as `today`, `yesterday`, and clock times. Resolve them to a visible interval and query structured records. Do not allow a language model to generate unsupported facts. Every answer must link to capture, analysis, or event IDs. If the interval or location is ambiguous, the API must say what it assumed.

## 15. Sensor agent

The sensor agent is a long-running process with a common service loop and hardware adapters.

### 15.1 Adapter interface

Define a typed asynchronous interface similar to:

```python
class SensorAdapter(Protocol):
    async def open(self) -> None: ...
    async def capabilities(self) -> SensorCapabilities: ...
    async def apply_profile(self, profile: CaptureProfile) -> None: ...
    async def capture(self, request: CaptureRequest) -> CaptureBundle: ...
    async def health(self) -> RadioHealth: ...
    async def close(self) -> None: ...
```

Hardware-specific imports must remain inside the appropriate adapter module. Installing or running the simulated sensor must not require Pluto or UHD libraries.

### 15.2 Service loop

The common agent must:

1. load and validate configuration;
2. establish its stable ID;
3. register and periodically re-register after backend restart if necessary;
4. send heartbeats on an independent timer;
5. poll desired state and validate profiles against capabilities;
6. capture according to the active schedule;
7. perform deterministic preprocessing;
8. create an immutable spool item with metadata and hashes;
9. upload the oldest eligible spool item;
10. delete a local item only after the server confirms the matching capture and hash;
11. retry transient failures with bounded backoff;
12. stop capturing or apply an explicit degradation policy when the spool reaches its configured maximum;
13. shut down cleanly without corrupting an active spool item.

Heartbeat and desired-state polling must continue even if capture or upload is temporarily failing.

### 15.3 Local spool

Use one directory per capture with a manifest and atomic state transitions such as `.writing` to `.ready`. On restart, recover complete ready items, quarantine malformed items, and report them. Never trust only an in-memory queue.

## 16. RF preprocessing compatibility

RF-GPT compatibility is a release gate, not an assumption.

The existing Pluto capture script produces 512×512 Viridis spectrogram PNGs and associated metadata, but it currently uses a Hann window. The published RF-GPT preprocessing describes a Blackman window with FFT size 512 and hop size 512, among other fixed image properties. The installed model may have its own exact preprocessing. Therefore:

1. preserve the legacy script under `references/legacy/atheer_capture.py` without treating it as production code;
2. document its behavior in `docs/rf-preprocessing.md`;
3. determine the installed RF-GPT model’s required:
   - sample format and scaling;
   - window function;
   - FFT and hop sizes;
   - frequency and time orientation;
   - dB clipping/normalization;
   - image size, color map, axes, margins, and interpolation;
   - supported bandwidth and duration;
   - prompt format and output format;
4. implement preprocessing once in a tested common module, not separately per radio;
5. save the complete preprocessing parameters in every capture;
6. create a golden fixture whose pixels or numeric matrix are compared with the model-compatible reference;
7. do not declare real RF-GPT integration complete until the golden fixture passes.

Prefer passing a lossless PNG and retaining the pre-render numeric matrix when practical. Any vertical flip, crop, resampling, or color normalization must be explicit and versioned.

## 17. RF-GPT adapter and worker

### 17.1 Adapter interface

```python
class RFGPTAdapter(Protocol):
    async def health(self) -> ModelHealth: ...
    async def analyze(self, request: AnalysisRequest) -> AnalysisResult: ...
```

Implement:

- `MockRFGPTAdapter`: deterministic, configurable, fast, and used by all Milestone 1 tests.
- `LocalRFGPTAdapter`: a placeholder boundary completed in Milestone 3 after confirming whether the installed model is invoked through Python, a local HTTP endpoint, or a command-line process.

### 17.2 Worker behavior

- Fetch one job at a time per configured concurrency slot.
- Verify the capture and artifact hash before inference.
- Record model, model version/hash, prompt version, preprocessing version, and worker software version.
- Enforce a timeout and classify failures as transient, permanent input failure, parser failure, or model failure.
- Store the raw response plus validated structured output.
- Reject or quarantine schema-invalid output; never silently coerce critical fields.
- Support multiple future worker processes without duplicate completed model runs.
- Publish completion only after the transaction commits.

### 17.3 Prompt contract

The prompt must ask for a constrained structured response and prohibit identity claims unsupported by RF evidence. Version prompts as files or constants with tests. A finding should describe observable RF characteristics and candidate technology labels, not accuse a person.

## 18. Correlation and rules

Individual image classifications are observations. The correlation layer creates operational events.

Initial rule categories:

- repeated technology observations on the same sensor within a rolling interval;
- simultaneous or near-simultaneous observations from multiple sensors;
- observation during a labeled exam or experiment session;
- occupancy or interference above a deterministic DSP threshold;
- sensor offline or storage below a configured threshold;
- analysis backlog, excessive latency, or repeated failure.

Every rule must have a stable ID and version, explicit thresholds, and tests. Store why a rule fired. Do not hide alert logic inside dashboard code.

For the exam use case, begin with neutral labels such as `unexpected_ble_activity` or `ble_like_activity_during_exam`. A `confirmed_test_device` label may be applied only through the controlled experiment workflow or human annotation.

## 19. Dashboard

Build one Gradio Blocks application with API-backed tabs. Set `share=False`. Bind to the configured LAN interface only when intended.

### 19.1 Overview

Show:

- total, online, degraded, offline, and stale sensors;
- active captures and current capture rate;
- queued, running, failed, and dead-letter jobs;
- recent RF events and alerts;
- RF-GPT health and recent inference latency;
- laptop artifact storage and database/broker health;
- GPU utilization, VRAM used/free, temperature, and VLM process health when GPU telemetry is available;
- warnings for low sensor storage, spool growth, or clock drift.

### 19.2 Sensors

For every sensor show:

- stable ID and display name;
- site/building/room;
- radio adapter and capabilities;
- online status and last heartbeat age;
- current hostname and source IP as diagnostic fields;
- software version;
- desired profile, active profile, and profile version;
- radio connection/error state;
- last capture and last successful upload;
- disk free/total, usage percent, spool items/bytes;
- CPU and memory health.

Allow authorized profile updates through the API. Do not provide an arbitrary shell or command-execution control.

### 19.3 Storage

Show central-host artifacts, database health, each sensor spool, percentage used, recent trend, and estimated time-to-full when enough samples exist. Display `unknown` instead of inventing an estimate from insufficient history. In the initial deployment, label the central host as `Laptop (all-in-one)`.

### 19.4 Jobs

Show queue depth, oldest job age, throughput, running jobs, retry counts, failures, dead-letter items, and latency percentiles. Permit an audited retry of an eligible failed job.

### 19.5 RF-GPT outputs

Provide filters by time, sensor, location, profile, technology, status, model version, and prompt version. Each detail view must show:

- spectrogram preview;
- capture time and radio settings;
- sensor and location;
- preprocessing version;
- structured findings;
- raw model response;
- model/prompt versions and latency;
- quality flags and parser status;
- linked event/alert/annotation;
- a visible notice that model output is not automatically verified ground truth.

### 19.6 Logs

Query `system_events` through the API with filters for time, service, sensor, severity, event type, and correlation ID. Process debug logs may be downloaded or inspected separately, but the UI must not try to ingest an unbounded log file into memory.

### 19.7 Alerts and review

Allow an operator to acknowledge, dismiss, confirm, and annotate an alert. Preserve the original result and append an audit record; never overwrite history.

### 19.8 Ask RF

Provide the conversational historical query UI. Always display the interpreted time range, time zone, filters, evidence links, and limitations beside the answer.

Auto-refresh operational summaries every 5–10 seconds without rebuilding the entire interface. All failures must be shown as actionable UI messages rather than raw stack traces.

## 20. Logging and observability

Every service must emit structured JSON logs with:

- UTC timestamp;
- severity;
- service and software version;
- host and optional sensor ID;
- event name;
- message;
- correlation ID, capture ID, job ID, and analysis ID when applicable;
- exception class and sanitized details.

Never log passwords, tokens, full database URLs, or unredacted authorization headers.

Expose lightweight service metrics or a structured metrics endpoint for:

- request counts and latency;
- accepted/rejected uploads and bytes;
- capture rate by sensor;
- queue depth and oldest job age;
- model inference count, latency, timeout, and failure;
- spool count/bytes;
- disk free/used;
- heartbeat age and offline count.

Prometheus and Grafana are future-compatible options, not required for Milestone 1. The Gradio dashboard initially consumes summary endpoints from the backend.

## 21. Security and privacy baseline

The first deployment is on a trusted laboratory LAN, but the code must not assume every LAN client is authorized.

- Give every sensor an independent revocable token. Store only a secure token hash on the server where practical.
- Use constant-time secret comparisons.
- Separate sensor-write and operator-read/write authorization paths.
- Restrict CORS to configured origins.
- Enforce upload size/type limits and validate PNG parsing.
- Prevent path traversal and unsafe filenames.
- Bind PostgreSQL, NATS, and the local VLM endpoint to laptop loopback in the initial deployment; do not publish them to the LAN or internet.
- Keep Gradio `share=False`; do not create a public tunnel.
- Add TLS or a trusted reverse proxy before using an untrusted network.
- Audit profile changes, retries, alert decisions, and retention actions.
- Use least-privilege service accounts and filesystem permissions.
- Document applicable campus authorization, exam policy, privacy notice, retention, and operator access before real monitoring.
- Keep the platform receive-only and metadata-focused. Device or person identification is outside the MVP.

## 22. Failure handling

| Failure | Required behavior |
|---|---|
| Laptop/API unreachable | Remote sensors retain complete spool items and retry with backoff |
| Laptop VLM exhausts GPU memory | Record a model failure, keep the durable job, reduce concurrency or model footprint, and keep the API/database/dashboard running |
| Laptop reboots | PostgreSQL and NATS recover persistent data; sensor spools and pending jobs resume without duplication |
| Sensor restarts | Recover ready spool items; do not duplicate capture IDs |
| Disk approaching full | Warn, stop or reduce new captures according to policy; never corrupt existing evidence |
| Duplicate upload | Return existing capture if hash matches; conflict if it differs |
| NATS unavailable during ingestion | Preserve committed capture and use an outbox/reconciliation mechanism so the job is eventually published |
| Worker stops before ACK | JetStream redelivers; database idempotency prevents duplicate result |
| RF-GPT timeout | Record attempt, retry if transient, dead-letter after limit |
| Invalid model JSON | Preserve raw output, mark parser failure, do not create trusted findings |
| Database unavailable | Readiness fails; services retry safely without pretending success |
| Artifact missing/hash mismatch | Quarantine job and emit a high-severity system event |
| Sensor heartbeat missing | Backend derives stale/offline state after threshold |
| Clock offset excessive | Warn and retain receive time as an additional ordering reference |
| Dashboard unavailable | Capture, ingestion, inference, and storage continue independently |

The API-to-broker boundary requires a reliable outbox or equivalent reconciliation mechanism. A capture must not become permanently unanalyzed because the database commit succeeded just before NATS publication failed.

## 23. Testing strategy

### 23.1 Unit tests

Cover:

- configuration validation and secret redaction;
- time-zone conversion and `yesterday at 11 PM` interval resolution;
- every Pydantic contract;
- profile validation against capabilities;
- artifact keys, hash checks, and traversal rejection;
- spool atomic transitions and restart recovery;
- mock RF-GPT output and parser validation;
- correlation/rule thresholds;
- offline and storage warning calculations;
- idempotency and retry classification.

### 23.2 Integration tests

Use disposable PostgreSQL and NATS services. Cover:

- migration from an empty database;
- sensor registration and heartbeat;
- multipart capture ingestion and artifact commit;
- duplicate capture handling;
- job publication, consumption, commit, and acknowledgment;
- message redelivery after simulated worker failure;
- outbox recovery after simulated NATS outage;
- filtered/paginated API queries;
- authentication rejection and upload limits.

### 23.3 End-to-end simulated test

One command must start infrastructure and application processes, then demonstrate:

1. the simulated sensor registers;
2. heartbeats expose device storage;
3. a fixture spectrogram is spooled and uploaded;
4. a durable job is created;
5. the mock worker produces a valid RF-GPT-like result;
6. the result is stored and appears in the API;
7. a rule creates an event when configured;
8. the dashboard API client sees the sensor, storage, job, output, and log records;
9. a historical query returns the result with evidence;
10. re-uploading the same capture produces no duplicate analysis.

### 23.4 Hardware and model validation

Add these later and mark them separately so CI can run without hardware:

- Pluto+ connection/capture smoke test;
- USRP B210 connection/capture smoke test;
- golden preprocessing comparison;
- real RF-GPT health and one fixture inference;
- controlled BLE-device experiment with explicit ground-truth windows.

## 24. Milestones and acceptance criteria

### Milestone 0 — Repository, environment, and contracts

Deliver:

- repository structure, `pyproject.toml`, `environment.yml`, Makefile;
- `.env.example`, validated settings, and logging;
- Docker Compose PostgreSQL and NATS with persistent named volumes and health checks;
- Pydantic contracts;
- SQLAlchemy models and initial Alembic migration;
- initial documentation and CI-friendly quality commands.

Acceptance:

- installation occurs only in `rf-intel`;
- `make infra-up` starts healthy PostgreSQL and NATS;
- `make migrate` succeeds from an empty database;
- `make check` passes;
- no secret or mutable IP is hardcoded in application code.

### Milestone 1 — Simulated vertical slice

Deliver:

- simulated sensor adapter and durable spool;
- registration, heartbeat, desired-state, and ingestion APIs;
- filesystem artifact store;
- reliable database-to-NATS job publication;
- durable worker with mock RF-GPT adapter;
- model result, events, logs, and historical query endpoints;
- Gradio dashboard tabs sufficient to inspect the flow;
- end-to-end demo script and tests.

Acceptance:

- all ten end-to-end steps in Section 23.3 pass;
- stopping the worker, uploading a capture, and restarting the worker processes the queued job;
- stopping the API causes the sensor to spool locally and later upload successfully;
- changing the central host address requires only environment/configuration changes;
- an offline sensor is visible after the configured threshold;
- the dashboard shows sensor availability, laptop/sensor storage, logs, jobs, RF-GPT output, and an evidence-backed historical answer;
- no Pluto, UHD, or real RF-GPT package is required to run this milestone.

### Milestone 2 — Operational dashboard and reliability hardening

Deliver:

- complete filtering/pagination;
- alerts, acknowledgment, dismissal, confirmation, and annotations;
- storage trend/time-to-full;
- job retry/dead-letter views;
- operational runbook and service health summary;
- retention reporting and backup/restore procedure.

Acceptance:

- dashboard remains responsive with a seeded realistic dataset;
- every operator mutation is audited;
- retention report identifies eligible files without deleting them;
- backup and restore are tested on a disposable environment.

### Milestone 3 — Real VLM/RF-GPT integration on laptop

Before implementation, record:

- invocation mechanism;
- model path/name/version or immutable hash;
- existing Conda environment and exact dependency versions;
- model architecture, precision/quantization, and GPU requirements;
- measured available GPU VRAM from `nvidia-smi` before loading;
- exact input preprocessing;
- prompt and output schema;
- concurrency and memory limits;
- timeout and failure behavior.

Deliver the real adapter and golden compatibility tests. Do not install, upgrade, downgrade, or move the already-working VLM as part of an unrelated worker task. The RTX 4090 laptop GPU is a strong candidate for the first demonstration, but successful operation must be established through measured VRAM use, latency, temperature, and sustained stability rather than assumed from the GPU name.

Acceptance:

- health check reports model readiness;
- a known fixture produces a stored, schema-valid result;
- raw and parsed outputs, model version, prompt version, latency, and preprocessing version are recorded;
- worker concurrency starts at one and does not exhaust laptop CPU, RAM, GPU VRAM, or storage;
- model failure or restart does not stop capture ingestion, the database, broker, API, or dashboard;
- dashboard reports VLM process health and available GPU telemetry;
- a 30-minute sustained fixture run completes without unbounded memory growth or a growing stuck-job backlog.

### Milestone 4 — Pluto+ sensor

Refactor reusable behavior from `atheer_capture.py` into the common sensor pipeline while preserving the legacy source as reference.

Acceptance:

- radio settings are profile-driven;
- the adapter reports verified capabilities and connection status;
- output passes the preprocessing golden test;
- unplugging the radio is reported without crashing the entire agent;
- outage spool and later upload are verified on the Pi.

### Milestone 5 — USRP B210 sensor

Implement the B210 adapter behind the same contract. Keep UHD-specific dependencies optional.

Acceptance:

- the same profile/capture envelope is used;
- hardware-specific limits are validated;
- output passes the same compatible preprocessing tests;
- both real sensors are simultaneously visible by stable ID.

### Milestone 6 — Controlled BLE exam experiment

Create a written experiment plan in `docs/ble-experiment.md`:

- authorization, room, sensor placement, and participants;
- baseline period with the device off;
- labeled activation windows with the device on;
- repeated distance/orientation trials;
- exact time synchronization checks;
- ground-truth annotations separated from model predictions;
- false-positive and false-negative evaluation;
- evidence retention and deletion policy.

Acceptance is an evaluation report, not a promise of perfect device detection. Report detection rate, false alarms, latency, and the conditions under which RF-GPT cannot distinguish the device.

### Milestone 7 — Campus coexistence monitoring

Add scheduled profiles, multiple locations, longer-term summaries, occupancy/interference trends, and multi-sensor event correlation.

Acceptance:

- a new sensor can be added through configuration and credentials without backend code changes;
- per-location and cross-location historical queries return evidence;
- load tests demonstrate the chosen capture rate for the targeted fleet size.

## 25. Work decomposition for Agent Orchestrator

For the initial run, create bounded worker tasks in this order. Tasks with independent files may run concurrently only after contracts are stable.

1. **Foundation and packaging**
   - repository files, environment, settings, logging, Makefile, docs skeleton.
2. **Contracts and database**
   - Pydantic models, SQLAlchemy models, Alembic migration, repositories.
3. **Infrastructure**
   - PostgreSQL/NATS Compose files, JetStream initialization, health checks.
4. **Backend core**
   - authentication baseline, registry, heartbeat, desired state, ingestion, artifact service, outbox.
5. **Simulated sensor**
   - simulator, profile loading, spool, heartbeat, upload, recovery.
6. **Mock worker**
   - durable consumer, mock adapter, validation, results, retries, dead letter.
7. **Events and historical query**
   - deterministic rules, events, evidence-backed query response.
8. **Dashboard**
   - API client and required operational tabs.
9. **Integration and end-to-end verification**
   - seed data, demo runner, failure tests, documentation.

Merge gates:

- No worker may redefine a public contract locally; contract changes go through the contracts task and update tests/docs.
- No dashboard task may query the database directly.
- No hardware package may enter the baseline dependency group.
- Every task must include tests for its behavior.
- Before merging, run `make check` and the relevant integration tests.
- After Milestone 1 passes, stop and present the implementation, commands, test evidence, known limitations, and questions needed for Milestone 3.

Suggested first prompt to Agent Orchestrator:

```text
Read PROJECT_SPEC.md completely and treat it as the source of truth. Build Milestone 0
and Milestone 1 only. Plan bounded tasks, establish the shared contracts first, and use
the simulated sensor plus mock RF-GPT adapter. Keep Agent Orchestrator out of the runtime.
Do not add Pluto+, B210, or real RF-GPT integration yet. Run all required checks and stop
after the simulated end-to-end acceptance criteria pass. Report commits, commands, test
results, remaining limitations, and the exact information needed for the real RF-GPT adapter.
```

## 26. Coding standards

- Use type annotations for public functions and service boundaries.
- Keep I/O asynchronous where it improves concurrency; do CPU-heavy DSP outside the event loop.
- Use dependency injection at API, storage, broker, and model boundaries.
- Prefer small modules with explicit responsibilities.
- Use UTC-aware `datetime`; reject naive datetimes at contracts.
- Use integer Hz/SPS values and byte counts; avoid ambiguous units.
- Use enums for finite states and verify database constraints match them.
- Return safe, structured API errors with a correlation ID.
- Document public APIs and non-obvious failure behavior.
- Pin a reproducible dependency set through an environment lock/export after the first stable install.
- Never include generated captures, model weights, database volumes, tokens, `.env`, or local spool data in Git.

## 27. Git ignore requirements

At minimum, `.gitignore` must include:

```gitignore
.env
.env.*
!.env.example
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
build/
dist/
*.egg-info/
.data/
data/
artifacts/
spool/
logs/
*.log
*.iq
*.c64
*.npy
*.npz
*.h5
*.hdf5
*.db
*.sqlite*
.DS_Store
.idea/
.vscode/
```

Do not ignore small, deliberately curated test fixtures under `tests/fixtures/`.

## 28. Explicitly out of scope for Milestones 0–1

- Agent Orchestrator as a runtime component;
- real Pluto+ or USRP B210 access;
- real RF-GPT invocation;
- public internet exposure or Gradio sharing links;
- Kubernetes, high availability, or multi-region deployment;
- continuous central upload of raw IQ;
- RF transmission, jamming, interception, or payload decoding;
- identity attribution, automated disciplinary decisions, or claims of cheating;
- geolocation or direction finding;
- mobile applications;
- automatic destructive retention;
- Prometheus/Grafana/Loki deployment;
- S3/MinIO implementation beyond the artifact-store interface.

## 29. Definition of done for the initial build

Milestones 0 and 1 are done only when:

- a fresh developer can create/activate `rf-intel` and install the project from documented commands;
- infrastructure starts with one documented command;
- migrations apply to an empty database;
- the simulated end-to-end flow passes;
- offline/recovery, duplicate upload, worker redelivery, and API outage spool behavior are tested;
- the dashboard displays devices, laptop/sensor storage, logs, jobs, RF-GPT-like outputs, alerts/events, and historical query evidence;
- all addresses and credentials are configurable;
- all automated checks pass;
- README and operations documentation match actual commands;
- no secrets, captures, model files, database files, or runtime data are committed;
- Agent Orchestrator is absent from runtime dependencies and deployment topology;
- the team receives a concise implementation report and stops before real hardware/model work.

## 30. References

- Agent Orchestrator repository: <https://github.com/Untrivial-ai/agent-orchestrator>
- FastAPI documentation: <https://fastapi.tiangolo.com/>
- NATS JetStream concepts: <https://docs.nats.io/nats-concepts/jetstream>
- Gradio Blocks documentation: <https://www.gradio.app/docs/gradio/blocks>
- Conda environment management: <https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html>
- PostgreSQL declarative partitioning: <https://www.postgresql.org/docs/current/ddl-partitioning.html>
- RF-GPT paper: <https://arxiv.org/abs/2602.14833>
