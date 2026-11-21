# Runbook: Service has no endpoints (traffic goes nowhere)

**Symptoms:** Requests to a Service time out or return 503 via the Ingress. `kubectl get endpoints <service>` shows `<none>`.

**What it means:** The Service has no ready pods behind it.

**How to confirm:**
1. `kubectl get endpoints <service>` (or `kubectl get endpointslices -l kubernetes.io/service-name=<service>`).
2. Compare the Service `spec.selector` labels with the pod labels (`kubectl get pods --show-labels`).
3. Check whether matching pods are Ready (READY column).

**Common causes:** Pods not Ready (see readiness-probe runbook); label selector mismatch/typo; pods crashing; `targetPort` not matching the container port.

**Fix:** Fix the readiness issue, or correct the selector/labels/targetPort so they match.
