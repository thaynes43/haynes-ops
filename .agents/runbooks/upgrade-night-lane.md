# Nightly upgrade lane (dry-run, OP512 steps 1 and 2)

The nightly lane will merge low-risk Renovate updates one at a time at night, after the
backups, with a health check after each one. Tom asked for upgrades to be 100% agent
driven (OP512, comment 6168). The design is OP512 comment 6193
(<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/512>).

**Status: dry-run only.** The lane classifies updates and records what it would do. It
merges nothing, comments on nothing and labels nothing.

**Step 2 (OP512 rollout step 2) added**, with no change to anything that merges:

- CI for the lane's own tests: workflow `Upgrade Agent Selftests`
  (`.github/workflows/upgrade-agent-selftests.yml`), aggregate check
  `Upgrade Agent Selftests - Success`. See [Tests](#tests).
- The labeler reads the lane's verdict tokens. See
  [Lane verdicts in the labeler](#lane-verdicts-in-the-labeler).
- Two new fields in every record, `fingerprint` and `cluster_effect_wait`, plus a fix to
  version comparison. See [What step 2 records](#what-step-2-records).
- The comparison of the first dry-run night (2026-11-10) with the hand-run of the same
  night. See [Reading the 2026-11-10 comparison](#reading-the-2026-11-10-comparison).

**Story 550 added four gate rules**, still dry-run only. See
[Gate rules from story 550](#gate-rules-from-story-550-r5-r7-r8-r10)
(<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/550>):

- R5: a critical alert blocks only the target it belongs to.
- R7: checks count as stale once main has changed one of the PR's files after them.
- R8: a shared component maps to the Kustomizations that include it.
- R10: ceph-csi always goes to lane B (agent).

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
of those paths asks for. A change to a shared component also counts every directory that
includes it (R8). An app-template bump reaches `dev-env-system`, so it is `EXCLUDED`.

| Lane | When |
|---|---|
| `EXCLUDED` | dev-env v2 paths, the merger's own paths (`upgrade-agent/**`, `scripts/upgrade-*`), workflow files, Renovate config, PRs not opened by Renovate. |
| `LANE-B-OWNER` | Majors, prereleases, holds in `.renovate/holds.json5`, v1-restarting paths, one-way changes (cilium minor, authentik YYYY.M), Dragonfly 2.1, a current-head vet `HOLD`. |
| `LANE-B-AGENT` | A commit not authored by Renovate, a draft, a path with no PR validation, a rule only an agent can satisfy (Kometa, ComfyUI, LazyLibrarian, rook-ceph, ceph-csi, HA, Zigbee2MQTT, the runner VERSION, the ComfyUI IMAGE_TAG), a rule not evaluated in step 1 (qBittorrent's tracker list, Plex's Kometa-idle check), a trusted vet verdict that declines the PR. |
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
ScheduledBackups, the target namespace's pods, Jobs and CronJobs, CephClusters, the
critical alerts from Prometheus, main's commits to the PR's files and each work order.

- **Checks:** if any expected check is older than 7 days, the tick asks for a Renovate
  retry first. Checks are also stale when main has a newer commit touching one of the
  PR's files than the head's checks (R7). The blocker is `checks predate main: …; needs a
  Renovate rebase first`.
- **Target mapping:** each changed app directory must be applied by a Kustomization
  (matched by `spec.path`). Otherwise the blocker is `target-unmapped`. A changed shared
  component maps to the directories that include it (R8). A component missing from the
  map stays `target-unmapped`.
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
  - a critical alert that belongs to the target (R5). An alert in its namespace blocks
    when its subject belongs to the target or its `dependsOn` chain. It also blocks when
    the subject cannot be traced, or when the alert names no workload. For a cluster-wide
    target, any critical alert with no namespace label blocks too;
  - Ceph in `HEALTH_ERR` or unknown.

  Every other NotReady Flux object is listed under `summary.baseline.flux_warnings`. Every
  alert that did not block is listed under `records[].gates.alerts_not_blocking`.
- **Work orders:** none may be `claimed`.
- **Merge window:** 01:30 to 04:45 New York.
- **Change lock (`upgrade-change-lock`): not implemented until the live step.** Nothing
  takes this lock yet, so it is not a protection. The tick reports it as
  `change_lock: not-implemented`.

Of the PRs that pass every gate, the tick picks one. It prefers no cluster effect, then
delayed, then stateless, then stateful, then the lowest PR number. In this step it only
records the choice.

## Gate rules from story 550 (R5, R7, R8, R10)

The rules come from the 2026-11-10 comparison (see
[Reading the 2026-11-10 comparison](#reading-the-2026-11-10-comparison)). The registry
entries are `alert_scope`, `cluster_wide_targets`, `check_rules`, `shared_components` and
the `ceph-csi-*` package rules in `upgrade-rules.json`. The fixture
`scripts/upgrade-agent/fixtures/lane-gates-20261110.json` holds that night's alerts, the
objects they name and the PRs below.

### R5: critical alerts scoped to the target's workloads

On 2026-11-10 the old namespace rule held #4477 (plex-hw-canary) back all night. The
alert was recyclarr's `MediaAutomationJobFailed` in `media`. `PVEVMNotOnboot`, about a
Proxmox VM, would likewise have held #4710 (kube-prometheus-stack) in `observability`.

For each critical alert, the tick now does this:

1. **Namespace.** It takes the `namespace` label, or else `exported_namespace` (Flux
   alerts carry the object's namespace there). An alert outside the target's namespaces
   does not block, as before.
2. **Subject.** The first label present names the subject: `job_name`, `cronjob`,
   `deployment`, `statefulset`, `daemonset`, `pod`, `persistentvolumeclaim`, then `app`,
   `app_kubernetes_io_name` or `app_kubernetes_io_instance`. Flux alerts name the object
   instead, with `customresource_kind` and `name`.
   - The `pod` label can name the exporter that scraped the metric rather than the
     subject. A pod whose `app.kubernetes.io/name` is `kube-state-metrics` is therefore
     not taken as the subject.
3. **Owner.** The tick traces the subject to the Kustomization that applied it.
   - It looks at the object's own Flux labels first.
   - Otherwise it uses the HelmRelease that rendered the object
     (`helm.toolkit.fluxcd.io/name`, or else `app.kubernetes.io/instance`) and that
     HelmRelease's Flux labels.
   - A pod is traced up through its Job and CronJob, and a deployment through the pods its
     ReplicaSet owns. A `job_name` whose Job the history limit already removed is traced
     through the CronJob named before its `-<minutes>` suffix.
4. **Decision.**

| What the alert resolves to | Result |
|---|---|
| The target's own Kustomization, or one in its `dependsOn` chain | blocks: `… belongs to <ns>/<ks>, the target or its dependsOn chain` |
| Another Kustomization | does not block; listed in `gates.alerts_not_blocking.unrelated` |
| A subject the tick cannot trace (no such object, no owner, owners disagree, Jobs unreadable) | blocks: `… cannot be traced to a Kustomization (fail closed)` |
| No workload label at all, or a Flux source (GitRepository, OCIRepository, HelmRepository) | blocks the whole namespace: `… names no workload (namespace fallback)` |

**Decision: a critical alert with no namespace label.** Such an alert blocks every
cluster-wide target and no other target. Cluster-wide targets are listed in
`cluster_wide_targets`: Flux and its bootstrap, cilium, coredns, rook-ceph, the CSI
drivers (ceph-csi, openebs, `storage/volsync-system`), kyverno, multus and
`kubernetes/shared/components/common/**`.

- Why: a change that reaches every namespace cannot be shown to be unrelated to an alert
  the lane cannot place, so the gate fails closed.
- A leaf target lists such alerts under `gates.alerts_not_blocking.cluster` and is not
  blocked. Before story 550, these alerts blocked nothing.
- Seen with no namespace label: `DevEnvRescueFailed` (`session`), `CNPGBackupStale`
  (`job=<ns>/<cluster>`), `KyvernoEnforceBlockingAdmission`, `GpuMissing` and the SMART
  alerts (`node`), `SigoalumniServiceEndpoints*`, `KubeAPIErrorBudgetBurn`,
  `CephOSDDownHigh` and `PVEExporterNoData`.
- Consequence: while `DevEnvRescueFailed` keeps firing (two sessions on 2026-11-11), no
  cluster-wide target passes this gate. Clearing or fixing that alert is a separate job.
  Most cluster-wide targets are lane B anyway (rook-ceph, ceph-csi, cilium minors).

**Alerts that block every target in their namespace** (from the 30 days to 2026-11-11):

- `BlackboxProbeFailed` and `AlertmanagerClusterFailedToSendAlerts` in `observability`:
  they carry no workload label.
- `PersistentVolumeClaimStuckPending`: its `pod` label is kube-state-metrics itself.
- `KubePersistentVolumeFillingUp`, when no running pod mounts the claim.
- `CephDaemonCrash` and `etcdDatabaseQuotaLowSpace`: the operator-made or static pods
  they name have no Flux or Helm owner.

Alerts traced to their owner: `MediaAutomationJobFailed`, `KometaOverlaysRunTooLong`,
`CigarJournalCredentialMonitorStale`, `QbittorrentVpnDown`, `OOMKilled` (while the pod
exists), the PVE alerts (the exporter) and `NodeRebooted` (node-exporter, which is
kube-prometheus-stack). The list is kept in `alert_scope.namespace_fallback_seen`.

### R7: checks that predate a change on main

A `pull_request` check tests the PR merged onto main as main stood when the check was
triggered. A re-run reuses that merge commit.

**"Require branches to be up to date" is off for main.** This was established on
2026-11-11 from read-only evidence:

- PR 3915 is 92 commits behind main, both required checks are SUCCESS, and it reads
  `mergeStateStatus` `CLEAN`. With the setting on, it would read `BEHIND`.
- On 2026-11-10 the non-admin `haynes-dev-bot` merged #4333, #4340, #4693, #4670 and
  #4753 while they were 11 to 80 commits behind main.
- `GET /branches/main/protection` returns 403 to the bot, so the setting itself was not
  read. Confidence: high.

So GitHub merges a head whose checks never saw a later change to the same files. On
2026-11-10, #4334's checks (2026-11-08T04:27Z) predated #4372, which changed
`scripts/comfyui/Dockerfile` at 00:00:50Z.

The rule:

- **Reference time.** The earlier of the head commit's date and the start of each
  expected check's newest attempt. The head date is part of it because a re-run tests the
  old merge commit.
- **Read.** For each changed file, the tick reads
  `gh api repos/<repo>/commits?sha=main&path=<file>&since=<reference>`. Any commit strictly
  newer than the reference time makes the checks stale.
- **Fail closed.** A failed read gives `unreadable: main commits since the head's checks
  for <file>`. No readable reference time also blocks.
- **Shared with the merge step.** Story 752 calls the same exported functions:
  `checksReferenceTime`, `mainCommitsTouching` and `checksPredateMain` in
  `lane-classify.mjs`.

### R8: shared components

`shared_components.entries` lists, for each directory under
`kubernetes/shared/components/` that has a `kustomization.yaml`, the directories whose
`kustomization.yaml` includes it.

- A component that includes another passes its consumers on. `common/repos` includes
  `./app-template`.
- A changed file maps to the deepest component that contains it. Its consumers become the
  PR's `targets`, and each target maps to the Kustomizations at or below it.
- A namespace-level consumer (for example `kubernetes/main/apps/frontend` for
  app-template) counts every Kustomization of that namespace.
- The path classes and path rules judge the consumers as well, as if the PR had changed
  them.
- On 2026-11-10, #4770 (the actions-runner pin in `arc/dind`) got `target-unmapped`. It
  now maps to the four runner pools that include the component, plus sigo-alumni.
- The classifier selftest rebuilds the list from the repository and fails when it
  differs. To regenerate it, run
  `node scripts/upgrade-agent/lane-classify-selftest.mjs --print-shared-components`.

### R10: ceph-csi

The package rule `ceph-csi-chart` matches `/^ceph-csi-/`, and the path rule
`ceph-csi-apps` matches `kubernetes/main/apps/storage/ceph-csi/**`. Both send the PR to
`LANE-B-AGENT`.

- Why: ceph-csi backs every dev-env session home (gasha01-rbd) as well as Prometheus and
  Loki. A driver roll while a session volume is being created or attached can fail that
  session.
- Before this rule, only the shepherd's LLM verdict (`LEFT`) kept #4607 out of lane A.
- The merge waits for the dev-env v2 coordinator to confirm that no session volume
  create, resume or attach is in flight. That check is lane-B work (piece 2). The night
  lane only records the rule.

## What step 2 records

- **`fingerprint`** names the change, not the commit. It is `sha256:` over each file
  section of the head commit's own diff, fetched by its SHA
  (`gh api -H 'Accept: application/vnd.github.diff' repos/<repo>/commits/<head>`), in
  path order: the header lines (`diff --git`, file modes,
  new, deleted or renamed, `---` and `+++`), then every added or removed line exactly as
  written. The `index` and similarity lines, hunk headers, context lines and the "No
  newline" marker are left out. A Renovate rebase onto a moved base therefore keeps the
  value, and any change to a path, a mode or an added or removed line changes it.
  - Only a PR with one commit has a fingerprint: `fingerprint_basis` is `commit-diff`.
    It is `multi-commit` for more commits, and `binary` or `unavailable` (value `null`)
    for a binary section or a failed read.
  - Because the diff is read by SHA, a push between the reads cannot pair one head with
    another head's patch.
  - Known limit: context is left out, so the same added and removed lines at another
    place in the same file give the same value. Any later use must still require green
    checks on the new head. A binary section, an
  empty diff or a failed read gives `null`, and `fingerprint_basis` then says `binary`
  or `unavailable`. The record also carries `commit_count` and `change_key` (the
  package, version and file identity that step 1 called `fingerprint`).
  - Evidence: on 2026-11-10 the rebases of #4477 (871dab84 to 41c67183) and #4607
    (d91793d3 to ea9bbac8) were byte-identical; the rebase of #4336 (c90af518 to
    da0449f2) changed one line. These three pairs are the fixture
    `scripts/upgrade-agent/fixtures/lane-rebase-pairs-20261110.json`.
  - Step 2 only records the value. Using it, for example to carry Tom's approving review
    or a vet decline across a rebase, needs a single Renovate commit and an equal
    fingerprint, and comes in a later step.
- **`cluster_effect_wait`** appears on a source PR for one of our own images whose
  digest pin Renovate auto-merges (`own_image_pin_wait` in `upgrade-rules.json`). The
  cluster changes only when Renovate's next hosted run opens the pin PR, and those runs
  follow pushes to main. On 2026-11-10 the upgrade-shepherd and upgrade-agent pins
  waited 55 minutes for the next push, so source to cluster took 87 minutes. Such a unit
  counts as verified only when the pin has merged, Flux applied it and the workload
  soaked on the new digest.
- **Exact pins.** Renovate's pip table prints `==0.36.2`. Step 1 could not compare
  that with a hold's `<2.0.0` and recorded a false hold on #4757 on every tick of the
  night. The classifier now reads an exact pin (`==`, `===`, `=`) and a leading `v` as
  the version it names. A range (`>=`, `^`, `~`, `~=`, `<`, `!=`) is left as written.
  It has no comparable version, so it still counts as held (fail closed). #4757 now
  reads as `BLOCKED` because its `validate` build fails, which is the real reason.

## Lane verdicts in the labeler

`.github/workflows/pr-disposition.yml` reads a lane verdict only from a comment that
meets all three conditions:

- it is by `haynes-ops-bot[bot]` (the REST login with the `[bot]` suffix);
- it starts with `<!-- shepherd-vet sha=<current head> -->`;
- `<!-- upgrade-lane v1 -->` is on its own line right after the `VERDICT:` line.
  A marker quoted anywhere else does not count.

| Token | Label | Ranks like |
|---|---|---|
| `OWNER-WINDOW` | `disposition:stranded` (the list that needs Tom) | a `HOLD`: before the carve-outs |
| `BLOCKED`, `AGENT-SESSION` | `disposition:manual-only` | a `HOLD`: before the carve-outs |
| `NIGHTLY`, `SCHEDULED` | `disposition:shepherd` | the shepherd's `AUTO-MERGE`: a manual carve-out (Kometa, LazyLibrarian, ComfyUI, HA) still wins |
| `MERGED`, `REVERTED`, `EXCLUDED`, `HOLD` | the handling that existed before step 2 | |

An enabled auto-merge and an unreadable merge state still come first. A verdict that
moves a PR toward a person outranks policy; a verdict that moves it toward automation
does not. A comment without
the lane marker keeps its old label. No such comment exists yet, because the lane writes
no comments until step 3. `scripts/pr-disposition-test.cjs` pins each token, a token by
a non-bot author (ignored), a token for an older head (ignored) and the same tokens
without the marker (unchanged).

## Reading the 2026-11-10 comparison

The report is `dryrun-comparison-20261110.md` on the tracker (OP512). Read it this way:

- The 20 scheduled ticks (06:00Z to 10:45Z) began after the hand-run had already merged
  8 of its 10 night updates (05:37Z to 05:55Z). Its verdicts on those PRs are therefore
  counterfactual: the step-1 classifier run read-only against each PR at its delivered
  head. They are not tick output, and the report labels them as such.
- `would merge` never advances in a dry run, because nothing merges. Every window tick
  named #3915. A live lane would have moved on to the next candidate.
- The gates the tick could not see for the counterfactual PRs (backups, the Flux
  baseline, alerts) are taken from the hand-run's checkpoints and from Prometheus.

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

Where each story 550 rule shows in the state:

| Rule | Field |
|---|---|
| R5 | `records[].gates.blockers`, entries starting `critical alert` (`… belongs to`, `… cannot be traced`, `… names no workload`, `… has no namespace label and the target is cluster-wide`). Alerts that did not block: `records[].gates.alerts_not_blocking.unrelated` and `.cluster`. All firing alerts: `summary.baseline.critical_alerts` (`<alertname> @ <namespace> <subject>`). The target's class: `records[].cluster_wide` |
| R7 | `records[].checks.reference_time`, `records[].checks.main_newer` (`path`, `sha`, `at`), the blocker `checks predate main: …`, or `unreadable: main commits since the head's checks …` |
| R8 | `records[].targets` (consumer directories in place of `kubernetes/shared/components/…`), `records[].via_components` (`<component> -> <consumer>`), no `target-unmapped` blocker for a mapped component |
| R10 | `records[].lane` `LANE-B-AGENT`, `class` `rule-agent`, `rules` containing `ceph-csi-chart` or `ceph-csi-apps` |

```bash
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.records}' \
  | jq '.[] | select(.lane=="LANE-A") | {pr, blockers: .gates.blockers, not_blocking: .gates.alerts_not_blocking, main_newer: .checks.main_newer, via: .via_components}'
```

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
- R8's map can lag the repository. The selftest workflow runs on changes to the lane,
  `scripts/upgrade-agent/**`, workflows and `.renovate/**`, not on every
  `kustomization.yaml`. A PR that adds a new consumer of a component without touching
  those paths leaves the map short until the next run of `Upgrade Agent Selftests`. Until
  then, a change to that component gates fewer Kustomizations than it reaches. Adding
  `kubernetes/**/kustomization.yaml` to that workflow's paths would close the gap; that is
  a change to the workflow and its check-map entry, and was out of story 550's scope.
- R7 compares commit dates. A squash or rebase merge on GitHub gets the merge time as its
  committer date, so the comparison holds for those. A "Create a merge commit" merge
  brings in branch commits under their old dates, and a path-filtered history may not
  list the merge commit itself. The repository allows merge commits, but none of the 500
  newest commits on main (read 2026-11-11) is one: all are squash merges by `web-flow`.
  If merge commits come into use, R7 needs a comparison by ancestry instead.
- R5 resolves a `deployment` through its pods, because the lane cannot read Deployments.
  A Deployment scaled to zero cannot be traced, so its alert blocks its namespace (fail
  closed).

## Tests

```bash
node scripts/upgrade-agent/lane-classify-selftest.mjs      # lanes, drift (workflows and shared components), own images, the 27 reviewed PRs, R7/R8/R10
node scripts/upgrade-agent/lane-night-dryrun-selftest.mjs  # the dry-run guarantee, the gates, R5 and R7/R8 through the tick
```

Since step 2 these run in CI as `Upgrade Agent Selftests` on pull requests and pushes to
main that touch the shepherd app (`kubernetes/main/apps/upgrade-agent/shepherd/app/**`),
the two shared shell sources the selftests execute (`coordination-lib.sh`,
`respond.sh`), `scripts/upgrade-agent/**`, any workflow file (for the drift test) or
`.renovate/**`. The workflow runs, one at a time:

- the two lane selftests above, plus `node --check` and `jq .` on the shipped files;
- the shell selftests: `coordination-sig`, `held-audit`, `shepherd-verdict`,
  `stale-corpse` and `triage-claim` (`scripts/upgrade-agent/*-selftest.sh`);
- the delivery helper's offline suite:
  `python3 -I -B scripts/upgrade-agent/test_deliver_reviewed_pr.py -v`.

`Upgrade Agent Selftests - Success` passes when every job passed or was skipped. It is
**not** a required check; branch protection is the owner's. The workflow's own entry in
the check map (`upgrade-agent-selftests`) is what keeps the drift test green.

```bash
node scripts/pr-disposition-test.cjs                        # the labeler truth table, lane tokens included
```

## What later steps add (design, rollout order)

2. Done: the selftest workflow and the labeler mapping. The fingerprint and the pin
   wait are recorded but not used yet.
3. The per-PR record as one bot comment, the comment-author check in the shepherd
   pre-filter, and the one-retry rule for stalled checks.
4. Coordination with triage and the gate: ownership of a regression signature, the
   morning digest and the orphan page.
5. The first live merge: one class, one merge a night, a stateless leaf. Then stateful
   targets and our own images. That step adds a live mode, a write-capable token, the
   verify-and-revert loop and the change lock.
6. Lane B: work orders, an ask phase and a scheduled phase.
