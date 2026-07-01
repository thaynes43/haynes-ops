#!/usr/bin/env bash
# Tier-4 breaking-change shepherd — main-container entrypoint (phase 4b.1, summoned).
# Mounted from a ConfigMap so the tool allowlist + prompt are auditable in every PR
# diff (not hidden in an image layer).
#
# CONTAINMENT (defense-in-depth; the LLM is prompt-injectable — it reads release notes):
#   - Read-only k8s SA (get/list/watch; no secrets/exec/write) — verify only.
#   - Egress CiliumNetworkPolicy: GitHub + Anthropic + cluster-read ONLY.
#   - The bot PEM is NEVER here — an initContainer minted a short-lived ghs_ token to
#     /creds/gh_token; this container only sees that token (contents+PRs:write, 1h TTL).
#   - --permission-mode dontAsk + an explicit --allowedTools allowlist (auto-denies
#     everything else). NO `gh pr merge`, NO WebFetch/WebSearch, NO kubectl write.
#   - DRY-RUN by default: makes NO changes. Set UPGRADE_AGENT_MODE=shepherd to enable
#     edits + PR authorship.
#
# KNOWN 4b.1 GAP (closed by the 4b.2 guardrails): the token has contents:write, so a
# rogue/injected agent could in principle `git push` straight to main. 4b.1 is
# SUMMONED + SUPERVISED (a human triggers this Job and watches). Do NOT run it
# unattended until the Kyverno admission baseline + diff-scope check + push protection
# land. `gh pr merge` is already blocked here regardless.
set -uo pipefail

MODE="${UPGRADE_AGENT_MODE:-dryrun}"
MODEL="${UPGRADE_AGENT_MODEL:-sonnet}"
MAX_TURNS="${UPGRADE_AGENT_MAX_TURNS:-40}"
MAX_BUDGET="${UPGRADE_AGENT_MAX_BUDGET_USD:-5.00}"
RUN_TIMEOUT="${UPGRADE_AGENT_TIMEOUT:-20m}"
REPO="thaynes43/haynes-ops"
WORKDIR="${HOME}/repo"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

# Quiet claude-code's phone-home (the egress CNP would block it anyway).
export DISABLE_TELEMETRY=1 CLAUDE_CODE_ENABLE_TELEMETRY=0 \
       DISABLE_ERROR_REPORTING=1 DISABLE_AUTOUPDATER=1 DISABLE_NON_ESSENTIAL_MODEL_CALLS=1

: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY unset (llm secret not mounted?)}"
[ -s /creds/gh_token ] || { log "FATAL: /creds/gh_token missing — initContainer token mint failed"; exit 1; }
GH_TOKEN="$(cat /creds/gh_token)"; export GH_TOKEN
# Assert the PEM did NOT leak into this (the LLM) container.
if [ -n "${GITHUB_BOT_APP_PRIVATE_KEY:-}" ]; then
  log "FATAL: bot PEM present in the LLM container env — refusing to run."; exit 3
fi

git config --global user.name  "haynes-ops-bot[bot]"
git config --global user.email "haynes-ops-bot[bot]@users.noreply.github.com"
git config --global safe.directory "${WORKDIR}"

log "cloning ${REPO} (shallow)…"
rm -rf "${WORKDIR}"
git clone --depth 1 "https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git" "${WORKDIR}" >&2 || {
  log "FATAL: clone failed (token/egress?)"; exit 1; }
cd "${WORKDIR}"

# ── Tool allowlist + task, per mode. dontAsk auto-denies anything NOT listed. ──
READONLY_TOOLS=(Read Grep Glob
  "Bash(git log:*)" "Bash(git diff:*)" "Bash(git show:*)" "Bash(git status:*)"
  "Bash(gh pr list:*)" "Bash(gh pr view:*)" "Bash(gh pr diff:*)" "Bash(gh pr checks:*)"
  "Bash(gh release view:*)" "Bash(gh release list:*)" "Bash(gh api repos/thaynes43/*)"
  "Bash(kubectl get:*)" "Bash(kubectl describe:*)" "Bash(flux get:*)" "Bash(grep:*)" "Bash(cat:*)")
WRITE_TOOLS=(Edit Write
  "Bash(git switch:*)" "Bash(git checkout -b:*)" "Bash(git add:*)" "Bash(git commit:*)"
  "Bash(git push:*)" "Bash(gh pr create:*)" "Bash(gh pr comment:*)")

# NB: set PROMPT defaults on their own line — NOT inline via ${UPGRADE_AGENT_PROMPT:-...}.
# An apostrophe inside a ${VAR:-default} (e.g. "component's") breaks bash quote parsing.
PROMPT="${UPGRADE_AGENT_PROMPT:-}"
if [ "$MODE" = "shepherd" ]; then
  ALLOWED=("${READONLY_TOOLS[@]}" "${WRITE_TOOLS[@]}")
  [ -n "$PROMPT" ] || PROMPT="You are the Tier-4 upgrade shepherd. Follow .agents/runbooks/upgrade-shepherd.md exactly. Survey open manual-tier Renovate PRs (gh pr list); pick the NEXT one by the runbook merge-order. CONSULT .renovate/holds.json5 first (skip if held). Read the release notes (gh release view) and the component section in .agents/runbooks/tier4-component-playbooks.md. Make the required supporting helmrelease/values edits on a NEW branch shepherd/<pkg>-<version>, commit, push, and open a PR with gh pr create. Do NOT merge, do NOT push to main, do NOT touch anything outside kubernetes/**. One PR only, then stop and summarize."
else
  ALLOWED=("${READONLY_TOOLS[@]}")
  [ -n "$PROMPT" ] || PROMPT="DRY RUN - make NO changes. You are the Tier-4 upgrade shepherd. Survey open manual-tier Renovate PRs (gh pr list) and, for the next one per .agents/runbooks/upgrade-shepherd.md, REPORT: is it held (.renovate/holds.json5)? what supporting helmrelease/values edits would it need (per tier4-component-playbooks.md)? Output a concise plan. Do NOT edit files, push, or open PRs."
fi

log "MODE=$MODE model=$MODEL max_turns=$MAX_TURNS budget=\$$MAX_BUDGET"
set +e
timeout "$RUN_TIMEOUT" claude -p "$PROMPT" \
  --permission-mode dontAsk \
  --allowedTools "${ALLOWED[@]}" \
  --disallowedTools "WebFetch" "WebSearch" \
  --append-system-prompt "SAFETY: read-only cluster default; ALL cluster changes go via a PR to kubernetes/**; NEVER kubectl apply/exec/delete; NEVER gh pr merge; NEVER push to main; stay inside kubernetes/**." \
  --max-turns "$MAX_TURNS" \
  --max-budget-usd "$MAX_BUDGET" \
  --model "$MODEL" \
  --output-format json
rc=$?
set -e 2>/dev/null || true
log "claude exited rc=$rc"
exit "$rc"
