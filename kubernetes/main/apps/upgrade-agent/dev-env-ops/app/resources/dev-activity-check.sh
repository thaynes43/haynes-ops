#!/usr/bin/env bash
# dev-activity-check.sh [scope-hint ...] — "is a dev-env agent doing this to us?"
#
# Tom, 2026-08-23: "it's important they check dev-env and even interact with
# agents working there to not step on any toes if it was a false escalation
# triggered by dev work (need a way for dev-env agents to try and avoid false
# escalations)."
#
# This is the READ side of that. The WRITE side is `declare-activity` in the
# dev-env pod (kubernetes/main/apps/dev/dev-env/app/resources/declare-activity.sh),
# which drops TTL'd, SCOPED activity records on the dev-env PVC.
#
# TRANSPORT — verified live 2026-08-23, and it corrects a wrong assumption in the
# design brief: the dev-env-ops SA *can* `create pods/exec` in namespace `dev`
# (`dev-env-operator` ClusterRole grants pods/exec:create cluster-wide;
# `kubectl auth can-i create pods --subresource=exec -n dev` → yes; a real exec
# returning `tmux list-sessions` was confirmed). NOTE the syntax trap that made
# it look denied: `kubectl auth can-i create pods/exec` says **no** because
# `pods/exec` is parsed as a resource NAME; you must use `--subresource=exec`.
# So the declared-activity channel needs NO new RBAC and NO ConfigMap write for
# the dev-env SA (which has none: `can-i create configmaps -n dev` → no).
#
# NEVER A MUTE. This produces EVIDENCE, not a suppression verdict. Declared
# activity may only downgrade an incident to "dev-caused" when the remediation
# session has ALSO confirmed the alert is consistent with that work and nothing
# is actually broken. A declaration can never suppress cases 3/4 — if a human is
# needed, escalate regardless of who caused it. See the decision table in
# .agents/runbooks/agentic-remediation.md.
#
# USAGE
#   dev-activity-check.sh                     # full report, human-readable
#   dev-activity-check.sh downloads qbittorrent
#         # additionally scores each declared activity against these scope hints
#         # (alert namespace / app / node) and prints MATCHED / no-match
#   DEV_ACTIVITY_JSON=1 dev-activity-check.sh downloads   # machine-readable
#
# Exit status: 0 always (a signal-gathering tool must never fail a session).
# Read the report; `matched_declarations` is the field that carries weight.
set -uo pipefail

DEV_NS="${DEV_ACTIVITY_NS:-dev}"
STATE_DIR="${DEV_ACTIVITY_DIR:-/home/dev/.local/state/dev-activity}"
LOOKBACK_H="${DEV_ACTIVITY_LOOKBACK_H:-3}"
REPO_CANON="${HOME}/repos/haynes-ops"
AS_JSON="${DEV_ACTIVITY_JSON:-0}"
now="$(date -u +%s)"
hints="$*"

note() { [ "$AS_JSON" = "1" ] || printf '%s\n' "$*"; }

# ── 1. DECLARED ACTIVITY (the primary, deliberate signal) ────────────────────
# One JSON file per declaration on the dev-env PVC. Expired ones are ignored
# here AND pruned by the writer — a TTL is what stops a forgotten declaration
# from muting the cluster forever.
declared='[]'
devpod="$(kubectl -n "$DEV_NS" get pods -l app.kubernetes.io/name=dev-env \
            --field-selector=status.phase=Running \
            -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
[ -n "$devpod" ] || devpod="$(kubectl -n "$DEV_NS" get pods -o name 2>/dev/null \
                                | grep -m1 'dev-env' | cut -d/ -f2)"
if [ -n "$devpod" ]; then
  raw="$(kubectl -n "$DEV_NS" exec "$devpod" -c app -- \
           sh -c "cat ${STATE_DIR}/*.json 2>/dev/null" 2>/dev/null)"
  if [ -n "$raw" ]; then
    declared="$(printf '%s' "$raw" | jq -sc --argjson now "$now" '
      [ .[] | select(type=="object")
        | select(((.expires // "0") | tonumber? // 0) > $now) ]' 2>/dev/null)" || declared='[]'
  fi
else
  note "WARN: no running dev-env pod found in ns/${DEV_NS} — declared-activity channel unavailable."
fi
[ -n "$declared" ] || declared='[]'

# Score declarations against the hints. A declaration's `scope` is a list of
# free-form tokens the declaring agent chose (namespace, app, node, "cluster").
# Match = any hint appears in any scope token or vice versa (substring, both
# directions, case-insensitive), or the declaration scope is the explicit
# wildcard "cluster"/"*" (which the writer discourages but permits).
matched='[]'
if [ -n "$hints" ] && [ "$declared" != "[]" ]; then
  matched="$(printf '%s' "$declared" | jq -c --arg hints "$hints" '
    ($hints | ascii_downcase | split(" ") | map(select(length>1))) as $h
    | [ .[] | . as $d
        | ((.scope // []) | map(ascii_downcase)) as $s
        | select(
            ($s | any(. == "cluster" or . == "*"))
            or ($h | any(. as $x | $s | any(contains($x) or ($x | contains(.)))))
          ) ]' 2>/dev/null)" || matched='[]'
fi
[ -n "$matched" ] || matched='[]'

# ── 2. LIVE SESSIONS (who is even awake in dev-env) ──────────────────────────
sessions=""
[ -n "$devpod" ] && sessions="$(kubectl -n "$DEV_NS" exec "$devpod" -c app -- \
    tmux list-sessions -F '#S' 2>/dev/null | head -20)"

# ── 3. FLUX SUSPENSIONS (a deliberate hold IS dev activity, and it is exactly
#       what makes reconciliation alerts fire) ─────────────────────────────────
suspended="$(kubectl get kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io \
    -A -o json 2>/dev/null \
  | jq -r '[.items[]? | select(.spec.suspend == true)
            | "\(.kind)/\(.metadata.namespace)/\(.metadata.name)"] | .[]' 2>/dev/null | head -20)"

# ── 4. RECENT agent/* BRANCH PUSHES (cheap, credential-light: the canonical
#       clone already exists and the ops bot can fetch) ───────────────────────
branches=""
if [ -d "$REPO_CANON/.git" ]; then
  git -C "$REPO_CANON" fetch --quiet origin '+refs/heads/agent/*:refs/remotes/origin/agent/*' 2>/dev/null
  branches="$(git -C "$REPO_CANON" for-each-ref --sort=-committerdate \
      --format='%(refname:short) %(committerdate:relative) %(subject)' \
      refs/remotes/origin/agent 2>/dev/null \
    | awk -v n="$LOOKBACK_H" '$0 ~ /(second|minute)s? ago/ || ($0 ~ /hours? ago/ && $2+0 <= n)' | head -10)"
fi

# ── 5. OPEN PRs touched recently (the other half of "someone is mid-change") ──
prs=""
if command -v gh >/dev/null 2>&1; then
  GH_TOKEN="$(cat /creds/gh_token 2>/dev/null || true)" \
    prs="$(gh pr list --repo thaynes43/haynes-ops --state open \
             --json number,title,updatedAt,headRefName --limit 15 2>/dev/null \
           | jq -r --argjson now "$now" --argjson lb "$(( LOOKBACK_H * 3600 ))" '
               .[]? | select(((.updatedAt | fromdateiso8601?) // 0) > ($now - $lb))
               | "#\(.number) \(.headRefName) — \(.title)"' 2>/dev/null | head -10)"
fi

n_decl="$(printf '%s' "$declared" | jq 'length' 2>/dev/null || echo 0)"
n_match="$(printf '%s' "$matched" | jq 'length' 2>/dev/null || echo 0)"

if [ "$AS_JSON" = "1" ]; then
  jq -nc --argjson declared "$declared" --argjson matched "$matched" \
     --arg sessions "$sessions" --arg suspended "$suspended" \
     --arg branches "$branches" --arg prs "$prs" --arg hints "$hints" \
     '{hints:$hints, declarations:$declared, matched_declarations:$matched,
       dev_sessions:($sessions|split("\n")|map(select(length>0))),
       flux_suspended:($suspended|split("\n")|map(select(length>0))),
       recent_agent_branches:($branches|split("\n")|map(select(length>0))),
       recent_open_prs:($prs|split("\n")|map(select(length>0)))}'
else
  echo "=== dev-env activity check ($(date -u +%FT%TZ))${hints:+ — scope hints: $hints}"
  echo "--- declared activity (${n_decl} live, ${n_match} matching the hints)"
  if [ "$n_decl" -gt 0 ] 2>/dev/null; then
    printf '%s' "$declared" | jq -r --argjson now "$now" '.[] |
      "  [\(if (.matched // false) then "?" else " " end)] \(.id)  by=\(.who // "?")  scope=\((.scope // [])|join(","))  ttl_left=\(((((.expires // "0")|tonumber? // 0) - $now)/60)|floor)m\n      what: \(.what // "?")"'
  else
    echo "  (none — no dev-env agent has declared disruptive work)"
  fi
  if [ "$n_match" -gt 0 ] 2>/dev/null; then
    echo "--- MATCHED declarations (these plausibly explain the incident — still VERIFY):"
    printf '%s' "$matched" | jq -r '.[] | "  * \(.id): \(.what // "?")  scope=\((.scope // [])|join(","))"'
  elif [ -n "$hints" ]; then
    echo "--- MATCHED declarations: none for [$hints] — treat the incident as NOT dev-caused unless other signals say otherwise."
  fi
  echo "--- dev-env live tmux sessions"; printf '%s\n' "${sessions:-  (none / pod unreachable)}" | sed 's/^/  /'
  echo "--- flux suspensions (deliberate holds)"; printf '%s\n' "${suspended:-  (none)}" | sed 's/^/  /'
  echo "--- agent/* branches pushed in the last ${LOOKBACK_H}h"; printf '%s\n' "${branches:-  (none)}" | sed 's/^/  /'
  echo "--- open PRs updated in the last ${LOOKBACK_H}h"; printf '%s\n' "${prs:-  (none)}" | sed 's/^/  /'
  echo "=== end dev-env activity check"
fi
exit 0
