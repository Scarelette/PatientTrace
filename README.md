# PatientTrace

Interview a simulated mental-health patient and watch each reply being traced onto the patient's
symptom network, find a contrast patient whose symptoms look similar but are caused differently,
and compare two patients side by side.

The demo runs in two modes:

| Mode | What works | Needs |
|---|---|---|
| **Browse / replay** | 330 patient profiles and their symptom networks, contrast-patient recommendations, recorded side-by-side interviews | Python 3.10+ only (no GPU, no API key) |
| **Live** | Everything above, plus live interviews with the patient model, reply tracing, and live side-by-side comparison | One NVIDIA GPU for the patient model and an Anthropic API key |

## 1. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install git+https://github.com/Scarelette/PatientTrace.git
```

## 2. Get the patient data

The patient data (synthetic profiles, networks and recorded examples, about 5 MB) is a private
Hugging Face dataset. Use the access token given with the paper submission:

```bash
export HF_TOKEN=<access token from the submission>
angel-demo download-data         # unpacks into ~/.cache/angel-demo/data
```

To keep the data elsewhere, pass `--dest DIR` and set `ANGEL_DEMO_DATA=DIR` before serving.

## 3. Run

### Browse / replay mode

```bash
angel-demo serve
```

Open http://localhost:7860. Pick **Use an existing profile**, choose a patient, then
**Find a contrast patient**, or click **See an example** on the start page for a recorded
side-by-side interview.

### Live mode

Live mode adds the patient model ([ChengLi0228/Angel-Actor](https://huggingface.co/ChengLi0228/Angel-Actor),
Qwen3-8B, served with vLLM) and Claude for reply tracing and difference summaries.

```bash
pip install "angel-demo[gpu] @ git+https://github.com/Scarelette/PatientTrace.git"
export ANTHROPIC_API_KEY=...       # or put it in a .env file (see .env.example)
angel-demo serve --start-worker
```

The first start downloads the model (about 16 GB) and loads it. The page works right away, and
the first live reply arrives once the model is ready (`~/.cache/angel-demo/state/logs/worker.log`).

The model needs about 17 GB of GPU memory plus room for the cache; we ran it on an H200, and a
24 GB GPU should be enough. If the GPU is on another machine, start the worker there and point
the app at it:

```bash
# on the GPU machine
angel-demo worker --host 0.0.0.0 --port 7900
# on the machine that serves the page
angel-demo serve --worker-url http://<gpu-host>:7900
```

`angel-demo check` shows whether the data, the API key and the worker are found.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Live tracing and difference summaries (Claude Opus 5.5) |
| `METHOD_MODEL` | `claude-opus-5-5` | Model for tracing and summaries |
| `ANGEL_DEMO_DATA` | `~/.cache/angel-demo/data` | Patient data folder |
| `ANGEL_DEMO_HOME` | `~/.cache/angel-demo/state` | Logs and response cache |
| `ANGEL_WORKER_URL` | – | Running worker (same as `--worker-url`) |
| `ROLEPLAY_MODEL_PATH` | `ChengLi0228/Angel-Actor` | Patient model (Hugging Face id or local folder) |
| `VLLM_GPU_MEM` | `0.85` | Share of GPU memory vLLM may use |

## Notes

- The patients are synthetic. The patient model is a research tool for training and studying
  clinical interviews, not a clinical or diagnostic tool.
- Live mode sends interview text to the Anthropic API.
- The app listens on `127.0.0.1` by default. Use `--host 0.0.0.0` only on a trusted network.

## Related

- Patient simulator and training code: [ANGEL-UserSim](https://github.com/Scarelette/ANGEL-UserSim)
- Models: [Angel-Actor](https://huggingface.co/ChengLi0228/Angel-Actor),
  [Angel-Observer](https://huggingface.co/ChengLi0228/Angel-Observer)

## License

Code: [Apache License 2.0](LICENSE). See the model cards for the models' terms.
