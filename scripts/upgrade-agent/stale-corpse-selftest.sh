#!/usr/bin/env bash
# Self-test for the stale-corpse filter the health gate and shepherd triage share
# (coordination-lib.sh: pod_is_stale_corpse). No cluster access: kubectl is a stub.
#
# WHY THIS EXISTS
# ---------------
# The filter decides which terminal Failed pods are HISTORY (dropped from the
# coordination set) and which are a live regression (kept -> sig -> remediate -> page).
# Both ways to be wrong are silent: drop too much and a failing deploy never pages;
# keep too much and the gate pages a human for a corpse (2026-07-24/25/28, 2026-09-13,
# 2026-10-08). Every rule's boundary is pinned below.
#
# 2026-10-08 (rule C): a dev-env probe Job (downloads/books-825-fresh-census-projection-
# 1008-0338, hand-made, exit 1) borrowed app.kubernetes.io/name=books-census; a merge had
# touched that app 34 minutes earlier, so triage classed it class=upgrade, spent a
# remediate run concluding "not a regression", recorded result=failed and paged
# (esc-shepherd-80441793). A Job with no ownerReferences and no Flux apply label can
# only have reached the cluster through kubectl, and no merge can change it.
#
# Rule, same as coordination-sig-selftest.sh: the function under test is EXTRACTED from
# the shipped lib, never copy-pasted, so the test cannot drift from what the cluster runs.
#
# Usage: scripts/upgrade-agent/stale-corpse-selftest.sh
# Requires: bash, awk, jq. No cluster access.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="${SRC:-$REPO_ROOT/kubernetes/main/apps/upgrade-agent/health-gate/app/resources/coordination-lib.sh}"
[ -r "$SRC" ] || { echo "FATAL: cannot read $SRC"; exit 1; }
command -v jq >/dev/null || { echo "FATAL: jq required"; exit 1; }

extract() {  # $1=function name — definition line through its closing brace
  awk -v fn="$1" '
    $0 ~ "^" fn "\\(\\)" { inside = 1 }
    inside { print }
    inside && /^}/ { exit }
  ' "$SRC"
}
body="$(extract pod_is_stale_corpse)"
[ -n "$body" ] || { echo "FATAL: pod_is_stale_corpse not found in $SRC"; exit 1; }
eval "$body"
COORD_STALE_POD_HOURS="$(grep -E '^COORD_STALE_POD_HOURS=' "$SRC" | sed -E 's/.*:-([0-9]+)\}.*/\1/')"
COORD_STALE_POD_HOURS="${COORD_STALE_POD_HOURS:-24}"
NOW="$(date -u +%s)"
FRESH="$(date -u -d "-20 minutes" +%FT%TZ)"
OLDER="$(date -u -d "-30 minutes" +%FT%TZ)"
NEWER="$(date -u -d "-10 minutes" +%FT%TZ)"

# ── fixtures ── one namespace, several Jobs; POD_JSON / JOBS_JSON are what the stub serves.
job() {  # $1=name $2=ownerRefs-json $3=labels-json $4=succeeded $5=created
  jq -cn --arg n "$1" --argjson o "$2" --argjson l "$3" --argjson s "$4" --arg c "$5" \
    '{metadata:{name:$n, ownerReferences:$o, labels:$l, creationTimestamp:$c}, status:{succeeded:$s}}'
}
CJ='[{"kind":"CronJob","name":"books-census"}]'
FLUX='{"kustomize.toolkit.fluxcd.io/name":"vexa","kustomize.toolkit.fluxcd.io/namespace":"ai"}'
HELM='{"helm.toolkit.fluxcd.io/name":"haynes-quest","helm.toolkit.fluxcd.io/namespace":"frontend"}'
ADHOC='{"app.kubernetes.io/name":"books-census","job-name":"books-825-fresh-census-projection-1008-0338"}'
JOBS_JSON="$(jq -cn --argjson a "$(job adhoc-failed            '[]'   "$ADHOC" 0 "$OLDER")" \
                    --argjson b "$(job flux-init-failed        '[]'   "$FLUX"  0 "$OLDER")" \
                    --argjson h "$(job helm-init-failed        '[]'   "$HELM"  0 "$OLDER")" \
                    --argjson c "$(job books-census-29856135   "$CJ"  '{}'     0 "$OLDER")" \
                    --argjson d "$(job books-census-29856136   "$CJ"  '{}'     1 "$NEWER")" \
                    --argjson e "$(job retried-then-ok         '[]'   "$ADHOC" 1 "$OLDER")" \
                    --argjson o "$(job operator-owned          '[{"kind":"ReplicationSource","name":"lazylibrarian"}]' '{}' 0 "$OLDER")" \
                    '{items:[$a,$b,$h,$c,$d,$e,$o]}')"
pod() {  # $1=name $2=job $3=phase $4=startTime
  jq -cn --arg n "$1" --arg j "$2" --arg p "$3" --arg t "$4" \
    '{metadata:{name:$n, ownerReferences:[{kind:"Job",name:$j}]}, status:{phase:$p, startTime:$t}}'
}
POD_JSON=""; POD_RC=0
kubectl() {  # stub: `kubectl -n ns get pod NAME -o json` | `kubectl -n ns get jobs -o json`
  case "$*" in
    *"get pod "*)  [ "$POD_RC" -eq 0 ] && printf '%s' "$POD_JSON" || { echo 'Error from server (NotFound): pods "x" not found' >&2; return 1; } ;;
    *"get jobs"*)  printf '%s' "$JOBS_JSON" ;;
    *) echo "stub: unexpected kubectl $*" >&2; return 1 ;;
  esac
}
log() { :; }

fails=0
check() {  # $1=name $2=pod-json $3=pod-rc $4=want (drop|keep)
  POD_JSON="$2"; POD_RC="$3"
  if pod_is_stale_corpse downloads x; then got=drop; else got=keep; fi
  if [ "$got" = "$4" ]; then echo "ok    $1"; else echo "FAIL  $1: got $got, want $4"; fails=$((fails+1)); fi
}

echo "rule C (2026-10-08): ad-hoc Jobs"
check "THE 2026-10-08 CASE: hand-made Job (no owner, no Flux label), Failed -> drop" \
      "$(pod p1 adhoc-failed Failed "$FRESH")" 0 drop
check "Flux kustomize-applied one-shot Job (no owner, Flux label), Failed -> keep (a real deploy fault)" \
      "$(pod p2 flux-init-failed Failed "$FRESH")" 0 keep
check "Flux helm-applied one-shot Job (no owner, helm label), Failed -> keep" \
      "$(pod p2h helm-init-failed Failed "$FRESH")" 0 keep
check "operator-owned Job (ownerReference, no Flux label), Failed -> keep" \
      "$(pod p3 operator-owned Failed "$FRESH")" 0 keep
check "hand-made Job but pod still Running -> keep (only terminal corpses are filterable)" \
      "$(pod p4 adhoc-failed Running "$FRESH")" 0 keep
check "hand-made Job but pod Pending -> keep" \
      "$(pod p4b adhoc-failed Pending "$FRESH")" 0 keep
check "owning Job missing from the listing -> keep (fail-noisy)" \
      "$(pod p5 not-listed Failed "$FRESH")" 0 keep
check "pod with no Job owner at all -> keep (rules need a Job)" \
      "$(jq -cn --arg t "$FRESH" '{metadata:{name:"bare"}, status:{phase:"Failed", startTime:$t}}')" 0 keep

echo "pre-existing rules still hold"
check "A0: own Job converged (retried then succeeded) -> drop" \
      "$(pod p6 retried-then-ok Failed "$FRESH")" 0 drop
check "A: CronJob-owned, a NEWER Job succeeded -> drop" \
      "$(pod p7 books-census-29856135 Failed "$FRESH")" 0 drop
check "A0 on a CronJob-owned Job that retried then succeeded -> drop" \
      "$(pod p8 books-census-29856136 Failed "$FRESH")" 0 drop
# A genuinely failing cron: the newest Job failed and only an older one succeeded.
JOBS_SAVE="$JOBS_JSON"
JOBS_JSON="$(printf '%s' "$JOBS_JSON" | jq -c --argjson n "$(job books-census-29856137 "$CJ" '{}' 0 "$(date -u -d '-5 minutes' +%FT%TZ)")" '.items += [$n]')"
check "A: CronJob-owned newest run failed, older success only -> keep (a genuinely failing cron pages)" \
      "$(pod p9 books-census-29856137 Failed "$FRESH")" 0 keep
JOBS_JSON="$JOBS_SAVE"
check "B: ancient Failed corpse (> COORD_STALE_POD_HOURS) -> drop" \
      "$(pod p10 operator-owned Failed "$(date -u -d "-$(( COORD_STALE_POD_HOURS + 1 )) hours" +%FT%TZ)")" 0 drop
check "pod gone from the API (NotFound) -> drop" "" 1 drop

echo
[ "$fails" -eq 0 ] && { echo "ALL PASS"; exit 0; } || { echo "$fails FAILED"; exit 1; }
