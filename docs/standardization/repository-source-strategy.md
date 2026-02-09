## Repository source strategy (HelmRepository vs OCIRepository, shared vs per-app)

This repo is mid-transition between two viable patterns for chart sourcing. This doc makes the decision explicit and ties it directly to the `HelmRelease.chartRef` migration work.

## The two patterns

### Pattern A: Shared sources (current haynes-ops direction)

Concept:

- Define chart sources centrally under `kubernetes/shared/repositories/` and/or `kubernetes/shared/components/common/repos/`.
- `HelmRelease` objects reference those shared sources (cross-namespace via `spec.chartRef.namespace: flux-system`).

What you have today:

- Many shared `HelmRepository` resources under `kubernetes/shared/repositories/helm/`
- A shared `OCIRepository/app-template` under `kubernetes/shared/components/common/repos/app-template/ocirepository.yaml`

Pros:

- Less per-app YAML (one chart source for many apps).
- Central governance of versions.

Cons:

- **Blast radius**: bumping the shared source version upgrades many apps at once.
- Harder to do “migrate one app at a time” chart source/version strategies.

### Pattern B: Per-app OCIRepository (reference repo pattern)

Concept:

- Each app defines its own `OCIRepository` in its `app/` directory (usually named after the app).
- `HelmRelease.spec.chartRef` points at that app’s OCIRepository.

Example from `example-ops/onedr0p-home-ops`:

- `kubernetes/apps/default/atuin/app/ocirepository.yaml` defines `OCIRepository/atuin` pointing at `app-template` tag `4.6.2`
- `kubernetes/apps/default/atuin/app/helmrelease.yaml` uses:
  - `spec.chartRef.kind: OCIRepository`
  - `spec.chartRef.name: atuin`

Pros:

- **Fine-grained upgrades**: version bumps affect a single app.
- Easier to trial and roll back per app.

Cons:

- More objects/YAML in the repo.
- Needs naming conventions and discipline to stay consistent.

## How this impacts `chartRef` migration (app-template fleet)

Your current `chartRef` migration doc (`helmrelease-chartref-migration.md`) is primarily about changing:

- from `spec.chart.spec` (HelmRepository + chart name/version)
- to `spec.chartRef` (OCIRepository reference)

But **the source strategy decision controls the risk**:

- If you use **shared** `OCIRepository/app-template`, then changing its `spec.ref.tag` can upgrade dozens of apps.
- If you use **per-app** OCIRepositories, the migration can be done app-by-app with a smaller blast radius.

## Recommended approach (pragmatic)

Given your repo structure and desire for efficiency:

1. **Short-term**: keep the shared `OCIRepository/app-template` for app-template to minimize YAML churn.
2. Add guardrails:
   - Document batching and recovery (already captured in `helmrelease-chartref-migration.md`).
   - Avoid bumping `app-template` tag during the mechanical migration unless intentionally planned.
3. **Medium-term**: for apps that repeatedly need special handling (or where independent upgrades are valuable), consider migrating those specific apps to **per-app OCIRepository** later.

This keeps the migration work focused while leaving a path to the reference-repo pattern where it provides clear value.

## Decision rule for new services (what we should do going forward)

Use this rule of thumb to avoid rework while you’re onboarding many new services:

- **Prefer shared sources** when:
  - many apps intentionally share the same chart (e.g., `app-template`), and
  - you’re comfortable upgrading them as a fleet (or at least accepting a larger blast radius)

- **Prefer per-app `OCIRepository`** when:
  - the chart is effectively “owned by” one app (no other app will ever reference it), or
  - you want strict blast-radius control for version bumps, or
  - the app/chart has special upgrade behavior and you want to stage upgrades independently

Concrete example:

- **Per-app makes sense** for things like `kubernetes/main/apps/storage/rook-ceph/` where nothing else would ever need to reference the same `OCIRepository`.

## Decision checklist

Answer these before changing the strategy:

- Do we want **one PR** to upgrade app-template everywhere, or **N PRs** per app?
- Are we comfortable with a shared source bump triggering selector immutability events across many apps?
- Do we want Renovate to manage versions centrally or per app?

## Operational notes (Flux + namespaces)

- `OCIRepository` is **namespaced**. If it’s shared, it should live in `flux-system` and all `HelmRelease.spec.chartRef.namespace` should explicitly point there.
- If per-app OCIRepositories are used, the OCIRepository typically lives in the **same namespace** as the app’s resources (or at least is managed in the same kustomization path). Make the namespace choice explicit and consistent.

