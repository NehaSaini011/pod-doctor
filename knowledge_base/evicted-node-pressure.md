# Runbook: Evicted pods / node pressure

**Symptoms:** Pods with STATUS `Evicted` (or `Error` with reason Evicted); message like `The node was low on resource: memory` or `ephemeral-storage`.

**What it means:** The kubelet evicted pods to protect the node when it ran low on memory, disk or PIDs. Pods with no requests (BestEffort QoS) are evicted first.

**How to confirm:** `kubectl describe pod <pod>` (Status/Message); `kubectl describe node <node>` (Conditions: MemoryPressure, DiskPressure).

**Fix:** Set realistic requests/limits so pods get Burstable/Guaranteed QoS; clean up disk (logs, images); add node capacity. Delete leftover evicted pods once the cause is fixed.
