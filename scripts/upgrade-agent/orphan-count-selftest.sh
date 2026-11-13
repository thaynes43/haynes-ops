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
# missing from the lane's last record (upgrade-lane-state data.records). Every open PR classified
# reads 0, and the gate does not page 0. A record that cannot be read counts every aging PR, as
# before, and says so. The function is EXTRACTED from the shipped script, never copied.
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
grep -q 'out="$(orphan_count "$prs" "$recs" "$cutoff")"' "$SHEP" || { echo "FATAL: write_orphan_report no longer calls orphan_count"; exit 1; }
grep -q -- '--from-literal=count="${count:-0}"' "$SHEP" || { echo "FATAL: the report's count key moved"; exit 1; }

passes=0; fails=0
ok()  { passes=$((passes+1)); echo "ok    $1"; }
bad() { fails=$((fails+1)); echo "FAIL  $1${2:+: $2}"; }
CUT="2026-11-06T10:00:00Z"
# The open PRs of 2026-11-13 as the lane held them (17 records), as gh pr list returns them: all
# Renovate's and all older than the cutoff, plus one young PR and one not by Renovate.
PRS="$(jq -c '[.records[] | {number: .pr, title: .title, author: {login: "app/renovate"}, createdAt: "2026-10-01T00:00:00Z"}]
  + [{number: 4999, title: "young", author: {login: "app/renovate"}, createdAt: "2026-11-12T00:00:00Z"},
     {number: 4998, title: "a session PR", author: {login: "app/haynes-dev-bot"}, createdAt: "2026-10-01T00:00:00Z"}]' "$LANE_FX")"
RECS="$(jq -c '[.records[] | {pr, lane, class}]' "$LANE_FX")"
count() { printf '%s' "$1" | cut -f1; }
basis() { printf '%s' "$1" | cut -f2; }
summ()  { printf '%s' "$1" | cut -f3-; }

r="$(orphan_count "$PRS" "$RECS" "$CUT")"
[ "$(count "$r")" = 0 ] && ok "every open PR classified (17 of 2026-11-13, blocked, excluded and owner lanes included): 0" || bad "all classified" "$r"
basis "$r" | grep -q 'PRs with no lane: missing from the night lane.s last record (17 classified)' && ok "the basis says how it was counted" || bad "basis" "$(basis "$r")"

RECS2="$(printf '%s' "$RECS" | jq -c '[.[] | select(.pr != 4757 and .pr != 4376)]')"
r="$(orphan_count "$PRS" "$RECS2" "$CUT")"
[ "$(count "$r")" = 2 ] && summ "$r" | grep -q '^#4376 .* | #4757 ' && ok "two PRs missing from the record: 2, named in the summary" || bad "two unclassified" "$r"
RECS3="$(printf '%s' "$RECS" | jq -c 'map(if .pr == 4757 then .lane = "" else . end)')"
[ "$(count "$(orphan_count "$PRS" "$RECS3" "$CUT")")" = 1 ] && ok "a record with no lane does not count as classified" || bad "empty lane"

r="$(orphan_count "$PRS" "" "$CUT")"
[ "$(count "$r")" = 17 ] && basis "$r" | grep -q 'could not be read' && ok "the record unreadable: every aging Renovate PR counts (17), and the basis says why" || bad "unreadable" "$r"
[ "$(count "$(orphan_count "$PRS" '{"not":"a list"}' "$CUT")")" = 17 ] && ok "a record that is not a list counts as unreadable" || bad "not a list"
[ "$(count "$(orphan_count "$PRS" "$RECS" "2026-09-01T00:00:00Z")")" = 0 ] && [ "$(count "$(orphan_count "$PRS" "" "2026-09-01T00:00:00Z")")" = 0 ] && ok "nothing older than the cutoff: 0 either way" || bad "cutoff"

EVIL="$(jq -nc '[{number: 7001, title: "evil\u0007\u001b[31m$(touch /tmp/x)\nnext", author: {login: "app/renovate"}, createdAt: "2026-10-01T00:00:00Z"}]')"
s="$(summ "$(orphan_count "$EVIL" '[]' "$CUT")")"
printf '%s' "$s" | grep -q $'[\x01-\x1f]' && bad "control characters in the summary" "$s" || ok "a title's control characters and newline do not reach the summary"

grep -q 'Renovate PR(s) open >7d with no lane' "$GATE" && grep -q 'if \[ "$o_count" -gt 0 \] 2>/dev/null' "$GATE" && ok "the gate pages only a count above 0, and names it 'no lane'" || bad "gate page"

echo
echo "$passes passed, $fails failed"
[ "$fails" -eq 0 ]
