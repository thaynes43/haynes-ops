#!/usr/bin/env bash
# Self-test for the morning summary's sender (story 554): `gate.sh morning-summary`, run by the
# health gate's 06:30 America/New_York CronJob, and page.sh's low priority.
#
# WHY THIS EXISTS
# ---------------
# Tom gets one quiet message a morning. The sender must read the lane's copy at the moment it
# acts; say so itself when the lane wrote nothing for the night or did not finish it (decided by
# the night's last tick, not a count: the review's L6); fit Pushover's limit by whole lines,
# counted in bytes, never splitting a character (L9); send at priority -1; carry outside text
# (PR titles, package names) as data that never runs; carry no secret; not send twice; try three
# times, never fail its Job, and say the next morning which night was not delivered (M5).
# Pinned below against a fake API server and a fake curl (test-fake-cluster.mjs); the shipped
# gate.sh and page.sh run unchanged, under a UTF-8 locale (so a sender that counted characters
# instead of bytes would pass multi-byte text over the limit, and fail here).
#
# Usage: scripts/upgrade-agent/gate-morning-selftest.sh
# Requires: bash, jq, node, python3. No cluster access, no network.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GATE_RES="$REPO_ROOT/kubernetes/main/apps/upgrade-agent/health-gate/app/resources"
FAKE="$REPO_ROOT/scripts/upgrade-agent/test-fake-cluster.mjs"
for t in jq node python3; do command -v "$t" >/dev/null 2>&1 || { echo "FATAL: $t is required"; exit 1; }; done
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/gate"
for t in kubectl curl; do printf '#!/usr/bin/env bash\nexec node %q %s "$@"\n' "$FAKE" "$t" > "$TMP/bin/$t"; chmod +x "$TMP/bin/$t"; done
# the ConfigMap mounts the scripts 0555; git does not carry the mode
cp "$GATE_RES/gate.sh" "$GATE_RES/page.sh" "$TMP/gate/"; chmod +x "$TMP/gate/"*.sh
UTF8="$(locale -a 2>/dev/null | grep -iE '^c\.utf-?8$' | head -1)"; UTF8="${UTF8:-C.UTF-8}"
NIGHT="$(TZ=America/New_York date +%F)"
D1="$(date -u -d "$NIGHT -1 day" +%F)"; D2="$(date -u -d "$NIGHT -2 day" +%F)"; D4="$(date -u -d "$NIGHT -4 day" +%F)"
fails=0; passes=0; n=0
ok()   { passes=$((passes+1)); echo "ok    $1"; }
bad()  { fails=$((fails+1)); echo "FAIL  $1${2:+: $2}"; }

# world <data-json or "absent"> — a fresh fake cluster with ConfigMap upgrade-orphan-report
world() {
  n=$((n+1)); W="$TMP/w$n"; mkdir -p "$W"
  if [ "$1" = absent ]; then echo '{"rv":100,"configmaps":{}}' > "$W/state.json"
  else printf '%s' "$1" | jq -c '{rv:101, configmaps:{"upgrade-agent/upgrade-orphan-report":{apiVersion:"v1",kind:"ConfigMap",metadata:{name:"upgrade-orphan-report",namespace:"upgrade-agent",resourceVersion:"101"},data:.}}}' > "$W/state.json"; fi
  : > "$W/curl.log"; : > "$W/kube.log"
}
send() {  # runs the sender in world $W; extra env as arguments
  env LC_ALL="$UTF8" LANG="$UTF8" PATH="$TMP/bin:$PATH" FAKE_KUBE_STATE="$W/state.json" FAKE_KUBE_LOG="$W/kube.log" FAKE_CURL_LOG="$W/curl.log" \
    PUSHOVER_TOKEN=secret-app-token-123 PUSHOVER_USER_KEY=secret-user-key-456 MORNING_RETRY_SECONDS=0 "$@" \
    bash "$TMP/gate/gate.sh" morning-summary > "$W/out" 2> "$W/err"
  echo $? > "$W/rc"
}
field() { head -1 "$W/curl.log" | jq -j --arg f "$1" 'first(.[] | select(startswith($f + "="))) | sub("^[^=]*="; "")'; }   # one form field of the first post, whole
posts() { grep -c . "$W/curl.log"; }
stamp() { jq -r '.configmaps["upgrade-agent/upgrade-orphan-report"].data.morning_sent_night // ""' "$W/state.json"; }
nbytes() { printf '%s' "$1" | LC_ALL=C wc -c | tr -d ' '; }
utf8ok() { printf '%s' "$1" | python3 -I -c 'import sys; sys.stdin.buffer.read().decode("utf-8")' 2>/dev/null; }
# base <text> [night] [ticks] [last tick] [sent night]
base() { jq -n --arg t "$1" --arg night "${2:-$NIGHT}" --arg ticks "${3:-20}" --arg last "${4:-05:45}" --arg sent "${5:-}" '{count:"0", summary:"", last_paged:"0", morning_text:$t, morning_night:$night, morning_composed_at:"2026-11-13T10:45:05.419Z", morning_ticks:$ticks, morning_last_tick:$last, morning_last_tick_expected:"05:45", morning_mode:"dryrun"} + (if $sent != "" then {morning_sent_night:$sent} else {} end)'; }
TEXT=$'Night of 13 Nov (lane dry-run): the lane finished the night: 20 runs, the last at 05:45.\nMerged 0 since 05:45 12 Nov. Reverted 0.\nHealth checks after merging: 0 passed, 0 still being checked, 0 failed. Waiting for you: 0 items.'

echo "== the lane's summary for this night"
world "$(base "$TEXT" "$NIGHT" 20 05:45 "$D1")"; send
[ "$(posts)" = 1 ] && ok "one message is sent" || bad "one message is sent" "$(posts) posts; $(cat "$W/err")"
[ "$(field priority)" = "-1" ] && ok "at Pushover priority -1 (no sound or vibration, still a notification)" || bad "priority -1" "$(field priority)"
[ "$(field title)" = "Night upgrades $NIGHT" ] && ok "titled 'Night upgrades $NIGHT'" || bad "title" "$(field title)"
[ "$(field message)" = "$TEXT" ] && ok "the message is the lane's text, unchanged (yesterday was delivered: no extra line)" || bad "message" "$(field message)"
[ "$(stamp)" = "$NIGHT" ] && ok "morning_sent_night is stamped at the version read" || bad "stamp" "$(stamp)"
[ "$(jq -r '.configmaps["upgrade-agent/upgrade-orphan-report"].data.morning_text' "$W/state.json")" = "$TEXT" ] && ok "the stamp keeps the lane's keys" || bad "stamp keeps keys"
grep -q 'secret-app-token-123\|secret-user-key-456' "$W/out" "$W/err" && bad "no secret in the log" || ok "no secret in the log"
jq -r '.[]' "$W/curl.log" | grep -v '^token=\|^user=' | grep -q 'secret-' && bad "no secret in the message" || ok "the token and user key ride only their own form fields"
send
[ "$(posts)" = 1 ] && grep -q 'already sent for the night of' "$W/err" && ok "a second run the same morning sends nothing" || bad "no second send" "$(posts)"

echo "== no summary for this night"
world "$(base "" "$NIGHT")"; send
case "$(field message)" in "The night lane wrote no summary for the night of $NIGHT: it did not run, or no run finished. Its last summary is for $NIGHT."*) ok "an empty text: the sender says the lane wrote no summary" ;; *) bad "empty text" "$(field message)" ;; esac
world "$(base "$TEXT" "2026-01-02")"; send
case "$(field message)" in *"wrote no summary for the night of $NIGHT"*"Its last summary is for 2026-01-02."*) ok "a summary for an older night: the sender says so, with the night it has" ;; *) bad "older night" "$(field message)" ;; esac
[ "$(field priority)" = "-1" ] && ok "also at priority -1" || bad "priority" "$(field priority)"
world "$(base "$TEXT" "not-a-date")"; send
case "$(field message)" in *"Its last summary is for no night."*) ok "a night that is not a date is not echoed" ;; *) bad "bad night" "$(field message)" ;; esac
world absent; send
case "$(field message)" in "The night lane's summary for the night of $NIGHT could not be read"*) ok "the ConfigMap absent: the sender says it could not read the summary" ;; *) bad "absent" "$(field message)" ;; esac
world "$(base "$TEXT")"; send FAKE_KUBE_UNREADABLE=1
case "$(field message)" in *"could not be read"*) ok "the ConfigMap unreadable: the same" ;; *) bad "unreadable" "$(field message)" ;; esac

echo "== finished is the night's last tick, not a count (L6)"
world "$(base "$TEXT" "$NIGHT" 7 02:30)"; send
first="$(field message | head -1)"
[ "$first" = "The night lane did not finish the night: its last run was at 02:30, not 05:45 (7 runs)." ] && ok "the last run at 02:30: did not finish, with the time and the count" || bad "not finished" "$first"
[ "$(field message | tail -n +2)" = "$TEXT" ] && ok "then the lane's last summary" || bad "then the text"
world "$(base "$TEXT" "$NIGHT" 16 05:45)"; send
[ "$(field message)" = "$TEXT" ] && ok "spring forward: 16 ticks, the last at 05:45: finished, no extra line" || bad "spring forward" "$(field message | head -1)"
world "$(base "$TEXT" "$NIGHT" 24 05:45)"; send
[ "$(field message)" = "$TEXT" ] && ok "fall back: 24 ticks, the last at 05:45: finished" || bad "fall back" "$(field message | head -1)"
world "$(base "$TEXT" "$NIGHT" 20 "5:45")"; send
field message | head -1 | grep -q 'did not finish the night: its last run was at an unknown time' && ok "a last tick that is not HH:MM: not believed, did not finish" || bad "bad last tick" "$(field message | head -1)"

echo "== too long for the limit, in bytes (L9)"
LONG="Night of 13 Nov: lane dry-run, 20 ticks."
for i in $(seq 1 12); do LONG="$LONG"$'\n'"- #50$i $(printf 'é%.0s' $(seq 1 60))"; done
world "$(base "$LONG" "$NIGHT" 20 05:45 "$D1")"; send
msg="$(field message)"
[ "$(nbytes "$msg")" -le 1000 ] && ok "multi-byte text (${#LONG} characters, $(nbytes "$LONG") bytes): at most 1000 bytes ($(nbytes "$msg"))" || bad "limit in bytes" "$(nbytes "$msg")"
printf '%s' "$msg" | tail -1 | grep -qE '^and [0-9]+ more lines$' && ok "whole lines cut, the last says 'and N more lines' ($(printf '%s' "$msg" | tail -1))" || bad "and N more lines" "$(printf '%s' "$msg" | tail -1)"
printf '%s\n' "$msg" | sed '$d' | while IFS= read -r l; do printf '%s\n' "$LONG" | grep -qxF -- "$l" || echo CUT; done | grep -q CUT && bad "no line cut in the middle" || ok "no line is cut in the middle"
HEAD="Night of 13 Nov."$'\n'"Waiting for you:"$'\n'"- #1 $(printf 'x%.0s' $(seq 1 990))"$'\n'"Merged:"$'\n'"- by Renovate: #2"
world "$(base "$HEAD" "$NIGHT" 20 05:45 "$D1")"; send
[ "$(field message)" = "Night of 13 Nov."$'\n'"and 4 more lines" ] && ok "a cut never leaves a heading as the last line kept" || bad "dangling heading" "$(field message | head -3)"
ONE="$(printf 'é%.0s' $(seq 1 700))"$'\n'"second line"
world "$(base "$ONE" "$NIGHT" 20 05:45 "$D1")"; send
msg="$(field message)"
[ "$(nbytes "$msg")" -le 1000 ] && utf8ok "$msg" && printf '%s' "$msg" | tail -1 | grep -qx 'and 1 more line' && ok "a first line too long on its own: cut by characters, never inside one, 'and 1 more line' kept ($(nbytes "$msg") bytes)" || bad "one long line" "$(nbytes "$msg") bytes; $(printf '%s' "$msg" | tail -c 40)"

echo "== hostile text is data"
MARK="$TMP/pwned"
EVIL="Night of 13 Nov: \$(touch $MARK) \`touch $MARK\` ; touch $MARK && echo x | sh @/etc/passwd"$'\n'"- #4757 'quoted' \"double\" \${PUSHOVER_TOKEN} %s %n"
world "$(base "$EVIL" "$NIGHT" 20 05:45 "$D1")"; send
[ ! -e "$MARK" ] && ok "nothing in the text ran (no file created)" || bad "the text ran"
[ "$(field message)" = "$EVIL" ] && ok "the text reaches Pushover literally (one quoted --form-string argument)" || bad "literal" "$(field message)"
field message | grep -q 'secret-app-token' && bad "\${PUSHOVER_TOKEN} was expanded" || ok "\${PUSHOVER_TOKEN} in the text is not expanded"

echo "== delivery: retry only what certainly did not go; the Job never fails (M5, second review C and D)"
deliver() {  # deliver <case> <sequence> <posts> <recorded: yes|no> <log pattern>
  world "$(base "$TEXT" "$NIGHT" 20 05:45 "$D1")"; send FAKE_PUSHOVER_SEQ="$2"
  local rec=no; [ "$(stamp)" = "$NIGHT" ] && rec=yes
  if [ "$(posts)" = "$3" ] && [ "$rec" = "$4" ] && grep -q -- "$5" "$W/err" && [ "$(cat "$W/rc")" = 0 ]; then ok "$1"; else bad "$1" "$(posts) posts, recorded $rec, rc $(cat "$W/rc"): $(grep 'morning summary' "$W/err" | tail -1 | cut -c1-160)"; fi
}
deliver "could not connect, then delivered: retried, recorded"          connect,ok      2 yes 'delivered on attempt 2'
deliver "could not resolve the host, then delivered: retried"          resolve,ok      2 yes 'delivered on attempt 2'
deliver "a connect timeout (no connection made), then delivered"       conntimeout,ok  2 yes 'delivered on attempt 2'
deliver "HTTP 500, then delivered: retried"                            500,ok          2 yes 'delivered on attempt 2'
deliver "HTTP 429, then delivered: retried"                            429,ok          2 yes 'delivered on attempt 2'
deliver "a timeout after connecting: may have arrived, not retried (no second copy), not recorded" timeout 1 no 'timed out after connecting: it may have been delivered; not retried'
deliver "HTTP 400: refused, not retried, not recorded"                 400             1 no 'Pushover refused the message'
deliver "three times no connection: three attempts, one ERROR, not recorded" connect,connect,connect 3 no 'NOT delivered for the night of .* after 3 attempt'
[ "$(grep -c 'ERROR' "$W/err")" = 1 ] && ok "exactly one ERROR line" || bad "one ERROR line" "$(grep -c ERROR "$W/err")"
grep -q 'PAGE FAILED: low summary/morning (curl exit 7, no connection made)' "$W/err" && ok "page.sh says why it failed" || bad "page.sh reason" "$(grep 'PAGE FAILED' "$W/err" | head -1)"

echo "== the record of a delivery (second review C)"
# the gate's own half-hourly run writes the ConfigMap between the sender's read and its record
world "$(base "$TEXT" "$NIGHT" 20 05:45 "$D1")"
send FAKE_KUBE_HOOK='["node","-e","const f=process.env.FAKE_KUBE_STATE,fs=require(\"fs\"),s=JSON.parse(fs.readFileSync(f));const c=s.configmaps[\"upgrade-agent/upgrade-orphan-report\"];c.data.last_paged=\"1794600000\";s.rv++;c.metadata.resourceVersion=String(s.rv);fs.writeFileSync(f,JSON.stringify(s))"]' FAKE_KUBE_HOOK_ONCE="$W/hook.once"
[ "$(stamp)" = "$NIGHT" ] && [ "$(jq -r '.configmaps["upgrade-agent/upgrade-orphan-report"].data.last_paged' "$W/state.json")" = 1794600000 ] && grep -q 'recorded: morning_sent_night' "$W/err" \
  && ok "a write between the read and the record: the record is a fresh read after the send, retried once on the conflict; the other write is kept" || bad "conflict" "stamp $(stamp); $(grep 'morning summary' "$W/err" | tail -1)"
world "$(base "$TEXT" "$NIGHT" 20 05:45 "$D1")"; send FAKE_KUBE_UNREADABLE_TIMES=1
case "$(field message)" in *"could not be read"*) ok "the ConfigMap unreadable when the sender read it: its own message" ;; *) bad "unreadable first" "$(field message)" ;; esac
[ "$(stamp)" = "$NIGHT" ] && ok "and the delivery is still recorded once the ConfigMap can be read again" || bad "recorded after unreadable" "$(stamp)"
world absent; send
grep -q 'could not record it (ConfigMap upgrade-orphan-report missing or unreadable)' "$W/err" && ok "the ConfigMap absent: sent, and the log says it could not be recorded (the gate cannot create it)" || bad "absent record" "$(tail -1 "$W/err")"

echo "== the next morning: what is known (second review C)"
world "$(base "$TEXT" "$NIGHT" 20 05:45 "$D2")"; send
[ "$(field message | head -1)" = "No delivery was recorded for the night of $D1, so its summary may not have arrived." ] && [ "$(field message | tail -n +2)" = "$TEXT" ] && ok "the next morning says no delivery was recorded for $D1, and that it may not have arrived" || bad "next day" "$(field message | head -1)"
world "$(base "$TEXT" "$NIGHT" 20 05:45 "$D4")"; send
[ "$(field message | head -1)" = "No delivery was recorded for the nights from $(date -u -d "$D4 +1 day" +%F) to $D1, so their summaries may not have arrived." ] && ok "several nights: from which to which" || bad "several" "$(field message | head -1)"
world "$(base "$TEXT" "$NIGHT" 7 02:30 "$D2")"; send
[ "$(field message | head -1)" = "No delivery was recorded for the night of $D1, so its summary may not have arrived." ] && [ "$(field message | sed -n 2p | cut -c1-40)" = "The night lane did not finish the night:" ] && ok "both: the unrecorded night first, then the unfinished night" || bad "both" "$(field message | head -2)"
world "$(base "$TEXT" "$NIGHT" 20 05:45 "yesterday; rm -rf /")"; send
[ "$(field message)" = "$TEXT" ] && grep -q 'morning_sent_night is not a date; no line about past deliveries' "$W/err" && ok "a junk record: no line, and it is logged" || bad "junk record" "$(field message | head -1)"

echo "== page.sh and the CronJob"
grep -q '\[ "\$SEV" = "low" \] && prio=-1' "$GATE_RES/page.sh" && ok "page.sh: severity low is priority -1; critical stays 1, others 0" || bad "page.sh low"
# page.sh for its existing caller (gate.sh page(), which ignores the exit code): the same lines
grep -q 'page() { "$GATE_DIR/page.sh" "$@"; REGRESSIONS=$((REGRESSIONS+1)); }' "$GATE_RES/gate.sh" && ok "gate.sh's page() still ignores page.sh's exit code" || bad "page() unchanged"
for c in ok 500 connect timeout; do
  rm -f "$TMP/p.log" "$TMP/p.log.n"
  r="$(env PATH="$TMP/bin:$PATH" FAKE_CURL_LOG="$TMP/p.log" FAKE_PUSHOVER_SEQ="$c" PUSHOVER_TOKEN=t PUSHOVER_USER_KEY=u bash "$TMP/gate/page.sh" critical flux x "body" 2>&1 >/dev/null)"
  case "$c" in ok) want='^paged: critical flux/x — body$' ;; *) want='^PAGE FAILED: critical flux/x' ;; esac
  printf '%s' "$r" | grep -qE "$want" && ok "page.sh, $c: the same first words on stderr as before ($(printf '%s' "$r" | cut -c1-60))" || bad "page.sh $c" "$r"
done
HR="$REPO_ROOT/kubernetes/main/apps/upgrade-agent/health-gate/app/helmrelease.yaml"
grep -q 'schedule: "30 6 \* \* \*"' "$HR" && grep -A3 'schedule: "30 6' "$HR" | grep -q 'timeZone: America/New_York' && ok "the CronJob runs at 06:30 America/New_York" || bad "schedule"
grep -q 'command: \["/bin/bash", "/opt/gate/gate.sh", "morning-summary"\]' "$HR" && ok "the CronJob runs gate.sh morning-summary" || bad "command"
[ "$(grep -c 'name: upgrade-health-gate-secret' "$HR")" = 2 ] && ok "it uses the gate's existing secret (no new one)" || bad "secret"

echo
echo "$passes passed, $fails failed"
[ "$fails" -eq 0 ]
