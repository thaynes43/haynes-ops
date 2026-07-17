# Tier-4 upgrade health gate

The component-agnostic, post-reconcile health verdict that runs **every cycle** — both as the scheduled gate (phase 4a in [`docs/renovate/README.md`](../../docs/renovate/README.md)) and as the upgrade-shepherd's per-merge verify step. It does not care which Renovate PR landed; it answers one question after Flux has pulled new state: **is the cluster still healthy?**

Verdict is one of:

| Verdict | Meaning | Action |
|---|---|---|
| **healthy** | All five checks green vs. the prior cycle's snapshot. | Record snapshot, done. |
| **benign-warn** | A check is non-green but matches a known-noise allowlist (transient pull, chronic spa, archived Ceph crash, ytdl-sub job). | Note it, re-poll the transients after one interval, do **not** page. |
| **regression** | A check is non-green and **new** (broke across the reconcile window, or is tied to the changed component). | **Page via Pushover.** In phase 4a, rollback stays human (see [Rollback](#rollback)). |

**Where it runs / credentials.** Read-and-page only — the gate **never mutates the cluster** (no `flux reconcile --with-source`, no `kubectl apply/exec/delete/annotate/scale`, no suspend/resume). It relies on Flux's own GitRepository **30-min poll** to pull new state (Flux is poll-only here — a `Receiver` exists but no GitHub webhook is wired, so pushes are not push-triggered). Two read paths, used in this priority:

- **Omni Reader service-account kubeconfig** ([`omni-service-account.md`](omni-service-account.md)) — read-only `kubectl` + `flux get`. The primary path for Flux reconcile state (since 2026-07-17 `gotk_resource_info` in Prometheus is a secondary readiness signal — see below). Denies `pods/exec`.
- **Grafana MCP** — Prometheus (datasource uid `prometheus`) + Loki (uid `loki`). The no-cluster-creds fallback for the pod sweep, ESO, Alertmanager, and Ceph health when the Omni OIDC token is expired ([`kubectl-omni-oidc-expiry`](omni-service-account.md)).

The gate deliberately does **not** read Home Assistant. HA/Zigbee/lock/device availability is owned by the **hass-sandbox** AppDaemon health-check suite (`zigbee_health_checker`, device/battery checkers) via the Alertmanager bridge — device health is not a deploy signal and was removed 2026-07 after an HA integration re-auth flipped every lock `unavailable` and paged ~5 unrelated criticals.

**Pages on regression** via the existing Pushover path (root Alertmanager route is `null`, only `severity=critical` pages — see the AM config under `kube-prometheus-stack`). A noisy phone channel is itself a precondition failure: alert-noise cleanup is a 4a prerequisite, since this gate is the de-facto safety net for unsupervised overnight Tier-2 auto-merges.

Component-specific verification (per-package breaking patterns, the exact entities/queries each upgrade touches) lives in the sibling **`tier4-component-playbooks.md`**. This file is only the cross-cutting layer that runs regardless of component.

---

## Checks

Run cheapest-to-most-expensive, each diffed against the prior cycle's snapshot. "Page only on **new**" is the rule throughout — pre-existing noise is curated into the exclude/allowlists below, not paged.

> **2026-07-06 additions to the deployed `gate.sh`** (this doc's checks are the design;
> the ConfigMap script is authoritative): (a) the shared signature/collect logic is now
> **single-sourced** from the `upgrade-coordination-lib` ConfigMap (missing lib ⇒ the
> gate pages `gate-blind coordination-lib`, never runs half-configured); (b) coordination
> pages append the **shepherd's recorded verdict** (`… | shepherd: BREAK-GLASS: <why>`)
> read from the state entry's `note` field; (c) **Check 1b** reads the
> `upgrade-orphan-report` CM the scheduled shepherd writes — pages `renovate/orphans`
> (warning, ≤1/week) for Renovate PRs aging >7d unowned, and `shepherd/ramp-mismatch`
> (critical, ≤1/day) when the scheduled run refused to vet on RAMP/prompt drift. Both
> page paths + their dedupe were proven able to FIRE on 2026-07-06.

### 1. Flux GitOps reconcile state

Every Kustomization + HelmRelease + Source `Ready=True`, **and** the `flux-system` GitRepository revision has advanced to the merged commit **on its own** (the gate must not force-reconcile).

| | |
|---|---|
| **Method** | `flux` / `kubectl` — **Reader SA only.** No Prometheus fallback (see note). |
| **Query** | `scripts/checkHealth.sh` (its `print_non_ready` helper already filters `flux get helmreleases/kustomizations/sources all -A` to non-Ready rows). Standalone: `flux get kustomizations -A \| grep -ivE 'True\|NAME'` ; `flux get helmreleases -A \| grep -ivE 'True\|NAME'` ; `flux get sources git -A`. Then confirm the `flux-system` GitRepository **REVISION** == the merged commit SHA, and dependent kustomizations reconciled at that revision. |
| **Healthy** | Every ks / HR / Source row `Ready=True`; GitRepository revision == merged commit. |
| **Benign-warn** | Immediately post-merge a row may transiently show `Reconciliation in progress`, `running health checks`, or HR `upgrade in progress`. Operator charts that roll their own pods (CNPG instances, EMQX) briefly show the dependent app reconciling. **Re-poll after one reconcile interval** before judging — the gate waits for Flux's own poll, it does **not** run `flux reconcile --with-source` (a write the Reader SA is denied). |
| **Regression** | Any `Ready=False` persisting past one interval: HR `install/upgrade retries exhausted` / `values don't meet the specifications of the schema` / `Helm upgrade failed`; ks `dependency ... is not ready` / `build failed`; or GitRepository **stuck on the OLD revision** (Renovate advanced `main` but Flux never pulled). |

> **Prometheus fallback (since 2026-07-17):** per-resource Flux readiness IS now in Prometheus as `gotk_resource_info{ready,exported_namespace,name,customresource_kind}` (kube-state-metrics customResourceState — NOT the old `gotk_reconcile_condition`, which Flux ≥2.1 removed and which still returns no data). If the Reader SA token is dead, query `gotk_resource_info{ready=~"False|Unknown", suspended!="true"}` via Grafana MCP instead of reporting blind; prefer the kubectl path when available (it carries condition messages the metric lacks).

### 2. Cluster-wide unhealthy-pod sweep

NEW unhealthy pods are the signal; pre-existing noise is diffed out against a curated stale-exclude list and the prior snapshot.

| | |
|---|---|
| **Method** | `kubectl` (Reader SA) primary; `promql` (Grafana MCP) fallback. |
| **Query** | **Reader SA:** the `checkHealth.sh` sweep `kubectl get pods -A \| awk 'NR==1 \|\| ($1 != "flux-system" && $4 !~ /^(Running\|Completed)$/)'` then `grep -vE "<curated-stale-pods>"`. **Grafana MCP:** `kube_pod_status_phase{phase=~"Pending\|Failed\|Unknown"} == 1` and `kube_pod_container_status_waiting_reason{reason=~"CrashLoopBackOff\|ImagePullBackOff\|ErrImagePull\|CreateContainerError\|CreateContainerConfigError"} == 1`. |
| **Healthy** | No pods outside Running/Completed beyond the curated excludes; KSM shows zero Pending/Failed/Unknown and zero CrashLoop/ImagePull waiting reasons. |
| **Benign-warn** | Transient `ImagePullBackOff`/`ErrImagePull`/`ContainerCreating`/`Init` under ~90s — Spegel `not found` / `connection reset by peer` self-heal on backoff ([`renovate-upgrade-batches.md`](renovate-upgrade-batches.md)); **re-check after 90s**. Stale `Error`/`Completed` pods with OLD age are GC leftovers. **Perpetual exclude-list noise:** `ytdl-sub` peloton/youtube backfill Jobs (routinely fail/loop >12h — AM-suppressed), in-flight VolSync mover Jobs, any node briefly draining during a Talos roll. |
| **Regression** | Any `CrashLoopBackOff`, or non-Running persisting >90s / >2 restart cycles, on a **NEW** pod or one tied to the changed component — **especially** stateful/control-plane: CNPG Postgres, rook-ceph mon/osd/mgr, any operator, cilium, coredns. Persistent image-pull failures on **one** node point at that node, not the upgrade. |

> Curating the exclude list is **mandatory** — the gate compares against the prior cycle's snapshot so only newly-broken pods page ([`renovate-upgrade-batches.md`](renovate-upgrade-batches.md) Verification: "curate the exclude list to known pre-existing noise").

### 3. External Secrets (ESO + onepassword-connect)

Cascade risk: if 1Password Connect or the ESO controller is down, **every** secret in the cluster stops refreshing.

| | |
|---|---|
| **Method** | `promql` (Grafana MCP) primary; `kubectl` (Reader SA) equivalent. |
| **Query** | **Grafana MCP:** `externalsecret_status_condition{condition="Ready",status="False"} > 0` and `sum(increase(externalsecret_sync_calls_total{status="error"}[15m]))`. **Reader SA:** `kubectl get externalsecrets -A` (STATUS != `SecretSynced` / READY != True) and `kubectl get pods -n external-secrets` (controller, webhook, cert-controller, onepassword-connect all Running). |
| **Healthy** | `externalsecret_status_condition{condition="Ready",status="False"} == 0` (all SecretSynced); no SecretSyncError; external-secrets + onepassword-connect pods Running. |
| **Benign-warn** | A **brand-new** ExternalSecret added by the same PR can show `SecretSyncError` for up to one `refreshInterval` while Connect catches up — re-check after the ES refreshInterval before paging. |
| **Regression** | Any **pre-existing** ExternalSecret flips to `SecretSyncError` / `Ready=False`, or the external-secrets controller / webhook / onepassword-connect pods go down. This is a fan-out failure — every dependent app's secret stops refreshing. Controllers live in ns `external-secrets`; ExternalSecret objects are cluster-wide. |

### 4. Alertmanager firing criticals

The existing runtime safety net: root route is `null`, only `severity=critical` pages Pushover.

| | |
|---|---|
| **Method** | `promql` (Grafana MCP) primary; Alertmanager API alternative. |
| **Query** | **Grafana MCP:** `ALERTS{alertstate="firing",severity="critical"}` (empty == none). For context: `sum(ALERTS{alertstate="firing"})`. **AM API:** `GET http://kube-prometheus-stack-alertmanager.observability.svc:9093/api/v2/alerts?filter=severity=critical&active=true`. |
| **Healthy** | Zero series for `ALERTS{alertstate="firing",severity="critical"}`. |
| **Benign-warn** | The always-firing `Watchdog` dead-man's-switch (by design — routed to the heartbeat receiver / healthchecks.io, not critical), `InfoInhibitor`, and AM-suppressed `KubeJobNotCompleted`/`KubeJobFailed` for `ytdl-sub-.*` (best-effort media backfills). These appear in `sum(ALERTS{...firing})` but **never** as `severity=critical`. |
| **Regression** | **ANY** new `severity=critical` series firing during/after the reconcile window — `KubePodCrashLooping`, `KubeDeploymentReplicasMismatch`, `CephClusterErrorState`, `KubeStatefulSetReplicasMismatch`, `PrometheusOperator*`, or a CNPG/EMQX critical tied to the changed component. |

### 5. Ceph cluster health

`HEALTH_OK`, no OSD/PG/mon/MDS regression. Ceph is Tier-4 storage — majors **cannot** be downgraded (see [Rollback](#rollback)).

| | |
|---|---|
| **Method** | `promql` (Grafana MCP) primary; `kubectl` CR read (Reader SA). **Not** toolbox exec. |
| **Query** | **Grafana MCP:** `ceph_health_status` (`0`=OK, `1`=WARN, `2`=ERR; exported by rook-ceph-mgr). **Reader SA:** `kubectl -n rook-ceph get cephcluster -o jsonpath='{.items[*].status.ceph.health}'` (checkHealth.sh already does this). |
| **Healthy** | `ceph_health_status == 0` **AND** cephcluster `.status.ceph.health == HEALTH_OK`. |
| **Benign-warn** | HEALTH_WARN allowlist — do **not** page: `N daemons have recently crashed` from stale/archived night crashes (the recurring ~02:30 ET mon.i tcmalloc / osd.2 perf-counter aborts, archived via `ceph crash archive-all` — break-glass); mon clock skew; maintenance flags (`noout`/`norebalance`) set during a roll; transient degraded% / `recovering` / `backfilling` as OSDs roll one-node-at-a-time during a rook upgrade (degraded spikes then recovers — `watchrook.sh` pattern). |
| **Regression** | `HEALTH_ERR` (`ceph_health_status == 2`) at **any** time, OR a **NEW** HEALTH_WARN naming OSD down/out, PG inactive/incomplete/stuck/degraded-not-recovering, mon out of quorum, MDS down, or pool near-full. **Ceph-version regressions are forward-only** — a git revert of the rook chart does **not** downgrade the daemons; page immediately, do not assume rollback recovers the data plane. |

> `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph -s` is the human/break-glass detail view. `exec` is a `pods/exec` CREATE — **denied on the Reader SA** — so the scheduled gate uses the promql + cephcluster-CR read paths, never the toolbox exec.

### Not a gate check: Home Assistant device availability

HA/Zigbee/lock/device availability is **not** the gate's job. It is owned by the **hass-sandbox** AppDaemon health-check suite (`zigbee_health_checker`, the device/battery checkers) which pages via its own Alertmanager bridge. The gate deliberately does **not** poll HA — device state is not a deploy/upgrade signal, and reading it here produced context-poor false pages (an HA integration re-auth flipped every lock `unavailable` and fired ~5 unrelated criticals, removed 2026-07). If a Z2M/HA restart race or a lock outage needs attention, the hass-sandbox suite catches it, not this gate.

---

## Running it

`scripts/checkHealth.sh` already does the heavy lifting for three of the five checks on the Reader SA:

- **Check 1 (Flux)** — `print_non_ready` over `flux get helmreleases / kustomizations / sources all -A` prints only non-Ready rows (`(all ready)` when clean).
- **Check 2 (pods)** — `kubectl get pods -A` swept for non-Running/Completed, with `flux-system` already excluded; also the per-node `flux-system` pod table.
- **Check 5 (Ceph)** — reads `cephcluster .status.ceph.health` and flags anything != `HEALTH_OK`; also dumps recent rook-ceph + all-namespace Warning events.
- Bonus: VolSync `ReplicationSources` `latestMoverStatus.result` (non-Succeeded surfaced) — useful context, not a gate check.

What the **agent layers on top** of the script:

1. **The other two checks** the script doesn't cover: ESO (check 3, Grafana MCP / `kubectl get externalsecrets`) and Alertmanager criticals (check 4, Grafana MCP).
2. **Judgment: benign-warn vs. regression.** The script prints raw non-Ready rows; the agent diffs them against the **prior cycle's snapshot** and the allowlists above, so only **newly-broken** state pages. Re-poll transients (post-merge reconcile-in-progress, <90s image pulls, new-ES sync lag) after one interval before deciding.
3. **The Grafana-MCP fallback path** when the Omni OIDC token is expired — checks 2–5 have promql equivalents, and since 2026-07-17 check 1 (Flux) does too: `gotk_resource_info{ready=~"False|Unknown", suspended!="true"}` (readiness only, no condition messages). Only if BOTH the Reader token and Prometheus are dead is the Flux dimension **blind → escalate to human**, never reported green.
4. **The page.** On a confirmed regression, page via Pushover (existing `severity=critical` path). In phase 4a, **rollback stays human** — see below.

Component-specific verification (which entities/queries each package upgrade touches, per-package breaking patterns) is **not** here — it lives in **`tier4-component-playbooks.md`**.

---

## Rollback

The scheduled gate is **read+page only** — it does **not** roll back in phase 4a. Rollback is human (phase 4a) or driven by the haynes-ops-bot GitHub App (phase 4b). Procedure for chart/image/HelmRelease/tag changes (rollback is **git-only**; a `GITHUB_TOKEN`-pushed revert fires no downstream workflow, so use the App identity):

1. **Identify** the offending merge commit: `gh pr view <N>` / `git log --oneline -- <changed path>`. **Check [`.renovate/holds.json5`](../../.renovate/holds.json5) first** — the bad version may already be a known hold.
2. **Revert via git:** `git revert <sha>` on a branch, OR re-pin the prior version in the HelmRelease / OCIRepository / image `tag:`. If the upgrade is permanently bad, **also add a hold** to `.renovate/holds.json5` (tightest `allowedVersions`) so Renovate stops re-proposing it.
3. **Push as a real identity** — the **haynes-ops-bot** GitHub App, NOT `GITHUB_TOKEN` (only the App fires the `flux-local` check). `export GH_TOKEN="$(scripts/github-app-token.sh)"`. Open + merge the PR; `flux-local` gates it. See [`docs/renovate/tier4-bot-setup.md`](../../docs/renovate/tier4-bot-setup.md).
4. **Let Flux reconcile on its poll interval** — do **NOT** `flux reconcile --with-source` (annotates a resource = a write the Reader SA is denied). Poll `flux get` until the GitRepository revision advances to the revert commit and the ks/HR returns `Ready=True` at that revision.
5. **Re-run the full gate** and confirm green across all five checks.

### Rollback caveats (when git revert is NOT enough)

| Caveat | Why it bites | Who acts |
|---|---|---|
| **Reader SA is read-only** | Cannot push/merge/reconcile, and **no** cluster write — `kubectl exec` (`ceph -s`, `ceph crash archive-all`), delete/scale/annotate, suspend/resume, finalizer removal are all **break-glass**. | Privileged human / 4b bot |
| **Ceph majors are forward-only** | A git revert of the rook chart does **not** downgrade the Ceph daemons. `cephVersion` is currently **not** explicitly pinned in `cluster/helmrelease.yaml` — flagged risk; pin it up front to keep the rollback option. | Human — page, don't assume revert recovers |
| **CNPG / stateful operators** | May need pod/PVC delete-and-reseed to recover replication after a bad upgrade — a privileged write, not a git revert. | Break-glass; gate pages, human/4b acts |
| **Shared/centralized versions** | The app-template OCIRepository pinned once in `kubernetes/shared/components/common` — a revert affects **all** consumers at once. Weigh a forward-fix instead. | Human judgment |
| **Stuck ks / HR** | May need suspend/resume or finalizer intervention to unwedge. | Break-glass |
| **Dead Reader token** | Flux dimension goes blind (no Grafana fallback). Grafana-MCP signals still work. | Escalate the Flux dimension to a human |

---

## See also

- [`renovate-upgrade-batches.md`](renovate-upgrade-batches.md) — per-component Verification + Rollback patterns this gate complements (curate-the-excludes, transient pull self-heal, operator-managed restarts, rook operator→csi→cluster order + cephVersion pin).
- [`omni-service-account.md`](omni-service-account.md) — Reader SA constraints (read-only, no exec/reconcile) that bound what the gate can do.
- [`docs/renovate/README.md`](../../docs/renovate/README.md) Tier 4 — phase 4a (watch+page) vs. 4b (in-cluster CronJob + automated rollback via the App) split, and the shepherd's three invocation modes.
- [`docs/renovate/tier4-bot-setup.md`](../../docs/renovate/tier4-bot-setup.md) — haynes-ops-bot GitHub App identity + token minting.
- [`.renovate/holds.json5`](../../.renovate/holds.json5) — read **before** working any PR so the gate never re-investigates a known-bad upgrade (EMQX operator/broker holds).
- `tier4-component-playbooks.md` — the per-component checks this cross-cutting gate sits alongside.
- [`.agents/rules/flux-pvc-prune-safety.md`](../rules/flux-pvc-prune-safety.md) — PVC-prune hazard when reverting/moving Kustomizations.
