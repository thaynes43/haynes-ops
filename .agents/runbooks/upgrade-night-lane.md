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
  `immediate-cluster-change`.
- ComfyUI, dev-env v1 and the sha-tag builds have no cluster effect.

## Gates the tick evaluates (lane-A candidates only)

- Expected checks: if any check is older than 7 days, the tick asks for a Renovate retry
  first.
- Backup: for each stateful target, either its VolSync ReplicationSource `<APP>-aws` or
  its CNPG cluster's newest Backup must be newer than 23:00 New York on the night of the
  tick.
- Baseline: no Flux Kustomization NotReady, Ceph not in `HEALTH_ERR`, and no critical
  alert firing in the target namespace.
- The change lock (`upgrade-change-lock`) is not held.
- No work order is `claimed`.
- The merge window, 01:30 to 04:45 New York, is open.

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

Set `suspend: true` on the `upgrade-night` controller in git and let Flux reconcile it.
A `kubectl` suspend lasts only until the next reconcile.

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
   verify-and-revert loop and the runtime `pause` key.
6. Lane B: work orders, an ask phase and a scheduled phase.
