# Runbook: Service-to-service DNS failures

**Symptoms:** App logs show `no such host`, `Name or service not known`, `could not resolve host` for another service.

**How to confirm:** Check the hostname used by the app. Inside the same namespace `<service>` works; across namespaces use `<service>.<namespace>.svc.cluster.local`. Verify the Service exists: `kubectl get svc -n <ns>`. Check CoreDNS pods are Running in `kube-system`.

**Common causes:** Wrong service name or namespace in configuration; Service not created; CoreDNS unhealthy; NetworkPolicy blocking DNS (UDP/TCP 53).

**Fix:** Correct the hostname in config (env var / ConfigMap); create the Service; fix CoreDNS or NetworkPolicy.
