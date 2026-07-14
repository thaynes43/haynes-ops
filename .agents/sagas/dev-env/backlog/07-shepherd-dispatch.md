# 07 — Shepherd → dev-env dispatch integration

**Status:** DESIGN REVISED 2026-07-13 (evening) — needs Tom's call before wiring.
**Depends on:** 06 (done)

## The goal, restated

Tom's motive (verbatim, session 2026-07-13): *"the Shepard becomes a
coordinator/dispatcher and the dev env pod is the workhorse, this will save direct
API usage calls and move them to my monthly plan."*

Two distinct wishes, and they are **separable**:

1. **Cost** — the Shepherd's LLM turns should burn the Max subscription, not the
   metered `ANTHROPIC_API_KEY` ($50/mo cap).
2. **Architecture** — the Shepherd coordinates; the dev-env executes.

## The problem the identity split exposed

Decision #3 (in-pod HTTP dispatch API) was made BEFORE the dev-env pod acquired
its real powers. As of tonight the dev-env pod holds:

- `haynes-dev-bot` — **contents+workflows write on ALL 23 repos**
- the **operator-tier** kubectl SA (pod delete, rollout restart, Flux patch, Jobs)
- both subscription credentials
- yolo-mode agents (`--dangerously-skip-permissions`) by design

The Shepherd, by contrast, is deliberately the most *contained* thing in the
cluster: read-only SA, tool allowlist that denies `gh pr merge` outside auto mode,
default-deny egress to GitHub+Anthropic only, and an ops-bot token scoped to
`haynes-ops` alone and non-admin (so GitHub itself gates merges on green checks).

**Dispatching Shepherd work into the dev-env pod means the LLM turn executes with the
dev-env pod's powers, not the Shepherd's.** `claude -p` runs tools locally — you
cannot ship "just the inference" to another pod and keep the caller's containment.

And the Shepherd's input is *hostile by construction*: it reads *release notes off
the internet* — a textbook prompt-injection surface. Today an injected Shepherd can,
at worst, open a PR in one repo behind server-side check gating. Dispatched into the
dev-env pod, an injected Shepherd would inherit push access to 23 repos (including
`.github/workflows` — i.e. CI execution) plus cluster mutation verbs. **That is a
material regression of the exact boundary the Tier-4 work spent months building.**

## Recommendation: get the cost win WITHOUT the regression

**Option A (recommended) — swap the Shepherd's credential, keep its containment.**
Run the LLM turn where it runs today (the contained Shepherd pod), but authenticate
with the Max subscription instead of the API key:

- `claude setup-token` (confirmed present in claude-code 2.1.207) mints a long-lived
  token for headless use → 1Password item `claude-code` → ExternalSecret →
  `CLAUDE_CODE_OAUTH_TOKEN` in the Shepherd pod.
- `run-shepherd.sh` prefers the OAuth token; falls back to `ANTHROPIC_API_KEY` when
  it is absent/rejected/rate-limited (the plan-first, API-fallback policy of
  Decision #2 — unchanged, just enforced in the Shepherd rather than a dispatcher).
- Spend guard keeps governing the *fallback* path; plan-served runs record $0.
- **Cost goal: achieved. Containment: unchanged. Blast radius: unchanged.**
- Cost: Tom runs one `setup-token` ceremony (~1 min) and adds a 1P item.

**Option B — dispatch, but into a CONTAINED flavor (this is plan 08's real job).**
If Tom wants the coordinator/executor architecture for its own sake, the executor
must NOT be the yolo dev pod. Stand up a second dev-env instance (`dev-env-ops`)
with: read-only SA, the ops-bot token only (no dev-bot), the Shepherd's tool
allowlist enforced pod-side from git, and the Shepherd's egress policy. The
dispatch API then hands work to a pod no more powerful than the Shepherd itself.
More moving parts; same containment; achieves both wishes.

**Option C — dispatch into the dev-env pod as originally decided.** Cheapest to
build, and the one I am NOT going to ship unasked: it trades a months-old security
boundary for an architectural preference whose cost benefit Option A already
delivers.

## Ask

Tom picks A, B, or C. Default if he says nothing: **A** (safe, achieves the stated
money goal, one-minute ceremony). Nothing is wired until he calls it — the
credential-swap PR is authored but held, because an ExternalSecret pointing at a
1Password item that does not exist yet wedges the Flux kustomization NotReady (a
trap already hit once tonight).

## If A: acceptance

- A scheduled Shepherd run completes with **zero** `ANTHROPIC_API_KEY` spend
  (the run's JSON records which auth path served it; the spend ConfigMap stays flat).
- Rate-limit exhaustion falls back to the API key rather than stalling a run
  (a stalled remediate run must never delay a regression fix — Decision #2).
- Kill switch and every existing guardrail (pre-filter, ramp check, vet markers,
  spend guard, health gate) behave identically.
