# Update item and event records

Two record formats for the nightly update lane:

- **`haynes.upgrade-item/v1`**: one update item, meaning one update that is available
  for one thing.
- **`haynes.upgrade-event/v1`**: one step of an attempt or one change of an item's state.

The later piece-1 stories write these records: the lane's merge step (story 752), the
read-only verifier (story 553), the revert and its drill (story 753) and the history of
every attempt (story 593). This page covers the two records, the state machine that
connects them, and how today's lane record maps onto an item.

**Status.** The format, its validator and fixtures exist. Nothing writes these records
yet; story 593 writes the first events. The lane's merge step (story 752) exists and runs
in dry-run; [Merge-step verdicts and the change lock](#merge-step-verdicts-and-the-change-lock)
says which of its verdicts become events. Metrics, counting rules and the dashboard pane
belong to piece 2 (story 591 and question Q-547-07) and are not defined here. Tracker:
[story 592](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/592).

## Files

| File | What it is |
|---|---|
| `scripts/upgrade-agent/schema/upgrade-item.v1.schema.json` | JSON Schema of an item |
| `scripts/upgrade-agent/schema/upgrade-event.v1.schema.json` | JSON Schema of an event |
| `scripts/upgrade-agent/schema/upgrade-states.v1.json` | The state machine, as data: states, events, legal edges, who may record each edge |
| `scripts/upgrade-agent/schema/lane-v1-to-item-v1.json` | Where each field of the lane record `haynes.upgrade-lane/v1` lands in an item |
| `scripts/upgrade-agent/schema/record-format.mjs` | Validator and helpers: `checkItem`, `checkEvent`, `applyEvent`, `replay`, `itemId`, `eventId`, `fromLaneRecord` |
| `scripts/upgrade-agent/fixtures/upgrade-records/` | Valid and invalid fixtures, one file per case |
| `scripts/upgrade-agent/fixtures/lane-tick-records-20261111.json` | Records and merge-step verdicts written by the real `run-night.mjs` in the stub ticks of `lane-night-dryrun-selftest.mjs` (synthetic world, not cluster data). The mapping test converts them. |
| `scripts/upgrade-agent/record-format-selftest.mjs` | The selftest; CI runs it in `Upgrade Agent Selftests - Night lane` |

## The item record

There is one item per source, item and available version. A pull request carries an
item, but it is not the item. When Renovate rebases the PR, the item stays the same. When
Renovate moves the PR to a newer version, that newer version is a new item.

| Field | Meaning |
|---|---|
| `schema` | `haynes.upgrade-item/v1` |
| `id` | `<source>:` followed by 24 hex digits of a sha256 over the source, `item.repo` and the sorted `[name, to]` pairs of `item.packages`. When there are no packages, the pair is `[item.name, available.version]`. `itemId()` computes it, and the validator refuses any other value. |
| `source` | The kind of update; see the sources table below. |
| `item` | What gets updated: `name` (package, image, entity, device or credential), `repo`, `packages[]` (`name`, `update`, `from`, `to`), `targets[]` (repository paths of the apps the update lands in), and `own_images[]` when the update changes the source of one of our own images. |
| `installed`, `available` | `version` (a string or null) and `kind` (`semver`, `digest`, `date`, `other`, `unknown`). `available.update_type` is the lane's update type, such as `patch`, `minor+digest` or `unknown`. |
| `behind` | `age_days`, `versions_behind`, `days_to_expiry`, and `basis`, which says what `age_days` counts from: `published`, `first-seen` (a lower bound) or `unknown`. |
| `lane` | Who acts; see the lanes table below. |
| `class`, `reason` | Why the item is in that lane, in the lane's own words. |
| `state` | Where the item is in its lifecycle; see [the state machine](#the-state-machine). |
| `approval` | **Reserved, and always empty in piece 1.** See [Approval](#approval). |
| `schedule` | `not_before`, and `window` (`first`, `last`, `tz`). Empty today. |
| `attempts[]` | One entry per attempt, built from the item's events: `n`, `actor`, `started_at`, `at` (the attempt's latest event), `result`, `recovery` (after a revert: `verified`, `failed` or null), `drill`, `evidence_url`. |
| `first_seen`, `observed_at` | The collector's clock, in UTC. |
| `coverage` | `status` is `read` (the whole item was read), `partial` (some facts failed to read) or `unread` (nothing could be read; the state must then be `unknown`). `detail` says what failed. |
| `carrier` | The pull request that carries the item, as last seen: `repo`, `pr`, `head`, `title`, `fingerprint`, `fingerprint_basis`, `commit_count`, `change_key`, `files` (its changed files) and `head_committed_at`. Required for PR sources. |
| `lane_detail` | The lane's own detail, copied exactly as the lane wrote it: `rules`, `findings`, `checks`, `cluster_effect`, `cluster_effect_wait`, `via_components`, `cluster_wide`, `gates`, `backup`, `last_attempt` and `retry` (story 549). Their shapes are set by `lane-classify.mjs`, `run-night.mjs` and `night-merge.mjs`. `checks` is typed field by field (`expected`, `missing`, `failing`, `pending`, `stale`, `ran_at`, `reference_time`, `main_newer`), because the item's blocked state and the merge step depend on it. The others are kept as written. |

**Sources.** In piece 1, the lane writes three sources. Each one has a valid fixture taken
from the 2026-11-09 queue.

| Source | What it is | Written in piece 1 by |
|---|---|---|
| `renovate-pr` | A Renovate PR in haynes-ops | the lane |
| `image-pin` | A Renovate PR whose update types are only `digest` or `pinDigest` | the lane |
| `own-image` | A Renovate PR that changes the source of one of our own images (`own_images` in `upgrade-rules.json`). The cluster changes only after the image's digest pin merges. | the lane |
| `renovate-pending`, `platform`, `ha-update`, `unifi-firmware`, `credential` | Named in the discovery report; each one needs its own adapter | nobody yet |

**Lanes.**

| Lane | Meaning |
|---|---|
| `A-night` | The night lane may merge the item. |
| `B-agent` | An agent has to look at it. |
| `B-owner` | Tom has to decide. |
| `excluded` | The lane never touches it. |
| `auto` | Renovate auto-merges it. |
| `hand` | A session delivers it by hand. |
| `none` | Shown for visibility only. |
| `unknown` | The lane could not decide. |

**Why the id ignores the carrier.** A rebase keeps the item, so the history stays
attached to it. A new PR for a version that was reverted maps to the same item, which is
still in state `reverted`. That is how the lane remembers never to merge that version
again (story 753). `change_key` is a different value and is kept in `carrier`: it also
hashes the changed files and the versions the update starts from.

### Approval

By Tom's ruling (saga 547 comment 6922), every approval goes through the dev-env v2
approval framework in piece 2, and piece 1 reads no approval. For that reason, `approval`
must be an empty object, and the validator refuses an item that has anything in it. The
state `approved` and the event `approval-recorded` are reserved and refused in the same
way. Piece 2 defines the approval fields when it lifts the reservation. A GitHub review is
not an approval, and the format has no field for one.

## The event record

An event is written in two cases: when an item's state changes, and when an attempt takes
a step. A tick that reads an item and finds it unchanged writes no event; only the item's
`observed_at` moves. This keeps the history small.

| Field | Meaning |
|---|---|
| `schema` | `haynes.upgrade-event/v1` |
| `id` | `ev:` followed by 24 hex digits of a sha256 over `item_id`, `type`, `at` and `attempt`. If a write is retried after a failure, the event keeps the same id, so the history can drop the duplicate. |
| `item_id` | The item the event belongs to. |
| `type` | One of the events in the next section. |
| `from`, `to` | The item's state before and after the event. `from` is null on the item's first event. |
| `at` | When it happened. `recorded_at` is when it was written, if that was later (a backfill). |
| `recorded_by` | `lane` (written as it happened) or `backfill` (rebuilt later from GitHub, story 593). |
| `actor` | `kind` (`lane`, `renovate`, `shepherd`, `hand`, `person`) and `login`. |
| `attempt` | The attempt number. Every type except `observed` and `closed` requires it. |
| `drill` | True on every event of a revert drill. |
| `lane`, `class`, `reason` | For `observed`: the lane's verdict. For `merge-refused`: why the merge step refused. For a failure: the failure mode. |
| `carrier` | `repo`, `pr`, `head`, `fingerprint` at the time of the event. |
| `evidence` | `url`, `merge_commit`, `merge_call`, `pr_refusals[]`, `lane_refusals[]`, `revert_pr`, `revert_branch`, `revert_commit`, `flux_revision`, `checks[]`. |

In the fixtures, a compact event averages about 440 bytes; the largest, a merge-step
event with its merge call, is about 760.

## The state machine

`upgrade-states.v1.json` holds the machine as data. An event is legal only if all of these
hold:

- it matches an edge of the machine: its type, its `from` state and its `to` state;
- its actor kind is one that edge allows;
- it carries the fields its type requires.

When an edge applies to the lane, it can also limit which lanes the item may be in.

**States.**

| State | Phase | Meaning, and what is owed |
|---|---|---|
| `needed` | open | An update is available and nothing is in flight. The lane starts a merge only from this state, and only for lane `A-night`. |
| `held` | open | A deliberate hold applies (a rule in `.renovate/holds.json5`, or a vet HOLD on the current head). |
| `blocked` | open | Something about the item must change first: a failing or incomplete check, incomplete facts, or a refusal that needs a new head. |
| `unknown` | open | The item could not be read. |
| `closed` | open | No longer offered. If the carrier reopens, the item is observed again. |
| `in_progress` | attempt | The lane holds the change lock and its merge step is running (lock phase `taken`, then `merging`). |
| `merge_unknown` | attempt | The merge call was made and its outcome could not be read; GitHub may have merged. The lock keeps its hold. Owed: read the PR. |
| `merged_unverified` | attempt | Merged, but nothing has verified it yet. Backfilled merges stay in this state. |
| `awaiting_pin` | attempt | The source of one of our own images merged. Verification waits, within a time box, for the image's digest pin. |
| `done` | settled | Merged and verified. |
| `failed` | attempt | Verification failed, or a drill asked for a revert. A revert is owed. |
| `reverting` | attempt | A revert PR is open. Its merge is owed. |
| `reverted_unverified` | attempt | The revert merged. A recovery check is owed. |
| `reverted` | settled | Reverted, and recovery verified. The version is never merged again; the only ways out are `drill-cleared` (after a drill) or `resolved` (by a person). |
| `escalated` | attempt | The lane has no moves left. A person owns the item (story 554). |
| `approved`, `scheduled` | reserved | Reserved for piece 2 and refused in piece 1. |

**Events.** In the table, `(new)` means the item had no earlier event.

| Event | From, to | Who may record it | Requires | Story |
|---|---|---|---|---|
| `observed` | (new), needed, held, blocked, unknown, closed to needed, held, blocked, unknown | lane | lane, class, reason | 593 |
| `closed` | needed, held, blocked, unknown to closed | lane, renovate, hand, person | reason | 593 |
| `merge-attempted` | needed to in_progress (opens an attempt) | lane, only for lane A-night; hand | attempt, lane, carrier | 752 |
| `merge-refused` | in_progress to needed or blocked, before any merge call; reverting to reverting (the revert's own merge was refused) | lane, hand | attempt, class, reason | 752, 753 |
| `merge-failed` | in_progress or merge_unknown to needed or blocked: the call was made and did not merge | lane, hand | attempt, class, reason, merge_call | 752 |
| `merge-unknown` | in_progress to merge_unknown: the call was made and its outcome could not be read | lane, hand | attempt, class, reason, merge_call | 752, 754 |
| `merged` | in_progress or merge_unknown to merged_unverified | lane, hand | attempt, carrier, merge_commit | 752 |
| `merged` | (new), needed, held, blocked, unknown to merged_unverified (opens an attempt) | renovate, shepherd, hand, person | the same | 593 |
| `awaiting-pin` | merged_unverified to awaiting_pin | lane | attempt | 553 |
| `verified` | merged_unverified, awaiting_pin to done | lane, hand | attempt | 553 |
| `verification-failed` | merged_unverified, awaiting_pin to failed | lane, hand | attempt, class, reason | 553 |
| `drill-requested` | done to failed (opens a drill attempt) | lane | attempt, reason, drill | 753 |
| `revert-opened` | failed to reverting | lane, hand, person | attempt, revert_pr | 753 |
| `revert-merged` | reverting to reverted_unverified | lane, hand, person | attempt, revert_commit | 753 |
| `revert-merged` | failed, merged_unverified, awaiting_pin, done to reverted_unverified | hand, person (a revert nobody recorded as opened) | the same | 593 |
| `recovery-verified` | reverted_unverified to reverted | lane, hand | attempt | 753 |
| `recovery-failed` | reverted_unverified to escalated | lane, hand | attempt, class, reason | 753, 554 |
| `escalated` | merge_unknown, merged_unverified, awaiting_pin, failed, reverting to escalated | lane | attempt, class, reason | 553, 753, 754 |
| `resolved` | escalated to done, reverted or closed | hand, person | attempt, reason | 554 |
| `resolved` | reverted to needed or closed (releases a remembered version, for example after a revert that was not the version's fault) | person only | attempt, reason | 753 |
| `drill-cleared` | reverted to needed, only when the current attempt is a drill | lane, hand | attempt, drill | 753 |

**Attempts.** An attempt opens with `merge-attempted`, with a `merged` that someone else
made, or with `drill-requested`. Each of these adds an entry to `attempts[]` with the next
number. Every later event names that attempt and updates its `at`, its `result` (where
the edge sets one) and its `recovery`. An attempt result is one of:

- `in-progress`, `merge-refused`, `merge-failed`, `merge-unknown`, `merged`, `awaiting-pin`, `verified`;
- `failed-verification`, `drill`, `reverted`, `escalated`.

A drill gets an attempt of its own, with `drill: true`, so a count of real attempts can
leave it out.

**Safety properties the selftest checks:**

- An attempt starts only from `needed`. The lane may start one only for lane `A-night`.
- The lane can record a merge only after its merge step has started: from `in_progress`,
  or from `merge_unknown` when a later read finds the PR merged.
- `merge_unknown` is reached only from a merge call in progress, and left only by reading
  the PR (`merged`, `merge-failed`) or by `escalated`.
- Every lock phase `night-merge.mjs` writes goes with item states, and a merge in flight is
  never under a released lock.
- Exactly two edges take an item out of `reverted`: `drill-cleared` (lane or hand, only
  for a drill) and `resolved` (a person only).
- `drill-cleared` is refused unless the attempt was a drill, so a real revert stays
  remembered.
- The lane takes no action on a held item.

## Merge-step verdicts and the change lock

The merge step (story 752, `night-merge.mjs`) writes one verdict per tick to
`summary.merge_step`, and a short copy (`at`, `mode`, `chosen`, `verdict`, `pr_refusals`,
`lane_refusals`, `merge_call`) to `last_attempt` on the record it ran on.
`eventsFromMergeStep()` turns a verdict into history events, using the full verdict,
because only that one carries the merge commit.

| Verdict | Events |
|---|---|
| `not-run`, `would-merge`, any dry-run verdict | none: nothing was attempted. The verdict stays in `lane_detail.last_attempt`. |
| `refused`, live, with any lane refusal (class not allowlisted, token, not chosen, lease, window, gate age, lock move, a settle fact or a yield to a shepherd Job, story 551) | none: the lane declined, which is not an attempt on the item |
| `refused`, live, PR refusals only | `merge-attempted`, then `merge-refused` (class `merge-step-refused`, to `needed`, the refusals in `evidence`) |
| `merged` | `merge-attempted`, then `merged` (merge commit and time from the verdict). With no merge commit read, `merge-unknown` instead. |
| `merge-failed` | `merge-attempted`, then `merge-failed` (class `merge-call-failed`, to `needed`) |
| `merge-unknown` | `merge-attempted`, then `merge-unknown` (class `merge-outcome-unread`) |
| `merging`, `would-merge` from the hand run (`night-merge.mjs --merge`) | written by the hand run itself, actor `hand` (story 712) |

A refused verdict goes to `needed`, not `blocked`: the open states follow the lane's own
reading of the PR on the next tick, and the refusal stays in the attempt. Event ids
depend only on the item, the type, the time and the attempt, so a write retried with the
same verdict is a duplicate the history can drop. `applyEvent()` refuses the events unless
the item is `needed` and in lane `A-night`.

**The change lock** (`upgrade-agent/upgrade-change-lock`). Its phases go with item states
(`lock_phases` in the machine file):

| Lock phase | Item state |
|---|---|
| `taken`, `merging` | `in_progress` |
| `merged-awaiting-verification` | `merged_unverified` |
| `merge-unknown` | `merge_unknown` |
| `released` | anything else: no merge call is in flight |

A tick that takes over an expired lock reads `took_over_phase`:

- `taken`: the call was never made. Record `merge-refused` with class `attempt-abandoned`.
- `merging`: the item is still `in_progress`, and the call may have been made. Read the
  PR: if it merged at that head, record `merged`; if it is open at that head,
  `merge-failed`; if it cannot be read, `merge-unknown`.
- `merge-unknown`: the item is already `merge_unknown`. Read the PR: `merged` or
  `merge-failed` as above. If it still cannot be read, write no event, because the state
  already says the read is owed. Once its time box has passed, record `escalated`.

Until story 553 holds the lock through verification, a merged item outlives its
post-merge lease.

Tick-level summary keys are not item fields: `merge_step` (the events above),
`change_lock` (the phases above), `token_permissions` and `live_classes` (both become lane
refusals), and `owed_work` (the attempt states `merged_unverified`, `awaiting_pin`,
`failed`, `reverting`, `reverted_unverified` and `merge_unknown`).

## Mapping from `haynes.upgrade-lane/v1`

`lane-v1-to-item-v1.json` lists every field the lane writes today, together with where it
lands in the item. `fromLaneRecord()` performs the mapping. The selftest checks it against
three sources:

- the classifier's output for the 30 PRs of the 2026-11-09 queue;
- the records in the captured tick fixture;
- `run-night.mjs` itself, read for the fields it sets (`r.<field> =`, `subjectRec.<field> =`
  and `r.checks.<field> =`).

It fails if any written field has no row in the mapping, if any `checks` field is missing
from the item schema, or if any row names a field the lane no longer writes. So a new lane field cannot be lost silently: whoever adds it must
also add a row that says where it goes.

| Lane field | Item field | How |
|---|---|---|
| `schema` | `lane_detail.from_schema` | copied |
| `pr`, `head`, `fingerprint`, `fingerprint_basis`, `commit_count`, `change_key`, `title`, `files`, `head_committed_at` | `carrier.*` | copied |
| `package` | `item.name` | copied; `<repo>#<pr>` when the PR has no package table |
| `from`, `to` | `installed.version`, `available.version` | copied; the `kind` is inferred |
| `packages`, `targets`, `own_images` | `item.packages`, `item.targets`, `item.own_images` | copied |
| `update_type` | `available.update_type` | copied |
| `lane` | `lane` and `state` | mapped (see below) |
| `class`, `reason` | `class`, `reason` | copied |
| `rules`, `findings`, `checks`, `cluster_effect`, `cluster_effect_wait`, `via_components`, `cluster_wide`, `backup`, `gates` | `lane_detail.*` | copied (`backup` and `gates` exist on lane-A records only; `checks.main_newer` is set by the tick) |
| `updated` | `observed_at` | copied |
| `preapproval` | `approval` | renamed; must stay empty |
| `schedule` | `schedule` | copied (empty today) |
| `last_attempt` | `lane_detail.last_attempt` | copied: the tick's merge-step verdict for the PR it ran on, `{}` on the others. It is not `attempts`: that list is rebuilt from events, and a live verdict becomes events as the table above says. |
| `retry` | `lane_detail.retry` | copied (story 549): the stalled-check retry for the PR as the tick read it: `state`, `why`, `count_7d`, `limits` and `history` (`head`, `at`, `status`, `outcome`, `next_head`) from the ledger `data.retry_ledger`. A retry is a Renovate rebase, not an attempt on the item, so it writes no event. |

**Lane and state.**

| Lane verdict | Item lane |
|---|---|
| `LANE-A` | `A-night` |
| `LANE-B-AGENT` | `B-agent` |
| `LANE-B-OWNER` | `B-owner` |
| `EXCLUDED` | `excluded` |
| `BLOCKED` | `A-night`, because the classifier takes the strictest finding, so a BLOCKED PR has no B or EXCLUDED finding and only its blocker keeps it out of lane A |
| `BLOCKED` with class `facts-incomplete` | `unknown`, with coverage `partial` |

The state follows from the verdict and the findings:

- A `BLOCKED` verdict gives state `blocked`.
- Otherwise, a `hold` or `vet-hold` finding gives state `held`, even when the top class is
  something else (for example PR 4376: class `major`, held).
- Everything else gives state `needed`.

A gate blocker from `run-night.mjs` (outside the window, a stale backup) does not change
the state. It describes tonight's conditions, not the item. A failure on the head that
follows a stalled-check retry (story 549) does: the tick sets class
`check-failing-after-retry` on a `BLOCKED` record, which maps to state `blocked` like any
other `BLOCKED` verdict.

**The record on the PR (story 549).** The lane also publishes each record as one comment on
its PR: the marker `<!-- upgrade-lane v1 -->` and a JSON block holding the fields
`night.pr_record.fields` in `upgrade-rules.json` names (`schema`, `pr`, `head`,
`fingerprint`, `lane`, `class`, `reason`, `rules`, `checks`, `backup`, `preapproval`,
`schedule`, `last_attempt`, `retry`, `updated`). Those fields map by the rows above. The
runbook's section "PR records and the stalled-check retry" says how the comment is written
and read.

**Source.** If the record lists any own image, the source is `own-image`. If not, and
every update-type token is `digest` or `pinDigest`, the source is `image-pin`. Otherwise
it is `renovate-pr`.

## Validation

`record-format.mjs` has no dependencies. It contains a hand-written validator for the part
of JSON Schema 2020-12 that these schemas use:

- `type`, `const`, `enum`, `properties`, `required`, `additionalProperties`, `items`;
- `minItems`, `maxItems`, `minLength`, `maxLength`, `pattern`, `format` (`date-time` only);
- `minimum`, `maximum`, `minProperties`, `maxProperties`;
- `$ref` (within the document, or `<$id>#/...` into the other schema);
- `allOf`, `anyOf`, `if`/`then`/`else`.

If a schema uses any other keyword, `schemaProblems()` refuses it rather than skipping it,
so a schema edit cannot rely on a check that does not run. After the schema passes,
`checkItem()` and `checkEvent()` apply the rules a schema cannot express: the derived ids,
the reserved names, the legal edges, the required fields, and the order of attempts. In a
chain of events, `applyEvent()` and `replay()` also check that each event starts from the
item's current state.

```bash
node scripts/upgrade-agent/record-format-selftest.mjs     # offline; Node 24
```

**Adding to the format:**

- **A new field** goes in the schema, plus a fixture that uses it.
- **A new event or state** goes in the machine file and in the matching schema enum; the
  selftest checks that they agree. It also needs a valid sequence fixture that uses it.
- **A new lane field** needs a row in the mapping file.

## Differences from the discovery report (ui.md section 3)

- **`id`:** derived from the update, not from `repo#pr` plus the fingerprint. The PR
  number, head and fingerprint are kept in `carrier`.
- **States:** added `closed`, `merged_unverified` (named in section 4 of the report),
  `awaiting_pin` (named in story 553), `reverting`, `reverted_unverified` and
  `escalated`, so that every state says what is owed next. `approved` and `scheduled` are
  reserved.
- **`approval`:** has no fields. The report's `how: github-review` is gone, per the
  ruling.
- **The report's `updated` field:** became `observed_at`.
- **`update_type`:** moved into `available`.
- **Attempt entries:** gained `n`, `started_at`, `recovery` and `drill`.
- **Lanes:** gained `unknown`.
- **New fields:** `carrier` and `lane_detail`.

## Open points

- The fixture events average about 440 bytes each, about 760 at most. Story 593 planned
  on about 3,000 compact events per MiB, about 350 bytes each. It can store them more
  compactly (for example, without the `schema` key on each line) or rotate sooner.
- In live mode, a PR the merge step refuses for its own reasons on every tick opens one
  attempt per tick until it changes. Story 593 may collapse repeated refusals of the same
  head.
- The sources the lane does not write yet have only the generic item shape. Their adapter
  stories may need more fields.
- `kind` is inferred from the version text. `semver` means the version starts with dotted
  numbers. Nothing infers `date` yet.
