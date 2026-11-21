# Runbook: Pod stuck because a PersistentVolumeClaim is Pending

**Symptoms:** Pod `Pending` with event `pod has unbound immediate PersistentVolumeClaims`, or `ContainerCreating` with `FailedMount` / `FailedAttachVolume` events.

**How to confirm:** `kubectl get pvc -n <ns>` (STATUS Pending?); `kubectl describe pvc <name>` for provisioning errors; check the `storageClassName` exists (`kubectl get storageclass`).

**Common causes:** StorageClass name typo or no default StorageClass; requested size/access mode not supported; volume attached to another node (ReadWriteOnce).

**Fix:** Correct the storageClassName or create a default StorageClass; adjust access mode; for RWO volumes make sure the old pod is gone.
