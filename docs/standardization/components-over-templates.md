## Components over templates

Your repo currently has both:

- `kubernetes/shared/templates/` (copied YAML patterns)
- `kubernetes/shared/components/` (Kustomize Components + reusable overlays)

The goal is to converge on **components** as the primary reuse mechanism so we don’t maintain two drifting sources of truth.

### Why components

- **DRY and consistent**: one implementation of a pattern (volsync/gatus/alerts/etc.).
- **Safer upgrades**: changing a component is still risky, but it is at least centralized and reviewable.
- **Closer to the reference**: `example-ops/onedr0p-home-ops` uses `kubernetes/components/...` and references them from app `ks.yaml`.

## Current state inventory (haynes-ops)

### Templates present

- `kubernetes/shared/templates/gatus/`
  - `external/`
  - `gaurded/` (note spelling; likely meant `guarded`)
- `kubernetes/shared/templates/volsync/`
  - `app-pvc/`
  - `extra-pvc/`

### Components present

- `kubernetes/shared/components/common/`
  - `alerts/` (alertmanager + github-status)
  - `repos/` (includes `app-template` OCIRepository)
  - `sops/`
- `kubernetes/shared/components/gatus/`
  - `external/`
  - `gaurded/` (same spelling issue)
- `kubernetes/shared/components/volsync/aws/`

Observation:

- Many templates appear to have a “component equivalent” already (`gatus/*`, `volsync/*`).
- This is a **quick win** area: choose one, delete the other later (after confirming no output change).

## Target state

- Treat `kubernetes/shared/components/**` as the **only** reuse mechanism.
- Phase out `kubernetes/shared/templates/**` once all consumers are migrated.

## Migration approach (safe and efficient)

### Step 1: Identify consumers

For each template directory, search for references in kustomizations.

Examples:

```bash
rg -n "shared/templates/gatus|shared/templates/volsync" kubernetes
```

Record:

- which cluster(s): `main`, `edge`, or `shared`
- which apps/namespaces reference it
- whether the consumer is a Flux `Kustomization` (`ks.yaml`) or a Kustomize `kustomization.yaml`

### Step 2: Map template → component

Create a simple mapping table during migration (keep it in this doc as you work):

|Template|Component|Notes|
|---|---|---|
|`shared/templates/gatus/external`|`shared/components/gatus/external`|Expected to be 1:1|
|`shared/templates/gatus/gaurded`|`shared/components/gatus/gaurded`|Spelling fix is a separate change|
|`shared/templates/volsync/app-pvc`|`shared/components/volsync/aws`|May not be 1:1; inspect values/claims|
|`shared/templates/volsync/extra-pvc`|`shared/components/volsync/aws`|May require additional mounts/claims|

### Step 3: Migrate one consumer at a time (edge first if unsure)

Rules:

- **Do not** mix “refactor structure” with “behavior change” in the same commit.
- Keep diffs small enough that a revert is safe.

### Step 4: Verify rendered output is effectively unchanged

Suggested verification on the cluster:

- reconcile and check health:

```bash
flux reconcile kustomization <name> -n flux-system --with-source
flux get ks -A
flux get hr -A
```

- inspect the affected resources (labels/kinds/names) before and after

If the change *does* alter output, document it explicitly here as a deliberate behavior change.

### Step 5: Remove templates

Only after all consumers are migrated and verified:

- remove `kubernetes/shared/templates/*`
- keep components as the single source of truth

## “Gotchas” to avoid

- **Component vs plain kustomization**: Kustomize Components are `apiVersion: kustomize.config.k8s.io/v1alpha1` and `kind: Component`. Consumers may need `components:` instead of `resources:` depending on how you wire them in.
- **Path depth**: when referencing components from app `ks.yaml`, use stable relative paths. Copy the reference pattern where possible.
- **Spelling**: `gaurded` exists in both templates and components. Renaming it to `guarded` is good hygiene but should be staged as a separate, mechanical refactor (to avoid breaking references).

