PYTHON ?= python
export PYTHONPATH := src$(if $(PYTHONPATH),:$(PYTHONPATH))
ENV_FILE ?= $(if $(wildcard .env),.env,.env.example)
ifneq (,$(wildcard $(ENV_FILE)))
include $(ENV_FILE)
export
endif
CONDA_ENV ?= rf-intel
COMPOSE ?= docker compose -f deploy/docker-compose.infra.yml --project-name rf-sensor

.PHONY: help install infra-up infra-down migrate seed api worker sensor-sim dashboard demo m2-acceptance backup-restore-check format lint typecheck test check

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

dashboard: ## Run Gradio dashboard
	$(PYTHON) -m rf_platform.dashboard.main

demo: ## Run simulated end-to-end acceptance demo
	$(PYTHON) scripts/run_demo.py

m2-acceptance: ## Run Milestone 2 operational acceptance flow
	$(PYTHON) scripts/run_milestone2_acceptance.py

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
