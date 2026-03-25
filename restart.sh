#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[restart] Restarting local AI Novel services..."
"$ROOT/stop.sh"
"$ROOT/start.sh"
echo "[done] Restart complete."
