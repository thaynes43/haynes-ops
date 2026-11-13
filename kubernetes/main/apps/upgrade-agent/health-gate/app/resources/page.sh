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

if curl -sf --max-time 10 https://api.pushover.net/1/messages.json \
     --form-string "token=${PUSHOVER_TOKEN}" \
     --form-string "user=${PUSHOVER_USER_KEY}" \
     --form-string "title=${TITLE}" \
     --form-string "message=${SUM}" \
     --form-string "priority=${prio}" >/dev/null; then
  printf 'paged: %s %s/%s — %s\n' "$SEV" "$CHECK" "$COMP" "$SUM" >&2
else
  printf 'PAGE FAILED: %s %s/%s\n' "$SEV" "$CHECK" "$COMP" >&2
  exit 1   # story 554: the morning sender stamps only a delivered summary (gate.sh ignores the rc)
fi
