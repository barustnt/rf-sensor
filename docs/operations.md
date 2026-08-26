# Operations

1. Create and activate `rf-intel` from `environment.yml`.
2. Copy `.env.example` to `.env` and change credentials/tokens for the local deployment.
3. Start infrastructure with `make infra-up`.
4. Apply migrations with `make migrate`.
5. Seed profiles with `make seed`.
6. Run API, worker, simulated sensor, and dashboard with their Make targets, or run the full
   simulated flow with `make demo`.

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
