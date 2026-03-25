#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT/logs"
DATA_DIR="$ROOT/data"
BACKEND_PID_FILE="$LOG_DIR/backend.pid"
FRONTEND_PID_FILE="$LOG_DIR/frontend.pid"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8899}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/.uv-cache}"
AI_NOVEL_LLM_TIMEOUT_SECONDS="${AI_NOVEL_LLM_TIMEOUT_SECONDS:-600}"
AI_NOVEL_REWRITE_WINDOW_MODE_ENABLED="${AI_NOVEL_REWRITE_WINDOW_MODE_ENABLED:-true}"
AI_NOVEL_REWRITE_WINDOW_MODE_GUARDRAIL_ENABLED="${AI_NOVEL_REWRITE_WINDOW_MODE_GUARDRAIL_ENABLED:-true}"
AI_NOVEL_REWRITE_WINDOW_MODE_AUDIT_ENABLED="${AI_NOVEL_REWRITE_WINDOW_MODE_AUDIT_ENABLED:-true}"
STARTED_BACKEND=0
STARTED_FRONTEND=0

ensure_prerequisites() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "[error] uv not found. Install uv first: https://docs.astral.sh/uv/"
    exit 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "[error] npm not found."
    exit 1
  fi
}

is_running() {
  local pid="$1"
  kill -0 "$pid" >/dev/null 2>&1
}

cleanup_stale_pid() {
  local pid_file="$1"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if [[ -n "$pid" ]] && is_running "$pid"; then
      echo "[warn] Service already running (PID $pid): $pid_file"
      return 1
    fi
    rm -f "$pid_file"
  fi
  return 0
}

stop_pid_file_quietly() {
  local pid_file="$1"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if [[ -n "$pid" ]] && is_running "$pid"; then
      kill "$pid" >/dev/null 2>&1 || true
      sleep 1
      if is_running "$pid"; then
        kill -9 "$pid" >/dev/null 2>&1 || true
      fi
    fi
    rm -f "$pid_file"
  fi
}

cleanup_on_error() {
  local code="$1"
  if [[ "$code" -ne 0 ]]; then
    echo "[error] Startup failed. Rolling back started services..."
    if [[ "$STARTED_FRONTEND" -eq 1 ]]; then
      stop_pid_file_quietly "$FRONTEND_PID_FILE"
    fi
    if [[ "$STARTED_BACKEND" -eq 1 ]]; then
      stop_pid_file_quietly "$BACKEND_PID_FILE"
    fi
  fi
}

wait_for_http() {
  local url="$1"
  local name="$2"
  local pid="$3"
  local log_file="$4"
  local timeout_seconds="${5:-20}"
  local elapsed=0

  while (( elapsed < timeout_seconds )); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "[ok] $name ready: $url"
      return 0
    fi
    if ! is_running "$pid"; then
      echo "[error] $name exited before becoming ready (PID $pid)."
      if [[ -f "$log_file" ]]; then
        echo "[error] Last logs from $log_file:"
        tail -n 40 "$log_file" || true
      fi
      return 1
    fi
    sleep 1
    ((elapsed += 1))
  done

  echo "[error] $name did not become ready within ${timeout_seconds}s: $url"
  return 1
}

start_backend() {
  cleanup_stale_pid "$BACKEND_PID_FILE" || return 1

  echo "[step] Starting backend..."
  (
    cd "$ROOT"
    nohup env AI_NOVEL_DATA_DIR="$DATA_DIR" UV_CACHE_DIR="$UV_CACHE_DIR" AI_NOVEL_LLM_TIMEOUT_SECONDS="$AI_NOVEL_LLM_TIMEOUT_SECONDS" \
      AI_NOVEL_REWRITE_WINDOW_MODE_ENABLED="$AI_NOVEL_REWRITE_WINDOW_MODE_ENABLED" \
      AI_NOVEL_REWRITE_WINDOW_MODE_GUARDRAIL_ENABLED="$AI_NOVEL_REWRITE_WINDOW_MODE_GUARDRAIL_ENABLED" \
      AI_NOVEL_REWRITE_WINDOW_MODE_AUDIT_ENABLED="$AI_NOVEL_REWRITE_WINDOW_MODE_AUDIT_ENABLED" \
      uv run uvicorn backend.app.main:app \
        --host "$BACKEND_HOST" \
        --port "$BACKEND_PORT" \
        >"$BACKEND_LOG" 2>&1 < /dev/null &
    echo $! >"$BACKEND_PID_FILE"
  )

  local pid
  pid="$(cat "$BACKEND_PID_FILE")"
  echo "[info] Backend PID: $pid"
  STARTED_BACKEND=1

  wait_for_http "http://127.0.0.1:${BACKEND_PORT}/health" "backend" "$pid" "$BACKEND_LOG" 30
}

start_frontend() {
  cleanup_stale_pid "$FRONTEND_PID_FILE" || return 1

  echo "[step] Starting frontend..."
  (
    cd "$ROOT/frontend"
    nohup npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" >"$FRONTEND_LOG" 2>&1 < /dev/null &
    echo $! >"$FRONTEND_PID_FILE"
  )

  local pid
  pid="$(cat "$FRONTEND_PID_FILE")"
  echo "[info] Frontend PID: $pid"
  STARTED_FRONTEND=1

  wait_for_http "http://127.0.0.1:${FRONTEND_PORT}" "frontend" "$pid" "$FRONTEND_LOG" 30
}

main() {
  trap 'cleanup_on_error $?' EXIT

  ensure_prerequisites
  mkdir -p "$LOG_DIR" "$DATA_DIR" "$UV_CACHE_DIR"

  echo "[start] Launching local AI Novel services..."
  start_backend
  start_frontend

  echo "[done] Services are running."
  echo "        Frontend: http://127.0.0.1:${FRONTEND_PORT}"
  echo "        Backend : http://127.0.0.1:${BACKEND_PORT}/api/v1"
  echo "        Logs    : $LOG_DIR"

  trap - EXIT
}

main "$@"
