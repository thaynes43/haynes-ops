# Nightly upgrade lane (dry-run, OP512 step 1)

The nightly lane will merge low-risk Renovate updates one at a time at night, after the
backups, with a health check after each one. Tom asked for upgrades to be 100% agent
driven (OP512, comment 6168). The design is OP512 comment 6193
(<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/512>).

**Status: dry-run only.** This step classifies updates and records what the lane would
do. It merges nothing, comments on nothing and labels nothing.

## What runs

CronJob `upgrade-shepherd-upgrade-night` in namespace `upgrade-agent`. Its controller is
`upgrade-night` in `kubernetes/main/apps/upgrade-agent/shepherd/app/helmrelease.yaml`.

- Schedule `*/15 1-5 * * *`, America/New_York (01:00 to 05:45).
- It uses the shepherd's image, ServiceAccount and egress policy. No LLM runs, and no
  Anthropic credential is mounted.
- The init container mints a **read-only** GitHub token (contents, pull requests and
  checks, all read).
- `run-night.mjs` refuses any `UPGRADE_NIGHT_MODE` except `dryrun` and exits 3 before
  reading anything.
- When a tick fails, see [When a tick fails](#when-a-tick-fails) below.

The code is in the same `resources/` directory as the shepherd and is mounted from the
`upgrade-shepherd-scripts` ConfigMap:

| File | What it does |
|---|---|
| `lane-classify.mjs` | Puts one PR in a lane from its facts. It is a pure function, with a read-only CLI around it. |
| `upgrade-rules.json` | The registry: path classes, the check map, our own images, per-package rules and CNPG clients. |
| `run-night.mjs` | One tick: classify the PRs, check the gates, choose one PR and write the state. |

## Lanes

The strictest finding wins. A PR that touches several paths takes the strictest lane any
of those paths asks for.

| Lane | When |
|---|---|
| `EXCLUDED` | dev-env v2 paths, the merger's own paths (`upgrade-agent/**`, `scripts/upgrade-*`), workflow files, Renovate config, PRs not opened by Renovate. |
| `LANE-B-OWNER` | Majors, prereleases, holds in `.renovate/holds.json5`, v1-restarting paths, one-way changes (cilium minor, authentik YYYY.M), Dragonfly 2.1, a current-head vet `HOLD`. |
| `LANE-B-AGENT` | A commit not authored by Renovate, a draft, a path with no PR validation, a rule only an agent can satisfy (Kometa, ComfyUI, LazyLibrarian, rook-ceph, HA, Zigbee2MQTT, the runner VERSION, the ComfyUI IMAGE_TAG), a rule not evaluated in step 1 (qBittorrent's tracker list, Plex's Kometa-idle check), a trusted vet verdict that declines the PR. |
| `BLOCKED` | An expected check is failing, or it is missing, skipped or still running. |
| `LANE-A` | Everything else. Every expected check passed on the head commit, and every commit is Renovate's. |

The **check map** is in `upgrade-rules.json`, under `check_map`. It lists every
workflow's `on.pull_request.paths` and the checks each workflow must report as SUCCESS
on the head commit.

- A `kubernetes/**` change needs `Flux Local - Test (main)` and both Diff legs to pass.
  A skipped leg is not a pass.
- A changed path outside `kubernetes/` that no workflow validates is never lane A.

The selftest fails when a workflow's filter and the check map drift apart.

**Own images.** Our own images are listed under `own_images`.

- For upgrade-shepherd, upgrade-agent, audio-authoring, actions-runner and
  blender-authoring, a source merge reaches the cluster later. The cluster changes when
  Renovate auto-merges the digest pin. These are `delayed-cluster-change`.
- The whisper pin follows a tag with no digest, so its source merge is an
  `immediate-cluster-change`. The live step must not merge this class until its verify
  step can observe the change. The pod only picks it up at its next start.
- ComfyUI, dev-env v1 and the sha-tag builds have no cluster effect.

## Gates the tick evaluates (lane-A candidates only)

Every gate **fails closed**. If a read errors or returns nothing parseable, that gate is a
blocker named `unreadable: <what>` and never a pass. This covers the ConfigMaps in
`upgrade-agent`, Kustomizations, HelmReleases, ReplicationSources, CNPG Backups and
ScheduledBackups, the target namespace's pods and CronJobs, CephClusters, the critical
alerts from Prometheus and each work order.

- **Checks:** if any expected check is older than 7 days, the tick asks for a Renovate
  retry first.
- **Target mapping:** each changed app directory must be applied by a Kustomization
  (matched by `spec.path`). Otherwise the blocker is `target-unmapped`.
- **Durable state:** a target counts as stateless only on positive evidence. It has state
  when any of these holds:
  - its Kustomization applied a ReplicationSource (matched by the
    `kustomize.toolkit.fluxcd.io/name` and `/namespace` labels, not by name);
  - that Kustomization substitutes `VOLSYNC_*`, includes a volsync component, or depends
    on `volsync`;
  - a pod or CronJob of the app in the target namespace mounts a PVC or an ephemeral claim;
  - the target is a CNPG client listed under `cnpg_clients`.

  A target with state and no ReplicationSource of its own gets the blocker
  `backup-unmatched`.
- **Backup freshness, per source:** the newest successful sync or backup must be at or
  after that source's most recent scheduled run, and no older than 24 hours at merge time.
  - VolSync: the source's most recent run is `status.nextSyncTime` if that time has
    already passed, else `nextSyncTime` minus the period of `spec.trigger.schedule`. The
    cluster evaluates `0 0 * * *` as 05:00Z.
  - CNPG: the most recent run is the ScheduledBackup's `status.lastScheduleTime`, checked
    against the newest Backup with `phase=completed`, timed by its `creationTimestamp`.
    `startedAt` and `stoppedAt` are not used: postgres16 and ytdrivarr stamp local time
    with `Z`, so a 05:00Z run reads as 00:00Z.
  - Weekly sources (`plex-aws`, `plexops-aws`) therefore pass only on their own night.
- **Baseline, scoped to the target.** These block:
  - a NotReady Kustomization that applies the target, sits in its `dependsOn` chain, or
    targets its namespace;
  - a NotReady HelmRelease in its namespace;
  - a critical alert firing in its namespace;
  - Ceph in `HEALTH_ERR` or unknown.

  Every other NotReady Flux object is listed under `summary.baseline.flux_warnings`.
- **Work orders:** none may be `claimed`.
- **Merge window:** 01:30 to 04:45 New York.
- **Change lock (`upgrade-change-lock`): not implemented until the live step.** Nothing
  takes this lock yet, so it is not a protection. The tick reports it as
  `change_lock: not-implemented`.

Of the PRs that pass every gate, the tick picks one. It prefers no cluster effect, then
delayed, then stateless, then stateful, then the lowest PR number. In this step it only
records the choice.

## Reading the state

```bash
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.table}'; echo
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.summary}' | jq .
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.records}' \
  | jq '.[] | {pr, lane, class, reason, blockers: .gates.blockers}'
kubectl -n upgrade-agent logs job/<latest upgrade-shepherd-upgrade-night job>
```

`summary.would_merge` is the PR the lane would have merged on that tick. `why_none` says
why there was no such PR. Each record follows schema `haynes.upgrade-lane/v1`. In this
step, `preapproval`, `schedule` and `last_attempt` are empty.

To run it by hand (read-only, writes nothing; gh must be logged in):

```bash
node kubernetes/main/apps/upgrade-agent/shepherd/app/resources/lane-classify.mjs     # the table
node kubernetes/main/apps/upgrade-agent/shepherd/app/resources/run-night.mjs         # tick, no --write-state
```

## Kill switch

- Set `suspend: true` on the `upgrade-night` controller in git and let Flux reconcile it.
  A `kubectl` suspend lasts only until the next reconcile.
- The runtime **pause** takes effect immediately and needs no git change:

  ```bash
  kubectl -n upgrade-agent patch cm upgrade-lane-state --type merge -p '{"data":{"pause":"<who> <why>"}}'
  ```

  While `data.pause` is non-empty, each tick logs that it is paused and does nothing else.
  It reads nothing from GitHub and writes nothing. To resume, remove the key. The
  server-side apply never removes a key that another field manager set.

## When a tick fails

The tick uses these exit codes:

| Exit | When |
|---|---|
| 0 | The tick finished or was paused. Also when it failed but recorded the failure: it writes `data.error` (and `summary.error`) to `upgrade-lane-state`. This includes a state over the 768 KiB budget, which is caught before the write; the small error record still lands. |
| 2 | The state write itself failed: the server-side apply was refused. The log carries `ERROR upgrade-night: STATE WRITE FAILED`. The ConfigMap cannot record its own write failure, so a Failed pod is the only signal. |
| 1 | The tick failed and its error record could not be written either. |

**How the gate and triage treat a Failed `upgrade-night` pod** (read from
`health-gate/app/resources/coordination-lib.sh`, which this PR does not change):

- `collect_regressions` takes Failed pods in every namespace from
  `kube_pod_status_phase`, once they have persisted for 10 minutes.
- `pod_is_stale_corpse` drops a Failed pod in three cases:
  - rule A0: its own Job later succeeded (`backoffLimit` is 0 here, so this never applies);
  - rule A: a newer Job of the same CronJob succeeded, so one failed tick followed by a
    good one 15 minutes later is filtered;
  - rule B: it is older than 24 hours.
- `pod_failure_is_the_alert` drops pods annotated
  `upgrade-agent.haynesnetwork.com/failure-is-the-alert`. These pods are not annotated:
  the contract for that annotation is that a critical alert already pages for the
  failure, and none does for this Job.

As a result, a write failure on the last tick of the night (05:45), or on every tick,
stays in the set. The gate then counts it as a regression and pages, and triage's path
attribution can call it an upgrade regression whenever a merge touched the app's path
in its 3-hour lookback (the library's own description of that behavior). That
is acceptable for a write failure, which needs a person.

Before the live step, a later change should decide how a Failed `upgrade-night` pod is
classified. One option is a covering alert plus the failure-is-the-alert annotation;
another is an explicit exclusion in `coordination-lib.sh`. That library is mounted in
dev-env-ops, so merge such a change only when no work order is claimed.

## Known limits

- State evidence reads pods and CronJobs only, because the read-only role cannot read
  PVCs, StatefulSets or Deployments. A stateful app that is scaled to zero and has no
  ReplicationSource could therefore look stateless. The live step must close this gap
  before it merges stateful targets.

## Tests

```bash
node scripts/upgrade-agent/lane-classify-selftest.mjs      # lanes, drift, own images, the 27 reviewed PRs
node scripts/upgrade-agent/lane-night-dryrun-selftest.mjs  # the dry-run guarantee
```

No CI workflow runs these yet. Rollout step 2 adds one.

## What later steps add (design, rollout order)

2. A workflow that runs the upgrade-agent selftests and this drift test, plus the
   labeler mapping.
3. The per-PR record as one bot comment, the comment-author check in the shepherd
   pre-filter, and the one-retry rule for stalled checks.
4. Coordination with triage and the gate: ownership of a regression signature, the
   morning digest and the orphan page.
5. The first live merge: one class, one merge a night, a stateless leaf. Then stateful
   targets and our own images. That step adds a live mode, a write-capable token, the
   verify-and-revert loop and the change lock.
6. Lane B: work orders, an ask phase and a scheduled phase.
