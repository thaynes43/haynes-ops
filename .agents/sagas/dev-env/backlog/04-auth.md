# 04 — CLI auth + expiry watch

**Status:** done (PRs #2041–#2043, 2026-07-13) — claude (Max) + codex (ChatGPT)
authenticated; gh-refresher sidecar mints haynes-ops-bot tokens to /creds every
40min (PEM env-isolated, agents read per-shell); auth-watch sidecar probes all
three daily and pages Pushover (proved itself live on day one during the 422
window). GitHub identity is haynes-dev-bot (PR #2044, Decision #5 revised): a
SEPARATE App installed on ALL 23 repos — verified live by a cross-repo dispatch
that opened hass-sandbox PR #97 as app/haynes-dev-bot. haynes-ops-bot stays scoped
to haynes-ops ops/failure work and its PEM is NOT in this pod.
1Password hardening of the Claude credential shipped 2026-08-29 (rides held
draft PR #2588) — see "Long-lived token hardening" below.

## 1Password gotchas (cost ~1h live, 2026-07-13)

- 1P mobile paste appends **trailing spaces** to item titles AND field labels.
  ESO does an EXACT match on both — `github-dev-bot ` and `GITHUB_BOT_APP_PRIVATE_KEY `
  each failed silently-ish (`key not found` / `got 0 ItemFields`). Check for them first.
- **1Password Connect caches**: after fixing an item, Connect can keep serving the OLD
  copy — an ES force-sync annotation is NOT enough. `kubectl rollout restart
  deploy/onepassword-connect -n external-secrets` forces a fresh vault sync.
- Read what Connect actually sees (labels/titles only, never values) with a throwaway
  curl pod using the `onepassword-connect-secret` token against
  `/v1/vaults/<id>/items` — that's what turned two guesses into two facts.
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

## Long-lived token hardening (2026-08-29, rides PR #2588)

**The incident that promoted this from "optional":** the Max credential expired
mid-task (2026-08-29 ~09:04), stranding a live cigar-journal ship chain. Likely
root cause: 5+ concurrent claude sessions share `~/.claude/.credentials.json`
and can race the OAuth refresh-token rotation — one process rotates, a sibling
replays the stale refresh token, the token family gets revoked. The in-session
`/login` recovery ALSO hit the documented wrapped-URL gotcha (tmux hard-wraps
the OAuth URL; capture it with `tmux capture-pane -p -J`, or drive the whole
ceremony from a kubectl-equipped agent session — proven again live).

**The fix:** `claude setup-token` long-lived token (~1yr, NO rotating refresh)
→ 1Password `dev-env` item, TOP-LEVEL field `CLAUDE_CODE_OAUTH_TOKEN` →
ExternalSecret `dev-env-claude` → `dev-env-claude-secret` → `envFrom` on BOTH
the app container (agents) and auth-watch (the probe must exercise the
credential agents actually use). Env var takes precedence over
`.credentials.json`, so the refresh race is out of the loop for headless/task
sessions, and auth survives PVC loss.

**Limitation found the same day (A/B-proven in-pod):** the setup-token CANNOT
register `/v1/code/sessions` — a `claude --remote-control <id>` launched with
the env var set comes up looking normal but silently never appears on the
phone/web remote list (no `/rc` badge in the status bar either; that badge is
the in-pod tell). Same launch with `env -u CLAUDE_CODE_OAUTH_TOKEN` registers
instantly. So agent-run strips the env var for mode=both, meaning:

- **remote-control sessions still ride `~/.claude/.credentials.json`** — the
  in-pod `/login` ceremony above stays load-bearing, and the multi-session
  refresh race still exists for concurrent "both" sessions (much smaller
  surface than before: task mode, local TUI, and auth-watch no longer touch
  the file).
- **auth-watch probes BOTH paths** (env token AND `env -u` credentials.json)
  and its page names which one died.

**Re-mint ceremony (~1x/year, or when auth-watch pages):**

1. On any machine with claude logged in (Tom's Mac): `claude setup-token` —
   interactive OAuth, prints an `sk-ant-oat01-…` token.
2. Paste it into the 1Password `dev-env` item, field `CLAUDE_CODE_OAUTH_TOKEN`
   (TOP-LEVEL, watch for the mobile-paste trailing-space trap above).
3. Connect can cache the old copy — if the ES doesn't pick it up,
   `kubectl rollout restart deploy/onepassword-connect -n external-secrets`,
   then force-sync the ES. Reloader rolls the dev-env pod on the secret change
   (bounces live sessions — pick a quiet window).
4. Verify in-pod: `claude -p "Reply with exactly: POD-AUTH-OK" --model haiku`.

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
