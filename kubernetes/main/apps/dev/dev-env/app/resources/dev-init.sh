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

# ── Claude: pod-wide default model (GitOps-declared) ────────────────────────────
# ~/.claude/settings.json lives on the PVC, and an in-session `/model` (Enter, not
# `s`) rewrites its `model` key — the default for EVERY future bare `claude`
# session in this pod, not just the one that changed it (that is how the default
# silently became Opus 4.8 on 2026-08-29). Re-assert the declared default on every
# boot; only the `model` key is touched — permissions, statusLine, theme and the
# per-model effort claude saves under modelSettings stay as they are on the PVC.
# agent-run passes --model explicitly regardless, so this governs bare `claude`.
# claude-fable-5-1 = Fable 5.1: 1M window by default on the API (no [1m] suffix);
# needs claude-code >=2.1.255 (Dockerfile CLAUDE_CODE_VERSION) or the CLI rejects
# the id outright. Model policy lives in config/claude/CLAUDE.md.
DEV_ENV_CLAUDE_MODEL="${DEV_ENV_CLAUDE_MODEL:-claude-fable-5-1}"
if command -v jq >/dev/null 2>&1; then
  sj="$HOME/.claude/settings.json"
  [ -s "$sj" ] || printf '{}\n' > "$sj"
  if jq -e --arg m "$DEV_ENV_CLAUDE_MODEL" '.model == $m' "$sj" >/dev/null 2>&1; then
    log "claude default model already $DEV_ENV_CLAUDE_MODEL"
  elif jq --arg m "$DEV_ENV_CLAUDE_MODEL" '.model = $m' "$sj" > "$sj.tmp" 2>/dev/null \
       && cat "$sj.tmp" > "$sj"; then   # cat, not mv: keeps the PVC file's mode/inode
    rm -f "$sj.tmp"
    log "claude default model → $DEV_ENV_CLAUDE_MODEL (settings.json 'model' re-asserted)"
  else
    rm -f "$sj.tmp"
    log "WARN could not set claude default model in $sj (unparseable? left untouched)"
  fi
fi

# ── Codex: declarative config, auth untouched ───────────────────────────────────
cp -f "$CFG/codex/config.toml" "$HOME/.codex/config.toml"

# ── Codex: managed STANDALONE install (enables `codex remote-control`) ───────────
# The npm codex can't run remote-control — it needs the standalone install at
# ~/.codex/packages/standalone. The image can't carry it (the home PVC mounts over
# ~/.codex), so install it here at boot, pinned to $CODEX_VERSION, onto the PVC.
# Puts the launcher at ~/.local/bin/codex (leads PATH via the image ENV). Idempotent
# (skips when already at the pin); the PVC persists it across pod rolls, so this is
# a one-time cost per home volume. install.sh pulls from GitHub releases (allowed by
# the egress policy). A failure only costs remote-control this boot — logged, not fatal.
if [ -n "${CODEX_VERSION:-}" ]; then
  have="$("$HOME/.local/bin/codex" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)"
  if [ "$have" = "$CODEX_VERSION" ]; then
    log "codex standalone $CODEX_VERSION present"
  else
    log "installing codex standalone $CODEX_VERSION (have: ${have:-none})…"
    if curl -fsSL "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/install.sh" \
         | sh -s -- --release "$CODEX_VERSION" >/tmp/codex-install.log 2>&1; then
      log "codex standalone installed → $("$HOME/.local/bin/codex" --version 2>/dev/null || echo '?')"
    else
      log "WARN codex standalone install failed (see /tmp/codex-install.log) — remote-control unavailable this boot"
    fi
  fi
fi

# ── kubectl-cnpg: pinned plugin install (CNPG instance surgery) ─────────────────
# `kubectl cnpg destroy <cluster> <n>` is the sanctioned fix for the recurring
# diverged-standby wedge (deletes the instance's PVC + pod so the operator
# re-clones; PVC delete is granted namespace-scoped in the database app's
# dev-env-rbac.yaml). Pin tracks the in-cluster operator minor — bump together.
# Same contract as codex above: GitHub releases → ~/.local/bin on the PVC,
# idempotent, non-fatal (fallback is plain PVC+pod delete).
CNPG_PLUGIN_VERSION="${CNPG_PLUGIN_VERSION:-1.30.0}"
have="$("$HOME/.local/bin/kubectl-cnpg" version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
if [ "$have" = "$CNPG_PLUGIN_VERSION" ]; then
  log "kubectl-cnpg $CNPG_PLUGIN_VERSION present"
else
  log "installing kubectl-cnpg $CNPG_PLUGIN_VERSION (have: ${have:-none})…"
  mkdir -p "$HOME/.local/bin"  # fresh-PVC boot: this runs before the shell-defaults mkdir below
  if curl -fsSL "https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v${CNPG_PLUGIN_VERSION}/kubectl-cnpg_${CNPG_PLUGIN_VERSION}_linux_x86_64.tar.gz" \
       | tar -xz -C "$HOME/.local/bin" kubectl-cnpg 2>/tmp/cnpg-plugin-install.log; then
    chmod +x "$HOME/.local/bin/kubectl-cnpg"
    log "kubectl-cnpg installed → $("$HOME/.local/bin/kubectl-cnpg" version 2>/dev/null || echo '?')"
  else
    log "WARN kubectl-cnpg install failed (see /tmp/cnpg-plugin-install.log) — fall back to PVC+pod delete"
  fi
fi

# ── git identity + credentials (saga Decision #5: haynes-ops-bot everywhere) ────
# Commits author as the bot; pushes/clones read the fresh minted token per call via
# the credential helper (never a static token in .gitconfig). The unset-all first is
# load-bearing: a stray `gh auth setup-git` leaves a SECOND helper value on the PVC
# gitconfig, plain `git config` refuses to replace a multi-valued key (so this line
# silently no-ops every boot), and gh's helper then serves the shell's snapshotted
# GH_TOKEN — a 60-min app token that any >1h-old shell holds dead (2026-08-19: broke
# agent-run fetches from aged shells with "Invalid username or token").
git config --global user.name  "haynes-dev-bot[bot]"
git config --global user.email "haynes-dev-bot[bot]@users.noreply.github.com"
git config --global --unset-all credential."https://github.com".helper 2>/dev/null || true
git config --global --unset-all credential."https://gist.github.com".helper 2>/dev/null || true
git config --global credential."https://github.com".helper \
  '!f() { echo username=x-access-token; echo "password=$(cat /creds/gh_token)"; }; f'
git config --global --replace-all safe.directory "$HOME/repos/*"

# ── workspace README (the first thing code-server shows in the file tree) ───────
# COPIED, not symlinked: a symlink into the read-only ConfigMap mount makes the editor
# fail on save with a confusing EROFS. Git is the source of truth — a boot overwrites
# local edits on purpose.
[ -f "$CFG/home/README.md" ] && cp -f "$CFG/home/README.md" "$HOME/README.md"

# ── playwright browsers: seed the PVC cache from the image's staging dir ────────
# PLAYWRIGHT_BROWSERS_PATH is on the PVC so repos can add their OWN pinned chromium
# revision (readOnlyRootFilesystem forbids writing an image path). Seed once; a repo's
# `playwright install` then adds revisions next to it.
mkdir -p "$HOME/.cache/ms-playwright"
if [ -d /opt/dev-env/ms-playwright ] && [ -z "$(ls -A "$HOME/.cache/ms-playwright" 2>/dev/null)" ]; then
  cp -a /opt/dev-env/ms-playwright/. "$HOME/.cache/ms-playwright/" 2>/dev/null \
    && log "seeded playwright browsers from image" || log "WARN playwright seed failed"
fi

# ── shell defaults + scripts on PATH ────────────────────────────────────────────
mkdir -p "$HOME/.local/bin"
ln -sf /opt/dev-env/scripts/agent-run.sh "$HOME/.local/bin/agent-run"
# declare-activity (2026-08-23): mounting it in the scripts ConfigMap does NOT make
# it a command. Without this link the CLAUDE.md instructions telling agents to
# declare disruptive work hit "command not found", the false-escalation guard is
# silently absent, and the file just sits unreferenced in /opt/dev-env/scripts.
ln -sf /opt/dev-env/scripts/declare-activity.sh "$HOME/.local/bin/declare-activity"
touch "$HOME/.bashrc"
grep -q 'dev-env/scripts/bashrc.sh' "$HOME/.bashrc" 2>/dev/null \
  || printf '\n[ -f /opt/dev-env/scripts/bashrc.sh ] && . /opt/dev-env/scripts/bashrc.sh\n' >> "$HOME/.bashrc"
# The PVC home has no skel files, so login shells (bash -l, ssh-style) would skip
# .bashrc entirely — standard Debian bridge:
[ -f "$HOME/.bash_profile" ] \
  || printf '[ -f ~/.bashrc ] && . ~/.bashrc\n' > "$HOME/.bash_profile"

log "done"
