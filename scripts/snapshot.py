"""
Record a snapshot of the demo cluster for the deployed website.

Run this on your laptop while the broken cluster is up (python scripts/cluster.py up):

    python scripts/snapshot.py

It runs every read-only kubectl command the agent's tools can use (for every pod,
deployment, service, configmap, secret, endpoints and node in the namespace) and saves
the raw output to data/cluster_snapshot.json. Commit that file; the deployed app answers
the agent's tool calls from it.

Secret VALUES are redacted before saving - only key names are kept.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pod_doctor import config  # noqa: E402
from pod_doctor.k8s_tools import ToolError, _run_kubectl, snapshot_key  # noqa: E402

NS = config.DEFAULT_NAMESPACE
KINDS = ["deployment", "service", "configmap", "secret", "endpoints"]


def sanitise(stdout):
    """Strip noisy metadata and redact secret values from JSON output."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    for obj in data.get("items", [data]):
        meta = obj.get("metadata", {})
        meta.pop("managedFields", None)
        (meta.get("annotations") or {}).pop("kubectl.kubernetes.io/last-applied-configuration", None)
        if obj.get("kind") == "Secret":
            for field in ("data", "stringData"):
                if obj.get(field):
                    obj[field] = {k: "<redacted>" for k in obj[field]}
    return json.dumps(data)


def main(run=_run_kubectl):
    commands = {}

    def record(args):
        key = snapshot_key(args)
        try:
            commands[key] = {"stdout": sanitise(run(args))}
        except ToolError as exc:
            commands[key] = {"error": str(exc)}
        status = "error" if "error" in commands[key] else "ok"
        print(f"  [{status:5s}] kubectl {key}")
        return commands[key]

    print(f"Recording namespace '{NS}' from context '{config.KUBE_CONTEXT}'...")
    pods_entry = record(["get", "pods", "-n", NS, "-o", "json"])
    if "error" in pods_entry:
        sys.exit("Cannot reach the cluster. Start it first: python scripts/cluster.py up")
    pods = [p["metadata"]["name"] for p in json.loads(pods_entry["stdout"])["items"]]

    record(["get", "events", "-n", NS, "-o", "json"])
    for pod in pods:
        record(["describe", "pod", pod, "-n", NS])
        record(["logs", pod, "-n", NS, "--tail=200", "--all-containers=true"])
        record(["logs", pod, "-n", NS, "--tail=200", "--all-containers=true", "--previous"])
        record(["get", "events", "-n", NS, "-o", "json", "--field-selector", f"involvedObject.name={pod}"])

    for kind in KINDS:
        listing = record(["get", kind, "-n", NS, "-o", "json"])
        names = [i["metadata"]["name"] for i in json.loads(listing.get("stdout") or '{"items": []}')["items"]]
        for name in names:
            record(["get", kind, name, "-n", NS, "-o", "json"])
            if kind in ("deployment", "service"):
                record(["get", "events", "-n", NS, "-o", "json", "--field-selector", f"involvedObject.name={name}"])

    nodes = record(["get", "node", "-o", "json"])
    for item in json.loads(nodes.get("stdout") or '{"items": []}')["items"]:
        record(["get", "node", item["metadata"]["name"], "-o", "json"])

    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "context": config.KUBE_CONTEXT,
        "namespace": NS,
        "commands": commands,
    }
    config.SNAPSHOT_FILE.parent.mkdir(exist_ok=True)
    config.SNAPSHOT_FILE.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
    print(f"\nSaved {len(commands)} commands for {len(pods)} pods to {config.SNAPSHOT_FILE}")
    print("Test it locally (PowerShell):  $env:CLUSTER_MODE='snapshot'; streamlit run app.py")


if __name__ == "__main__":
    main()
