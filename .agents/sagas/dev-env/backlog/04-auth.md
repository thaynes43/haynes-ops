# 04 — CLI auth + expiry watch

**Status:** backlog
**Depends on:** 02
**Parallel with:** 03, 05

## Goal

Every agent CLI in the pod is authenticated against the right plan, auth survives pod
restarts, and Tom gets **paged before** an expired credential strands a dispatched
agent — never discovers it mid-task.

## Auth paths per CLI

- **Claude Code (Max plan)**: run `claude setup-token` interactively once (on the Mac
  or in-pod via the paste-URL flow), store the long-lived OAuth token in 1Password →
  ExternalSecret → `CLAUDE_CODE_OAUTH_TOKEN` env in the pod. Headless `claude -p` and
  interactive sessions both honor it. Expiry ~1 year, revocable, no in-pod refresh —
  re-mint is a human ceremony (document it in this file when built).
  - Fallback: `ANTHROPIC_API_KEY` (the shepherd's existing 1Password item) switchable
    per-invocation for over-limit spillover (see backlog 07 dispatch policy).
- **Codex CLI (ChatGPT plan)**: `codex login` device-auth flow entirely in-pod
  (one-time, ~90s human ceremony); `auth.json` lands on the PVC and self-refreshes.
- **gh**: decide identity per Decision log #5 — options are a PAT for Tom's account,
  the existing haynes-ops-bot app (initContainer token-mint pattern), or a new
  machine user. Whatever is chosen lands as ExternalSecret → `GH_TOKEN`.
- **git push**: same identity decision; https + token preferred over SSH keys in-pod
  (no agent forwarding, no key file on the PVC).

## Expiry watch

`auth-check.sh` (in the scripts ConfigMap), run by a CronJob (or a Gatus-scraped
sidecar endpoint) daily:

- Claude: cheapest possible authenticated call (e.g. `claude -p 'ok' --max-turns 1`
  with a tiny model, or an authenticated account endpoint) — distinguishes
  *invalid/expired* from *rate-limited* (the latter is NOT a page, it's expected).
- Codex: `codex login status` (exit code).
- gh: `gh auth status`.
- On failure: Pushover page via the existing pattern (crib from health-gate's
  page.sh), including WHICH credential and the re-auth runbook steps.

## Acceptance

- Pod delete + reschedule → all CLIs still authenticated with zero human steps.
- Revoking a test token triggers a Pushover page on the next check cycle.
- The re-auth ceremony for each CLI is documented step-by-step in this file.
