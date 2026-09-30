#!/usr/bin/env bash
# The functions under test arrive via eval, so shellcheck cannot see them call the stubs
# (SC2329) or read REPO (SC2034); the '$…' strings are literal grep patterns (SC2016).
# shellcheck disable=SC2016,SC2034,SC2329
# Self-test for the shepherd launcher's post-run handling (run-shepherd.sh, MODE=auto):
# the auto-merge stall fallback, the terminal-verdict escalation, and the DEFER backstop.
#
# WHY THIS EXISTS
# ---------------
# 2026-09-30 (#3287, esc-shepherd-0c7a9806): Tom was paged at 08:01Z for a healthy cluster.
#   1. The 04:07Z run queued `gh pr merge --auto` on #3280 (rook v1.20.8 operator), whose
#      checks had been green since 02:21Z. GitHub never fired it: the PR sat CLEAN with
#      auto-merge set for 4h. automerge_fallback now finishes such a merge itself.
#   2. The 08:00Z run re-vetted the pair's second half (#3281, no vet marker by design)
#      and wrote `HOLD: … has to wait for #3280`. Every HOLD escalates, and the wording
#      changes from run to run, so the page would have re-fired every 4h under a fresh
#      signature. A wait the next run clears is now `DEFER:`. It does not escalate unless
#      the same wait is still there ~8h later (defer_track, keyed on the PR numbers).
# Every case below is one of those behaviours pinned. Silent either way: a regression
# either pages Tom for nothing or lets a real stall sit unseen.
#
# Rule, same as the other selftests here: the functions under test are EXTRACTED from
# the shipped script, never copy-pasted, and the shipped defaults are read from it too.
# Stubbed: log, epoch_now, sleep (a fake clock), gh, shepherd_escalate, and the
# ConfigMap read/write (defer_state_read / defer_state_write).
#
# Usage: scripts/upgrade-agent/shepherd-verdict-selftest.sh
# Requires: bash 4+, jq. No cluster or GitHub access.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN="${RUN:-$REPO_ROOT/kubernetes/main/apps/upgrade-agent/shepherd/app/resources/run-shepherd.sh}"
HR="${HR:-$REPO_ROOT/kubernetes/main/apps/upgrade-agent/shepherd/app/helmrelease.yaml}"
command -v jq >/dev/null 2>&1 || { echo "FATAL: jq is required"; exit 1; }

extract() {  # $1=file $2=function name — its definition through the first column-0 '}'
  awk -v fn="$2" '
    $0 ~ "^" fn "\\(\\) \\{" { inside = 1 }
    inside { print }
    inside && /^}$/ { exit }
  ' "$1"
}
for fn in automerge_candidates automerge_decide automerge_complete automerge_fallback \
          escalation_reason defer_keys defer_next_state defer_overdue defer_track; do
  body="$(extract "$RUN" "$fn")"
  case "$body" in *"$fn() {"*"}") ;; *) echo "FATAL: could not extract $fn() from $RUN"; exit 1 ;; esac
  eval "$body" || { echo "FATAL: extracted $fn() does not parse"; exit 1; }
done
defaults="$(grep -E '^(AUTOMERGE_GRACE_S|AUTOMERGE_WATCH_S|AUTOMERGE_POLL_S|SHEPHERD_BOT_LOGIN|REQUIRED_CHECKS_JSON|DEFER_CM|DEFER_ESCALATE_MIN)=' "$RUN")"
[ "$(printf '%s\n' "$defaults" | wc -l)" -eq 7 ] || { echo "FATAL: shipped defaults not found in $RUN"; exit 1; }
eval "$defaults"
REPO="thaynes43/haynes-ops"

fails=0
ok()   { printf 'ok    %s\n' "$1"; }
bad()  { printf 'FAIL  %s: %s\n' "$1" "$2"; fails=$((fails+1)); }
expect() {  # $1=name $2=got $3=want
  if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "got '$2', want '$3'"; fi
}

# ── Wiring: the shipped script must still CALL what is tested here. A revert to the old
#    inline escalation would pass every behavioural case below while the bug is back. ──
static() {  # $1=name $2=file $3=fixed string that must be present
  if grep -qF -- "$3" "$2"; then ok "$1"; else bad "$1" "'$3' missing from $(basename "$2")"; fi
}
static "auto block runs the stall fallback"       "$RUN" 'automerge_fallback "$RUN_START_ISO"'
static "auto block escalates via escalation_reason" "$RUN" 'esc_reason="$(escalation_reason "$SUMMARY" "$rc")"'
static "auto block tracks DEFER streaks"          "$RUN" 'defer_track "$RESULT_TEXT" "$esc_reason"'
static "operating rules define DEFER"             "$RUN" "'DEFER: #<N> waits for #<blocking PR>"
static "vet-marker rule exempts DEFER"            "$RUN" 'A DEFERRED PR gets NO marker'
static "HR prompt defers the rook pair's next half" "$HR" "otherwise end with 'DEFER: #<next> waits for #<previous>'"
static "HR prompt defers plex on Kometa"          "$HR" "end with 'DEFER: #<N> waits for Kometa idle'"
merge_line="$(grep -E 'gh pr merge "\$pr"' "$RUN")"
case "$merge_line" in
  *--admin*|*--auto*) bad "fallback merge is plain (no --admin, no --auto)" "$merge_line" ;;
  *'--squash --delete-branch --match-head-commit "$sha"'*) ok "fallback merge is plain squash pinned to the head SHA" ;;
  *) bad "fallback merge is plain squash pinned to the head SHA" "${merge_line:-no gh pr merge line}" ;;
esac
if grep -qF 'that order one per run' "$HR"; then bad "HR prompt dropped 'one per run' for the rook set" "still present"
else ok "HR prompt dropped 'one per run' for the rook set"; fi

# ── stubs ──
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
log() { :; }
FAKE_NOW=0
epoch_now() { printf '%s' "$FAKE_NOW"; }
sleep() { FAKE_NOW=$((FAKE_NOW + $1)); }
shepherd_escalate() { printf '%s\t%s\n' "${ESCALATE_SIG:-<text-sig>}" "$1" >> "$T/esc"; }
esc_sigs() { [ -s "$T/esc" ] && cut -f1 "$T/esc" | paste -sd' ' -; }

# gh: `pr list` → $LIST_JSON; `pr view N --json mergedAt` → merged state; `pr view N` →
# scenario_view N (per case, reads FAKE_NOW); `pr merge` → recorded (with the clock) and
# merged, or refused when MERGE_RC=1 (MERGE_RACE=1: refused because it already merged).
LIST_JSON='[]'; LIST_RC=0; MERGE_RC=0; MERGE_RACE=0
scenario_view() { :; }
gh() {
  case "$1 $2" in
    "pr list") printf '%s' "$LIST_JSON"; return "$LIST_RC" ;;
    "pr view")
      echo "$3" >> "$T/views"
      if [ -f "$T/merged.$3" ]; then printf '{"state":"MERGED","mergedAt":"2026-09-30T04:10:00Z"}'; return 0; fi
      case " $* " in *" --json mergedAt "*) printf '{"mergedAt":null}'; return 0 ;; esac
      scenario_view "$3" ;;
    "pr merge")
      printf '%s@%s\n' "$*" "$FAKE_NOW" >> "$T/merges"
      if [ "$MERGE_RC" -eq 0 ]; then : > "$T/merged.$3"; return 0; fi
      [ "$MERGE_RACE" -eq 1 ] && : > "$T/merged.$3"
      echo "GraphQL: Pull request is not mergeable (mergePullRequest)" >&2; return 1 ;;
  esac
}

# A PR as `gh pr view --json …` returns it. $1=mergeStateStatus $2=head sha
# $3=auto-merge on (1/0); then checks: "name=SUCCESS|SKIPPED|FAILURE|PENDING" for a
# CheckRun, "ctx:name=SUCCESS|PENDING" for a legacy StatusContext.
view_json() {
  local ms="$1" sha="$2" auto="$3"; shift 3
  printf '%s\n' "$@" | jq -R 'select(length > 0)
      | capture("^(?<ctx>ctx:)?(?<name>.*)=(?<res>[A-Z]+)$")
      | if .ctx != null then {__typename: "StatusContext", context: .name, state: .res}
        elif .res == "PENDING" then {__typename: "CheckRun", name: .name, status: "IN_PROGRESS", conclusion: ""}
        else {__typename: "CheckRun", name: .name, status: "COMPLETED", conclusion: .res} end' \
    | jq -sc --arg ms "$ms" --arg sha "$sha" --arg auto "$auto" '{state: "OPEN", isDraft: false,
        mergedAt: null, mergeStateStatus: $ms, headRefOid: $sha,
        autoMergeRequest: (if $auto == "1" then {mergeMethod: "SQUASH"} else null end),
        statusCheckRollup: .}'
}
GREEN=("Flux Local - Success=SUCCESS" "Diff Scope - Success=SUCCESS" "Flux Local - Test (main)=SUCCESS" "Flux Local - Filter=SKIPPED")
RUN_START="2026-09-30T04:00:07Z"
listing() {  # $1=pr $2=enabledAt $3=enabledBy login — one open PR with auto-merge set
  jq -nc --argjson n "$1" --arg at "$2" --arg by "$3" \
    '[{number: $n, autoMergeRequest: {enabledAt: $at, enabledBy: {login: $by}, mergeMethod: "SQUASH"}}]'
}
fresh() { rm -f "$T"/merges "$T"/views "$T"/esc "$T"/merged.*; FAKE_NOW=1000; MERGE_RC=0; MERGE_RACE=0; LIST_RC=0; }
merges() { [ -s "$T/merges" ] && wc -l < "$T/merges" | tr -d ' ' || echo 0; }

echo "── automerge fallback ──"
fresh; LIST_JSON="$(listing 3280 2026-09-30T04:07:27Z haynes-ops-bot)"
scenario_view() { view_json CLEAN aaa111 1 "${GREEN[@]}"; }
automerge_fallback "$RUN_START"
expect "THE #3280 CASE: CLEAN+green, GitHub never fires -> exactly one fallback merge" "$(merges)" 1
m="$(cat "$T/merges" 2>/dev/null)"
expect "  ... plain squash on the checked head, no --admin" "${m%@*}" "pr merge 3280 -R thaynes43/haynes-ops --squash --delete-branch --match-head-commit aaa111"
at="${m##*@}"; if [ "${at:-0}" -ge $((1000 + AUTOMERGE_GRACE_S)) ]; then ok "  ... only after the ${AUTOMERGE_GRACE_S}s grace"; else bad "  ... only after the ${AUTOMERGE_GRACE_S}s grace" "merged at +$(( ${at:-0} - 1000 ))s"; fi
expect "  ... and files no escalation" "$(esc_sigs)" ""

fresh; scenario_view() { if [ "$FAKE_NOW" -ge 1030 ]; then printf '{"state":"MERGED","mergedAt":"x"}'; else view_json CLEAN aaa111 1 "${GREEN[@]}"; fi; }
automerge_fallback "$RUN_START"
expect "GitHub merges inside the grace -> no fallback merge" "$(merges)" 0

fresh; scenario_view() { view_json BLOCKED aaa111 1 "Flux Local - Success=PENDING" "Diff Scope - Success=SUCCESS"; }
automerge_fallback "$RUN_START"
expect "checks still pending all watch -> left to GitHub" "$(merges)" 0
if [ "$FAKE_NOW" -ge $((1000 + AUTOMERGE_WATCH_S)) ] && [ "$FAKE_NOW" -lt $((1000 + AUTOMERGE_WATCH_S + 2 * AUTOMERGE_POLL_S)) ]; then
  ok "  ... and the watch stops at ${AUTOMERGE_WATCH_S}s"; else bad "  ... and the watch stops at ${AUTOMERGE_WATCH_S}s" "stopped at +$((FAKE_NOW - 1000))s"; fi

fresh; scenario_view() { if [ "$FAKE_NOW" -ge 1300 ]; then view_json CLEAN aaa111 1 "${GREEN[@]}"; else view_json BLOCKED aaa111 1 "Flux Local - Success=PENDING" "Diff Scope - Success=SUCCESS"; fi; }
automerge_fallback "$RUN_START"
expect "green only at +300s (grace would end past the watch) -> left to GitHub" "$(merges)" 0

fresh; scenario_view() { view_json CLEAN aaa111 0 "${GREEN[@]}"; }
automerge_fallback "$RUN_START"
expect "a human switched auto-merge off -> never merged" "$(merges)" 0

fresh; scenario_view() { view_json CLEAN aaa111 1 "Flux Local - Success=SUCCESS" "Flux Local - Test (main)=SUCCESS"; }
automerge_fallback "$RUN_START"
expect "required 'Diff Scope - Success' absent -> never merged" "$(merges)" 0

fresh; scenario_view() { view_json UNSTABLE aaa111 1 "${GREEN[@]}" "kubeconform=FAILURE"; }
automerge_fallback "$RUN_START"
expect "a red check (even non-required) -> never merged" "$(merges)" 0

fresh; scenario_view() { view_json CLEAN aaa111 1 "${GREEN[@]}" "ctx:legacy/ci=SUCCESS"; }
automerge_fallback "$RUN_START"
expect "a green legacy StatusContext counts as green" "$(merges)" 1

fresh; scenario_view() { if [ "$FAKE_NOW" -ge 1060 ]; then view_json CLEAN bbb222 1 "${GREEN[@]}"; else view_json CLEAN aaa111 1 "${GREEN[@]}"; fi; }
automerge_fallback "$RUN_START"
m="$(cat "$T/merges" 2>/dev/null)"; at="${m##*@}"
if [ "$(merges)" = 1 ] && [ "${m%@*}" = "pr merge 3280 -R thaynes43/haynes-ops --squash --delete-branch --match-head-commit bbb222" ] && [ "${at:-0}" -ge $((1060 + AUTOMERGE_GRACE_S)) ]; then
  ok "a new push restarts the grace and the merge pins the NEW head"
else bad "a new push restarts the grace and the merge pins the NEW head" "$(merges) merge(s): ${m:-none}"; fi

fresh; MERGE_RC=1; scenario_view() { view_json CLEAN aaa111 1 "${GREEN[@]}"; }
automerge_fallback "$RUN_START"
expect "fallback merge refused, PR still open -> escalates on PR+SHA" "$(esc_sigs)" "shepherd-automerge|pr=3280|sha=aaa111"

fresh; MERGE_RC=1; MERGE_RACE=1; scenario_view() { view_json CLEAN aaa111 1 "${GREEN[@]}"; }
automerge_fallback "$RUN_START"
expect "fallback merge refused because GitHub merged first -> no escalation" "$(esc_sigs)" ""

for who in "renovate" "haynes-ops-bot-evil" "thaynes43"; do
  fresh; LIST_JSON="$(listing 3280 2026-09-30T04:07:27Z "$who")"; scenario_view() { view_json CLEAN aaa111 1 "${GREEN[@]}"; }
  automerge_fallback "$RUN_START"
  expect "auto-merge enabled by '$who' -> not ours, never read" "$( [ -s "$T/views" ] && echo read || echo untouched)" untouched
done
for who in "haynes-ops-bot[bot]" "app/haynes-ops-bot"; do
  fresh; LIST_JSON="$(listing 3280 2026-09-30T04:07:27Z "$who")"; scenario_view() { view_json CLEAN aaa111 1 "${GREEN[@]}"; }
  automerge_fallback "$RUN_START"
  expect "enabledBy login spelled '$who' is still the shepherd bot" "$(merges)" 1
done
fresh; LIST_JSON="$(listing 3280 2026-09-30T03:59:59Z haynes-ops-bot)"; scenario_view() { view_json CLEAN aaa111 1 "${GREEN[@]}"; }
automerge_fallback "$RUN_START"
expect "auto-merge enabled before this run started -> not this run's, untouched" "$(merges)" 0
fresh; LIST_RC=1; LIST_JSON=""
automerge_fallback "$RUN_START"; rc=$?
expect "gh pr list fails -> returns 0 and reads nothing" "$rc/$( [ -s "$T/views" ] && echo read || echo untouched)" "0/untouched"

d() { printf '%s' "$1" | automerge_decide; }
expect "decide: merged"           "$(d '{"state":"MERGED","mergedAt":"x"}')" merged
expect "decide: closed"           "$(d '{"state":"CLOSED","mergedAt":null}')" "skip state=CLOSED"
expect "decide: draft"            "$(d '{"state":"OPEN","isDraft":true,"autoMergeRequest":{}}')" "skip draft"
expect "decide: no checks at all" "$(d '{"state":"OPEN","isDraft":false,"autoMergeRequest":{},"mergeStateStatus":"CLEAN","statusCheckRollup":[]}')" "wait no-checks"
expect "decide: green but BLOCKED" "$(view_json BLOCKED aaa111 1 "${GREEN[@]}" | automerge_decide)" "wait mergeState=BLOCKED"
expect "decide: UNKNOWN (lazy) is a wait, not a skip" "$(view_json UNKNOWN aaa111 1 "${GREEN[@]}" | automerge_decide)" "wait mergeState=UNKNOWN"

echo "── escalation_reason ──"
SUM_0800='HOLD: rook-ceph cluster chart PR #3281 has to wait, because the operator half of the same rook unit (#3280) hasn'"'"'t merged or been checked healthy yet.  - **Merged or auto-merged this run:** nothing.'
expect "THE 08:00Z SUMMARY (HOLD) still escalates" "$(escalation_reason "$SUM_0800" 0 | cut -c1-23)" "terminal verdict: HOLD:"
expect "DEFER alone does not escalate" "$(escalation_reason 'DEFER: #3281 waits for #3280 — operator half queued, not merged yet. Merged this run: nothing.' 0)" ""
expect "BREAK-GLASS escalates" "$(escalation_reason 'BREAK-GLASS: HelmRelease wedged' 0 | cut -c1-17)" "terminal verdict:"
expect "HOLD NEEDED escalates" "$(escalation_reason 'HOLD NEEDED: cilium 1.20.2 — rollback loop' 0 | cut -c1-17)" "terminal verdict:"
expect "DEFER plus a real HOLD escalates" "$(escalation_reason 'DEFER: #40 waits for #10 — drain rule.  HOLD: backup safety net compromised for cnpg' 0 | cut -c1-17)" "terminal verdict:"
expect "a DEFER run that died still escalates" "$(escalation_reason 'DEFER: #40 waits for #10' 124)" "auto run died rc=124 (DEFER: #40 waits for #10)"
expect "no summary, rc=1" "$(escalation_reason '' 1)" "auto run died rc=1 (no summary — see Job logs)"
expect "a clean run stays quiet" "$(escalation_reason 'Queued auto-merge on #3280 (rook operator v1.20.8). Nothing paged.' 0)" ""

echo "── defer_keys ──"
k() { printf '%s\n' "$1" | defer_keys | cut -f1 | paste -sd' ' -; }
expect "plain DEFER line"            "$(k 'DEFER: #3281 waits for #3280 — operator half not merged yet')" "3280+3281"
expect "markdown-bold DEFER line"    "$(k '**DEFER:** #3281 waits for #3280 (operator half)')" "3280+3281"
expect "reversed phrasing, same key" "$(k 'DEFER: waiting on #3280 before #3281 can go')" "3280+3281"
expect "only the first two PRs count" "$(k 'DEFER: #3281 waits for #3280 (same shape as #2549)')" "3280+3281"
expect "blocker is not a PR"         "$(k 'DEFER: #3300 waits for Kometa idle')" "3300"
expect "no PR at all"                "$(k 'DEFER: something vague')" "?"
expect "two waits, two keys"         "$(k "$(printf 'DEFER: #3281 waits for #3280\nDEFER: #3300 waits for Kometa idle\nMerged: nothing')")" "3280+3281 3300"
expect "no DEFER line, no key"       "$(k 'HOLD: backup safety net compromised for cnpg')" ""

echo "── defer_track (the backstop) ──"
READ_RC=0
defer_state_read() { [ "$READ_RC" -eq 0 ] || return 1; cat "$T/state" 2>/dev/null || printf '{}'; }
defer_state_write() { printf '%s' "$1" > "$T/state"; echo w >> "$T/writes"; }
st() { jq -r --arg k "$1" ".[\$k].$2 // \"none\"" "$T/state" 2>/dev/null || echo none; }
reset_defer() { rm -f "$T/state" "$T/writes" "$T/esc"; READ_RC=0; }
T0=2000000000; H=3600
PAIR='DEFER: #3281 waits for #3280 — operator half queued, not merged yet'

reset_defer
FAKE_NOW=$T0;                 defer_track "$PAIR" ""
expect "THE PAIR, run 1: no escalation, streak opened" "$(esc_sigs)|$(st 3280+3281 runs)|$(st 3280+3281 first_seen)" "|1|$T0"
FAKE_NOW=$((T0 + 4 * H));     defer_track "$PAIR" ""
expect "THE PAIR, run 2 (+4h): still quiet"            "$(esc_sigs)|$(st 3280+3281 runs)|$(st 3280+3281 first_seen)" "|2|$T0"
FAKE_NOW=$((T0 + 8 * H - 120)); defer_track "$PAIR" ""
expect "THE PAIR, run 3 (+8h less jitter): escalates once, stable signature" "$(esc_sigs)" "shepherd-defer|3280+3281"
expect "  ... and records it"                          "$(st 3280+3281 escalated)" "$((T0 + 8 * H - 120))"
FAKE_NOW=$((T0 + 12 * H));    defer_track "$PAIR" ""
expect "THE PAIR, run 4: no second escalation in the same streak" "$(esc_sigs)" "shepherd-defer|3280+3281"
FAKE_NOW=$((T0 + 16 * H));    defer_track 'Queued auto-merge on #3281 (rook cluster v1.20.8).' ""
expect "the wait clears -> streak dropped"             "$(cat "$T/state")" "{}"

reset_defer
FAKE_NOW=$T0;                 defer_track "$PAIR" ""
FAKE_NOW=$((T0 + DEFER_ESCALATE_MIN * 60 - 1)); defer_track "$PAIR" ""
expect "one second short of ${DEFER_ESCALATE_MIN}min -> quiet" "$(esc_sigs)" ""
FAKE_NOW=$((T0 + DEFER_ESCALATE_MIN * 60)); defer_track "$PAIR" ""
expect "at ${DEFER_ESCALATE_MIN}min -> escalates"        "$(esc_sigs)" "shepherd-defer|3280+3281"

reset_defer
i=0; for blocker in 10 20 30 35; do
  FAKE_NOW=$((T0 + i * 4 * H)); defer_track "DEFER: #40 waits for #$blocker — drain rule: one stateful unit per run" ""; i=$((i + 1))
done
expect "drain queue (blocker changes every run, 12h) never escalates" "$(esc_sigs)" ""

reset_defer
i=0; for _ in 1 2 3; do
  FAKE_NOW=$((T0 + i * 4 * H)); defer_track "DEFER: #3300 waits for Kometa idle — kometa-operations overran" ""; i=$((i + 1))
done
expect "Kometa starving plex for 8h escalates"          "$(esc_sigs)" "shepherd-defer|3300"

reset_defer
FAKE_NOW=$T0; defer_track "$PAIR" ""
FAKE_NOW=$((T0 + 8 * H)); defer_track "$PAIR" "terminal verdict: HOLD: backup safety net compromised for cnpg"
expect "overdue, but this run already escalated -> not filed twice" "$(esc_sigs)|$(st 3280+3281 escalated)" "|0"
FAKE_NOW=$((T0 + 12 * H)); defer_track "$PAIR" ""
expect "  ... the next run files it"                    "$(esc_sigs)" "shepherd-defer|3280+3281"

reset_defer
FAKE_NOW=$T0; defer_track "$PAIR" ""
READ_RC=1; : > "$T/writes"
FAKE_NOW=$((T0 + 8 * H)); defer_track "$PAIR" ""; rc=$?
expect "unreadable state -> rc 1, no write, no escalation (never reset blind)" "$rc|$(wc -l < "$T/writes" | tr -d ' ')|$(esc_sigs)" "1|0|"

reset_defer
FAKE_NOW=$T0; defer_track 'Queued auto-merge on #3280. Nothing paged.' ""
expect "a quiet run with no streaks writes nothing" "$( [ -f "$T/writes" ] && echo wrote || echo none)" none

echo
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; exit 0; fi
echo "$fails FAILED"; exit 1
