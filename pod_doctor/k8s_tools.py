"""
The agent's "hands": small, READ-ONLY wrappers around kubectl.

Safety design (worth mentioning in the presentation):
  * Only `get`, `describe` and `logs` are ever run - the agent cannot change the cluster.
  * Commands are built as argument lists (no shell), and every name the LLM passes in is
    validated against the Kubernetes naming rules, so the model cannot inject commands.
  * Every call is pinned to one kube context (KUBE_CONTEXT), so the agent can never touch
    a different cluster you may have configured.
  * Secret *values* are never returned, only key names.
  * Output is trimmed so one huge response cannot flood the model's context window.
"""
import json
import re
import subprocess
from datetime import datetime, timezone

from . import config

NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
ALLOWED_KINDS = {"deployment", "service", "configmap", "secret", "node", "endpoints"}
KIND_ALIASES = {
    "deployments": "deployment", "deploy": "deployment", "services": "service", "svc": "service",
    "configmaps": "configmap", "cm": "configmap", "secrets": "secret", "nodes": "node",
    "endpoint": "endpoints", "ep": "endpoints",
}


class ToolError(Exception):
    pass


def _check_name(value, field):
    if not isinstance(value, str) or not NAME_RE.match(value):
        raise ToolError(f"Invalid {field} '{value}'. Must be a valid Kubernetes name.")
    return value


def _kubectl(args, timeout=30):
    if config.CLUSTER_MODE == "snapshot":
        return _from_snapshot(args)
    return _run_kubectl(args, timeout)


def _run_kubectl(args, timeout=30):
    cmd = ["kubectl", "--context", config.KUBE_CONTEXT, *args]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except FileNotFoundError:
        raise ToolError("kubectl is not installed or not on PATH.")
    except subprocess.TimeoutExpired:
        raise ToolError(f"kubectl timed out after {timeout}s.")
    if result.returncode != 0:
        raise ToolError(result.stderr.strip() or f"kubectl exited with code {result.returncode}")
    return result.stdout


# ------------------------------------------------------------------ snapshot mode
# On the deployed website there is no cluster. scripts/snapshot.py records the output
# of every kubectl command the tools can run against the real cluster; here we answer
# from that recording. The agent, the LLM calls and RAG are all still live.

_snapshot = None


def snapshot_key(args):
    """Same key for a command whatever --tail value the model asked for."""
    return " ".join(a for a in args if not a.startswith("--tail="))


def load_snapshot():
    global _snapshot
    if _snapshot is None:
        if not config.SNAPSHOT_FILE.exists():
            raise ToolError("No cluster snapshot found. Run: python scripts/snapshot.py")
        _snapshot = json.loads(config.SNAPSHOT_FILE.read_text(encoding="utf-8"))
    return _snapshot


def _from_snapshot(args):
    snap = load_snapshot()
    entry = snap["commands"].get(snapshot_key(args))
    if entry is None:
        if args[:2] == ["get", "events"]:
            return json.dumps({"items": []})  # no events recorded for that object
        target = next((a for a in args[2:] if not a.startswith("-")), "")
        raise ToolError(f'Error from server (NotFound): "{target or args[1]}" not found')
    if "error" in entry:
        raise ToolError(entry["error"])
    out = entry["stdout"]
    tail = next((a for a in args if a.startswith("--tail=")), None)
    if args[0] == "logs" and tail:  # apply the requested --tail to the recorded logs
        lines = out.splitlines()
        out = "\n".join(lines[-int(tail.split("=")[1]):]) + ("\n" if lines else "")
    return out


def _now():
    """Current time, or the moment the snapshot was taken (so pod ages stay realistic)."""
    if config.CLUSTER_MODE == "snapshot":
        try:
            return datetime.fromisoformat(load_snapshot()["captured_at"])
        except ToolError:
            pass
    return datetime.now(timezone.utc)


def _trim(text):
    limit = config.MAX_TOOL_OUTPUT_CHARS
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} characters]"


def _age(ts):
    if not ts:
        return "?"
    seconds = int((_now() - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def summarise_pod(pod):
    """Turn one pod's JSON into the same facts you'd read from `kubectl get pods`,
    plus the last termination reason (which is where OOMKilled hides)."""
    meta, spec, status = pod["metadata"], pod.get("spec", {}), pod.get("status", {})
    statuses = status.get("containerStatuses") or []
    total = len(spec.get("containers", [])) or len(statuses)
    ready = sum(1 for c in statuses if c.get("ready"))
    restarts = sum(c.get("restartCount", 0) for c in statuses)

    reason = status.get("phase", "Unknown")
    last_termination = None
    for c in statuses:
        state = c.get("state", {})
        if "waiting" in state and state["waiting"].get("reason"):
            reason = state["waiting"]["reason"]
        elif "terminated" in state and state["terminated"].get("reason"):
            reason = state["terminated"]["reason"]
        last = c.get("lastState", {}).get("terminated")
        if last:
            last_termination = {"reason": last.get("reason"), "exit_code": last.get("exitCode")}
    if reason == "Pending":
        for cond in status.get("conditions", []):
            if cond.get("type") == "PodScheduled" and cond.get("status") == "False":
                reason = f"Pending ({cond.get('reason', 'Unschedulable')})"
    if meta.get("deletionTimestamp"):
        reason = "Terminating"

    labels = meta.get("labels", {})
    healthy = reason == "Running" and ready == total
    return {
        "pod": meta["name"],
        "app": labels.get("app", ""),
        "status": reason,
        "ready": f"{ready}/{total}",
        "restarts": restarts,
        "last_termination": last_termination,
        "node": spec.get("nodeName") or "<none>",
        "age": _age(meta.get("creationTimestamp")),
        "healthy": healthy,
    }


# ------------------------------------------------------------------ tools


def list_pods(namespace=config.DEFAULT_NAMESPACE):
    """Overview of every pod in a namespace with its health status."""
    _check_name(namespace, "namespace")
    data = json.loads(_kubectl(["get", "pods", "-n", namespace, "-o", "json"]))
    pods = [summarise_pod(p) for p in data.get("items", [])]
    unhealthy = [p["pod"] for p in pods if not p["healthy"]]
    return {"namespace": namespace, "pod_count": len(pods), "unhealthy_pods": unhealthy, "pods": pods}


def describe_pod(pod_name, namespace=config.DEFAULT_NAMESPACE):
    """Full `kubectl describe pod` text: container state, last state, exit codes, probes, events."""
    _check_name(pod_name, "pod_name")
    _check_name(namespace, "namespace")
    return {"pod": pod_name, "describe": _trim(_kubectl(["describe", "pod", pod_name, "-n", namespace]))}


def get_pod_logs(pod_name, namespace=config.DEFAULT_NAMESPACE, previous=False, tail_lines=40):
    """Last lines of a container's logs. previous=True reads the crashed attempt."""
    _check_name(pod_name, "pod_name")
    _check_name(namespace, "namespace")
    tail = max(1, min(int(tail_lines), 200))
    args = ["logs", pod_name, "-n", namespace, f"--tail={tail}", "--all-containers=true"]
    if previous:
        args.append("--previous")
    try:
        logs = _kubectl(args)
    except ToolError as exc:
        # Very common and informative: the container never started, so there are no logs.
        return {"pod": pod_name, "previous": previous, "logs": "", "note": str(exc)}
    return {"pod": pod_name, "previous": previous, "logs": _trim(logs) or "(no log output)"}


def get_events(namespace=config.DEFAULT_NAMESPACE, object_name=None):
    """Recent Kubernetes events (warnings first), optionally only for one object."""
    _check_name(namespace, "namespace")
    args = ["get", "events", "-n", namespace, "-o", "json"]
    if object_name:
        _check_name(object_name, "object_name")
        args += ["--field-selector", f"involvedObject.name={object_name}"]
    items = json.loads(_kubectl(args)).get("items", [])
    items.sort(key=lambda e: (e.get("type") != "Warning", e.get("lastTimestamp") or e.get("eventTime") or ""))
    events = [
        {
            "type": e.get("type"),
            "reason": e.get("reason"),
            "object": f"{e['involvedObject'].get('kind')}/{e['involvedObject'].get('name')}",
            "count": e.get("count") or 1,
            "message": (e.get("message") or "").strip()[:400],
        }
        for e in items[:40]
    ]
    return {"namespace": namespace, "events": events}


def _clean(obj):
    obj.get("metadata", {}).pop("managedFields", None)
    annotations = obj.get("metadata", {}).get("annotations", {})
    annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)
    return obj


def get_resource(kind, name=None, namespace=config.DEFAULT_NAMESPACE):
    """Read one resource's spec (or list all of a kind). Kinds: deployment, service,
    configmap, secret (key names only), node, endpoints."""
    kind = (kind or "").lower().strip()
    kind = KIND_ALIASES.get(kind, kind)
    if kind not in ALLOWED_KINDS:
        raise ToolError(f"Kind '{kind}' not allowed. Use one of: {sorted(ALLOWED_KINDS)}")
    _check_name(namespace, "namespace")
    args = ["get", kind]
    if name:
        args.append(_check_name(name, "name"))
    if kind != "node":
        args += ["-n", namespace]
    data = json.loads(_kubectl(args + ["-o", "json"]))

    if not name:  # a list: return names only (plus capacity for nodes)
        out = []
        for item in data.get("items", []):
            entry = {"name": item["metadata"]["name"]}
            if kind == "node":
                entry["allocatable"] = item.get("status", {}).get("allocatable", {})
            out.append(entry)
        return {"kind": kind, "namespace": namespace, "items": out, "count": len(out)}

    data = _clean(data)
    if kind == "secret":  # never leak secret values to the model
        data = {"metadata": {"name": name}, "type": data.get("type"), "keys": sorted((data.get("data") or {}).keys())}
    elif kind == "node":
        st = data.get("status", {})
        data = {
            "name": name,
            "capacity": st.get("capacity"),
            "allocatable": st.get("allocatable"),
            "conditions": [{"type": c["type"], "status": c["status"]} for c in st.get("conditions", [])],
        }
    elif kind == "deployment":
        data.pop("status", None)
    return {"kind": kind, "name": name, "spec": _trim(json.dumps(data, indent=1))}
