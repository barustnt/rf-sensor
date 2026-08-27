PYTHON ?= python
export PYTHONPATH := src$(if $(PYTHONPATH),:$(PYTHONPATH))

# Preserve explicit RF_SCAN_* environment/command-line values when the default
# .env.example is included by Make. This keeps documented dry-run invocations like
# `RF_SCAN_ENABLED_PROFILE_IDS=... make scan-plan` from being overwritten by
# example defaults.
PRESERVED_RF_SCAN_VARS := RF_SCAN_PROFILE_CONFIG RF_SCAN_PROFILE_SET RF_SCAN_ENABLED_PROFILE_IDS RF_SCAN_MAX_INFLIGHT_JOBS RF_SCAN_BACKPRESSURE_POLL_SECONDS RF_SCAN_FAILURE_COOLDOWN_SECONDS RF_SCAN_RETUNE_SETTLE_SECONDS RF_SCAN_CYCLE_INTERVAL_SECONDS RF_SCAN_MAX_SLICES_PER_CYCLE
$(foreach var,$(PRESERVED_RF_SCAN_VARS),$(eval _ENV_ORIGIN_$(var) := $(origin $(var)))$(eval _ENV_VALUE_$(var) := $($(var))))

ENV_FILE ?= $(if $(wildcard .env),.env,.env.example)
ifneq (,$(wildcard $(ENV_FILE)))
include $(ENV_FILE)
export
endif
$(foreach var,$(PRESERVED_RF_SCAN_VARS),$(if $(filter environment% command line,$(_ENV_ORIGIN_$(var))),$(eval override $(var) := $(_ENV_VALUE_$(var)))$(eval export $(var))))
CONDA_ENV ?= rf-intel
COMPOSE ?= docker compose -f deploy/docker-compose.infra.yml --project-name rf-sensor

.PHONY: help install infra-up infra-down migrate seed api worker sensor-sim sensor-b210-once sensor-b210 scan-plan b210-scan b210-local-smoke dashboard ask-rf demo m2-acceptance m3-real-smoke backup-restore-check format lint typecheck test check

help: ## Show commands
	@awk 'BEGIN {FS = ":.*##"; printf "Available targets:\n"} /^[a-zA-Z_-]+:.*##/ {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Create/update the rf-intel Conda environment
	conda env update -f environment.yml --prune

infra-up: ## Start PostgreSQL and NATS JetStream
	$(COMPOSE) up -d --wait

infra-down: ## Stop PostgreSQL and NATS JetStream
	$(COMPOSE) down

migrate: ## Apply database migrations
	$(PYTHON) -m alembic upgrade head

seed: ## Seed capture profiles for local demo
	$(PYTHON) scripts/seed_demo.py

api: ## Run FastAPI backend
	$(PYTHON) -m rf_platform.backend.main

worker: ## Run durable mock RF-GPT worker
	$(PYTHON) -m rf_platform.worker.main

sensor-sim: ## Run one simulated sensor cycle
	$(PYTHON) -m rf_platform.sensor_agent.main --once

sensor-b210-once: ## Run one receive-only B210 sensor upload cycle
	RF_SENSOR_ADAPTER=b210 $(PYTHON) -m rf_platform.sensor_agent.main --once

sensor-b210: ## Run the continuous receive-only B210 sensor agent
	RF_SENSOR_ADAPTER=b210 $(PYTHON) -m rf_platform.sensor_agent.main

scan-plan: ## Print deterministic dry-run UAE B210 scan plan without hardware/API access
	@RF_SENSOR_ADAPTER=b210 $(PYTHON) -m rf_platform.sensor_agent.main --scan-plan

b210-scan: ## Run receive-only sequential B210 multi-band scanner
	RF_SENSOR_ADAPTER=b210 $(PYTHON) -m rf_platform.sensor_agent.main --scan

b210-local-smoke: ## Run receive-only local B210 hardware/preprocessing smoke test
	RF_SENSOR_ADAPTER=b210 $(PYTHON) scripts/run_b210_receive_smoke.py

dashboard: ## Run Gradio dashboard
	$(PYTHON) -m rf_platform.dashboard.main

ask-rf: ## Run separate Ask RF presentation interface
	$(PYTHON) -m rf_platform.ask_rf.main

demo: ## Run simulated end-to-end acceptance demo
	$(PYTHON) scripts/run_demo.py

m2-acceptance: ## Run Milestone 2 operational acceptance flow
	$(PYTHON) scripts/run_milestone2_acceptance.py

m3-real-smoke: ## Manually run one real local vLLM RF-GPT smoke test
	$(PYTHON) scripts/run_real_model_smoke.py

backup-restore-check: ## Verify PostgreSQL and artifact backup/restore
	$(PYTHON) scripts/verify_backup_restore.py

format: ## Format code
	$(PYTHON) -m ruff format src tests scripts
	$(PYTHON) -m ruff check --fix src tests scripts

lint: ## Validate formatting and lint
	$(PYTHON) -m ruff format --check src tests scripts
	$(PYTHON) -m ruff check src tests scripts

typecheck: ## Run mypy
	$(PYTHON) -m mypy src tests

test: ## Run tests
	$(PYTHON) -m pytest

check: lint typecheck test ## Run all quality gates
