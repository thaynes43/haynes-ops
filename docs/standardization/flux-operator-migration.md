## Flux Operator migration (reference-aligned, edge-first)

This doc explains how we would migrate `haynes-ops` from “Flux installed via flux2 install manifests (GOTK style)” to “Flux managed by Flux Operator + Flux Instance”, similar to `example-ops/onedr0p-home-ops`.

This is a **separate project** from chartRef/component/global-patches work because it changes how Flux itself is installed and configured.

### TL;DR

- **You do not need Flux Operator** to do the current standardization work.
- If you adopt it, do it **edge-first** with a clean rollback plan.
- You can keep SOPS, External Secrets, or both. The reference repo uses **External Secrets heavily**.

## Current state (haynes-ops)

Flux components are installed by a non-Flux-tracked bootstrap kustomization:

- `kubernetes/main/bootstrap/flux/kustomization.yaml`
- `kubernetes/edge/bootstrap/flux/kustomization.yaml`

Both pull:

- `github.com/fluxcd/flux2/manifests/install?...`

Then Flux is configured via the normal Flux CRDs under:

- `kubernetes/*/flux/config/*`
- `kubernetes/*/flux/apps.yaml`

## Reference state (example-ops/onedr0p-home-ops)

The reference repo installs Flux as two apps in `flux-system`:

- **`flux-operator`**: installs the operator itself
- **`flux-instance`**: defines the Flux instance (which controllers, sync settings, controller patches, etc.)

Key examples:

- `kubernetes/apps/flux-system/flux-operator/app/helmrelease.yaml`
- `kubernetes/apps/flux-system/flux-instance/app/helmrelease.yaml`

Notable: the instance HelmRelease includes configuration such as:

- Which controllers are enabled (source/kustomize/helm/notification)
- The Git sync URL/ref/path
- Patches that tune controllers (workers, memory limits, caching, feature gates)

## Secrets management note (External Secrets vs SOPS)

### What the reference repo does

The reference repo uses External Secrets to populate Flux-related secrets (example: GitHub webhook token):

- `kubernetes/apps/flux-system/flux-instance/app/externalsecret.yaml`

That ExternalSecret reads from a `ClusterSecretStore` (OnePassword Connect in the reference repo):

- `kubernetes/apps/external-secrets/onepassword/app/clustersecretstore.yaml`

### What haynes-ops does today

You already have both patterns in this repo:

- **SOPS** for `flux-system` “cluster vars”:
  - `kubernetes/main/flux/vars/cluster-secrets.sops.yaml`
- **External Secrets** used by many apps (including Flux notifications):
  - e.g. `kubernetes/main/apps/flux-system/addons/app/notifications/github/externalsecret.yaml`

So the migration question is not “SOPS vs External Secrets”. It’s “what secrets does Flux Operator/Instance need, and how do we source them safely”.

## Migration goals

Pick the minimal set of goals up front:

- Manage Flux controller deployment via Flux Operator (instead of bootstrapped GOTK install manifests)
- Keep the same Git source of truth and sync paths
- Preserve existing Flux objects and app reconciliation behavior as much as possible

## High-risk areas (plan for them)

- **Control plane of GitOps changes**: you are changing the system that applies everything else.
- **Double-managing controllers**: if GOTK controllers and operator-managed controllers overlap, you can end up with conflicting deployments/CRDs.
- **CRDs**: operator/instance distribution will install CRDs; you must avoid multiple conflicting CRD managers.
- **Sync “path” differences**: reference repo syncs `kubernetes/flux/cluster`; you sync `kubernetes/<cluster>/flux` and `kubernetes/<cluster>/apps` via different Kustomizations.

## Edge-first migration outline (recommended)

### Phase 0: Decide the end-state wiring

Answer these (document the decisions in this doc as you proceed):

- Where should the “instance sync path” point for edge?
  - likely `kubernetes/edge/flux/config` or a new dedicated path that only defines flux config
- Do we keep `cluster` and `cluster-apps` Kustomizations as-is?
  - recommended: yes, initially
- Do we keep SOPS for cluster vars?
  - recommended: yes (add ExternalSecrets only where needed)

### Phase 1: Ensure External Secrets baseline exists on edge (if required)

If you want Flux webhook tokens or operator credentials sourced dynamically, ensure `external-secrets` is installed and a `ClusterSecretStore` exists on edge.

### Phase 2: Install Flux Operator on edge (as an app)

Mirror the reference repo structure in edge (names and locations can differ, but keep the concept):

- Add `flux-operator` app to `kubernetes/edge/apps/flux-system/` using:
  - an `OCIRepository` for the chart
  - a `HelmRelease` to install it

### Phase 3: Install Flux Instance on edge

Add `flux-instance` app that declares:

- instance distribution artifact version
- enabled components
- sync settings (repo URL/ref/path)
- controller tuning patches

### Phase 4: Prevent controller conflict

Before turning off GOTK-managed controllers, confirm:

- operator-managed controllers are running and healthy
- they are reconciling correctly (sources/ks/hr)

Then disable/remove the old controller deployments installed by the bootstrap method.

This step is the highest risk. Do it on edge and only once you have a rollback path (git revert and/or re-apply bootstrap install).

### Phase 5: Roll forward to main

After edge is stable for a while:

- repeat the process on main, with a maintenance window mindset

## Verification checklist (edge)

After each phase:

```bash
flux check
flux get sources all -A
flux get ks -A
flux get hr -A
kubectl -n flux-system get pods
kubectl get events -A --sort-by=.lastTimestamp | tail -n 50
```

## Rollback strategy (edge)

Rollback should be “simple and fast”:

- revert the commit that introduced operator/instance
- re-apply the bootstrap Flux install manifests if controllers were removed

Do not attempt a partial rollback that leaves two controller sets alive.

## Recommended sequencing with our other standardization work

Do not attempt Flux Operator migration while you are simultaneously doing:

- large `chartRef` migrations
- major global patch behavior changes
- ingress/gateway migrations

Treat Flux Operator migration as its own project, after edge is stabilized.

