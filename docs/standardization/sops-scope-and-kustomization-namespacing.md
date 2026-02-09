## Keep SOPS in `flux/vars` (stop stamping SOPS into every app namespace)

Goal: **do not** require `sops-age`, `cluster-settings`, or `cluster-secrets` to exist in every application namespace just because Flux `Kustomization` CRs live outside `flux-system`.

This keeps us aligned with the “app Kustomizations live in the app namespace” pattern while we still bootstrap with SOPS.

### Why this is necessary in this repo

Historically, `kubernetes/*/flux/apps.yaml` injected SOPS decryption + `postBuild.substituteFrom` into **all child Flux Kustomizations** by default. That caused breakage when Flux `Kustomization` CRs lived outside `flux-system` (because `sops-age`, `cluster-settings`, etc. would be expected in that app namespace).

As part of aligning with the reference repo’s “explicit per-app settings” model, `haynes-ops` has moved away from default-on injection.

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

### Step 2: Make SOPS decryption explicit only where needed

If a Flux `Kustomization` applies any `*.sops.yaml` files, add decryption to that `Kustomization` CR:

```yaml
spec:
  decryption:
    provider: sops
    secretRef:
      name: sops-age
```

If it does not apply SOPS files, do not add decryption.

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

`kubernetes/main/apps/frontend/homepage/ks.yaml` is a Flux `Kustomization` in the `frontend` namespace. It does not apply SOPS files, so it does not need `spec.decryption`. It does need an explicit `sourceRef.namespace: flux-system`.

## Definition of done (for a namespace)

- Namespace kustomization no longer includes `shared/components/common/sops`
- Flux `Kustomization` CRs in that namespace only include `spec.decryption` when they actually apply `*.sops.yaml`
- No dependency on stamping SOPS assets into app namespaces

