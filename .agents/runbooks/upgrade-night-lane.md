# Nightly upgrade lane (OP512; dry-run in git)

The nightly lane will merge low-risk Renovate updates one at a time at night, after the
backups, with a health check after each one. Tom asked for upgrades to be 100% agent
driven (OP512, comment 6168). The design is OP512 comment 6193
(<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/512>).

**Status: dry-run in git.** The lane classifies updates and records what it would do. It
merges nothing and labels nothing. Since story 549 it writes one record comment on each
open Renovate PR and ticks Renovate's retry box on at most one PR a tick. See
[PR records and the stalled-check retry](#pr-records-and-the-stalled-check-retry-story-549).

**Story 752 added** a live mode behind the switch, the merge step, the change lock and a
token that can merge
(<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/752>). Live
mode merges only a class named in an allowlist, and git ships that allowlist empty with
`UPGRADE_NIGHT_MODE: dryrun`, so the lane still merges nothing. Each dry-run tick takes
and releases the lock and records the merge step's verdict. See
[Live mode, the merge step and the change lock](#live-mode-the-merge-step-and-the-change-lock-story-752).

**Story 551 added** one merger at a time
(<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/551>), still
dry-run. It works on facts, not clocks (Tom's ruling Q-548-04): no actor reads another
actor's schedule.

- **Settle gate:** no lane merge while main has a change Flux has not applied that reaches
  the candidate's target, its chain or a cluster-wide part. It also waits while the target
  has not applied Flux's revision or is not settled, or while a cluster-wide part is
  mid-change.
- **F4:** no lane merge while a shepherd Job that can merge is running.
- **The shepherd:** stands aside while the change lock shows a lane change in progress.
- **One owner:** a PR on the shepherd's ramp is the shepherd's, never the lane's.
- **Hand runs:** they run the settle gate and F4 too.

Renovate is not changed. See [One merger at a time](#one-merger-at-a-time-story-551).

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

**Story 553 added the read-only verifier**
(<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/553>). On ticks
after a merge it reads whether the rollout is healthy and gives each merged update a verdict:
verified, failed, not yet or cannot tell, fact by fact, with the other changes on main since
the merge. While the lane merges nothing it practises on Renovate's and the shepherd's
merges and writes those verdicts to `upgrade-lane-state` only. See
[The verifier](#the-verifier-story-553).

**Story 554 added who handles a regression and the morning summary**
(<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/554>). The lane, triage
and the gate share one claim per regression signature in `upgrade-remediation-state`. Only a
live tick claims, for the lane's own merge; dry-run and practice read only. Tom gets one quiet
message at 06:30 New York. The weekly orphan page counts only PRs with no lane. See
[Who handles a regression](#who-handles-a-regression-story-554) and
[The morning summary](#the-morning-summary-story-554).

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
- The init container mints a GitHub token with the shepherd's rights: contents and pull
  requests write, checks read (Tom's ruling Q-548-02, 2026-11-11, recorded on story 752).
  It also writes the permission JSON it sent to GitHub to `/creds/gh_token_permissions`,
  and each tick logs those names. The token itself is never logged.
- The token can merge, so the dry-run guarantee rests on the code: dry-run never reaches
  a GitHub write call. The selftests check this statically and against stub binaries.
- `run-night.mjs` accepts `UPGRADE_NIGHT_MODE` `dryrun` or `live` and nothing else. It
  also refuses a class name in `UPGRADE_NIGHT_LIVE_CLASSES` that does not exist. Either
  refusal exits 3 before reading anything.
- When a tick fails, see [When a tick fails](#when-a-tick-fails) below.

The code is in the same `resources/` directory as the shepherd and is mounted from the
`upgrade-shepherd-scripts` ConfigMap:

| File | What it does |
|---|---|
| `lane-classify.mjs` | Puts one PR in a lane from its facts. It is a pure function, with a read-only CLI around it. |
| `upgrade-rules.json` | The registry: path classes, the check map, our own images, per-package rules and CNPG clients. |
| `run-night.mjs` | One tick: classify the PRs, check the gates, choose one PR, run the merge step under the change lock and write the state. |
| `night-merge.mjs` | The merge step, the change lock and the hand-run CLI (story 752). |
| `night-verify.mjs` | The read-only verifier of each merge, and a read-only CLI for named merged PRs (story 553). |
| `night-claim.mjs` | The lane's claim on a regression signature in `upgrade-remediation-state` (story 554). |
| `night-summary.mjs` | The morning summary, composed every tick (story 554). |

## Lanes

The strictest finding wins. A PR that touches several paths takes the strictest lane any
of those paths asks for. A change to a shared component also counts every directory that
includes it (R8). An app-template bump reaches `dev-env-system`, so it is `EXCLUDED`.

| Lane | When |
|---|---|
| `EXCLUDED` | dev-env v2 paths, the merger's own paths (`upgrade-agent/**`, `scripts/upgrade-*`), workflow files, Renovate config, PRs not opened by Renovate, paths on the shepherd's ramp (class `shepherd-ramp`, story 551). |
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
- **Settle gate (story 551):** facts about the candidate's target (F1 to F3), for a
  candidate with an immediate cluster effect. The blocker starts `settle gate:`. See
  [The settle gate](#the-settle-gate).
- **Merge window:** 01:30 to 04:45 New York.
- **Change lock (`upgrade-change-lock`):** taken by the merge step after the gates, not a
  gate of its own. See
  [The change lock](#the-change-lock). Since story 551 the shepherd stands aside while it
  shows a lane change in progress, and a hand run refuses while it is held.

Of the PRs that pass every gate, the tick picks one. It prefers no cluster effect, then
delayed, then stateless, then stateful, then the lowest PR number. In live mode it
prefers a PR whose class is allowlisted. The merge step then runs on that PR.

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
   alerts carry the object's namespace there).
   - An alert in a namespace of the target's `dependsOn` chain (each Kustomization's own
     namespace and its `targetNamespace`) blocks only when it traces to a chain
     Kustomization, such as a Flux failure of `volsync-system/volsync` for mosquitto.
     Anything else there is listed under `gates.alerts_not_blocking.unrelated`; the
     namespace fallback does not apply there. If that namespace's pods, Jobs or CronJobs
     cannot be read, an alert naming a workload there blocks (fail closed). Both were
     added after the review of PR 4825.
   - An alert in any other namespace does not block, as before.
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

## Live mode, the merge step and the change lock (story 752)

### The switch

Two env vars on the `upgrade-night` container, both in git:

| Variable | Git ships | Meaning |
|---|---|---|
| `UPGRADE_NIGHT_MODE` | `dryrun` | `dryrun` or `live`. Anything else exits 3 before a read. |
| `UPGRADE_NIGHT_LIVE_CLASSES` | `""` | Lane-A classes live mode may merge, comma-separated: `no-cluster-effect`, `delayed-cluster-change`, `cluster-change`, `immediate-cluster-change`. An unknown name exits 3 before a read. |

- Live mode with an empty allowlist merges nothing. The merge step still runs on the
  chosen PR and refuses it with `class <c> is not in the live allowlist
  (UPGRADE_NIGHT_LIVE_CLASSES: empty)`.
- Switching a class live is story 556. Do not name a class before verification (story
  553) is built: see [After a merge](#after-a-merge).
- `scripts/upgrade-agent/lane-night-dryrun-selftest.mjs` fails if git ships anything but
  `dryrun`, an empty allowlist and the token below. Story 556 changes that test together
  with the switch.
- Live mode, and any run with `--write-state`, refuses (exit 3, before a read) when
  `UPGRADE_NIGHT_NOW` is set. A lock taken at a pinned future time would carry that
  time's expiry. The variable is for a local read-only run. The selftests pin the clock
  with a test-only preload (`scripts/upgrade-agent/test-fake-clock.mjs`), which the
  CronJob never loads (a selftest checks its env and command).

**The token.** The init container requests
`{"contents":"write","pull_requests":"write","checks":"read"}`, the shepherd's rights,
by Tom's ruling Q-548-02. GitHub mints exactly the requested permissions or refuses the
mint. The tick reads the request back from `/creds/gh_token_permissions` and logs the
names. The merge step refuses a token that cannot merge (no contents or pull requests
write), a token wider than those three, and a file it cannot read.

### Why Node

The merge step is a port of the preflight in `scripts/upgrade-agent/deliver_reviewed_pr.py`
and the handoff's `preflight.py` to Node (`night-merge.mjs`). The shepherd image is
`node:24-slim` and has no Python. Adding Python means an image change, and the lane's
code would then span two languages.

### The merge step

**Which PR.** In live mode, the PR the tick chose. In dry-run, the chosen PR, or, when
the tick chose none, the nearest lane-A candidate in pick order. So every dry-run night
records a verdict. The record then says `chosen: false`, and the first lane refusal names
the gate blockers.

**What it reads afresh**, right before the merge and under the lock:

- `gh pr view` with state, draft flag, head and head branch, base, whether it comes
  from another repository, author, mergeability, files, commits and every check and
  commit status on the head, read again while GitHub has not computed the mergeability
  (below);
- the REST commit list (`repos/<repo>/pulls/<n>/commits`): each commit's author and
  committer accounts and its signature;
- story 550's R7 through the same three functions
  (`checksReferenceTime`, `mainCommitsTouching`, `checksPredateMain`);
- for each changed file, the newest commit on main that touches it
  (`commits?sha=main&path=<file>&per_page=1`), and whether the head contains it
  (`compare/<head>...<sha>`: `ahead_by` 0 means it does).

**Mergeability is read until GitHub has answered (story 773).** GitHub works out whether
a PR can merge only when asked. The first read after main changes answers `mergeable`
`UNKNOWN` or `mergeStateStatus` `UNKNOWN`. On 2026-11-12 that refused PR 4752 on 5 of 20
dry-run ticks, each the first tick after a merge to main. `readMergeViewAnswered()`
therefore reads the view again while an open PR reads `mergeable` `UNKNOWN`, or
`MERGEABLE` with the merge state still `UNKNOWN`. It pauses 2s, 4s and 8s between reads:
at most 4 reads and 14s of pauses (`MERGEABLE_REREAD`).

- Every decision is on the last view read, taken whole. No field of an earlier read is
  kept.
- If GitHub never answers, the refusal is the same as before:
  `GitHub reads it UNKNOWN, not MERGEABLE | merge state UNKNOWN`.
- If the head changes between reads (two readable SHAs that differ), the step refuses and
  reads nothing more about the new head in that tick. A read with no readable head is
  refused as `unreadable: the PR head SHA`, not as a move.
- A closed PR, or one GitHub has already answered (`CONFLICTING` included, whatever the
  merge state), is read once.
- `facts.mergeable_reads` records the number of reads in both modes. The merge-step log
  line ends `mergeability <mergeable>/<merge state> after <n> read(s)`.

This is a bounded poll of GitHub's own answer about one PR, so it is allowed under ruling
Q-548-04. It does not wait on another actor or on another actor's schedule.

The re-reads happen in `evaluate()`, among the step's fact reads. In live mode the
freshness checks come after them, so they measure that time: the 240s lease minimum and
the 300s gate age at the merge call. A tick reaches the lock take about 27s after it
starts, and the step itself takes about 3s (tick of 2026-11-12T10:45Z). With GitHub
answering at its usual speed, 14s of pauses and three more reads leave plenty of room
under both limits.

The bound is a count of reads, not of time. Each read keeps `runReadOnly`'s 120s
deadline, so if GitHub answered slowly with `UNKNOWN` every time, the step could pass a
limit. That check then refuses: the tick makes no merge, and the next tick tries again.
The Job's `activeDeadlineSeconds: 600`, equal to the lease, ends the tick before a lease
it holds can lapse.

Nothing is re-read just before the merge call: the call is pinned with
`--match-head-commit`, and GitHub refuses to merge a PR it cannot merge.

**It refuses** (fail closed: a read it cannot make is a refusal named `unreadable: …`):

| Refusal | Why |
|---|---|
| not open, a draft, base not `main`, a commit not Renovate's alone | as in the handoff preflight |
| `opened by … not the Renovate app`, `a pull request from another repository`, `head branch … is not under renovate/` | the author must be the Renovate app as gh reports it (`app/renovate`, `is_bot` true), from this repository, on a `renovate/` branch |
| `commit … is not the Renovate app's (…)` | every REST commit authored by `renovate[bot]` (type `Bot`, id 29139614), committed by `web-flow` with a valid signature, as GitHub makes Renovate's commits. Read live on 2026-11-11 for every open Renovate PR |
| `head moved: …` | the head is not the one the tick chose; the merge is pinned to it anyway |
| `GitHub reads it … not MERGEABLE`, `merge state …` | `CLEAN`, `HAS_HOOKS` and `UNSTABLE` pass; the check scan below decides on non-required checks. `UNKNOWN` here means GitHub had still not answered after 4 reads |
| `head moved between reads of the pull request (… on read 1, … on read n); not judging the new head in this tick` | the PR moved while the step waited for GitHub's answer (story 773) |
| `unreadable: the pull request (gh pr view, read n of 4, while GitHub had not yet computed its mergeability)` | a re-read failed. Read 1 failing keeps the old text, `unreadable: the pull request (gh pr view)` |
| `commit status <context> is pending (PENDING)` or `(EXPECTED)` | **gap 1.** The handoff preflight read a pending status as complete |
| `check still running: …`, `check failed: …` | any check run on the head, newest attempt counted. `Claude Review (advisory)` is never a gate |
| `required check not SUCCESS: …` | `Flux Local - Success` and `Diff Scope - Success`, literals as in `deliver_reviewed_pr.py` |
| `expected check …` | every check the check map expects for the files, as in the classifier |
| `checks predate main: …` | **gap 2.** R7, reused, not rewritten |
| `head is behind main: <file> changed by <sha> …` | **gap 3.** "Require branches to be up to date" is off for main, so GitHub would merge it |
| `the change lock …`, `paused before the lock take: …` | see below |
| `class … not in the live allowlist`, `the token …` | the lane's own conditions (listed apart as `lane_refusals`) |

**Just before the merge call** (live mode only) it checks again, and refuses on any of:

| Refusal | Why |
|---|---|
| `the change lock has …s of lease left, under 240s` | not merging on a lease that may lapse |
| `the merge window is closed at the merge call (now HH:MM)` | the tick's window gate was judged at its start; a stalled tick must not merge after 04:45 |
| `the tick's gates are …s old at the merge call, over 300s` | `max_gate_age_seconds`: the backups, alerts and Flux baseline were read at the tick's start |
| `settle gate at the merge call: …` | the settle gate read again (story 551), before the freshness checks: another actor's change may have reached main while the step read the PR |
| `F4 at the merge call: …; yielded-to-shepherd` | read after the `merging` write: a shepherd Job that can merge started meanwhile. The lock is released with the note `yielded-to-shepherd`, and the next tick tries again |
| `paused just before the merge call: …`, or the pause unreadable | a pause set while the step read the PR stops the merge |
| `the change lock could not be moved to phase merging` | the post-merge hold must be written before the call |

**The merge call** is `gh pr merge <n> --repo thaynes43/haynes-ops --squash
--match-head-commit <head>`. It never carries `--admin`, `--auto` or a force flag;
`checkMergeArgv` refuses them, and GitHub refuses the merge if the head moved.

- The call is made only in live mode, with a clean verdict, after the checks above.
- Before it, the lock moves to phase `merging` with the 4-hour post-merge lease. A kill,
  a timeout or a lost reply during the call therefore cannot leave only the short lease.
- `callMerge` throws for any other mode before it starts a process.
- After the call, a read checks that the PR is `MERGED` at that head.

**Timeouts.** Every read through `runReadOnly` is killed after 120 seconds and counts as
a failed read. The Job has `activeDeadlineSeconds: 600`, equal to the lock's lease and
under the 15-minute period, so a tick is dead before a lease it took can expire.

### The change lock

ConfigMap `upgrade-agent/upgrade-change-lock`. It is runtime state, not Flux-managed. The
ServiceAccount's Role `upgrade-shepherd-spend` already allows the create and update it
needs.

| `data` key | Meaning |
|---|---|
| `holder` | `upgrade-night/<pod>/<random>`; empty when free |
| `phase` | `taken`, `merging` (written just before the merge call), `merged-awaiting-verification`, `merge-unknown` or `released` |
| `purpose`, `mode`, `pr`, `head` | what the holder is doing |
| `taken_at`, `expires_at`, `lease_seconds` | the lease |
| `took_over_from`, `took_over_phase` | the holder of an expired lease this take replaced, and its phase. A `merging` or `merge-unknown` phase means a merge may have happened that nobody verified |
| `last_holder`, `last_purpose`, `released_at`, `last_note`, `last_took_over_from`, `last_took_over_phase` | after a release; the take-over fields stay visible |

- **Free:** no object, or `holder` empty. **Expired:** `expires_at` has passed; the
  next taker takes it over. **Held:** anything else.
- **Take.** A create when the object is absent, else a replace at the
  `resourceVersion` just read. A second taker fails with `AlreadyExists` or `Conflict`
  and holds nothing.
- **Lease.** 600 seconds, shorter than the 15-minute tick: a crashed tick blocks at most
  the next one. The values are `night.change_lock` in `upgrade-rules.json`.
- **When.** Every tick run with `--write-state` takes it after the gates, before the
  merge step's reads, and releases it before the state write. This happens in dry-run
  too, and with no candidate, so the mechanism runs every night. A local run (no
  `--write-state`) never takes it, so it never merges.
- **Release on every exit path:** `finally`, plus an exit handler and a SIGTERM handler
  for a process that ends while holding it. A SIGKILL leaves it to the lease. A release
  is also a replace at the `resourceVersion`, so it never overwrites a new holder.
- **Never released once a merge may have happened:** from the `merging` write on, every
  exit path keeps the lock with the 4-hour lease.
- **Fails closed:** an unreadable lock, a holder with no readable `expires_at`, or a
  take the API refuses means no take and no merge. A lock with an unreadable expiry
  stays held until a person clears it.

To read it (the shepherd and hand runs read it the same way, story 551):

```bash
kubectl -n upgrade-agent get cm upgrade-change-lock -o jsonpath='{.data.holder} {.data.phase} {.data.expires_at}'; echo
```

To clear a lock left with an unreadable expiry (a person, after checking that no tick
runs): `kubectl -n upgrade-agent delete cm upgrade-change-lock`.

### After a merge

The verifier (story 553) judges a merge on the ticks after it; see
[The verifier](#the-verifier-story-553). It does not touch the lock. A live merge therefore
still keeps the lock, phase `merged-awaiting-verification`, for `post_merge_lease_seconds`
(4 hours). Until it expires no tick merges, so the lane merges at most once a night.
Releasing the hold on a verified merge, and the revert, are stories 753 and 556. Under
pause the tick stops before the verifier runs. `owedWork()` in `night-merge.mjs` still
records `built: false` for that pause path and for the revert.

The outcome of the call:

- **`merged`**: the PR reads `MERGED` at the head. The lock keeps the 4-hour hold.
- **`merge-failed`**: the call returned a real non-zero exit and the PR still reads
  `OPEN`. The lock is released.
- **`merge-unknown`**: anything else, including a call with no exit status (timed out
  or killed) even when the PR reads `OPEN`. GitHub may have merged, so it is treated as a
  merge: phase `merge-unknown`, the 4-hour hold kept.

### Pause and suspend in live mode

- **`data.pause`** on `upgrade-lane-state` stops new merges and new lock takes.
  - A tick reads it at its start, again just before the lock take, and again just before
    the merge call. A pause set at any point before the call stops that tick's merge. A
    pause it cannot read refuses.
  - The hand run reads it at its start and, with `--merge`, again just before the call.
  - Verification and reverts already owed for a merge would carry on through the
    `owedWork()` hook. They are not built yet.
- **`suspend: true`** on the controller in git stops every tick, including that owed
  work. Use pause unless you mean that.

### The hand run (rehearsal)

`night-merge.mjs` is also a CLI for one named PR. **A real `--merge` needs the owner's
yes for that PR**; the CLI reads no approval.

What it checks:

- **the classifier**, on the same facts the tick gathers. The PR must be `LANE-A`:
  holds in `.renovate/holds.json5`, majors and prereleases, package rules (ceph-csi,
  for one), path classes and excluded paths, and a current-head vet `HOLD` all refuse.
  `--check-only` prints the lane and the reason;
- the merge step's preflight, as above;
- the lock and the pause. It refuses while either is set, and re-reads the pause just
  before a merge call;
- the settle gate and F4 (story 551), at its start and, with `--merge`, again just before
  the call. Their refusals start `settle gate:` and `F4`, and `… just before the merge call:`.

What it skips, because only the tick knows it:

- the merge window;
- the class allowlist;
- the tick's other gates (backups, the Flux baseline, critical alerts, work orders).

It also differs from the lane in three ways:

- It does not take the lock: a session pod cannot write ConfigMaps in `upgrade-agent`.
  So the lane and the shepherd cannot see a hand run. Keep hand runs out of the lane's
  window, and do not run one while the shepherd is running (`kubectl -n upgrade-agent get
  jobs`).
- It uses the caller's own `gh` identity and reads no token file.
- `--merge` refuses any test clock (exit 2): `UPGRADE_NIGHT_NOW` set, `FAKE_NOW` set, or
  a `Date.now` that is not the native one.

```bash
# from the repository root of an up-to-date worktree
PR=3915
HEAD_SHA=$(gh pr view "$PR" -R thaynes43/haynes-ops --json headRefOid -q .headRefOid)
# 1. check only: prints the verdict, changes nothing (exit 0 = would merge, 1 = refused)
node kubernetes/main/apps/upgrade-agent/shepherd/app/resources/night-merge.mjs --pr "$PR" --head "$HEAD_SHA" --check-only
# 2. the real run, only after the owner's yes: merges once at that head if the verdict is clean
node kubernetes/main/apps/upgrade-agent/shepherd/app/resources/night-merge.mjs --pr "$PR" --head "$HEAD_SHA" --merge
```

Exit 0 means merged (or, with `--check-only`, would merge). Exit 1 means refused and
nothing merged. Exit 2 is a usage error. Exit 3 means the merge call failed or its
outcome could not be verified. `--json` prints the verdict as JSON. On 2026-11-11 the
check-only run read PR 3915 at `a7174f4e` as **would merge**:

- main was 94 commits ahead of the head, but no main commit had touched its two files
  (`scripts/tests/dev-env-archive-guard/go.mod`, `go.sum`) since its base;
- its checks ran at 2026-11-10T00:11Z, inside the 7-day limit.

So the rules above do not refuse it without a refresh.

### Known limits of the merge step

- R7 and the behind-main rule look at the PR's own files. A check can also depend on
  other files: the archive-guard workflow that PR 3915 needs also watches Kyverno policy
  files. A main change to such a file after the checks does not make them stale here.
- The behind-main rule assumes main is linear. It is today: squash merges only. With
  merge commits, a newer commit to the file could be contained while an older one is
  not.
- The hand run does not take the lock, so the other actors do not see it.
- `/creds/gh_token_permissions` records the permissions the init container requested,
  not the ones GitHub granted. GitHub mints exactly the request or refuses it, so the two
  agree on a successful mint. Recording the grant itself needs `github-app-token.sh`
  (baked into the image) to print the response's `.permissions`, which is outside this
  story's files.
- `renovate_pr_authors` is now `["app/renovate"]` alone. The plain `renovate` and
  `renovate[bot]` logins were dropped. No PR open on 2026-11-11 changes lane, and no
  fixture changes.

## One merger at a time (story 551)

Rule R6 of the 2026-11-10 comparison: only one actor merges a change that reaches the
same target, or a cluster-wide part, while another actor's change there is still in
flight. On 2026-11-10 the shepherd merged #4760 (cni-plugins for multus) at 05:01:56Z
and #4761 (dragonfly-operator) at 05:02:06Z. Renovate's auto-merge followed with #4767 at
05:03:25Z, and multus was mid-rollout until 05:36:43Z. Four actors merge to main: Renovate
(through GitHub's auto-merge), the shepherd, the lane and people (hand runs and sessions).

**The ruling (Tom, 2026-11-11, Q-548-04, comment 7135 on story 551): facts, not clocks.**

- Renovate is not changed: no schedule change and no auto-merge change.
- No actor has to know another actor's schedule. Every check reads the state at the
  moment the lane or the shepherd acts.
- Two updates to different apps do not interfere. What matters is another change landing
  on the same app, or on a cluster-wide part, while the lane checks its own update, and
  the lane can see that from what changed.

### How Renovate's merges land (established 2026-11-11, read-only)

Data: the 110 merged Renovate PRs that touched `kubernetes/**`, merged from 2026-10-28T00:00Z
to 2026-11-11 (`gh pr list --state merged --author app/renovate --search
"merged:>=2026-10-28" --json number,mergedAt,mergedBy,autoMergeRequest,files`).

| New York hour | Merges | GitHub auto-merge (Renovate enabled it) | Shepherd (`haynes-ops-bot`) | A session (`haynes-dev-bot`) |
|---|---|---|---|---|
| 00 | 15 | 1 | 4 | 10 (the 2026-11-10 hand-run) |
| 01 | 9 | 9 | | |
| 02 | 1 | 1 | | |
| 03 | 4 | 4 | | |
| 04 | 4 | 2 | 2 | |
| 05 to 07 | 0 | | | |
| 08 | 2 | | 2 | |
| 09 to 11 | 0 | | | |
| 12 | 1 | | 1 | |
| 13 to 17 | 0 | | | |
| 18 | 2 | 2 | | |
| 19 | 1 | 1 | | |
| 20 and 21 | 0 | | | |
| 22 | 44 | 44 | | |
| 23 | 27 | 27 | | |
| **Total** | **110** | **91** | **9** | **10** |

- **Inside the lane's window (01:30 to 04:45): 12 of 110**, on 6 of the 15 nights. Ten
  were Renovate's auto-merges (#4441, #4442, #4492, #4615, #4687, #4770, #4772, #4773,
  #4775, #4817). Two were the shepherd's 04:00 run (#4475 on 2026-10-31, #4677 on
  2026-11-06).
- **Outside Renovate's schedule (22:00 to 06:00): 6.** Three are the shepherd's (08:00,
  08:01 and 12:01). The other three are GitHub auto-merges of Renovate PRs: #4711 and
  #4712 at 18:35 and #4642 at 19:13 New York. Each landed 2 to 3 minutes after Renovate
  pushed a new commit to the PR outside its schedule (a rebase; #4711 at 23:33:02Z, merged
  23:35:34Z, 8 seconds after `Flux Local - Success` completed).
- **Who merges.** For an auto-merged PR, GitHub reports `mergedBy` as the account that
  enabled auto-merge, so Renovate's PRs read `app/renovate`. They carry an
  `autoMergeRequest` enabled by `app/renovate`, and each merges seconds after its last
  required check: GitHub's native auto-merge did the merge, not a Renovate run. From
  enabling to merging took a median of 2.4 minutes and a 90th percentile of 3.1 minutes
  (94 PRs), about the time `flux-local` takes. Confidence: high.

**What Renovate's options control under `platformAutomerge: true`:**

- `schedule` only limits when Renovate creates branches. With `updateNotScheduled` at its
  default (true), Renovate still updates and rebases existing PRs outside the schedule.
  <https://docs.renovatebot.com/configuration-options/#schedule>,
  <https://docs.renovatebot.com/configuration-options/#updatenotscheduled>.
- `automergeSchedule` defaults to "at any time", and the docs warn: "When
  `platformAutomerge` is enabled, Renovate enqueues the platform PR automerge at time of
  creation, so the schedule specified in `automergeSchedule` cannot be followed."
  <https://docs.renovatebot.com/configuration-options/#automergeschedule>. Renovate's own
  PR bodies here say "Automerge: At any time (no schedule defined)".
- `platformAutomerge` lets GitHub merge once the branch protection's checks pass, and
  "Renovate re-enables the PR for platform-native automerge whenever it is rebased".
  <https://docs.renovatebot.com/configuration-options/#platformautomerge>.
- Without `platformAutomerge`, Renovate merges during its own runs, at most one PR per
  base branch per run. <https://docs.renovatebot.com/key-concepts/automerge/>.

So nothing in Renovate's configuration bounds when GitHub merges. The merge fires
whenever the required checks pass after auto-merge was enabled: at PR creation, and again
after each rebase. Confidence: high (documentation plus #4711, #4712 and #4642).

### The settle gate

`settleVerdict()` in `night-merge.mjs`, per candidate with an immediate cluster effect. A
candidate with no immediate cluster effect (no-cluster-effect, delayed) changes nothing in
the cluster now and is not judged (`records[].gates.settle.applicable: false`).

**Scope.** The gate reuses the scope the baseline gate already has, nothing new:

- **The target T:** `records[].targets`, the app directories, and for a shared component
  the consumers that include it (R8).
- **T's chain:** `kustomizationsFor` and `fluxScope` (now in `lane-classify.mjs`) give T's
  Kustomizations, their `dependsOn` chain and their namespaces.
- **T's appliers:** the Kustomizations that apply T's own, found from their
  `kustomize.toolkit.fluxcd.io/name` and `/namespace` labels, up to the root. Live, 183 of
  186 Kustomizations are applied by `flux-system/cluster-apps` (path `./kubernetes/main/apps`,
  no `wait`), which `flux-system/cluster` applies. Nothing names `cluster-apps` in the code
  (review finding S1).
- **A cluster-wide part:** an entry of `cluster_wide_targets`, which R5 uses. Its Flux
  objects are the Kustomizations whose `spec.path` falls under its globs, and the
  HelmReleases those Kustomizations applied (their Flux labels).

The facts:

- **F1. Nothing unapplied reaches T.** Compare Flux's source revision (GitRepository
  `flux-system/haynes-ops`) with main's head (`compare/<source sha>...main`). Then read
  each commit in between (`commits/<sha>`, its files). No commit may change T's paths, a
  path of T's chain, or a cluster-wide part, directly or through a shared component.
  - A file directly under `kubernetes/main/apps/<dir>/` (`namespace.yaml`, the
    namespace's `kustomization.yaml`) reaches every target and chain path in `<dir>`.
  - A file directly under `kubernetes/main/apps/` is the apps root. It belongs to the
    cluster-wide part `flux`, whose globs now include `kubernetes/main/apps/*`. The part
    already covered `kubernetes/main/flux/apps.yaml`, which defines `cluster-apps` (S1).
  - A change to another app does not hold T.
  - It fails closed on an unreadable compare, a source revision not on main's history,
    more than `max_unapplied_commits` (50), a commit list shorter than `ahead_by`, or a
    commit with 300 or more files.
- **F2. T has applied the revision and is settled.**
  - Each Kustomization of T's chain has applied the source revision
    (`status.lastAppliedRevision`, not only attempted it), is Ready at its generation and
    is not reconciling.
  - So has each applier, and it is settled. A failing applier holds T too: it has not
    applied the revision, and no baseline gate watches it (S1).
  - Each HelmRelease of T's namespaces is settled.
  - A suspended Kustomization of T, of its chain or among its appliers, or a suspended
    HelmRelease of T, holds T: a merge would not apply until someone resumes it. The
    blocker names the object (S2). A suspended object elsewhere holds nothing.
  - A Kustomization of the chain or an applier that does not exist fails closed.
- **F3. No cluster-wide part is mid-change.** No Kustomization or HelmRelease of a
  cluster-wide part is progressing or has not applied the revision. The source is checked
  too: mid-fetch, or failing.

Progressing and failing are read from conditions and generations only; no transition time
is read:

- **Progressing:** no Ready condition; Ready `Unknown`; `Reconciling` with any reason but
  `ProgressingWithRetry`; a generation not observed yet; or NotReady before attempting the
  current revision.
- **Failing, not settling:** Flux's retry loop (`ProgressingWithRetry`), `Stalled`, a
  failure at the current generation and revision, or `DependencyNotReady` behind a failing
  dependency (review finding R3). Also `DependencyNotReady` behind a suspended one: it is
  stuck, not mid-change, so F3 does not hold every candidate on it (S2).
- A failing object is listed under `failing_not_settling`, and the baseline gate judges it
  by scope.
- `DependencyNotReady` behind a dependency that is in flight (multus rolling) is
  progressing.

**F4. No shepherd Job that can merge is running.** It is read in the merge step (both
modes), and in live mode again after the `merging` write. Jobs in `upgrade-agent` that
have not finished (no Complete or Failed condition) count as follows:

| Job | Can merge? |
|---|---|
| a Job of CronJob `upgrade-shepherd`, or carrying its controller label (a summon) | yes |
| a Job of `upgrade-shepherd-triage` | only while `upgrade-remediation-state` holds a claimed, unfinished remediation (`attempted` 1, `result` none), because remediate runs inside the triage Job; an unreadable state counts as yes |
| the lane's own Jobs, `alert-responder` (its LLM has read-only Bash: `kubectl get` and `describe`, `flux get`, `grep`, `cat`, `git log` and `show`, and its query scripts; no `gh`), `upgrade-health-gate`, `dev-env-ops-curation-order` | no |
| any other unfinished Job | yes (unknown, fail closed) |

The order makes the handshake safe:

- The shepherd's Job exists before `run-shepherd.sh` reads the lock. Triage writes its
  claim before it starts `run-shepherd.sh`.
- The lane writes `merging` before it reads the Jobs.
- So at least one side sees the other. If the lane sees a Job, it yields: it releases the
  lock with the note `yielded-to-shepherd`, makes no merge call, and the next tick tries
  again.
- The exit handler keeps the lock only from immediately before the merge call
  (`held.merged`, S4). An exit or SIGTERM between the `merging` write and the call
  releases it. A SIGKILL there leaves the `merging` phase to its 4-hour lease, because no
  handler runs.

**Where it is checked:**

- at the tick's start (one read, judged per candidate);
- in the merge step, in both modes;
- in live mode just before the call. The fact reads come first, then the freshness checks
  (lease, window, gate age), then the pause, the `merging` write, F4 and the call
  (review finding R5).
- by the hand run, at its start and before `--merge`.

**The fixture.** `scripts/upgrade-agent/fixtures/settle-20261110.json` holds main's
compares and commit files as GitHub lists them, plus the Flux objects. The objects are
rebuilt from Prometheus around multus's roll; the chains are as read live. It judges
three targets:

| Instant | plex-hw-canary (none of the three merges) | ytdl-sub (#4767) | home-assistant (depends on multus) |
|---|---|---|---|
| 05:15Z: #4760, #4761 and #4767 not fetched | waits: F1, cluster-wide multus (#4760) | waits: F1, multus and its own target (#4767) | waits: F1, multus and its chain |
| 05:32:30Z: counterfactual without #4767 | waits: F1, multus | waits: F1, multus | waits: F1, multus |
| 05:34Z and 05:36:30Z: multus rolling | waits: F3, multus | waits: F3, multus | waits: F2 (DependencyNotReady on multus) and F3 |
| 05:40Z: counterfactual, nothing else merged | may merge | may merge | may merge |
| hypothetical: only #4767 not fetched | may merge | waits: F1, #4767 | may merge |

In the real night #4767 was never the only commit Flux had not fetched, which is why that
last row is hypothetical.

**For the verifier (stories 553 and 753).** `otherChanges(range, scope, rules, { exclude
})` is a pure function over `readCommitRange(repo, base, head, run)`. It returns the other
changes on main to T's paths, its chain or a cluster-wide part: SHA, time, subject, PR,
author, committer and which paths matched which part. On the fixture: after #4760 and
#4761, #4767 touches the ytdl-sub target only. Dry-run records nothing new with it.

### The shepherd stands aside

`run-shepherd.sh` decides in the launcher, not in a prompt. `shepherd_may_merge` reads the
lock (`kubectl -n upgrade-agent get configmap upgrade-change-lock`) and nothing else: no
window and no time of day.

| Lock | Shepherd |
|---|---|
| absent, free (empty holder), expired | merges |
| held in phase `taken` (a tick's bookkeeping, about 25 seconds even in dry-run) | merges, and logs that it read it |
| held in phase `merging`, `merged-awaiting-verification` or `merge-unknown` (a lane change in progress) | stands aside |
| held in an unknown or missing phase, with an expiry its jq cannot parse (it reads `…Z` only, not offsets), or unreadable | stands aside (fail closed) |

The lane's own `lockStatus()` and the hand runs are unchanged: they refuse any held lock.
`scripts/upgrade-agent/fixtures/change-lock-states.json` holds 20 lock states, and both
readers are tested on them (review finding R7).

- **Before the LLM turn** (MODE `auto` and `remediate`, the modes with `gh pr merge`):
  on a stand-aside, `Bash(gh pr merge:*)` leaves `--allowedTools`. The operating rules
  gain a `MERGE STAND-ASIDE` rule: do everything else, and end with `DEFER: #<N> waits
  for the night lane to finish its change …` and no vet marker.
- **The stall fallback** asks again just before its own `gh pr merge`. A stand-aside
  keeps the PR in the watch, and the next poll asks again: a lock read that failed once
  merges on the next poll. Only a stand-aside that still holds at the watch's deadline
  escalates, on the signature `shepherd-automerge-stood-aside|pr=<n>|sha=<sha>`. It
  escalates because the PR's vet marker keeps later runs from retrying it (review findings
  R4 and S3).
- **Unchanged otherwise.** When the answer is yes, `ALLOWED` and `SAFETY_PROMPT` are
  byte-identical to before (pinned by `shepherd-stand-aside-selftest.sh`).

What it does not cover:

- **Queued auto-merges.** A GitHub auto-merge the shepherd queued before the lane's change
  can still fire later, whenever its checks pass, and F4 does not cover it: no Job is
  running by then. That is the same case as Renovate's merges, because no lock binds
  GitHub. The verifier's `otherChanges` reports it as another change.
- **The REST merge endpoint.** The LLM keeps `gh api repos/*` for release-note reads, and
  that pattern also admits GitHub's REST merge endpoint. Only the rule forbids it. This
  predates story 551; a follow-up story will add a GET-only wrapper.
- **Remediate during a lane change.** A remediate run while the lock shows a lane change
  in progress opens its fix PR but does not merge it, and triage reads the run as pending.
  After a live lane merge that lasts for the 4-hour `merged-awaiting-verification` hold,
  exactly when a regression from that merge is likeliest. This is deliberate for now: the
  hold is the lane's verification window, and reverting the lane's own merge is the lane's
  job (`owedWork()`, stories 553, 753 and 754). **Decide before live mode (story 556)**
  whether a remediate revert of the lock's own merged PR may pass.

### One owner for a PR on the shepherd's ramp

**Rule: the shepherd owns every Renovate PR that touches a path on its ramp, and the lane
never merges one.** The path class `shepherd-ramp` in `upgrade-rules.json` makes such a
PR `EXCLUDED`. #4780 (traefik) was both LANE-A and on the ramp on 2026-11-10.

- Why the shepherd: its pre-filter already summons it for any open Renovate PR on these
  paths, its playbooks cover those components (the rook set, Kometa idle before plex),
  and this keeps the shepherd's decision path unchanged.
- The cost: a patch of a ramp component (for example cilium) no longer goes through the
  lane, and the shepherd's 4-hourly cadence is its pace.
- Drift: `lane-classify-selftest.mjs` rebuilds the class from `UPGRADE_AGENT_RAMP` in the
  helmrelease and `ramp_globs_for` in `run-shepherd.sh`, and fails when they differ.
- A ramp PR's other findings stay in `records[].findings` (for example the cilium minor's
  one-way rule).

### Hand runs

- A hand run refuses while the lock is held or unreadable, in any phase: `handRun()` adds
  `lockStatus().why` for `held` and `unreadable` before anything else.
- It cannot take the lock (no ConfigMap write in `upgrade-agent` for a session), so the
  lane and the shepherd cannot see it.
- It runs the settle gate and F4 at its start and before a `--merge` call.

### What shows it

| Where | What |
|---|---|
| Tick log | `upgrade-night <mode>: settle gate: Flux at <sha8>, <n> commit(s) on main not fetched yet; #<pr> settled; #<pr> NOT settled (<blockers>)`, and `upgrade-night <mode>: F4 shepherd Jobs: none that can merge is running` or `… a Job that can merge is running: <job> (…)` |
| Tick stdout | `settle gate: #<pr> settled, #<pr> NOT settled (<n> blockers)` |
| `summary.settle` | `source_revision`, `unapplied_complete`, `unapplied_why`, `unapplied_commits` (`sha`, `pr`, `subject`) |
| `records[].gates.settle` | `applicable`, `settled`, `blockers` (`F1 …`, `F2 …`, `F3 …`), `unapplied` (with the parts each reaches), `unsettled`, `cluster_wide_unsettled`, `failing_not_settling` |
| `summary.merge_step_settle`, `summary.merge_step_shepherd_jobs` | the merge step's own reads |
| `summary.merge_step.lane_refusals` | `settle gate: …`, `F4 …`; in live mode also `settle gate at the merge call: …` and `F4 at the merge call: …; yielded-to-shepherd` |
| `records[].class` | `shepherd-ramp` for a ramp PR |
| Shepherd log | `stand-aside: not needed: the change lock shows no lane change in progress.`, `stand-aside: not needed: (read the change lock: held by … in phase taken …)`, or `STAND-ASIDE: this run merges nothing: <why> (story 551).`; from the fallback `automerge-fallback: STAND-ASIDE: #<n> …` and an `esc-shepherd` entry |
| Hand run | `settle gate: settled …`, `NOT settled …` or `not applicable …`, and `refused: settle gate: …` or `refused: F4: …` |

```bash
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.records}' | jq '.[] | select(.lane=="LANE-A") | {pr, settle: .gates.settle | {applicable, settled, blockers}}'
kubectl -n upgrade-agent logs job/<latest upgrade-shepherd job> | grep -i 'stand-aside'
```

### Known limits of story 551

- **Workload rollouts.** Flux can report Ready before T's workloads have finished rolling,
  when a Kustomization does not wait and a HelmRelease's chart does not either. The lane's
  ServiceAccount (ClusterRole `upgrade-health-gate`) reads pods, but nothing in `apps`, so
  the gate cannot compare a Deployment's, StatefulSet's or DaemonSet's generation and
  updated replicas. The missing rule: `apiGroups: ["apps"]`, `resources: ["deployments",
  "statefulsets", "daemonsets"]`, `verbs: ["get", "list"]` (story 755). No RBAC is added
  here.
- **Late merges.** A merge that lands after the lane's merge call is invisible to the
  gate. The verifier (stories 553 and 753) finds it with `otherChanges`.
- **Broken dependencies.** A Kustomization of T's chain that waits on a broken dependency
  is failing, not settling (R3). The baseline gate holds T for it if it is in scope; the
  settle gate does not hold other targets.
- **Unknown Jobs.** An unknown unfinished Job in `upgrade-agent` holds the lane (F4, fail
  closed) until it finishes.
- **A stale remediation claim (S5, not fixed).** F4 counts any claim in
  `upgrade-remediation-state` with `attempted` 1 and `result` none, not only the active
  triage Job's.
  - A crashed remediate leaves such a claim behind. While its regression persists, every
    triage run (:00 and :30) then holds the lane's ticks that overlap it, for about 17
    seconds each.
  - The claim carries no time or Job identity that ties it to a run: `triage.sh` writes
    `class`, `attempted`, `result`, `note`, and `first_seen`, which is when the signature
    was first seen. So the lane cannot tell a stale claim from a live one. It stays fail
    closed, and the hold is bounded.
  - What clears it: triage recording a result, a person editing the entry, or the entry
    aging out 24 hours after its `first_seen` (`STATE_TTL_HOURS`) on the next state write.
- **GitHub auto-merges are not covered by F4.** A merge queued earlier through GitHub
  auto-merge, by the shepherd or Renovate, lands whenever its checks pass, Job or no Job.
- **SIGKILL in the merge window.** A SIGKILL between the `merging` write and the merge
  call leaves the `merging` phase for its 4-hour lease, with no merge made. The shepherd
  stands aside for that time (S4).

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
  - Step 2 only records the value. Using it, for example to carry a vet decline across a
    rebase, needs a single Renovate commit and an equal fingerprint, and comes in a later
    step. Piece 1 reads no approval (saga 547, comment 6922), so it never carries one.
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

## PR records and the stalled-check retry (story 549)

Story 549 (<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/549>)
is OP512 rollout step 3. The lane stays in dry-run: none of this is a merge. The code is
in `night-merge.mjs` (section "PR records and the stalled-check retry") and the tick's
PR-write phase in `run-night.mjs`. The settings are `night.pr_record` and `night.retry` in
`upgrade-rules.json`.

### The record on each PR

- **One comment per open Renovate PR**, written by the lane's own identity and edited in
  place. Its body starts with the line `<!-- upgrade-lane v1 -->`, then one line of plain
  text, then a fenced JSON block with schema `haynes.upgrade-lane/v1`. The JSON holds the
  tick's record from `upgrade-lane-state`, cut down to `night.pr_record.fields`: `schema`, `pr`, `head`, `fingerprint`, `lane`,
  `class`, `reason`, `rules`, `checks`, `backup`, `preapproval` (always `{}`), `schedule`,
  `last_attempt`, `retry`, `terminal` (story 593: see below) and `updated`. It leaves out `gates`, `findings`, `files` and
  everything else in the state.
- **The lane's identity** is the haynes-ops-bot GitHub App: REST `user.login`
  `haynes-ops-bot[bot]`, `user.type` `Bot`. The night CronJob mints its token from the same
  app as the shepherd (`upgrade-shepherd-bot-secret`).
- **Only the lane's own comment is the record.** A comment from any other author is never
  read as the record and never edited, even when its body starts with the marker. The tick
  lists such comments in `summary.pr_records.foreign_ignored`. Before it edits its own
  comment, the lane reads that comment again by id and checks the author and the marker.
- **Safe to publish.** The repository is public. Inside the JSON, `<`, `>`, `&`, `@` and
  the backtick are written as `\u` escapes, so the block cannot contain an HTML comment,
  a mention (which would also start `claude.yml`) or a code fence. A record with text
  shaped like a credential (a GitHub, Slack or AWS token, a private key, a JWT, a URL with
  a password, a bearer value or a `password=` assignment), a non-empty `preapproval`, or
  more than 60,000 bytes is not written. The tick lists it under `refused` with the
  reason. On 2026-11-12 the 14 records in `upgrade-lane-state` passed this check, and the
  largest was 2.2 KB.
- **No shepherd-vet marker and no `VERDICT:` line.** The record therefore does not change
  labels (the labeler reads only comments that start with a shepherd-vet marker). It also
  does not count as a vet in the shepherd's pre-filter or in the classifier.
- **The lane writes only when the content changed.** The comparison leaves out `updated`
  and `last_attempt.at`, which change on every tick. On the first night the lane creates
  one comment per open PR: 14 on 2026-11-12, plus one retry. After that, an unchanged tick
  makes no GitHub write, and a PR whose record changed (lane, class, checks, retry state,
  the merge-step verdict) costs one edit.
- **One identity, shared with the shepherd.** The lane and the shepherd write as the same
  bot. A record is never an input to a decision: the lane reads its own record only to decide
  whether to write it again. So a record the shepherd's LLM wrote could only be overwritten or
  demoted by the lane.
- **Live mode (story 556).** The advisory review of PR 4856 noted that in live mode some text
  in the merge step's refusals depends on the clock (lease seconds left, `now HH:MM`, the
  gates' age). The comparison leaves out only `last_attempt.at`, so the merge step's subject
  could be edited on every tick, about 20 a night. Dry-run does not write that text. Story 556
  should leave those values out of the comparison when it turns on the first live class.
- **Never two records.** The lane lists the PR's comments again just before it creates one.
  If it ever finds two or more of its own records, it keeps the oldest and edits each newer
  one into a one-line note without the marker that links to the record. It does not delete
  comments.
- **A failed write.** The tick reports a refused or failed write in `summary.pr_records.failed`.
  The reason comes from a fixed vocabulary: `refused by GitHub (HTTP 401|403)`, `rate limited
  by GitHub (HTTP 403|429)`, `not found (HTTP 404)`, `rejected by GitHub as invalid`, `GitHub
  server error (HTTP 5xx)`, `timed out or killed`, `network or client error (no HTTP
  status)`. The record, the ledger and the state never carry gh's or GitHub's own text, because
  a network error's last line can name the cluster's resolver. That text goes to the Job log
  only, in the lines `record write failed: …` and `retry write on #<n> failed: …`. The PR is
  not listed as current, and the tick makes no further GitHub write: the remaining PRs go to
  `not_attempted`.
- **A denial is a stop.** A 401, or a 403 that is not a rate limit, sets the durable `halt`
  in the ledger, as an identity mismatch does. The lane then makes no PR write until a person
  removes the halt (see [Kill switch](#kill-switch)). A rate limit, a 5xx, a timeout or a
  network error stops only that tick's writes, and the next tick tries again by the same
  route. There is no other route.
- **The pause before each write.** The PR-write phase reads `data.pause` before it starts,
  the retry reads it again before its write, and the record writer reads it just before each
  create, edit or demotion. A tick that finds nothing to write reads it once.
- **Identity check.** GitHub's reply to a create names the comment's author. If the author
  is not the configured identity, every later tick would add another comment the lane does
  not recognise as its own. So the tick sets `halt` in the ledger and makes no PR write
  until a person removes it (see [Kill switch](#kill-switch)).

### Vet markers count only from our bot

The shepherd's pre-filter (`prefilter_should_run` in `run-shepherd.sh`) skips the LLM for a
ramp PR whose current head already carries `<!-- shepherd-vet sha=<head> -->`. Before story
549 the pre-filter read comments through `gh pr view --json comments`, which is GraphQL,
and counted a marker from any author. Two facts about how the bot's identity is known:

- GraphQL reports the bot's comments as `haynes-ops-bot`, without the `[bot]` suffix.
  GitHub answers 404 for a user with that name, so anyone could register it.
- REST reports `haynes-ops-bot[bot]`, type `Bot`, id 298460192, `performed_via_github_app`
  `haynes-ops-bot`. A person cannot register a login with the `[bot]` suffix.

So the pre-filter now reads the comments over REST
(`gh api --paginate repos/<repo>/issues/<pr>/comments`). A marker counts only when the
comment's author is `${SHEPHERD_BOT_LOGIN}[bot]` with type `Bot` and the body starts with
the marker. All 64 vet markers on record (read 2026-11-12) meet that, so a genuine marker
behaves as before. The only other marker on record (PR 4827, by `github-actions[bot]`) is a Flux diff that quotes
one. If the comments cannot be read, no marker counts and the LLM runs. The classifier
already required `haynes-ops-bot[bot]` over REST (`trusted_vet_authors`).

### The retry of a stalled check

Renovate's PR body has one line ` - [ ] <!-- rebase-check -->If you want to rebase/retry
this PR, check this box`. When the box is ticked, Renovate rebases the branch, which gives
a new head and new checks. On 2026-11-09 Renovate acted within about 5 minutes, outside its
schedule (#4711, #4712).

**Which PR.** A PR whose record reads lane `BLOCKED`, class `check-failing`: a required or
expected check failed, and nothing else keeps it out of lane A. Lane B and excluded PRs are
never retried.

**The facts, read at the moment of the write** (Tom's ruling Q-548-04, facts not clocks):

- the newest run of the failing check on that head failed, and no check run on the head is
  queued or running and no commit status is pending;
- no workflow run for the head is queued, waiting, pending, requested or in progress. Such a
  run (a re-run waiting in a concurrency group, for example) has no check run yet, so the
  lane reads the head's check suites:
  - `gh api repos/<repo>/commits/<head>/check-suites`, which needs `checks:read`, a
    permission the token holds;
  - any `github-actions` suite that is not `completed`, or a list that cannot be read, stops
    the retry.

  The workflow-runs endpoint (`actions/runs?head_sha=`) would need `actions:read`, which the
  night token does not hold. Read live on 2026-11-12, head `6bd661d7` had 4 queued workflow
  runs, 4 queued `github-actions` suites and no check run. The suites of other apps
  (`claude`, `cursor`, `renovate`, `cloudflare-workers-and-pages`) stay `queued` on every
  head, so they are not judged;
- the box is in the body exactly once and is unticked;
- the PR is open, not a draft, based on main, opened by `renovate[bot]` (Bot, id 29139614)
  from a `renovate/` branch of this repository, and every commit is the Renovate app's
  (author `renovate[bot]`, committed by `web-flow`, signed);
- the head is the one the tick judged.

The story's first text said "failed at least 30 minutes ago". That clock is not used: the
facts above decide.

**Limits** (`night.retry`): once per head; at most 2 per PR in 7 days; at most 1 box write
per tick. The per-tick cap is a budget, not a wait, and it counts writes, not PRs tried:

- Eligible PRs are tried in PR order. If the facts at the write refuse one (a check stuck
  `QUEUED`, say), it reads `not-written` with the reason and the next eligible PR is tried.
- At most `max_preflights_per_tick` (3) PRs are read at the write per tick, so a long queue
  cannot spend the tick. The rest read `deferred`.

One retry per tick bounds how many happen at once, not how many in a night: up to about 20
a night in theory, one per tick. The per-head and per-PR limits bound the total.

**The write.** The lane checks the pause, reads the PR (REST, `gh pr view`, the REST
commits and the head's check suites) and checks the facts. It writes the ledger entry
(`intent`) to `upgrade-lane-state`. Then it reads the
pause and the PR again. If the tick is now paused, the head moved or the body changed, it
writes nothing this tick and the entry becomes `abandoned`, which does not count. Otherwise
it sends the body it read with the box's one character changed (`ghWrite` refuses any other
body edit), and the entry becomes `ticked`, or `write-failed` with the reason. Both count,
because GitHub may have applied a write that reported a failure. GitHub has no conditional
update for a PR body, so a Renovate edit in the milliseconds between the second read and
the write would be overwritten. Renovate rewrites its body on its next run.

**After the retry**, each tick reads the PR again:

| `retry.state` | When | Effect |
|---|---|---|
| `waiting-for-renovate` | the head has not moved and the box still reads ticked | none |
| `new-head-checks-running` | a new head, its checks not all finished | none |
| `resolved` | a new head whose expected checks passed | none; a later failure on a later head may get the second retry |
| `failed-again` | a new head with a failing check | the PR is blocked: class `check-failing-after-retry`, reason `failed again on <head> after one retry on <head> at <time>: <checks>` |
| `no-new-head` | Renovate cleared the box and pushed nothing, and the check still fails | blocked the same way |
| `budget-used` | 2 retries in the last 7 days | none; the failing check keeps it blocked |

**An interrupted write.** A Job killed between the `intent` write and the next ledger write
leaves the entry at `intent`. The next tick decides it from facts:

- the box ticked, or the head moved since the intent: the write was made. The entry becomes
  `ticked` and counts for the per-head limit, the 7-day budget and the second-failure rule;
- the box unticked at the same head: it was not made. The entry becomes `abandoned`, and the
  PR may be retried;
- anything else (the box absent, the same head): it stays `intent`, which counts (fail
  closed).

One case only a wait could decide, so the lane does not decide it: a ticked box that
Renovate never acts on reads `waiting-for-renovate` for as long as that lasts. No second
tick is made on that head, and the failing check keeps the PR blocked. A person reads the
state in the record.

**The ledger** is `data.retry_ledger` on `upgrade-lane-state`, written by its own field
manager `upgrade-night-ledger`. The tick's server-side apply under `upgrade-night`, and the
error record on a failed tick, therefore never remove it. It keeps entries for open PRs for
30 days. An unreadable ledger allows no retry and no PR write.

- **Proved writable first.** Before any GitHub write the tick writes the ledger once. If that
  write fails, the tick makes no GitHub write: a halt it might need to record would be lost,
  and without one a mismatched identity would add a comment on every tick.
- **Written at a version (F8).** Every ledger write carries the `resourceVersion` of the
  tick's last read or write of the ConfigMap as a precondition. If another Job (a hand-made
  Job overlapping a tick, say) wrote the state after this tick read it, this tick's first
  ledger write fails with a Conflict. It then makes no retry and no record write. A person
  changing `data.pause` mid-tick has the same effect.
- **Not proved against the API server.** The selftests check the precondition against a stub
  that enforces it. That the API server enforces `metadata.resourceVersion` on a server-side
  apply is taken from Kubernetes' behavior; it was not run against the cluster from a
  session, which cannot write ConfigMaps in `upgrade-agent`. If the server ignored it, an
  overlap would fall back to the last writer winning, and the 7-day budget could undercount
  by one.

### When the lane writes to GitHub

The PR-write phase runs after the merge step, and only when all of these hold:

- the tick runs with `--write-state`. The CronJob does. A local run writes nothing: it reads
  the facts at the write for the eligible PRs and logs `would create`, `would update` and
  `WOULD-TICK`;
- the token's recorded permissions include `pull_requests:write`;
- the ledger is readable, has no `halt`, and its proving write succeeds at the version the
  tick read;
- the pause, read again just before the phase, is not set. It is read again before each write.

GitHub's permission tables for installation tokens list all three endpoints the lane calls
under "Pull requests", write: `POST /repos/{owner}/{repo}/issues/{n}/comments`,
`PATCH /repos/{owner}/{repo}/issues/comments/{id}` and
`PATCH /repos/{owner}/{repo}/pulls/{n}`. The night token has `pull_requests:write` (story
752, ruling Q-548-02). The first night is the real proof.

### Reading it

Log lines of each tick (stderr):

```text
upgrade-night dryrun: PR records: 15 written (created #4349, ...; updated none; demoted none), 0 current
upgrade-night dryrun: retry: #4757 162b35d8 TICKED; eligible #4757, #4848; blocked after a retry none; waiting for Renovate none; ledger read
upgrade-night dryrun: GitHub writes this tick: 15 (no merge in dry-run)
```

```bash
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.summary}' \
  | jq '{github_writes, pr_records, retry: (.retry | del(.by_state))}'
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.records}' \
  | jq '.[] | {pr, class, retry: (.retry | {state, why, count_7d})}'
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.retry_ledger}' | jq .
```

`summary.pr_records` lists the PRs whose record was `created`, `updated` or already
`current` this tick. A PR in any of the three carries a record that matches this tick's
record, except for `updated` and `last_attempt.at`. The other lists are `refused`, `failed`,
`not_attempted`, `demoted` and `foreign_ignored`, and `why_not` says why nothing was written.
`summary.retry` holds `eligible`, `selected`, `attempts` (outcome and reason for each) and
`by_state`.

## The history of every attempt (story 593)

Story 593 (<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/593>)
adds the history piece 1 remembers: ConfigMap `upgrade-agent/upgrade-ledger`, and beside it
`upgrade-agent/upgrade-ledger-marker`. Both are runtime state, like `upgrade-lane-state`, and
Flux does not manage them. The code is `upgrade-history.mjs`, and the settings are
`night.history` in `upgrade-rules.json`. The records follow the format in
`.agents/reference/upgrade-records.md`.

**What this does, and what it does not.** The history *remembers*: every attempt, and the
item states that outlive it, such as `reverted` (never merge this version again). Nothing
reads it before a merge yet. Refusing a remembered version before a merge is the work of
stories 753 and 556.

### What a tick writes

The phase runs once per tick, after the merge step, when the change lock is already
released. `run-night.mjs` loads the module inside the guarded step, so a module that does
not load is an ERROR line and the rest of the tick runs. Each event is a
`haynes.upgrade-event/v1` record, built and checked by `record-format.mjs`. A tick writes
each of these events once:

| Event | When |
|---|---|
| `observed` | An open Renovate PR's item is seen for the first time, or its state (`needed`, `held`, `blocked`), lane or class changed. |
| `closed` | The PR closed without a merge, by whoever GitHub says closed it: the shepherd as `shepherd`, a bot the lane does not know as `unknown` with its login. Also when Renovate replaced the item with a newer version on the same PR, whether the lane saw the newer version open or only at the merge (actor `renovate`). |
| `merged` | Someone other than the lane merged it: Renovate's auto-merge, the shepherd, a session, a person, or `unknown` (another bot, its login kept). It goes on the item that was merged. A merge of a version the history remembers as `reverted` is recorded too (`merged`, from `reverted`; the lane is never an actor on that edge). When Renovate moved the PR to a newer version before it merged, the old item closes and the merge goes on the new one. A PR the lane never saw open is recorded with `recorded_by: backfill`. |
| `revert-merged` | A merged PR titled `Revert "<title>"` whose body names (`Reverts #N`) a PR of exactly that title that the history holds as merged, merged by a session or a person. |

In live mode the merge step's verdict also becomes events, through `eventsFromMergeStep()`.
A refusal at the same head for the same reasons as the item's last refusal is recorded
only once.

**Nothing is written for:**

- an unchanged item, or a rebase (a new head is the same item);
- a record whose facts were not read (`facts-incomplete`), which names no item and does not
  move an item to another PR (see below);
- a fact the tick could not read, such as who closed a PR, who merged it, or its merge
  commit. The next tick reads it again; the lane is never recorded as the closer.

**Two PRs, one item.** When two open PRs carry the same item (the same package and
version), the lowest PR speaks for it. A transient `facts-incomplete` on that PR does not
hand the item to the other one.

**Every item is named by the lane's own route.** That is the PR's package table, labels,
title and files, read the way `gatherFacts` and `classify` read them, through
`fromLaneRecord()`. A backfilled item is therefore the same item the lane computes for the
same update while it is open.
- When a PR's body cannot be read, or gives no package table (edited to empty, say), the
  item is named from its title instead and marked `basis: "title"` in `data.items`.
  `summary.history.total.title_items` counts these items. A title-named item is never
  taken as a move to a newer version.
- On the 2026-11-09 queue, all 30 real PRs get the same id by both routes
  (`lane-history-selftest.mjs`).

### The ConfigMaps

| Object and key | Holds |
|---|---|
| `upgrade-ledger` `data.events` | One event per line, oldest first. Each line is the format's event without the constant `schema` key, without `id` (that is `eventId()` over item, type, time and attempt) and without a null `actor.login`. `unpack()` restores all three. |
| `upgrade-ledger` `data.items` | For each item, what the events cannot say: `name`, `from`, `to`, `type` (update type), `pr` and, for an item named from a title, `basis`. Also `base`, the item's state when its earlier events rotated out, and `mirrored`, the terminal event its PR record now carries. |
| `upgrade-ledger` `data.meta` | `schema` `haynes.upgrade-ledger/v1`; the backfill marker (`backfill.at`, `window`, `query`, `events`, `compare`); the last rotation; `mirror_halt`; `catch_up_mismatch` (a catch-up disagreement waiting for the next tick); and `tried` (`fate`, `mirror`: when each PR still waiting was last tried, for the fair order). |
| `upgrade-ledger-marker` `data.marker` | `schema` `haynes.upgrade-ledger-marker/v1`, `created_at` and `backfilled_at`: that a history exists. |

**Rotation.** `night.history.max_bytes` caps the bytes of `upgrade-ledger`'s data, which is
what the API server limits to 1 MiB. It is set to 768 KiB, the same budget the lane state
keeps. When the cap is reached, the oldest events go first, and an item whose events
rotated out keeps its state in `items[id].base`:

- `reverted` stays `reverted`;
- an open item keeps its place, and so does an item whose attempt still owes work. That
  includes a `merged_unverified` the lane merged, whose verification is owed (story 553);
- an item that is `closed` or `done`, or `merged_unverified` after someone else merged it,
  is forgotten once no event of it is left.

**A merge of a remembered version.** If GitHub shows a merge of a version the history
holds as `reverted`, it is recorded as an event (`merged`, from `reverted`, by whoever
merged it; the lane is never an actor on that edge), and the tick that sees it logs
`ERROR upgrade-night <mode>: HISTORY NEEDS A PERSON: … MERGED WHILE REMEMBERED: #<pr>
(reverted)`. An item that was ever reverted is never forgotten by rotation.

**Where an update lands is part of the item.** The format's item id has the place in it:
the sorted paths the update changes, a fact of the pull request alone. It is not the lane's
targets: a tuning of the lane's rules would otherwise re-key open items. Two PRs for the
same package and version in different files are two items. When a PR's files change while
it is open (Renovate rebased PR 4873 from two traefik apps to one after the other half
merged as PR 4876), the old item closes as replaced and the new one is observed.

**A file list GitHub cut off.** GitHub lists at most 100 files for a pull request (the
`files` field gh reads). The lane's `gatherFacts` already ends a shorter list than
`changedFiles` with `<file list truncated>`. The history's own reads ask for `changedFiles`
and add the same marker, so the lane and the backfill build the same id from the same cut
list. Such an item is marked `files_cut: true` in `data.items` and counted in
`summary.history.total.cut_file_items` and the log line. On 2026-11-13 none of the 301
Renovate PRs merged in 30 days, and none of the 17 open ones, had more than 100 files (the
largest had 14).
`rememberedReverted(historyItems(doc), name, version)` returns every item for that package
and version, whatever its files, that a revert touches or touched. Each comes with `why`:
`failed`, `reverting`, `reverted_unverified` (where every revert the history records today
stays), `reverted`, or `ever-reverted` (merged again after its revert). Nothing decides a
merge with it yet (stories 753 and 556).

**Free text is guarded and bounded before it is stored.** Every free-text field of an event
(`reason`, `merge_call`, `revert_branch`, `flux_revision`, `url`, the refusal lines,
`evidence.checks`, `actor.login`) and each item's name passes the PR record's secret shapes
(`secretShape()` in `night-merge.mjs`). A hit is replaced by a fixed placeholder, and the
tick logs `REDACTED <field> of <item> (shaped like …)` at ERROR level: the field, never the
text. The item's own package versions are exempt from the private-address shapes only
(PR 4757's `==10.3.2.106` reads like 10.x.x.x); the PR record uses the same exemption. Then
`fit()` cuts the event to the format's bounds with a visible mark (`…`, `… (+N more)`). A
patterned field is never cut: validation refuses it instead.

**Size** (measured in `lane-history-selftest.mjs`):

- A stored line is about 440 bytes: 489 for an `observed` with a 160-character reason, 438
  for a `merged`. An item's description adds about 130 bytes more.
- With 5,000 merges of distinct items, the cap holds 1,370 of them (784,944 of 786,432
  bytes).
- The rate on 2026-11-12: 303 Renovate merges and 61 closures in 30 days, about 12 items a
  day at one or two events each, plus the state changes of the 14 open PRs (not measured
  yet). At about 20 events a day the cap holds about 75 days; at 17 a day, about 90.

### The writes

- **The history.** A server-side apply under field manager `upgrade-night-history`, always
  with the `metadata.resourceVersion` the tick read. The API server answers 409 Conflict
  when another writer changed the object since:
  - `Store.Update` in kubernetes `staging/src/k8s.io/apiserver/pkg/registry/generic/registry/store.go`
    compares the applied object's resourceVersion with the stored one.
  - The apply patcher (`endpoints/handlers/patch.go`, `applyPatcher`) merges the applied
    configuration, resourceVersion included, over the live object first.
- **The first write.** When the object is missing, the tick creates it empty under
  `upgrade-night-history-init`. A create through apply wipes the resourceVersion, so a
  version on it guards nothing, and an apply also succeeds on an object that already
  exists. So the tick reads what the create returned: if it already holds events, another
  first writer got there first, and this tick writes nothing (a conflict). Otherwise the
  tick writes at the version the create returned.
- **Reading them.** The tick reads the marker first, then the history. A first writer
  writes the history and then the marker, so this order cannot read a marker without its
  history.
- **The marker.** After the history it vouches for, the tick writes the marker under
  `upgrade-night-history-marker`: once at the first write, and again when the backfill
  completes. It is its own ConfigMap, so it never moves the resourceVersion of
  `upgrade-lane-state`, which story 549's retry ledger writes depend on.
- **A conflict or a failure** writes nothing. The log line reads
  `ERROR upgrade-night <mode>: HISTORY NOT WRITTEN: …`, and kubectl's own text goes to the
  Job log only. `summary.history.write` says `conflict` or `failed`. The next tick derives
  the same events again; event ids are deterministic, so a duplicate line is dropped on
  read.
- **Stuck.** A history the tick cannot read (a line that is not a valid event), a history
  that does not replay, an invalid `night.history`, or a history over the cap with
  remembered items alone, is never overwritten. Each one reads
  `HISTORY NOT WRITTEN: STUCK: …` at ERROR level on every tick, with `summary.history.write`
  `stuck`.
- **No effect on the rest of the tick.** None of this changes a merge verdict or the lock,
  and the state write still happens.

### The history was lost

If `upgrade-ledger-marker` says a history exists but `upgrade-ledger` is absent or has no
data (deleted, or emptied by hand), the tick writes nothing and logs on every tick:

```text
ERROR upgrade-night dryrun: HISTORY NOT WRITTEN: STUCK: …; the history was lost: upgrade-ledger-marker says a history exists (created …, backfilled …), but upgrade-ledger is absent. Nothing is written until a person restores upgrade-ledger or deletes upgrade-ledger-marker …
```

`summary.history.lost` carries the marker. A person does one of two things:

- **Restore the history** from a copy, for example a saved `kubectl get cm upgrade-ledger
  -o yaml` with `resourceVersion`, `uid` and `managedFields` removed, applied under the
  history's own field manager:
  `kubectl apply --server-side --field-manager=upgrade-night-history -f <file>`.
  A session cannot write ConfigMaps in `upgrade-agent`, so this is the owner's to do.
- **Accept the loss and start again:** `kubectl -n upgrade-agent delete cm
  upgrade-ledger-marker`. The next tick starts a new history and backfills 30 days again.
  The remembered `reverted` items are gone for good.

### The backfill and the catch-up

- **What is read.** With no backfill marker in `meta`, the tick lists the merged pull
  requests of the last `backfill_days` (30) that become items: Renovate's (`gh pr list
  --state merged --search "author:app/renovate merged:<since>..<until>"`, with body, labels
  and files). It also lists the revert candidates (`"Revert in:title merged:…"`, any author).
- **Split lists.** A list that reaches GitHub's search limit (`max_search_results`, 1,000)
  is split in halves, at most 6 times. A list still at the limit is refused, never taken as
  complete. On 2026-11-12 the 30 days held 303 Renovate PRs; all authors together held 845,
  and the 1,000 limit would have cut a busier month.
- **What is recorded.** Each merged Renovate PR becomes an item in `merged_unverified`,
  with its `merged` event (actor, time, merge commit, labels in the reason), shaped like
  the format's fixture `seq-backfill-renovate-automerge.json`. Only Renovate's PRs become
  items; the others are not update items (accepted, comment 7383 on story 593).
- **The count comparison** goes to `meta.backfill.compare` and to
  `summary.history.backfill`. It compares two numbers: `github_count`, GitHub's own search
  `total_count` for the same query and window, read separately from the list, against
  `history_count`, the Renovate PRs of that window that the history holds. It also gives
  `listed`, `recorded`, `shared_item`, `missing`, `reverts` and `match`.
  - `match` is true only when GitHub's count equals both the list and what the history
    holds, and none is missing.
  - It is null, never true, when GitHub's count could not be read. The comparison is then
    still owed: each later tick reads GitHub's count for the backfill's window again (one
    search, the backfill is not re-run) until a count has been read. A mismatch is an
    ERROR line on every tick until a later count matches. To re-run the backfill instead,
    remove `meta.backfill` (the events are kept, and their ids make it idempotent).
  - `shared_item` is a PR whose package and version another PR in the window already
    carried: the format holds one item per package and version.
- **Read-only run on 2026-11-12 17:33Z** (`run-night.mjs` without `--write-state`): GitHub
  counted 303 merged Renovate PRs in the 30 days, 303 were listed, and the history would
  hold 303 (298 on their own item, 5 through another PR's item), with none missing. Its
  first tick would write 311 events (13 observed, 298 merged) in 182,804 bytes, with no item
  named from a title. The history step took 16.6 seconds.
- **The catch-up.** Each later tick reads the merges since the newest one the history
  holds, less `catch_up_margin_hours` (24, for GitHub's search index). It records the PRs
  it does not hold yet, and compares that window the same way in
  `summary.history.catch_up`. The list and the count are read seconds apart for a window
  that ends at the tick's start, so GitHub's search index can put a merge just before the
  tick in one and not the other. A first disagreement is therefore an info line and a
  summary field (`meta.catch_up_mismatch`). It becomes an ERROR when the next tick
  disagrees again (`catch_up.repeated`). No wait is added.
- **The budget.** Per tick, at most `max_pr_reads_per_tick` (30) per-PR reads: a PR's fate,
  who closed it, a revert's body, a reverted PR's title, a record to mirror. No read starts
  once the tick has run `time_budget_seconds` (300; the Job's deadline is 600). What is left
  carries over to the next tick, and the backfill marker waits until nothing is left.
- **The fair order.** A PR that stays unread is tried again on later ticks in the order of
  when it was last tried, the least recent first (`meta.tried.fate`), so one stuck PR
  cannot hold a read on every tick. The terminal mirrors use the same order
  (`meta.tried.mirror`).

### Terminal events on the PR record

- **The field.** When a PR leaves the open list with a terminal event (`merged`, `closed`),
  the lane's own record on that PR gains `terminal: { type, at, state, by, item, event,
  merge_commit }`. `terminal` is one of `night.pr_record.fields`, goes through the publish
  check, and is mapped in `lane-v1-to-item-v1.json`.
- **Owed on facts, not on a clock.** A terminal recorded by the lane stays owed until the
  history marks it mirrored (`items[id].mirrored`): its record carries it, or the PR has no
  lane record. Backfilled merges are never owed, because the lane did not watch those PRs.
- **When the mirrors are written.** After the open-PR records, at most
  `max_mirrors_per_tick` (10) per tick, in the fair order, and only when the PR-write phase
  can write: no comment is read otherwise. A record that is refused or cannot be parsed
  stays owed and takes its turn, so it cannot starve the others. The edit happens only when the content changes, and a PR with
  no lane record gets no new comment (`summary.history.mirrors.no_record`).
- **Their own stop.** A failure on a mirror stops only the mirrors. A denial halts only the
  mirrors: it is kept in `meta.mirror_halt`, whose text names that key, and logged as
  `ERROR … TERMINAL MIRRORS HALTED`. The open-PR records go on. To clear it, after fixing the cause:

  ```bash
  kubectl -n upgrade-agent get cm upgrade-ledger -o json \
    | jq '.data.meta |= (fromjson | del(.mirror_halt) | tojson) | del(.metadata.managedFields, .metadata.uid, .metadata.creationTimestamp)' \
    | kubectl apply --server-side --field-manager=upgrade-night-history -f -
  ```

  The resourceVersion in that object makes the apply fail if a tick wrote in between; run
  it again.
- **No edit storm.** On an open PR `terminal` is null, and a null compares equal to a record
  written before the field existed, so the change edits no current record.

### Reading it

```bash
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.summary}' | jq .history
kubectl -n upgrade-agent get cm upgrade-ledger -o jsonpath='{.data.meta}' | jq '{backfill: .backfill.compare, catch_up_mismatch, mirror_halt}'
kubectl -n upgrade-agent get cm upgrade-ledger -o jsonpath='{.data.events}' | jq -c 'select(.type != "merged" or .recorded_by != "backfill")'
kubectl -n upgrade-agent get cm upgrade-ledger -o jsonpath='{.data.items}' | jq 'to_entries[] | select(.value.base.state == "reverted" or .value.basis == "title")'
kubectl -n upgrade-agent get cm upgrade-ledger-marker -o jsonpath='{.data.marker}' | jq .
```

Log line of each tick (stderr):

```text
upgrade-night dryrun: history: WRITTEN: 311 new events (observed 13, merged 298); backfill this tick: 298 merged events, GitHub counts 303 merged Renovate PRs, listed 303, the history holds 303 (5 through another PR's item), missing none; holds 311 events, 311 items (0 named from a title), 182804 of 786432 bytes; 16605 ms
```

**Known limits.**

- **The lane and the shepherd share one identity.** In dry-run a merge by
  `haynes-ops-bot` is the shepherd's, because the lane merges nothing. In live mode, if the
  history write fails on the tick that merged, the next tick finds the PR merged by
  `haynes-ops-bot` and would record it as the shepherd's. The lane's own merges are told
  apart by the change lock's last holder, which story 556 builds before the first live
  class.
- **A deferred revert.** A revert left unread by the budget is read on a later tick only
  while its merge time is inside the catch-up window. During the backfill, that is the whole
  window, because the marker waits.
- **The scripts ConfigMap grows.** `upgrade-shepherd-scripts` now holds about 516 KB of its
  1 MiB, with the lane's copy of the record format and the history module.

## The verifier (story 553)

Story 553 (<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/553>)
builds the read-only verifier: acceptance lines 1 to 3. The revert, the drill and "never merge
this version again" are story 753; the app health check through Gatus is story 755. The code
is `night-verify.mjs`, and the settings are `night.verify` in `upgrade-rules.json`.

**The principle** (the review of PR 4880): "failed, the update's own regression" needs
positive evidence that the update did it. Where the evidence is only absence, or points
elsewhere, the verdict is `not-yet` or `cannot-tell`. It is shown every morning with what it
waits for and since when, and it is never silently open for ever: once it stays open on
`needs_person_after_nights` (2) of the lane's nights and `needs_person_min_ticks` (8) of its
ticks it reads `NEEDS A PERSON`. A practice item that needs a person is given up: it keeps its
last facts, is not judged again, and no longer holds the commit-range read open. The lane's
own merge is never given up.

**When it runs.** Once per tick, after the merge step and before the history, inside a
guarded step: a verifier that fails or does not load is an ERROR line and
`summary.verify.error`, and the rest of the tick runs. Under pause the tick stops before it.

**What it judges.** Every update the history (`upgrade-ledger`) holds in `merged_unverified`
or `awaiting_pin`:

- the lane's own merges (the history's `merged` event by actor `lane`), always;
- in practice, merges by anyone else (Renovate's auto-merges, the shepherd, a person) made at
  or after the verifier was installed. "Installed" is a fact in git: the committer date of the
  first commit on main that added `night-verify.mjs`. The tick reads it once and keeps it in
  `data.verify.since`; a lost memory reads it again. The 30-day backfill is older, so it is
  never practised on. If the install time cannot be read, nothing is practised that tick.

**The update's containers.** A crash, a restart or a container error is charged to the update
only in the update's containers: those whose image is a package of the update (init
containers and native sidecars included), or, for a chart update, every container of the
workloads its HelmRelease renders. The same in another container is `cannot-tell`, and the
container is named.

### The facts

Each fact has its own result: `pass`, `fail`, `waiting`, `unreadable` or `not-applicable`.
"Two reads" means the same condition on two reads at least `min_stable_seconds` (600) apart
on the tick's own clock; on the first read the fact waits and says since when.

| Fact | What is read | Fails when | Unreadable when |
|---|---|---|---|
| `source` | GitRepository `flux-system/haynes-ops` `status.artifact.revision` contains the merge commit (position on main from the tick's one commit-range read, else a GitHub compare) | (waits) | the revision or the compare cannot be read |
| `applied` | the target's own Kustomizations (`settleScope`: those whose `spec.path` is the target or below) have a `lastAppliedRevision` that contains the merge and are settled; the `dependsOn` chain and the Kustomizations that apply the target are settled (`fluxStates`, story 551) | on two reads: `not-applied` (attempted the merge and failing), `not-ready` (applied it and failing) | no Kustomization applies the target; one is suspended; it waits on a failing dependency; a chain Kustomization or an applier is failing (not charged to the update) |
| `helmreleases` | the HelmReleases in those inventories (and with their Flux labels) are settled and their last release is `deployed` | `not-applied`: failing, or its last release failed, on two reads | a HelmRelease is missing or suspended |
| `pods` | in pods made at or after the merge, the update's containers (init containers included) | `crash-loop`; `image-pull`; `not-ready`: CreateContainerConfigError, CreateContainerError or RunContainerError on two reads | pods unreadable; the same in another container; a Pending pod (with the scheduler's reason) |
| `alerts` | Prometheus `/api/v1/alerts`, critical and firing, traced by R5's `alertSubject`; new means `activeAt` at or after the merge; Prometheus's start from `/api/v1/status/runtimeinfo` | `critical-alert`: a new alert whose subject belongs to the target or its chain | Prometheus or its start unreadable; a new alert within `prometheus_restart_slack_seconds` (300) of a Prometheus restart after the merge (a rule with no `for:` restarts its clock); a new alert in a target namespace that cannot be traced, or names no workload; for a cluster-wide target, a new alert with no namespace |
| `image` | each package: the update's containers run the new version (digest or tag decided by the update type the history recorded, never by the characters); else a CronJob template names it; else the HelmRelease whose last release is that chart | `wrong-image`: the old version with nothing rolling; a CronJob template naming another version; a HelmRelease settled on another chart while its chart source is settled; `not-applied`: the chart source failing on two reads | no container, CronJob or chart of the target is the package; a version cannot be read (in a pod or a CronJob template) |
| `exercised` | a CronJob whose template names the update: the newest finished Job from the new template made since the merge (a newer one still running does not hide it) | `job-failed`: that Job failed, and the CronJob's last finished Job from the old template did not fail too | Jobs unreadable; the CronJob suspended; the old template's last Job failed too (it was failing before the merge). None yet waits for the first run, with its schedule, last run and next run |
| `rollout` | each Deployment, StatefulSet and DaemonSet in the inventories: its pods (by owner) are of one template, none terminating, all Ready | (waits) | a workload has no running pod |
| `stable` | two reads (below) | `restart-growth` or `not-ready` of an update's container on a node that did not reboot; `churn` | pods or nodes unreadable; the same in another container; a pod on a node that is not Ready |
| `pin` | own images only (below) | never: before the pin has merged nothing of the update runs, so the item stays awaiting | when a pin candidate was made cannot be read |

`workloads` appears as an extra unreadable fact when an inventory cannot be read. Facts
are read in order: until `source` passes, the rest wait; `image`, `exercised`, `rollout` and
`stable` wait until `applied` passes and the HelmReleases are settled.

**Stability: two reads at least 600 seconds apart.** On the first tick on which the image,
the rollout and the pods pass, the verifier records the target's pods (uid, node, restarts per
container) in its memory, and `stable` waits. A later tick reads them again once at least
`min_stable_seconds` have passed on its own clock: the same pods still there and Ready, with no
more restarts of the update's containers, is a pass. Earlier, the verdict is not yet and says
how long ago the pods were recorded. This minimum is the lane's own measurement of the target
(the story's 10-minute soak), not a wait on another actor. Cluster maintenance is not the
update's:

- a restart of an update's container on a node whose boot id changed since the record, or that
  was not Ready since the record (its Ready condition moved after it), takes a new record and
  says so;
- a recorded pod that is gone after it was Ready with no new restarts at its last read was
  moved (the descheduler, a drain): a new record, and the move is counted;
- a recorded pod gone after it was not Ready or restarting was replaced; at `max_replacements`
  (3) replacements the update's workload fails as `churn`;
- once `stable` has passed it stays passed for the item: health after the soak is the
  alerting's business.

**The verdict.** `failed` when any fact fails; else `cannot-tell` when any is unreadable;
else `not-yet` (with what it waits for); else `verified`. An unreadable fact never yields a
pass. A merge with no cluster effect (a GitHub Action, a script outside `kubernetes/`) has
nothing in the cluster to verify: its facts are `not-applicable` and it is verified.

**Other changes** (Tom's ruling Q-548-04). Every verdict lists the other commits on main after
the merge that reach the target, its chain or a cluster-wide part (`otherChanges`, story 551),
with the parts each reaches and whether Flux has fetched it. It also lists the earlier merges
to the same scope whose own verdict is not final yet: of two merges to one app a minute apart,
the later one's failure is not charged to it alone. An earlier one whose files are unknown
makes the list incomplete. A passing verdict stands whatever else merged. A failing verdict
with such a change, or with the list incomplete, is `cannot-tell-which-change`; with none it is
`own-regression`. The tick reads the commits once, from the oldest merge it judges to main (at
most `max_range_commits`, 60).

**Own images.** A source merge of an image whose digest pin Renovate auto-merges
(`own_image_pin_wait`) is verified through its pin. The pin is identified by what it pins: a
merged history item whose package is this image, whose commit on main changes one of the
image's `pins` files, and whose PR's last commit was made after this source's image build
started. **What ties a pin to a build** is that time: the run's start
(`run_started_at`, from `gh api …/actions/runs?head_sha=<merge>`), not its last update,
because the shepherd's build signs the image after pushing it and a re-run moves the last
update. With no run read, the source merge's own time is the bound (no image carries the
change before it). Another PR that edits those files (the shepherd's own changes), a pin of
another image, or a pin made before the build started is not the pin, and the morning view
names it. The pin is then verified on its own target with its digest, and only those facts,
read after the pin merged, can fail.

Before the pin has merged nothing of the update runs in the cluster, so the verdict is never
failed: a build that failed or was cancelled (GitHub cancels an older pending run when a newer
push queues, and a later run may carry the change), a pin PR that is blocked (its class and
reason, for example `check-failing-after-retry`), an unreadable build run or simply no pin
keep the item "awaiting the digest pin" with that reason, every morning, until the common
"needs a person" rule marks it.

### What it writes

- **Practice verdicts** go to `upgrade-lane-state` only: `data.verify` (its memory: `since`,
  the records, the `done` list) and `data.verify_table` (the morning view), under their own
  field manager `upgrade-night-verify`, written at the end of the tick, after the PR-write
  phase and the tick's own apply. So the tick's apply and its error record never delete them,
  and the retry ledger's version check sees no write before the PR writes. `summary.verify`
  has the counts and the verdicts by PR. A practice verdict never writes the history, never
  opens a pull request, never reverts, and changes no merge verdict, no lock, no PR record and
  none of the tick's own keys. Each record shows the events it would write (`would_write`),
  checked against the item with the format's `applyEvent`. A practice verdict that is verified
  or failed is final: kept, and not judged again.
- **The memory is kept** when the verifier fails, when it cannot read `upgrade-lane-state` or
  the history, and when its time budget runs out: then it writes nothing. A tick that cannot
  list the practice candidates (no install time) drops no open record.
- **The lane's own merges** get history events only with
  `night.verify.write_history_events: true` (git ships `false`): `verified`;
  `verification-failed` with the failing fact's class (its own regression: `failed`, a revert
  is owed, story 753); `escalated` with class `cannot-tell-which-change` (a person owns it; no
  revert is owed); `awaiting-pin`. Not yet and cannot tell write nothing. The history refuses a
  verifier event on an item whose current attempt the lane did not make.

**Budget.** Per tick at most `max_items_per_tick` (12) updates, the least recently judged
first. No read starts once the tick has run `time_budget_seconds` (150; the Job's deadline is
600): the check runs before each shared read (the install time, the history, the Flux
objects, each try of the commit-range read, the alerts), inside the commit-range read (each
commit read) and inside each update's own reads. When it is used, the verifier does nothing
more that tick, says so in `summary.verify.why_not` or `stopped`, keeps its memory (an update
whose reads ran out keeps its last record), and the tick goes on. A record the history no
longer holds is named in `summary.verify.forgotten` when it is dropped. `data.verify` keeps at most
`max_items_kept` (60) records within `max_state_bytes` (128 KiB): final records go first; an
open record keeps its recorded pods (only its details are cut), then the oldest `done` entries
go, and an open record is dropped only last (`summary.verify.trimmed_open`).

**Reads of a steady-state tick** (practice items present, install time kept):
- kubectl: the history (1), and, when no lane-A candidate already read them, Kustomizations,
  HelmReleases and the GitRepository (3); per target namespace pods, CronJobs and Jobs (3,
  shared with the tick's cache); nodes only when a recorded pod is not Ready; a chart source
  only when a HelmRelease is settled on another chart;
- GitHub: one compare plus one read per commit on main since the oldest open merge (at most
  60), one read of the oldest merge's files, and per own-image source one build-run read and
  one read per pin candidate;
- Prometheus: `/api/v1/alerts` and `/api/v1/status/runtimeinfo`.

  Measured on 2026-11-13 from a session pod, last night's five merges: 24 reads (12 GitHub, 12
  kubectl), 15.6 s on the first run and 6.8 s and 14.4 s on the next two (GitHub's latency
  varies). Not measured inside the cluster.

### Reading it

```bash
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.verify_table}'; echo
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.summary}' | jq .verify
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.verify}' | jq '.items[] | {pr, verdict, attribution, needs_person, verdict_since, nights, why, facts: [.facts[]? | "\(.fact): \(.result)"], other: .other_changes.changes}'
```

Each open verdict in `data.verify_table` has an `open:` line: since when, on how many ticks
and nights, the pods' replacements and when the pods were recorded.

Log line of each tick: `upgrade-night dryrun: verifier: 5 judged (verified #…; not-yet #…);
NEEDS A PERSON #…; since …; history read at resourceVersion …; 0 event(s) for the history, 5
practice event(s) not written; 24 reads (12 GitHub); … ms`.

**By hand, read-only, for named merged PRs** (it builds each item as the history would and
keeps its two-tick memory in a local file; a session pod cannot reach Prometheus, so pass the
critical alerts read through Grafana in the shape of `/api/v1/alerts`, with
`prometheus_start`):

```bash
node kubernetes/main/apps/upgrade-agent/shepherd/app/resources/night-verify.mjs \
  --pr 4869 --pr 4874 --state /tmp/verify-state.json --alerts-file /tmp/alerts.json
```

On 2026-11-13 three runs judged last night's merges. At 14:20:52Z #4871 (a GitHub Action, no
cluster effect) was verified and the pods of #4869 (seerr), #4870 (piper), #4873
(traefik-external, chart 42.4.0, merged by the shepherd) and #4874 (cloudflared) were
recorded. At 14:26:13Z all four were still not yet: "recorded 322 s ago; read again at least
600 s after that". At 14:31:04Z, 612 s after the record, all four were verified. #4874 listed
one other change, #4873 on its chain (`network/traefik-external`); the others listed none. The
selftest's fixture is that night, captured read-only
(`scripts/upgrade-agent/fixtures/verify-20261113.json`).

### What pods alone cannot tell (story 755)

The lane's ServiceAccount (ClusterRole `upgrade-health-gate`) reads pods but nothing in
`apps`. So:

- a Deployment, StatefulSet or DaemonSet with no running pod (scaled to zero, or not created
  yet) is `cannot-tell`;
- a controller that has not created all its replicas yet cannot be told from a finished one:
  the pods read one template, all Ready, and the desired count is unknown;
- a pod's template cannot be matched to the workload's current revision, so "the new
  template" is read through the image, not through the ReplicaSet.

The missing rule: `apiGroups: ["apps"]`, `resources: ["deployments", "statefulsets",
"daemonsets", "replicasets"]`, `verbs: ["get", "list"]`. No RBAC is added here.

### Known limits of story 553

- The scope is the lane's: the target is the app directory (`kubernetes/main/apps/<ns>/<app>`),
  so #4873's target holds both traefik Kustomizations, and #4870's every wyoming app.
- A merge more than `max_range_commits` (60) commits back loses its other changes and its pin
  lookup: both read `unreadable`, and the record says why (L2 of the review, not changed).
- Without get and list on deployments, statefulsets, daemonsets and replicasets, a controller
  that has not yet created all its replicas looks finished (story 755).
- A package the target names only outside a container image, a CronJob template or a chart
  (a CRD URL, a kustomize remote resource) is `cannot-tell`.
- An update that changes no pod (a chart bump that renders the same pods) passes on the pods
  it finds: pods alone cannot show that a template did not need to change.
- `NEEDS A PERSON` is a mark in `upgrade-lane-state`, not a history event and not a page.
  It reaches a person through the morning summary (story 554).
- An earlier merge that is still open and whose files are unknown makes every later merge's
  list of other changes incomplete, in any scope; stored files are cut at 100. Both err towards
  `cannot-tell` (L-f of the review, not changed).
- A pin made by a person or a session is not a history item, so it is not found: the source
  merge stays awaiting its pin and ends at `NEEDS A PERSON` (L-d of the review).
- Only `stable` stays passed once it has passed. An item kept open by another fact (a CronJob
  that has not run yet) still reads its pods, alerts and image on every tick.
- Pause stops the tick before the verifier, and the verifier does not release the
  post-merge hold of the change lock (stories 753 and 556).

## Who handles a regression (story 554)

Story 554 (<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/554>),
OP512 rollout step 4. Exactly one of the lane and triage acts on a regression. The code is
`night-claim.mjs`, triage's `lane_hold` and `state_claim` in `triage.sh`, and `lane_phase` in
`gate.sh`. The three files in ConfigMap `upgrade-coordination-lib` are not changed.

**The key is the library's signature.** Triage and the gate key a regression by what
`coordination-lib.sh` computes. `collect_regressions` takes the Flux Kustomizations and
HelmReleases that have been NotReady for more than 600 seconds, and the pods that have stayed
unhealthy, after the library's corpse filters. The key is then
`sig_of(sig_key_of(REG_IDS))`: the Flux lines only when there are any (#3182), otherwise each
pod reduced to its workload. The lane does not re-implement this.
`coordinationSignature()` runs the library itself: `bash -c` with a fixed script that sources
`/opt/coordination/coordination-lib.sh`. That is the same file triage sources, mounted in the
lane's pod by the shepherd release's `globalMounts`. So both sides compute the same signature
for the same regression set by construction. `lane-claim-selftest.mjs` section 1 runs one
fixture through both. A set that changes between two reads gets a new signature, as it does
for triage between its own cycles. Section 4 covers that case.

**Which ids are the update's.** `targetIds()` keeps the ids of the set that belong to the
verifier's record:

- the target's own Kustomizations (`scope.own`);
- its HelmReleases (`scope.hr`, recorded since this story);
- pods whose names its workloads' controllers make.

If the set names nothing of the target, triage and the gate do not see this regression.
There is nothing to claim (`not-in-set`).

**How a claim is written and read today (before this story).** Triage (`triage.sh`) owns
`class`, `attempted`, `result` and `note` on the signature's entry in ConfigMap
`upgrade-agent/upgrade-remediation-state`. The gate only stamps `last_paged` (and `first_seen`
when absent). Triage's claim of a remediation attempt is `attempted=1`, written before it
summons remediate (rule 1, at most once). The write was a fresh `get | jq | replace`, so the
resourceVersion came from a second read, not from the read the decision was made on. A write
by anyone between the two reads went unseen. The gate reads `class`, `result` and `first_seen`
(`remediation_phase`):

- `upgrade` with `pending`, `none` or `inprogress` younger than 40 minutes is `inflight`: no
  Flux page;
- `failed`, or older than 40 minutes, pages;
- `fixed`: a Flux object that still fails pages.

**What ends a claim.** Nothing has a lease:

- triage's result (`fixed`, `pending` or `failed`), which only changes what the gate does;
  `attempted=1` stays;
- the 24-hour age-out. Triage's `state_prune` and every upsert drop entries whose
  `first_seen` is older than `STATE_TTL_HOURS`. A healthy cycle runs `state_prune` too;
- a person editing the entry.

The gate's 40-minute grace measures the age of `first_seen`.

**What this story adds.**

- **The lane's claim** uses the same format: `class=upgrade`, `attempted=1`,
  `result=inprogress`, `note`, `first_seen` (the claim time) and `last_paged` (kept). It adds
  `holder=lane`, `schema=haynes.upgrade-claim/v1`, `pr`, `item`, `lane_state`, `claimed_at`,
  `until` and `ids` (the target's ids).
  - `attempted=1` alone already stops a triage that predates this story.
  - `until` is the lane's bound on its own action: `night.claim.max_work_seconds`, 10800 s,
    from the claim to a verified revert.
  - The lane's states, and the `result` each sets:
    - `working`: `inprogress` (the lane has taken it);
    - `reverting`: `inprogress` (story 753: a revert pull request is open). Only in this state
      do the gate's note and the morning summary say the lane "is reverting"; otherwise they say
      it "has taken" the regression;
    - `reverted`: `fixed`;
    - `revert-failed`: `failed`.
  - `updateClaim()` moves the claim; story 753 calls it.
- **No claim without a revert step (`night.claim.write`, git ships `false`).** A claim makes
  triage stand down and keeps the gate silent until `until`. Before story 753 the lane has no
  revert to act on it. Enabling a live class would then leave the regression with nobody acting
  for up to 10,800 s, where on main triage starts remediation within about 30 minutes and the
  gate pages by 40. So with `write` off, a failed own-regression verdict on the lane's own merge
  in live mode writes nothing to `upgrade-remediation-state`. It records what it would claim,
  as practice does (`would-claim`, with `read only: night.claim.write is off until story 753
  builds the revert`), and triage and the gate act exactly as on main. **Story 753 turns
  `write` on together with the revert.**
- **One holder.** Both sides decide and write on one read. The write is a replace at that
  read's resourceVersion, or a create when the ConfigMap is absent. If someone writes in
  between, the API server refuses the second writer with `Conflict` or `AlreadyExists`. That
  writer reads again: if the entry is now held, it stands down; if it is still free, it tries
  once more. Triage's claim is `state_claim`. It also records `holder=triage` and `ids`.
- **The lane stops** (`triage-holds`, `lost`, `lane-holds`, `unreadable`, with
  `revert_allowed: false`) in four cases:
  - the entry has `attempted=1` and is not the lane's;
  - triage is still working on an entry under another signature (`attempted=1`, result
    `none` or `pending`) that names one of the target's ids;
  - another lane claim holds it;
  - the ConfigMap, the entry or the signature cannot be read or understood.

  It records why. Story 753 reads `revert_allowed`.
- **Triage stops** for a signature whose entry is the lane's. It also stops when a lane claim
  that is still `working` or `reverting`, and whose `until` has not passed, names one of the current ids (a
  set that changed since the claim). It does not attribute, does not summon and does not write
  that entry. It logs `… is held by the night lane for #<pr> … — not triage's: NOT summoning
  remediation and NOT writing its entry`. `state_upsert` never writes over a lane-held entry.
- **The gate** reads the lane's fields first (`lane_phase`):
  - `working` or `reverting` before `until`: `inflight`, no page;
  - `working` or `reverting` after `until`: `failed`; it pages that the lane did not finish by its own bound;
  - `revert-failed`: `failed`; it pages that the revert did not verify, at the existing
    critical priority;
  - `reverted`: `fixed`.

  The gate decides on its own clock. A working claim keeps it silent only while all of these
  hold:
  - `pr`, `claimed_at` and `until` are plain numbers: no sign, no leading zero, at most 12
    digits;
  - `claimed_at` is not ahead of the gate's clock by more than `CLOCK_SKEW_SECONDS` (300, the
    clock difference allowed between pods);
  - `until` is at or after `claimed_at`, and at most `LANE_CLAIM_MAX_SECONDS` (21600) after it;
  - the gate's clock is before `until`.

  Any other entry is one it does not understand, and it is read as today, by
  `remediation_phase`. That covers an unknown state, a claim from the future, and a claim that
  is too long. `remediation_phase` applies the same tolerance: a `first_seen` more than
  `CLOCK_SKEW_SECONDS` ahead counts as past its 40-minute grace, so it pages. On main, such an
  entry stayed silent. An unreadable ConfigMap pages as today. No claim silences anyone for
  ever: the lane's bound ends its silence on the gate's own clock, and the 24-hour age-out ends
  the entry.
- **A moved signature.** A set can change after the lane claimed part of it, so that the new
  signature has no entry of its own while a working lane claim names some of its ids. Triage
  then stands down for the whole set, and the gate pages it at once (as today). The page says
  which PR the lane has taken (or is reverting, when its claim says so), the ids it holds, and
  that the rest of the set has no owner (`lane_overlap_note`).
- **Dry-run and practice never write.** For every verdict that fails on a tick, the step runs
  read-only. It computes the signature, reads the claims, and records in `summary.claims` the
  signature it would claim and whether triage holds it (`would-claim`, `triage-holds`,
  `not-in-set`). Only a live tick with `--write-state`, for the lane's own merge, with a valid
  `night.claim` and `night.claim.write` on, may write.

Log line: `upgrade-night dryrun: claims: #4869 would-claim sig=ae85955b9204 (3 id(s) of its
target)` (or `no failed verdict this tick`).

**Known limits.**

- A regression only the verifier sees has no shared signature: a critical alert, a wrong
  image, a failed Job or restart growth with nothing NotReady. Nothing is claimed for it.
  Triage and the gate do not act on it either.
- A claim of the whole set also covers an unrelated failure that is in the set at the time. It
  silences the gate for that failure until `until`. Triage's at-most-once rule already treats
  the set as one regression.
- After `until` passes, triage still does not summon for the lane's own signature. The
  attempt was made. The gate pages, and its repeat page escalates after 6 hours.
- The gate pages a moved signature while the lane is still working on part of it (see above),
  with a note that names the lane's PR and ids and says the rest has no owner. Triage does not
  remediate the rest while the lane's claim is working.
- F4: a Job of CronJob `upgrade-health-gate-morning-summary` is listed as one that never merges
  (`SHEPHERD_JOBS.never`), so a hand run that overlaps 06:30 is not held by it.

### The alert responder (`rem-*` lane): finding, not changed

The responder can act on the same regression. It neither reads nor writes
`upgrade-remediation-state`.

- `alert-responder/app/resources/respond.sh` keeps its own state in ConfigMaps
  `alert-responder-state` and `alert-responder-spend` (lines 100 and 101). Nothing in it names
  `upgrade-remediation-state`.
- It diagnoses firing critical alerts. On `ACTION: urgent` and still firing, it files a
  `rem-*` order through `/opt/coordination/remediate.sh` instead of paging (lines 514 to 534).
- `health-gate/app/resources/remediate.sh` writes only ConfigMap `upgrade-work-orders`
  (line 50; reads and writes at lines 97 to 120).
- A regression from a merge fires `FluxReconciliationFailure`, which is `severity: critical`
  after 15 minutes (`flux-system/addons/app/monitoring/prometheusrule.yaml`, lines 27 to 38).
- The `rem-*` session may then fix it with runtime verbs or a git PR as the ops bot
  (`dev-env-ops/app/resources/ops-claude.md`, contract step 4, lines 122 to 125). That could
  be a second revert.
- Separately, `respond.sh` passes `ESCALATE_SIG`, but `remediate.sh` reads `REMEDIATE_SIG`
  (respond.sh line 528, remediate.sh line 77). So the `rem-*` key hashes the reason text
  rather than `alertname@namespace`.

To honour a claim, the responder would read `upgrade-remediation-state` before it files a
`rem-*` order: its Role already allows a namespaced configmaps get. When an entry held by the
lane or by triage (`attempted=1`, `ids`) names the alert's Flux object or pod, it would not
file. It would log that the lane or triage owns the regression, and the gate pages as above.
That change is under `alert-responder/` and was not made (the work order holds it).

## The morning summary (story 554)

Tom gets one short message a day at 06:30 America/New_York, at Pushover priority **-1**: no
sound or vibration, still shown as a notification. The design is OP512 comment 6193.

**Composed by the lane.** `night-summary.mjs` composes it on every tick, after the history
and the PR records. It uses only what the tick already holds and makes no read of its own and
no GitHub read:

- the open-PR records;
- the verifier's records (`data.verify` items);
- the history document as the history phase left it;
- the history's own verdict;
- `summary.claims`.

The text is ordered by what a reader must not miss, so that cutting from the end loses
detail, never a count:

1. **Every count, in the first three lines:**
   - the night and the lane's mode; in plain words, whether the lane finished the night (its
     last run was the 05:45 New York one) or not yet; its runs and the time of the last one;
   - merged since the last summary, by whom (the lane, Renovate, the shepherd, a session
     (`hand`), or a person by login); reverted, and how many recovered, did not, or are not
     checked yet;
   - the health checks after merging (passed, still being checked, failed), marked practice
     in dry-run (or in live mode when only practice was judged); and how many items wait for
     Tom.
2. **Then the detail, each group under its own heading, and a group with nothing in it has
   no heading:**
   - `Reverted and NOT recovered:` each with the reason;
   - `Failed health checks:` each with the PR, what failed, whether this update caused it
     ("cannot tell which change caused it" otherwise, also when the other changes could not
     be read), and who has taken it;
   - `Waiting for you:` one line per item: the verifier's `NEEDS A PERSON` marks, the items
     waiting for their digest pin, the PRs whose record says blocked with the reason (for
     `check-failing-after-retry`, the failing check), the owner's updates (majors, holds) on
     one line, and a history that needs a person. This is story 715's list;
   - `Reverted:` those that recovered, and those not checked yet;
   - `Merged:` one line per actor, each with the PR, the package and the version;
   - `Health checks:` what passed and what is still being checked.

Every line is in plain words for a reader who knows nothing of the lane. The text never says
"tick", "claim" or "signature". Who has taken a failure is one of:

- "the night lane has taken it" (or "is reverting it", only when the claim says a revert is
  under way, story 753);
- "the automatic remediation (triage) has taken it";
- "no automatic fix has taken it yet" (also a `lost` claim that nobody holds, and a regression
  the gate does not see: the text makes no claim about what else reports it, because a
  critical alert also pages).

When the text is cut, the last line says `and N more lines`: it counts lines, not items. A
heading is never the last line kept, because it would head nothing. The sender's cut follows
the same rule.

The window runs from the last night's record (`composed_at`). A merge by day therefore
appears the next morning, and nothing appears twice. The facts behind the text keep every
list.

**Limits.** Outside text is data. PR titles, package names, versions, check names and reasons
lose control and format characters. Their whitespace is folded, token shapes are redacted,
and each is capped (`max_name_chars` 40, `max_reason_chars` 90). The text is at most
`night.summary.max_bytes` (1000), under Pushover's 1,024-character cap and `page.sh`'s 1,000
clamp. When it does not fit, whole lines go from the end and the last line says
`and N more`.

**Where it is written.**

- `upgrade-lane-state` `data.morning` (`haynes.upgrade-morning/v1`) holds `night`,
  `composed_at`, `window_from`, `ticks_completed`, `finished`, `last_tick_local`,
  `expected_last_tick_local`, `text`, `bytes`, `lines_cut` and `facts`. Field manager
  `upgrade-night-summary`, written after the tick's own state has landed.
- A delivery copy goes to ConfigMap `upgrade-orphan-report`, keys `morning_text`,
  `morning_night`, `morning_composed_at`, `morning_ticks`, `morning_last_tick`,
  `morning_last_tick_expected` and `morning_mode`. It is a server-side apply of those keys only, under
  the same field manager. The shepherd's orphan keys (its client-side apply keeps keys it
  never set) and the gate's stamps are untouched.

**Delivered by the gate.** CronJob `upgrade-health-gate-morning-summary`, in the health gate's
release: `30 6 * * *`, `timeZone: America/New_York`, running `gate.sh morning-summary`. It
uses the gate's image, ServiceAccount, secret and egress policy, and needs no new access:

| Access the delivery uses | Where it comes from today |
|---|---|
| The lane writes `upgrade-orphan-report` | Role `upgrade-shepherd-spend`: configmaps get, list, create, update, patch in `upgrade-agent` (shepherd `rbac.yaml`) |
| The sender reads it and stamps `morning_sent_night` | Role `upgrade-remediation-state-pager`: get and update on `upgrade-orphan-report` by name (health-gate `rbac.yaml`) |
| Pushover | `upgrade-health-gate-secret` (ExternalSecret, 1Password item `upgrade-gate`), CiliumNetworkPolicy `upgrade-health-gate` (`api.pushover.net:443`), which selects the release's pods by `app.kubernetes.io/name` |

The gate cannot read `upgrade-lane-state`: its Role names three ConfigMaps. Hence the
delivery copy.

The sender reads the copy at the moment it acts:

- **This night's text:** it sends the text.
- **A last run before the night's last one (05:45):** it puts first `The night lane did not
  finish the night: its last run was at HH:MM, not 05:45 (N runs).` Finished is decided by
  the last run, not by a count. The night the clocks go forward has 16 runs, and the night
  they go back may have more than 20.
- **A copy that is missing, empty, or for an older night:** it sends its own message, `The
  night lane wrote no summary for the night of <date>: it did not run, or no run finished.
  Its last summary is for <night>.`
- **A ConfigMap it cannot read:** it says it could not read the summary.
- **Nights with no delivery recorded:** it puts first `No delivery was recorded for the night
  of <date>, so its summary may not have arrived.` (or the range of nights). It does this when
  `morning_sent_night` is older than the night before the one it sends. A value that is not a
  date gives no line and is logged.

It fits the message to 1000 bytes by whole lines, counted in bytes, ending `and N more
lines`. When even the first line and that do not fit, it cuts the first line by characters,
never inside one. It sends through `page.sh` with severity `low` and title `Night upgrades
<date>`. The text reaches curl as one quoted `--form-string`, never as a command line.

**Delivery.** `page.sh`'s exit status says what is known about a send. `gate.sh`'s `page()`
ignores it, as before, and the stderr lines start as before (`paged: …`, `PAGE FAILED: …`):

| Exit | What happened | The sender |
|---|---|---|
| 0 | HTTP 2xx | records the delivery |
| 2 | Certainly not sent: the host could not be resolved or connected to, or Pushover answered HTTP 5xx or 429 | tries again, at most `MORNING_ATTEMPTS` (3) sends, `MORNING_RETRY_SECONDS` (20) apart |
| 3 | A timeout or a broken answer after connecting: it may have been delivered | does not retry (a second copy) and does not record |
| 1 | Any other HTTP answer (a 4xx) | does not retry and does not record |

The delivery is recorded in `morning_sent_night` from a fresh read after the send: get, set the
key, replace at that version, with one retry on a conflict. The gate's own half-hourly run
writes the same ConfigMap. The send is recorded also after the "could not be read" message,
once the ConfigMap can be read. The record is what stops a second run the same morning; its
absence proves nothing. Any failure is one `ERROR morning summary: …` line. The night is left
unrecorded, and the Job still succeeds: a Failed pod of this CronJob would enter the
coordination set and page at priority 1 for a missed quiet note.

**Known limits of the summary.**

- A failed send is not retried after the run. The next morning's message says that no delivery
  was recorded for that night, so it may not have arrived. That is all that is known: a timeout
  after connecting may have delivered it.
- The lane composes the text, so it is as of the night's last completed run. The sender adds
  only its own first lines.

**Pages may not reach Tom.** The saga's comment 6927 says the login watcher logged a page
every night from 2026-11-03 to 2026-11-09 and Tom saw none. That watcher is
`dev-env-ops/app/resources/work-order-watch.sh`: `page()` at lines 90 to 98, priority 0,
title `[dev-env-ops] …`. Its expiry pages are at lines 517 and 523. It uses the Pushover
application token from 1Password item `upgrade-gate` (dev-env-ops `externalsecret.yaml`,
lines 17 to 20), the same application and user key as the gate and this summary. Finding the
cause is piece 2's work. Until Tom confirms he received one summary, delivery is unproven.

```bash
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.morning}' | jq -r .text
kubectl -n upgrade-agent get cm upgrade-orphan-report -o json | jq -r '.data | with_entries(select(.key | startswith("morning_")))'
kubectl -n upgrade-agent logs job/<latest upgrade-health-gate-morning-summary job>
```

## The weekly orphan page counts PRs with no lane (story 554)

The count is made in `run-shepherd.sh`, `write_orphan_report` and `orphan_count`. The
shepherd's scheduled run (every 4 hours) writes it to `upgrade-orphan-report`, and the gate
pages it weekly when it is above 0. It used to count every Renovate PR open for more than
7 days: 63 on 2026-11-09 (comment 6154 on story 508). Now it counts only those missing from
the lane's last record, `upgrade-lane-state` `data.records`. A PR the lane classified into any
class has a lane, blocked, excluded and the owner's lane included. With every open PR
classified the count reads 0 and the gate does not page.

A PR has a lane only while the lane is acting. Every aging PR counts, as before, in each of
these cases (`ORPHAN_LANE_MAX_AGE_HOURS` is a bound on how old the lane's own record may be
before the shepherd stops relying on it; it only ever makes the count louder, and the daily
sender already reports a lane that did not run):

- the lane is paused (`data.pause`);
- its last tick (`data.updated`) is more than `ORPHAN_LANE_MAX_AGE_HOURS` (36) old: the lane
  ticks every night, so it is suspended or not running;
- the record cannot be read;
- the PRs cannot be counted against the record.

`count_basis` in the report, and the page, say which. The lane's ConfigMap reaches jq through
a file descriptor, never as an argument. Linux refuses one argument over 128 KiB, and the
record of 17 PRs is already 41,606 bytes. Before this fix the count read 0 past about 53 open
PRs (the review's H1).

On 2026-11-13 the lane's record held all 17 open Renovate PRs, so the count reads 0. The
report then read 5 (#4349, #4376, #4377, #4378, #4521), all of them classified.

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
the lane marker keeps its old label. The lane's own record (story 549) starts with
`<!-- upgrade-lane v1 -->`, with no shepherd-vet marker and no `VERDICT:` line, so the
labeler does not read it and labels do not change. Making the record drive labels is
outside story 549. `scripts/pr-disposition-test.cjs` pins each token, a token by
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
why there was no such PR. Each record follows schema `haynes.upgrade-lane/v1`.
`preapproval` and `schedule` are empty: piece 1 reads no approval (saga 547, comment
6922). `last_attempt` is set on the merge step's PR only.

What story 752 adds to the state, and how a dry-run night shows the lock and the
merge step:

| Field | What it holds |
|---|---|
| `summary.token_permissions` | `names` (for example `checks:read`, `contents:write`, `pull_requests:write`), `can_merge`, `why` |
| `summary.live_classes` | the allowlist this tick read (git ships `[]`) |
| `summary.change_lock` | `outcome` (`taken-and-released`, `not-taken`, `held after merge …`), `before.state` (`absent`, `free`, `expired`, `held`, `unreadable`), `holder`, `taken_at`, `expires_at`, `released_at`, `took_over_from` and `took_over_phase` (an expired lease taken over), `why` |
| `summary.merge_step` | `pr`, `head`, `class`, `chosen`, `mode`, `verdict` (`not-run`, `refused`, `would-merge`, `merged`, `merge-failed`, `merge-unknown`), `pr_refusals`, `lane_refusals`, `pr_ready`, `merge_call`, `facts` (`mergeable`, `merge_state`, `mergeable_reads` (story 773), `reference_time`, `main_newer`, `behind_main`) |
| `summary.owed_work` | the post-merge hook; `built: false` |
| `records[].last_attempt` | `verdict`, `pr_refusals`, `lane_refusals`, `merge_call` for that PR |

Log lines of each tick (stderr, in this order):

```text
upgrade-night dryrun: token permissions: checks:read, contents:write, pull_requests:write (as requested by mint-token, /creds/gh_token_permissions); can merge
upgrade-night dryrun: change lock upgrade-agent/upgrade-change-lock: taken-and-released (before: absent) taken_at=… expires_at=… released_at=…
upgrade-night dryrun: merge step #<pr> <head8> (<class>): REFUSED: class <class> is not in the live allowlist (UPGRADE_NIGHT_LIVE_CLASSES: empty); merge call: not reached (dry-run)
```

On a dry-run night with git's settings, a clean PR reads `REFUSED` for its class alone,
with `pr_ready: true`. Any other refusal is about the PR. `merge call` always reads
`not reached (dry-run)`.

```bash
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.summary}' \
  | jq '{token_permissions, change_lock, merge_step: (.merge_step | del(.facts))}'
```

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

To run it by hand (read-only, writes nothing; gh must be logged in). Without
`--write-state` the tick takes no lock and so can never merge:

```bash
node kubernetes/main/apps/upgrade-agent/shepherd/app/resources/lane-classify.mjs     # the table
node kubernetes/main/apps/upgrade-agent/shepherd/app/resources/run-night.mjs         # tick, no --write-state
```

For one PR, use the [hand run](#the-hand-run-rehearsal) with `--check-only`.

## Kill switch

- Set `suspend: true` on the `upgrade-night` controller in git and let Flux reconcile it.
  A `kubectl` suspend lasts only until the next reconcile.
- The runtime **pause** takes effect immediately and needs no git change:

  ```bash
  kubectl -n upgrade-agent patch cm upgrade-lane-state --type merge -p '{"data":{"pause":"<who> <why>"}}'
  ```

  While `data.pause` is non-empty, each tick logs that it is paused and does nothing else.
  It reads nothing from GitHub, takes no lock, merges nothing and writes nothing: no PR
  record and no retry (story 549). The PR-write phase and the retry read the pause again
  just before they write. To
  resume, remove the key. The server-side apply never removes a key that another field
  manager set. For what pause leaves running in live mode, see
  [Pause and suspend in live mode](#pause-and-suspend-in-live-mode).

- **PR writes only** (story 549): a `halt` in `data.retry_ledger` stops every PR record
  and retry and nothing else. The lane sets it itself in two cases: GitHub reports that a
  record was written under an identity other than `night.pr_record.author`, or GitHub denies
  a write (401, or a 403 that is not a rate limit). To clear it, after
  fixing the cause, remove the key:

  ```bash
  kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.retry_ledger}' \
    | jq -c '.halt = null' > /tmp/ledger.json
  kubectl -n upgrade-agent patch cm upgrade-lane-state --type merge \
    -p "$(jq -n --rawfile l /tmp/ledger.json '{data: {retry_ledger: ($l | rtrimstr("\n"))}}')"
  ```

## When a tick fails

The tick uses these exit codes:

| Exit | When |
|---|---|
| 0 | The tick finished or was paused. Also when it failed but recorded the failure: it writes `data.error` (and `summary.error`) to `upgrade-lane-state`. This includes a state over the 768 KiB budget, which is caught before the write; the small error record still lands. |
| 2 | The state write itself failed: the server-side apply was refused. The log carries `ERROR upgrade-night: STATE WRITE FAILED`. The ConfigMap cannot record its own write failure, so a Failed pod is the only signal. |
| 1 | The tick failed and its error record could not be written either. |
| 3 | Refused before any read: an unknown `UPGRADE_NIGHT_MODE`, an unknown class in `UPGRADE_NIGHT_LIVE_CLASSES`, or `UPGRADE_NIGHT_NOW` set in live mode or with `--write-state`. |

- A tick that fails after it took the change lock releases it on the way out (see
  [The change lock](#the-change-lock)). A pod killed outright leaves it to its
  600-second lease. Once the lock reads `merging`, nothing releases it before its
  4-hour lease.
- A tick still running at 600 seconds is killed by the Job's `activeDeadlineSeconds`.
  Its Job fails (`DeadlineExceeded`) and counts as a Failed pod, as below. Ticks take
  about 25 seconds today.

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

Before the first live night, story 754 decides how a Failed `upgrade-night` pod is
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
node scripts/upgrade-agent/lane-night-dryrun-selftest.mjs  # the dry-run guarantee, the gates, R5 and R7/R8, the merge step and the lock through the tick
node scripts/upgrade-agent/lane-merge-step-selftest.mjs    # the merge step's refusals, the mergeability re-read, the lock's states and exit paths, the hand-run CLI, the settle gate
bash scripts/upgrade-agent/shepherd-stand-aside-selftest.sh  # the shepherd standing aside for a lane change in progress (story 551)
node scripts/upgrade-agent/lane-pr-records-selftest.mjs      # story 549: the record, whose record, the retry's facts, limits and ledger, the GitHub writes
bash scripts/upgrade-agent/shepherd-prefilter-selftest.sh    # story 549: vet markers count only from our bot (64 real markers, forged copies)
node scripts/upgrade-agent/lane-history-selftest.mjs         # story 593: the history: events, backfill, comparison, rotation, conflicts, terminal records
node scripts/upgrade-agent/lane-verify-selftest.mjs          # story 553: the verifier on 2026-11-13's merges: each failure mode, not yet, cannot tell, other changes, the pin, practice never in the history
node scripts/upgrade-agent/lane-claim-selftest.mjs           # story 554: one signature on both sides, who claims first, both at once, unreadable claims, the gate's reading on its own clock, a moved signature, triage.sh run, dry-run writes nothing
node scripts/upgrade-agent/lane-summary-selftest.mjs         # story 554: the morning summary: counts first, reverts, waits, plain words, the 05:45 tick, too long, hostile text (--sample prints 2026-11-13 and a busy night)
bash scripts/upgrade-agent/gate-morning-selftest.sh          # story 554: the 06:30 sender: priority -1, no summary, not finished, bytes not characters, three attempts, the missed night, hostile text runs nothing
bash scripts/upgrade-agent/orphan-count-selftest.sh          # story 554: the weekly orphan count is PRs with no lane: 60+ PRs, a paused or stale lane
```

The story 554 suites run the shipped `gate.sh`, `page.sh` and `triage.sh` functions against
`scripts/upgrade-agent/test-fake-cluster.mjs`, a fake `kubectl` and `curl` with the API
server's preconditions (Conflict, AlreadyExists). `test-triage-harness.sh` runs one extracted
triage function. `test-lane-claim-hook.mjs` is the lane's side of a race.

`lane-night-dryrun-selftest.mjs` section 6 runs story 549 through the real tick against a
stubbed GitHub: the steady state (a second identical tick writes nothing), the per-tick cap,
the ledger written before the box, a second failure that blocks, a refused write, a body
or head that changed between the read and the write, a forged record, the token, the
local run, the halt and a pause.

`lane-merge-step-selftest.mjs` reads
`scripts/upgrade-agent/fixtures/merge-step-pr3915-20261111.json`, PR 3915 as the merge
step's reads saw it on 2026-11-11. It and `lane-night-dryrun-selftest.mjs` read
`scripts/upgrade-agent/fixtures/settle-20261110.json` for the settle gate, and it and
`shepherd-stand-aside-selftest.sh` read `scripts/upgrade-agent/fixtures/change-lock-states.json`
(story 551).

Since step 2 these run in CI as `Upgrade Agent Selftests` on pull requests and pushes to
main that touch the shepherd app (`kubernetes/main/apps/upgrade-agent/shepherd/app/**`),
the two shared shell sources the selftests execute (`coordination-lib.sh`,
`respond.sh`), `scripts/upgrade-agent/**`, any workflow file (for the drift test) or
`.renovate/**`. The workflow runs, one at a time:

- the lane selftests above (classifier, dry-run tick, merge step, record format), plus `node --check` and `jq .` on the shipped files;
- the PR-record suite (story 549), after the record-format suite;
- the shell selftests: `coordination-sig`, `held-audit`, `shepherd-verdict`,
  `shepherd-stand-aside`, `shepherd-prefilter` (story 549), `stale-corpse`, `triage-claim`,
  `gate-morning` and `orphan-count` (story 554) (`scripts/upgrade-agent/*-selftest.sh`);
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
3. Built (story 549), in dry-run: the per-PR record as one bot comment, the
   comment-author check in the shepherd pre-filter, and the one-retry rule for stalled
   checks.
4. Built (story 554): ownership of a regression signature between the lane, triage and the
   gate; the morning summary at 06:30; the orphan page counting PRs with no lane. The
   alert responder's `rem-*` lane does not honour a claim yet (finding above).
5. The first live merge: one class, one merge a night, a stateless leaf (story 556).
   Then stateful targets and our own images. Story 752 built the live mode, the
   write-capable token, the merge step and the change lock. Story 553 built the read-only
   verifier (practice verdicts in dry-run). Still to come: revert by pull request (story 753), and failure handling
   and a revert owed while paused (story 754). Story 551 added the settle gate, the
   shepherd standing aside and the one-owner rule for ramp PRs.
6. Lane B: work orders, an ask phase and a scheduled phase.
