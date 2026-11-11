#!/usr/bin/env bash
# The functions under test arrive via eval, so shellcheck cannot see them call the stubs
# (SC2329) or read the variables set here (SC2034); '$…' strings are literal (SC2016).
# shellcheck disable=SC2016,SC2034,SC2329
# Self-test for the shepherd standing aside for the night lane (story 551,
# https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/551).
#
# WHY THIS EXISTS
# ---------------
# Only one actor may merge cluster changes at a time. The night lane holds ConfigMap
# upgrade-agent/upgrade-change-lock while it merges and verifies, and merges only from
# 01:30 to 04:45 New York. The shepherd merges with the same bot: its 04:00 run fell
# inside that window on 2026-10-31 (#4475, 08:01Z) and 2026-11-06 (#4677, 09:01Z). Each
# case below pins one part of the rule; removing any guard in run-shepherd.sh fails one:
#   1. merge_gate_decide (pure): the window, inclusive at both ends, in New York time
#      across the DST change; the lock free, absent, expired, held, held with a
#      millisecond expiry as the lane writes it, held with no readable expiry; a lock or
#      rules file that cannot be read; garbage in;
#   2. shepherd_may_merge: kubectl NotFound is "absent", any other failure is a
#      stand-aside; an unreadable rules file or clock is a stand-aside; it returns 0 only
#      on "merge";
#   3. the pre-LLM block: with the lock free and the window closed the allowlist and the
#      operating rules are byte-identical; otherwise exactly MERGE_TOOLS leave the
#      allowlist (auto and remediate) and a MERGE STAND-ASIDE rule is added; dryrun and
#      shepherd modes are untouched and read nothing;
#   4. the stall fallback: no `gh pr merge` while standing aside, and none when the
#      check itself is missing (fail closed);
#   5. wiring: the block runs before the first LLM call and the allowlist it edits is
#      the one passed to claude.
# The functions are EXTRACTED from the shipped script, never copied. Stubbed: log,
# epoch_now, kubectl, gh.
#
# Usage: scripts/upgrade-agent/shepherd-stand-aside-selftest.sh
# Requires: bash 4+, jq, node. No cluster or GitHub access.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN="${RUN:-$REPO_ROOT/kubernetes/main/apps/upgrade-agent/shepherd/app/resources/run-shepherd.sh}"
RULES_FILE="$REPO_ROOT/kubernetes/main/apps/upgrade-agent/shepherd/app/resources/upgrade-rules.json"
command -v jq >/dev/null 2>&1 || { echo "FATAL: jq is required"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "FATAL: node is required"; exit 1; }

extract() {  # $1=file $2=function name — its definition through the first column-0 '}'
  awk -v fn="$2" '
    $0 ~ "^" fn "\\(\\) \\{" { inside = 1 }
    inside { print }
    inside && /^}$/ { exit }
  ' "$1"
}
for fn in merge_gate_decide change_lock_read lane_local_time shepherd_may_merge drop_merge_tools automerge_complete; do
  body="$(extract "$RUN" "$fn")"
  case "$body" in *"$fn() {"*"}") ;; *) echo "FATAL: could not extract $fn() from $RUN"; exit 1 ;; esac
  eval "$body" || { echo "FATAL: extracted $fn() does not parse"; exit 1; }
done
defaults="$(grep -E '^(CHANGE_LOCK_NS|CHANGE_LOCK_CM|MERGE_STAND_ASIDE_WHY)=' "$RUN")"
[ "$(printf '%s\n' "$defaults" | wc -l)" -eq 3 ] || { echo "FATAL: shipped stand-aside defaults not found in $RUN"; exit 1; }
eval "$defaults"
REPO="thaynes43/haynes-ops"

fails=0
ok()   { printf 'ok    %s\n' "$1"; }
bad()  { printf 'FAIL  %s: %s\n' "$1" "$2"; fails=$((fails+1)); }
expect() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "got '$2', want '$3'"; fi; }
like()   { case "$2" in $3) ok "$1" ;; *) bad "$1" "got '$2', want $3" ;; esac; }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
log() { printf '%s\n' "$*" >> "$T/log"; }
FAKE_NOW=0
epoch_now() { printf '%s' "$FAKE_NOW"; }
at() { date -u -d "$1" +%s; }   # ISO -> epoch

# ── 1. merge_gate_decide (pure) ──
NIGHT="$(jq -c '.night' "$RULES_FILE")"
expect "the shipped window is 01:30-04:45 America/New_York" "$(printf '%s' "$NIGHT" | jq -r '"\(.merge_window_local.first)-\(.merge_window_local.last) \(.timezone)"')" "01:30-04:45 America/New_York"
decide() {  # $1=now epoch $2=local HH:MM (or "") $3=lock JSON (null | "unreadable" | object) [$4=rules JSON]
  jq -nc --argjson now "$1" --arg local "$2" --argjson lock "$3" --argjson rules "${4:-$NIGHT}" \
    '{now: $now, local: (if $local == "" then null else $local end), rules: $rules, lock: $lock}' | merge_gate_decide
}
NOW="$(at 2026-11-11T17:00:00Z)"
lock() {  # $1=holder $2=expires_at (or "-" for none) $3=phase
  jq -nc --arg h "$1" --arg e "$2" --arg p "${3:-taken}" \
    '{metadata: {name: "upgrade-change-lock", namespace: "upgrade-agent", resourceVersion: "41"}, data: ({holder: $h, phase: $p} + (if $e == "-" then {} else {expires_at: $e} end))}'
}
expect "no lock ConfigMap, window closed -> merge" "$(decide "$NOW" 12:00 null)" "merge"
expect "a free lock (empty holder) -> merge" "$(decide "$NOW" 12:00 "$(lock '' 2026-11-11T17:05:00Z released)")" "merge"
like "a held lock -> stand aside, naming holder, phase and expiry" "$(decide "$NOW" 12:00 "$(lock upgrade-night/pod/ab12 2026-11-11T17:05:00Z merging)")" "stand-aside the change lock is held by upgrade-night/pod/ab12 (merging) until 2026-11-11T17:05:00Z"
like "a held lock with the lane's millisecond expiry -> stand aside" "$(decide "$NOW" 12:00 "$(lock h 2026-11-11T17:05:00.123Z)")" "stand-aside the change lock is held by h *"
expect "a lock past its expiry -> merge (the lane takes it over too)" "$(decide "$NOW" 12:00 "$(lock h 2026-11-11T16:59:59.999Z)")" "merge"
expect "a lock expiring exactly now -> merge (expired, as lockStatus reads it)" "$(decide "$NOW" 12:00 "$(lock h 2026-11-11T17:00:00Z)")" "merge"
like "a holder with no expires_at -> stand aside" "$(decide "$NOW" 12:00 "$(lock h -)")" "stand-aside *no readable expires_at"
like "a holder with an unparseable expires_at -> stand aside" "$(decide "$NOW" 12:00 "$(lock h tomorrow)")" "stand-aside *no readable expires_at"
like "an unreadable lock -> stand aside" "$(decide "$NOW" 12:00 '"unreadable"')" "stand-aside unreadable: ConfigMap upgrade-agent/upgrade-change-lock"
like "a lock that is not an object -> stand aside" "$(decide "$NOW" 12:00 '[1]')" "stand-aside unreadable*"
expect "01:29 New York, lock free -> merge" "$(decide "$NOW" 01:29 null)" "merge"
like "01:30 New York -> stand aside (window start, inclusive)" "$(decide "$NOW" 01:30 null)" "stand-aside inside the night lane merge window (01:30-04:45 America/New_York, now 01:30)"
like "04:45 New York -> stand aside (window end, inclusive)" "$(decide "$NOW" 04:45 null)" "stand-aside inside the night lane merge window*"
expect "04:46 New York, lock free -> merge" "$(decide "$NOW" 04:46 null)" "merge"
like "inside the window even with a free lock -> stand aside" "$(decide "$NOW" 03:00 "$(lock '' -)")" "stand-aside inside*"
like "no local time (the clock could not be read) -> stand aside" "$(decide "$NOW" "" null)" "stand-aside unreadable: the time in America/New_York"
like "a malformed local time -> stand aside" "$(decide "$NOW" "4:00" null)" "stand-aside unreadable: the time*"
like "rules without a window -> stand aside" "$(decide "$NOW" 12:00 null '{"timezone":"America/New_York"}')" "stand-aside unreadable: the night lane merge window*"
like "no rules at all -> stand aside" "$(decide "$NOW" 12:00 null null)" "stand-aside unreadable: the night lane merge window*"
got="$(printf 'not json' | merge_gate_decide)"
if [ "$got" != "merge" ]; then ok "garbage input never yields merge"; else bad "garbage input never yields merge" "$got"; fi

# ── 2. shepherd_may_merge, with kubectl stubbed and the real clock conversion ──
KUBECTL_MODE=absent
kubectl() {
  printf '%s\n' "$*" >> "$T/kubectl"
  case "$KUBECTL_MODE" in
    absent)    echo 'Error from server (NotFound): configmaps "upgrade-change-lock" not found' >&2; return 1 ;;
    forbidden) echo 'Error from server (Forbidden): configmaps "upgrade-change-lock" is forbidden' >&2; return 1 ;;
    garbage)   printf 'not json'; return 0 ;;
    held)      lock upgrade-night/pod/cd34 "$(date -u -d "@$((FAKE_NOW + 600))" +%FT%T.000Z)" taken; return 0 ;;
    free)      lock '' - released; return 0 ;;
  esac
}
may() {  # $1=ISO time $2=kubectl mode -> "rc why"
  FAKE_NOW="$(at "$1")"; KUBECTL_MODE="$2"; : > "$T/log"
  shepherd_may_merge; local rc=$?
  printf '%s %s' "$rc" "$MERGE_STAND_ASIDE_WHY"
}
LANE_RULES="$RULES_FILE"
expect "12:00 New York, no lock -> may merge (rc 0)" "$(may 2026-11-11T17:00:00Z absent)" "0 "
expect "12:00 New York, free lock -> may merge" "$(may 2026-11-11T17:00:00Z free)" "0 "
like "12:00 New York, lock held -> stand aside (rc 1)" "$(may 2026-11-11T17:00:00Z held)" "1 the change lock is held by upgrade-night/pod/cd34 (taken) until *"
like "kubectl refused (Forbidden) -> stand aside" "$(may 2026-11-11T17:00:00Z forbidden)" "1 unreadable: ConfigMap upgrade-agent/upgrade-change-lock"
like "kubectl printed garbage -> stand aside" "$(may 2026-11-11T17:00:00Z garbage)" "1 unreadable: ConfigMap*"
like "THE 2026-11-06 CASE: the 04:00 run at 09:01:37Z (#4677) -> stand aside (EST)" "$(may 2026-11-06T09:01:37Z absent)" "1 inside the night lane merge window (01:30-04:45 America/New_York, now 04:01)"
like "THE 2026-10-31 CASE: the 04:00 run at 08:01:17Z (#4475) -> stand aside (EDT)" "$(may 2026-10-31T08:01:17Z absent)" "1 inside the night lane merge window (01:30-04:45 America/New_York, now 04:01)"
like "the 2026-11-10 09:00Z run (04:00 New York) -> stand aside" "$(may 2026-11-10T09:00:00Z absent)" "1 inside*now 04:00)"
expect "the 2026-11-10 05:01Z run (00:01 New York, lock free) -> may merge; the lane's settle gate covers it" "$(may 2026-11-10T05:01:56Z absent)" "0 "
expect "06:31Z is 01:31 EST, 05:31Z is 01:31 EDT: the window follows New York time" "$(may 2026-11-11T06:31:00Z absent | cut -c1) $(may 2026-10-30T05:31:00Z absent | cut -c1) $(may 2026-11-11T05:31:00Z absent | cut -c1)" "1 1 0"
FAKE_NOW="$(at 2026-11-11T17:00:00Z)"; KUBECTL_MODE=held; : > "$T/log"; shepherd_may_merge >/dev/null
like "a stand-aside logs one clear STAND-ASIDE line" "$(grep -c '^STAND-ASIDE: this run merges nothing: the change lock is held by upgrade-night/pod/cd34' "$T/log")" "1"
LANE_RULES="$T/no-such-rules.json"
like "an unreadable rules file -> stand aside" "$(may 2026-11-11T17:00:00Z absent)" "1 unreadable: the night lane merge window*"
printf '{"night": {"timezone": "Mars/Olympus", "merge_window_local": {"first": "01:30", "last": "04:45"}}}' > "$T/bad-tz.json"; LANE_RULES="$T/bad-tz.json"
like "a zone Node cannot resolve -> stand aside" "$(may 2026-11-11T17:00:00Z absent)" "1 unreadable: the time in Mars/Olympus"
LANE_RULES="$RULES_FILE"
saved_lt="$(declare -f lane_local_time)"; lane_local_time() { return 127; }
like "no node (the local time cannot be computed) -> stand aside" "$(may 2026-11-11T17:00:00Z absent)" "1 unreadable: the time*"
eval "$saved_lt"
saved_mgd="$(declare -f merge_gate_decide)"; merge_gate_decide() { printf 'merge-ish'; }
like "a decision that is not exactly 'merge' -> stand aside" "$(may 2026-11-11T17:00:00Z absent)" "1 unreadable: the stand-aside decision (merge-ish)"
eval "$saved_mgd"
: > "$T/kubectl"; may 2026-11-11T17:00:00Z absent >/dev/null
expect "the lock is read from upgrade-agent/upgrade-change-lock" "$(head -1 "$T/kubectl")" "-n upgrade-agent get configmap upgrade-change-lock -o json"

# ── 3. the pre-LLM block, extracted from the script with the allowlists it builds ──
tools="$(awk '/^READONLY_TOOLS=\(/ { on = 1 } on { print } /^WORKORDER_TOOLS=\(/ { exit }' "$RUN")"
case "$tools" in *"MERGE_TOOLS=("*"WORKORDER_TOOLS=("*) ;; *) echo "FATAL: tool arrays not found in $RUN"; exit 1 ;; esac
eval "$tools"
block="$(awk '/^# ── STAND ASIDE \(story 551\), BEFORE the LLM turn/ { on = 1 } on { print } on && /^esac$/ { exit }' "$RUN")"
case "$block" in *'MERGE_STOOD_ASIDE=""'*'shepherd_may_merge'*'drop_merge_tools'*'esac') ;; *) echo "FATAL: the stand-aside block was not found in $RUN"; exit 1 ;; esac
pre_llm() {  # $1=MODE $2=ISO time $3=kubectl mode; sets ALLOWED, SAFETY_PROMPT, MERGE_STOOD_ASIDE
  MODE="$1"; FAKE_NOW="$(at "$2")"; KUBECTL_MODE="$3"; : > "$T/kubectl"
  case "$MODE" in
    auto|remediate) ALLOWED=("${READONLY_TOOLS[@]}" "${WRITE_TOOLS[@]}" "${MERGE_TOOLS[@]}" "${SILENCE_TOOLS[@]}" "${WORKORDER_TOOLS[@]}") ;;
    shepherd) ALLOWED=("${READONLY_TOOLS[@]}" "${WRITE_TOOLS[@]}") ;;
    *) ALLOWED=("${READONLY_TOOLS[@]}") ;;
  esac
  SAFETY_PROMPT="SAFETY: the rules as built."
  BEFORE_ALLOWED="$(printf '%s\x1f' "${ALLOWED[@]}")"; BEFORE_PROMPT="$SAFETY_PROMPT"
  eval "$block"
}
expect "MERGE_TOOLS is exactly gh pr merge" "$(printf '%s|' "${MERGE_TOOLS[@]}")" "Bash(gh pr merge:*)|"
for m in auto remediate; do
  pre_llm "$m" 2026-11-11T17:00:00Z absent
  expect "$m, lock free, window closed: the allowlist is byte-identical" "$(printf '%s\x1f' "${ALLOWED[@]}")" "$BEFORE_ALLOWED"
  expect "$m, lock free, window closed: the operating rules are unchanged" "$SAFETY_PROMPT" "$BEFORE_PROMPT"
  expect "$m, lock free, window closed: not stood aside" "${MERGE_STOOD_ASIDE:-}" ""
  pre_llm "$m" 2026-11-11T17:00:00Z held
  want="$(printf '%s\x1f' "${READONLY_TOOLS[@]}" "${WRITE_TOOLS[@]}" "${SILENCE_TOOLS[@]}" "${WORKORDER_TOOLS[@]}")"
  expect "$m, lock held: exactly gh pr merge leaves the allowlist" "$(printf '%s\x1f' "${ALLOWED[@]}")" "$want"
  like "$m, lock held: the rules gain one MERGE STAND-ASIDE rule naming why" "${SAFETY_PROMPT#"$BEFORE_PROMPT"}" " MERGE STAND-ASIDE (this run only; story 551): the change lock is held by upgrade-night/pod/cd34*DEFER: #<N> waits for the night lane*NO vet marker*"
  expect "$m, lock held: stood aside" "$MERGE_STOOD_ASIDE" "1"
  pre_llm "$m" 2026-11-10T09:00:00Z absent
  case "$(printf '%s\x1f' "${ALLOWED[@]}")" in *"Bash(gh pr merge:*)"*) bad "$m, 04:00 New York: gh pr merge is off the allowlist" "still allowed" ;; *) ok "$m, 04:00 New York: gh pr merge is off the allowlist" ;; esac
  pre_llm "$m" 2026-11-11T17:00:00Z forbidden
  case "$(printf '%s\x1f' "${ALLOWED[@]}")" in *"Bash(gh pr merge:*)"*) bad "$m, lock unreadable: gh pr merge is off the allowlist" "still allowed" ;; *) ok "$m, lock unreadable: gh pr merge is off the allowlist" ;; esac
done
for m in dryrun shepherd; do
  pre_llm "$m" 2026-11-11T17:00:00Z held
  expect "$m mode (no merge tools): untouched and nothing read" "$(printf '%s\x1f' "${ALLOWED[@]}")|$SAFETY_PROMPT|${MERGE_STOOD_ASIDE:-}|$(wc -l < "$T/kubectl" | tr -d ' ')" "$BEFORE_ALLOWED|$BEFORE_PROMPT||0"
done

# ── 4. the stall fallback ──
gh() {
  case "$1 $2" in
    "pr merge") printf '%s\n' "$*" >> "$T/merges"; return 0 ;;
    "pr view") printf '{"mergedAt":null}'; return 0 ;;
  esac
}
shepherd_escalate() { printf '%s\n' "$1" >> "$T/esc"; }
merges() { [ -s "$T/merges" ] && wc -l < "$T/merges" | tr -d ' ' || echo 0; }
rm -f "$T/merges"; FAKE_NOW="$(at 2026-11-11T17:00:00Z)"; KUBECTL_MODE=absent
automerge_complete 3280 aaa111 130; rc=$?
expect "fallback, lock free, window closed: merges as before (rc 0, one call)" "$rc $(merges)" "0 1"
rm -f "$T/merges" "$T/esc"; KUBECTL_MODE=held; : > "$T/log"
automerge_complete 3280 aaa111 130; rc=$?
expect "fallback, lock held: no gh pr merge, rc 1, no escalation" "$rc $(merges) $([ -s "$T/esc" ] && echo esc || echo none)" "1 0 none"
like "  ... and it logs the stand-aside" "$(grep -c '^automerge-fallback: STAND-ASIDE: #3280' "$T/log")" "1"
rm -f "$T/merges"; FAKE_NOW="$(at 2026-11-10T09:05:00Z)"; KUBECTL_MODE=absent
automerge_complete 3280 aaa111 130 2>/dev/null
expect "fallback inside the window: no gh pr merge" "$(merges)" "0"
rm -f "$T/merges"; FAKE_NOW="$(at 2026-11-11T17:00:00Z)"
unset -f shepherd_may_merge
automerge_complete 3280 aaa111 130 2>/dev/null
expect "fallback with the check missing: no gh pr merge (fail closed)" "$(merges)" "0"

# ── 5. wiring in the shipped script ──
line() { grep -nF -- "$1" "$RUN" | head -1 | cut -d: -f1; }
b="$(line '# ── STAND ASIDE (story 551), BEFORE the LLM turn')"; c="$(line 'run_claude "$OUT_FILE"; rc=$?')"
if [ -n "$b" ] && [ -n "$c" ] && [ "$b" -lt "$c" ]; then ok "the stand-aside block runs before the first LLM call"; else bad "the stand-aside block runs before the first LLM call" "block line ${b:-none}, call line ${c:-none}"; fi
s="$(line 'SAFETY_PROMPT="${SAFETY_PROMPT} VET MARKER')"
if [ -n "$s" ] && [ "$s" -lt "$b" ]; then ok "the block comes after the operating rules are built"; else bad "the block comes after the operating rules are built" "vet marker ${s:-none}, block ${b:-none}"; fi
if grep -qF -- '--allowedTools "${ALLOWED[@]}"' "$RUN"; then ok "claude gets the ALLOWED the block edits"; else bad "claude gets the ALLOWED the block edits" "no --allowedTools \"\${ALLOWED[@]}\""; fi
ac="$(extract "$RUN" automerge_complete)"
g="$(printf '%s\n' "$ac" | grep -n 'if ! shepherd_may_merge; then' | head -1 | cut -d: -f1)"; mm="$(printf '%s\n' "$ac" | grep -n 'gh pr merge "\$pr"' | head -1 | cut -d: -f1)"
if [ -n "$g" ] && [ -n "$mm" ] && [ "$g" -lt "$mm" ]; then ok "automerge_complete asks shepherd_may_merge before its gh pr merge"; else bad "automerge_complete asks shepherd_may_merge before its gh pr merge" "check ${g:-none}, merge ${mm:-none}"; fi
expect "only one direct gh pr merge call in the launcher (the fallback's)" "$(grep -cE '^\s+if out="\$\(gh pr merge ' "$RUN")" "1"

echo
if [ "$fails" -eq 0 ]; then echo "shepherd stand-aside selftest: all passed"; else echo "shepherd stand-aside selftest: $fails FAILED"; exit 1; fi
