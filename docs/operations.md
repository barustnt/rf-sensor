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
  monitored. LTE/5G questions state that configured bands were not monitored until later scan
  profiles exist.
