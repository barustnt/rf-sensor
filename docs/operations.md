# Operations

1. Create and activate `rf-intel` from `environment.yml`.
2. Copy `.env.example` to `.env` and change credentials/tokens for the local deployment.
3. Start infrastructure with `make infra-up`.
4. Apply migrations with `make migrate`.
5. Seed profiles with `make seed`.
6. Run API, worker, simulated sensor, Command Center, and Ask RF with their Make targets, or run
   the full simulated flow with `make demo`.

## Interfaces

- Command Center: technical Gradio dashboard on port 7860 via `make dashboard`.
- Ask RF: separate read-only presentation interface on port 7861 via `make ask-rf`.

Ask RF uses the platform API server-side, displays plain-language answers only, and does not expose
sensor tokens, database credentials, retry controls, alert controls, UUIDs, model/debug details,
raw JSON, logs, or spectrograms. It does not call vLLM, RF-GPT, the B210, or any sensor while
answering historical questions. See `docs/ask-rf.md`.


## Test infrastructure isolation

`make check` includes Docker-backed simulated acceptance tests. Those tests must not share the
operational Compose project, fixed operational container names, host ports, or PostgreSQL volume.
They start an ephemeral Compose project named `rf-sensor-test-<suffix>` from
`deploy/docker-compose.acceptance.yml`, publish PostgreSQL and NATS on dynamic loopback ports, and
point the API/worker/sensor test environment at those mapped ports. Test cleanup is fail-closed:
`down -v --remove-orphans` is allowed only after the project name validates as `rf-sensor-test-*`;
cleanup is refused for an empty name or the operational `rf-sensor` project.

Operational data lives under the normal `rf-sensor` Compose project and `rf-sensor_rf_postgres_data`
volume. Do not run destructive acceptance cleanup commands against that project.

## Milestone 2 operator actions

- Dashboard list views are bounded by filters plus `limit`/`offset`; avoid unbounded time ranges
  for high-volume tables.
- RF-GPT output details show structured findings, raw response, model/prompt versions, linked
  evidence, annotations, and a limitation notice. Treat model output as an observation, not
  verified ground truth.
- Alert acknowledgment, dismissal, confirmation, annotations, retry requests, and retention
  reports are written to `system_events` for audit review.
- Failed and dead-letter analysis jobs may be retried from the dashboard/API only when marked
  retry-eligible. The retry creates a fresh durable analysis request.
- Retention runs in report-only mode for Milestone 2. Reports identify eligible records and files
  but never delete them automatically.

## Backup and restore

Use `docs/backup-restore.md` for PostgreSQL and artifact backup/restore procedures. The disposable
verification command is:

```bash
conda run -n rf-intel python scripts/verify_backup_restore.py
```

## Milestone 3 local RF-GPT

- Use `docs/rf-preprocessing.md` for the canonical `atheer-hann-v1` preprocessing pipeline.
- Use `docs/rfgpt-runtime.md` to launch the local-only vLLM server in `vllm-env`.
- Start the API and worker from the same code revision and with identical `RF_RFGPT_ADAPTER`,
  `RF_RFGPT_MODEL_NAME`, `RF_RFGPT_MODEL_VERSION`, prompt/schema defaults, and `RF_DATABASE_URL`.
  The database URL must include the PostgreSQL password in the runtime environment, but the
  password must not be printed in logs or documentation.
- The worker validates PostgreSQL readiness with `SELECT 1` before it connects to NATS or consumes
  analysis jobs. Authentication or connectivity failures stop startup before subscription.
- Internally inconsistent RF-GPT output is preserved raw, marked `semantic_inconsistency`, excluded
  from trusted findings, and does not create an event or alert. RF-GPT findings that are accepted
  remain unverified model observations.
- Keep model paths and weights outside Git and supply `RF_RFGPT_MODEL_PATH` only through an
  untracked local environment file.
- The worker still runs with `RF_WORKER_CONCURRENCY=1`.
- The real-model smoke test is manual and requires an already-running vLLM endpoint:

```bash
conda run -n rf-intel python scripts/run_real_model_smoke.py
```

## Milestone 4 receive-only B210 sensor

- Use `environment-b210.yml` for UHD hardware access in `rf-b210`.
- Keep the VLM runtime separate in `vllm-env`; do not run RF-GPT from `rf-b210`.
- Use `docs/b210-sensor.md` for one-shot, continuous, and full-platform acceptance commands.
- Raw IQ persistence is disabled by default with `RF_B210_PERSIST_RAW_IQ=false`.

## Ask RF presentation behavior

- Start the API with the same `RF_SENSOR_TOKEN` used by sensors and a password-bearing
  `RF_DATABASE_URL`; never print the password.
- Start Ask RF with `RF_PLATFORM_URL`, `RF_ASK_RF_HOST`, `RF_ASK_RF_PORT`,
  `RF_DISPLAY_TIMEZONE`, and `RF_API_TIMEOUT_SECONDS`.
- Ask RF excludes simulated, mock, parser-invalid, failed, dead-letter, model-mismatched, and
  semantically inconsistent records. Historical excluded records remain visible in the Command
  Center.
- Internally inconsistent historical output is preserved in the platform but rejected from Ask RF
  trusted findings and produces no presentation event or alert claim.
- Bluetooth/BLE answers mention partial 2.4 GHz coverage when only a slice of the band was
  monitored. LTE/5G questions now distinguish no eligible coverage from experimental monitored
  ranges that are not yet presentation-validated.

## Milestone 6 UAE multi-band scanning and coverage

Milestone 6 adds a separate receive-only B210 scan mode without changing the existing B210 `--once`
command. Scanning is disabled unless the operator supplies an explicit profile allowlist. An empty
`RF_SCAN_ENABLED_PROFILE_IDS` plans and captures nothing.

Dry-run plan validation is safe in `rf-intel` or `rf-b210`; it does not open hardware, call the API,
consume jobs, or invoke vLLM:

```bash
RF_SENSOR_ADAPTER=b210 \
RF_SCAN_ENABLED_PROFILE_IDS=uae_shared_2400_2483_5 \
RF_SCAN_MAX_SLICES_PER_CYCLE=2 \
make PYTHON='conda run -n rf-intel python' scan-plan
```

Sequential scan mode must run from the UHD-capable `rf-b210` environment and uses one B210 capture
operation at a time:

```bash
RF_SENSOR_TOKEN="${RF_SENSOR_TOKEN:?set the shared sensor token}" \
RF_SENSOR_ADAPTER=b210 \
RF_SENSOR_ID=laptop-b210-001 \
RF_PLATFORM_URL=http://127.0.0.1:8000 \
RF_B210_DEVICE_ARGS=serial=321D88A \
RF_B210_SERIAL=321D88A \
RF_SCAN_ENABLED_PROFILE_IDS=uae_shared_2400_2483_5 \
RF_SCAN_MAX_SLICES_PER_CYCLE=2 \
RF_B210_PERSIST_RAW_IQ=false \
/home/user/miniconda3/envs/rf-b210/bin/python -m rf_platform.sensor_agent.main --scan --scan-one-cycle --scan-max-slices 2
```

The scanner checks sensor-scoped in-flight analysis jobs before each slice. Queued, running, and
retry-pending jobs count toward `RF_SCAN_MAX_INFLIGHT_JOBS`; succeeded, failed, and dead-letter jobs
do not. API outages and hardware failures pause with bounded cooldowns. This prevents RF-GPT
backlog from growing unbounded when inference is much slower than capture.

Technical operators can inspect read-only scan and coverage data in the Command Center Operations
section or by API:

```text
GET /api/v1/scan-profiles
GET /api/v1/coverage?start_utc=...&end_utc=...&sensor_id=...
GET /api/v1/sensors/{sensor_id}/jobs/summary
```

The browser exposes no start/stop, retune, transmit, token, password, or profile-promotion controls.
Ask RF remains separate and read-only on port 7861.

### Live stack with an external vLLM

`scripts/live_up.sh` starts the complete operational stack. By default it manages a local vLLM.
To use an already-running LAN server without starting or stopping that server, set:

```bash
RF_VLLM_MANAGED=false \
RF_RFGPT_ENDPOINT=http://192.168.1.11:8000/v1 \
RF_RFGPT_MODEL_NAME=rfgpt \
./scripts/live_up.sh
```

Before the API, worker, or scanner starts, the script checks `/health` and `/v1/models`. The
configured `RF_RFGPT_MODEL_NAME` must exactly match an advertised model ID. Prefer launching the
remote server with `--served-model-name rfgpt`. If that is not possible, set
`RF_RFGPT_MODEL_NAME` to the exact ID returned by `/v1/models`.

`scripts/live_down.sh` stops only processes that `live_up.sh` started on the sensor host. With
`RF_VLLM_MANAGED=false`, it never attempts to stop the external vLLM. It stops operational Compose
services without `-v`, so the PostgreSQL history volume is preserved.

Experimental Ask RF findings may include an aggregate model-reported score when the stored model
output supplied one. The score is explicitly labeled as uncalibrated, rejected or band-incompatible
results are excluded from it, and the answer remains non-definitive until the scan profile is
validated.

See `docs/scan-profiles.md` for the UAE catalogue, regulatory/source notes, qualification states,
coverage accounting, band-compatibility checks, and the full manual acceptance plan. RF-GPT labels
remain unverified model observations. Experimental profiles may be scanned and shown to technical
operators, but do not establish presentation-ready technology conclusions.
