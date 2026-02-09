## Keep SOPS in `flux/vars` (stop stamping SOPS into every app namespace)

Goal: **do not** require `sops-age`, `cluster-settings`, or `cluster-secrets` to exist in every application namespace just because Flux `Kustomization` CRs live outside `flux-system`.

This keeps us aligned with the “app Kustomizations live in the app namespace” pattern while we still bootstrap with SOPS.

### Why this is necessary in this repo

`kubernetes/main/flux/apps.yaml` (and `kubernetes/edge/flux/apps.yaml`) injects SOPS decryption + substitutions into **all child Flux Kustomizations** unless they opt out:

- Patch target selector:
  - `labelSelector: substitution.flux.home.arpa/disabled notin (true)`

That injected config expects these namespaced objects to exist in the **same namespace as the Flux `Kustomization` CR**:

- `Secret/sops-age`
- `ConfigMap/cluster-settings`
- `Secret/cluster-secrets`
- (optionally) user settings/secrets

If you place Flux `Kustomization` CRs into an app namespace (e.g., `frontend`), the defaults will try to use `frontend/sops-age`, `frontend/cluster-settings`, etc.

### Current coupling we want to remove

The component `kubernetes/shared/components/common/sops` creates:

- `Secret/sops-age`
- `Secret/cluster-secrets` (SOPS-encrypted)
- `ConfigMap/cluster-settings`

Because it’s a component (and those manifests have no explicit `metadata.namespace`), including it in a namespace kustomization (example: `kubernetes/main/apps/frontend/kustomization.yaml`) stamps those objects into that namespace.

We want to stop doing that so SOPS stays confined to:

- `kubernetes/*/flux/vars/*` (applied in `flux-system`)

## The pattern (what to do)

### Step 1: Remove `common/sops` from app namespaces

- Stop including `kubernetes/shared/components/common/sops` from namespace-level kustomizations (like `kubernetes/main/apps/frontend/kustomization.yaml`).

### Step 2: Opt out app Kustomizations from global SOPS/substitution defaults

For any Flux `Kustomization` CR that lives outside `flux-system`, add:

```yaml
metadata:
  labels:
    substitution.flux.home.arpa/disabled: "true"
```

This prevents Flux from injecting:

- `spec.decryption.secretRef: sops-age`
- `spec.postBuild.substituteFrom: cluster-settings/cluster-secrets`

So the Kustomization no longer depends on SOPS assets in that namespace.

### Step 3: Keep `sourceRef` explicit

If the `GitRepository` source lives in `flux-system` (as it does in this repo), then app Kustomizations in other namespaces must set:

```yaml
spec:
  sourceRef:
    kind: GitRepository
    name: haynes-ops
    namespace: flux-system
```

(`homepage` and `omni` already do this.)

## Example: `frontend/homepage`

`kubernetes/main/apps/frontend/homepage/ks.yaml` is a Flux `Kustomization` in the `frontend` namespace. To keep it from requiring `frontend/sops-age` and `frontend/cluster-secrets`, it should be labeled with the opt-out label above.

## Definition of done (for a namespace)

- Namespace kustomization no longer includes `shared/components/common/sops`
- Any Flux `Kustomization` CRs in that namespace that do not need SOPS/substituteFrom have `substitution.flux.home.arpa/disabled: "true"`
- `cluster-apps` still uses SOPS/substituteFrom from `flux-system` via `kubernetes/*/flux/vars/`

