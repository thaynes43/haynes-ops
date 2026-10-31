# OP324 producer identity — held source handoff

Canonical work: OpenProject [#324](https://work.haynesnetwork.com/work_packages/324),
work order OVERNIGHT-PAGESPRODUCER-324-B, journals 3959/3960/3962.
Owner: haynes-ops-1031-064449; lead reviewer: dev-env-1029-132039.
Branch: `agent/haynes-ops-1031-064449`.
Pinned main: `a036fda0c24b146c52ee199d1f9924d29006523e`.

## Contract implemented

`alertIdentity` is an optional object with the exact string `fingerprint` and
original string `startsAt` from the raw singleton unit. The dedicated
`REMEDIATE_ALERT_IDENTITY` and `ESCALATE_ALERT_IDENTITY` environment inputs carry
JSON independently of the legacy `alert` context string. That context retains
its existing projection, control-character cleaning and 900-character clamp.
Missing or malformed identity inputs are omitted, never inferred or backfilled.

Singleton remediation and failed-diagnosis responder orders receive it. Promotion
through order-status copies the persisted parent identity for explicit escalation,
failed remediation, orphan sweep and session-launch close-out. Legacy parents
remain absent. Storm/aggregate units and esc-rem-watchdog orders omit it. Terminal
key reuse replaces the entry with its new identity (or absence) and creation time.
Existing object rewriters require no source changes and retain the field.

## Evidence

Final validation was serial, offline and bounded:

- `scripts/upgrade-agent/alert-identity-selftest.sh`: 19 cases pass. Uses extracted
  shipped setup/handoff/write blocks, fake kubectl and synthetic ConfigMap data.
  Exercises unusual Unicode/control/quote fingerprint bytes, a startsAt over
  2,000 characters with a trailing newline, unchanged legacy context, producers,
  promotion paths, exclusions, dedup, key reuse and all seven listed rewriters.
- The same 19 cases against an archive of pinned main each fail specifically on
  absent exact identity. Rewriter/exclusion cases first require a bound singleton
  control; these are end-to-end contract tests, not claims that main's existing
  object round-trip or exclusion logic was itself broken.
- `scripts/work-order-lane-selftest.sh` and the existing upgrade-agent
  coordination-sig, held-audit, shepherd-verdict, stale-corpse and triage-claim
  selftests: each run once in the final serial regression pass; all pass.
- `bash -n` on all four modified production scripts and the new shell selftest;
  `git diff --check`: pass.

## Activation hold and next safe step

**HELD DRAFT ONLY. Do not merge or mark ready.** Changed health-gate scripts are
keys of `upgrade-coordination-lib`, mounted into dev-env-ops. Its HelmRelease has
`reloader.stakater.com/auto: "true"`; merging changes that ConfigMap and causes
Reloader to **restart dev-env-ops, ending its running sessions**. Activation must
wait for **no claimed work orders**, per escalate.sh, agentic-remediation.md and
#3415. The H7 maintenance hold (#3934, dev-env#290) also covers respond.sh and is
unchanged. Tom's morning review must resolve activation; this handoff grants none.

Source and fixture tests only. No deployment, runtime acceptance, reader change,
new access, live order read, responder invocation, synthetic live alert, cluster
write or restart. The dev-env reader's `alertIdentity` join is a later dev-env PR.
Stop at the held draft PR; lead reviews. Story status remains unchanged; the final
story comment records the PR URL, exact head and review result.
