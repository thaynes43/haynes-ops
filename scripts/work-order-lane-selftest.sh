#!/usr/bin/env bash
# Self-test for the dev-env-ops work-order lane gate (single-flight) and for the
# wo-* hung-session watchdog that frees a lane nothing else can.
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
# The same file also carries `wo_stale()`/`wo_watchdog()` (#2976): a wo-* session
# that HANGS never exits, so neither the orphan sweep (window gone) nor
# session-launch.sh's lane release (process returned) ever sees it, and its
# `claimed` order pins the lane until someone kills the window by hand. The
# watchdog fails such an order out after OPS_WO_STALE_MINUTES. Its two ways to be
# wrong are both silent and both bad — fire on a healthy long run (a spurious
# page and a second session on the lane) or never fire (the stall it exists to
# end) — so every boundary it draws is pinned below: the wo-only scope, the
# deadline, and the fail-safes that must all resolve to "do nothing".
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
for fn in lane_active win_exists wo_stale wo_watchdog; do
  body="$(extract "$fn")"
  case "$body" in
    *"$fn()"*) ;;
    *) echo "FATAL: could not extract $fn() from $SRC (was it renamed?)"; exit 1 ;;
  esac
  eval "$body" || { echo "FATAL: extracted $fn() does not parse"; exit 1; }
done

# The shipped deadline is part of the contract, so read it from the script
# rather than restating it — a bump in the HelmRelease/script must show up here.
eval "$(grep -E '^WO_STALE_MIN=' "$SRC")"
[ -n "${WO_STALE_MIN:-}" ] || { echo "FATAL: WO_STALE_MIN not found in $SRC"; exit 1; }

# ── stubs ──
WINDOWS=""                      # what tmux reports, one window name per line
tmux() { printf '%s\n' "$WINDOWS" | sed '/^$/d'; }

order() { printf '"%s":"{\\"status\\":\\"%s\\"}"' "$1" "$2"; }   # CM .data entry
# …with an `updated` stamp: quoted (how every writer in-tree stamps it) and raw
# (a JSON number — accepted too, since `tonumber?` takes both).
order_at()     { printf '"%s":"{\\"status\\":\\"%s\\",\\"updated\\":\\"%s\\"}"' "$1" "$2" "$3"; }
order_at_num() { printf '"%s":"{\\"status\\":\\"%s\\",\\"updated\\":%s}"' "$1" "$2" "$3"; }

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


# ─────────────────────── wo-* hung-session watchdog (#2976) ───────────────────
# Fixed clock, and offsets expressed against the SHIPPED deadline so the cases
# stay meaningful if it is ever retuned.
NOW=1758300000
STALE=$((  NOW - (WO_STALE_MIN + 1) * 60 ))     # one minute past the deadline
ANCIENT=$(( NOW - (WO_STALE_MIN * 4) * 60 ))
EDGE=$((   NOW - (WO_STALE_MIN - 1) * 60 ))     # one minute inside it
FRESH=$((  NOW - 120 ))                         # a heartbeat two minutes ago

sel() {  # $1=name $2=expected keys (space-separated, "" = none) $3=cm-data-json
  local name="$1" want="$2" got
  got="$(wo_stale "$3" "$NOW" | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  if [ "$got" = "$want" ]; then
    printf 'ok   %-58s %s\n' "$name" "${got:-<none>}"
  else
    printf 'FAIL %-58s want=%s got=%s\n' "$name" "${want:-<none>}" "${got:-<none>}"
    fails=$((fails + 1))
  fi
}

echo
echo "== hung-session selector: claimed wo-* past the ${WO_STALE_MIN}min deadline =="
sel "stale claimed wo"            "wo-cigar-curate-20260919" "{$(order_at wo-cigar-curate-20260919 claimed "$STALE")}"
sel "stale claimed wo (numeric updated)" "wo-1234"           "{$(order_at_num wo-1234 claimed "$STALE")}"
sel "fresh claimed wo"            ""                         "{$(order_at wo-cigar-curate-20260919 claimed "$FRESH")}"
sel 'heartbeat ("working") refreshed mid-run' ""             "{$(order_at wo-1234 claimed "$FRESH")}"
sel "one minute inside the deadline" ""                      "{$(order_at wo-1234 claimed "$EDGE")}"
sel "pending, however old"        ""                         "{$(order_at wo-1234 pending "$ANCIENT")}"
sel "done, however old"           ""                         "{$(order_at wo-1234 done "$ANCIENT")}"
sel "failed, however old"         ""                         "{$(order_at wo-1234 failed "$ANCIENT")}"
sel "escalated, however old"      ""                         "{$(order_at wo-1234 escalated "$ANCIENT")}"
sel "only the stale one of several" "wo-1234" \
  "{$(order_at wo-1234 claimed "$STALE"),$(order_at wo-5678 claimed "$FRESH"),$(order_at wo-9 done "$ANCIENT")}"

echo "== scope: esc-* is exempt, rem-* belongs to respond.sh's rem_watchdog =="
sel "esc claimed long past the deadline" ""                  "{$(order_at esc-shepherd-99e57e39 claimed "$ANCIENT")}"
sel "rem claimed long past the deadline" ""                  "{$(order_at rem-responder-1a2b3c4d claimed "$ANCIENT")}"
sel "digest bookkeeping key"      ""                         '{"digest.last":"{\"last_flush\":\"1\"}"}'

echo "== fail-safe: anything unverifiable means DO NOTHING =="
sel "CM unreadable this poll"     ""                         '{}'
sel "empty snapshot argument"     ""                         ""
sel "no updated field at all"     ""                         "{$(order wo-1234 claimed)}"
sel "updated is not a number"     ""                         "{$(order_at wo-1234 claimed "not-a-time")}"
sel "updated is an ISO string"    ""                         "{$(order_at wo-1234 claimed "2026-09-19T10:00:00Z")}"
sel "entry value is not JSON"     ""                         '{"wo-1234":"not json at all"}'

# An unreadable clock must be inert too (the watcher's `now` comes from date(1)).
got0="$(wo_stale "{$(order_at wo-1234 claimed "$STALE")}" 0 | tr -d '[:space:]')"
if [ -z "$got0" ]; then
  printf 'ok   %-58s %s\n' "now=0 selects nothing" "<none>"
else
  printf 'FAIL %-58s got=%s\n' "now=0 selects nothing" "$got0"; fails=$((fails + 1))
fi

# ── the action: what it does, once, and to whom ──
CALLS=""
ORDER_STATUS_RC=0
log()   { :; }                                   # the watcher's stdout, muted here
oplog() { :; }
bash() {  # intercept ONLY the shipped order-status.sh hand-off
  case "${1:-}" in
    */order-status.sh) CALLS="${CALLS}$2 $3"$'\n'; return "$ORDER_STATUS_RC" ;;
    *) command bash "$@" ;;
  esac
}
act() {  # $1=name $2=expected calls ("key status" per line, "" = none) $3=windows $4=cm
  local name="$1" want="$2" got
  CALLS=""; WINDOWS="$3"
  wo_watchdog "$4" "$NOW"
  got="$(printf '%s' "$CALLS" | sed '/^$/d' | tr '\n' ';' | sed 's/;$//')"
  if [ "$got" = "$want" ]; then
    printf 'ok   %-58s %s\n' "$name" "${got:-<no action>}"
  else
    printf 'FAIL %-58s want=%s got=%s\n' "$name" "${want:-<no action>}" "${got:-<no action>}"
    fails=$((fails + 1))
  fi
}

HUNG="{$(order_at wo-cigar-curate-20260919 claimed "$STALE")}"
echo "== the action: mark failed through order-status.sh, never kill the window =="
act "stale claimed wo -> failed"  "wo-cigar-curate-20260919 failed" "wo-cigar-curate-20260919" "$HUNG"
act "fresh claimed wo untouched"  ""  "wo-1234" "{$(order_at wo-1234 claimed "$FRESH")}"
act "stale esc untouched"         ""  "esc-shepherd-99e57e39" "{$(order_at esc-shepherd-99e57e39 claimed "$ANCIENT")}"
act "window already gone: the orphan sweep's case, not ours" "" "" "$HUNG"

echo "== exactly once: a terminal order is never re-processed =="
CALLS=""; WINDOWS="wo-cigar-curate-20260919"
wo_watchdog "$HUNG" "$NOW"
first="$(printf '%s' "$CALLS" | sed '/^$/d' | wc -l)"
# order-status.sh writes status=failed + updated=now, exactly as the real one does.
AFTER="$(printf '%s' "$HUNG" | jq -c --arg k wo-cigar-curate-20260919 --arg now "$NOW" \
  '.[$k] = ((.[$k]|fromjson) | .status="failed" | .updated=$now | tojson)')"
CALLS=""
wo_watchdog "$AFTER" "$NOW"
second="$(printf '%s' "$CALLS" | sed '/^$/d' | wc -l)"
if [ "$first" -eq 1 ] && [ "$second" -eq 0 ]; then
  printf 'ok   %-58s %s\n' "one call on the first poll, none after" "1 then 0"
else
  printf 'FAIL %-58s got=%s then %s\n' "one call on the first poll, none after" "$first" "$second"
  fails=$((fails + 1))
fi

echo "== a close-out that did not land retries instead of giving up =="
ORDER_STATUS_RC=1
act "order-status.sh failed -> still claimed, fires again" \
  "wo-cigar-curate-20260919 failed" "wo-cigar-curate-20260919" "$HUNG"
ORDER_STATUS_RC=0

echo
if [ "$fails" -eq 0 ]; then
  echo "PASS — all lane-gate and hung-session cases hold."
else
  echo "FAIL — $fails case(s) failed."
fi
exit "$fails"
