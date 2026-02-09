## Gatus deployment alignment (matching `example-ops/onedr0p-home-ops`)

This repo historically deployed Gatus using:

- A `HelmRelease` based on `app-template` v3
- A sidecar (`k8s-sidecar`) to watch `gatus.io/enabled` ConfigMaps across namespaces
- Postgres (`cloudnative-pg`) + init container (`postgres-init`)
- Flux global substitution/SOPS injection (repo-wide default behavior)

The reference repo (`example-ops/onedr0p-home-ops`) has moved to a simpler pattern:

- SQLite on a local PVC
- `gatus-sidecar` to auto-discover endpoints from Kubernetes resources
- Per-app `OCIRepository` + `HelmRelease.spec.chartRef`
- Explicitly disabling Flux substitution for the generated configmap so `${VAR}` remains for **Gatus runtime** env var expansion

### Why this matters for “substitution patterns”

Gatus config supports `${ENV_VAR}` expansion at runtime.

In `haynes-ops`, Flux currently injects substitution defaults globally, which means `${VAR}` inside generated ConfigMaps can be interpreted by Flux during build time. The reference repo avoids this by adding:

```yaml
generatorOptions:
  annotations:
    kustomize.toolkit.fluxcd.io/substitute: disabled
```

We now do the same for Gatus configmaps.

### Target pattern (what we’re aligning to)

- **Storage**: SQLite (no Postgres dependency)
- **Controller**: StatefulSet + `volumeClaimTemplates` for `/config`
- **Endpoint discovery**: `gatus-sidecar` init container
- **Chart sourcing**: `HelmRelease.spec.chartRef` -> per-app `OCIRepository` (named `gatus`)

### Operational implications / gotchas

- **PVC naming changes**: with `volumeClaimTemplates` the created PVC name is derived from the template + StatefulSet name (e.g. `config-gatus-0`), not a stable `gatus` PVC name.
  - This does **not** prevent VolSync, but it does make generic “`${APP}`-named claim” templates harder.
- **Chart upgrade risk**: switching from `app-template` v3 to v4 (via `chartRef` to an `OCIRepository`) can trigger immutable-field issues for Deployments.
  - For Gatus we change controller type to StatefulSet anyway; plan for a delete/recreate of old workloads if Helm can’t mutate cleanly.
- **Old PVC cleanup**: if a previous `gatus` PVC exists (from the “existingClaim” pattern), it may become unused after the switch and should be cleaned up deliberately once you’re satisfied with the new deployment.

### Rollout steps (main cluster)

1. Ensure the new manifests are committed (GitOps source of truth).
2. Reconcile the app:

```bash
flux reconcile kustomization gatus -n flux-system --with-source
flux reconcile helmrelease gatus -n observability --with-source
```

3. If Helm gets stuck and the old workload blocks the new controller type:
   - Suspend the HelmRelease, delete the old Deployment/StatefulSet, resume and reconcile (same pattern as the `app-template` v3 -> v4 remediation).

### Follow-ups (optional)

- Decide whether we still want the legacy “labelled ConfigMap endpoint injection” (`gatus.io/enabled`) approach anywhere.
  - If the `gatus-sidecar` approach is sufficient, we can phase out `kubernetes/shared/templates/gatus/*` over time.

