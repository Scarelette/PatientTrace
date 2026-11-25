"""Where the demo reads its data and writes its state.

  ANGEL_DEMO_DATA  folder holding the downloaded data bundle (`angel-demo download-data`)
                   default: ~/.cache/angel-demo/data
  ANGEL_DEMO_HOME  writable folder for logs, the LLM response cache and the worker state file
                   default: ~/.cache/angel-demo/state
"""
import os

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(PKG_DIR, "static")
DATA_DIR = os.path.abspath(os.path.expanduser(os.environ.get("ANGEL_DEMO_DATA", "~/.cache/angel-demo/data")))
HOME_DIR = os.path.abspath(os.path.expanduser(os.environ.get("ANGEL_DEMO_HOME", "~/.cache/angel-demo/state")))
LOG_DIR = os.path.join(HOME_DIR, "logs")
WORKER_STATE = os.environ.get("DEMO_WORKER_STATE", os.path.join(HOME_DIR, "worker.json"))


def data(*parts: str) -> str:
    return os.path.join(DATA_DIR, *parts)


def state(*parts: str) -> str:
    return os.path.join(HOME_DIR, *parts)


def data_ready() -> bool:
    return os.path.exists(data("patients_adult.jsonl")) and os.path.isdir(data("precomputed"))
