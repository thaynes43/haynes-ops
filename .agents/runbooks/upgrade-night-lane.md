# Nightly upgrade lane (OP512; dry-run in git)

The nightly lane will merge low-risk Renovate updates one at a time at night, after the
backups, with a health check after each one. Tom asked for upgrades to be 100% agent
driven (OP512, comment 6168). The design is OP512 comment 6193
(<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/512>).

**Status: dry-run in git.** The lane classifies updates and records what it would do. It
merges nothing, comments on nothing and labels nothing.

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

Verification after a merge is story 553 and is **not built**. A live merge therefore
keeps the lock, phase `merged-awaiting-verification`, for `post_merge_lease_seconds`
(4 hours). Until it expires no tick merges, so the lane merges at most once a night.
`owedWork()` in `night-merge.mjs` is the hook where stories 553, 753 and 754 run the
verification and revert a merge owes; today it records `built: false` and runs nothing.

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
  It reads nothing from GitHub, takes no lock, merges nothing and writes nothing. To
  resume, remove the key. The server-side apply never removes a key that another field
  manager set. For what pause leaves running in live mode, see
  [Pause and suspend in live mode](#pause-and-suspend-in-live-mode).

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
```

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

- the three lane selftests above, plus `node --check` and `jq .` on the shipped files;
- the shell selftests: `coordination-sig`, `held-audit`, `shepherd-verdict`,
  `shepherd-stand-aside`, `stale-corpse` and `triage-claim`
  (`scripts/upgrade-agent/*-selftest.sh`);
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
5. The first live merge: one class, one merge a night, a stateless leaf (story 556).
   Then stateful targets and our own images. Story 752 built the live mode, the
   write-capable token, the merge step and the change lock. Still to come: verification
   after a merge (story 553), revert by pull request (story 753), and failure handling
   and a revert owed while paused (story 754). Story 551 added the settle gate, the
   shepherd standing aside and the one-owner rule for ramp PRs.
6. Lane B: work orders, an ask phase and a scheduled phase.
