## Standardization roadmap (haynes-ops)

This folder is the **planning + runbook hub** for modernizing `haynes-ops` toward the conventions used by the broader **home-ops community**, using two concrete references:

- The reference repo included in this workspace: `example-ops/onedr0p-home-ops`
- The widely-forked template many home-ops repos are based on: [`onedr0p/cluster-template`](https://github.com/onedr0p/cluster-template)

The goal is to make it easier to:

- Lift patterns directly from kubesearch examples
- Reduce YAML duplication (use components + global defaults)
- Manage risk during reconciles (batching, rollback paths, edge-first where needed)

### Principles

- **Docs first**: we do not refactor manifests until the docs/runbooks are agreed.
- **GitOps strictly**: changes happen through commits; Flux applies them.
- **Blast radius control**: change one dimension at a time (structure vs behavior vs versions).
- **Edge as proving ground**: if the change is risky or hard to roll back, validate on `edge` first.
- **Assume immutable fields exist**: Deployments/StatefulSets can require delete/recreate when selectors/labels change.

### Current repo realities (important constraints)

- **Two clusters**: `kubernetes/main` and `kubernetes/edge`.
- **Flux entry points**:
  - `kubernetes/*/flux/config/cluster.yaml` (GitRepository + `cluster` Kustomization)
  - `kubernetes/*/flux/apps.yaml` (the `cluster-apps` Kustomization that points at `kubernetes/*/apps`)
  - `kubernetes/*/flux/repositories/kustomization.yaml` (applies `kubernetes/shared/repositories`)
- **Shared resources** live under `kubernetes/shared/` (repositories, components, etc.).

### Target direction (adapted, not copied blindly)

We’re aiming for:

- **Components-first** (Kustomize Components for repeatable patterns like volsync, alerts, gatus)
- **Fewer “templates”** (avoid duplicated YAML that drifts)
- **Chart sources standardized** (clear, consistent `HelmRelease` chart sourcing)
- **Global defaults** applied by Flux root Kustomization patches (where safe), similar to the patterns in the reference repo

Important nuance (about the reference patterns):

- `example-ops/onedr0p-home-ops` uses **per-app `OCIRepository`** objects and strong global patching defaults.
- `haynes-ops` currently uses **shared repositories** under `kubernetes/shared/repositories/`.

Neither is “the one true way” for all home-ops repos; we’re choosing what fits this repo best while reducing operational risk.

## Work phases (ordered)

### Phase 0: Documentation set (now)

Definition of done:

- This README explains the ordering, risk, and how to run/verify each phase.
- Each risky/tedious phase has a breakout runbook.

### Phase 1: Edge stabilization (prereq)

We want `edge` to be a reliable proving ground before we take on risky refactors.

- Runbook: `edge-stabilization.md`

### Phase 2: Quick wins (low risk, high value)

These should be mostly additive or purely structural:

- Remove/merge obvious duplication between `kubernetes/shared/templates/` and `kubernetes/shared/components/` where it doesn’t change output.
- Adopt a consistent component usage approach and document it.

- Runbook: `components-over-templates.md`

### Phase 3: Standardize chart sourcing (medium risk)

Standardize `HelmRelease` chart sourcing patterns, starting with the `app-template` fleet, with careful batching and known recovery procedures.

- Runbook: `helmrelease-chartref-migration.md`
- Related decision: `repository-source-strategy.md`

### Phase 4: Flux global defaults / patches (medium to high risk)

Expand Flux root Kustomization patching to reduce boilerplate and make remediation behavior consistent. Some defaults can materially change reconcile behavior, so we stage this carefully.

- Runbook: `flux-global-patches.md`

### Phase 4.5: Flux Operator migration (very high risk, edge first)

Migrating to Flux Operator changes how Flux itself is installed and configured. Keep this as a separate project with a dedicated rollout and rollback plan.

- Runbook: `flux-operator-migration.md`

### Phase 5: Network modernization (high risk)

Ingress → Gateway API (Traefik provider changes + resource type migrations) can cause downtime if done incorrectly. Keep this separate from other refactors.

- Existing notes (to be split later): `todo-refactor.md`

## Standard commands (copy/paste)

### Flux status

```bash
flux check
flux get ks -A
flux get hr -A
```

### Reconcile a specific resource

```bash
flux reconcile helmrelease <name> -n <namespace> --with-source
flux reconcile kustomization <name> -n flux-system --with-source
```

### Immutable selector remediation (pattern)

When you see `spec.selector ... field is immutable`:

```bash
flux suspend helmrelease <name> -n <namespace>
kubectl -n <namespace> get deploy,sts,ds,cronjob -l helm.toolkit.fluxcd.io/name=<name>
kubectl -n <namespace> delete deployment|statefulset|daemonset <workload-name> --wait=true
flux resume helmrelease <name> -n <namespace>
flux reconcile helmrelease <name> -n <namespace> --with-source
```

### Incident note: `comfyui` rollback during `app-template` v3 → v4

During the `chartRef` migration / `app-template` v3→v4 upgrade, `HelmRelease/ai/comfyui` failed and rolled back even though the Flux `Kustomization` looked “clean” at a glance.

- **Why it failed**: Kubernetes forbids changes to most `StatefulSet.spec` fields. The chart upgrade attempted a forbidden `StatefulSet` change, so Helm failed the upgrade and **rolled back** to `app-template@3.7.3`.
- **Why KS didn’t obviously show it**: `comfyui` is applied with `wait: false`, so the KS primarily reflects “applied manifests”, not “Helm upgrade succeeded”. The HelmRelease status is the source of truth for chart upgrade outcomes.
- **Remediation pattern**: suspend HR → delete the blocking workload (StatefulSet for `comfyui`, Deployment for `ollama-*`) → resume + reconcile HR, then reconcile KS to refresh its health.

## Breakout documents (index)

- `edge-stabilization.md`: get `edge` to a trustworthy baseline
- `components-over-templates.md`: converge on components; stop template drift
- `sops-scope-and-kustomization-namespacing.md`: keep SOPS confined to `flux/vars` while allowing app `Kustomization`s outside `flux-system`
- `seed-secrets-and-removing-sops.md`: future task — external-secrets seed strategy + removing per-app `*.sops.yaml`
- `helmrelease-chartref-migration.md`: migrate `HelmRelease` to `chartRef` safely (batching + recovery)
- `flux-global-patches.md`: staged approach to onedr0p-style global defaults
- `flux-operator-migration.md`: edge-first migration plan to Flux Operator + Flux Instance
- `health-signals-with-wait-false.md`: how the reference repo gets strong health signals without relying on KS `wait: true`
- `gatus-deployment-alignment.md`: align Gatus deployment + substitution behavior to the reference repo
- `repository-source-strategy.md`: decide shared vs per-app OCI sources, and how that affects migrations
- `todo-refactor.md`: backlog (includes Gateway API migration ideas; treat as high risk)