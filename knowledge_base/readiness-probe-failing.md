# Runbook: Running but not Ready (readiness probe failing)

**Symptoms:** Pod STATUS is `Running`, RESTARTS 0, but READY is `0/1`. The Service that selects these pods receives no traffic; users see 503s or connection errors through the Service/Ingress.

**What it means:** The container process is alive, but its **readiness probe** keeps failing, so Kubernetes removes the pod from the Service endpoints. Readiness failures do NOT restart the container (that is the liveness probe's job), which is why this failure is easy to miss - the pod "looks" fine at a glance.

**How to confirm:**
1. `kubectl describe pod <pod>` - Events show `Readiness probe failed: ...`. Typical messages: `connection refused` (wrong port or app not listening), `HTTP probe failed with statuscode: 404` (wrong path), `context deadline exceeded` (app too slow / timeout too short).
2. Compare the probe's `port` and `path` with the port the container actually listens on (`containerPort`, application config).
3. `kubectl get endpoints <service>` - an empty endpoints list confirms no pod is receiving traffic.

**Common causes:**
- Probe points at the wrong port (e.g. 8080 while the server listens on 80).
- Probe path does not exist (`/healthz` not implemented).
- Timeout or initialDelaySeconds too short for a slow-starting app.
- A dependency checked by the readiness endpoint is down.

**Fix:**
- Correct `readinessProbe.httpGet.port` / `path` in the Deployment to match the application, and re-apply.
- Tune `initialDelaySeconds`, `timeoutSeconds`, `failureThreshold` for slow starters.

**Prevention:** Use named ports (`port: http`) in probes so probe and container port cannot drift apart.
