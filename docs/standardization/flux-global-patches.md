## Flux global patches / defaults

This doc explains how to reduce per-app boilerplate by applying **global defaults** at the root Flux Kustomization layer, and how to do it safely.

### Why this is worth doing

- Historically, this repo applied shared SOPS + substitutions via patches in:
  - `kubernetes/main/flux/apps.yaml`
  - `kubernetes/edge/flux/apps.yaml`
- The reference repo (`example-ops/onedr0p-home-ops`) takes this further by also injecting **HelmRelease remediation defaults** via patches in:
  - `d:/labspace/example-ops/onedr0p-home-ops/kubernetes/flux/cluster/ks.yaml`

The outcome is fewer repeated fields across `ks.yaml` and `helmrelease.yaml`, and more consistent remediation behavior.

## Current state (haynes-ops)

`haynes-ops` is moving toward the reference repo’s “explicit per-app settings” model:

- `cluster-apps` no longer injects `postBuild.substituteFrom` by default
- SOPS decryption is configured explicitly on the few Flux `Kustomization` CRs that actually apply `*.sops.yaml`

## Reference pattern (example-ops/onedr0p-home-ops)

The reference repo applies a patch to *all child Kustomizations* that itself contains a patch targeting HelmReleases, to inject default remediation and CRD handling.

Key ideas (not copy/paste exact, but the pattern is useful):

- HelmRelease defaults like:
  - `install.crds: CreateReplace`
  - `upgrade.crds: CreateReplace`
  - aggressive remediation strategies
  - rollback cleanup/recreate

This is powerful, but it changes how upgrades behave and must be staged carefully.

## Staged adoption plan (recommended)

### Stage A: Safe, additive defaults (low risk)

Goal: reduce noise without changing upgrade semantics.

Candidate defaults to inject:

- Standardize `spec.interval` for Kustomizations (already mostly done).
- Add consistent `wait: false` or `wait: true` policy (only if you’re confident).
- Add a standard `timeout` (again, only if you’re confident).

Rollout:

- Start on `edge`.
- Add patch logic to `kubernetes/edge/flux/apps.yaml`.
- Reconcile `cluster-apps` and ensure no child Kustomizations become NotReady.

### Stage B: HelmRelease remediation defaults (medium to high risk)

Goal: make Helm behavior consistent, especially around failures and CRDs.

Candidate defaults (examples from the reference pattern):

- CRD upgrade/install:
  - `spec.install.crds: CreateReplace`
  - `spec.upgrade.crds: CreateReplace`
- Consistent rollback/upgrade remediation:
  - `cleanupOnFail: true`
  - `remediation.retries` bounds

Why it is risky:

- CRD replace behavior can be disruptive if a chart manages CRDs unexpectedly.
- “Recreate on rollback/upgrade” can cause extra restarts.

Rollout:

- Apply to `edge` first.
- Keep the patch scoped via `target` selectors (labels or name patterns) if possible.
- Consider opt-out labels (e.g., allow a HelmRelease to disable defaults).

### Stage C: Structural simplification (optional)

Once defaults are stable:

- remove redundant blocks from individual `ks.yaml` (but only when verified)

## Suggested implementation technique

In `kubernetes/*/flux/apps.yaml` (root `cluster-apps` Kustomization), add a patch targeting child Kustomizations, and inside it include a `spec.patches` entry targeting HelmReleases.

Key safety tactics:

- Prefer **opt-in selectors** for higher-risk behavior changes (safer rollout), or opt-out selectors for low-risk defaults.
- **Batch and reconcile**: add defaults, reconcile, and check readiness before continuing.

## Operational commands

### Reconcile root

```bash
flux reconcile kustomization cluster -n flux-system --with-source
flux reconcile kustomization cluster-apps -n flux-system --with-source
```

### Verify nothing regressed

```bash
flux get ks -A
flux get hr -A
kubectl get events -A --sort-by=.lastTimestamp | tail -n 50
```

## Exit criteria (definition of done)

- Root patches applied on `edge` without introducing new NotReady resources.
- Defaults documented (what we set, why, and how to opt out).
- Only then consider rolling the same patches to `main`.

