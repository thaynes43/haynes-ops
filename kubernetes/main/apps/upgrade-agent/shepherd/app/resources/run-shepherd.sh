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

# ── Monthly spend guard (defense-in-depth; the Anthropic account balance is the HARD
# backstop) ── Tracks bot spend in a ConfigMap and REFUSES an UNATTENDED run (auto/
# remediate) once month-to-date + this run's cap would exceed MONTHLY_CAP. Manual modes
# (dryrun/shepherd) still record spend but are never blocked (a human chose to run them).
MONTHLY_CAP="${UPGRADE_AGENT_MONTHLY_CAP_USD:-50}"
SPEND_NS="${UPGRADE_AGENT_NAMESPACE:-upgrade-agent}"
SPEND_CM="${UPGRADE_AGENT_SPEND_CM:-upgrade-shepherd-spend}"
SPEND_MONTH=""; SPEND_PRIOR="0"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

# Returns 0 = proceed, 1 = skip (cap reached). Fails OPEN on any kubectl/RBAC error
# (the account balance + per-run --max-budget-usd are the real ceilings).
spend_guard() {
  SPEND_MONTH="$(date -u +%Y-%m)"
  local cm; cm="$(kubectl -n "$SPEND_NS" get configmap "$SPEND_CM" -o json 2>/dev/null)" || {
    log "spend-guard: cannot read $SPEND_CM (proceeding; account balance is the backstop)"; return 0; }
  local m; m="$(printf '%s' "$cm" | jq -r '.data.month // ""')"
  SPEND_PRIOR="$(printf '%s' "$cm" | jq -r '.data.spent_usd // "0"')"
  [ "$m" = "$SPEND_MONTH" ] || SPEND_PRIOR="0"   # new month => reset
  if awk -v s="$SPEND_PRIOR" -v b="$MAX_BUDGET" -v c="$MONTHLY_CAP" 'BEGIN{exit !(s + b > c)}'; then
    log "spend-guard: month-to-date \$$SPEND_PRIOR + run cap \$$MAX_BUDGET would exceed \$$MONTHLY_CAP — SKIPPING."
    return 1
  fi
  log "spend-guard: month-to-date \$$SPEND_PRIOR / \$$MONTHLY_CAP — OK (run cap \$$MAX_BUDGET)."
  return 0
}

# Best-effort: add this run's actual cost (from claude's JSON) to the ConfigMap. Uses
# create-or-apply (the CM is runtime state, deliberately NOT in git — Flux would revert
# the counter to its seed value every reconcile).
record_spend() {
  local cost; cost="$(jq -r '.total_cost_usd // .cost_usd // 0' "$1" 2>/dev/null)"
  [ -n "$cost" ] && [ "$cost" != "null" ] || cost=0
  [ -n "$SPEND_MONTH" ] || SPEND_MONTH="$(date -u +%Y-%m)"
  local new; new="$(awk -v a="${SPEND_PRIOR:-0}" -v b="$cost" 'BEGIN{printf "%.4f", a + b}')"
  if kubectl -n "$SPEND_NS" create configmap "$SPEND_CM" \
       --from-literal=month="$SPEND_MONTH" --from-literal=spent_usd="$new" \
       --dry-run=client -o yaml | kubectl -n "$SPEND_NS" apply -f - >/dev/null 2>&1; then
    log "spend-guard: recorded run cost \$$cost; month-to-date now \$$new / \$$MONTHLY_CAP."
  else
    log "spend-guard: WARN could not record cost \$$cost (RBAC?)."
  fi
}

# ── Deterministic PRE-FILTER (Phase D cost control) ── On a SCHEDULED auto run, only
# summon the (paid) LLM when an open Renovate PR actually touches a component in the
# current auto-merge ramp. On a quiet day (no in-scope PR) this exits $0 with ZERO LLM
# cost. It does NOT decide mergeability — the LLM still fully vets every in-scope PR
# (holds, release notes, supporting edits). The filter only decides IF there is work
# worth looking at, keeping the conservative posture: deterministic layer gates the
# look, LLM owns the judgement.
#
# Gated by UPGRADE_AGENT_PREFILTER_GLOBS — a space-separated list of repo path prefixes
# (one per ramp component), set ONLY on the scheduled cronjob. A manual/targeted summon
# leaves it empty (or clears it) and is never filtered — it runs as asked. WIDEN THE
# RAMP by adding the new component's path prefix HERE (via the HR env) AND naming it in
# UPGRADE_AGENT_PROMPT, in the same commit. Fails OPEN (returns 0 = proceed to the LLM)
# on any gh/jq error — never silently skips real work.
#
# Returns 0 = an in-scope PR exists (or we're unsure) -> run the LLM.
# Returns 1 = definitively no in-scope open Renovate PR -> caller should exit $0.
prefilter_should_run() {
  local globs="$1" prs pr files g
  # -R "$REPO" is REQUIRED: the pre-filter runs BEFORE the clone, so gh has no git
  # remote to infer the repo from — a bare `gh pr list` errors "not a git repository"
  # and the filter fails open (runs the LLM) every time (bit us live 2026-07-04).
  prs="$(gh pr list -R "$REPO" --state open --limit 200 --json number,author \
           --jq '.[] | select(.author.login|test("renovate")) | .number' 2>/dev/null)" || {
    log "pre-filter: gh pr list failed — failing OPEN (running the LLM)."; return 0; }
  [ -n "$prs" ] || { log "pre-filter: no open Renovate PRs — skipping LLM (\$0)."; return 1; }
  local checked=0
  for pr in $prs; do
    files="$(gh pr view "$pr" -R "$REPO" --json files --jq '.files[].path' 2>/dev/null)" || {
      log "pre-filter: gh pr view #$pr failed — failing OPEN (running the LLM)."; return 0; }
    checked=$((checked + 1))
    for g in $globs; do
      if printf '%s\n' "$files" | grep -qF -- "$g"; then
        log "pre-filter: Renovate PR #$pr touches ramp path '$g' — summoning the LLM."
        return 0
      fi
    done
  done
  log "pre-filter: checked $checked open Renovate PR(s); none touch the ramp — skipping LLM (\$0). Ramp: $globs"
  return 1
}

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

# Pre-filter BEFORE the clone/LLM: a scheduled auto run with no in-scope PR exits here
# at $0 (skips clone + LLM). Only when MODE=auto AND the ramp-globs var is set (i.e. the
# scheduled cronjob). Manual/targeted summons leave it empty and are never filtered.
if [ "$MODE" = "auto" ] && [ -n "${UPGRADE_AGENT_PREFILTER_GLOBS:-}" ]; then
  if ! prefilter_should_run "${UPGRADE_AGENT_PREFILTER_GLOBS}"; then
    log "run skipped by pre-filter (no in-scope open PR) — no clone, no LLM, \$0."
    exit 0
  fi
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
# gh pr merge is allowed ONLY in auto/remediate modes. Note this is NOT the safety
# boundary: the bot is non-admin + non-bypass, so `gh pr merge --auto` only QUEUES and
# GitHub merges server-side ONLY when Flux Local + Diff Scope are both green. It cannot
# merge past a red/pending check, and `--admin` (skip-checks) fails for a non-admin.
MERGE_TOOLS=("Bash(gh pr merge:*)")

# NB: set PROMPT/SAFETY defaults on their OWN line — NOT inline via ${VAR:-default}.
# An apostrophe or brace inside a ${VAR:-default} breaks bash quote parsing.
PROMPT="${UPGRADE_AGENT_PROMPT:-}"
SAFETY_MERGE="NEVER gh pr merge, NEVER push to main"
case "$MODE" in
  shepherd)  # open a PR, human merges (Phase 4b.1)
    ALLOWED=("${READONLY_TOOLS[@]}" "${WRITE_TOOLS[@]}")
    [ -n "$PROMPT" ] || PROMPT="You are the Tier-4 upgrade shepherd. Follow .agents/runbooks/upgrade-shepherd.md exactly. Survey open manual-tier Renovate PRs (gh pr list); pick the NEXT one by the runbook merge-order. CONSULT .renovate/holds.json5 first (skip if held). Read the release notes (gh release view) and the component section in .agents/runbooks/tier4-component-playbooks.md. Make the required supporting helmrelease/values edits on a NEW branch shepherd/<pkg>-<version>, commit, push, and open a PR with gh pr create. Do NOT merge, do NOT push to main, do NOT touch anything outside kubernetes/**. One PR only, then stop and summarize."
    ;;
  auto)      # open a PR AND enable server-side auto-merge (Phase 4b.3)
    ALLOWED=("${READONLY_TOOLS[@]}" "${WRITE_TOOLS[@]}" "${MERGE_TOOLS[@]}")
    SAFETY_MERGE="you MAY enable auto-merge with 'gh pr merge <N> --auto --squash --delete-branch' AFTER opening the PR; NEVER merge immediately, NEVER use --admin, NEVER push to main"
    [ -n "$PROMPT" ] || PROMPT="You are the Tier-4 upgrade shepherd in AUTO mode. Follow .agents/runbooks/upgrade-shepherd.md. Survey open manual-tier Renovate PRs (gh pr list); pick the NEXT one by the runbook merge-order. CONSULT .renovate/holds.json5 first (skip if held). Read the release notes and the component playbook. If supporting edits are needed, make them on a NEW branch shepherd/<pkg>-<version>, commit, push, and open a PR (gh pr create); then enable auto-merge with gh pr merge <N> --auto --squash --delete-branch. GitHub merges only when Flux Local AND Diff Scope are both green. Do NOT merge immediately, do NOT push to main, do NOT touch anything outside kubernetes/**. One PR only, then stop and summarize."
    ;;
  remediate) # mode 2: diagnose a regression, forward-fix or rollback+hold (Phase 4b.3)
    ALLOWED=("${READONLY_TOOLS[@]}" "${WRITE_TOOLS[@]}" "${MERGE_TOOLS[@]}")
    SAFETY_MERGE="you MAY open a forward-fix or rollback PR and enable auto-merge with 'gh pr merge <N> --auto'; NEVER merge immediately, NEVER use --admin, NEVER push to main; if git-alone cannot converge, STOP and page a human"
    [ -n "$PROMPT" ] || PROMPT="You are the Tier-4 upgrade shepherd in REMEDIATE mode (Mode 2). A recent upgrade may have regressed. Follow .agents/runbooks/upgrade-shepherd.md Mode 2. Diagnose READ-ONLY (flux get, kubectl describe/get). Identify the culprit merge (git log). Attempt the documented rollback from .agents/runbooks/tier4-component-playbooks.md: git revert the bump or re-pin the prior version on a NEW branch, commit, push, open a PR, enable auto-merge (gh pr merge <N> --auto). If a supporting forward-fix is the right call instead, do that. If the rollback cannot converge from git alone (immutable field, wedged HelmRelease, stuck finalizer, one-way major), do NOT improvise a cluster write; record a hold in .renovate/holds.json5 if applicable and STOP with a diagnosis for a human. Do NOT push to main, stay inside kubernetes/**."
    ;;
  *)         # dryrun (default): read-only, report a plan
    ALLOWED=("${READONLY_TOOLS[@]}")
    [ -n "$PROMPT" ] || PROMPT="DRY RUN - make NO changes. You are the Tier-4 upgrade shepherd. Survey open manual-tier Renovate PRs (gh pr list) and, for the next one per .agents/runbooks/upgrade-shepherd.md, REPORT: is it held (.renovate/holds.json5)? what supporting helmrelease/values edits would it need (per tier4-component-playbooks.md)? Output a concise plan. Do NOT edit files, push, or open PRs."
    ;;
esac

# Populate month-to-date spend + enforce the cap on UNATTENDED runs only.
spend_guard; guard_rc=$?
if [ "$guard_rc" -ne 0 ]; then
  case "$MODE" in
    auto|remediate) log "run skipped by spend guard (monthly cap reached)"; exit 0 ;;
    *) log "spend-guard: cap reached but MODE=$MODE is manual/human-summoned — proceeding." ;;
  esac
fi

log "MODE=$MODE model=$MODEL max_turns=$MAX_TURNS budget=\$$MAX_BUDGET cap=\$$MONTHLY_CAP"
set +e
OUT_FILE="$(mktemp 2>/dev/null || echo /tmp/claude-out.json)"
timeout "$RUN_TIMEOUT" claude -p "$PROMPT" \
  --permission-mode dontAsk \
  --allowedTools "${ALLOWED[@]}" \
  --disallowedTools "WebFetch" "WebSearch" \
  --append-system-prompt "SAFETY: read-only cluster default; ALL cluster changes go via a PR to kubernetes/**; NEVER kubectl apply/exec/delete; ${SAFETY_MERGE}; stay inside kubernetes/**." \
  --max-turns "$MAX_TURNS" \
  --max-budget-usd "$MAX_BUDGET" \
  --model "$MODEL" \
  --output-format json | tee "$OUT_FILE"
rc=${PIPESTATUS[0]}
set -e 2>/dev/null || true
record_spend "$OUT_FILE"
log "claude exited rc=$rc"
exit "$rc"
