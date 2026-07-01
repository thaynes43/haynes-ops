#!/usr/bin/env bash
# scripts/diff-scope.sh — Phase 4b.2 PRIMARY gate (the control that must PREVENT a
# malicious shepherd merge, since the shepherd can edit the Kyverno backstop itself).
# Proves a PR diff never reaches beyond a pure image tag/digest + chart-version (+
# pinned-action) bump and touches nothing sensitive.
#
# TAMPER-PROOFING: the workflow executes THIS script from the BASE branch
# (git show origin/<base>:scripts/diff-scope.sh), never the PR-editable copy, and the
# workflow YAML holds the author gate (the bot lacks Workflows scope). A PR that edits
# scripts/diff-scope.sh runs the trusted base version AND is flagged below.
#
# GATE A (sensitive denylist, hard fail) scans ADDED (+) lines + changed paths, so
# EXISTING privileged workloads (their host*/privileged lines are unchanged context)
# never trip it. GATE B (shape allowlist, fail-closed) requires masked-multiset
# equality of removed vs added lines per file — only a pure version/digest/action-pin
# bump satisfies it. Exit 0 = safe shape; exit 1 = human review required.
set -uo pipefail

BASE_BRANCH="${DIFF_SCOPE_BASE_BRANCH:-main}"
REMOTE="${DIFF_SCOPE_REMOTE:-origin}"

if [ -n "${DIFF_SCOPE_RANGE:-}" ]; then
  RANGE="${DIFF_SCOPE_RANGE}"
else
  if git rev-parse --verify --quiet "${REMOTE}/${BASE_BRANCH}" >/dev/null; then
    BASE_REF="${REMOTE}/${BASE_BRANCH}"
  elif git rev-parse --verify --quiet "${BASE_BRANCH}" >/dev/null; then
    BASE_REF="${BASE_BRANCH}"
  else
    echo "diff-scope: FATAL cannot resolve base '${BASE_BRANCH}'" >&2; exit 2
  fi
  MERGE_BASE="$(git merge-base "${BASE_REF}" HEAD)" || { echo "diff-scope: FATAL merge-base failed" >&2; exit 2; }
  RANGE="${MERGE_BASE}..HEAD"
fi

fail=0
declare -a VIOLATIONS=()
violation() { VIOLATIONS+=("$1"); fail=1; }

normalize() {
  perl -pe '
    s{(uses:\s*[^\s@]+)@[^\s#]+}{$1@REF}g;
    s/sha256:[0-9a-f]{7,64}/sha256:DIGEST/g;
    s/\@[0-9a-f]{7,40}(?![0-9a-f])/@GITSHA/g;
    s/\bv?\d+\.\d+(?:\.[0-9A-Za-z][\w.\-]*)*\b/VER/g;
    s/#\s*v?\d+\b/# VER/g;
    s/\s+$//;
  '
}

mapfile -t NAME_STATUS < <(git diff --no-renames --name-status "${RANGE}")

SENSITIVE_ADD_RE='^\+[[:space:]]*(hostPath|hostNetwork|hostPID|hostIPC|hostUsers|hostPort|privileged:[[:space:]]*true|allowPrivilegeEscalation:[[:space:]]*true|runAsUser:[[:space:]]*0|runAsNonRoot:[[:space:]]*false|procMount|automountServiceAccountToken:[[:space:]]*true|securityContext:|capabilities:|hostAliases:|serviceAccountName:|serviceAccount:)'
SENSITIVE_KIND_RE='^\+[[:space:]]*kind:[[:space:]]*(ClusterRole|ClusterRoleBinding|Role|RoleBinding|ServiceAccount|NetworkPolicy|CiliumNetworkPolicy|CiliumClusterwideNetworkPolicy|CustomResourceDefinition|MutatingWebhookConfiguration|ValidatingWebhookConfiguration|ClusterPolicy|Policy|PolicyException|CleanupPolicy|Deployment|StatefulSet|DaemonSet|Job|CronJob|Pod|ExternalSecret)([[:space:]]|$)'
SENSITIVE_WORD_RE='(cluster-admin|system:masters|:default:default)'
SENSITIVE_NS_RE='^\+[[:space:]]*(namespace|targetNamespace):[[:space:]]*(kube-system|flux-system|kyverno)([[:space:]]|$)'

for entry in "${NAME_STATUS[@]}"; do
  [ -z "$entry" ] && continue
  status="${entry%%$'\t'*}"; path="${entry#*$'\t'}"; base="$(basename "$path")"

  # ---- GATE A: sensitive PATHS ----
  printf '%s' "$base" | grep -qiE 'rbac' && violation "sensitive path (rbac file): ${path}"
  [[ "$path" == kubernetes/*/apps/kyverno/* ]] && violation "touches the Kyverno guardrail tree: ${path}"
  [[ "$path" == scripts/diff-scope.sh ]]      && violation "touches the diff-scope guard itself: ${path}"
  if [[ "$path" == .github/* ]] && [[ "$path" != .github/workflows/* ]]; then
    violation "sensitive path (.github non-workflow): ${path}"
  fi
  if [ "${status:0:1}" = "A" ] && [[ "$path" != kubernetes/* ]]; then
    violation "new file outside kubernetes/**: ${path}"
  fi

  # ---- GATE A: sensitive ADDED content (only '+' lines) ----
  added="$(git diff --no-color "${RANGE}" -- "$path" | grep -E '^\+' | grep -vE '^\+\+\+' || true)"
  printf '%s\n' "$added" | grep -qEi "$SENSITIVE_ADD_RE"  && violation "adds security-sensitive field in ${path}"
  printf '%s\n' "$added" | grep -qE  "$SENSITIVE_KIND_RE" && violation "adds sensitive resource kind in ${path}"
  printf '%s\n' "$added" | grep -qE  "$SENSITIVE_WORD_RE" && violation "adds sensitive token in ${path}"
  printf '%s\n' "$added" | grep -qE  "$SENSITIVE_NS_RE"   && violation "targets kube-system/flux-system/kyverno in ${path}"

  # ---- GATE B: shape allowlist (masked-multiset equality of removed vs added) ----
  rm_lines="$(git diff --no-color "${RANGE}" -- "$path" | grep -E '^-'  | grep -vE '^---' | sed 's/^-//' | normalize | LC_ALL=C sort)"
  add_lines="$(git diff --no-color "${RANGE}" -- "$path" | grep -E '^\+' | grep -vE '^\+\+\+' | sed 's/^+//' | normalize | LC_ALL=C sort)"
  [ "$rm_lines" != "$add_lines" ] && violation "diff exceeds allowed shape (not a pure version/digest bump): ${path}"
done

if [ "$fail" -ne 0 ]; then
  report="$(
    echo "### Diff Scope — human review required"; echo ""
    echo "This PR's diff reaches beyond the auto-mergeable shape (pure image tag/digest +"
    echo "chart \`version:\` + pinned-action bumps) or touches a security-sensitive path/"
    echo "resource, so shepherd auto-merge is blocked. An admin can still merge after review."
    echo ""; echo "Violations:"
    printf '%s\n' "${VIOLATIONS[@]}" | sort -u | sed 's/^/- /'
  )"
  printf '%s\n' "$report"
  [ -n "${GITHUB_STEP_SUMMARY:-}" ] && printf '%s\n' "$report" >> "$GITHUB_STEP_SUMMARY"
  exit 1
fi

echo "diff-scope: OK — pure version/digest/chart-version (or pinned-action) bump. Safe to auto-merge."
[ -n "${GITHUB_STEP_SUMMARY:-}" ] && echo "### Diff Scope — OK (auto-mergeable shape)" >> "$GITHUB_STEP_SUMMARY"
exit 0
