# Live mode (Interview and Formulation Contrast)

## What it needs

| Piece | What | Where |
|---|---|---|
| Patient model | Angel-Actor (Qwen3-8B) on vLLM, in the GPU worker (`patienttrace.stage1_worker`) | Full H200 job (`scripts/live_worker.slurm`); a MIG slice is too small. Bills ~$0.90/h while up, busy or not. |
| API key | Alignment, guidance, gists, AI reading, learning point (Claude), embeddings | `ANTHROPIC_API_KEY` in `.env` / the environment, or a key file named by `LIVE_SECRETS` in `scripts/site.env` (an Azure Foundry key is mapped at startup) |
| Env | `ANGEL_WORKER_URL` (or `serve --start-worker`), `ANTHROPIC_API_KEY` (+ `ANTHROPIC_BASE_URL` for Foundry), `PATIENTTRACE_DATA`, `PATIENTTRACE_HOME` | `.env` is gitignored; keys are never printed or written by the scripts |

Without them the page stays in browse/replay mode and says exactly what is missing ("Live chat is off on this server: …").

## Run it here

```bash
sbatch scripts/live_worker.slurm          # H200 worker; exits after 20 min idle (WORKER_IDLE_EXIT_S) or 2 h
scripts/dev_serve.sh 18880 --live         # login node; waits for nothing: run it once the worker job is up
scancel <jobid>                           # when done (or let the idle exit stop it)
```

Site settings (Slurm account / partition / QoS, the worker's Python, model cache, key file) go in `scripts/site.env`; see `scripts/site.env.example`.
Without a GPU: `scripts/mock_worker.py` speaks the worker protocol with recorded replies (`live_mock.slurm`).

## How a turn runs

- **Interview**: `/api/chat/stream` streams the patient reply (NDJSON, cumulative text). When it finishes, the
  composer is free again; `/api/ext/interview/turn` aligns the reply and writes the guidance in the background
  ("Analyzing…" in the left panel), then the reply is pinned and the network animates as in the replay.
  Sessions are saved after each analysed turn to `.state/sessions/interview/<pid>_<sid>.json` (recorded format).
- **Formulation Contrast**: `/api/ext/fc/live/start` creates both patient sessions; `/api/ext/fc/live/ask` streams
  both replies at once (the worker decodes them in one batch); then `/api/ext/fc/turn` aligns both answers and
  computes the gap, gists, Both said check and AI reading. "Finish and compare" calls `/api/ext/fc/learning`.
  Rounds are saved to `.state/sessions/fc/fc.live-<id>.json`.
- **Robustness**: 60 s without output = timeout (`PATIENTTRACE_STREAM_TIMEOUT`); one automatic retry if nothing
  was shown yet; then an inline error with Retry and the question back in the box. Stop cancels the stream on
  the GPU (`/api/ext/live/stop`). A conversation only advances when its reply finished. Alignment, guidance and
  FC analysis are cached per (patient, turn text).
- **Latency log**: `.state/logs/live_latency.jsonl` (per step: patient_reply with first-token / generation time,
  tokens and GPU use; alignment; guidance; analysis; learning). Summary: `scripts/live_latency_report.py`.

## Deployed site: keeping the patient model available (Slurm clusters)

A public copy (`scripts/serve_public.sh`, served at `<host>/patient-trace/` behind a reverse proxy or tunnel) is kept up
by a site watchdog that also runs `scripts/worker_keepalive.py` every minute. Live mode follows the worker
job: the pages poll `/api/live-status` and switch between **Live · ready**, **Warming up / Starting the patient
model…** (a question asked meanwhile is held and sent when it is ready) and **Live interview unavailable right now**
(input off, "Watch a recorded example" offered), without a reload.

A worker holds a full H200 (~$0.90/h) while it runs, busy or not, so it is not kept up around the clock:

| Control | What it does |
|---|---|
| `scripts/live_mode.sh demo on 3` | Demo period of 3 h: the watchdog (every minute) keeps exactly one worker job and submits its replacement 15 min before the time limit (2 h), so there is no gap; idle exit is suspended until the period ends. `demo off` ends it (the worker then stops after 20 idle minutes). |
| `scripts/live_mode.sh autostart on` | When a visitor opens a live page and no worker job runs, the server submits one: at most one job at a time, one submission per 5 min, 8 a day (`PATIENTTRACE_AUTOSTART_GAP_S`, `PATIENTTRACE_AUTOSTART_DAILY`). First reply about a minute after opening the page if a GPU is free. `autostart off` disables it. |
| `scripts/live_mode.sh start` / `stop` / `status` | Submit a worker now / cancel all worker jobs / show jobs, demo period, autostart and today's submissions. |

Keep-alive: `scripts/worker_keepalive.py` (called by the watchdog; does nothing outside demo periods).
Submissions are logged to `.state/logs/worker_jobs.jsonl`, keep-alive actions to `.state/logs/keepalive.log`.
`/api/live-status` adds an `admin` block (worker jobs with id, start time, time limit and time left; autostart and
demo state; how to start a worker) only for direct requests (on the node or through an SSH tunnel), never for
requests through Cloudflare. `/api/health` never touches the model.
