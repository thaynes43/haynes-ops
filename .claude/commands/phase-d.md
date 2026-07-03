---
description: Execute Tier-4 Phase D (auto-merge + auto-summon) from the committed handoff
---
You are picking up **Tier-4 Phase D** cold. Read `docs/renovate/tier4-phase-d-handoff.md`
in full and follow it — it is a self-contained problem statement + implementation plan.

Do the orientation reads in its §0 first (agent memory, the runbooks, the audit), then
work its §3 plan **in order, starting with Step 1** (confirm the base is quiet via
`/kyverno-verify`). Validate every step with concrete proof on the live cluster — do not
trust the handoff's claims without checking state.

**Hard rule:** the operator-gated actions in §4 — the first real auto-merge (Step 3), the
scheduled-auto-merge flip (Step 4), any widening of scope, and any Kyverno Enforce flip —
require an explicit go-ahead from the operator, with proof shown first. Everything else you
may drive autonomously. Respect the kill switch and the spend guard.

$ARGUMENTS
