#!/bin/bash
# Dev server for this codebase only. Usage: scripts/dev_serve.sh [port] [--live] [extra serve args]
#   default  browse/replay mode (no worker, no key)
#   --live   live mode while the worker in .state/worker.json (scripts/live_worker.slurm) answers, and the
#            API key from $LIVE_SECRETS (set in scripts/site.env) or .env / the environment, loaded into this
#            process only; nothing is printed or written.
cd "$(dirname "$0")/.."
export PATIENTTRACE_DATA="$PWD/data-local" PATIENTTRACE_HOME="$PWD/.state"
PORT="${1:-18880}"; shift || true
if [ "${1:-}" = "--live" ]; then
  shift
  # live mode follows the worker job: on while the worker in .state/worker.json answers, browse mode otherwise
  export PATIENTTRACE_WORKER_FILE="$PWD/.state/worker.json"
  [ -f scripts/site.env ] && { set -a; . scripts/site.env; set +a; }
  S="${LIVE_SECRETS:-}"
  [ -n "$S" ] && [ -f "$S" ] && { set -a; . "$S"; set +a; }
  if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -n "${AZURE_FOUNDRY_API_KEY:-}" ]; then
    export ANTHROPIC_API_KEY="$AZURE_FOUNDRY_API_KEY" ANTHROPIC_BASE_URL="https://${AZURE_FOUNDRY_RESOURCE}.services.ai.azure.com/anthropic/"
  fi
  echo "Live mode when the GPU worker runs (scripts/live_mode.sh start)${S:+; API key loaded from $(basename "$S")}."
fi
exec .venv/bin/patienttrace serve --port "$PORT" "$@"
