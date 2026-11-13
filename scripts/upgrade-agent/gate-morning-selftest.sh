#!/usr/bin/env bash
# Self-test for the morning summary's sender (story 554): `gate.sh morning-summary`, run by the
# health gate's 06:30 America/New_York CronJob, and page.sh's low priority.
#
# WHY THIS EXISTS
# ---------------
# Tom gets one quiet message a morning. The sender must read the lane's copy at the moment it
# acts, say so itself when the lane wrote nothing for the night or did not finish it, fit
# Pushover's limit by whole lines, send at priority -1 (no sound, still a notification), carry
# outside text (PR titles, package names) as data that never runs, carry no secret, and not
# send twice. Pinned below against a fake API server and a fake curl (test-fake-cluster.mjs);
# the shipped gate.sh and page.sh run unchanged.
#
# Usage: scripts/upgrade-agent/gate-morning-selftest.sh
# Requires: bash, jq, node. No cluster access, no network.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GATE_RES="$REPO_ROOT/kubernetes/main/apps/upgrade-agent/health-gate/app/resources"
FAKE="$REPO_ROOT/scripts/upgrade-agent/test-fake-cluster.mjs"
for t in jq node; do command -v "$t" >/dev/null 2>&1 || { echo "FATAL: $t is required"; exit 1; }; done
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/gate"
for t in kubectl curl; do printf '#!/usr/bin/env bash\nexec node %q %s "$@"\n' "$FAKE" "$t" > "$TMP/bin/$t"; chmod +x "$TMP/bin/$t"; done
# the ConfigMap mounts the scripts 0555; git does not carry the mode
cp "$GATE_RES/gate.sh" "$GATE_RES/page.sh" "$TMP/gate/"; chmod +x "$TMP/gate/"*.sh
NIGHT="$(TZ=America/New_York date +%F)"
fails=0; passes=0; n=0
ok()   { passes=$((passes+1)); echo "ok    $1"; }
bad()  { fails=$((fails+1)); echo "FAIL  $1${2:+: $2}"; }

# world <data-json or "absent"> — a fresh fake cluster with ConfigMap upgrade-orphan-report
world() {
  n=$((n+1)); W="$TMP/w$n"; mkdir -p "$W"
  if [ "$1" = absent ]; then echo '{"rv":100,"configmaps":{}}' > "$W/state.json"
  else jq -n --argjson d "$1" '{rv:101, configmaps:{"upgrade-agent/upgrade-orphan-report":{apiVersion:"v1",kind:"ConfigMap",metadata:{name:"upgrade-orphan-report",namespace:"upgrade-agent",resourceVersion:"101"},data:$d}}}' > "$W/state.json"; fi
  : > "$W/curl.log"; : > "$W/kube.log"
}
send() {  # runs the sender in world $W; extra env as arguments
  env PATH="$TMP/bin:$PATH" FAKE_KUBE_STATE="$W/state.json" FAKE_KUBE_LOG="$W/kube.log" FAKE_CURL_LOG="$W/curl.log" \
    PUSHOVER_TOKEN=secret-app-token-123 PUSHOVER_USER_KEY=secret-user-key-456 "$@" \
    bash "$TMP/gate/gate.sh" morning-summary > "$W/out" 2> "$W/err"
}
field() { head -1 "$W/curl.log" | jq -j --arg f "$1" 'first(.[] | select(startswith($f + "="))) | sub("^[^=]*="; "")'; }   # one form field of the first post, whole
posts() { grep -c . "$W/curl.log"; }
stamp() { jq -r '.configmaps["upgrade-agent/upgrade-orphan-report"].data.morning_sent_night // ""' "$W/state.json"; }
base() { jq -n --arg t "$1" --arg night "${2:-$NIGHT}" --arg ticks "${3:-20}" '{count:"0", summary:"", last_paged:"0", morning_text:$t, morning_night:$night, morning_composed_at:"2026-11-13T10:45:05.419Z", morning_ticks:$ticks, morning_ticks_expected:"20", morning_last_tick:"05:45", morning_mode:"dryrun"}'; }
TEXT=$'Night of 13 Nov: lane dry-run, 20 of 20 ticks (last 05:45). The lane merged nothing.\nWaits for you: nothing.\nMerged since 05:45 12 Nov: nothing.\nChecks: verified none; still checking none.\nReverted: none.'

echo "== the lane's summary for this night"
world "$(base "$TEXT")"; send
[ "$(posts)" = 1 ] && ok "one message is sent" || bad "one message is sent" "$(posts) posts; $(cat "$W/err")"
[ "$(field priority)" = "-1" ] && ok "at Pushover priority -1 (no sound or vibration, still a notification)" || bad "priority -1" "$(field priority)"
[ "$(field title)" = "Night upgrades $NIGHT" ] && ok "titled 'Night upgrades $NIGHT'" || bad "title" "$(field title)"
[ "$(field message)" = "$TEXT" ] && ok "the message is the lane's text, unchanged" || bad "message" "$(field message)"
[ "$(stamp)" = "$NIGHT" ] && ok "morning_sent_night is stamped at the version read" || bad "stamp" "$(stamp)"
[ "$(jq -r '.configmaps["upgrade-agent/upgrade-orphan-report"].data.morning_text' "$W/state.json")" = "$TEXT" ] && ok "the stamp keeps the lane's keys" || bad "stamp keeps keys"
grep -q 'secret-app-token-123\|secret-user-key-456' "$W/out" "$W/err" && bad "no secret in the log" || ok "no secret in the log"
jq -r '.[]' "$W/curl.log" | grep -v '^token=\|^user=' | grep -q 'secret-' && bad "no secret in the message" || ok "the token and user key ride only their own form fields"

echo "== sent already"
send
[ "$(posts)" = 1 ] && grep -q 'already sent for the night of' "$W/err" && ok "a second run the same morning sends nothing" || bad "no second send" "$(posts)"

echo "== no summary for this night"
world "$(base "" "$NIGHT")"; send
case "$(field message)" in "The night lane wrote no summary for the night of $NIGHT: it did not run, or no tick finished. Its last summary is for $NIGHT."*) ok "an empty text: the sender says the lane wrote no summary" ;; *) bad "empty text" "$(field message)" ;; esac
world "$(base "$TEXT" "2026-01-02")"; send
case "$(field message)" in *"wrote no summary for the night of $NIGHT"*"Its last summary is for 2026-01-02."*) ok "a summary for an older night: the sender says so, with the night it has" ;; *) bad "older night" "$(field message)" ;; esac
[ "$(field priority)" = "-1" ] && ok "also at priority -1" || bad "priority" "$(field priority)"
world "$(base "$TEXT" "not-a-date")"; send
case "$(field message)" in *"Its last summary is for no night."*) ok "a night that is not a date is not echoed" ;; *) bad "bad night" "$(field message)" ;; esac
world absent; send
case "$(field message)" in "The night lane's summary for the night of $NIGHT could not be read"*) ok "the ConfigMap absent: the sender says it could not read the summary" ;; *) bad "absent" "$(field message)" ;; esac
world "$(base "$TEXT")"; send FAKE_KUBE_UNREADABLE=1
case "$(field message)" in *"could not be read"*) ok "the ConfigMap unreadable: the same" ;; *) bad "unreadable" "$(field message)" ;; esac

echo "== a night the lane did not finish"
world "$(jq -n --argjson b "$(base "$TEXT" "$NIGHT" 7)" '$b + {morning_last_tick:"02:30"}')"; send
first="$(field message | head -1)"
[ "$first" = "The night lane did not finish the night: 7 of 20 ticks completed, the last at 02:30." ] && ok "first line: did not finish, 7 of 20 ticks, the last at 02:30" || bad "not finished" "$first"
[ "$(field message | tail -n +2)" = "$TEXT" ] && ok "then the lane's last summary" || bad "then the text"

echo "== too long for the limit"
LONG="Night of 13 Nov: lane dry-run, 20 of 20 ticks (last 05:45)."
for i in $(seq 1 30); do LONG="$LONG"$'\n'"- #50$i ghcr.io/example/package-$i: blocked, a check still fails after one retry: Some Long Check Name $i"; done
world "$(base "$LONG" "$NIGHT" 19)"; send
msg="$(field message)"
bytes="$(LC_ALL=C; printf '%s' "$msg" | wc -c | tr -d ' ')"
[ "$bytes" -le 1000 ] && ok "at most 1000 bytes ($bytes)" || bad "limit" "$bytes"
printf '%s' "$msg" | tail -1 | grep -qE '^and [0-9]+ more$' && ok "whole lines cut, the last says 'and N more' ($(printf '%s' "$msg" | tail -1))" || bad "and N more" "$(printf '%s' "$msg" | tail -1)"
printf '%s' "$msg" | head -1 | grep -q 'did not finish' && ok "the 'did not finish' line is kept first" || bad "first line kept"
printf '%s\n' "$msg" | sed '1d;$d' | while IFS= read -r l; do printf '%s\n' "$LONG" | grep -qxF -- "$l" || echo CUT; done | grep -q CUT && bad "no line cut in the middle" || ok "no line is cut in the middle"

echo "== hostile text is data"
MARK="$TMP/pwned"
EVIL="Night of 13 Nov: \$(touch $MARK) \`touch $MARK\` ; touch $MARK && echo x | sh @/etc/passwd"$'\n'"- #4757 'quoted' \"double\" \${PUSHOVER_TOKEN} %s %n"
world "$(base "$EVIL")"; send
[ ! -e "$MARK" ] && ok "nothing in the text ran (no file created)" || bad "the text ran"
[ "$(field message)" = "$EVIL" ] && ok "the text reaches Pushover literally (one quoted --form-string argument)" || bad "literal" "$(field message)"
field message | grep -q 'secret-app-token' && bad "\${PUSHOVER_TOKEN} was expanded" || ok "\${PUSHOVER_TOKEN} in the text is not expanded"

echo "== a failed delivery"
world "$(base "$TEXT")"; send FAKE_PUSHOVER_FAIL=1
[ -z "$(stamp)" ] && grep -q 'NOT delivered' "$W/err" && ok "page.sh failed: no stamp, so a re-run sends it" || bad "failed delivery" "$(stamp) $(cat "$W/err")"

echo "== page.sh and the CronJob"
grep -q '\[ "\$SEV" = "low" \] && prio=-1' "$GATE_RES/page.sh" && ok "page.sh: severity low is priority -1; critical stays 1, others 0" || bad "page.sh low"
HR="$REPO_ROOT/kubernetes/main/apps/upgrade-agent/health-gate/app/helmrelease.yaml"
grep -q 'schedule: "30 6 \* \* \*"' "$HR" && grep -A3 'schedule: "30 6' "$HR" | grep -q 'timeZone: America/New_York' && ok "the CronJob runs at 06:30 America/New_York" || bad "schedule"
grep -q 'command: \["/bin/bash", "/opt/gate/gate.sh", "morning-summary"\]' "$HR" && ok "the CronJob runs gate.sh morning-summary" || bad "command"
grep -q 'name: upgrade-health-gate-secret' "$HR" && [ "$(grep -c 'name: upgrade-health-gate-secret' "$HR")" = 2 ] && ok "it uses the gate's existing secret (no new one)" || bad "secret"

echo
echo "$passes passed, $fails failed"
[ "$fails" -eq 0 ]
