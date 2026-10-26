---
name: renovate-queue
description: Survey and deliver the live Renovate update queue, covering the Dependency Dashboard plus every open update pull request, including manual and held items. Use when classifying eligibility, ownership, and the next reviewed delivery. Skip hourly health monitoring, cluster policy edits, and new delivery tooling.
---

# Renovate queue

Advance the live queue to a truthful checkpoint. Read the linked section for the current item; do not paste the manuals here.

- Batch order, one-PR delivery, and rollback: [renovate-upgrade-batches.md](../../runbooks/renovate-upgrade-batches.md)
- Shepherd loop, holds, and merge order: [upgrade-shepherd.md](../../runbooks/upgrade-shepherd.md)
- Post-reconcile health verdict: [upgrade-health-gate.md](../../runbooks/upgrade-health-gate.md)
- Component notes before a manual-tier change: [tier4-component-playbooks.md](../../runbooks/tier4-component-playbooks.md)
- Reviewed delivery command: [deliver_reviewed_pr.py](../../../scripts/upgrade-agent/deliver_reviewed_pr.py)

## What to read first

Open the live Dependency Dashboard and every open update pull request, including manual and held items. Neither replaces the other. For each item record the exact head, required and advisory checks, review and Renovate comments, the matching `.renovate/holds.json5` rule, and the current owner. Then read a fresh upstream release, its compatibility notes, and the backup and runtime prerequisites it requires. An old summary is not that evidence.

Keep source edit, CI on that head, merge, artifact publication, runtime rollout, and usable acceptance separate. Classify the item as eligible, blocked, or already owned. Change eligible source or tests only through the current owner. Do not start a second writer.

## Delivery and after

Existing task authority covers normal routine steps, including an already-authorized security patch, without another generic approval. Only a reserved access hold, a security-policy hold, unapproved downtime, or a session hold gates the affected action. Never release a held action without its actual required approval. This skill and any source contract are instructions, not an access grant.

Deliver with `scripts/upgrade-agent/deliver_reviewed_pr.py`, one pull request per command. `--head` and `--reviewed-head` are that native-reviewed commit. Required aggregates come from the fixed repository profile. After `--`, pass one preflight program and its arguments with a finite timeout; it must check each prerequisite and exit nonzero on failure. Do not use `assert` alone or an opaque `bash -c` chain. Choose `--source-only` for source publication without activation. Pass named `--reconcile namespace/name` only for authorized haynes-ops runtime. Profiles are fixed, and the helper does not execute a release. It verifies the pull request is merged before targeted Flux. Track automatic main-only publication separately, and verify the actual artifact before any authorized runtime pin.

After an authorized rollout, read the live workload's image ID and health again. Roll back with the existing git revert or re-pin. Keep a security fix where that rollback stays compatible. If the only rollback would remove the fix, disclose that unavoidable security tradeoff before proceeding, and keep existing holds. That disclosure is not a new approval flow. Clear hosted Renovate warnings only when a real Renovate run shows them gone, not by adding suppressions. Record a truthful OpenProject checkpoint, including anything still unverified.

Stop on an exhausted budget, an exact denial, or a missing prerequisite. Do not switch identity, route, login, or credential, and do not add an hourly monitor. The scheduled health gate already owns that loop.
