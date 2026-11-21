# Runbook: Restarts caused by a failing liveness probe

**Symptoms:** RESTARTS increases steadily; logs look normal right up to the restart; `describe` shows `Liveness probe failed` events followed by `Container ... failed liveness probe, will be restarted`. Last State reason is usually `Error` or `Completed` with exit code 137/143.

**What it means:** The kubelet decided the container was unhealthy and killed it. Unlike an app crash, the app itself did not choose to exit.

**How to confirm:** `kubectl describe pod <pod>` events; compare probe port/path/timeouts with the app.

**Common causes:** Wrong probe port/path; `initialDelaySeconds` shorter than startup time; probe timeout too strict under load; liveness endpoint checking downstream dependencies (one database blip restarts every pod).

**Fix:** Correct port/path; add a `startupProbe` for slow-starting apps; make liveness checks cheap and local (do not check dependencies in liveness).

**Prevention:** Keep liveness simple; use readiness for dependency checks.
