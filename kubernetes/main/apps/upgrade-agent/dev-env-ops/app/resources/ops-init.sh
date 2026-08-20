#!/usr/bin/env bash
# ops-init.sh — dev-env-ops boot: link GitOps-managed config into $HOME.
# Everything here is replaced on every boot — edit in git, never on the PVC.
set -u
log() { printf 'ops-init: %s %s\n' "$(date -u +%FT%TZ)" "$*"; }

mkdir -p "$HOME/.claude" "$HOME/work/orders" "$HOME/repos"

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
