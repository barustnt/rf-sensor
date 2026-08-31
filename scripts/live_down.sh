#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${RF_LIVE_RUNTIME_DIR:-${REPO_ROOT}/.data/runtime}"
COMPOSE_FILE="${REPO_ROOT}/deploy/docker-compose.infra.yml"
COMPOSE_PROJECT="rf-sensor"
RF_VLLM_BIN="${RF_VLLM_BIN:-/home/user/miniconda3/envs/vllm-env/bin/vllm}"
RF_RFGPT_MODEL_PATH="${RF_RFGPT_MODEL_PATH:-/home/user/models/Qwen2.5-VL-7B-rfa-wtr-v2-joint}"

log() {
  printf '[live-down] %s\n' "$*"
}

cmdline_for_pid() {
  local pid="$1"
  tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true
}

stop_service() {
  local name="$1"
  local expected="$2"
  local pid_file="${RUNTIME_DIR}/${name}.pid"
  local pid
  local cmdline
  local attempt

  if [[ ! -f "$pid_file" ]]; then
    log "$name is not managed by live_up.sh"
    return
  fi
  pid="$(<"$pid_file")"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    log "refusing invalid PID file: $pid_file"
    return 1
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pid_file"
    log "$name already stopped"
    return
  fi
  cmdline="$(cmdline_for_pid "$pid")"
  if [[ "$cmdline" != *"$expected"* ]]; then
    log "refusing to stop PID $pid: command does not match '$expected'"
    return 1
  fi

  log "stopping $name (PID $pid)"
  kill -TERM "$pid"
  for attempt in $(seq 1 40); do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pid_file"
      log "$name stopped"
      return
    fi
    sleep 0.5
  done

  cmdline="$(cmdline_for_pid "$pid")"
  if [[ "$cmdline" == *"$expected"* ]]; then
    log "$name did not stop after 20 seconds; sending KILL to validated PID $pid"
    kill -KILL "$pid"
    rm -f "$pid_file"
    return
  fi
  log "refusing KILL because PID $pid no longer matches '$expected'"
  return 1
}

status=0
stop_service scanner "rf_platform.sensor_agent.main --scan" || status=1
stop_service worker "rf_platform.worker.main" || status=1
stop_service ask-rf "rf_platform.ask_rf.main" || status=1
stop_service dashboard "rf_platform.dashboard.main" || status=1
stop_service api "rf_platform.backend.main" || status=1
stop_service vllm "$RF_VLLM_BIN serve $RF_RFGPT_MODEL_PATH" || status=1

if [[ "${RF_LIVE_KEEP_INFRA:-false}" == "true" ]]; then
  log "leaving PostgreSQL and NATS running because RF_LIVE_KEEP_INFRA=true"
else
  log "stopping PostgreSQL and NATS without deleting volumes"
  docker compose \
    -f "$COMPOSE_FILE" \
    --project-name "$COMPOSE_PROJECT" \
    down
fi

log "operational PostgreSQL volume was preserved; no -v flag was used"
exit "$status"
