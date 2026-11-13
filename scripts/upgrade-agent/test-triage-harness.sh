#!/usr/bin/env bash
# test-triage-harness.sh — runs ONE function of the shipped triage.sh against whatever kubectl is
# on PATH (the story 554 selftests put scripts/upgrade-agent/test-fake-cluster.mjs there). The
# functions are EXTRACTED from the shipped files, never copied, like the other shell selftests;
# log is the only stub, and it writes to stderr.
#
# Usage: test-triage-harness.sh <function> [args...]
#   state_claim <sig> <ids>      triage's claim (story 554), rc 0 = claimed
#   lane_hold <sig> <ids>        reads the ConfigMap first; rc 0 = the lane holds it (stdout says which)
#   state_upsert <sig> <class> <attempted> <result> [note]
#   nonupgrade_record <sig>      reads the ConfigMap first, as triage does
# Env: NOW (epoch; default now), TRIAGE, LIB (the shipped files by default).
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TRIAGE="${TRIAGE:-$REPO_ROOT/kubernetes/main/apps/upgrade-agent/shepherd/app/resources/triage.sh}"
LIB="${LIB:-$REPO_ROOT/kubernetes/main/apps/upgrade-agent/health-gate/app/resources/coordination-lib.sh}"
extract() {  # $1=file $2=function: its definition through the first line that is only "}"
  awk -v fn="$2" '$0 ~ "^" fn "\\(\\)" { inside = 1 } inside { print } inside && /^}/ { exit }' "$1"
}
NOW="${NOW:-$(date -u +%s)}"
STATE_TTL_HOURS="${STATE_TTL_HOURS:-24}"
log() { printf 'triage: %s\n' "$*" >&2; }
# shellcheck disable=SC1090
. "$LIB" || { echo "FATAL: cannot source $LIB" >&2; exit 90; }
for fn in state_read entry_of state_upsert nonupgrade_record lane_hold state_claim; do
  body="$(extract "$TRIAGE" "$fn")"
  case "$body" in *"$fn()"*) ;; *) echo "FATAL: could not extract $fn() from $TRIAGE" >&2; exit 91 ;; esac
  eval "$body" || { echo "FATAL: $fn() does not parse" >&2; exit 92; }
done
STATE_READ_STATUS=""; STATE_JSON=""
fn="$1"; shift
case "$fn" in
  lane_hold) state_read; [ "$STATE_READ_STATUS" = ok ] || exit 3; lane_hold "$STATE_JSON" "$1" "$2" ;;
  nonupgrade_record) state_read; e=""; [ "$STATE_READ_STATUS" = ok ] && e="$(entry_of "$STATE_JSON" "$1")"; nonupgrade_record "$1" "$e" "$STATE_READ_STATUS" ;;
  *) "$fn" "$@" ;;
esac
