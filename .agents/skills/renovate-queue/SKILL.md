---
name: renovate-queue
description: Survey and deliver the live Renovate update queue, covering the Dependency Dashboard plus every open update pull request, including manual and held items. Use when classifying eligibility, ownership, and the next reviewed delivery. Skip hourly health monitoring, cluster policy edits, and new delivery tooling.
---

# Renovate queue

Advance the live queue to a truthful checkpoint. Read the linked section; do not paste the manuals here.

- Batch order, one-PR delivery, and rollback: [renovate-upgrade-batches.md](../../runbooks/renovate-upgrade-batches.md)
- Holds, stops, and merge order: [upgrade-shepherd.md](../../runbooks/upgrade-shepherd.md)
- Post-reconcile health: [upgrade-health-gate.md](../../runbooks/upgrade-health-gate.md)
- Manual-tier notes: [tier4-component-playbooks.md](../../runbooks/tier4-component-playbooks.md)
- Reviewed delivery: [deliver_reviewed_pr.py](../../../scripts/upgrade-agent/deliver_reviewed_pr.py)

## What to read first

Open the live Dependency Dashboard and every open update pull request, including manual and held items. Neither replaces the other. Record each exact head, required and advisory checks, review and Renovate comments, the matching `.renovate/holds.json5` rule, and the current owner. Read a fresh release, its compatibility notes, and the backup and runtime prerequisites it requires. An old summary is not evidence.

Keep source edit, CI, merge, publication, runtime, and usable acceptance separate. For each remaining item record disposition, owner, next action, and brief evidence: eligible, blocked, or already owned. Edit eligible source or tests only through the current owner. Do not add a second writer, or close or suppress a legitimate update to clear the board.

## Delivery and after

Existing task authority covers normal routine steps, including an already-authorized security patch, with no further generic approval. Security, trust, access, and network-policy changes outside that recorded authority stay held. A reserved access hold, security-policy hold, unapproved downtime, or session hold gates only that action. Never release it without its actual required approval. This skill grants no scope or access.

Deliver one pull request per command with `scripts/upgrade-agent/deliver_reviewed_pr.py`. `--head` and `--reviewed-head` are the native-reviewed commit. Required aggregates come from the fixed profile. After `--`, pass one preflight program and arguments with a finite timeout; a failed prerequisite exits nonzero. No `assert`-only check and no opaque `bash -c` chain. `--source-only` publishes source without activation. Named `--reconcile namespace/name` is only for authorized haynes-ops runtime. The helper does not execute a release. It verifies merge before targeted Flux. Track automatic main-only publication separately, and verify the artifact before any authorized runtime pin.

After an authorized rollout, read the live image ID and health. Roll back by the existing git revert or re-pin. Keep a security fix where compatible. If the only rollback would remove it, disclose that tradeoff first and keep existing holds. That is not a new approval flow. Clear hosted Renovate warnings only when a real hosted run shows them gone, not by suppression. Checkpoint OpenProject truthfully, including what is still unverified.

Stop only that denied or missing-prerequisite delivery. Continue independent eligible work already authorized, inside the recorded budget. At the recorded boundary, checkpoint and hand off. Do not claim completion, and do not use an unrecorded budget to stop or continue. Keep denied, pending, and uncertain outcomes distinct. Do not switch identity, route, login, or credential. Retry the same action at most once, only with materially new authorization or evidence and only if that attempt is known not to have executed. An exact denial stops. An uncertain mutation needs a readback, not a replay. Use the linked shepherd runbook. Do not add a retry framework or hourly monitor.
