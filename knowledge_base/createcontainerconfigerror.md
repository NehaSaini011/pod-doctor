# Runbook: CreateContainerConfigError (missing ConfigMap or Secret)

**Symptoms:** Pod STATUS is `CreateContainerConfigError`. READY 0/1, RESTARTS 0. No logs, because the container was never created.

**What it means:** Kubernetes could not build the container configuration. The most common reason is that the pod references a **ConfigMap or Secret that does not exist** (or a key inside it that does not exist), via `env.valueFrom`, `envFrom`, or a volume.

**How to confirm:**
1. `kubectl describe pod <pod>` - Events show a message like `Error: configmap "<name>" not found` or `secret "<name>" not found` or `couldn't find key X in ConfigMap`.
2. `kubectl get configmap -n <namespace>` / `kubectl get secret -n <namespace>` - check whether it exists **in the same namespace** as the pod (ConfigMaps/Secrets are namespace-scoped).
3. Check the Deployment's `envFrom` / `valueFrom` references for typos.

**Common causes:**
- The ConfigMap/Secret was never created (forgotten in the release, or created in a different namespace).
- Typo in the referenced name or key.
- Deploy order: the app was deployed before its configuration.

**Fix:**
- Create the missing ConfigMap/Secret in the correct namespace (`kubectl create configmap <name> --from-literal=KEY=value -n <ns>` or apply a YAML manifest), and the kubelet will start the container automatically on its next retry.
- Or fix the reference name in the Deployment.
- If the config is genuinely optional, mark the reference `optional: true`.

**Prevention:** Ship ConfigMaps/Secrets in the same Helm chart / kustomization as the Deployment.
