# 07 — Shepherd → dev-env dispatch integration

**Status:** backlog — blocked on Decision log #2 (dispatch policy) and #3 (mechanism)
**Depends on:** 06

## Goal

The Shepherd stops being the workhorse and becomes the **coordinator**: its
deterministic layers (pre-filter, spend guard, triage, ramp consistency) keep running
as-is, but when a run needs an LLM it dispatches the task to the dev-env pod — moving
the token burn from the metered API key to the subscription plan.

## Mechanism options (Decision log #3)

1. **In-pod dispatch API** (recommended): a tiny HTTP service in the dev-env pod
   (`POST /tasks` → wraps `agent-run`, `GET /tasks/<id>` → status/result/summary).
   Clean audit boundary, rate-limitable, the Shepherd's CNP gains ONE in-cluster
   egress rule. The Shepherd keeps zero new RBAC.
2. `kubectl exec` from the Shepherd into the dev pod — no new service, but grants the
   Shepherd `pods/exec` (a huge RBAC jump for a contained agent: exec = arbitrary
   code in a pod holding subscription + git credentials). Dispreferred.
3. Shepherd creates k8s Jobs from the dev-env image — loses the warm pod/PVC state,
   needs Job-create RBAC + token secrets mounted into transient pods. Dispreferred.

## Dispatch policy (Decision log #2)

- Which task classes go to the plan vs stay on the API key (e.g. scheduled vets →
  plan; time-critical remediate → API key so a rate-limit window never blocks a
  regression fix).
- Rate-limit awareness: before dispatch, check plan-window headroom (or catch the
  over-limit error and fall back to API). The `-p` JSON output should record which
  auth path served the run.
- Spend accounting: subscription runs cost $0 API — the spend-guard ConfigMap gains a
  `dispatched_runs` counter instead so the monthly cap logic stays meaningful for the
  API-key fallback path.

## Containment note

This wires the CONTAINED agent (shepherd) to the UNCONTAINED one (dev-env). The
dispatch payload (prompt) must remain fully determined by the Shepherd's committed
config — the dev-env side should enforce `agent-run` guardrails (worktree, branch-only
pushes) regardless of what the dispatcher sends, so a compromised shepherd prompt
can't escalate via dispatch.

## Acceptance

- A scheduled shepherd run with in-scope work completes end-to-end with zero
  ANTHROPIC_API_KEY spend (verified in the JSON output + spend CM).
- Kill switch: suspending the dev-env or the dispatch API cleanly falls back to the
  API-key path (or refuses, per policy) — never silently drops the run.
- The vet-marker / orphan-report / silence flows behave identically post-migration.
