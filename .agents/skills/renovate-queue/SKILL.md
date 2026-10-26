---
name: renovate-queue
description: Survey and deliver the live Renovate update queue, covering the Dependency Dashboard plus every open update pull request, including manual and held items. Use when classifying eligibility, ownership, and the next reviewed delivery. Skip hourly health monitoring, cluster policy edits, and new delivery tooling.
---

# Renovate queue

Advance the live dependency queue to a truthful checkpoint. Read the linked section the current item needs, and do not paste those manuals here.

- Batch order, one-PR delivery, and rollback: [renovate-upgrade-batches.md](../../runbooks/renovate-upgrade-batches.md)
- Shepherd loop, holds, and merge order: [upgrade-shepherd.md](../../runbooks/upgrade-shepherd.md)
- Post-reconcile health verdict: [upgrade-health-gate.md](../../runbooks/upgrade-health-gate.md)
- Component notes before a manual-tier change: [tier4-component-playbooks.md](../../runbooks/tier4-component-playbooks.md)
- Reviewed delivery command: [deliver_reviewed_pr.py](../../../scripts/upgrade-agent/deliver_reviewed_pr.py)

## What to read first

Open the live Dependency Dashboard and every open update pull request, including manual-only and held items. Neither view replaces the other. For each item, record the exact head SHA, required and advisory check results, review and Renovate comments, the matching rule in `.renovate/holds.json5`, and who currently owns the change. Then read a fresh upstream release, its compatibility notes, and the backup and runtime prerequisites that release actually requires. A remembered queue size or an old summary is not that evidence.

Keep source edit, CI on that head, merge, artifact publication, runtime rollout, and usable acceptance as separate states. Classify the item as eligible, blocked, or already owned. Change eligible source or tests only through the writer who already owns those files. Do not start a second writer on the same change.

## Delivery and after

An authorized routine flow does not need another generic approval. A hold, an access limit, a security change, downtime, or a session gate still blocks that action. This skill and any source contract are instructions, not an access grant.

Deliver with `scripts/upgrade-agent/deliver_reviewed_pr.py`, one pull request per invocation. `--head` and `--reviewed-head` must both be the native-reviewed commit. Required aggregates come from that repository's profile. Pass one preflight program and its arguments after `--`, with a finite timeout. The program must check each prerequisite and return nonzero on failure. Do not rely on `assert` alone, and do not hide earlier failures in an opaque `bash -c` chain. The command must see the pull request actually merged before it runs Flux or a release step, and only for targets that invocation is authorized to roll out.

After an authorized rollout, read the live workload's image ID and health again. Roll back with the existing git revert or re-pin, and leave an in-place security fix in place when the rollback is for a different fault. Clear hosted Renovate warnings only when a real Renovate run shows them gone. Do not clear them by adding notification suppressions. Record a truthful OpenProject checkpoint, including anything still unverified.

Stop when the budget is exhausted, a command returns an exact denial, or a prerequisite is missing. Do not switch identity, route, login, or credential, and do not add another hourly monitor. The scheduled health gate already owns that loop.
