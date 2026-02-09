## Edge stabilization (make `edge` a safe proving ground)

This runbook is about getting **`kubernetes/edge`** to a state where we can safely trial changes before rolling them into `main`.

### Definition of done

- `flux check` is clean on `edge`.
- All Flux sources and kustomizations are **Ready**.
- Core baseline apps are healthy (CNI/DNS/metrics/reloader at minimum).
- You can reconcile any one app and understand failures quickly (events/logs workflow).

## Quick context: how `edge` is wired

Repo paths of interest:

- Flux config and root Kustomizations:
  - `kubernetes/edge/flux/config/cluster.yaml` (GitRepository + `cluster` Kustomization)
  - `kubernetes/edge/flux/apps.yaml` (`cluster-apps` Kustomization → `./kubernetes/edge/apps`)
  - `kubernetes/edge/flux/repositories/kustomization.yaml` (applies `kubernetes/shared/repositories`)

Baseline apps currently in `edge` (not exhaustive):

- `kubernetes/edge/apps/kube-system/` (cilium, coredns, metrics-server, reloader, etc.)
- `kubernetes/edge/apps/flux-system/addons/` (notifications/webhooks/monitoring)
- `kubernetes/edge/apps/observability/prometheus-operator-crds/`

## Baseline verification commands

Run these on your workstation against the `edge` kubecontext.

### Flux health

```bash
flux check
flux get sources all -A
flux get ks -A
flux get hr -A
```

If something is failing, pull the details:

```bash
flux get ks -A --status-selector ready=false
flux get hr -A --status-selector ready=false
```

### Cluster baseline pods

```bash
kubectl get nodes
kubectl get pods -A --field-selector=status.phase!=Running
kubectl -n flux-system get pods
kubectl -n kube-system get pods
```

### Events (last ~50)

```bash
kubectl get events -A --sort-by=.lastTimestamp | tail -n 50
```

## Reconcile workflow (standard approach)

### Reconcile the cluster “entry points”

Start at the top and work downward:

```bash
flux reconcile kustomization cluster -n flux-system --with-source
flux reconcile kustomization cluster-apps -n flux-system --with-source
```

Then reconcile repositories if needed:

```bash
flux reconcile kustomization repositories -n flux-system --with-source
```

### Reconcile one app

```bash
flux reconcile kustomization <app-ks-name> -n flux-system --with-source
```

Or if it is a HelmRelease-driven app:

```bash
flux reconcile helmrelease <hr-name> -n <namespace> --with-source
```

## Known failure classes (and what to do)

### 1) Source / artifact errors

Symptoms:
- GitRepository not ready
- HelmRepository / OCIRepository not ready

What to do:
- Check `flux get sources -A` output
- Verify namespaces on namespaced sources (e.g., `OCIRepository` must have `metadata.namespace`)

### 2) Kustomization build errors

Symptoms:
- “accumulating resources” errors
- missing files / bad patches

What to do:
- `flux logs --kind Kustomization --name <name> -n flux-system`
- inspect the referenced `path:` in the Kustomization

### 3) Helm upgrade failures / rollbacks

Symptoms:
- HelmRelease stuck, repeated rollbacks
- “field is immutable” errors

What to do:
- `kubectl -n <ns> describe helmrelease <name>`
- `kubectl -n <ns> get events --sort-by=.lastTimestamp | tail -n 50`

#### Immutable selector remediation (Deployment/StatefulSet/DaemonSet)

If you see `spec.selector ... field is immutable`, the controller must be **deleted and recreated**.

```bash
flux suspend helmrelease <name> -n <namespace>

kubectl -n <namespace> get deploy,sts,ds,cronjob -l helm.toolkit.fluxcd.io/name=<name>
kubectl -n <namespace> delete deployment|statefulset|daemonset <workload-name> --wait=true

flux resume helmrelease <name> -n <namespace>
flux reconcile helmrelease <name> -n <namespace> --with-source
```

Notes:
- Deleting the controller does **not** delete PVCs unless you delete PVCs separately.

## “Edge is fixed” checklist (copy/paste)

- [ ] `flux check` passes
- [ ] `flux get ks -A` shows no `Ready=False`
- [ ] `flux get hr -A` shows no `Ready=False`
- [ ] `kubectl -n flux-system get pods` all Running/Ready
- [ ] `kubectl -n kube-system get pods` all Running/Ready (cilium + coredns healthy)
- [ ] You can reconcile one app end-to-end without guessing (events/logs workflow works)

