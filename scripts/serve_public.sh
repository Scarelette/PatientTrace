#!/bin/bash
# Public copy behind a reverse proxy / tunnel at <host>/patient-trace/ (PT_BASE_PATH), kept up by a site watchdog;
# listens on 127.0.0.1 only.
# Live mode follows the GPU worker job (sbatch scripts/live_worker.slurm writes .state/worker.json): on while it
# answers, browse/replay mode otherwise. The API key is loaded from $LIVE_SECRETS (scripts/site.env) into this
# process only (never printed or written).
# Usage: scripts/serve_public.sh [port]   (default 27895)
cd "$(dirname "$0")/.."
PORT="${1:-27895}"
export PATIENTTRACE_DATA="$PWD/data-local" PATIENTTRACE_HOME="$PWD/.state" DEMO_BASE_PATH="${PT_BASE_PATH:-/patient-trace}"
export PATIENTTRACE_WORKER_FILE="$PWD/.state/worker.json"
[ -f scripts/site.env ] && { set -a; . scripts/site.env; set +a; }
S="${LIVE_SECRETS:-}"
if [ -n "$S" ] && [ -f "$S" ]; then
  set -a; . "$S"; set +a
  if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -n "${AZURE_FOUNDRY_API_KEY:-}" ]; then
    export ANTHROPIC_API_KEY="$AZURE_FOUNDRY_API_KEY" ANTHROPIC_BASE_URL="https://${AZURE_FOUNDRY_RESOURCE}.services.ai.azure.com/anthropic/"
  fi
fi
exec .venv/bin/patienttrace serve --host 127.0.0.1 --port "$PORT"
