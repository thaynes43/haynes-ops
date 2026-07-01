#!/usr/bin/env bash
# Direct Pushover page — the gate's INDEPENDENT alert channel (does not route through
# the in-cluster Alertmanager the gate monitors). Never echoes the token.
# Usage: page.sh <severity> <check> <component> <summary>
set -u
SEV="${1:?severity}"; CHECK="${2:?check}"; COMP="${3:-unknown}"; SUM="${4:?summary}"
: "${PUSHOVER_TOKEN:?PUSHOVER_TOKEN unset}"; : "${PUSHOVER_USER_KEY:?PUSHOVER_USER_KEY unset}"

# critical -> priority 1 (breaks through quiet hours); else 0.
prio=0; [ "$SEV" = "critical" ] && prio=1

if curl -sf --max-time 10 https://api.pushover.net/1/messages.json \
     --form-string "token=${PUSHOVER_TOKEN}" \
     --form-string "user=${PUSHOVER_USER_KEY}" \
     --form-string "title=[upgrade-gate] ${SEV} ${CHECK}/${COMP}" \
     --form-string "message=${SUM}" \
     --form-string "priority=${prio}" >/dev/null; then
  printf 'paged: %s %s/%s — %s\n' "$SEV" "$CHECK" "$COMP" "$SUM" >&2
else
  printf 'PAGE FAILED: %s %s/%s\n' "$SEV" "$CHECK" "$COMP" >&2
fi
