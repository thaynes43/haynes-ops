# Update item and event records

Two record formats for the nightly update lane:

- **`haynes.upgrade-item/v1`**: one update item, meaning one update that is available
  for one thing.
- **`haynes.upgrade-event/v1`**: one step of an attempt or one change of an item's state.

The later piece-1 stories write these records: the lane's merge step (story 752), the
read-only verifier (story 553), the revert and its drill (story 753) and the history of
every attempt (story 593). This page covers the two records, the state machine that
connects them, and how today's lane record maps onto an item.

**Status.** The format, its validator and fixtures exist. Story 593 writes the first
events, to the history ConfigMap `upgrade-agent/upgrade-ledger` (`upgrade-history.mjs`; the
runbook's section "The history of every attempt (story 593)" says what a tick writes). The lane's merge step (story 752) exists and runs
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
| `kubernetes/main/apps/upgrade-agent/shepherd/app/resources/record-format.mjs` and the four JSON files beside it | The lane's copy (story 593): the night lane runs from the scripts ConfigMap, which is built from that directory only. The copies must be byte-identical; the selftest fails on any drift. Change the format here, then copy the five files. |

## The item record

There is one item per source, item and available version. A pull request carries an
item, but it is not the item. When Renovate rebases the PR, the item stays the same. When
Renovate moves the PR to a newer version, that newer version is a new item.

| Field | Meaning |
|---|---|
| `schema` | `haynes.upgrade-item/v1` |
| `id` | `<source>:` followed by 24 hex digits of a sha256 over the source, `item.repo`, the sorted `[name, to]` pairs of `item.packages` (when there are no packages, the pair is `[item.name, available.version]`), and **the place**: the sorted paths the update changes (`carrier.files`). One update in one place. The place is a fact of the pull request alone. It is not the lane's targets, which come through the lane's rules: those keep being tuned, and each tuning would re-key open items and record a "replaced" that never happened. This differs from the reader's suggestion (sorted targets). `item.targets` stays on the item for readers. A file list GitHub cut off (more than 100 files) ends in `<file list truncated>` (`CUT_FILES`), so an id built on it says so. `itemId()` computes it, and the validator refuses any other value. Only the writer derives ids; a reader treats an id as opaque and never recomputes it. |
| `source` | The kind of update; see the sources table below. |
| `item` | What gets updated: `name` (package, image, entity, device or credential), `repo`, `packages[]` (`name`, `update`, `from`, `to`), `targets[]` (repository paths of the apps the update lands in), and `own_images[]` when the update changes the source of one of our own images. |
| `installed`, `available` | `version` (a string or null) and `kind` (`semver`, `digest`, `date`, `other`, `unknown`). `available.update_type` is the lane's update type, such as `patch`, `minor+digest` or `unknown`. |
| `behind` | `age_days`, `versions_behind`, `days_to_expiry`, and `basis`, which says what `age_days` counts from: `published`, `first-seen` (a lower bound) or `unknown`. |
| `lane` | Who acts; see the lanes table below. |
| `class`, `reason` | Why the item is in that lane, in the lane's own words. |
| `state` | Where the item is in its lifecycle; see [the state machine](#the-state-machine). |
| `approval` | **Reserved, and always empty in piece 1.** See [Approval](#approval). |
| `schedule` | `not_before`, and `window` (`first`, `last`, `tz`). Empty today. |
| `attempts[]` | One entry per attempt, built from the item's events: `n`, `actor`, `started_at`, `at` (the attempt's latest event), `result`, `recovery` (after a revert: `verified`, `failed` or null), `drill`, `evidence_url`. The newest 20 are kept; `attempts_dropped` counts the older ones, and the kept ones are numbered after them, so a PR refused on every tick cannot grow an item without limit. |
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

**Why the id is the update and its place, not the carrier.** A rebase keeps the item:
Renovate replays the same change on the same files, so the history stays attached to it. A
new PR for a version that was reverted, in the same files, maps to the same item, which is
still in state `reverted`. That is how the lane
remembers never to merge that version again (story 753). The same version in other files
is another item. `rememberedReverted(items, name, version)` in `record-format.mjs` returns
every item for that package at that version, whatever its files, that a revert touches or
touched, each with `why` it matched:
- `failed`: a revert is owed;
- `reverting`: a revert is open;
- `reverted_unverified`: the revert merged and recovery is not verified. Every revert the
  history records today stays here, because nothing verifies recovery yet;
- `reverted`: recovery verified;
- `ever-reverted`: the version was merged again after its revert (`ever_reverted` on a
  history item, or an attempt with result `reverted`).

A version never reverted returns nothing. The answer is conservative, and stories 753 and
556 decide what to do with it; nothing decides a merge with it yet. When a PR's files change
while it is open (Renovate rebased PR 4873 from two traefik apps to one after the other half
merged as PR 4876), the old item closes as replaced and the new one is observed, as for a
newer version. A change of the lane's rules that maps a file to other targets changes no
id. `change_key` is a different value and is kept in `carrier`: it also hashes
the versions the update starts from.

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
| `actor` | `kind` (`lane`, `renovate`, `shepherd`, `hand`, `person`, or `unknown` when who acted could not be established, for example another bot) and `login` (kept whenever GitHub gave one). In dry-run a merge by the `haynes-ops-bot` identity is the shepherd's, because the lane merges nothing. In live mode the lane's own merges are told apart by the change lock's last holder, which story 556 builds. |
| `attempt` | The attempt number. Every type except `observed` and `closed` requires it. |
| `drill` | True on every event of a revert drill. |
| `lane`, `class`, `reason` | For `observed`: the lane's verdict. For `merge-refused`: why the merge step refused. For a failure: the failure mode. |
| `carrier` | `repo`, `pr`, `head`, `fingerprint` at the time of the event. |
| `evidence` | `url`, `merge_commit`, `merge_call`, `pr_refusals[]`, `lane_refusals[]`, `revert_pr`, `revert_branch`, `revert_commit`, `flux_revision`, `checks[]`. |

In the fixtures, a compact event averages about 440 bytes; the largest, a merge-step
event with its merge call, is about 760.

## Rules for readers

These rules come from the format review by the dev-env v2 coordinator, who reads these
records from the portal (PR 4824, 2026-11-12), and were settled in story 593.

1. **What a v1 reader tolerates.** A new optional field or a new enum value (a state, an
   event, a source, a lane, an actor kind) stays v1. A reader ignores fields it does not
   know, and shows an enum value it does not know as written. A removal, a rename or a
   change of meaning is v2. Only the writer derives ids: a reader treats an id as opaque and
   never recomputes it.
2. **The order of one item's events.** The stored order is the order. Within one `at`, the
   event whose `from` equals the previous event's `to` comes next (`merge-attempted` and the
   `merge-refused` that follows it share a time). There is no sequence number; one can be
   added later as an optional field under rule 1.
3. **Bounds.** Every free string and every list in both schemas has a `maxLength` or
   `maxItems`. The numbers are in one place, the item schema's `$defs`, plus the inline
   ones. The writer enforces them by cutting with a visible mark (`fit()` in
   `record-format.mjs`); it never refuses a record for its length.

   | Bound | Applies to |
   |---|---|
   | `$defs/name`, 300 characters | names, versions, paths, targets, rule ids, check names, `revert_branch`, `flux_revision` |
   | `$defs/text`, 1000 characters | `reason`, `coverage.detail`, `findings[]`, `merge_call`, refusal lines, `evidence.checks[]`, `carrier.title` |
   | `$defs/names` and `$defs/texts`, 100 entries | lists of names and of texts |
   | `item.packages`, 100; `carrier.files`, 300; `lane_detail.checks.main_newer`, 100 | lists of objects |
   | `attempts[]`, 20 | the newest kept, `attempts_dropped` counts the rest |
   | inline | `repo` 200, `class` 100, `actor.login` 100, `update_type` 100, `url` 1000, and fixed-length SHAs, ids and times |

   A cut string ends in `…`; a cut list of strings ends in an entry `… (+N more)`. A
   patterned field (a class, a timestamp, a repo, a SHA, an id) is never cut: a value that is
   too long is left as it is, so validation refuses it with the real reason.
4. **`lane_detail` is not part of the contract**, apart from `lane_detail.checks`. Its other
   shapes are the lane's own (`lane-classify.mjs`, `run-night.mjs`, `night-merge.mjs`) and
   may change without a version change. A reader shows it as raw text at most. A field a
   pane should show belongs outside `lane_detail`.
5. **Secrets in free text.** The writer of the history (`upgrade-history.mjs`) passes every
   free-text field of an event, and each item's name, through the same shape check the
   lane's PR record uses (`secretShape()` in `night-merge.mjs`: GitHub, Slack and AWS
   tokens, private keys, JWTs, URLs with credentials, bearer values, password assignments,
   private network addresses and in-cluster names). The fields checked are `reason`,
   `evidence.merge_call`, `revert_branch`, `flux_revision`, `url`, the lists `checks`,
   `pr_refusals` and `lane_refusals`, `actor.login`, and the item's name. On a hit the text
   is replaced by a fixed placeholder (`[redacted: the text was shaped like a credential, a
   private address or an in-cluster name]`; for `evidence.url`, `https://redacted.invalid/`,
   so the field keeps its shape). The tick logs an ERROR that names the field, never the
   text.

   One exemption, for the private-address shapes only: a string that is exactly one of the
   item's own package versions (from or to, as written or without the pin operator) is not
   an address. PR 4757 updates `nvidia-curand-cu12` from `==10.3.2.106`, which reads like
   10.x.x.x. The lane's PR record uses the same check with the same exemption. Only a
   version that is itself shaped like an address is exempt, so a short version cannot hide
   part of a real address.

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
| `reverted` | settled | Reverted, and recovery verified. The lane never merges the version again; the lane leaves it only by `drill-cleared` (after a drill), a person by `resolved`. A merge by anyone but the lane is recorded as it happened (`merged`, to `merged_unverified`), so the history says the version runs again. |
| `escalated` | attempt | The lane has no moves left. A person owns the item (story 554). |
| `approved`, `scheduled` | reserved | Reserved for piece 2 and refused in piece 1. |

**Events.** In the table, `(new)` means the item had no earlier event.

| Event | From, to | Who may record it | Requires | Story |
|---|---|---|---|---|
| `observed` | (new), needed, held, blocked, unknown, closed to needed, held, blocked, unknown | lane | lane, class, reason | 593 |
| `closed` | needed, held, blocked, unknown to closed | lane, renovate, shepherd, hand, person, unknown | reason | 593 |
| `merge-attempted` | needed to in_progress (opens an attempt) | lane, only for lane A-night; hand | attempt, lane, carrier | 752 |
| `merge-refused` | in_progress to needed or blocked, before any merge call; reverting to reverting (the revert's own merge was refused) | lane, hand | attempt, class, reason | 752, 753 |
| `merge-failed` | in_progress or merge_unknown to needed or blocked: the call was made and did not merge | lane, hand | attempt, class, reason, merge_call | 752 |
| `merge-unknown` | in_progress to merge_unknown: the call was made and its outcome could not be read | lane, hand | attempt, class, reason, merge_call | 752, 754 |
| `merged` | in_progress or merge_unknown to merged_unverified | lane, hand | attempt, carrier, merge_commit | 752 |
| `merged` | (new), needed, held, blocked, unknown to merged_unverified (opens an attempt) | renovate, shepherd, hand, person, unknown | the same | 593 |
| `merged` | reverted to merged_unverified (opens an attempt): a version remembered as reverted merged again | renovate, shepherd, hand, person, unknown (never the lane) | the same | 593 |
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
- Three edges take an item out of `reverted`: `drill-cleared` (lane or hand, only for a
  drill), `resolved` (a person only), and `merged` by anyone but the lane (it records that
  the version runs again). The lane leaves `reverted` only by `drill-cleared`, so it never
  merges a remembered version.
- An item first seen at its close is not recorded: `closed` has no edge from first sight.
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
| `terminal` | `state` | events (story 593): the item's terminal event as the history recorded it (`type` merged or closed, `at`, `state`, `by`, `item`, `event`, `merge_commit`); null on an open PR. Not converted: the state and attempts come from the item's events. |

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
`schedule`, `last_attempt`, `retry`, `terminal` (story 593), `updated`). Those fields map by the rows above. The
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

- **The backfill names items by the lane's route (story 593 review, 2026-11-12).** The
  backfill fixture `seq-backfill-renovate-automerge.json` first named PR 4817's item from
  its title (`ghcr.io/stakater/charts/reloader` at `v2.2.20`, no packages). The lane names
  the same update from the PR's package table (`2.2.20`, one package), so the two routes
  gave different ids: 29 of 40 recent merged Renovate PRs differed. A reverted version
  recorded on a backfilled item would then not be the item the lane computes when Renovate
  reopens it. The fixture now follows the lane's route: the package table, labels and files
  through `fromLaneRecord()`. With the place (the changed paths) in the id, its item id is
  now `renovate-pr:396fd61c012d314c5103c24b` and its event id `ev:7f6776e5338a202d3ce67ed9`,
  and its carrier lists the PR's files. The
  history names an item from a title only when the PR's body cannot be read, and marks it
  `basis: "title"` in its item description, which is not part of the item record.
- **Size (story 593).** The history stores each event without `schema`, without `id` and
  without a null `actor.login`; `unpack()` in `upgrade-history.mjs` restores them. A merge
  by someone else carries no URL, lane or class (the actor and the PR say them). A stored
  line is still about 440 bytes, plus about 130 bytes of item description. The 768 KiB cap
  therefore holds about 1,400 single-event items, or about 75 days at an estimated 20
  events a day, not the 3,000 per MiB first planned.
- **Repeated refusals (story 593).** In live mode, a refusal at the same head for the same
  reasons as the item's last refusal is recorded once.
- **Settled in story 593 (the reader's points, 2026-11-13).** The place is in the item id
  (the sorted changed paths, not the targets the reader suggested, so that the lane's rules
  cannot re-key an item), so two PRs for the same package and version in different files
  (PRs 4376 and 4377) are two items. A merge of a version remembered as reverted is an event (`merged`, from
  `reverted`, by anyone but the lane). The shepherd is an actor on `closed`. An actor that
  cannot be established is `unknown`, its login kept. An item first seen at its close is
  not recorded. Every string and list is bounded, and `attempts[]` keeps the newest 20.
- **Not taken:** a per-item sequence number (the order rule is enough while the store is
  the only source and is in write order); the five piece-2 points of the review
  (transport, a feed-level freshness time, delivery of the machine file, a key for one
  thing across versions, the shape of the approval reference); checking the change lock's
  last holder (story 556).
- The sources the lane does not write yet have only the generic item shape. Their adapter
  stories may need more fields.

### Open points for the format (story 553, the verifier)

The verifier (`night-verify.mjs`) writes `verified`, `verification-failed`, `escalated` and
`awaiting-pin` for the lane's own merges, behind `night.verify.write_history_events` (git
ships `false`). It uses only existing edges; nothing here was added to the format.

**What the format lets a verdict on someone else's merge record.** All four. The edges
constrain the verifier's actor (`lane`), not who merged, so on an item that Renovate's or the
shepherd's `merged` event put in `merged_unverified` (attempt opened by `renovate`,
`shepherd`, `person` or `unknown`) the lane may legally record `verified` (to `done`),
`verification-failed` (to `failed`), `escalated` and `awaiting-pin`. The verifier's practice
records show this: on 2026-11-13 each would-write `verified` on #4869, #4870, #4871, #4873 and
#4874 is legal. The verifier does not write them, and the history refuses a verifier event on
an item whose current attempt the lane did not make.

**What the format lacks for that:**

1. **A practice or observed verdict.** No field says that a verdict was made on a merge the
   lane did not make. `recorded_by` is `lane` or `backfill`; the actor of `verified` is the
   verifier, so a reader cannot tell "the lane verified its own merge" from "the lane watched
   Renovate's merge roll out" except by reading the attempt's actor.
2. **`failed` means a revert is owed, whoever merged.** A `verification-failed` on Renovate's
   merge would make the lane owe a revert of another actor's merge, and
   `rememberedReverted()` counts `failed` as a remembered version. There is no state for
   "verification failed, and the revert is not the lane's".
3. **Hold and report.** Q-548-04 says a failure with another change on the same target is
   "cannot tell which change did it": no automatic revert. The nearest edge is `escalated`
   (class `cannot-tell-which-change`), which loses that verification failed. There is no
   state for "failed, cause not attributed".
4. **The other changes.** The event has no field for them; the verifier puts them in
   `evidence.checks` as one line (`other changes: <sha8> #<pr>, …` or `no other change`).
5. **Failure classes.** `verification-failed`'s meaning names six classes; the story also
   names crash loops and image-pull failures, which the verifier records as `crash-loop` and
   `image-pull` (the schema's class pattern allows them; the meaning text does not list them).
6. **Time boxes.** `awaiting_pin` and `pin-timeout` are described as time-boxed, and
   `escalated` covers "verification cannot be read". Under Q-548-04 (facts, not clocks) the
   verifier ends the pin wait on facts (a failed build, or three pushes to main after the build
   with no pin) and writes nothing for `cannot-tell`. When a lasting `cannot-tell` should
   become `escalated` has no fact yet.
- `kind` is inferred from the version text. `semver` means the version starts with dotted
  numbers. Nothing infers `date` yet.
