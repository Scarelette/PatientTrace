#!/bin/bash
# Live-mode controls for the deployed copy (and the dev server). Works from any login node (files on GPFS + Slurm).
#   scripts/live_mode.sh status              worker jobs, demo period, autostart, today's submissions
#   scripts/live_mode.sh demo on [HOURS]     keep one worker up for HOURS (default 3): the watchdog resubmits it
#   scripts/live_mode.sh demo off            end the demo period (the worker then stops after 20 idle minutes)
#   scripts/live_mode.sh autostart on|off    start a worker when a visitor opens a live page (rate-limited, daily cap)
#   scripts/live_mode.sh start | stop        submit a worker now | cancel all worker jobs
# Cost: a worker holds a full H200 (~$0.90/h) while it runs, busy or not.
cd "$(dirname "$0")/.."
D=.state/live; mkdir -p "$D"
case "${1:-status}" in
  demo)
    if [ "${2:-}" = "on" ]; then H="${3:-3}"; echo $(( $(date +%s) + ${H%.*} * 3600 )) > "$D/demo_until"; echo "demo period on until $(date -d @$(cat $D/demo_until) '+%F %T') (watchdog keeps one worker; first one within a minute)"
    elif [ "${2:-}" = "off" ]; then rm -f "$D/demo_until"; echo "demo period off"; else echo "usage: demo on [HOURS] | demo off"; exit 1; fi ;;
  autostart)
    case "${2:-}" in on|off) echo "$2" > "$D/autostart"; echo "autostart $2" ;; *) echo "usage: autostart on|off"; exit 1 ;; esac ;;
  start) mkdir -p .state/logs; ARGS=$(python3 -c "import sys; sys.path.insert(0, '.'); from patienttrace.live import sbatch_args; print(' '.join(sbatch_args()))" 2>/dev/null || true)
         sbatch $ARGS scripts/live_worker.slurm ;;
  stop) squeue -h -u "$USER" -n patienttrace-worker -o %i | xargs -r scancel; echo "worker jobs cancelled" ;;
  status)
    echo "worker jobs:"; squeue -u "$USER" -n patienttrace-worker -o "  %.10i %.9T %.20S %.10l %.10L %N"
    du=$(cat "$D/demo_until" 2>/dev/null); [ -n "$du" ] && [ "$du" -gt "$(date +%s)" ] && echo "demo period: on until $(date -d @$du '+%F %T')" || echo "demo period: off"
    echo "autostart: $(cat "$D/autostart" 2>/dev/null || echo off)"
    echo "submissions today: $(grep -c "\"day\": \"$(date +%F)\"" .state/logs/worker_jobs.jsonl 2>/dev/null || echo 0)" ;;
  *) sed -n 2,9p "$0"; exit 1 ;;
esac
