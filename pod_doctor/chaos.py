"""
"Break it yourself": fault injection for the live demo.

An evaluator picks a Deployment and a fault; we change that Deployment so it breaks, then the
agent has to find out what is wrong - without being told. This proves nothing is hard-coded.

Safety design:
  * These functions are NOT agent tools. The agent stays read-only; only a human clicking the
    panel can change the cluster.
  * They refuse to run anywhere except the demo cluster/namespace (CHAOS_CONTEXT/CHAOS_NAMESPACE),
    so they can never touch a real cluster you have selected in the picker.
  * The fault is NOT recorded anywhere the agent can read (no labels/annotations on the
    Deployment). The original spec is saved locally so every fault can be undone.
"""
import copy
import json
import subprocess

from . import config
from .k8s_tools import ToolError, _check_name, _run_kubectl

STATE_FILE = config.CACHE_DIR / "chaos_state.json"


def _typo_image(image):
    repo, _, tag = image.rpartition(":") if ":" in image.split("/")[-1] else (image, "", "")
    return f"{repo}:{tag + tag[-1]}" if tag else f"{image}:latset"


def _fault_bad_image(dep, c, spec):
    c["image"] = _typo_image(c["image"])


def _fault_missing_configmap(dep, c, spec):
    c.setdefault("envFrom", []).append({"configMapRef": {"name": f"{dep}-settings"}})


def _fault_missing_secret(dep, c, spec):
    c.setdefault("env", []).append(
        {"name": "API_TOKEN", "valueFrom": {"secretKeyRef": {"name": f"{dep}-credentials", "key": "token"}}}
    )


def _fault_readiness_port(dep, c, spec):
    c["readinessProbe"] = {"httpGet": {"path": "/", "port": 8081}, "initialDelaySeconds": 2, "periodSeconds": 5}


def _fault_liveness(dep, c, spec):
    c["livenessProbe"] = {
        "httpGet": {"path": "/healthz", "port": 80},
        "initialDelaySeconds": 3, "periodSeconds": 3, "failureThreshold": 2,
    }


def _fault_huge_cpu(dep, c, spec):
    res = c.setdefault("resources", {})
    res.setdefault("requests", {})["cpu"] = "48"
    res.setdefault("limits", {})["cpu"] = "48"


def _fault_bad_command(dep, c, spec):
    c["command"] = ["/app/start-server"]
    c.pop("args", None)


FAULTS = {
    "bad_image": ("Typo in the image tag", _fault_bad_image),
    "missing_configmap": ("Reference a ConfigMap that doesn't exist", _fault_missing_configmap),
    "missing_secret": ("Reference a Secret that doesn't exist", _fault_missing_secret),
    "readiness_port": ("Readiness probe on the wrong port", _fault_readiness_port),
    "liveness": ("Liveness probe on a path that returns 404", _fault_liveness),
    "huge_cpu": ("Request 48 CPU cores", _fault_huge_cpu),
    "bad_command": ("Start command that doesn't exist in the image", _fault_bad_command),
}


def apply_fault(deployment_obj, fault_key):
    """Pure function: return a broken copy of a Deployment object (also used by the tests)."""
    if fault_key not in FAULTS:
        raise ToolError(f"Unknown fault '{fault_key}'.")
    broken = copy.deepcopy(deployment_obj)
    spec = broken["spec"]["template"]["spec"]
    FAULTS[fault_key][1](broken["metadata"]["name"], spec["containers"][0], spec)
    return broken


# ------------------------------------------------------------------ cluster side


def available():
    return config.CLUSTER_MODE == "live" and config.KUBE_CONTEXT == config.CHAOS_CONTEXT


def _guard():
    if not available():
        raise ToolError(f"Fault injection only works on the demo cluster ({config.CHAOS_CONTEXT}), in live mode.")


def _get_deployment(name):
    _check_name(name, "deployment")
    obj = json.loads(_run_kubectl(["get", "deployment", name, "-n", config.CHAOS_NAMESPACE, "-o", "json"]))
    obj.pop("status", None)
    obj.get("metadata", {}).pop("managedFields", None)
    return obj


def _replace(obj):
    cmd = ["kubectl", "--context", config.CHAOS_CONTEXT, "replace", "-f", "-"]
    result = subprocess.run(cmd, input=json.dumps(obj), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
    if result.returncode != 0:
        raise ToolError(result.stderr.strip() or "kubectl replace failed")


def _load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state):
    config.CACHE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def list_deployments():
    _guard()
    data = json.loads(_run_kubectl(["get", "deployments", "-n", config.CHAOS_NAMESPACE, "-o", "json"]))
    return [d["metadata"]["name"] for d in data.get("items", [])]


def inject(deployment, fault_key):
    """Break a Deployment. Saves its original pod template so restore() can undo it."""
    _guard()
    current = _get_deployment(deployment)
    state = _load_state()
    if deployment not in state:  # keep the ORIGINAL template, even if faults are stacked
        state[deployment] = {"template": current["spec"]["template"], "faults": []}
    _replace(apply_fault(current, fault_key))
    state[deployment]["faults"].append(fault_key)
    _save_state(state)


def injected():
    """{deployment: [fault labels]} for everything currently broken by the panel."""
    return {d: [FAULTS[f][0] for f in v["faults"]] for d, v in _load_state().items()}


def restore(deployment):
    _guard()
    state = _load_state()
    if deployment not in state:
        raise ToolError(f"No injected fault recorded for '{deployment}'.")
    current = _get_deployment(deployment)
    current["spec"]["template"] = state[deployment]["template"]
    _replace(current)
    del state[deployment]
    _save_state(state)
