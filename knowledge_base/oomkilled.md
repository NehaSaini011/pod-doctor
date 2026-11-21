# Runbook: OOMKilled (Out Of Memory)

**Symptoms:** Pod restarts repeatedly, often shown as `CrashLoopBackOff` or `OOMKilled`. `kubectl describe pod` shows `Last State: Terminated, Reason: OOMKilled, Exit Code: 137`.

**What it means:** The container tried to use more memory than its `resources.limits.memory`. The Linux kernel's OOM killer terminated the process (SIGKILL = exit code 137). Memory, unlike CPU, cannot be throttled - exceeding the limit always means the process is killed.

**How to confirm:**
1. `kubectl describe pod <pod>` - look for `Reason: OOMKilled` and `Exit Code: 137` under Last State.
2. Compare the container's memory **limit** in the Deployment with what the workload needs (application config, e.g. JVM heap `-Xmx`, worker buffer sizes, `--vm-bytes`, batch size).
3. If metrics-server is installed: `kubectl top pod <pod>` to watch real usage.

**Common causes:**
- Memory limit set too low for the real workload.
- A memory leak that grows until the limit.
- JVM / runtime heap configured larger than the container limit.
- Processing a large batch/file fully in memory.

**Fix:**
- If the workload legitimately needs the memory: raise `resources.limits.memory` (and set `requests.memory` close to typical usage) to comfortably above peak usage, e.g. 20-30% headroom.
- If it is a leak: raising the limit only delays the crash - profile and fix the code.

**Prevention:** Load-test to measure peak memory; set requests/limits from real data; alert on memory usage above 80-90% of the limit.
