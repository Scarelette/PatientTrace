#!/bin/bash
#SBATCH --job-name=patienttrace-worker
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=.state/logs/worker_%j.out
# Patient-model GPU worker for live mode, as a Slurm job (submit from the repo root, or use scripts/live_mode.sh
# start, which also passes account / partition / QoS from scripts/site.env). A full GPU is billed while it runs,
# busy or not: it exits after WORKER_IDLE_EXIT_S (default 20 min) without a patient request (never during a demo
# period, see scripts/live_mode.sh), and at --time. The app finds it through .state/worker.json.
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")/..}"
[ -f scripts/site.env ] && { set -a; . scripts/site.env; set +a; }
[ -n "${PT_LD_LIBRARY_PATH:-}" ] && export LD_LIBRARY_PATH="$PT_LD_LIBRARY_PATH${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
[ -n "${PT_HF_HOME:-}" ] && export HF_HOME="$PT_HF_HOME"
export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-8}
export PATIENTTRACE_DATA="${PATIENTTRACE_DATA:-$PWD/data-local}" PATIENTTRACE_HOME="${PATIENTTRACE_HOME:-$PWD/.state}" PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
export WORKER_IDLE_EXIT_S=${WORKER_IDLE_EXIT_S:-1200} DEMO_WORKER_STATE="$PATIENTTRACE_HOME/worker.json"
exec "${PT_WORKER_PYTHON:-python}" -m patienttrace.stage1_worker --host "$(hostname -s)" --port "${PT_WORKER_PORT:-18930}"
