"""`angel-demo` command line.

  angel-demo download-data [--source SRC] [--dest DIR]   fetch the patient data bundle
  angel-demo check                                         report what is configured
  angel-demo worker [--port 7900] [--gpu-mem 0.85]         GPU worker for the patient model
  angel-demo serve  [--port 7860] [--worker-url URL | --start-worker]
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile

# Where `download-data` fetches from when --source is not given. "hf://<user>/<dataset>" (a private
# Hugging Face dataset holding angel-demo-data.tar.gz; read with HF_TOKEN), an https URL to a .tar.gz/.zip, or a local file.
DEFAULT_DATA_SOURCE = os.environ.get("ANGEL_DEMO_DATA_SOURCE", "hf://Cheng228/PatientTrace-Data")
ARCHIVE_NAME = "angel-demo-data.tar.gz"

# Defaults the demo was served with (scripts/serve_ext.sh in the research repo).
SERVE_ENV = {
    "DEMO_FEAT_PRECOMPUTED": "1", "DEMO_FEAT_ALIGN": "1", "DEMO_FEAT_CONTRAST": "1", "DEMO_FEAT_DUAL": "1",
    "DEMO_FEAT_CUSTOM": "0",
    "DEMO_EXT_ALIGN_TIMEOUT_S": "10", "F3_ALIGN_TIMEOUT_S": "20", "F3_DIFF_TIMEOUT_S": "45",
    "PATIENT_MODEL_TIMEOUT_S": "90", "DEMO_WORKER_HTTP_TIMEOUT": "90", "DEMO_THREADPOOL": "200",
}


def _load_dotenv() -> None:
    """Read KEY=VALUE lines from ./.env (never overrides variables already set)."""
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# --------------------------------------------------------------------------- download-data
def _extract(archive: str, dest: str) -> None:
    os.makedirs(dest, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            try:
                t.extractall(dest, filter="data")
            except TypeError:          # Python < 3.12 without the filter argument
                t.extractall(dest)
    # The archive may wrap everything in one top folder: flatten it.
    entries = [e for e in os.listdir(dest) if not e.startswith(".")]
    if len(entries) == 1 and os.path.isdir(os.path.join(dest, entries[0])) and \
            os.path.exists(os.path.join(dest, entries[0], "patients_adult.jsonl")):
        inner = os.path.join(dest, entries[0])
        for e in os.listdir(inner):
            shutil.move(os.path.join(inner, e), os.path.join(dest, e))
        os.rmdir(inner)


def cmd_download(args) -> int:
    from angel_demo import paths as P
    dest = os.path.abspath(os.path.expanduser(args.dest or P.DATA_DIR))
    src = args.source or DEFAULT_DATA_SOURCE
    if os.path.exists(os.path.join(dest, "patients_adult.jsonl")) and not args.force:
        print(f"Data already present in {dest} (use --force to replace it).")
        return 0
    tmp = tempfile.mkdtemp(prefix="angel-demo-dl-")
    try:
        if src.startswith("hf://"):
            try:
                from huggingface_hub import hf_hub_download
            except ImportError:
                print("huggingface_hub is required: pip install huggingface_hub", file=sys.stderr)
                return 1
            repo = src[len("hf://"):]
            print(f"Downloading {ARCHIVE_NAME} from the Hugging Face dataset {repo} ...")
            try:
                archive = hf_hub_download(repo_id=repo, filename=ARCHIVE_NAME, repo_type="dataset",
                                          local_dir=tmp)
            except Exception as exc:  # noqa: BLE001
                print(f"Download failed: {exc}\n\nThe dataset {repo} is private. Set HF_TOKEN to the access "
                      "token given with the paper submission and run this command again.", file=sys.stderr)
                return 1
        elif src.startswith(("http://", "https://")):
            import requests
            archive = os.path.join(tmp, "data.archive")
            print(f"Downloading {src} ...")
            with requests.get(src, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(archive, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
        else:
            archive = os.path.abspath(os.path.expanduser(src))
            if not os.path.exists(archive):
                print(f"No such file: {archive}", file=sys.stderr)
                return 1
        if os.path.exists(dest) and args.force:
            shutil.rmtree(dest)
        print(f"Extracting into {dest} ...")
        _extract(archive, dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not os.path.exists(os.path.join(dest, "patients_adult.jsonl")):
        print(f"The archive did not contain patients_adult.jsonl; check {dest}", file=sys.stderr)
        return 1
    if args.dest and os.path.abspath(args.dest) != P.DATA_DIR:
        print(f"Done. Set ANGEL_DEMO_DATA={dest} before `angel-demo serve`.")
    else:
        print("Done.")
    return 0


# --------------------------------------------------------------------------- check
def cmd_check(args) -> int:
    _load_dotenv()
    from angel_demo import paths as P
    ok = True
    print(f"data folder   {P.DATA_DIR}  ->  {'OK' if P.data_ready() else 'MISSING (run angel-demo download-data)'}")
    ok &= P.data_ready()
    print(f"state folder  {P.HOME_DIR}")
    key = "ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY") else (
        "AZURE_FOUNDRY_API_KEY" if os.environ.get("AZURE_FOUNDRY_API_KEY") else None)
    print(f"API key       {key + ' set' if key else 'not set (live alignment and summaries off)'}")
    url = os.environ.get("ANGEL_WORKER_URL")
    if url:
        import requests
        try:
            up = requests.get(url.rstrip('/') + "/health", timeout=4).ok
        except Exception:  # noqa: BLE001
            up = False
        print(f"GPU worker    {url}  ->  {'reachable' if up else 'NOT reachable'}")
    else:
        print("GPU worker    not configured (browse/replay mode)")
    try:
        import vllm  # noqa: F401
        print("vllm          installed (this machine can run `angel-demo worker`)")
    except Exception:  # noqa: BLE001
        print("vllm          not installed (only needed on the GPU machine: pip install 'angel-demo[gpu]')")
    return 0 if ok else 1


# --------------------------------------------------------------------------- worker
def cmd_worker(args) -> int:
    _load_dotenv()
    if args.gpu_mem is not None:
        os.environ["VLLM_GPU_MEM"] = str(args.gpu_mem)
    if args.model:
        os.environ["ROLEPLAY_MODEL_PATH"] = args.model
    os.environ.setdefault("WORKER_PRELOAD", "chat")
    from angel_demo import stage1_worker
    sys.argv = [sys.argv[0], "--host", args.host, "--port", str(args.port)]
    stage1_worker.main()
    return 0


# --------------------------------------------------------------------------- serve
def cmd_serve(args) -> int:
    _load_dotenv()
    from angel_demo import paths as P
    if not P.data_ready():
        print(f"No patient data in {P.DATA_DIR}.\nRun `angel-demo download-data` first "
              "(or set ANGEL_DEMO_DATA to the folder that holds it).", file=sys.stderr)
        return 1
    for k, v in SERVE_ENV.items():
        os.environ.setdefault(k, v)
    if args.base_path:
        os.environ["DEMO_BASE_PATH"] = args.base_path
    if args.worker_url:
        os.environ["ANGEL_WORKER_URL"] = args.worker_url
    os.makedirs(P.LOG_DIR, exist_ok=True)
    from angel_demo import gpu_manager
    if args.start_worker:
        url = gpu_manager.start_local_worker(args.worker_port, args.gpu_mem,
                                             os.path.join(P.LOG_DIR, "worker.log"))
        print(f"Starting the GPU worker on this machine ({url}); log: {os.path.join(P.LOG_DIR, 'worker.log')}")
        print("The first live reply waits until the patient model has loaded (a few minutes on first use).")
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AZURE_FOUNDRY_API_KEY"))
    if gpu_manager.live_enabled() and has_key:
        print("Mode: LIVE (interview and compare with the patient model)")
    else:
        why = [] if gpu_manager.live_enabled() else ["no GPU worker"]
        if not has_key:
            why.append("no ANTHROPIC_API_KEY")
        print(f"Mode: BROWSE/REPLAY ({', '.join(why)}). Networks, contrast patients and recorded examples work.")
    import atexit
    atexit.register(gpu_manager.shutdown)
    from angel_demo import demo_app
    # uvicorn re-raises Ctrl+C / SIGTERM after shutting down, so atexit may never run: stop the
    # worker in the app's own shutdown as well (the worker also dies with this process on Linux).
    demo_app.app.on_event("shutdown")(gpu_manager.shutdown)
    sys.argv = [sys.argv[0], "--host", args.host, "--port", str(args.port)]
    demo_app.main()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="angel-demo", description="ANGEL patient-simulation demo")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("download-data", help="download and unpack the patient data bundle")
    d.add_argument("--source", help=f"hf://<user>/<dataset>, an https URL or a local archive (default {DEFAULT_DATA_SOURCE})")
    d.add_argument("--dest", help="target folder (default $ANGEL_DEMO_DATA or ~/.cache/angel-demo/data)")
    d.add_argument("--force", action="store_true", help="replace existing data")
    d.set_defaults(fn=cmd_download)

    c = sub.add_parser("check", help="show data, key and worker status")
    c.set_defaults(fn=cmd_check)

    w = sub.add_parser("worker", help="run the GPU worker that hosts the ANGEL patient model")
    w.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 only when the app runs on another machine")
    w.add_argument("--port", type=int, default=7900)
    w.add_argument("--gpu-mem", type=float, default=None, help="fraction of GPU memory for vLLM (default 0.85)")
    w.add_argument("--model", help="patient model: Hugging Face id or local folder (default ChengLi0228/Angel-Actor)")
    w.set_defaults(fn=cmd_worker)

    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=7860)
    s.add_argument("--worker-url", help="URL of a running `angel-demo worker` (enables live mode)")
    s.add_argument("--start-worker", action="store_true", help="also start the GPU worker on this machine")
    s.add_argument("--worker-port", type=int, default=7900)
    s.add_argument("--gpu-mem", type=float, default=None)
    s.add_argument("--base-path", default="", help="serve under a URL prefix, e.g. /demo")
    s.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
