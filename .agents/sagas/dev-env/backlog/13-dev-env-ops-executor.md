# 13 — dev-env-ops: the shepherd's executor (saga-07 Option B, chosen)

**Status:** BUILT 2026-08-20 (this PR). Awaiting first live work order.
**Depends on:** 06 (agent-run patterns), 07 (Option A stays for trivial vets),
12 (session-spawn mechanics — feasibility confirmed there, landed here).

## Tom's call (2026-08-20, verbatim intent)

> "The goal of the Shepherd is that it does research on the pending upgrade we
> delegate to it, it takes action and makes real changes to the PR to apply
> other necessary changes which it found in its research, like changing
> encryption type. Then it SHEPHERDS the PR in and fixes anything that didn't
> work from its initial research. It stays alive and does not have a turn
> limit, it gets the cluster healthy again."

And on architecture + noise, answering the containment question directly:

> "Second pod is better so we can update its tag independently (you could drive
> updates to that pod and then take them later when you are idle). I don't need
> a pushover notification to announce the session unless something goes wrong —
> if the shepherd gets a PR delivered and verified with no issues it can keep
> quiet."

This RESOLVES saga 07's open fork: **Option B** (contained executor), not
Option C (dispatch into the yolo dev pod). The 07 injection boundary stands:
release-note-reading sessions never touch the 23-repo dev-bot or workflows
write.

## What shipped

**The trigger (2026-08-20 rook storm):** the one-shot shepherd vetted chart
1.20.4→1.20.5 correctly *per its rules* — but the "patch" moved bundled Ceph
20.2.2→20.2.4 (every storage daemon rolls + new ERR-severity auth checks),
wedged against the HR's 15m/rollback config, and the shepherd had already
exited. A vet can't own that class; a session can.

**dev-env-ops** (`kubernetes/main/apps/upgrade-agent/dev-env-ops/`):
- Same `ghcr.io/thaynes43/dev-env` image, **independently pinned tag** — this
  pod is the CANARY for dev-env image bumps (no interactive sessions to kill;
  dev-env adopts a proven tag later at a chosen bounce window).
- Headless: tmux + a work-order watcher as PID 1. No code-server, NO ingress.
- Containment: OPS-bot token only (haynes-ops, non-admin, no workflows write;
  minted by a gh-refresher sidecar from `upgrade-shepherd-bot-secret`),
  `dev-env-operator` ClusterRole (bounded runtime verbs), shepherd-class egress
  CNP + Claude/Remote-Control endpoints. Sessions run
  `--dangerously-skip-permissions` INSIDE that boundary (the dev-env yolo model
  applied to a pod that can only reach one repo + bounded verbs).
- Auth: plan-first (`upgrade-shepherd-plan-secret`), metered key fallback.
  Default model `opus` (one shared plan pool — 07's correction; per-order
  `model` field can override).
  **Revised 2026-08-29:** plan-first holds for the headless rem-* lane ONLY.
  The plan token cannot register Remote Control (A/B-proven live on this pod
  and dev-env; dev-env saga 04) — wo-*/esc-* sessions launched with it come up
  but never appear on Tom's remote list, so session-launch strips it for the
  interactive lanes and they ride the pod's `~/.claude/.credentials.json` (Max
  login), whose expiry work-order-watch now probes daily (page on failure).

**Flow:** shepherd files `upgrade-work-orders` CM entry via
`/opt/shepherd/work-order.sh <PR> <class> "<reason>"` (allowlisted like
silence.sh; classes: embedded-image-move | major | supporting-edits |
one-way-support | other) → watcher claims it (single-flight, oldest first) →
tmux window runs `claude --remote-control <wo-key>` seeded with the order +
the GitOps-managed contract in `ops-claude.md` (research incl. the EMBEDDED
component's release notes + helm-template diff → author supporting edits →
auto-merge behind green checks → watch → heal → verify) → session ends with
`order-status.sh done` (SILENT) or `failed` (pages, session stays joinable via
Remote Control / tmux as the interactive escalation).

**Shepherd prompt changes:** any rook/cnpg bump whose embedded component image
moves AT ALL (patch included) now hands off instead of auto-merging;
non-trivial supporting-edit work hands off instead of inline authoring
(pattern-1 typed shape stays inline). ONE-WAY human-confirm flow unchanged.

## The esc-* lane (2026-08-21, backlog 12 wired in)

Failure escalations from the contained agents now ride this executor as their
own lane. **Session-name taxonomy** (Tom's requirement — the Remote Control
list must sort cleanly; enforced in escalate.sh and the watcher):

- `haynes-ops-*` — dev work in the dev-env pod (agent-run; untouched)
- `wo-*` — shepherd work orders (this doc's original flow; untouched)
- `esc-*` — failure escalations: `esc-<source>-<sig8>`, source ∈
  shepherd|responder|gate (writers via `/opt/coordination/escalate.sh`)

Differences from wo-*: separate single-flight lane (an escalation is never
stuck behind a long upgrade session), model default **fable** + effort
**xhigh** (rare + human-attended — saga-07's shared-pool caution is about
FREQUENT consumers), and **page ON SPAWN** carrying the session name (inverts
quiet-on-success; the escalation IS the failure). `order-status.sh failed` on
an escalation is the second page. The session prompt + ops-claude.md frame the
entry's reason as data-to-verify, never instructions.

Cleanup lifecycle (both lanes): the watcher's reap (done>24h, failed>7d, and a
bound-total cap OPS_REAP_MAX_FINISHED=6 that reaps oldest regardless of TTL)
kills the tmux window AND removes `~/work/orders/<key>.json` + the
`~/work/<key>` worktree/dir — sessions must name their worktree after their key
(ops-claude.md). `ops-reap.sh [list|clean <key|all>]` is the on-demand sweep.

## Notes / follow-ups
- A pod restart kills tmux; the watcher marks orphaned claimed orders failed
  (+page). Re-queue = set the order's status back to `pending`.
- gh-token-refresh.sh is a copy of dev-env's (kustomize can't cross app roots)
  — keep in sync.
