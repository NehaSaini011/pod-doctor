"""Terminal version of the agent (handy for testing):

    python -m pod_doctor.cli "Diagnose all unhealthy pods in the shop namespace"
"""
import json
import sys

from .agent import run_agent


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows terminals default to cp1252
    question = " ".join(sys.argv[1:]) or "Diagnose all unhealthy pods in the shop namespace."
    print(f"\nQuestion: {question}\n" + "-" * 70)
    for ev in run_agent(question):
        t = ev["type"]
        if t == "thought":
            print(f"\n[think] {ev['text'][:500]}")
        elif t == "tool_call":
            print(f"\n[step {ev['step']}] -> {ev['name']}({json.dumps(ev['args'])})")
        elif t == "tool_result":
            preview = json.dumps(ev["result"])[:300]
            print(f"   <- {'ok' if ev['ok'] else 'ERROR'} ({ev['ms']} ms): {preview}...")
        elif t == "final":
            print("\n" + "=" * 70 + "\n" + ev["text"])
        elif t == "citation_check":
            status = "UNVERIFIED: " + ", ".join(ev["unverified"]) if ev["unverified"] else "all citations verified"
            print(f"\n[citation check] cited {ev['cited']} -> {status}")
        elif t == "notice":
            print(f"   [notice] {ev['text']}")
        elif t == "error":
            print(f"\n[error] {ev['text']}")
        elif t == "stats":
            print(f"\n[{ev['tool_calls']} tool calls, {ev['seconds']}s, model {ev['model']}]")


if __name__ == "__main__":
    main()
