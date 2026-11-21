# Runbook: Pod stuck in Pending (Unschedulable)

**Symptoms:** Pod STATUS is `Pending` for a long time, no node assigned (NODE column is `<none>`). No restarts, no logs.

**What it means:** The scheduler cannot find any node that satisfies the pod's requirements, so the pod is never placed on a node.

**How to confirm:**
1. `kubectl describe pod <pod>` - the `FailedScheduling` event explains why, e.g. `0/1 nodes are available: 1 Insufficient cpu` or `Insufficient memory`, `node(s) had untolerated taint`, `didn't match Pod's node affinity/selector`, or `pod has unbound immediate PersistentVolumeClaims`.
2. Compare the pod's `resources.requests` with node capacity: `kubectl describe node` (Allocatable section).

**Common causes:**
- Resource **requests** larger than any node can offer (e.g. `cpu: "32"` on a 4-8 core node - often a units mistake: `32` means 32 cores, `32m` means 0.032 cores).
- Cluster is full; requests exceed remaining allocatable capacity.
- nodeSelector / affinity / taints that no node matches.
- PersistentVolumeClaim that cannot be bound.

**Fix:**
- Lower `resources.requests` to what the app really needs (e.g. `cpu: 100m` = 0.1 core) and re-apply.
- Or add capacity (bigger/more nodes, cluster autoscaler).
- Fix selectors/tolerations if scheduling constraints are wrong.

**Prevention:** Review resource requests in code review; use LimitRange / ResourceQuota to cap per-pod requests in a namespace.
