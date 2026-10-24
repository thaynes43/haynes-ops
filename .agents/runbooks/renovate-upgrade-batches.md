# Runbook: merging Renovate PRs in risk-tiered batches

How to work through a large backlog of Renovate update PRs safely: batch by blast radius, merge lowest-risk first, reconcile and verify after each batch, and roll back on any snag. This is the process used to clear ~50 PRs in one pass.

## Core loop (every batch)

Deliver **one pull request per command** and stop the batch on the first nonzero exit. Independent review of that exact head, both required GitHub checks green on that head, and a completed preflight are required before a merge. Permission is scoped to merging the named pull request. Pass `--reconcile namespace/name` only for the Flux kustomizations this pull request should roll out. `--source-only` merges the source pull request and does not reconcile.

A native Sol approval is source-review evidence for `--reviewed-head`. This command reads the required check status from GitHub. It does not read GitHub review decisions or agent comments, and those are not a substitute for the required checks.

```bash
python3 scripts/upgrade-agent/deliver_reviewed_pr.py \
  --repo thaynes43/haynes-ops \
  --pr <n> \
  --head <40-hex> \
  --reviewed-head <same-40-hex> \
  --preflight-timeout 120 \
  --reconcile <namespace>/<kustomization> \
  -- <preflight-command> <args>
```

Source-only, when this change must merge without a cluster reconcile:

```bash
python3 scripts/upgrade-agent/deliver_reviewed_pr.py \
  --repo thaynes43/haynes-ops \
  --pr <n> \
  --head <40-hex> \
  --reviewed-head <same-40-hex> \
  --preflight-timeout 120 \
  --source-only \
  -- <preflight-command> <args>
```

The command runs `<preflight-command>` as an argv list with a finite timeout. After that process exits 0 it requires `Flux Local - Success` and `Diff Scope - Success` to be completed and successful on the pull request head, and that head must equal `--head`. It squash-merges with `--match-head-commit`, verifies the pull request is merged, and only then runs `flux reconcile kustomization <name> --namespace <namespace> --with-source` for each named target, one at a time. A preflight that is still running, times out, or exits nonzero, a required check that is pending, failed, missing, or otherwise unknown, or a head mismatch stops the command before `gh pr merge` and before `flux`.

Supply one trusted preflight program with explicit arguments. It must check each internal prerequisite separately and propagate failure, including any required review-comment publication or activity declaration. Do not supply an opaque `bash -c` compound chain whose final success can hide an earlier failure. The delivery command checks the program's exit; it cannot establish whether an arbitrary program correctly checked its own internals. The reviewed-head flag records the caller's exact-source review evidence and grants no runtime permission or release of an existing hold.

If delivery returns nonzero, stop the batch and inspect the failed stage. Do not append another merge or reconcile command to continue after that failure.

Then **verify** the affected workloads (see Verification below). **Roll back on snag** (see Rollback) and research before retrying.

Branch protection requires both aggregate contexts, `Flux Local - Success` and `Diff Scope - Success`. Each pull request is delivered on its own head; another pull request's green checks do not cover it.

## Survey first

```bash
gh pr list --limit 200 --json number,title,labels,mergeable,statusCheckRollup \
  --jq 'sort_by(.number) | .[] | "\(.number)\t\(.mergeable)\t\(.title)"'
```
Note the `type/major|minor|patch` labels (Renovate adds them) and CI status. Flag:
- **`!` / `type/major`** titles — breaking, handle individually (Tier 3).
- **Superseded duplicates** — e.g. a `v84.5.0` minor and a `v86` major for the same chart; merge the newer, close the older.
- **Excluded PRs** — confirm with the user which to skip (e.g. config refactors that aren't updates), and which to save for last (storage).
- **`archive/**` / out-of-cluster paths** — Renovate is set to ignore `archive/**`; close any stragglers and don't merge them.

## Risk tiers (merge order)

| Tier | What | Approach |
|---|---|---|
| **1 — safe** | single-app image tag bumps (patch/minor): media, home-automation, UI, sidecars | Batch ~8 at a time, reconcile, broad pod sweep. Post-merge verification *is* the safety net here — no need to research every leaf app. |
| **2 — infra** | helm/images for infra: CNI (cilium), Flux, operators (CNPG, ESO, device plugins), DNS, monitoring exporters | Quick research on minors of operators/CRD-bearing charts. Watch operator-managed restarts (CNPG rolls Postgres instances; that's expected). |
| **3 — breaking (`!`)** | chart/app majors: app-template, traefik, prometheus stack+CRDs, etc. | **Research release notes first**, make required value edits, merge **one at a time**, verify, then next. |
| **4 — storage** | rook-ceph / Ceph | **Always last.** See the rook section — it has its own remediation. |

Within a tier, prefer lowest consequence first. Ingress (traefik) and SSO (authentik) sit high in their tier because breakage is user-visible.

## Tier 3 — breaking changes, one at a time

For each major:
1. **Google the release notes** (chart `UPGRADING`/GitHub release; `gh release view <tag> --repo <org/repo> --json body`).
2. **Grep our usage** for the affected features; make the required value edits in a commit.
3. **For a chart used in multiple places**, do one instance first, verify clean, then the rest (e.g. traefik-internal before traefik-external).
4. Deliver that pull request with the core-loop command, verify, then move on.

### Patterns seen
- **traefik v40**: `service` values moved under `service.spec` (k8s-aligned); strict-ish schema → migrate `service.type`/`loadBalancerSourceRanges`/`externalIPs`. Verify the LoadBalancer keeps its IP and routers load (`kubectl logs` the pods).
- **kube-prometheus-stack v84→v86**: distroless images + operator bump; CRDs live in the **separate `prometheus-operator-crds`** chart — bump it **first** (the stack `dependsOn` it). Distroless only bites if something execs a shell into those containers (we don't).
- **app-template v5** (shared base chart, pinned once in `kubernetes/shared/components/common/repos/app-template/ocirepository.yaml` → bumps ALL consumers at once): the dangerous default flip is `defaultPodOptions.automountServiceAccountToken: false`. Any app-template HR that **defines a `serviceAccount` + RBAC** (needs k8s API access) must set `automountServiceAccountToken: true` or it breaks (multus panics on missing token; homepage k8s widgets, gatus sidecar, dragonfly operator all need it). Find them: HRs referencing `name: app-template` that also have `serviceAccount:`.
- **`cephVersion`-style "follows chart default"**: if a value isn't pinned, a chart major can silently jump the underlying app a major version. **Check before merging** (see rook).

## Tier 4 — rook-ceph (storage), last and special

Rook majors reliably need remediation. For v1.19→v1.20 specifically (and the general method):
1. **Read the rook release + upgrade guide.** Current state: `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph version && ceph health`.
2. **Operator first (`rook-ceph` chart)** → cluster second (`rook-ceph-cluster`). v1.20 introduced a 3rd chart: **`ceph-csi-drivers`** (oci `ghcr.io/home-operations/charts-mirror/ceph-csi-drivers`). Order: **operator → ceph-csi-drivers → cluster**, wired via Flux `dependsOn`. `installCsiOperator: true` ships only the controller-manager; the drivers chart provides the per-driver **ServiceAccounts + RBAC + Driver CRs**. Without it, ctrlplugin (provisioner/attacher) pods fail with `serviceaccount "...-ctrlplugin-sa" not found` → new PVC provisioning + RBD re-attach break (existing mounts keep working). Mirror [dmfrey/home-gitops](https://github.com/dmfrey/home-gitops) `rook-ceph/csi-drivers/`. CSI driver settings that left the operator chart (`cephFSKernelMountOptions: ms_mode=prefer-crc` — required because `connections.requireMsgr2: true`; `enableLiveness`) move to the drivers chart's `drivers.cephfs.kernelMountOptions` / `liveness`.
3. **Pin the Ceph version.** The v1.20 `rook-ceph-cluster` chart defaults Ceph to a new **major** (v20 Tentacle). If `cephClusterSpec.cephVersion` is unpinned you'll jump Ceph majors as a side effect. Decide deliberately — Ceph majors can't be downgraded, so pinning to the current squid release decouples the rook chart upgrade from the Ceph upgrade.
4. **Adopting pre-existing CRs into Helm**: if the drivers chart's `OperatorConfig`/`Driver` CRs already exist, pre-annotate them so Helm adopts instead of erroring:
   ```bash
   kubectl -n rook-ceph annotate <res> meta.helm.sh/release-name=ceph-csi-drivers meta.helm.sh/release-namespace=rook-ceph --overwrite
   kubectl -n rook-ceph label <res> app.kubernetes.io/managed-by=Helm --overwrite
   ```
5. **Monitor throughout.** `ceph -s` should stay HEALTH_OK / transient HEALTH_WARN as daemons roll (mgr → mons one-at-a-time keeping quorum → OSDs per-node; degraded% spikes then recovers). The user has a `watchrook.sh` for this. Verify at the end: new PVC provisions (`ceph-block` + `ceph-filesystem`) AND mounts.

## Verification

After each reconcile:
```bash
# fresh unhealthy pods cluster-wide (curate the exclude list to known pre-existing noise)
kubectl get pods -A | grep -ivE "Running|Completed|^NAMESPACE" \
  | grep -vE "<known-stale-pods>"
# the affected app(s)
kubectl get pods -n <ns> -l app.kubernetes.io/name=<app>
flux get helmrelease -A | grep -ivE "True"   # any HR not Ready
```
- **Transient image-pull errors** (`connection reset by peer`, Spegel `not found`) usually self-heal on backoff — wait ~60–90s and recheck before assuming a bad upgrade. Persistent pulls failing on **one node** point at that node, not the upgrade (a flaky node's egress can masquerade as upgrade breakage).
- **Stale `Error`/`Completed` pods** with old AGE are pre-existing GC leftovers, not your change — confirm by age and by a healthy running counterpart.

## Rollback

- No auto-rollback unless the HR sets `upgrade.remediation.strategy: rollback`. Otherwise revert the Git change (`git revert` or re-pin the version) and reconcile.
- For a shared/centralized version (app-template OCIRepository, etc.), reverting affects all consumers — weigh that vs. a forward fix.
- Some upgrades can't be cleanly rolled back (Ceph majors). Pin conservatively up front so you keep the rollback option.

## Cleanup

- Close superseded/duplicate PRs with a comment pointing at the commit that replaced them.
- If you create test PVCs/pods to verify (e.g. a CephFS mount probe), **delete them in the same step** — don't background a test+cleanup and walk away (a leftover `Completed` pod pins the `pvc-protection` finalizer and leaves a PVC stuck `Terminating`).
