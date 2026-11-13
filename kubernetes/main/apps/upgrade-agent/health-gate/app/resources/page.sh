#!/usr/bin/env bash
# Direct Pushover page — the gate's INDEPENDENT alert channel (does not route through
# the in-cluster Alertmanager the gate monitors). Never echoes the token.
# Usage: page.sh <severity> <check> <component> <summary>
set -u
SEV="${1:?severity}"; CHECK="${2:?check}"; COMP="${3:-unknown}"; SUM="${4:?summary}"
: "${PUSHOVER_TOKEN:?PUSHOVER_TOKEN unset}"; : "${PUSHOVER_USER_KEY:?PUSHOVER_USER_KEY unset}"

# Pushover rejects messages >1024 chars — and a rejected page is WORSE than a trimmed
# one, because gate.sh stamps last_paged regardless and dedupes the retry for 6h. A
# mass incident (20+ NotReady kustomizations, ESO outage) builds exactly such a body.
# Clamp here so EVERY caller is covered (found by adversarial review 2026-07-06).
SUM="${SUM:0:1000}"

# critical -> priority 1 (breaks through quiet hours); low -> -1 (no sound or vibration, still
# shown as a notification: the morning summary, story 554); else 0.
prio=0; [ "$SEV" = "critical" ] && prio=1; [ "$SEV" = "low" ] && prio=-1
# PAGE_TITLE overrides the title (the morning summary: "Night upgrades <date>").
TITLE="${PAGE_TITLE:-[upgrade-gate] ${SEV} ${CHECK}/${COMP}}"

# Exit status (story 554, the morning sender reads it; gate.sh's page() ignores it, as before):
#   0  sent: HTTP 2xx;
#   2  certainly not sent, safe to retry: the host could not be resolved or connected to (no
#      connection was made), or Pushover answered HTTP 5xx or 429;
#   3  may have been sent: the request timed out or broke after connecting; a retry could
#      deliver a second copy, so do not retry;
#   1  refused: any other HTTP answer (a 4xx: a retry would be refused too).
out="$(curl -s -o /dev/null -w '%{http_code} %{time_connect}' --connect-timeout 5 --max-time 10 \
     https://api.pushover.net/1/messages.json \
     --form-string "token=${PUSHOVER_TOKEN}" \
     --form-string "user=${PUSHOVER_USER_KEY}" \
     --form-string "title=${TITLE}" \
     --form-string "message=${SUM}" \
     --form-string "priority=${prio}" 2>/dev/null)"
rc=$?
code="${out%% *}"; tconn="${out#* }"
case "$code" in [0-9][0-9][0-9]) ;; *) code=000 ;; esac
if [ "$rc" -eq 0 ] && [ "${code:0:1}" = 2 ]; then
  printf 'paged: %s %s/%s — %s\n' "$SEV" "$CHECK" "$COMP" "$SUM" >&2
  exit 0
fi
connected=1; awk -v t="${tconn:-0}" 'BEGIN { exit !(t + 0 == 0) }' && connected=0
if [ "$rc" -eq 0 ]; then
  case "$code" in 5??|429) why="HTTP $code, not accepted"; st=2 ;; *) why="HTTP $code, refused"; st=1 ;; esac
elif [ "$rc" -eq 6 ] || [ "$rc" -eq 7 ] || [ "$connected" = 0 ]; then
  why="curl exit $rc, no connection made"; st=2
else
  why="curl exit $rc after connecting: it may have been delivered"; st=3
fi
printf 'PAGE FAILED: %s %s/%s (%s)\n' "$SEV" "$CHECK" "$COMP" "$why" >&2
exit "$st"
