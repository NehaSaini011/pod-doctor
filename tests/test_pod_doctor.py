"""
Offline tests: no cluster and no API key needed.

    python tests/test_pod_doctor.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["GEMINI_API_KEY"] = ""  # force the offline TF-IDF retriever

from pod_doctor import config, k8s_tools  # noqa: E402

config.GEMINI_API_KEY = ""
from tests.fake_kubectl import fake_kubectl  # noqa: E402

_original_kubectl = k8s_tools._kubectl
k8s_tools._kubectl = fake_kubectl  # swap real kubectl for simulated output
from pod_doctor.rag import get_index  # noqa: E402
from pod_doctor.tools import call_tool  # noqa: E402

passed = 0


def check(name, condition):
    global passed
    if not condition:
        raise AssertionError(f"FAILED: {name}")
    passed += 1
    print(f"  ok  {name}")


print("Tool tests")
pods = {p["app"]: p for p in k8s_tools.list_pods()["pods"]}
check("healthy pod detected", pods["web-frontend"]["healthy"])
check("crash loop detected", pods["payment-api"]["status"] == "CrashLoopBackOff")
check("OOMKilled found in last termination", pods["report-worker"]["last_termination"]["reason"] == "OOMKilled")
check("unschedulable pending detected", pods["search-api"]["status"].startswith("Pending"))
check("Running-but-not-Ready counted as unhealthy", pods["orders-api"]["status"] == "Running" and not pods["orders-api"]["healthy"])
check("crash logs readable", "DATABASE_URL" in call_tool("get_pod_logs", {"pod_name": "payment-api-5f6b-xk2lp", "previous": True})["logs"])
check("missing logs returned as a note, not a crash", "note" in call_tool("get_pod_logs", {"pod_name": "inventory-service-6c8d-q9w8e"}))
check("secret values hidden", "c2VjcmV0" not in str(call_tool("get_resource", {"kind": "secret", "name": "db-creds"})))
check("command injection rejected", "error" in call_tool("describe_pod", {"pod_name": "x; rm -rf /"}))
check("disallowed kind rejected", "error" in call_tool("get_resource", {"kind": "clusterrole"}))
check("unknown tool handled", "error" in call_tool("delete_everything", {}))

print("\nRAG retrieval tests (each failure must retrieve its own runbook first)")
expected = {
    "CrashLoopBackOff exit code 1 missing environment variable": "crashloopbackoff.md",
    "ImagePullBackOff manifest unknown image tag": "imagepullbackoff.md",
    "OOMKilled exit code 137 memory limit": "oomkilled.md",
    "configmap not found CreateContainerConfigError": "createcontainerconfigerror.md",
    "FailedScheduling Insufficient cpu pending": "pending-unschedulable.md",
    "Running but not ready readiness probe failed connection refused": "readiness-probe-failing.md",
}
for query, runbook in expected.items():
    check(f"{runbook:32s} <- '{query}'", get_index().search(query, k=1)[0]["runbook"] == runbook)

print("\nCitation check tests")
from pod_doctor.agent import check_citations  # noqa: E402

_events = [{"type": "tool_result", "name": "search_runbooks",
            "result": {"results": [{"runbook": "oomkilled.md"}, {"runbook": "crashloopbackoff.md"}]}}]
_ok = check_citations("Runbook: `oomkilled.md`", _events)
_bad = check_citations("Runbook: `oomkilled.md`, `exit-codes.md`", _events)
check("retrieved runbook counts as verified", _ok["verified"] == ["oomkilled.md"] and not _ok["unverified"])
check("never-retrieved runbook is flagged", _bad["unverified"] == ["exit-codes.md"])

from pod_doctor.agent import review_report  # noqa: E402

_pods = {"type": "tool_result", "name": "list_pods", "result": k8s_tools.list_pods()}
check("self-review flags 'none retrieved' and unread crash logs",
      len(review_report("### payment-api - CrashLoopBackOff\nRunbook: none retrieved", _events + [_pods])) == 2)
check("self-review ignores restarting pods the report doesn't cover",
      review_report("### web-frontend - ImagePullBackOff\nRunbook: `oomkilled.md`", _events + [_pods]) == [])
check("self-review passes a complete report",
      review_report("Runbook: `oomkilled.md`", _events + [
          {"type": "tool_call", "name": "get_pod_logs", "args": {"pod_name": p, "previous": True}}
          for p in ("payment-api-5f6b-xk2lp", "report-worker-79b4-mn3op")]) == [])

print("\nBreak-it-yourself tests (fault injection)")
from pod_doctor import chaos  # noqa: E402

_dep = {"metadata": {"name": "web-frontend", "labels": {"app": "web-frontend"}},
        "spec": {"template": {"metadata": {"labels": {"app": "web-frontend"}},
                              "spec": {"containers": [{"name": "web", "image": "nginx:1.27-alpine",
                                                       "resources": {"requests": {"cpu": "50m"}}}]}}}}
_expect = {
    "bad_image": lambda c: c["image"] == "nginx:1.27-alpinee",
    "missing_configmap": lambda c: c["envFrom"][0]["configMapRef"]["name"] == "web-frontend-settings",
    "missing_secret": lambda c: c["env"][0]["valueFrom"]["secretKeyRef"]["name"] == "web-frontend-credentials",
    "readiness_port": lambda c: c["readinessProbe"]["httpGet"]["port"] == 8081,
    "liveness": lambda c: c["livenessProbe"]["httpGet"]["path"] == "/healthz",
    "huge_cpu": lambda c: c["resources"]["requests"]["cpu"] == "48",
    "bad_command": lambda c: c["command"] == ["/app/start-server"],
}
for _key, _ok in _expect.items():
    _broken = chaos.apply_fault(_dep, _key)
    check(f"fault '{_key}' changes the container as intended",
          _ok(_broken["spec"]["template"]["spec"]["containers"][0]))
check("faults leave no label/annotation hint for the agent",
      all(chaos.apply_fault(_dep, k)["metadata"] == _dep["metadata"] for k in chaos.FAULTS))
check("original Deployment object is never mutated", "envFrom" not in _dep["spec"]["template"]["spec"]["containers"][0])
config.KUBE_CONTEXT = "some-real-cluster"
try:
    chaos.inject("web-frontend", "bad_image")
    check("fault injection refused outside the demo cluster", False)
except k8s_tools.ToolError:
    check("fault injection refused outside the demo cluster", True)
config.KUBE_CONTEXT = config.CHAOS_CONTEXT

print("\nPaste-mode tests")
check("paste mode can only use search_runbooks",
      "error" in call_tool("list_pods", {}, allowed=["search_runbooks"]))

print("\nSnapshot mode tests (what the deployed website uses)")
import contextlib, io, tempfile  # noqa: E402
import scripts.snapshot as snapshot_script  # noqa: E402

config.SNAPSHOT_FILE = Path(tempfile.mkdtemp()) / "snap.json"
with contextlib.redirect_stdout(io.StringIO()):
    snapshot_script.main(run=fake_kubectl)
config.CLUSTER_MODE = "snapshot"
k8s_tools._snapshot = None
k8s_tools._kubectl = _original_kubectl  # real dispatcher, now reading the snapshot
check("snapshot recorded every pod", len(k8s_tools.list_pods()["pods"]) == 7)
check("logs tail applied to recorded logs",
      call_tool("get_pod_logs", {"pod_name": "payment-api-5f6b-xk2lp", "previous": True, "tail_lines": 1})["logs"].count("\n") == 1)
check("unknown pod gives a NotFound error", "error" in call_tool("describe_pod", {"pod_name": "ghost-pod"}))
secret_json = '{"kind": "Secret", "metadata": {"name": "db"}, "type": "Opaque", "data": {"password": "c2VjcmV0"}}'
check("secret values redacted before saving", "c2VjcmV0" not in snapshot_script.sanitise(secret_json)
      and "password" in snapshot_script.sanitise(secret_json))
config.CLUSTER_MODE = "live"

print(f"\nAll {passed} checks passed (retrieval backend: {get_index().backend}).")
