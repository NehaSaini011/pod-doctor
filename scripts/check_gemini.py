"""
Quick health check for your Gemini setup (takes under a minute):

    python scripts/check_gemini.py

It checks the API key, lists the Flash models your key can actually use, and times a
tiny request to each model the agent is configured to use (GEMINI_MODEL + FALLBACK_MODELS).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from pod_doctor import config  # noqa: E402

if not config.GEMINI_API_KEY:
    sys.exit("No GEMINI_API_KEY found. Put it in the .env file in the project folder.")
print(f"API key: found (ends with ...{config.GEMINI_API_KEY[-4:]})")
print(f"Configured models, in order: {', '.join(config.MODELS)}\n")

client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=30_000))

print("Flash models your key can use:")
try:
    names = sorted(
        m.name.removeprefix("models/")
        for m in client.models.list()
        if "flash" in m.name and "generateContent" in (m.supported_actions or [])
    )
    for name in names:
        print(f"  - {name}")
except Exception as exc:
    print(f"  Could not list models: {type(exc).__name__}: {exc}")

print("\nTiming a tiny request to each configured model (30s limit each):")
for model in config.MODELS:
    t0 = time.time()
    try:
        r = client.models.generate_content(model=model, contents="Reply with just the word OK.")
        print(f"  OK     {model:28s} {time.time() - t0:5.1f}s  -> {(r.text or '').strip()[:20]}")
    except Exception as exc:
        print(f"  FAILED {model:28s} {time.time() - t0:5.1f}s  -> {type(exc).__name__}: {str(exc)[:120]}")

print("\nPut a model that says OK (and is fast) as GEMINI_MODEL in .env, then restart Streamlit.")
