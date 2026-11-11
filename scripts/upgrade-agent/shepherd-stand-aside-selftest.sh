#!/usr/bin/env bash
# The functions under test arrive via eval, so shellcheck cannot see them call the stubs
# (SC2329) or read the variables set here (SC2034); '$…' strings are literal (SC2016).
# shellcheck disable=SC2016,SC2034,SC2329
# Self-test for the shepherd standing aside for the night lane (story 551,
# https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/551).
#
# WHY THIS EXISTS
# ---------------
# Only one actor may merge cluster changes at a time, on facts, not clocks (Tom's ruling
# Q-548-04): the shepherd reads no schedule. It stands aside while the night lane's
# ConfigMap upgrade-agent/upgrade-change-lock shows a lane change in progress (phase
# merging, merged-awaiting-verification or merge-unknown), and when it cannot read it. A
# tick's `taken` lock (about 25 seconds, even in dry-run) is bookkeeping, not a change.
# The lane's side of the handshake (F4: no merge while this Job runs) is tested in
# lane-merge-step-selftest.mjs. Removing any guard in run-shepherd.sh fails a case here:
#   1. merge_gate_decide (pure) on the shared lock states
#      (fixtures/change-lock-states.json, which the lane's lockStatus test reads too);
#   2. shepherd_may_merge: kubectl NotFound is "absent", any other failure is a
#      stand-aside; it returns 0 only on "merge" or "merge (<note>)";
#   3. the pre-LLM block: with no change in progress the allowlist and the operating rules
#      are byte-identical; otherwise exactly MERGE_TOOLS leave the allowlist (auto and
#      remediate) and a MERGE STAND-ASIDE rule is added; dryrun and shepherd modes are
#      untouched and read nothing;
#   4. the stall fallback: no `gh pr merge` while standing aside; the PR stays in the
#      watch, a transient lock-read failure merges on the next poll, and only a stand-aside
#      still holding at the deadline escalates, on a stable signature (review findings R4
#      and S3); no merge when the check itself is missing (fail closed);
#   5. wiring and the handshake order: the shepherd's Job exists before it reads the lock
#      (and triage claims a remediation before it starts run-shepherd.sh); no clock rule
#      is left (no window, no time-of-day).
# The functions are EXTRACTED from the shipped script, never copied. Stubbed: log,
# epoch_now, sleep (a fake clock), kubectl, gh, shepherd_escalate.
#
# Usage: scripts/upgrade-agent/shepherd-stand-aside-selftest.sh
# Requires: bash 4+, jq. No cluster or GitHub access.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN="${RUN:-$REPO_ROOT/kubernetes/main/apps/upgrade-agent/shepherd/app/resources/run-shepherd.sh}"
TRIAGE="$REPO_ROOT/kubernetes/main/apps/upgrade-agent/shepherd/app/resources/triage.sh"
LOCKS="$REPO_ROOT/scripts/upgrade-agent/fixtures/change-lock-states.json"
command -v jq >/dev/null 2>&1 || { echo "FATAL: jq is required"; exit 1; }

extract() {  # $1=file $2=function name — its definition through the first column-0 '}'
  awk -v fn="$2" '
    $0 ~ "^" fn "\\(\\) \\{" { inside = 1 }
    inside { print }
    inside && /^}$/ { exit }
  ' "$1"
}
for fn in merge_gate_decide change_lock_read shepherd_may_merge drop_merge_tools automerge_complete \
          automerge_strand_escalate automerge_candidates automerge_decide automerge_fallback; do
  body="$(extract "$RUN" "$fn")"
  case "$body" in *"$fn() {"*"}") ;; *) echo "FATAL: could not extract $fn() from $RUN"; exit 1 ;; esac
  eval "$body" || { echo "FATAL: extracted $fn() does not parse"; exit 1; }
done
defaults="$(grep -E '^(CHANGE_LOCK_NS|CHANGE_LOCK_CM|MERGE_STAND_ASIDE_WHY|AUTOMERGE_GRACE_S|AUTOMERGE_WATCH_S|AUTOMERGE_POLL_S|SHEPHERD_BOT_LOGIN|REQUIRED_CHECKS_JSON)=' "$RUN")"
[ "$(printf '%s\n' "$defaults" | wc -l)" -eq 8 ] || { echo "FATAL: shipped stand-aside and fallback defaults not found in $RUN"; exit 1; }
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

# ── 1. merge_gate_decide on the shared lock states (R7) ──
NOW="$(jq -r '.now' "$LOCKS")"; FAKE_NOW="$(at "$NOW")"
n="$(jq '.cases | length' "$LOCKS")"
[ "$n" -ge 20 ] || { echo "FATAL: the shared lock fixture has $n cases"; exit 1; }
for i in $(seq 0 $((n - 1))); do
  id="$(jq -r ".cases[$i].id" "$LOCKS")"; want="$(jq -r ".cases[$i].want_shepherd" "$LOCKS")"
  lockj="$(jq -c ".cases[$i] | if .read == \"absent\" then null elif .read == \"unreadable\" then \"unreadable\" else .configmap end" "$LOCKS")"
  got="$(jq -nc --argjson now "$FAKE_NOW" --argjson lock "$lockj" '{now: $now, lock: $lock}' | merge_gate_decide)"
  expect "lock state '$id' -> $want" "${got%% *}" "$want"
done
decide() { jq -nc --argjson now "$FAKE_NOW" --argjson lock "$1" '{now: $now, lock: $lock}' | merge_gate_decide; }
lock() {  # $1=holder $2=expires_at (or "-" for none) $3=phase (or "-" for none)
  jq -nc --arg h "$1" --arg e "$2" --arg p "${3:-taken}" \
    '{metadata: {name: "upgrade-change-lock", namespace: "upgrade-agent", resourceVersion: "41"}, data: ({holder: $h} + (if $e == "-" then {} else {expires_at: $e} end) + (if $p == "-" then {} else {phase: $p} end))}'
}
like "a change phase names holder, phase and expiry" "$(decide "$(lock upgrade-night/pod/ab12 2026-11-11T21:00:00.000Z merged-awaiting-verification)")" "stand-aside a lane change is in progress: the change lock is held by upgrade-night/pod/ab12 in phase merged-awaiting-verification until 2026-11-11T21:00:00.000Z"
like "a taken lock is read and noted, not a stand-aside" "$(decide "$(lock upgrade-night/pod/ab12 2026-11-11T17:10:00.000Z taken)")" "merge (read the change lock: held by upgrade-night/pod/ab12 in phase taken until 2026-11-11T17:10:00.000Z, a tick's bookkeeping, not a change in progress)"
like "an unknown phase names it" "$(decide "$(lock h 2026-11-11T17:10:00Z verifying)")" "stand-aside *unknown phase (verifying)*"
like "an offset timestamp is one this launcher cannot read" "$(decide "$(lock h 2026-11-11T12:10:00-05:00 taken)")" "stand-aside *no expires_at this launcher can read (2026-11-11T12:10:00-05:00)"
got="$(printf 'not json' | merge_gate_decide)"
if [ "${got%% *}" != "merge" ]; then ok "garbage input never yields merge"; else bad "garbage input never yields merge" "$got"; fi

# ── 2. shepherd_may_merge, with kubectl stubbed ──
KUBECTL_MODE=absent
# seq <modes…>: each lock read takes the next mode, the last one repeats. A file, because
# change_lock_read calls kubectl inside a command substitution (a subshell).
seq_modes() { printf '%s\n' "$@" > "$T/seq"; }
kubectl() {
  printf '%s\n' "$*" >> "$T/kubectl"
  local exp; exp="$(date -u -d "@$((FAKE_NOW + 600))" +%FT%T.000Z)"
  if [ -s "$T/seq" ]; then KUBECTL_MODE="$(head -1 "$T/seq")"; [ "$(wc -l < "$T/seq")" -gt 1 ] && sed -i 1d "$T/seq"; fi
  case "$KUBECTL_MODE" in
    absent)    echo 'Error from server (NotFound): configmaps "upgrade-change-lock" not found' >&2; return 1 ;;
    forbidden) echo 'Error from server (Forbidden): configmaps "upgrade-change-lock" is forbidden' >&2; return 1 ;;
    garbage)   printf 'not json'; return 0 ;;
    taken)     lock upgrade-night/pod/cd34 "$exp" taken; return 0 ;;
    merging)   lock upgrade-night/pod/cd34 "$exp" merging; return 0 ;;
    awaiting)  lock upgrade-night/pod/cd34 "$(date -u -d "@$((FAKE_NOW + 14400))" +%FT%T.000Z)" merged-awaiting-verification; return 0 ;;
    unknown)   lock upgrade-night/pod/cd34 "$exp" -; return 0 ;;
    free)      lock '' - released; return 0 ;;
  esac
}
may() {  # $1=kubectl mode -> "rc why"
  FAKE_NOW="$(at 2026-11-11T09:00:00Z)"; KUBECTL_MODE="$1"; : > "$T/log"
  shepherd_may_merge; local rc=$?
  printf '%s %s' "$rc" "$MERGE_STAND_ASIDE_WHY"
}
expect "no lock -> may merge (rc 0)" "$(may absent)" "0 "
expect "a free lock -> may merge" "$(may free)" "0 "
expect "THE 04:00 CASE: a tick's taken lock at 09:00Z -> may merge (no coincidence of schedules)" "$(may taken)" "0 "
like "a merging lock -> stand aside (rc 1)" "$(may merging)" "1 a lane change is in progress: the change lock is held by upgrade-night/pod/cd34 in phase merging until *"
like "the 4h post-merge hold -> stand aside" "$(may awaiting)" "1 a lane change is in progress: * in phase merged-awaiting-verification *"
like "a held lock with no phase -> stand aside" "$(may unknown)" "1 the change lock is held by upgrade-night/pod/cd34 in an unknown phase (null)*"
like "kubectl refused (Forbidden) -> stand aside" "$(may forbidden)" "1 unreadable: ConfigMap upgrade-agent/upgrade-change-lock"
like "kubectl printed garbage -> stand aside" "$(may garbage)" "1 unreadable: ConfigMap*"
may taken >/dev/null
expect "a taken lock is logged as read, not as a stand-aside" "$(grep -c "^stand-aside: not needed: (read the change lock: held by upgrade-night/pod/cd34 in phase taken" "$T/log")" "1"
may merging >/dev/null
expect "a stand-aside logs one clear STAND-ASIDE line" "$(grep -c '^STAND-ASIDE: this run merges nothing: a lane change is in progress: the change lock is held by upgrade-night/pod/cd34 in phase merging' "$T/log")" "1"
saved_mgd="$(declare -f merge_gate_decide)"
merge_gate_decide() { printf 'merge-ish'; }
like "a decision that is not exactly 'merge' or 'merge (...)' -> stand aside" "$(may absent)" "1 unreadable: the stand-aside decision (merge-ish)"
merge_gate_decide() { printf 'merge (but no closing parenthesis'; }
like "  ... nor a 'merge (' without its closing parenthesis" "$(may absent)" "1 unreadable: the stand-aside decision*"
eval "$saved_mgd"
: > "$T/kubectl"; may absent >/dev/null
expect "the lock is read from upgrade-agent/upgrade-change-lock" "$(head -1 "$T/kubectl")" "-n upgrade-agent get configmap upgrade-change-lock -o json"

# ── 3. the pre-LLM block, extracted from the script with the allowlists it builds ──
tools="$(awk '/^READONLY_TOOLS=\(/ { on = 1 } on { print } /^WORKORDER_TOOLS=\(/ { exit }' "$RUN")"
case "$tools" in *"MERGE_TOOLS=("*"WORKORDER_TOOLS=("*) ;; *) echo "FATAL: tool arrays not found in $RUN"; exit 1 ;; esac
eval "$tools"
block="$(awk '/^# ── STAND ASIDE \(story 551\), BEFORE the LLM turn/ { on = 1 } on { print } on && /^esac$/ { exit }' "$RUN")"
case "$block" in *'shepherd_may_merge'*'drop_merge_tools'*'esac') ;; *) echo "FATAL: the stand-aside block was not found in $RUN"; exit 1 ;; esac
pre_llm() {  # $1=MODE $2=kubectl mode; sets ALLOWED and SAFETY_PROMPT
  MODE="$1"; FAKE_NOW="$(at 2026-11-11T09:00:00Z)"; KUBECTL_MODE="$2"; : > "$T/kubectl"
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
  for free in absent free taken; do
    pre_llm "$m" "$free"
    expect "$m, lock $free: the allowlist and the operating rules are byte-identical" "$(printf '%s\x1f' "${ALLOWED[@]}")|$SAFETY_PROMPT" "$BEFORE_ALLOWED|$BEFORE_PROMPT"
  done
  want="$(printf '%s\x1f' "${READONLY_TOOLS[@]}" "${WRITE_TOOLS[@]}" "${SILENCE_TOOLS[@]}" "${WORKORDER_TOOLS[@]}")"
  for held in merging awaiting unknown forbidden; do
    pre_llm "$m" "$held"
    expect "$m, lock $held: exactly gh pr merge leaves the allowlist" "$(printf '%s\x1f' "${ALLOWED[@]}")" "$want"
  done
  pre_llm "$m" merging
  like "$m, lock merging: the rules gain one MERGE STAND-ASIDE rule naming why" "${SAFETY_PROMPT#"$BEFORE_PROMPT"}" " MERGE STAND-ASIDE (this run only; story 551): a lane change is in progress: the change lock is held by upgrade-night/pod/cd34 in phase merging*DEFER: #<N> waits for the night lane to finish its change*NO vet marker*"
done
for m in dryrun shepherd; do
  pre_llm "$m" merging
  expect "$m mode (no merge tools): untouched and nothing read" "$(printf '%s\x1f' "${ALLOWED[@]}")|$SAFETY_PROMPT|$(wc -l < "$T/kubectl" | tr -d ' ')" "$BEFORE_ALLOWED|$BEFORE_PROMPT|0"
done

# ── 4. the stall fallback ──
gh() {
  case "$1 $2" in
    "pr list") printf '%s' "$LIST_JSON"; return 0 ;;
    "pr merge") printf '%s\n' "$*" >> "$T/merges"; : > "$T/merged.$3"; return 0 ;;
    "pr view")
      if [ -f "$T/merged.$3" ]; then printf '{"state":"MERGED","mergedAt":"2026-11-11T09:10:00Z"}'; return 0; fi
      case " $* " in *" --json mergedAt "*) printf '{"mergedAt":null}'; return 0 ;; esac
      jq -nc '{state: "OPEN", isDraft: false, mergedAt: null, mergeStateStatus: "CLEAN", headRefOid: "aaa111", autoMergeRequest: {mergeMethod: "SQUASH"},
        statusCheckRollup: [{__typename: "CheckRun", name: "Flux Local - Success", status: "COMPLETED", conclusion: "SUCCESS"}, {__typename: "CheckRun", name: "Diff Scope - Success", status: "COMPLETED", conclusion: "SUCCESS"}]}'; return 0 ;;
  esac
}
sleep() { FAKE_NOW=$((FAKE_NOW + $1)); }
shepherd_escalate() { printf '%s\t%s\n' "${ESCALATE_SIG:-<text-sig>}" "$1" >> "$T/esc"; }
merges() { [ -s "$T/merges" ] && wc -l < "$T/merges" | tr -d ' ' || echo 0; }
fresh() { rm -f "$T"/merges "$T"/esc "$T"/merged.* "$T/seq"; : > "$T/log"; FAKE_NOW="$(at 2026-11-11T09:00:00Z)"; }
fresh; KUBECTL_MODE=taken
automerge_complete 3280 aaa111 130; rc=$?
expect "fallback, a tick's taken lock: merges as before (rc 0, one call, no escalation)" "$rc $(merges) $([ -s "$T/esc" ] && echo esc || echo none)" "0 1 none"
fresh; KUBECTL_MODE=merging
automerge_complete 3280 aaa111 130; rc=$?
expect "fallback, lock merging: no gh pr merge, rc 2 (the watch keeps the PR), no escalation yet (S3)" "$rc $(merges) $([ -s "$T/esc" ] && echo esc || echo none)" "2 0 none"
like "  ... and it logs the stand-aside" "$(grep -c '^automerge-fallback: STAND-ASIDE: #3280' "$T/log")" "1"
# the whole watch: one PR whose auto-merge this run enabled, CLEAN and green from the start
LIST_JSON="$(jq -nc '[{number: 3280, autoMergeRequest: {enabledAt: "2026-11-11T09:00:30Z", enabledBy: {login: "haynes-ops-bot"}, mergeMethod: "SQUASH"}}]')"
fresh; seq_modes forbidden absent
automerge_fallback "2026-11-11T09:00:00Z"
expect "S3 transient: a lock read that fails once, then reads free: the next poll merges, no escalation" "$(merges) $([ -s "$T/esc" ] && echo esc || echo none)" "1 none"
fresh; seq_modes merging
automerge_fallback "2026-11-11T09:00:00Z"
expect "S3 persistent: a stand-aside that holds through the watch: no merge, one escalation at the deadline" "$(merges) $(wc -l < "$T/esc" 2>/dev/null | tr -d ' ')" "0 1"
expect "  ... on the stable signature (R4)" "$(cut -f1 "$T/esc" 2>/dev/null)" "shepherd-automerge-stood-aside|pr=3280|sha=aaa111"
like "  ... naming why" "$(cut -f2 "$T/esc" 2>/dev/null)" "auto-merge stranded: #3280 *until its watch ended (a lane change is in progress*"
if [ "$FAKE_NOW" -ge $(( $(at 2026-11-11T09:00:00Z) + AUTOMERGE_WATCH_S )) ]; then ok "  ... only after the ${AUTOMERGE_WATCH_S}s watch"; else bad "  ... only after the ${AUTOMERGE_WATCH_S}s watch" "ended at +$((FAKE_NOW - $(at 2026-11-11T09:00:00Z)))s"; fi
fresh; seq_modes forbidden
automerge_fallback "2026-11-11T09:00:00Z"
expect "S3: a lock that stays unreadable through the watch escalates once" "$(merges) $(cut -f1 "$T/esc" 2>/dev/null)" "0 shepherd-automerge-stood-aside|pr=3280|sha=aaa111"
fresh; KUBECTL_MODE=absent
unset -f shepherd_may_merge
automerge_complete 3280 aaa111 130 2>/dev/null
expect "fallback with the check missing: no gh pr merge (fail closed)" "$(merges)" "0"

# ── 5. wiring, the handshake order, and no clock rule left ──
line() { grep -nF -- "$1" "$2" | head -1 | cut -d: -f1; }
b="$(line '# ── STAND ASIDE (story 551), BEFORE the LLM turn' "$RUN")"; c="$(line 'run_claude "$OUT_FILE"; rc=$?' "$RUN")"
if [ -n "$b" ] && [ -n "$c" ] && [ "$b" -lt "$c" ]; then ok "the lock is read before the first LLM call (so after this Job exists)"; else bad "the lock is read before the first LLM call (so after this Job exists)" "block line ${b:-none}, call line ${c:-none}"; fi
s="$(line 'SAFETY_PROMPT="${SAFETY_PROMPT} VET MARKER' "$RUN")"
if [ -n "$s" ] && [ "$s" -lt "$b" ]; then ok "the block comes after the operating rules are built"; else bad "the block comes after the operating rules are built" "vet marker ${s:-none}, block ${b:-none}"; fi
if grep -qF -- '--allowedTools "${ALLOWED[@]}"' "$RUN"; then ok "claude gets the ALLOWED the block edits"; else bad "claude gets the ALLOWED the block edits" "no --allowedTools \"\${ALLOWED[@]}\""; fi
ac="$(extract "$RUN" automerge_complete)"
g="$(printf '%s\n' "$ac" | grep -n 'if ! shepherd_may_merge; then' | head -1 | cut -d: -f1)"; mm="$(printf '%s\n' "$ac" | grep -n 'gh pr merge "\$pr"' | head -1 | cut -d: -f1)"
if [ -n "$g" ] && [ -n "$mm" ] && [ "$g" -lt "$mm" ]; then ok "automerge_complete asks shepherd_may_merge before its gh pr merge"; else bad "automerge_complete asks shepherd_may_merge before its gh pr merge" "check ${g:-none}, merge ${mm:-none}"; fi
expect "only one direct gh pr merge call in the launcher (the fallback's)" "$(grep -cE '^\s+if out="\$\(gh pr merge ' "$RUN")" "1"
cl="$(line 'state_upsert "$SIG" upgrade 1 none' "$TRIAGE")"; rs="$(line '/bin/bash /opt/shepherd/run-shepherd.sh' "$TRIAGE")"
if [ -n "$cl" ] && [ -n "$rs" ] && [ "$cl" -lt "$rs" ]; then ok "triage claims the remediation (the lane's F4 reads it) before it starts run-shepherd.sh"; else bad "triage claims the remediation before it starts run-shepherd.sh" "claim ${cl:-none}, run ${rs:-none}"; fi
if grep -qE 'merge_window_local|lane_local_time|Intl\.DateTimeFormat|LANE_RULES|upgrade-rules\.json' "$RUN"; then bad "no clock rule is left in the launcher (ruling Q-548-04)" "$(grep -nE 'merge_window_local|lane_local_time|Intl\.DateTimeFormat|LANE_RULES|upgrade-rules\.json' "$RUN" | head -3)"; else ok "no clock rule is left in the launcher (ruling Q-548-04)"; fi
if grep -q 'MERGE_STOOD_ASIDE' "$RUN"; then bad "MERGE_STOOD_ASIDE is gone (set and never read)" "still present"; else ok "MERGE_STOOD_ASIDE is gone (set and never read)"; fi
if grep -qF 'The LLM keeps' "$RUN" && grep -qF 'admits GitHub'"'"'s REST' "$RUN"; then ok "the comment says gh api repos/* still admits a REST merge (R2)"; else bad "the comment says gh api repos/* still admits a REST merge (R2)" "not found"; fi

echo
if [ "$fails" -eq 0 ]; then echo "shepherd stand-aside selftest: all passed"; else echo "shepherd stand-aside selftest: $fails FAILED"; exit 1; fi
