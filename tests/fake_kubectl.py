"""Simulated kubectl output for the broken "shop" namespace, so the tools can be
tested without a real cluster."""
import json

from pod_doctor.k8s_tools import ToolError


def fake_kubectl(a, timeout=30):
    T = "2026-11-20T14:00:00Z"
    def pod(name, app, state, ready, restarts=0, last=None, phase="Running", node="pod-doctor-control-plane", sched=True):
        cs = {"name": "c", "ready": ready, "restartCount": restarts, "state": state}
        if last: cs["lastState"] = {"terminated": last}
        st = {"phase": phase, "containerStatuses": [cs] if node else [],
              "conditions": [] if sched else [{"type":"PodScheduled","status":"False","reason":"Unschedulable"}]}
        return {"metadata": {"name": name, "labels": {"app": app}, "creationTimestamp": T},
                "spec": {"containers": [{}], "nodeName": node}, "status": st}
    pods = [
     pod("web-frontend-7d9c-abcde","web-frontend",{"running":{}},True),
     pod("payment-api-5f6b-xk2lp","payment-api",{"waiting":{"reason":"CrashLoopBackOff"}},False,6,{"reason":"Error","exitCode":1}),
     pod("inventory-service-6c8d-q9w8e","inventory-service",{"waiting":{"reason":"ImagePullBackOff"}},False),
     pod("report-worker-79b4-mn3op","report-worker",{"waiting":{"reason":"CrashLoopBackOff"}},False,5,{"reason":"OOMKilled","exitCode":137}),
     pod("auth-service-5d7f-r4t5y","auth-service",{"waiting":{"reason":"CreateContainerConfigError"}},False),
     pod("search-api-8f9a-z1x2c","search-api",{},False,phase="Pending",node=None,sched=False),
     pod("orders-api-6b5c-v7b8n","orders-api",{"running":{}},False),
    ]
    if a[:2] == ["get","pods"]: return json.dumps({"items":pods})
    if a[0] == "describe":
        n=a[2]
        if n.startswith("report-worker"):
            return f"Name: {n}\nNamespace: shop\nContainers:\n  worker:\n    Image: polinux/stress\n    State: Waiting\n      Reason: CrashLoopBackOff\n    Last State: Terminated\n      Reason: OOMKilled\n      Exit Code: 137\n    Limits:\n      memory: 100Mi\nEvents:\n  Warning  BackOff  kubelet  Back-off restarting failed container"
        return f"Name: {n}\nNamespace: shop\nEvents: <none>"
    if a[0] == "logs":
        n=a[1]
        if n.startswith("payment-api"): return "Starting payment-api v2.3.1\nFATAL: required environment variable DATABASE_URL is not set"
        if n.startswith("inventory"): raise ToolError(f'Error from server (BadRequest): container "inventory" in pod "{n}" is waiting to start: trying and failing to pull image\n')
        return ""
    if a[:2] == ["get","events"]:
        ev=[{"type":"Normal","reason":"Pulled","involvedObject":{"kind":"Pod","name":"web-frontend-7d9c-abcde"},"message":"ok","lastTimestamp":T},
            {"type":"Warning","reason":"FailedScheduling","involvedObject":{"kind":"Pod","name":"search-api-8f9a-z1x2c"},"message":"0/1 nodes are available: 1 Insufficient cpu.","lastTimestamp":T,"count":12}]
        if "--field-selector" in a:
            name=a[a.index("--field-selector")+1].split("=")[1]; ev=[e for e in ev if e["involvedObject"]["name"]==name]
        return json.dumps({"items":ev})
    if a[:2] == ["get","secret"] and len(a)>2 and a[2]!="-n":
        return json.dumps({"metadata":{"name":a[2],"managedFields":[1]},"type":"Opaque","data":{"password":"c2VjcmV0"}})
    if a[:2] == ["get","deployment"] and a[2]!="-n":
        return json.dumps({"metadata":{"name":a[2],"managedFields":[1],"annotations":{"kubectl.kubernetes.io/last-applied-configuration":"x"}},"spec":{"replicas":1},"status":{"x":1}})
    if a[:2] == ["get","configmap"] and a[2]=="-n":
        return json.dumps({"items":[{"metadata":{"name":"kube-root-ca.crt"}}]})
    if a[:2] == ["get","node"]:
        return json.dumps({"items":[{"metadata":{"name":"pod-doctor-control-plane"},"status":{"allocatable":{"cpu":"8","memory":"16Gi"}}}]})
    raise ToolError(f"unhandled {a}\n")
