#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${RF_LIVE_RUNTIME_DIR:-${REPO_ROOT}/.data/runtime}"
COMPOSE_FILE="${REPO_ROOT}/deploy/docker-compose.infra.yml"
COMPOSE_PROJECT="rf-sensor"

RF_INTEL_PYTHON="${RF_INTEL_PYTHON:-/home/user/miniconda3/envs/rf-intel/bin/python}"
RF_B210_PYTHON="${RF_B210_PYTHON:-/home/user/miniconda3/envs/rf-b210/bin/python}"
RF_VLLM_BIN="${RF_VLLM_BIN:-/home/user/miniconda3/envs/vllm-env/bin/vllm}"
RF_RFGPT_MODEL_PATH="${RF_RFGPT_MODEL_PATH:-/home/user/models/Qwen2.5-VL-7B-rfa-wtr-v2-joint}"
RF_RFGPT_MODEL_NAME="${RF_RFGPT_MODEL_NAME:-rfgpt}"
RF_RFGPT_MODEL_VERSION="${RF_RFGPT_MODEL_VERSION:-Qwen2.5-VL-7B-rfa-wtr-v2-joint}"
RF_VLLM_MANAGED_VALUE="${RF_VLLM_MANAGED:-true}"
RF_RFGPT_ENDPOINT_VALUE="${RF_RFGPT_ENDPOINT:-http://127.0.0.1:8090/v1}"
RF_RFGPT_ENDPOINT_VALUE="${RF_RFGPT_ENDPOINT_VALUE%/}"
RF_VLLM_HEALTH_URL_VALUE="${RF_VLLM_HEALTH_URL:-${RF_RFGPT_ENDPOINT_VALUE%/v1}/health}"
if [[ "$RF_RFGPT_ENDPOINT_VALUE" == */v1 ]]; then
  RF_VLLM_MODELS_URL_VALUE="${RF_RFGPT_ENDPOINT_VALUE}/models"
else
  RF_VLLM_MODELS_URL_VALUE="${RF_RFGPT_ENDPOINT_VALUE}/v1/models"
fi
RF_SENSOR_TOKEN_VALUE="${RF_SENSOR_TOKEN:-change-me}"
RF_SENSOR_ID_VALUE="${RF_SENSOR_ID:-laptop-b210-001}"
RF_B210_SERIAL_VALUE="${RF_B210_SERIAL:-321D88A}"
RF_SCAN_ENABLED_PROFILE_IDS_VALUE="${RF_SCAN_ENABLED_PROFILE_IDS:-uae_srd_915_921,uae_imt_1805_1880,uae_shared_2400_2483_5,uae_nr_tdd_3300_3400,uae_wifi5_5150_5250}"
RF_SCAN_MAX_INFLIGHT_JOBS_VALUE="${RF_SCAN_MAX_INFLIGHT_JOBS:-1}"
RF_SCAN_CYCLE_INTERVAL_SECONDS_VALUE="${RF_SCAN_CYCLE_INTERVAL_SECONDS:-5}"
RF_GRADIO_SHARE_VALUE="${RF_GRADIO_SHARE:-true}"
PYTHONPATH_VALUE="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

log() {
  printf '[live-up] %s\n' "$*"
}

fail() {
  log "ERROR: $*" >&2
  exit 1
}

require_executable() {
  [[ -x "$1" ]] || fail "required executable not found: $1"
}

cmdline_for_pid() {
  local pid="$1"
  tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true
}

managed_pid() {
  local name="$1"
  local expected="$2"
  local pid_file="${RUNTIME_DIR}/${name}.pid"
  local pid
  local cmdline

  [[ -f "$pid_file" ]] || return 1
  pid="$(<"$pid_file")"
  [[ "$pid" =~ ^[0-9]+$ ]] || fail "invalid PID file: $pid_file"
  kill -0 "$pid" 2>/dev/null || {
    rm -f "$pid_file"
    return 1
  }
  cmdline="$(cmdline_for_pid "$pid")"
  [[ "$cmdline" == *"$expected"* ]] || {
    fail "PID $pid in $pid_file does not match expected process '$expected'"
  }
  printf '%s\n' "$pid"
}

start_service() {
  local name="$1"
  local expected="$2"
  shift 2
  local pid
  local log_file="${RUNTIME_DIR}/${name}.log"

  if pid="$(managed_pid "$name" "$expected")"; then
    log "$name already running (PID $pid)"
    return
  fi
  if pgrep -f -- "$expected" >/dev/null 2>&1; then
    fail "$name appears to be running outside live_up.sh; stop it before retrying"
  fi

  : > "$log_file"
  nohup "$@" >> "$log_file" 2>&1 < /dev/null &
  pid=$!
  printf '%s\n' "$pid" > "${RUNTIME_DIR}/${name}.pid"
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    tail -50 "$log_file" >&2 || true
    fail "$name exited during startup"
  fi
  log "$name started (PID $pid)"
}

wait_http() {
  local name="$1"
  local url="$2"
  local attempts="$3"
  local delay="$4"
  local log_file="${5-${RUNTIME_DIR}/${name}.log}"
  local attempt

  for attempt in $(seq 1 "$attempts"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "$name ready"
      return
    fi
    sleep "$delay"
  done
  if [[ -n "$log_file" && -f "$log_file" ]]; then
    tail -50 "$log_file" >&2 || true
  fi
  fail "$name did not become ready at $url"
}

show_service() {
  local name="$1"
  local expected="$2"
  local pid
  if pid="$(managed_pid "$name" "$expected")"; then
    printf '%-16s READY (PID %s)\n' "$name" "$pid"
  else
    printf '%-16s STOPPED\n' "$name"
    return 1
  fi
}

show_gradio_public_url() {
  local service="$1"
  local label="$2"
  local public_url=""
  local attempt

  for attempt in $(seq 1 30); do
    public_url="$(
      grep -Eo 'https://[^ ]+\.gradio\.live' "${RUNTIME_DIR}/${service}.log" |
        tail -1 || true
    )"
    [[ -n "$public_url" ]] && break
    sleep 2
  done
  if [[ -n "$public_url" ]]; then
    printf '%-22s %s\n' "$label" "$public_url"
    return
  fi
  log "WARNING: $service is local, but no public Gradio URL was found"
  return 1
}

validate_vllm_model() {
  local models_url="$RF_VLLM_MODELS_URL_VALUE"

  if ! curl -fsS --max-time 15 "$models_url" |
    EXPECTED_MODEL_NAME="$RF_RFGPT_MODEL_NAME" "$RF_INTEL_PYTHON" -c '
import json
import os
import sys

expected = os.environ["EXPECTED_MODEL_NAME"]
payload = json.load(sys.stdin)
model_ids = [str(item.get("id")) for item in payload.get("data", []) if item.get("id")]
if expected not in model_ids:
    available = ", ".join(model_ids) if model_ids else "none"
    raise SystemExit(
        f"expected served model {expected!r}; vLLM advertises: {available}. "
        "Set RF_RFGPT_MODEL_NAME to an advertised ID or restart vLLM with "
        "--served-model-name rfgpt."
    )
'; then
    fail "vLLM model validation failed at $models_url"
  fi
  log "vLLM serves expected model '$RF_RFGPT_MODEL_NAME'"
}

require_executable "$RF_INTEL_PYTHON"
require_executable "$RF_B210_PYTHON"
case "$RF_VLLM_MANAGED_VALUE" in
  true|false) ;;
  *) fail "RF_VLLM_MANAGED must be true or false" ;;
esac
if [[ "$RF_VLLM_MANAGED_VALUE" == "true" ]]; then
  require_executable "$RF_VLLM_BIN"
  [[ -d "$RF_RFGPT_MODEL_PATH" ]] || fail "RF-GPT model path not found: $RF_RFGPT_MODEL_PATH"
fi
command -v docker >/dev/null || fail "docker is not installed"
command -v curl >/dev/null || fail "curl is not installed"
command -v pgrep >/dev/null || fail "pgrep is not installed"

mkdir -p "$RUNTIME_DIR"
cd "$REPO_ROOT"

log "starting persistent PostgreSQL and NATS infrastructure"
docker compose -f "$COMPOSE_FILE" --project-name "$COMPOSE_PROJECT" up -d --wait

RF_DATABASE_URL_VALUE="${RF_DATABASE_URL:-}"
if [[ -z "$RF_DATABASE_URL_VALUE" ]]; then
  RF_DATABASE_URL_VALUE="$(
    docker inspect rf-platform-postgres --format '{{json .Config.Env}}' |
      "$RF_INTEL_PYTHON" -c '
import json
import sys
from urllib.parse import quote

values = dict(item.split("=", 1) for item in json.load(sys.stdin))
user = quote(values["POSTGRES_USER"], safe="")
password = quote(values["POSTGRES_PASSWORD"], safe="")
database = quote(values["POSTGRES_DB"], safe="")
print(f"postgresql+asyncpg://{user}:{password}@127.0.0.1:5432/{database}")
'
  )"
fi
[[ -n "$RF_DATABASE_URL_VALUE" ]] || fail "could not construct RF_DATABASE_URL"

log "applying database migrations"
env \
  PYTHONPATH="$PYTHONPATH_VALUE" \
  RF_DATABASE_URL="$RF_DATABASE_URL_VALUE" \
  "$RF_INTEL_PYTHON" -m alembic upgrade head

if [[ "$RF_VLLM_MANAGED_VALUE" == "true" ]]; then
  start_service \
    vllm \
    "$RF_VLLM_BIN serve $RF_RFGPT_MODEL_PATH" \
    env \
      HF_HUB_OFFLINE=1 \
      TRANSFORMERS_OFFLINE=1 \
      CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}" \
      "$RF_VLLM_BIN" serve "$RF_RFGPT_MODEL_PATH" \
        --served-model-name "$RF_RFGPT_MODEL_NAME" \
        --host 127.0.0.1 \
        --port 8090 \
        --dtype bfloat16 \
        --gpu-memory-utilization "${RF_VLLM_GPU_MEMORY_UTILIZATION:-0.80}" \
        --cpu-offload-gb "${RF_VLLM_CPU_OFFLOAD_GB:-10}" \
        --max-model-len "${RF_VLLM_MAX_MODEL_LEN:-2048}" \
        --max-num-seqs "${RF_VLLM_MAX_NUM_SEQS:-1}" \
        --enforce-eager \
        --limit-mm-per-prompt '{"image":1,"video":0}'
else
  log "using external vLLM endpoint $RF_RFGPT_ENDPOINT_VALUE"
fi
if [[ "$RF_VLLM_MANAGED_VALUE" == "true" ]]; then
  wait_http vllm "$RF_VLLM_HEALTH_URL_VALUE" 120 5
else
  wait_http vllm "$RF_VLLM_HEALTH_URL_VALUE" 12 5 ""
fi
validate_vllm_model

start_service \
  api \
  "rf_platform.backend.main" \
  env \
    PYTHONPATH="$PYTHONPATH_VALUE" \
    RF_DATABASE_URL="$RF_DATABASE_URL_VALUE" \
    RF_SENSOR_TOKEN="$RF_SENSOR_TOKEN_VALUE" \
    RF_SCAN_ENABLED_PROFILE_IDS="$RF_SCAN_ENABLED_PROFILE_IDS_VALUE" \
    RF_RFGPT_ADAPTER=vllm \
    RF_RFGPT_ENDPOINT="$RF_RFGPT_ENDPOINT_VALUE" \
    RF_RFGPT_MODEL_NAME="$RF_RFGPT_MODEL_NAME" \
    RF_RFGPT_MODEL_VERSION="$RF_RFGPT_MODEL_VERSION" \
    RF_RFGPT_REQUEST_TIMEOUT_SECONDS="${RF_RFGPT_REQUEST_TIMEOUT_SECONDS:-300}" \
    RF_RFGPT_REPETITION_PENALTY="${RF_RFGPT_REPETITION_PENALTY:-1.05}" \
    RF_RFGPT_MAX_OUTPUT_TOKENS="${RF_RFGPT_MAX_OUTPUT_TOKENS:-224}" \
    PYTHONUNBUFFERED=1 \
    "$RF_INTEL_PYTHON" -u -m rf_platform.backend.main
wait_http api http://127.0.0.1:8000/health/ready 60 2

start_service \
  worker \
  "rf_platform.worker.main" \
  env \
    PYTHONPATH="$PYTHONPATH_VALUE" \
    RF_DATABASE_URL="$RF_DATABASE_URL_VALUE" \
    RF_RFGPT_ADAPTER=vllm \
    RF_RFGPT_ENDPOINT="$RF_RFGPT_ENDPOINT_VALUE" \
    RF_RFGPT_MODEL_NAME="$RF_RFGPT_MODEL_NAME" \
    RF_RFGPT_MODEL_VERSION="$RF_RFGPT_MODEL_VERSION" \
    RF_RFGPT_REQUEST_TIMEOUT_SECONDS="${RF_RFGPT_REQUEST_TIMEOUT_SECONDS:-300}" \
    RF_RFGPT_REPETITION_PENALTY="${RF_RFGPT_REPETITION_PENALTY:-1.05}" \
    RF_RFGPT_MAX_OUTPUT_TOKENS="${RF_RFGPT_MAX_OUTPUT_TOKENS:-224}" \
    RF_WORKER_CONCURRENCY="${RF_WORKER_CONCURRENCY:-1}" \
    PYTHONUNBUFFERED=1 \
    "$RF_INTEL_PYTHON" -u -m rf_platform.worker.main

start_service \
  scanner \
  "rf_platform.sensor_agent.main --scan" \
  env \
    PYTHONPATH="$PYTHONPATH_VALUE" \
    RF_SENSOR_TOKEN="$RF_SENSOR_TOKEN_VALUE" \
    RF_SENSOR_ADAPTER=b210 \
    RF_SENSOR_ID="$RF_SENSOR_ID_VALUE" \
    RF_PLATFORM_URL=http://127.0.0.1:8000 \
    RF_B210_DEVICE_ARGS="serial=$RF_B210_SERIAL_VALUE" \
    RF_B210_SERIAL="$RF_B210_SERIAL_VALUE" \
    RF_SCAN_ENABLED_PROFILE_IDS="$RF_SCAN_ENABLED_PROFILE_IDS_VALUE" \
    RF_SCAN_MAX_INFLIGHT_JOBS="$RF_SCAN_MAX_INFLIGHT_JOBS_VALUE" \
    RF_SCAN_CYCLE_INTERVAL_SECONDS="$RF_SCAN_CYCLE_INTERVAL_SECONDS_VALUE" \
    RF_B210_PERSIST_RAW_IQ=false \
    PYTHONUNBUFFERED=1 \
    "$RF_B210_PYTHON" -u -m rf_platform.sensor_agent.main --scan

start_service \
  dashboard \
  "rf_platform.dashboard.main" \
  env \
    PYTHONPATH="$PYTHONPATH_VALUE" \
    RF_PLATFORM_URL=http://127.0.0.1:8000 \
    RF_DASHBOARD_HOST=127.0.0.1 \
    RF_DASHBOARD_PORT=7860 \
    RF_GRADIO_SHARE="$RF_GRADIO_SHARE_VALUE" \
    PYTHONUNBUFFERED=1 \
    "$RF_INTEL_PYTHON" -u -m rf_platform.dashboard.main
wait_http dashboard http://127.0.0.1:7860 60 2

start_service \
  ask-rf \
  "rf_platform.ask_rf.main" \
  env \
    PYTHONPATH="$PYTHONPATH_VALUE" \
    RF_PLATFORM_URL=http://127.0.0.1:8000 \
    RF_ASK_RF_HOST=0.0.0.0 \
    RF_ASK_RF_PORT=7861 \
    RF_GRADIO_SHARE="$RF_GRADIO_SHARE_VALUE" \
    PYTHONUNBUFFERED=1 \
    "$RF_INTEL_PYTHON" -u -m rf_platform.ask_rf.main
wait_http ask-rf http://127.0.0.1:7861 60 2

log "live stack status"
status=0
if [[ "$RF_VLLM_MANAGED_VALUE" == "true" ]]; then
  show_service vllm "$RF_VLLM_BIN serve $RF_RFGPT_MODEL_PATH" || status=1
else
  printf '%-16s READY (external: %s)\n' "vllm" "$RF_RFGPT_ENDPOINT_VALUE"
fi
show_service api "rf_platform.backend.main" || status=1
show_service worker "rf_platform.worker.main" || status=1
show_service scanner "rf_platform.sensor_agent.main --scan" || status=1
show_service dashboard "rf_platform.dashboard.main" || status=1
show_service ask-rf "rf_platform.ask_rf.main" || status=1

printf '%-16s %s\n' "Command Center" "http://127.0.0.1:7860"
printf '%-16s %s\n' "Ask RF local" "http://127.0.0.1:7861"

if [[ "$RF_GRADIO_SHARE_VALUE" == "true" ]]; then
  show_gradio_public_url dashboard "Command Center public" || status=1
  show_gradio_public_url ask-rf "Ask RF public" || status=1
fi

log "logs and PID files: $RUNTIME_DIR"
exit "$status"
