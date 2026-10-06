#!/usr/bin/env bash
# ops-init.sh — dev-env-ops boot: link GitOps-managed config into $HOME.
# Everything here is replaced on every boot — edit in git, never on the PVC.
set -u
log() { printf 'ops-init: %s %s\n' "$(date -u +%FT%TZ)" "$*"; }

mkdir -p "$HOME/.claude" "$HOME/work/orders" "$HOME/repos"

# ── Runtime dir for claude's cross-session messaging sockets (see the HR env) ──
# /dev/shm is a fresh tmpfs every container start; claude needs it 0700 and ours.
if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
  if mkdir -p "$XDG_RUNTIME_DIR" && chmod 0700 "$XDG_RUNTIME_DIR"; then
    log "runtime dir $XDG_RUNTIME_DIR ready (cross-session messaging sockets)"
  else
    log "WARN could not prepare $XDG_RUNTIME_DIR — cross-session messaging will be off"
  fi
fi

# ── Claude auth (2026-08-20; rewritten 2026-10-06 for haynes-ops#3414) ──────
# Two credentials, never mixed:
#   * CLAUDE_CODE_OAUTH_TOKEN (pod env, a `claude setup-token`): rem-*,
#     curation, and every no-Remote-Control fallback. Nothing to seed — the CLI
#     reads the env var directly.
#   * $CLAUDE_CONFIG_DIR/.credentials.json: dev-env-ops' OWN Max login, written
#     ONLY by `claude auth login` during the renewal ceremony (runbook
#     agentic-remediation.md) and refreshed in place by the CLI. It lives on the
#     dev-env-ops-home PVC, so it survives restarts. wo-*/esc-* ride it for
#     Remote Control, which the setup token cannot register.
# Until 2026-10-06 this block SYNTHESIZED .credentials.json from the setup token
# and rewrote it on any boot where the two differed — which made a real login
# impossible to keep, and made every wo-*/esc-* "Remote Control" session a
# setup-token session that was never registered (45 of 45, 2026-09-03..10-05).
# NEVER write .credentials.json here again, and never copy one in from another
# pod: a copy is a second refresh owner of the same token family (the 2026-08-29
# revocation class) — the next rotation in one pod logs out the other.
cred="$HOME/.claude/.credentials.json"
if jq -e '.claudeAiOauth.refreshToken // empty' "$cred" >/dev/null 2>&1; then
  log "$(bash /opt/dev-env-ops/login-check.sh --quiet 2>&1 | head -1)"
elif [ -f "$cred" ] && jq -e '(keys == ["claudeAiOauth"]) and ((.claudeAiOauth.refreshToken // "") == "")' "$cred" >/dev/null 2>&1; then
  # The legacy synthesized file: setup token, no refresh token, nothing else.
  # Remove it so "is there a Max login?" has one honest answer.
  rm -f "$cred" \
    && log "removed the legacy setup-token .credentials.json — no Max login yet: wo-*/esc-* run on the setup token WITHOUT Remote Control until the dev-env-ops Max login ceremony is run (runbook agentic-remediation.md)"
else
  log "$(bash /opt/dev-env-ops/login-check.sh --quiet 2>&1 | head -1)"
fi
if [ ! -f "$HOME/.claude/.claude.json" ]; then
  jq -nc '{hasCompletedOnboarding: true, lastOnboardingVersion: "2.1.217",
           bypassPermissionsModeAccepted: true, theme: "dark",
           projects: {"/home/dev": {hasTrustDialogAccepted: true,
                                    hasCompletedProjectOnboarding: true}}}' \
    > "$HOME/.claude/.claude.json" \
    && log "seeded .claude.json onboarding state"
fi

# Session operating contract — claude auto-loads it from ~/.claude/CLAUDE.md
# like dev-env. install -m, NOT cp: cp preserves the CM mount's 0555 mode and
# the next boot's overwrite is then Permission-denied (hit live 2026-08-20).
install -m 0644 /opt/dev-env-ops/ops-claude.md "$HOME/.claude/CLAUDE.md"

# Ops-bot git identity + at-use-time token (fresh mint each op; 1h TTL).
git config --global user.name "haynes-ops-bot[bot]" 2>/dev/null || true
git config --global user.email "haynes-ops-bot[bot]@users.noreply.github.com" 2>/dev/null || true
git config --global credential.helper \
  '!f(){ printf "username=x-access-token\npassword=%s\n" "$(cat /creds/gh_token)"; }; f' 2>/dev/null || true

# Canonical fetch-only clone; sessions use worktrees under ~/work (dev-env rule).
if [ ! -d "$HOME/repos/haynes-ops/.git" ]; then
  git clone https://github.com/thaynes43/haynes-ops.git "$HOME/repos/haynes-ops" 2>/dev/null \
    && log "cloned haynes-ops" \
    || log "WARN initial clone failed — sessions will clone on demand"
fi

# ...and REFRESH it. The clone guard above is `[ ! -d .git ]` and this PVC survives
# pod restarts, so without this the clone stays frozen at whatever commit it was
# first created from — forever. Found live 2026-08-23: the clone AND its origin/main
# ref were both pinned at 2dcf42fd (Aug 20) on a pod that had restarted repeatedly.
# That is a correctness bug, not just staleness: ops-claude.md directs every wo-*
# session to consult .renovate/holds.json5 and .agents/runbooks/, and those files
# RESOLVED — to their Aug-20 contents. A hold added after that date was invisible,
# so a work-order session could merge an upgrade that had been explicitly held.
# fetch (not `reset --hard`): this is a fetch-only canonical clone and sessions
# branch worktrees from origin/main, so there is no working tree worth clobbering.
if [ -d "$HOME/repos/haynes-ops/.git" ]; then
  git -C "$HOME/repos/haynes-ops" fetch --prune origin main 2>/dev/null \
    && log "fetched origin/main ($(git -C "$HOME/repos/haynes-ops" rev-parse --short origin/main 2>/dev/null))" \
    || log "WARN fetch failed — clone may be stale; sessions must verify their base"
fi
log "init complete"
