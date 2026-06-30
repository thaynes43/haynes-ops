# Tier-4 upgrade-shepherd

The master operating manual for the Tier-4 upgrade-shepherd: **one agent, three
invocation modes**, the thing that finally takes the irreducible-manual upgrades
off your hands. The design — why one agent, why these three modes, why a GitHub
App + a read-only cluster SA — is locked in
[`docs/renovate/README.md` → Tier 4](../../docs/renovate/README.md#tier-4--stateful-operators-with-a-health-gate);
this runbook is *how to run it*.

| Mode | Trigger | Job | Writes? |
|---|---|---|---|
| **1 — scheduled health gate** | cron / `/loop`, post-reconcile | run health checks, page on regression | read + page only |
| **2 — summoned remediation** | alert fires / stuck `Kustomization` / spotted regression | diagnose → attempt documented rollback → page if it won't converge | git revert/re-pin (Flux applies) |
| **3 — breaking-change shepherd** | manual-tier Renovate PR sits in the queue | read notes → make supporting edits → merge one-at-a-time → reconcile → gate | git edits/PR/merge (Flux applies) |

The shepherd never blind-merges a stateful upgrade. Its whole value is the work a
pre-merge PR check structurally cannot do: reading release notes, grepping our
usage, making the supporting `values` edits, and watching the *post-reconcile*
failure modes that only show up after Flux applies.

---

## Credentials & guardrails

Two credentials, deliberately split — neither single leak yields write-to-cluster
([rationale](../../docs/renovate/tier4-bot-setup.md#why-a-github-app-and-not-a-pat)).

### Push / PR / merge identity = `haynes-ops-bot`

Mint a 1-hour installation token from the App private key and hand it to `gh`/`git`:

```bash
export GH_TOKEN="$(scripts/github-app-token.sh)"           # reads GITHUB_BOT_APP_* from env (1Password item 'github-bot', vault HaynesKube)
git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/thaynes43/haynes-ops.git"
gh pr list                                                  # now acts as haynes-ops-bot[bot]
```

Re-mint before the 1-hour expiry on long runs. Creds live at
`op://HaynesKube/github-bot/<FIELD>`; in-cluster they sync via the
`onepassword-connect` ClusterSecretStore. Setup + threat model:
[`tier4-bot-setup.md`](../../docs/renovate/tier4-bot-setup.md).

**Why the App and not `GITHUB_TOKEN`:** identity, not triggering. The
`GITHUB_TOKEN`-doesn't-fire-downstream-workflows restriction only applies *inside*
an Actions run — but the point is the bot needs a **non-human, scoped, revocable**
identity whose pushes/PRs trigger the `Flux Local - Success` check. The App is
that identity (Contents:write + Pull requests:write + Checks:read, single-repo,
no webhook). It is **not** in any branch-protection bypass list — even a leaked
token can't reach `main` without a green flux-local.

### Verify on the read-only Omni Reader kubeconfig

All cluster reads (`kubectl get/logs/describe`, `flux get`, `scripts/checkHealth.sh`)
run on the read-only **Omni Reader** SA kubeconfig
([runbook](./omni-service-account.md)) — headless, no browser-OIDC dance.

- **NEVER `flux reconcile … --with-source`.** It annotates the Kustomization = a
  write the Reader role is denied. After a merge, rollback push, or value edit,
  **rely on the Flux Receiver / poll interval** to apply it. (Note: `main` has no
  push Receiver for the GitRepository — budget up to the **30-minute** poll before
  the change is even *seen*; the Reader can't accelerate it.)
- **Any cluster WRITE is BREAK-GLASS / human.** `kubectl delete/scale/annotate`,
  `flux suspend/resume`, finalizer edits, PVC/pod deletion, reseeds — all live
  outside the Reader SA. The shepherd's recovery path is **git → Flux**, full
  stop. When git-alone won't converge (immutable fields, wedged HelmReleases,
  stuck finalizers), it **pages with the diagnosis + runbook link** and stops.

### Scope & cadence

- **Edit only `kubernetes/**`.** The flux-local gate's test/diff jobs run *only*
  on `kubernetes/**` changes, and `Flux Local - Success` passes green on a
  *skipped* job — so a bot PR touching `.github/**` or repo root earns a
  trivially-green check. Constrain every shepherd edit to `kubernetes/**`. (The
  one exception is `.renovate/holds.json5` for the holds protocol below — that's a
  config write, not a cluster change, and never claims a flux-local pass.)
- **All changes via Git → Flux.** No `kubectl apply` for persistent state. Repo is
  source of truth.
- **One PR at a time** for the manual tier. Merge, reconcile, verify *green*, then
  the next. Lowest blast radius first; must-move-together pairs together (below).

---

## Mode 3 — breaking-change shepherd (the core loop)

The manual-tier set that will never blind-auto-merge: database operators
(`cnpg`, `dragonfly-operator`, `emqx`), `cilium`, `coredns`, `traefik`,
`authentik`, `multus`, `device-plugins`, `rook-ceph`, `flux`. (Talos/k8s is its
own beast — it's *not* a Flux/Renovate flow; see
[talos-version-upgrade.md](./talos-version-upgrade.md).) These open **ordinary
Renovate PRs** (no allowlist, never auto-merge) — that is the shepherd's input
queue.

This is [`renovate-upgrade-batches.md` → Tier 3](./renovate-upgrade-batches.md#tier-3--breaking-changes-one-at-a-time)
made on-call. Per-component specifics (release-note URLs, the exact grep, the
required `values` edit, health queries, rollback steps) live in
[`tier4-component-playbooks.md`](tier4-component-playbooks.md) —
**open the component's section before touching its PR**.

### The loop

1. **Survey the queue.** Lowest blast radius first.
   ```bash
   gh pr list --limit 200 --json number,title,labels,mergeable,statusCheckRollup \
     --jq 'sort_by(.number) | .[] | "\(.number)\t\(.mergeable)\t\(.title)"'
   ```
   Note `type/major|minor|patch` labels. Pick the next PR by the
   [merge-order table](#component-playbooks--rollback) — coredns/traefik/multus
   before cilium/rook/cnpg/authentik; storage (rook) and Talos last.

2. **CONSULT the holds registry FIRST.**
   [`.renovate/holds.json5`](../../.renovate/holds.json5) is the final word — it
   `extends` last, so a hold beats any allowlist.
   ```bash
   grep -A6 "$PKG" .renovate/holds.json5    # read the HELD/Reason/Issue/Resume lines
   ```
   - **Held** (PR matches an `allowedVersions` exclusion) → **skip it**, don't
     re-investigate. The WHY is right there (e.g. EMQX, emqx/emqx#17600).
   - **Newly blocked** (you discover this release is broken and there's no
     same-package fix yet) → **[ADD a hold](#holds-protocol)** and move on. Don't
     leave the PR to churn.

3. **Read the release notes.** Chart `UPGRADING` / GitHub release:
   ```bash
   gh release view <tag> --repo <org/repo> --json body --jq .body
   ```
   The component playbook lists the exact notes to read (`readReleaseNotes[]`).

4. **Grep our usage + make the supporting edits.** The playbook's `grepOurUsage`
   and `knownBreakingPatterns` tell you the feature → file → required edit. Make
   the `helmrelease.yaml` / `helm-values.yaml` / CR edits in the same branch as
   the bump. Known landmines (see the playbook for the full set):
   - **app-template v5** flips `automountServiceAccountToken:false` — any
     app-template HR with a `serviceAccount:` (multus, dragonfly operator, gatus
     sidecars) must set it back to `true`.
   - **rook v1.20** needs the third `ceph-csi-drivers` chart wired `dependsOn`
     between operator and cluster, and `cephVersion` **pinned** (chart default
     jumps Ceph a major — one-way).
   - **traefik v40+** moved `service` values under `service.spec`.
   - **kube-prometheus-stack** CRDs live in the separate `prometheus-operator-crds`
     chart — bump it **first**.

5. **Branch, commit, push as the bot, open the PR.**
   ```bash
   git switch -c shepherd/<pkg>-<version>
   git add kubernetes/...                 # kubernetes/** ONLY
   git commit -m "feat(<pkg>): upgrade to <version>"
   git push -u origin HEAD
   gh pr create --fill
   ```

6. **Wait for the gate.** `gh pr checks <N> --watch` → require
   **`Flux Local - Success`**. Read the sticky rendered-diff comment; it's the
   pre-merge sanity check.

7. **Merge — one at a time.**
   ```bash
   gh pr merge <N> --squash --delete-branch
   ```
   **Must-move-together pairs merge together** (one PR / branch, never split):

   | Component | Move as a unit |
   |---|---|
   | `rook-ceph` | operator → `ceph-csi-drivers` → cluster (`dependsOn` order) |
   | `emqx` | operator **+** broker — the operator's blue-green *is* the fault; never bump the broker major without the operator |
   | `cnpg` | operator first, then the `Cluster` CRs it manages |
   | `device-plugins` | each plugin + its `dependsOn` NFD (NodeFeatureRule PCI IDs) |

8. **Reconcile = wait for Flux.** Do **not** `--with-source` (Reader can't write).
   Let the Receiver / 30-min poll pick up `main`. Watch read-only:
   ```bash
   flux get kustomization <ks> -n <ns>          # Ready=True + revision == your commit
   ```

9. **Run the health gate.** [`upgrade-health-gate.md`](./upgrade-health-gate.md)
   **plus the component's own `healthChecks`** from the playbook (CNPG cluster
   health, `ceph -s`, EMQX broker up, cilium connectivity, HA Zigbee availability,
   etc.).
   ```bash
   scripts/checkHealth.sh                        # non-Ready Flux ks/hr cluster-wide
   ```
   - **GREEN** → next PR.
   - **REGRESSION** → **roll back** via the component's rollback section
     (git revert / re-pin the OCIRepository `ref.tag` → push as the bot → Flux
     applies) **and page**. If the documented rollback won't converge (one-way
     major, wedged HR, reseed needed) it's **break-glass** — page with the
     diagnosis, don't improvise a cluster write.

> **Blast-radius discipline:** never have two stateful upgrades in flight. Verify
> one *green* before merging the next. A bad CNI/DNS push can sever the very
> networking Flux needs to apply your revert — which is exactly why these are
> Tier-4 and exactly why you go one at a time.

---

## Mode 2 — summoned remediation

Invoked when an upgrade *already failed*: an alert fired, a `Kustomization` is
stuck `not Ready`, or a post-merge regression got spotted (chat session,
page-reply, or a `RemoteTrigger` hook). This is the **Rollback** section of
[`renovate-upgrade-batches.md`](./renovate-upgrade-batches.md#rollback), automated
and on-call.

1. **Diagnose** (read-only, Reader SA):
   ```bash
   flux get kustomization -A | grep -v 'True'
   flux get helmrelease -A   | grep -v 'True'
   kubectl describe kustomization <ks> -n <ns>           # what's wedged + why
   kubectl -n <ns> get pods; kubectl -n <ns> logs <pod> --tail=100
   ```
   Cross-check the component playbook's `healthChecks[].regression` for the signal.
2. **Attempt the documented rollback** from the component's playbook section —
   git revert the bump / re-pin the prior OCIRepository tag → push as the bot →
   let Flux apply (no `--with-source`). Re-run the health gate to confirm
   convergence.
3. **If it doesn't converge → page (break-glass).** Reverts that hit immutable
   fields, crash-looping source/kustomize-controllers, stuck finalizers, one-way
   majors (Ceph daemon, PG major, cilium eBPF map, authentik forward-only
   migration), or a reseed all need a cluster write the Reader is denied. **Do not
   improvise it.** Page with: what regressed, the diagnosis, the rollback you
   tried, and the link to the component playbook + the relevant runbook
   ([volsync-unlock](./volsync-unlock.md), [talos-version-upgrade](./talos-version-upgrade.md),
   [flux-pvc-prune-safety](../rules/flux-pvc-prune-safety.md)).

---

## Mode 1 — scheduled health gate

The phase-4a watch-and-page loop. Full procedure:
**[`upgrade-health-gate.md`](./upgrade-health-gate.md).** In brief: cron / `/loop`
trigger runs the post-reconcile health checks (Flux Kustomization status, CNPG /
Rook-Ceph / EMQX health, HA Zigbee availability) on the Reader SA and **pages on
regression**. **Rollback stays human in 4a** — the gate's job is to guarantee a
bad merge never goes unnoticed, not yet to act on it. (Automated rollback is the
phase-4b endgame, in-cluster CronJob.)

---

## Holds protocol

A hold is how the shepherd records "this release is broken and I can't fix it
right now" so the PR stops churning and the WHY lives next to the enforcement —
not in a human's head. Full spec:
[README → Holds registry](../../docs/renovate/README.md#holds-registry--reasoned-release-blacklist).
Each hold is a `packageRule` in [`.renovate/holds.json5`](../../.renovate/holds.json5)
that **enforces** via `allowedVersions` and **documents** via a structured
`description` (`HELD` / `Reason:` / `Issue:` / `Resume:` / `Recorded:`).

**ADD a hold** (when you hit an un-fixable-right-now blocker):

1. Append a `packageRule` per the in-file convention; set the **tightest**
   `allowedVersions` that excludes the bad release.
   - Fix is a later version of the *same* package → auto-resume range:
     `'<2.3.2 || >=3.0.0'` (skips 2.3.2–2.x, re-opens at 3.0.0).
   - Resume condition is something else (e.g. "after a *different* component
     upgrades") → plain upper bound (`'<6.2.1'`), lifted by hand.
2. Validate + commit (this is a `.renovate/` config write, not a `kubernetes/**`
   change):
   ```bash
   npx --yes --package renovate renovate-config-validator .renovate/holds.json5
   git commit -m "renovate(holds): hold <pkg> <range> — <reason>"
   ```

**LIFT a hold:** delete the rule (or widen `allowedVersions`) in a commit
referencing the upstream fix; Renovate re-proposes. (Example live today: EMQX —
operator auto-resumes at `>=3.0.0`; broker is a **manual** lift, because its
resume condition is the *operator* version, not the broker's.)

---

## Component playbooks & rollback

Merge lowest-risk first. Each row links its full section in
[`tier4-component-playbooks.md`](tier4-component-playbooks.md)
(release notes, grep targets, breaking patterns, health checks, rollback steps).
**Rollback-risk** is the one-word "what bites if you have to undo it":

| Order | Component | ns | Rollback risk | One-line caveat |
|---|---|---|---|---|
| 1 | [`coredns`](tier4-component-playbooks.md#coredns) | kube-system | **clean** | fully stateless; HR auto-rolls a failed upgrade. Risk is a DNS-resolution gap during churn, not data. |
| 2 | [`traefik`](tier4-component-playbooks.md#traefik) | network | **clean** | stateless; revert the `lbipam.cilium.io/ips` annotation alongside the chart. Do `traefik-internal` before `-external`. |
| 3 | [`multus`](tier4-component-playbooks.md#multus) | network | **clean / node-wide blast** | stateless DaemonSet, but `multusConfigFile:auto` makes it the node's primary CNI → a break stops **all** new pods on that node. If breakage came from the shared **app-template** chart, revert *that* OCIRepository, not the multus tag. |
| 4 | [`device-plugins`](tier4-component-playbooks.md#device-plugins) | kube-system | **clean / Plex-unschedulable** | stateless; HRs auto-roll a *failed* upgrade. intel failure → Plex `Unschedulable` (HW transcode dies). `generic-device-plugin` is `:latest` — git never recorded a good digest, so there's nothing to revert to. GPU off the PCI bus = host reboot (break-glass). |
| 5 | [`flux`](tier4-component-playbooks.md#flux) | flux-system | **clean / CRD-wedge** | stateless (state is CRs in etcd). A CRD storage-version downgrade can wedge the git-only revert; a crash-looping source/kustomize-controller can't apply its own revert → break-glass. |
| 6 | [`dragonfly-operator`](tier4-component-playbooks.md#dragonfly-operator) | database | **in-memory flush** | control-plane (operator/CRD/rbac) revert is clean **if** the old operator doesn't re-roll the STS. Any data-plane restart flushes the entire keyspace (replicas:1, no PVC) → drops immich BullMQ + paperless Celery in-flight jobs (re-enqueue on restart). HAND-MAINTAINED ClusterRole in `app/rbac.yaml`. |
| 7 | [`cilium`](tier4-component-playbooks.md#cilium) | kube-system | **one-way eBPF / break-glass** | no data, but eBPF map-layout migration is one-directional (old agent crash-loops; recovery = a brief `cleanBpfState=true` value edit = datapath wipe). CRD storage-version (`CiliumLoadBalancerIPPool` v2alpha1→v2) can strand LB-IP announcement. A bad CNI push severs networking Flux needs to self-heal → out-of-band. |
| 8 | [`authentik`](tier4-component-playbooks.md#authentik) | network | **migrations / PITR** | **patch within same `YYYY.M` = clean.** A `YYYY.M` **major** runs forward-only DB migrations with no down-migrations → old image crashes on the migrated schema; recovery = CNPG PITR, which is **all-tenant** (one barmanObjectStore covers authentik+grafana+…) and loses every write since the upgrade. **Bias to forward-fix.** |
| 9 | [`emqx`](tier4-component-playbooks.md#emqx) | database | **operator-pair / held** | **HELD at broker 6.2.0 / operator <2.3.2** until operator 3.0.0 GA (emqx/emqx#17600 blue-green bug). In-scope revert is data-safe (blue pod never migrates Mnesia). A *future* broker-major revert post-3.0.0 is a reseed (retained msgs + dashboard users; init superuser re-seeds). Operator + broker move as a unit. |
| 10 | [`cnpg`](tier4-component-playbooks.md#cnpg) | database | **reseed** | operator-chart revert is low-risk (data plane untouched), but CNPG doesn't officially support operator **downgrade**. PG **major** = one-way (`pg_upgrade`); reverting `imageName` against migrated PGDATA only crash-loops → restore from S3 (~600 GB egress). **`postgres16-pgvecto` is single-instance** — `delete pvc` = total loss (no replica source); the "delete pvc to reseed" trick is **only** for the 3-instance `postgres16` replicas. Bias to forward-fix. |
| 11 | [`rook-ceph`](tier4-component-playbooks.md#rook-ceph) | rook-ceph | **one-way major (storage)** | **always last.** Chart-only bump is revertible (HRs carry `strategy:rollback`); any bump that moved a Ceph daemon **major** is effectively one-way — old binaries refuse migrated metadata. Operator → `ceph-csi-drivers` → cluster as a unit. **Pin `cephVersion`** so the chart bump never silently carries a Ceph major. Break-glass OSD/daemon surgery can destroy a replica set — page instead. |
| — | [`talos-kubernetes`](./talos-version-upgrade.md) | *(below Flux)* | **break-glass (Omni admin)** | **NOT a Flux/Renovate flow** — applied by `task omni:sync` (omnictl, Omni **admin** identity), so a git revert does nothing and the Reader SA can't roll it back. Talos patch/minor downgrades in-place (preserves `/var`); **k8s minor downgrade is unsupported** → forward-fix only. Entire rollback escalates to a supervised `OMNICONFIG` session. Follow [talos-version-upgrade.md](./talos-version-upgrade.md) + [talos-omni-gotchas](../reference/talos-omni-gotchas.md). |

### Cross-references

- Batch process & per-component patterns: [`renovate-upgrade-batches.md`](./renovate-upgrade-batches.md)
- Read-only cluster access: [`omni-service-account.md`](./omni-service-account.md)
- Bot identity / threat model: [`tier4-bot-setup.md`](../../docs/renovate/tier4-bot-setup.md)
- Scheduled gate: [`upgrade-health-gate.md`](./upgrade-health-gate.md)
- PVC prune safety (renaming/moving Kustomizations): [`flux-pvc-prune-safety.md`](../rules/flux-pvc-prune-safety.md)
- VolSync stale-lock fallout after operator rolls: [`volsync-unlock.md`](./volsync-unlock.md)
