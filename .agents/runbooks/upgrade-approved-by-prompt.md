# Updates Tom approves by phone prompt, run by hand (story 712)

Some updates need Tom's yes before they merge: majors, prereleases, holds, one-way changes
and the other classes the nightly lane leaves to a person. Tom ruled that every approval
goes through the dev-env v2 approval framework, which arrives in piece 2. Until then, this
runbook carries approvals. A coordinator session asks Tom by phone prompt. On a yes, it
runs that one update by hand at night, after the backups, and reads the verifier's verdict.

- Story: <https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/712>
  (comment 7027 is part of its scope). Epic 548, saga 547.
- **Status: interim.** It ends when the lane reads a dev-env approval (story 552). See
  [When this procedure ends](#9-when-this-procedure-ends).
- **Who uses it:** a coordinator session in haynes-ops, and the piece-2 platform fixes
  (stories 570 and 574, for example), which also need Tom's yes before each change.
- **Read with:**
  - [upgrade-night-lane.md](upgrade-night-lane.md): the lane, its gates, the change lock,
    story 551's checks and the verifier (story 553).
  - [../reference/upgrade-records.md](../reference/upgrade-records.md): the lane's record
    on each PR and the history's events.
  - [renovate-upgrade-batches.md](renovate-upgrade-batches.md): the delivery command
    (`deliver_reviewed_pr.py`), how to research a major (its Tier 3) and its checks after a
    reconcile. This runbook uses those parts and does not repeat them. It does not use
    that runbook's batches: here it is one update, one yes, one night.

## The rules in one place

- **The yes is Tom's answer to a phone prompt.** It covers one update, at one head, on one
  night. The run uses it up. A new head, a new night or a second update needs a new yes.
- **No answer is not a yes.** An expired question is asked again.
- **A GitHub review is not an approval.** An Approve click, a review comment or a label
  carries no approval. Tom has never approved on GitHub. `deliver_reviewed_pr.py` does not
  read review decisions.
- **The lane never reads the yes.** The yes lives in a comment by the session on the PR and
  in a tracker comment. The lane's record (`preapproval`) and the history's `approval`
  field stay empty in piece 1.
- **Facts, not clocks** (Tom, ruling Q-548-04, comment 7135 on story 551). The session
  plans around no other actor's schedule and uses no fixed wait. It reads the real state at
  the moment it acts, and it does not assume it is the only one acting.
- **One update at a time.** The next one starts only after the previous one is verified.
- **On a failure:** revert by PR when the failure is the update's own. When the verifier
  cannot tell which change did it, hold and report (Tom's ruling).
- **A denial is a stop.** RBAC, admission, `gh` or an API that refuses ends the run. Never
  retry through another identity or route.

## 1. Which updates

**In:** open Renovate PRs in lane `LANE-B-OWNER` (item lane `B-owner`, "Tom has to
decide"). The lane puts these there:

- majors and prereleases;
- versions held in `.renovate/holds.json5` (a yes overrides one of Tom's standing holds, so
  the question must say so);
- paths that restart dev-env v1, and with it every session in v1;
- one-way changes (a cilium minor, an authentik `YYYY.M`), and Dragonfly 2.1;
- a PR whose current head carries a shepherd vet `HOLD`.

Also in: a change a session prepared itself that a ruling says needs Tom's yes (the piece-2
platform fixes). It follows the same steps, with the differences in
[A PR that is not Renovate's](#a-pr-that-is-not-renovates).

**Out, with their owner:**

| Lane | Why it is not here | Who |
|---|---|---|
| `LANE-A` | needs no yes | the lane, or a bridge night (story 729) while the lane is dry-run |
| `LANE-B-AGENT` | an agent must change something first (a rule only an agent can satisfy, a non-Renovate commit) | an agent session; if the result still needs Tom, it comes back here at its new head |
| `EXCLUDED` | it has another owner: the shepherd's ramp, the dev-env v2 coordinator's paths, the merger's own paths, workflows | that owner |
| `BLOCKED` | a check fails or is missing | Renovate's retry, or an agent |

**Where the list comes from.** Today, the lane's last tick:

```bash
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.records}' \
  | jq -c '.[] | select(.lane == "LANE-B-OWNER") | {pr, head, class, reason, cluster_effect, targets}'
```

On 2026-11-13 this listed PR 4376 (Node 26 image) and PR 4764 (ControlFoley major). The
morning summary of story 554 and the daily list of story 715 will list them; neither is
built yet.

**Done when:** you have one PR number, and the lane's record and the check below agree that
its only reason to wait is Tom.

## 2. Asking

### Before you ask

Check the premise. Ask only about a head that can run.

1. **Review the update at its head.** Read the diff and the release notes. For a major,
   follow Tier 3 of [renovate-upgrade-batches.md](renovate-upgrade-batches.md): release
   notes, our usage, required value edits. If the update needs an edit, it is not this
   runbook's update any more: the edit makes a new head and a non-Renovate commit (lane B,
   agent). Record the review on the tracker.
2. **Run the hand-run check, read-only.** From an up-to-date worktree of main:

   ```bash
   PR=4764
   HEAD=$(gh pr view "$PR" -R thaynes43/haynes-ops --json headRefOid -q .headRefOid)
   node kubernetes/main/apps/upgrade-agent/shepherd/app/resources/night-merge.mjs \
     --pr "$PR" --head "$HEAD" --check-only
   ```

   It always exits 1 for these PRs, because it refuses anything outside lane A. Read its
   `refused:` lines. The only one allowed is the lane line, for example
   `refused: lane LANE-B-OWNER (major): update type major`. A refusal about the change
   lock, the pause, the settle gate or F4 describes the moment, not the PR (section 5.4).
   Any other refusal (a failing or missing check, `checks predate main`,
   `head is behind main`, a non-Renovate commit) must be fixed first, usually by a Renovate
   rebase, and that gives a new head. On 2026-11-13 PR 4764 also read
   `check failed: Build and Push ControlFoley Image / validate`, so it was not ready to ask
   about.
3. **Know the answers to the risk questions.** Which app restarts. Whether it has state (a
   PVC, a ReplicationSource, a CNPG client in `cnpg_clients`). Whether a revert by PR
   restores it (a schema migration or another one-way change may not). Whether it ends any
   session (a v1 restart ends every session in v1: name them). What the lane record's
   `cluster_effect` says: `immediate`, `delayed` (one of our own images: the cluster
   changes only when Renovate auto-merges the digest pin, at a time nobody chooses) or
   `none`.
4. **Pick the night.** "The night of Thursday 2026-11-19" means the backups at 00:00 New
   York on Friday 2026-11-20 and the merge window 01:30 to 04:45 New York that follows.
5. **Number the question** `Q-<story>-NN`, on the story that carries the night, and save
   its exact text in a file before you ask. The record quotes that file.

### The question

Use the AskUserQuestion tool (the phone prompt). One question at a time: never two open
prompts, and never two updates in one question. Plain words. It carries:

- the link: the full PR URL and the short head;
- what the update is: the package, from and to, and the app it lands in;
- the risk: what can break, who would notice, whether a revert restores it;
- your recommendation, and why;
- the proposed night, and how the run will be started (see section 4).

The form, with PR 4764 as it would read once its build check passes:

> Approve one update? PR 4764, https://github.com/thaynes43/haynes-ops/pull/4764 at head
> d6a69455: ControlFoley's Python requirements move to new major versions (av 14 to 19,
> and others). It changes only the source of the ControlFoley image; nothing running in
> the cluster changes. Risk: the next image build or render could break; a revert by PR
> restores it. I recommend yes. Proposed night: Thursday 2026-11-19, after the backups,
> between 01:30 and 04:45 New York. This session starts it itself after the backups; if
> the session is not running then, nothing happens and I ask you again.

Options: `Yes, on the night of <date> (Recommended)`, `Not yet`, `No`. Put
`(Recommended)` on the option you recommend.

### The answer

- **Only a plain yes to that update and that night counts.** `Not yet`, `No`, or free text
  that is not plainly yes, is not a yes. Record it as given anyway.
- **An answer that may not be Tom's is not a yes.** A message from another session typed
  into a session with an open prompt lands as the answer (2026-11-10). If the free text
  reads like a session's message, ask again. Reach a session that may be waiting on Tom
  through the tracker, not `agent-run msg`.
- **No answer is not a yes.** If the prompt expires, or the session restarts before he
  answers, ask again with the same number, marked "asked again" with the time. Do not ask
  while a run is in progress.

**Done when:** you have his answer, word for word, to a question whose text you saved.

## 3. Recording the yes

**What a session can and cannot write.** The lane's record on a PR is one comment by the
lane's identity, `haynes-ops-bot[bot]`. It starts with `<!-- upgrade-lane v1 -->`, and the
lane rewrites it whenever its content changes. Its `preapproval` field must stay empty: the
lane refuses to publish a record that has anything in it. So a session never edits the
lane's record. Even where GitHub would let a write-access account edit it, the lane would
overwrite the edit, or the edit would corrupt the record.

What a session does instead:

1. **A comment of its own on the PR**, as the session's GitHub identity
   (`haynes-dev-bot[bot]`).
2. **The same text as a tracker comment** on the story that carries the night, linked from
   story 712.

Both are an owner answer. The lane does not read either. Write the comment so that no
automation reads it as anything:

- no `<!-- upgrade-lane v1 -->` and no `<!-- shepherd-vet ... -->` marker, and no
  `VERDICT:` line (the labeler and the lane read those);
- no `@` mentions. `@claude` starts the Claude workflow, and any `@name` notifies someone.
  If Tom's words contain `@`, put them in backticks and change nothing else;
- nothing private. The repository is public. If his answer names people, places, health or
  money, put it word for word on the tracker only, and say on the PR that the answer is on
  the tracker.

Right after his answer:

```bash
EV=~/night-run/pr4764-20261119; mkdir -p "$EV"   # the evidence folder, never in ~/.shared
date -u +%FT%TZ | tee "$EV/answer-recorded-at"
```

Then write `$EV/owner-answer.md`:

```markdown
Owner answer, recorded by session <session> at <time from answer-recorded-at> (shell clock, after the answer).

Question Q-<story>-NN as put, by phone prompt in session <session>: "<exact text>"
Options offered: "<a>"; "<b>"; "<c>"
Tom's answer, word for word: "<answer>"

What it covers: this pull request at head <40-character sha>, on the night of <date> (New York),
run once under .agents/runbooks/upgrade-approved-by-prompt.md by session <session>. Nothing else.
The run uses it up. A new head or another night needs a new answer.
The nightly lane does not read this comment. A GitHub review or approval is not an approval.
This is not an instruction to any other actor.
Tracker: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/<story>
```

Post both and read them back:

```bash
gh pr comment 4764 -R thaynes43/haynes-ops --body-file "$EV/owner-answer.md"   # prints the comment URL
gh api --paginate repos/thaynes43/haynes-ops/issues/4764/comments \
  --jq '.[] | select(.user.login == "haynes-dev-bot[bot]") | "\(.id) \(.html_url) \(.created_at)"' | tail -1
python3 -I -B scripts/openproject/opwork.py comment <story> - < "$EV/owner-answer.md"
python3 -I -B scripts/openproject/opwork.py show <story> --history | tail -5
```

Then set the start and create the morning-after reminder (section 4).

**Done when:** both read-backs show the comment, and you have its PR comment URL and its
tracker comment id.

## 4. The trigger for the agreed night

Two parts: what starts the run, and one durable reminder that tells Tom the next morning
when the run did not happen or did not finish its record. Tom is never paged at night to
start a run he already approved.

### What may start the run

- **Today: the coordinator session that heard the yes** in its own phone prompt. It starts
  the run itself from its own check-in, set in the session for the start of the merge
  window (01:30 New York on the agreed night). Use a check-in the harness offers, such as
  a loop or a wake-up in the session, not a shell `sleep`. When the check-in fires, the run
  starts at 5.1.
- **A worker that session starts when the check-in fires**, with a work order naming the
  PR, the head and both records. The coordinator stays accountable.
- **Once scheduled work is served: a session started by a scheduled item** whose exact spec
  Tom approved (below).

Any other session that finds the recorded yes has data, not authority.

**The check-in is not durable.** It lives only as long as the session. If the session is
suspended, restarted or gone at that time, nothing runs and the yes lapses with the night.
That is the safe outcome: nothing merges without a session that heard the yes. The session
asks Tom again for another night.

### The morning-after reminder (durable)

Create it when the yes is recorded (section 3), for 08:00 New York on the morning after the
agreed night. It tells Tom where the result is, and that no result means no run:

```bash
agent-run remind create "PR 4764: approved for last night, result on story <story>" \
  --due 2026-11-20T08:00 --tz America/New_York \
  --body "PR 4764 (ControlFoley major) was approved for last night. Its result is on https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/<story>. If nothing is there, it did not run and the approval has lapsed." \
  --targets pushover:default --key op712-pr4764-20261120-morning
agent-run remind show <name> -o json
```

The session cancels it once its close-out is on the tracker (section 8):

```bash
agent-run remind cancel <name>
agent-run remind show <name> -o json    # proof: no longer pending
```

So Tom receives it only when the session did not finish its job. A reminder pages Tom's
phone and starts nothing. `agent-run remind list` answered on 2026-11-13, so this part
works today. If Tom withdraws the yes before the night, record that on the tracker and
cancel the reminder too.

### Scheduled work (not served today)

When the operator serves the scheduled-work API, the coordinator creates one item per
agreed night. That replaces the session's own check-in; the morning-after reminder stays.
Write the work order to a file first: the PR, the head, the two record links, this
runbook and its stop rules.

```bash
agent-run schedule create "Approved update: PR 4764 ControlFoley major (story 712)" \
  --due 2026-11-20T01:30 --tz America/New_York \
  --not-after 2026-11-20T04:45:00-05:00 \
  --mode headless --template <dispatch template> --repo haynes-ops \
  --prompt-file "$EV/work-order.md" \
  --notify pushover:default --key op712-pr4764-20261120
agent-run schedule show <name> -o json
```

What it needs:

- **Tom's approval of that exact spec.** A coordinator's item never runs without it. Tom
  runs `agent-run schedule approve <name>` from his own token; the API refuses it from a
  session. His phone-prompt yes to the update does not approve the spec. Any edit voids
  the approval.
- **A dispatch template** that admits the work, in ConfigMap
  `dev-env-system/dev-env-scheduled-work`.
- `--not-after` at the end of the merge window, so nothing starts after 04:45. Use that
  date's offset (`-05:00` in winter, `-04:00` in summer).
- A headless item pages nobody without `--notify`; `schedule show` prints its results.
  With `--notify`, an occurrence that is parked pages Tom at its due time, which is at
  night.

**Read on 2026-11-13 at 21:06Z:** `agent-run schedule list` answered
`no route GET /v1/scheduled-work (404 not_found)`. The dispatch-template ConfigMap does not
exist, and there are no ScheduledWork objects. The operator's HelmRelease does not set
`--enable-scheduled-work-api`. So this route is not available.

**Done when:** the session's check-in is set for 01:30 New York on the agreed night (or,
once scheduled work is served, `schedule show` prints the item), `remind show` prints the
morning-after reminder at 08:00 the next day, and both are named in the tracker record.
When the yes is withdrawn, cancel both.

## 5. The run

It runs inside the merge window, 01:30 to 04:45 New York (`night.merge_window_local` in
`upgrade-rules.json`). It runs only after that night's backups have been read back as
finished for the app. Every step below is a read at the moment of acting. When a read says
"not now" (a held lock, a running Job, a backup not finished yet), read it again. The run
does not use a fixed wait. If the window closes first, the night is lost and the yes
lapses.

**Shell state.** An agent's shell keeps no variables between calls. Keep the values in a
file and source it at the top of each call:

```bash
cat > ~/night-run/pr4764-20261119/env <<'EOF'
PR=4764
HEAD=<40-character head from the owner answer>
REPO=thaynes43/haynes-ops
EV=$HOME/night-run/pr4764-20261119
KS_NS=<namespace of the app's Flux Kustomization>; KS_NAME=<its name>   # from the app's ks.yaml
APP_NS=<namespace the app's pods run in>
EOF
. ~/night-run/pr4764-20261119/env    # first line of every later call
```

Work from an up-to-date worktree of main (`git fetch origin main`, then a worktree on
`origin/main`). Stamp every step with `date -u +%FT%TZ` into `$EV/times.txt`. The record
takes its times from there and from GitHub.

### 5.1 Is it still a yes?

```bash
gh pr view "$PR" -R "$REPO" --json state,isDraft,headRefOid --jq '[.state, .isDraft, .headRefOid] | @tsv'
kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.pause}'; echo
```

- The PR must read `OPEN false <HEAD>`. A different head is not covered: stop, and ask
  again for another night.
- Read the story's newest comments and Tom's latest words. His latest pause or stop
  governs.
- A non-empty `data.pause` means someone paused upgrades. Stop.

**Done when:** all three hold, and their times are stamped.

### 5.2 The backups finished for the app

Find the target and its Kustomization. Use the lane record's `targets`, or the PR's paths:

```bash
kubectl get kustomizations -A -o json | jq -r --arg p "./kubernetes/main/apps/<ns>/<app>" \
  '.items[] | select(.spec.path == $p or (.spec.path | startswith($p + "/"))) | "\(.metadata.namespace)/\(.metadata.name)"'
```

**VolSync.** Read each ReplicationSource the Kustomization applied:

```bash
kubectl get replicationsources -A \
  -l kustomize.toolkit.fluxcd.io/name="$KS_NAME",kustomize.toolkit.fluxcd.io/namespace="$KS_NS" -o json \
  | jq -r '.items[] | [.metadata.namespace, .metadata.name, .spec.trigger.schedule, .status.lastSyncTime, .status.nextSyncTime, .status.latestMoverStatus.result] | @tsv'
```

A source passes when its result is `Successful`, and `lastSyncTime` is at or after its most
recent scheduled run, and no older than 24 hours. The most recent scheduled run is
`nextSyncTime` if that time has passed; otherwise it is `nextSyncTime` minus the period of
the schedule. `0 0 * * *` runs at 05:00Z. This is the lane's rule (`night.backup_rule`).

**CNPG.** Only when the target's path is listed under `cnpg_clients` in `upgrade-rules.json`:

```bash
kubectl get scheduledbackups.postgresql.cnpg.io -A -o json \
  | jq -r '.items[] | [.metadata.namespace, .metadata.name, .spec.cluster.name, .status.lastScheduleTime] | @tsv'
kubectl -n <ns> get backups.postgresql.cnpg.io -o json | jq -r --arg c <cluster> \
  '[.items[] | select(.spec.cluster.name == $c and .status.phase == "completed")] | max_by(.metadata.creationTimestamp) | "\(.metadata.name) \(.metadata.creationTimestamp)"'
```

It passes when the newest completed Backup's `creationTimestamp` is at or after the
ScheduledBackup's `lastScheduleTime`. Do not use `startedAt` or `stoppedAt`: some instances
stamp local time with `Z`.

**No source.** If the app has state (a PVC, a volsync component or `VOLSYNC_*` in its
Kustomization, a CNPG client) and no ReplicationSource of its own, stop. That is the lane's
`backup-unmatched`. An update with `cluster_effect` `none` changes nothing in the cluster;
record that, and read no backup.

**Done when:** every source of the target passes, and each line is saved in
`$EV/backups.txt`.

### 5.3 The baseline, and the tick's gates the hand run skips

The hand-run check skips what only the lane's tick reads: backups (above), the Flux
baseline, critical alerts and work orders. Read them, and save each to `$EV/baseline.txt`:

```bash
flux get kustomizations -A --status-selector ready=false
flux get helmreleases -A --status-selector ready=false
kubectl get pods -A --no-headers | grep -vE ' (Running|Completed) '
kubectl -n rook-ceph get cephcluster -o jsonpath='{range .items[*]}{.status.ceph.health}{"\n"}{end}'
kubectl -n upgrade-agent get cm upgrade-work-orders -o json \
  | jq -r '.data | to_entries[] | select((.value | fromjson? | .status) == "claimed") | .key'
```

A session pod cannot reach Prometheus. Read the critical alerts through Grafana, with the
MCP tool `grafana_api_request`:

- endpoint `/api/datasources/proxy/uid/prometheus/api/v1/alerts`
- jq `[.data.alerts[] | select(.state == "firing" and .labels.severity == "critical") | "\(.labels.alertname) @ \(.labels.namespace // .labels.exported_namespace // "-")"]`

Stop when any of these holds. These are the lane's blockers:

- a NotReady Kustomization that applies the target, is in its `dependsOn` chain, or targets
  its namespace;
- a NotReady HelmRelease in its namespace;
- a firing critical alert in the target's namespace or its chain's namespaces, unless you
  can name the object it is about and show that another Kustomization applied it; for a
  cluster-wide target, any critical alert with no namespace label;
- Ceph `HEALTH_ERR`, or no answer;
- a work order `claimed`.

Everything else in the baseline is recorded and does not stop the run.

**Done when:** `$EV/baseline.txt` holds all six reads with their times, and none of them is
a blocker.

### 5.4 Story 551's checks, at the moment of acting

Story 551 sets these checks for every actor that merges (four actors: Renovate's
auto-merge, the shepherd, the lane, and people and sessions):

- **The change lock** must be free. A hand run cannot take it (a session cannot write
  ConfigMaps in `upgrade-agent`), so it refuses while anyone holds it.
- **No pause.**
- **The settle gate** (F1 to F3), for an update with an immediate cluster effect. No change
  on main that Flux has not applied may reach the target, its chain or a cluster-wide part.
  The target must have applied Flux's revision and be settled. No cluster-wide part may be
  mid-change.
- **F4.** No unfinished Job in `upgrade-agent` that can merge (a shepherd Job, a triage Job
  with a claimed remediation, any unknown Job).

The hand-run check reads all four, plus the merge step's own preflight. Run it last, right
before delivery:

```bash
node kubernetes/main/apps/upgrade-agent/shepherd/app/resources/night-merge.mjs \
  --pr "$PR" --head "$HEAD" --check-only | tee "$EV/check-only.txt"
```

The only `refused:` line allowed is the lane line (`lane LANE-B-OWNER (…)`). A refusal
starting `the change lock`, `settle gate:`, `F4` or `paused` is a fact of this moment: read
again. A lock held in phase `merged-awaiting-verification` means the lane merged tonight
and keeps the lock for 4 hours; no hand run merges until it is released or expires. Any
other refusal is about the PR: stop.

**What the other actors see.**

- **During the run:** nothing. The hand run holds no lock. The shepherd stands aside only
  for the lock, and Renovate's auto-merges are bound by nothing. Either may merge a change
  to the same target while you verify. The verifier lists such a change, and a failure
  then reads `cannot-tell-which-change` (section 6).
- **After the merge,** on the lane's next tick: the history's catch-up records `merged`,
  actor `hand`, login `haynes-dev-bot[bot]`, and the lane's PR record gains `terminal`.
  From the tick after that, the verifier practises on the merge (section 6). It writes
  nothing to the history for it, reverts nothing and owes nothing.
- **In live mode (story 556):** the lane's settle gate counts the merge commit as a change
  that reaches the target until Flux has applied it and the target has settled. Until
  then, the lane does not merge to that target or its chain.

**Done when:** `$EV/check-only.txt` shows the lane line as its only refusal, and the lock
free.

### 5.5 Declare before a restart that can trip an alert

If the update restarts an app with state (Dragonfly, Mosquitto, a database client), or one
whose restart can trip a critical alert, declare it before delivery:

```bash
agent-run declare-activity start "hand run of PR 4764: <app> restarts (story 712)" --scope <ns>,<app> --ttl 45m
agent-run declare-activity list
```

Keep the id it prints. End it once the update is verified, or once a revert has recovered:
`agent-run declare-activity end <id>`. A declaration is evidence for the remediation lane,
not a mute.

### 5.6 Deliver

One update. The preflight is `scripts/upgrade-agent/hand_run_preflight.py`, the one both
hand-run nights used, now in git with its tests (`test_hand_run_preflight.py`).

```bash
python3 -I -B scripts/upgrade-agent/deliver_reviewed_pr.py \
  --repo "$REPO" --pr "$PR" --head "$HEAD" --reviewed-head "$HEAD" \
  --preflight-timeout 120 --reconcile "$KS_NS/$KS_NAME" \
  -- python3 -I -B scripts/upgrade-agent/hand_run_preflight.py "$REPO" "$PR" "$HEAD" \
  | tee "$EV/deliver.txt"
gh pr view "$PR" -R "$REPO" --json mergedAt,mergeCommit --jq '"\(.mergedAt) \(.mergeCommit.oid)"' | tee -a "$EV/deliver.txt"
```

- The preflight refuses unless the PR is open, not a draft, at `$HEAD`, made only of
  Renovate's commits, with no failing or running check, and with both required checks
  green.
- `deliver_reviewed_pr.py` then checks the required checks itself, squash-merges pinned to
  `$HEAD` (`--match-head-commit`), deletes the branch, reads the PR back as merged, and
  only then reconciles the named Kustomization.
- What each refusal means is in the "Core loop" of
  [renovate-upgrade-batches.md](renovate-upgrade-batches.md).
- A nonzero exit stops the night. Read the failed stage. Never append another merge.
- An update with no cluster effect is delivered with `--source-only` in place of
  `--reconcile`.

**Done when:** the output reads `merged thaynes43/haynes-ops#<PR> at <HEAD>` and
`reconciled <ns>/<ks>`, and `mergedAt` and the merge commit are saved. The yes is now
used.

### 5.7 Reconcile and rollout

```bash
flux get kustomization "$KS_NAME" -n "$KS_NS"    # Ready, at a revision that contains the merge commit
kubectl -n "$APP_NS" get deploy,sts,ds -o wide
kubectl -n "$APP_NS" rollout status deploy/<name> --timeout=15m   # each workload of the target
kubectl -n "$APP_NS" get pods -o wide            # new pods Ready, restarts 0
```

Then read the baseline again (5.3) and compare. Look at the logs of the new pods for
errors. Record counts and short quotes only, never a log dump: logs can hold secrets.
Follow the "Verification" section of
[renovate-upgrade-batches.md](renovate-upgrade-batches.md) for transient pull errors and
stale pods.

**Done when:** Flux is Ready at the merge, the rollout is complete, and the baseline shows
nothing new. Then go to the verdict. Start nothing else until it is `verified`.

## 6. The verdict

The verifier (story 553, `night-verify.mjs`) judges the merge. It is read-only. It
practises on merges the lane did not make, from the time it was installed. Hand merges
from then on are judged.

**Where to read it.**

1. **The lane's own verdict,** in ConfigMap `upgrade-lane-state`, on the lane's ticks after
   the merge. The tick after the merge records it in the history. The verifier first
   judges it on the tick after that. `stable` needs a second read at least 600 seconds
   later. So `verified` comes about three ticks after the merge at the earliest.

   ```bash
   kubectl -n upgrade-agent get cm upgrade-ledger -o jsonpath='{.data.events}' \
     | jq -c --argjson pr "$PR" 'select(.carrier.pr == $pr) | {type, at, actor, to}'
   kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.verify_table}' \
     | awk -v p="#$PR " 'index($0, p) == 1 {f = 1; print; next} /^#/ {f = 0} f'
   kubectl -n upgrade-agent get cm upgrade-lane-state -o jsonpath='{.data.verify}' \
     | jq --argjson pr "$PR" '.items[] | select(.pr == $pr) | {verdict, attribution, needs_person, verdict_at, why}'
   ```

2. **The same code run by hand,** read-only, so the session reads facts when it acts and
   does not wait on the lane's ticks. First write the alerts file through Grafana with
   `grafana_api_request`:
   - endpoint `/api/datasources/proxy/uid/prometheus/api/v1/alerts`, jq
     `{status, data: {alerts: [.data.alerts[] | select(.labels.severity == "critical")]}}`;
   - endpoint `/api/datasources/proxy/uid/prometheus/api/v1/status/runtimeinfo`, jq
     `.data.startTime`.

   Save them as `{"status":"success","data":{"alerts":[…]},"prometheus_start":"<startTime>"}`
   in `$EV/alerts.json`. Then:

   ```bash
   node kubernetes/main/apps/upgrade-agent/shepherd/app/resources/night-verify.mjs \
     --pr "$PR" --state "$EV/verify-state.json" --alerts-file "$EV/alerts.json" | tee -a "$EV/verify.txt"
   ```

   Run it again at least 600 seconds after it recorded the pods, with a fresh alerts file.
   This minimum is the verifier's own soak, not a wait on another actor.

The header line of a verdict reads, for example,
`#4764 <package> <from> -> <to>, merged <time> by haynes-dev-bot[bot] (practice): VERIFIED at <time>`.
If the two reads disagree, the stricter one counts (failed, then cannot tell, then not
yet), and both go in the record.

**What each verdict means for the run.**

| Verdict | Meaning | What the run does |
|---|---|---|
| `VERIFIED` | Every fact passed: Flux applied the merge; the HelmReleases are settled; the update's containers run the new version; the rollout is complete; no new critical alert on the target; stable on two reads 600 seconds apart. An update with no cluster effect is verified at once. | Record it. End the declaration. The next approved update of the night may start, from 5.1. |
| `NOT-YET` | A fact is still waiting. The `open:` line says which, and since when: Flux has not fetched the merge, the rollout, the soak, a CronJob's first run, an own image's digest pin. | Not verified. Start nothing else. Read again later. If it is still open when the window ends, record what it waits for; the lane's next nights keep judging it. After 2 nights and 8 ticks it reads `NEEDS A PERSON`: report to Tom by phone prompt. |
| `CANNOT-TELL` | A fact could not be read, and the verdict names it. Example: a workload with no running pod, because the lane cannot read Deployments (story 755). | Not a pass. Start nothing else. A session can read Deployments: record your own reading as your evidence, but it does not change the verdict. If it stays, hold and report as for `NOT-YET`. |
| `FAILED (own-regression)` | A fact failed (`crash-loop`, `image-pull`, `not-ready`, `wrong-image`, `restart-growth`, `critical-alert`, `job-failed`, `churn`, `not-applied`), and no other change reached the target, its chain or a cluster-wide part. | Revert (section 7), unless the question said a revert does not restore this update. Then hold and report. |
| `FAILED (cannot-tell-which-change)` | A fact failed, but other changes reached the same scope since the merge (listed under `other changes:`), or the list is incomplete. | **Hold and report, by Tom's ruling.** Do not revert. Stop the night. Ask Tom by phone prompt, with the failing fact and the other changes. |

## 7. A failed verdict: revert by PR

The lane's own revert is story 753 and does not exist yet. Revert by hand:

```bash
TITLE=$(gh pr view "$PR" -R "$REPO" --json title -q .title)
MERGE=$(gh pr view "$PR" -R "$REPO" --json mergeCommit -q .mergeCommit.oid)
git -C ~/repos/haynes-ops fetch origin main
git -C ~/repos/haynes-ops worktree add ~/work/revert-pr$PR -b agent/revert-pr$PR origin/main
git -C ~/work/revert-pr$PR revert --no-edit "$MERGE"
git -C ~/work/revert-pr$PR diff --stat HEAD~1 HEAD      # the same files as the merge, nothing else
git -C ~/work/revert-pr$PR push -u origin HEAD
gh pr create -R "$REPO" --base main --head agent/revert-pr$PR \
  --title "Revert \"$TITLE\"" --body-file "$EV/revert-body.md"
```

- The title must be exactly `Revert "<PR title>"`, and the body must say `Reverts #<PR>`,
  the failing fact and the verdict line. Then the history records `revert-merged`, and
  the lane remembers the version as reverted. Open it ready, not as a draft.
- Merge it once its required checks are green. `hand_run_preflight.py` refuses a revert
  (its commit is not Renovate's), so the preflight program is GitHub's own answer about
  the required checks:

  ```bash
  REV=<revert PR>; RHEAD=$(gh pr view "$REV" -R "$REPO" --json headRefOid -q .headRefOid)
  gh pr checks "$REV" -R "$REPO" --required --watch
  python3 -I -B scripts/upgrade-agent/deliver_reviewed_pr.py \
    --repo "$REPO" --pr "$REV" --head "$RHEAD" --reviewed-head "$RHEAD" \
    --preflight-timeout 120 --reconcile "$KS_NS/$KS_NAME" \
    -- gh pr checks "$REV" -R "$REPO" --required
  ```

- Read any finding the advisory Claude review has posted, and answer it on the PR. Do not
  wait for one that has not arrived; answer it in the morning.
- The merge deletes the remote branch. `deliver_reviewed_pr.py` passes `--repo`, and with
  it gh leaves the local branch and the worktree in place. Without `--repo`, gh has also
  removed the local worktree (PR 4824, 2026-11-12). So keep nothing unpushed there. After
  the merge, check `git -C ~/repos/haynes-ops worktree list`, and remove what is left:
  `git -C ~/repos/haynes-ops worktree remove ~/work/revert-pr$PR`, then
  `git -C ~/repos/haynes-ops branch -D agent/revert-pr$PR`.
- **Confirm recovery.** Nothing verifies a revert yet (story 753). Read by hand: Flux Ready
  at the revert's commit; the pods back on the old version, Ready; no new restarts on two
  reads at least 600 seconds apart; the failing fact gone (the alert resolved, the
  crash-loop ended); the baseline as before the run. Record each read with its time.
- **Stop the night.** No other update runs that night. Any other yes for that night lapses;
  the close-out says so (section 8). Cancel its scheduled item, if it has one. End the
  declaration once recovery is confirmed.
- If recovery fails, hold and report to Tom by phone prompt. Do not try a second fix at
  night.

**Done when:** the revert is merged and reconciled, recovery is confirmed by reads with
times, and the night is recorded as stopped.

## 8. The record

**On the PR**, a second comment by the session after the verdict (`gh pr comment
--body-file`), with the same rules as the owner answer: no markers, no `@`, nothing private:

- the owner answer it used (link to the first comment);
- the merge: `mergedAt` and the merge commit, from `gh pr view`;
- the reconcile and rollout times, from `$EV/times.txt` and the command output;
- the verdict: its header line, where it was read (lane tick or by hand), and its time;
- the other changes it listed;
- the declaration (id, start, end);
- the revert, if any: its PR, merge time and the recovery reads.

**On the tracker**, on the story that carries the night: a checkpoint after each update,
then the close-out. Use a table like the one in story 537:

| # | PR | Head | Merged (UTC) | Commit | Verdict, source, time |
|---|---|---|---|---|---|

**The close-out** is written when the night's run ends, before the 08:00 reminder fires. It
reads again, and records:

- each update's result: verified, still open (and what it waits for), held, reverted, or
  did not run and lapsed;
- the lane's verdict line for each update (`data.verify_table`) and its history events, as
  far as the lane's ticks have reached;
- restarts since the rollout;
- critical alerts and Flux compared with the night's baseline;
- every declaration ended, and every scheduled item finished or cancelled;
- every review finding answered, or the ones still to come named.

Once the close-out is on the tracker, cancel each morning-after reminder of that night and
read it back:

```bash
agent-run remind cancel <name>
agent-run remind show <name> -o json
```

A later read in the morning (the lane's last ticks, restarts after a few hours) goes in a
second comment; the reminder is already cancelled by then.

Times come from command output and GitHub, never from memory. Link stories with full
`work.haynesnetwork.com` links.

## 9. When this procedure ends

It ends when piece 2's approval framework is live, that is, when the lane reads a dev-env
approval (story 552, re-scoped from "reads Tom's GitHub Approve",
<https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/552>). From then
on, every yes goes through the framework, and this runbook starts no new run. A yes lasts
one night, so at most one night overlaps.

**Open point, not answered here.** Does a hand run under this runbook break the lane's
count of fourteen sustained nights (story 562)? It is a question for Tom before story 562
starts, not now.

## A PR that is not Renovate's

The piece-2 platform fixes (VolSync 0.14 to 0.17, PostgreSQL 16.15) are changes a session
makes itself. Sections 2 to 8 apply, with these differences:

- **Review:** an independent native agent review of the exact head, recorded on the PR, as
  for any session PR.
- **The check-only step** also refuses the PR's author, branch and commits, and puts it in
  lane `EXCLUDED` (not opened by Renovate). Those refusals are expected. Any other refusal
  is a stop.
- **Preflight:** `scripts/upgrade-agent/hand_run_preflight.py` refuses non-Renovate
  commits. Use `gh pr checks <n> -R thaynes43/haynes-ops --required` as the preflight
  program, as for a revert.
- **Verdict:** the history holds only Renovate's PRs as update items, so the lane's
  verifier never judges this merge. Running `night-verify.mjs` on such a PR was not tried.
  Verify by hand, with the reads of 5.7 on two reads at least 600 seconds apart, plus the
  story's own acceptance. Record it as "verified by hand, not by the verifier".

## Known limits

- **No durable start today.** Story 712 (comment 7027) asks for a durable start for each
  agreed night. That needs scheduled work, and the operator does not serve it: the API
  answers 404, and there is no dispatch-template ConfigMap (read 2026-11-13 21:06Z). Until
  it does, the coordinator session that heard the yes starts the run from its own check-in.
  That is not durable. If the session is suspended, restarted or gone at that time, nothing
  runs and the yes lapses with the night. The only durable part is the morning-after
  reminder, which tells Tom when a run did not happen or did not finish its record.
- **Two approvals for a scheduled run.** When scheduled work is served, Tom must also
  approve the exact spec with `agent-run schedule approve`, from his own token. Not known
  yet: whether a session that scheduled work starts is a summoned session (its
  `agent-run show <session> -o json` has a `caller`). If it is, it records only through its
  contract and `agent-run report`, and the coordinator copies the record to the PR and the
  tracker.
- **The hand run is invisible to the other actors.** A session cannot write ConfigMaps in
  `upgrade-agent`. So it cannot take the change lock, set `data.pause`, or write history
  events. Its check-only read and its merge are seconds apart and not atomic. The lane, the
  shepherd and Renovate's auto-merge can each merge to the same target in between, or while
  the merge is verified.
- **No hand-run events.** `upgrade-records.md` assigns the hand run's own merge-step events
  (`merging`, `would-merge`, actor `hand`) to this story. Nothing writes them, because a
  session cannot write `upgrade-ledger`. The history gets only the `merged` event it reads
  from GitHub.
- **`night-merge.mjs --merge` cannot deliver these updates:** it refuses anything outside
  lane A. Its `--check-only` serves only for the reads. `deliver_reviewed_pr.py` and
  `hand_run_preflight.py` read no lock, pause, settle gate or F4.
- **The verdict is a practice verdict.** It goes to `upgrade-lane-state` only. A failed one
  owes no revert and reverts nothing. The revert and the recovery check are by hand
  (story 753 builds them).
- **The verifier needs PR 4880 on main,** and a lane tick after Flux applies it.
  `data.verify` and `data.verify_table` do not exist before that. Before then, there is no
  verifier verdict, and this runbook cannot be followed. The history ConfigMap
  `upgrade-ledger` (story 593, merged 2026-11-13 12:22Z) did not exist yet at 21:15Z that
  day; the lane's first tick after the merge creates it.
- **The lane's verdict comes only on its ticks,** every 15 minutes from 01:00 to 05:45 New
  York, and about three ticks after the merge at the earliest. A merge near 04:45 may get
  its lane verdict only the next night; the by-hand run of the verifier covers that.
- **Pods alone cannot tell** whether a controller created all its replicas (story 755).
- **Own images:** the cluster changes only when Renovate auto-merges the digest pin, at a
  time nobody chooses, often outside the night. The verdict stays `NOT-YET` (awaiting the
  pin) until then. A pin made by a session or a person is not found.
- **Not checked:** whether the shepherd's LLM, if it reads PR comments, could take the
  owner-answer comment as leave to merge. The comment says it is not an instruction to any
  other actor.
- **The window.** [upgrade-night-lane.md](upgrade-night-lane.md) advises keeping hand runs
  out of the lane's window. That is a rule about another actor's schedule. This runbook
  follows ruling Q-548-04 instead: it runs in the merge window after the backups, and it
  reads the lock, the Jobs and the settle facts when it acts. The two documents need one
  answer when they are cross-linked.
- **Ramp PRs that need Tom.** A shepherd ramp PR is `EXCLUDED` and belongs to the shepherd,
  even when it needs Tom's yes (label `disposition:stranded`). How that yes is carried
  without GitHub approvals is not settled here.
