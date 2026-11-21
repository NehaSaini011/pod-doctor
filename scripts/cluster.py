"""
Manage the local demo cluster. Works on Windows, macOS and Linux.

    python scripts/cluster.py up              # create cluster + deploy the broken "shop" apps
    python scripts/cluster.py status          # show pod health in the shop namespace
    python scripts/cluster.py fix payment-api # apply the reference fix for one service
    python scripts/cluster.py fix all         # apply every reference fix
    python scripts/cluster.py reset           # break everything again (for the next demo)
    python scripts/cluster.py down            # delete the cluster

Needs: Docker Desktop (running), kind, kubectl.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLUSTER_NAME = "pod-doctor"
CONTEXT = f"kind-{CLUSTER_NAME}"
NAMESPACE = "shop"
MANIFESTS = ROOT / "cluster" / "manifests"
SOLUTIONS = ROOT / "cluster" / "solutions"

INSTALL_HINTS = {
    "docker": "Install Docker Desktop: https://www.docker.com/products/docker-desktop/ (and make sure it is running)",
    "kind": "Windows: winget install Kubernetes.kind    |  macOS: brew install kind    |  https://kind.sigs.k8s.io/docs/user/quick-start/",
    "kubectl": "Windows: winget install Kubernetes.kubectl |  macOS: brew install kubectl |  https://kubernetes.io/docs/tasks/tools/",
}


def run(cmd, check=True, capture=False):
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, text=True, capture_output=capture, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        if capture:
            print(result.stdout, result.stderr)
        sys.exit(f"Command failed: {' '.join(cmd)}")
    return result


def kubectl(*args, **kw):
    return run(["kubectl", "--context", CONTEXT, *args], **kw)


def check_tools():
    missing = [t for t in ("docker", "kind", "kubectl") if shutil.which(t) is None]
    for tool in missing:
        print(f"[missing] {tool}: {INSTALL_HINTS[tool]}")
    if missing:
        sys.exit("Install the missing tools above, open a NEW terminal, and run this again.")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        sys.exit("Docker is installed but not running. Start Docker Desktop and try again.")


def cluster_exists():
    out = run(["kind", "get", "clusters"], capture=True, check=False).stdout
    return CLUSTER_NAME in out.split()


def up():
    check_tools()
    if cluster_exists():
        print(f"Cluster '{CLUSTER_NAME}' already exists, reusing it.")
    else:
        run(["kind", "create", "cluster", "--config", str(ROOT / "cluster" / "kind-config.yaml")])
    kubectl("apply", "-f", str(MANIFESTS))
    print("\nWaiting ~60s for images to pull and failures to show up...")
    time.sleep(60)
    status()
    print("\nExpected: web-frontend Running 1/1, everything else broken in a different way.")
    print("If some pods still say ContainerCreating, wait a minute and run: python scripts/cluster.py status")


def status():
    kubectl("get", "pods", "-n", NAMESPACE, "-o", "wide", check=False)


def fix(target):
    files = sorted(SOLUTIONS.glob("*.yaml")) if target == "all" else [SOLUTIONS / f"{target}.yaml"]
    for f in files:
        if not f.exists():
            names = ", ".join(p.stem for p in SOLUTIONS.glob("*.yaml"))
            sys.exit(f"No reference fix for '{target}'. Options: {names}, all")
        kubectl("apply", "-f", str(f))
    print("Applied. Give it ~20s, then: python scripts/cluster.py status")


def reset():
    kubectl("delete", "namespace", NAMESPACE, "--wait=true", check=False)
    kubectl("apply", "-f", str(MANIFESTS))
    print("Shop re-deployed in its broken state. Wait ~60s before the demo.")


def down():
    run(["kind", "delete", "cluster", "--name", CLUSTER_NAME])


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in {"up", "status", "fix", "reset", "down"}:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "fix":
        if len(sys.argv) < 3:
            sys.exit("Usage: python scripts/cluster.py fix <service-name|all>")
        fix(sys.argv[2])
    else:
        {"up": up, "status": status, "reset": reset, "down": down}[cmd]()
