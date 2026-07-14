#!/usr/bin/env bash
# dev-init — runs at pod start (before code-server) to link the GitOps-managed agent
# configs into the PVC-backed $HOME. Idempotent; NEVER touches mutable auth/state
# (~/.claude/.credentials.json, ~/.codex/auth.json, history, caches).
# Mounted from the dev-env-scripts ConfigMap; source of truth is
# kubernetes/main/apps/dev/dev-env/app/resources/ (auditable in PR diffs).
set -uo pipefail

CFG=/opt/dev-env/config
log() { printf 'dev-init: %s\n' "$*"; }

mkdir -p "$HOME/.claude" "$HOME/.codex" "$HOME/.config/dev-env" "$HOME/repos" "$HOME/work"

# ── Claude: global memory + MCP config ──────────────────────────────────────────
# CLAUDE.md symlinks (live-updates with the ConfigMap); the MCP config is COPIED to
# a stable path with env placeholders EXPANDED (claude reads ${VAR} in .mcp.json
# itself, but registering via `claude mcp add-json` stores the resolved URL — the
# secret path env only exists in this pod, which is the point).
ln -sf "$CFG/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
cp -f "$CFG/claude/mcp.json" "$HOME/.config/dev-env/mcp.json"

# Register the GitOps-defined MCP servers at user scope so EVERY claude invocation
# (any repo, any worktree) sees them. remove-then-add = declarative overwrite; a
# server Tom removes from git therefore disappears here on the next boot.
if command -v claude >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
  for name in $(jq -r '.mcpServers | keys[]' "$CFG/claude/mcp.json"); do
    spec="$(jq -c --arg n "$name" '.mcpServers[$n]' "$CFG/claude/mcp.json")"
    # Expand ${VAR} references from this pod's env (ExternalSecret-fed).
    spec="$(printf '%s' "$spec" | envsubst)"
    claude mcp remove -s user "$name" >/dev/null 2>&1 || true
    claude mcp add-json -s user "$name" "$spec" >/dev/null 2>&1 \
      && log "mcp '$name' registered" || log "WARN mcp '$name' registration failed"
  done
fi

# ── Codex: declarative config, auth untouched ───────────────────────────────────
cp -f "$CFG/codex/config.toml" "$HOME/.codex/config.toml"

# ── git identity + credentials (saga Decision #5: haynes-ops-bot everywhere) ────
# Commits author as the bot; pushes/clones read the fresh minted token per call via
# the credential helper (never a static token in .gitconfig).
git config --global user.name  "haynes-dev-bot[bot]"
git config --global user.email "haynes-dev-bot[bot]@users.noreply.github.com"
git config --global credential."https://github.com".helper \
  '!f() { echo username=x-access-token; echo "password=$(cat /creds/gh_token)"; }; f'
git config --global --replace-all safe.directory "$HOME/repos/*"

# ── playwright browsers: seed the PVC cache from the image's staging dir ────────
# PLAYWRIGHT_BROWSERS_PATH is on the PVC so repos can add their OWN pinned chromium
# revision (readOnlyRootFilesystem forbids writing an image path). Seed once; a repo's
# `playwright install` then adds revisions next to it.
mkdir -p "$HOME/.cache/ms-playwright"
if [ -d /opt/dev-env/ms-playwright ] && [ -z "$(ls -A "$HOME/.cache/ms-playwright" 2>/dev/null)" ]; then
  cp -a /opt/dev-env/ms-playwright/. "$HOME/.cache/ms-playwright/" 2>/dev/null \
    && log "seeded playwright browsers from image" || log "WARN playwright seed failed"
fi

# ── shell defaults + agent-run on PATH ──────────────────────────────────────────
mkdir -p "$HOME/.local/bin"
ln -sf /opt/dev-env/scripts/agent-run.sh "$HOME/.local/bin/agent-run"
touch "$HOME/.bashrc"
grep -q 'dev-env/scripts/bashrc.sh' "$HOME/.bashrc" 2>/dev/null \
  || printf '\n[ -f /opt/dev-env/scripts/bashrc.sh ] && . /opt/dev-env/scripts/bashrc.sh\n' >> "$HOME/.bashrc"
# The PVC home has no skel files, so login shells (bash -l, ssh-style) would skip
# .bashrc entirely — standard Debian bridge:
[ -f "$HOME/.bash_profile" ] \
  || printf '[ -f ~/.bashrc ] && . ~/.bashrc\n' > "$HOME/.bash_profile"

log "done"
