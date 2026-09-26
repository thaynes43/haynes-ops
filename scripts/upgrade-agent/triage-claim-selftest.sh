#!/usr/bin/env bash
# Self-test for shepherd triage's at-most-once remediation claim on the NON-upgrade path
# (triage.sh: nonupgrade_record).
#
# WHY THIS EXISTS
# ---------------
# `attempted=1` on a sig's entry in `upgrade-remediation-state` is the only thing that
# stops triage from summoning remediate (and filing an escalation, and paging) for the
# same regression twice. Until #3182 the non-upgrade path rewrote the entry
# `attempted=0 result=none`: once a remediated regression aged past the 3 h merge
# lookback it was re-classed nonupgrade and the claim was erased, so the next merge to
# main while it still failed re-summoned. Seen live 2026-09-25 (sig cce9e6af45bf,
# ollama-prime). Silent both ways: erase the claim (duplicate spend + pages) or write
# blind over an unreadable CM (same, one step removed). Every case is pinned below.
#
# Rule, same as scripts/work-order-lane-selftest.sh: the functions under test are
# EXTRACTED from the shipped scripts, never copy-pasted. state_upsert and log are the
# only stubs.
#
# Usage: scripts/upgrade-agent/triage-claim-selftest.sh
# Requires: bash, jq. No cluster access.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TRIAGE="${TRIAGE:-$REPO_ROOT/kubernetes/main/apps/upgrade-agent/shepherd/app/resources/triage.sh}"
LIB="${LIB:-$REPO_ROOT/kubernetes/main/apps/upgrade-agent/health-gate/app/resources/coordination-lib.sh}"
command -v jq >/dev/null 2>&1 || { echo "FATAL: jq is required"; exit 1; }

extract() {  # $1=file $2=function name — definition line through its closing brace
  awk -v fn="$2" '
    $0 ~ "^" fn "\\(\\)" { inside = 1 }
    inside { print }
    inside && (/^}/ || /}[[:space:]]*$/) { exit }
  ' "$1"
}
for spec in "$TRIAGE:nonupgrade_record" "$LIB:jget"; do
  f="${spec%%:*}"; fn="${spec##*:}"; body="$(extract "$f" "$fn")"
  case "$body" in *"$fn()"*) ;; *) echo "FATAL: could not extract $fn() from $f"; exit 1 ;; esac
  eval "$body" || { echo "FATAL: extracted $fn() does not parse"; exit 1; }
done
# The shipped call site must use the function (a revert to the raw write would pass
# every case below while the bug is back).
grep -q '^nonupgrade_record "\$SIG" "\$entry" "\$STATE_READ_STATUS"$' "$TRIAGE" \
  || { echo "FATAL: triage.sh no longer calls nonupgrade_record on the nonupgrade path"; exit 1; }
grep -q '^state_upsert "\$SIG" nonupgrade 0 none$' "$TRIAGE" \
  && { echo "FATAL: triage.sh still has the unconditional 'nonupgrade 0 none' write"; exit 1; }

# ── stubs ──
WROTE=""
state_upsert() { WROTE="$*"; }
log() { :; }

fails=0
check() {  # $1=name $2=entry $3=read_status $4=expected write ("" = no write)
  WROTE=""
  nonupgrade_record S "$2" "$3"
  if [ "$WROTE" = "$4" ]; then echo "ok    $1"; else echo "FAIL  $1: wrote '$WROTE', want '$4'"; fails=$((fails+1)); fi
}

check "THE 2026-09-25 CASE: remediated+failed sig ages out -> claim kept" \
      '{"class":"upgrade","attempted":"1","result":"failed"}'  ok "S nonupgrade 1 failed"
check "claim kept with a pending result"   '{"class":"upgrade","attempted":"1","result":"pending"}' ok "S nonupgrade 1 pending"
check "claim kept with no result recorded" '{"class":"upgrade","attempted":"1"}'                    ok "S nonupgrade 1 "
check "never attempted -> 0/none as before" '{"class":"nonupgrade","attempted":"0","result":"none"}' ok "S nonupgrade 0 none"
check "no entry yet -> 0/none as before"    ''                                                        ok "S nonupgrade 0 none"
check "unreadable CM -> no write at all"    '{"attempted":"1","result":"failed"}'                     unreadable ""
check "unreadable CM, no entry -> no write" ''                                                        unreadable ""

echo
[ "$fails" -eq 0 ] && { echo "ALL PASS"; exit 0; } || { echo "$fails FAILED"; exit 1; }
