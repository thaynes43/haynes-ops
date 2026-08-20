#!/usr/bin/env bash
# ops-init.sh — dev-env-ops boot: link GitOps-managed config into $HOME.
# Everything here is replaced on every boot — edit in git, never on the PVC.
set -u
log() { printf 'ops-init: %s %s\n' "$(date -u +%FT%TZ)" "$*"; }

mkdir -p "$HOME/.claude" "$HOME/work/orders" "$HOME/repos"

# ── Claude auth bootstrap (2026-08-20, proven live during the synthetic test) ──
# INTERACTIVE claude ignores CLAUDE_CODE_OAUTH_TOKEN (headless-only) and runs
# the first-run wizard on a fresh PVC (theme → login → OAuth browser dance = an
# unattended wedge). Seed the logged-in state instead:
#   1. $CLAUDE_CONFIG_DIR/.credentials.json synthesized from the setup-token
#      (NOTE the path: CLAUDE_CONFIG_DIR moves BOTH state files under ~/.claude/
#      — seeding ~/.claude.json does nothing, hit live).
#   2. $CLAUDE_CONFIG_DIR/.claude.json with the onboarding flags.
# No oauthAccount object is needed (verified). Re-seed whenever the env token
# differs from the stored one so a 1P token rotation heals on the pod roll the
# secret change triggers (this pod's contract is setup-token auth — a manual
# in-pod login would be overwritten on the next boot, by design).
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  cur="$(jq -r '.claudeAiOauth.accessToken // empty' "$HOME/.claude/.credentials.json" 2>/dev/null)"
  if [ "$cur" != "$CLAUDE_CODE_OAUTH_TOKEN" ]; then
    EXP=$(( ($(date +%s) + 300*24*3600) * 1000 ))
    jq -nc --arg t "$CLAUDE_CODE_OAUTH_TOKEN" --argjson e "$EXP" \
      '{claudeAiOauth: {accessToken: $t, expiresAt: $e,
        scopes: ["user:inference","user:profile"], subscriptionType: "max"}}' \
      > "$HOME/.claude/.credentials.json" \
      && chmod 600 "$HOME/.claude/.credentials.json" \
      && log "seeded .credentials.json from the setup-token"
  fi
fi
if [ ! -f "$HOME/.claude/.claude.json" ]; then
  jq -nc '{hasCompletedOnboarding: true, lastOnboardingVersion: "2.1.217",
           bypassPermissionsModeAccepted: true, theme: "dark",
           projects: {"/home/dev": {hasTrustDialogAccepted: true,
                                    hasCompletedProjectOnboarding: true}}}' \
    > "$HOME/.claude/.claude.json" \
    && log "seeded .claude.json onboarding state"
fi

# Session operating contract — claude auto-loads $CLAUDE_CONFIG_DIR/../CLAUDE.md
# conventions via the home dir; keep it at ~/.claude/CLAUDE.md like dev-env.
cp /opt/dev-env-ops/ops-claude.md "$HOME/.claude/CLAUDE.md"

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
log "init complete"
