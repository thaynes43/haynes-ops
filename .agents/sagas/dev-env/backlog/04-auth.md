# 04 — CLI auth + expiry watch

**Status:** done (PRs #2041–#2043, 2026-07-13) — claude (Max) + codex (ChatGPT)
authenticated; gh-refresher sidecar mints haynes-ops-bot tokens to /creds every
40min (PEM env-isolated, agents read per-shell); auth-watch sidecar probes all
three daily and pages Pushover (proved itself live on day one during the 422
window). NOTE: the App installation covers ONLY thaynes43/haynes-ops — extend it
in GitHub App settings before dispatching agents at hass-sandbox/haynesnetwork.
Optional left: 1Password hardening of the Claude credential.
**Depends on:** 02
**Parallel with:** 03, 05

## The proven re-auth ceremony (2026-07-13, works fully remote)

Both credentials live on the PVC and self-refresh; this is only needed after a PVC
loss or a revocation. Requires: an agent session with kubectl (drives tmux in the
pod) + Tom on any browser (phone works).

- **Claude (Max)**: in the pod, `tmux send-keys -t main:0 "claude" Enter` → first-run
  wizard → pick "Claude account with subscription" → widen the window
  (`tmux resize-window -x 500`) and capture the `https://claude.com/cai/oauth/authorize?…`
  URL from the pane → Tom opens it, approves, and pastes back the `code#state` string →
  `tmux send-keys -l '<pasted>'` + Enter → `~/.claude/.credentials.json` (0600) appears.
  Verify: `claude -p "Reply with exactly: POD-AUTH-OK" --model haiku`.
- **Codex (ChatGPT)**: one-time prereq — enable device-code authorization in ChatGPT
  Security Settings. Then `codex login --device-auth` in a tmux window → relay the
  printed URL + `XXXX-XXXXX` code → Tom enters it on his phone → poll
  `codex login status` until "Logged in using ChatGPT" → `~/.codex/auth.json` (0600).
  Codes expire in 15 min — mint freely, they're cheap. Verify:
  `codex exec --skip-git-repo-check "Reply with exactly: CODEX-POD-AUTH-OK"`.
- **Gotchas hit live**: `CODEX_HOME` dir must exist before `codex login` (dev-init
  will own this, plan 03); the login TUI hard-wraps URLs (resize the tmux window
  before capturing); the pod needed `ndots: 1` + `claude.com` egress (fixed in PR
  #2034) before `claude` would even start.

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
