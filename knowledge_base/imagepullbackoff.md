# Runbook: ImagePullBackOff / ErrImagePull

**Symptoms:** Pod STATUS is `ErrImagePull` and then `ImagePullBackOff`. The container never starts, so there are no logs.

**What it means:** The kubelet on the node could not download the container image. Kubernetes retries with a back-off.

**How to confirm:**
1. `kubectl describe pod <pod>` - read the Events at the bottom. Typical messages:
   - `manifest unknown` / `not found` -> the image name or **tag does not exist** (very often a typo).
   - `pull access denied` / `unauthorized` -> the image is private and credentials are missing.
   - `i/o timeout` / `no such host` -> the node cannot reach the registry (network/DNS/proxy).
2. Compare the image in the Deployment spec with the tags that really exist in the registry.

**Common causes:**
- Typo in the image name or tag (e.g. `alpne` instead of `alpine`, `lates` instead of `latest`).
- Tag was deleted or never pushed by CI.
- Private registry without an `imagePullSecrets` entry.
- Registry rate limits (Docker Hub anonymous pulls).

**Fix:**
- Correct the `image:` field in the Deployment and re-apply, or `kubectl set image deployment/<name> <container>=<correct-image>`.
- For private images, create a docker-registry Secret and reference it under `spec.template.spec.imagePullSecrets`.

**Prevention:** Pin images by immutable tag or digest produced by CI; validate that the image exists before deploying.
