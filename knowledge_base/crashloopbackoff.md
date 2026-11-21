# Runbook: CrashLoopBackOff

**Symptoms:** Pod STATUS shows `CrashLoopBackOff`. RESTARTS keeps increasing. READY is 0/1.

**What it means:** The container starts, then exits (or is killed), and Kubernetes keeps restarting it with an increasing back-off delay (10s, 20s, 40s ... up to 5 minutes). CrashLoopBackOff is a *symptom*, not a root cause. The real cause is whatever makes the process exit.

**How to confirm the root cause:**
1. `kubectl logs <pod> --previous` - logs of the crashed attempt. Look at the last lines before exit.
2. `kubectl describe pod <pod>` - check `Last State: Terminated`, its `Reason` and `Exit Code`.
3. Interpret the exit code: 1 = application error, 137 = killed by SIGKILL (often OOMKilled), 139 = segfault, 127 = command not found, 126 = command not executable.

**Common causes:**
- Missing or wrong environment variable / config (app validates config at startup and exits).
- Cannot reach a dependency (database, cache) and the app exits instead of retrying.
- Wrong `command` / `args` in the Deployment.
- Memory limit too low (Last State reason `OOMKilled`, exit code 137) - see the OOMKilled runbook.
- Liveness probe failing and killing a healthy-but-slow container.

**Fix:**
- Missing env var: add it to the Deployment under `spec.template.spec.containers[].env`, ideally sourced from a Secret (`valueFrom.secretKeyRef`) for credentials such as database URLs.
- Then `kubectl apply -f <file>`; the Deployment rolls out a new pod.

**Prevention:** Validate required configuration in CI; use `kubectl apply --dry-run=server`; keep config in ConfigMaps/Secrets that are deployed together with the app.
