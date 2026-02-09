## Flux Kustomization namespace moves & PVC safety

Moving a Flux `Kustomization` CR (e.g. from `flux-system` to an app namespace) is **not** a “rename”. In Kubernetes it is **delete + create** (namespace is immutable), and Flux may **prune** resources that were previously applied by the old object.

This matters most for **PersistentVolumeClaims** (PVCs) and other stateful resources.

### What happened (the gotcha)

- A Flux `Kustomization` maintains an **inventory** of the objects it applied.
- When a `Kustomization` is **deleted**, Flux’s finalizer can garbage-collect (prune) objects from that inventory.
- If the inventory included PVCs, they can be deleted as part of the move.

### Symptoms

- Pods become `Pending` with events like:
  - `persistentvolumeclaim "<name>" not found`
  - `pod has unbound immediate PersistentVolumeClaims`
- Flux `Kustomization` health checks time out waiting for PVCs.

### Make PVCs “sticky” (recommended default)

For any PVC you never want Flux to delete automatically, set:

```yaml
metadata:
  annotations:
    kustomize.toolkit.fluxcd.io/prune: disabled
```

Notes:
- This annotation is respected by Flux when pruning resources (including inventory-based pruning).
- For Helm-managed PVCs, the analogous pattern is often `helm.sh/resource-policy: keep` (Helm behavior, not Flux).

### Safer namespace-move procedure (runbook)

Do this **before** moving the `Kustomization` CR:

1. **Inventory the stateful objects** under the Kustomization path
   - PVCs (`kind: PersistentVolumeClaim`)
   - PV-backed StatefulSets
   - VolSync restored PVCs / ReplicationDestination resources

2. **Protect PVCs**
   - Add `kustomize.toolkit.fluxcd.io/prune: disabled` to the PVC manifests (or PVC templates) that must never be deleted.

3. **Optional: disable pruning for the old Kustomization (belt-and-suspenders)**
   - Temporarily set `spec.prune: false` on the old `Kustomization`, reconcile it, then perform the move.
   - This reduces the chance of deletions during the cutover if the old Kustomization is removed.

4. **Move the Kustomization**
   - Change `metadata.namespace` to the target namespace.
   - Ensure `spec.sourceRef.namespace` and any `dependsOn[].namespace` are explicit.

5. **Force a reconciliation**

```bash
flux reconcile kustomization <name> -n <new-namespace> --with-source
flux reconcile helmrelease <name> -n <new-namespace> --with-source
```

### Recovery if you already got bit

If the Kustomization path still contains the PVC manifests (and they are not Helm-owned), the fastest recovery is usually:

```bash
flux reconcile kustomization <name> -n <namespace> --with-source
kubectl -n <namespace> get pvc
```

If the PVCs were deleted and **not** defined in Git (or were Helm-owned), you must restore them via the correct owner (Git manifests, Helm values, or storage restore process).

