#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT/logs"
BACKEND_PID_FILE="$LOG_DIR/backend.pid"
FRONTEND_PID_FILE="$LOG_DIR/frontend.pid"
BACKEND_PORT="${BACKEND_PORT:-8899}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

is_running() {
  local pid="$1"
  kill -0 "$pid" >/dev/null 2>&1
}

stop_by_pid_file() {
  local service="$1"
  local pid_file="$2"
  local stopped=1

  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if [[ -n "$pid" ]] && is_running "$pid"; then
      echo "[step] Stopping $service (PID $pid)..."
      kill "$pid" >/dev/null 2>&1 || true

      local i
      for i in {1..10}; do
        if ! is_running "$pid"; then
          break
        fi
        sleep 1
      done

      if is_running "$pid"; then
        echo "[warn] $service did not stop with SIGTERM, sending SIGKILL (PID $pid)"
        kill -9 "$pid" >/dev/null 2>&1 || true
      fi
      echo "[ok] $service stopped"
      stopped=0
    else
      echo "[info] $service already stopped (stale pid file)"
    fi
    rm -f "$pid_file"
  fi

  return "$stopped"
}

stop_by_port_fallback() {
  local service="$1"
  local port="$2"
  local pids
  pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "[step] Stopping stray $service process(es) on port $port: $pids"
    kill $pids >/dev/null 2>&1 || true
    sleep 1
    local left
    left="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
    if [[ -n "$left" ]]; then
      kill -9 $left >/dev/null 2>&1 || true
    fi
    echo "[ok] $service fallback stop complete"
  fi
}

main() {
  echo "[stop] Stopping local AI Novel services..."
  mkdir -p "$LOG_DIR"

  stop_by_pid_file "backend" "$BACKEND_PID_FILE" || true
  stop_by_pid_file "frontend" "$FRONTEND_PID_FILE" || true

  stop_by_port_fallback "backend" "$BACKEND_PORT"
  stop_by_port_fallback "frontend" "$FRONTEND_PORT"

  echo "[done] All services stopped."
}

main "$@"
