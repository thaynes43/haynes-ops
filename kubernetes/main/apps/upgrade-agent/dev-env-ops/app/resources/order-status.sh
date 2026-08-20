#!/usr/bin/env bash
# order-status.sh <key> <done|failed> "<note>" — how a work-order session reports
# its outcome. `done` is SILENT (quiet-on-success contract, Tom 2026-08-20);
# `failed` updates the order AND pages the human with the note.
set -uo pipefail
key="${1:?usage: order-status.sh <key> <done|failed> \"<note>\"}"
status="${2:?usage: order-status.sh <key> <done|failed> \"<note>\"}"
note="${3:-}"
case "$status" in done|failed) ;; *) echo "status must be done|failed" >&2; exit 2 ;; esac

CM="${WORK_ORDER_CM:-upgrade-work-orders}"
NS="${WORK_ORDER_NS:-upgrade-agent}"

kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
  | jq --arg k "$key" --arg s "$status" --arg n "$note" --arg now "$(date -u +%s)" '
      .data[$k] = ((.data[$k] | fromjson? // {}) | .status=$s | .note=$n | .updated=$now | tojson)' \
  | kubectl -n "$NS" replace -f - >/dev/null \
  || { echo "order-status: could not update $CM/$key" >&2; exit 1; }

if [ "$status" = "failed" ]; then
  curl -sf --max-time 10 https://api.pushover.net/1/messages.json \
    --form-string "token=${PUSHOVER_TOKEN:-}" \
    --form-string "user=${PUSHOVER_USER_KEY:-}" \
    --form-string "title=[dev-env-ops] work order ${key} FAILED" \
    --form-string "message=${note:-no reason recorded} — session stays joinable: Remote Control '${key}' / tmux window '${key}' on the dev-env-ops pod." \
    --form-string "priority=0" >/dev/null || echo "order-status: page failed" >&2
fi
echo "order $key -> $status"
