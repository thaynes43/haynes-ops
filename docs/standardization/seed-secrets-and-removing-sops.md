## Seed secrets & removing per-app SOPS (future standardization task)

This doc captures a future standardization task: converge on the “seed secret” bootstrap strategy used in the reference repo (`example-ops/onedr0p-home-ops`) so we can eventually remove **per-app** `*.sops.yaml` secrets (like `onepassword-connect.secret.sops.yaml`) while keeping GitOps bootstrapping viable.

This is intentionally **not** an immediate refactor; it affects bootstrap ordering and failure modes.

---

### Current state (haynes-ops)

We still have some apps that apply SOPS-encrypted manifests inside the app path, e.g.:

- `kubernetes/main/apps/external-secrets/onepassword-connect/app/onepassword-connect.secret.sops.yaml`

Those apps require the Flux `Kustomization` applying them to have:

```yaml
spec:
  decryption:
    provider: sops
    secretRef:
      name: sops-age
```

Important: `spec.decryption.secretRef.name` is **namespaced**. The referenced secret must exist in the **same namespace as the Flux `Kustomization` CR**.

So if a KS lives in `flux-system`, it depends on `flux-system/sops-age`.

This is why KS namespace moves are easy for apps with no `*.sops.yaml`, and harder for apps that still apply SOPS secrets.

---

### Reference pattern (onedr0p-home-ops): ExternalSecret “seed” concept

The reference repo uses `ExternalSecret` to materialize a Kubernetes Secret that’s then used by a `ClusterSecretStore`:

- `ExternalSecret/onepassword` creates `Secret/onepassword-secret`
- `ClusterSecretStore/onepassword` uses `Secret/onepassword-secret` to authenticate

Files in the reference repo:

- `kubernetes/apps/external-secrets/onepassword/app/externalsecret.yaml`
- `kubernetes/apps/external-secrets/onepassword/app/clustersecretstore.yaml`

---

## The chicken-and-egg problem (must be documented)

At first glance, this is circular:

- `ExternalSecret` needs a working `ClusterSecretStore`
- `ClusterSecretStore` needs credentials that are typically provided by… a Secret that the `ExternalSecret` would create

Therefore: a new cluster needs a **seed** path to create the first secret(s) required to reach the external secret backend.

---

## Seed strategies (choose one)

### Strategy A: Minimal SOPS “seed” (recommended for haynes-ops today)

Use SOPS only for the smallest possible set of bootstrap secrets, typically:

- 1Password Connect credentials/token secret (or equivalent)
- any Flux webhook tokens needed during bootstrap (optional)

Then everything else is sourced via External Secrets.

This keeps GitOps bootstrapping intact and avoids “kubectl create secret” day-0 steps.

What changes over time:

- We remove per-app SOPS secrets (inside app paths)
- We retain a *tiny* bootstrap SOPS footprint (likely in `flux-system` or a dedicated bootstrap path)

### Strategy B: Manual day-0 secret (not preferred for this repo)

Create the initial secret by hand (once) with `kubectl create secret ...`, then proceed with External Secrets.

This breaks “GitOps strictly” for day-0, so we generally avoid it here.

### Strategy C: Workload identity (if available)

Authenticate to the secret backend without a static secret committed anywhere (cloud IAM patterns).

Typically not available / not worth the complexity for a homelab.

---

## Proposed staged plan (haynes-ops)

### Phase 0: Inventory and classify

- List all `*.sops.yaml` used under `kubernetes/main/apps/**`
- For each, decide: **bootstrap seed** vs **app secret**

### Phase 1: Constrain SOPS to “seed only”

- Keep SOPS assets confined to `flux-system` (already the direction of `sops-scope-and-kustomization-namespacing.md`)
- Move per-app SOPS secrets out of app paths where possible

### Phase 2: Convert app secrets to ExternalSecret

Example target: replace

- `onepassword-connect.secret.sops.yaml`

with:

- an `ExternalSecret` that materializes the same keys into `external-secrets/onepassword-connect-secret`

### Phase 3: Namespace moves become simple

Once an app no longer applies `*.sops.yaml`, its Flux `Kustomization` can live in the app namespace without requiring `sops-age` in that namespace.

---

## Operational notes (risk)

- **Namespace moving a KS is delete+create**. Inventory pruning can delete resources; PVCs must be protected (`kustomize.toolkit.fluxcd.io/prune: disabled`).
- For VolSync-populated PVCs, KS moves can also trigger restore workflows; treat those moves as potentially data-affecting even if the PVC survives.

---

## Related docs

- `sops-scope-and-kustomization-namespacing.md`
- `flux-operator-migration.md` (reference repo uses External Secrets heavily)

