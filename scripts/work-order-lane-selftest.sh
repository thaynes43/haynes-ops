#!/usr/bin/env bash
# Self-test for the dev-env-ops work-order lane gate (single-flight).
#
# WHY THIS EXISTS
# ---------------
# `lane_active()` decides whether a pending work order may spawn. Until
# 2026-09-19 it asked tmux one question — "is there a window named <lane>-*?" —
# and windows outlive their sessions on purpose (session-launch.sh ends in
# `exec bash`, and the reap keeps a done window 24h / a failed one 7d). So a
# FINISHED session pinned its lane for the whole reap TTL: on 2026-09-17
# `wo-cigar-curate-20260917` sat idle at its prompt and the next two DAILY
# curate orders were skipped in silence until the window was killed by hand.
# The same shape on 2026-09-06 came from a window whose session had DIED.
#
# The gate now reads the order's status out of the work-order ConfigMap, which
# is exactly the kind of logic that rots quietly: it fails OPEN (skip a real
# order) or CLOSED (double-spawn a lane) with no alarm either way. Hence a test.
#
# Rule, same as scripts/credential-expiry-selftest.sh: the function under test
# is EXTRACTED from the shipped script, never copy-pasted here, so the test
# cannot drift from what the cluster runs. tmux and the CM snapshot are the only
# stubs — the jq/status logic runs for real.
#
# Usage: scripts/work-order-lane-selftest.sh
# Requires: bash 4+, jq. No cluster access.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${SRC:-$REPO_ROOT/kubernetes/main/apps/upgrade-agent/dev-env-ops/app/resources/work-order-watch.sh}"

command -v jq >/dev/null 2>&1 || { echo "FATAL: jq is required"; exit 1; }
[ -r "$SRC" ] || { echo "FATAL: cannot read $SRC"; exit 1; }

# ── extract the real functions (definition line through its closing brace) ──
extract() {  # $1=function name — the definition line through its closing brace,
             # one-liner (`f() { …; }`) or block, and NOTHING after it.
  awk -v fn="$1" '
    $0 ~ "^" fn "\\(\\)" { inside = 1 }
    inside { print }
    inside && (/^}/ || /}[[:space:]]*$/) { exit }
  ' "$SRC"
}
for fn in lane_active; do
  body="$(extract "$fn")"
  case "$body" in
    *"$fn()"*) ;;
    *) echo "FATAL: could not extract $fn() from $SRC (was it renamed?)"; exit 1 ;;
  esac
  eval "$body" || { echo "FATAL: extracted $fn() does not parse"; exit 1; }
done

# ── stubs ──
WINDOWS=""                      # what tmux reports, one window name per line
tmux() { printf '%s\n' "$WINDOWS" | sed '/^$/d'; }

order() { printf '"%s":"{\\"status\\":\\"%s\\"}"' "$1" "$2"; }   # CM .data entry

fails=0
check() {  # $1=name $2=lane $3=expected(active|idle) $4=windows $5=cm-data-json
  local name="$1" lane="$2" want="$3" got
  WINDOWS="$4"
  if lane_active "$lane" "$5"; then got=active; else got=idle; fi
  if [ "$got" = "$want" ]; then
    printf 'ok   %-58s %s\n' "$name" "$got"
  else
    printf 'FAIL %-58s want=%s got=%s\n' "$name" "$want" "$got"
    fails=$((fails + 1))
  fi
}

CLAIMED="{$(order wo-cigar-curate-20260918 claimed)}"
DONE="{$(order wo-cigar-curate-20260917 done)}"
FAILED="{$(order wo-cigar-curate-20260917 failed)}"
ESCALATED="{$(order wo-cigar-curate-20260917 escalated)}"
PENDING="{$(order wo-cigar-curate-20260919 pending)}"
BOTH="{$(order wo-cigar-curate-20260917 done),$(order wo-cigar-curate-20260918 claimed)}"
ESC_CLAIMED="{$(order esc-shepherd-99e57e39 claimed)}"

echo "== lane gate: a window only holds its lane while its order is non-terminal =="
check "no windows at all"                     wo idle   ""                                              '{}'
check "running session (claimed)"             wo active "wo-cigar-curate-20260918"                      "$CLAIMED"
check "THE 2026-09-17 STALL: finished (done)" wo idle   "wo-cigar-curate-20260917"                      "$DONE"
check "failed order (7d post-mortem window)"  wo idle   "wo-cigar-curate-20260917"                      "$FAILED"
check "escalated order"                       wo idle   "wo-cigar-curate-20260917"                      "$ESCALATED"
check "pending order with a window"           wo active "wo-cigar-curate-20260919"                      "$PENDING"
check "finished window + running session"     wo active "wo-cigar-curate-20260917
wo-cigar-curate-20260918"                                                                               "$BOTH"

echo "== fail-safe: unverifiable never means idle =="
check "window with no CM entry (retention)"   wo active "wo-cigar-curate-20260917"                      '{}'
check "CM unreadable this poll"               wo active "wo-cigar-curate-20260918"                      '{}'
check "unrecognised status"                   wo active "wo-cigar-curate-20260918"                      "{$(order wo-cigar-curate-20260918 sideways)}"

echo "== lanes do not leak into each other =="
check "esc window does not busy the wo lane"  wo idle   "esc-shepherd-99e57e39"                         "$ESC_CLAIMED"
check "esc window busies the esc lane"        esc active "esc-shepherd-99e57e39"                        "$ESC_CLAIMED"
check "wo prefix is not a substring match"    rem idle  "wo-cigar-curate-20260918"                      "$CLAIMED"

echo "== the skip log must name the window that actually blocked =="
WINDOWS="wo-cigar-curate-20260917
wo-cigar-curate-20260918"
lane_active wo "$BOTH"
case "$LANE_HOLDER" in
  "wo-cigar-curate-20260918 (claimed)")
    printf 'ok   %-58s %s\n' "LANE_HOLDER is the running session, not the corpse" "$LANE_HOLDER" ;;
  *)
    printf 'FAIL %-58s got=%s\n' "LANE_HOLDER is the running session, not the corpse" "$LANE_HOLDER"
    fails=$((fails + 1)) ;;
esac

echo
if [ "$fails" -eq 0 ]; then
  echo "PASS — all lane-gate cases hold."
else
  echo "FAIL — $fails case(s) failed."
fi
exit "$fails"
