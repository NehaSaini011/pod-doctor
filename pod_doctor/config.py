"""All settings in one place. Values come from the .env file (copy .env.example -> .env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _extend_windows_path():
    """Docker Desktop and winget install docker/kind/kubectl in standard folders that
    Git Bash and VS Code terminals often don't search. Add them so we always find them."""
    if os.name != "nt":
        return
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "resources" / "bin",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
    ]
    path = os.environ.get("PATH", "")
    for folder in candidates:
        if folder.is_dir() and str(folder).lower() not in path.lower():
            path += os.pathsep + str(folder)
    os.environ["PATH"] = path


_extend_windows_path()


def _get(name, default=""):
    """Read a setting from the environment / .env, or from Streamlit secrets when deployed."""
    value = os.getenv(name)
    if value is None:
        try:
            import streamlit as st

            value = st.secrets.get(name)
        except Exception:  # no secrets file, or not running under Streamlit
            value = None
    return str(value if value is not None else default).strip()


GEMINI_API_KEY = _get("GEMINI_API_KEY", "")
GEMINI_MODEL = _get("GEMINI_MODEL", "gemini-3.6-flash")
# Backup models tried in order if the main one is busy, rate-limited or not found.
FALLBACK_MODELS = [m.strip() for m in _get("FALLBACK_MODELS", "gemini-3.8-flash,gemini-3.5-flash-lite").split(",") if m.strip()]
MODELS = [GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != GEMINI_MODEL]
EMBED_MODEL = _get("EMBED_MODEL", "gemini-embedding-001")

# Which cluster and namespace the agent is allowed to look at.
KUBE_CONTEXT = _get("KUBE_CONTEXT", "kind-pod-doctor")
DEFAULT_NAMESPACE = _get("NAMESPACE", "shop")

# Safety limits for the agent loop.
MAX_AGENT_STEPS = int(_get("MAX_AGENT_STEPS", "15"))   # model turns
MAX_TOOL_OUTPUT_CHARS = 6000                                # truncate huge kubectl output

# "live"     -> run real kubectl against KUBE_CONTEXT (your laptop)
# "snapshot" -> answer from a recorded snapshot of the cluster (the deployed website)
CLUSTER_MODE = _get("CLUSTER_MODE", "live").lower()
SNAPSHOT_FILE = ROOT / "data" / "cluster_snapshot.json"

# Protection for the public website (set these as secrets when deploying).
APP_PASSWORD = _get("APP_PASSWORD", "")           # empty = no password
MAX_RUNS_PER_SESSION = int(_get("MAX_RUNS_PER_SESSION", "5"))

KNOWLEDGE_BASE_DIR = ROOT / "knowledge_base"
CACHE_DIR = ROOT / ".cache"
RUNS_DIR = ROOT / "runs"
