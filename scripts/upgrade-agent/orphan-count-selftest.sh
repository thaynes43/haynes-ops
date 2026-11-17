#!/usr/bin/env bash
# Self-test for the weekly orphan page's count (story 554): run-shepherd.sh orphan_count, and the
# gate's page of it.
#
# WHY THIS EXISTS
# ---------------
# The weekly page said "63 Renovate PR(s) open >7d with no automation owner" (comment 6154 on
# story 508) while the night lane classified every one of them: a PR the lane put in any class
# (blocked, excluded and the owner's lane included) has a lane. The count is made in
# run-shepherd.sh (write_orphan_report); since story 554 it counts only the aging Renovate PRs
# missing from the lane's last record (upgrade-lane-state data.records), and only while the lane
# is acting. Every open PR classified reads 0, and the gate does not page 0. Pinned below:
#   - the record of 60 and more PRs (over Linux's 128 KiB argument limit: the review's H1, where
#     jq never ran and the count read 0) counts right, classified and not;
#   - a paused lane, a lane whose last tick is older than ORPHAN_LANE_MAX_AGE_HOURS, a record
#     that cannot be read: every aging PR counts, and the basis says why (the review's M3);
#   - outside text (titles) loses its control characters.
# The function is EXTRACTED from the shipped script, never copied.
#
# Usage: scripts/upgrade-agent/orphan-count-selftest.sh
# Requires: bash, jq. No cluster access, no network.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SHEP="$REPO_ROOT/kubernetes/main/apps/upgrade-agent/shepherd/app/resources/run-shepherd.sh"
GATE="$REPO_ROOT/kubernetes/main/apps/upgrade-agent/health-gate/app/resources/gate.sh"
LANE_FX="$REPO_ROOT/scripts/upgrade-agent/fixtures/morning-20261113.json"
command -v jq >/dev/null 2>&1 || { echo "FATAL: jq is required"; exit 1; }
body="$(awk '/^orphan_count\(\)/ { inside = 1 } inside { print } inside && /^}/ { exit }' "$SHEP")"
case "$body" in *"orphan_count()"*) ;; *) echo "FATAL: could not extract orphan_count() from $SHEP"; exit 1 ;; esac
eval "$body" || { echo "FATAL: orphan_count() does not parse"; exit 1; }
eval "$(grep '^ORPHAN_LANE_MAX_AGE_HOURS=' "$SHEP")"
grep -q 'out="$(orphan_count "$prs" "$lane" "$cutoff" "$(date -u +%s)")"' "$SHEP" || { echo "FATAL: write_orphan_report no longer calls orphan_count with the lane's ConfigMap"; exit 1; }
grep -q 'lane="$(kubectl -n "$SPEND_NS" get configmap upgrade-lane-state -o json 2>/dev/null)"' "$SHEP" || { echo "FATAL: write_orphan_report no longer reads upgrade-lane-state"; exit 1; }
grep -q -- '--from-literal=count="${count:-0}"' "$SHEP" || { echo "FATAL: the report's count key moved"; exit 1; }

passes=0; fails=0
ok()  { passes=$((passes+1)); echo "ok    $1"; }
bad() { fails=$((fails+1)); echo "FAIL  $1${2:+: $2}"; }
CUT="2026-11-06T10:00:00Z"
NOW="$(TZ=UTC date -d 2026-11-13T19:00:00Z +%s)"
UPDATED="2026-11-13T10:45:05.419Z"   # the last tick of the night, 8 hours before NOW
count() { printf '%s' "$1" | cut -f1; }
basis() { printf '%s' "$1" | cut -f2; }
summ()  { printf '%s' "$1" | cut -f3-; }
# lane <records-json> [updated] [pause] — upgrade-lane-state as kubectl returns it
# (the records ride stdin here too: the test must not hit the argument limit it tests)
lane() { printf '%s' "$1" | jq -Rsc --arg u "${2:-$UPDATED}" --arg p "${3:-}" '{apiVersion:"v1", kind:"ConfigMap", metadata:{name:"upgrade-lane-state"}, data:({records:., updated:$u, mode:"dryrun"} + (if $p != "" then {pause:$p} else {} end))}'; }

# The open PRs of 2026-11-13 as the lane held them (17 records), as gh pr list returns them: all
# Renovate's and all older than the cutoff, plus one young PR and one not by Renovate.
PRS="$(jq -c '[.records[] | {number: .pr, title: .title, author: {login: "app/renovate"}, createdAt: "2026-10-01T00:00:00Z"}]
  + [{number: 4999, title: "young", author: {login: "app/renovate"}, createdAt: "2026-11-12T00:00:00Z"},
     {number: 4998, title: "a session PR", author: {login: "app/haynes-dev-bot"}, createdAt: "2026-10-01T00:00:00Z"}]' "$LANE_FX")"
RECS="$(jq -c '[.records[] | {pr, lane, class}]' "$LANE_FX")"

echo "== the night of 2026-11-13"
r="$(orphan_count "$PRS" "$(lane "$RECS")" "$CUT" "$NOW")"
[ "$(count "$r")" = 0 ] && ok "every open PR classified (17 of 2026-11-13, blocked, excluded and owner lanes included): 0" || bad "all classified" "$r"
basis "$r" | grep -q 'PRs with no lane: missing from the night lane.s last record (17 classified)' && ok "the basis says how it was counted" || bad "basis" "$(basis "$r")"
RECS2="$(printf '%s' "$RECS" | jq -c '[.[] | select(.pr != 4757 and .pr != 4376)]')"
r="$(orphan_count "$PRS" "$(lane "$RECS2")" "$CUT" "$NOW")"
[ "$(count "$r")" = 2 ] && summ "$r" | grep -q '^#4376 .* | #4757 ' && ok "two PRs missing from the record: 2, named in the summary" || bad "two unclassified" "$r"
RECS3="$(printf '%s' "$RECS" | jq -c 'map(if .pr == 4757 then .lane = "" else . end)')"
[ "$(count "$(orphan_count "$PRS" "$(lane "$RECS3")" "$CUT" "$NOW")")" = 1 ] && ok "a record with no lane does not count as classified" || bad "empty lane"
[ "$(count "$(orphan_count "$PRS" "$(lane "$RECS")" "2026-09-01T00:00:00Z" "$NOW")")" = 0 ] && ok "nothing older than the cutoff: 0" || bad "cutoff"

echo "== many PRs: the record is over 128 KiB (H1)"
for n in 60 63 80; do
  P="$(jq -nc --argjson n "$n" '[range(0; $n) | {number: (1000 + .), title: "feat(container): update image x to v\(.)", author: {login: "app/renovate"}, createdAt: "2026-10-01T00:00:00Z"}]')"
  R="$(jq -nc --argjson n "$n" '[range(0; $n) | {pr: (1000 + .), lane: "BLOCKED", class: "check-failing-after-retry", pad: ("x" * 2400)}]')"
  L="$(lane "$R")"
  r="$(orphan_count "$P" "$L" "$CUT" "$NOW")"
  [ "$(count "$r")" = 0 ] && basis "$r" | grep -q "($n classified)" && ok "$n PRs, all classified, a ${#L}-byte record: 0" || bad "$n classified" "$(printf '%s' "$r" | cut -c1-120)"
  R2="$(jq -nc --argjson n "$n" '[range(0; $n) | {pr: (5000 + .), lane: "BLOCKED", pad: ("x" * 2400)}]')"
  r="$(orphan_count "$P" "$(lane "$R2")" "$CUT" "$NOW")"
  [ "$(count "$r")" = "$n" ] && ok "$n PRs, none on the record: $n" || bad "$n unclassified" "$(printf '%s' "$r" | cut -c1-120)"
done

echo "== the lane is not acting: every aging PR counts (M3)"
r="$(orphan_count "$PRS" "$(lane "$RECS" "$UPDATED" "tom: holding for the ceph upgrade")" "$CUT" "$NOW")"
[ "$(count "$r")" = 17 ] && basis "$r" | grep -q 'paused (data.pause)' && ok "paused: 17, and the basis says the lane is paused" || bad "paused" "$r"
OLD="2026-11-11T10:45:05.419Z"   # 56 hours before NOW: two nights with no tick
r="$(orphan_count "$PRS" "$(lane "$RECS" "$OLD")" "$CUT" "$NOW")"
[ "$(count "$r")" = 17 ] && basis "$r" | grep -q "last tick is 56h old (over ${ORPHAN_LANE_MAX_AGE_HOURS}h" && ok "the last tick 56 h old (suspended): 17, and the basis says how old" || bad "stale" "$r"
EDGE="$(TZ=UTC date -d "@$(( NOW - ORPHAN_LANE_MAX_AGE_HOURS * 3600 ))" +%FT%T.000Z)"
[ "$(count "$(orphan_count "$PRS" "$(lane "$RECS" "$EDGE")" "$CUT" "$NOW")")" = 0 ] && ok "exactly ${ORPHAN_LANE_MAX_AGE_HOURS} h old: still the lane's" || bad "edge"
r="$(orphan_count "$PRS" "$(lane "$RECS" "yesterday")" "$CUT" "$NOW")"
[ "$(count "$r")" = 17 ] && basis "$r" | grep -q 'no readable time (data.updated)' && ok "an unreadable time: every aging PR counts, and the basis says the time could not be read (not that the lane is old)" || bad "bad time" "$r"
[ "$ORPHAN_LANE_MAX_AGE_HOURS" = 36 ] && ok "the bound is 36 hours (the lane ticks every night)" || bad "bound" "$ORPHAN_LANE_MAX_AGE_HOURS"

echo "== the record cannot be read"
r="$(orphan_count "$PRS" "" "$CUT" "$NOW")"
[ "$(count "$r")" = 17 ] && basis "$r" | grep -q 'could not be read' && ok "the ConfigMap unreadable: every aging Renovate PR counts (17), and the basis says why" || bad "unreadable" "$r"
[ "$(count "$(orphan_count "$PRS" "$(lane '{"not":"a list"}')" "$CUT" "$NOW")")" = 17 ] && ok "records that are not a list: every aging PR counts" || bad "not a list"
r="$(orphan_count "$PRS" "$(lane 'not json')" "$CUT" "$NOW")"
[ "$(count "$r")" = 17 ] && basis "$r" | grep -q "record (upgrade-lane-state data.records) could not be read" && ok "records that are not JSON: every aging PR counts, and the basis says the record could not be read" || bad "not json" "$r"
[ "$(count "$(orphan_count "$PRS" '{"data":{}}' "$CUT" "$NOW")")" = 17 ] && ok "no records key: every aging PR counts" || bad "no key"

echo "== outside text"
EVIL="$(jq -nc '[{number: 7001, title: "evil\u0007\u001b[31m$(touch /tmp/x)\nnext", author: {login: "app/renovate"}, createdAt: "2026-10-01T00:00:00Z"}]')"
s="$(summ "$(orphan_count "$EVIL" "$(lane '[]')" "$CUT" "$NOW")")"
printf '%s' "$s" | grep -q $'[\x01-\x1f]' && bad "control characters in the summary" "$s" || ok "a title's control characters and newline do not reach the summary"

echo "== the gate"
grep -q 'Renovate PR(s) open >7d with no lane' "$GATE" && grep -q 'if \[ "$o_count" -gt 0 \] 2>/dev/null' "$GATE" && ok "the gate pages only a count above 0, and names it 'no lane'" || bad "gate page"

echo
echo "$passes passed, $fails failed"
[ "$fails" -eq 0 ]
