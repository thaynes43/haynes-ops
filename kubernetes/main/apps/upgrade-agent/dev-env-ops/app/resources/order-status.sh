#!/usr/bin/env bash
# order-status.sh <key> <done|failed|working|escalate> "<note>" — how a session
# reports its outcome. The status vocabulary IS the paging policy:
#
#   done      terminal, SUCCESS.  SILENT — no page, ever. For rem-* this is the
#             whole point: "if the agent fixes the issue NO ESCALATION or PAGE
#             or RC SESSION is necessary" (Tom, 2026-08-23). It still lands in
#             the quiet digest.
#   working   NOT terminal. A heartbeat for a long job: refreshes `updated` so
#             the stale-lane watchdog does not mistake "still fixing" for
#             "died". No page. Call it if you will be busy more than ~30min.
#   escalate  terminal for this order, and the ONLY route from an autonomous
#             remediation to a human: marks the order `escalated` and files a
#             real esc-* order, which the watcher turns into a joinable Remote
#             Control session AND pages Tom with its name. Use it for case 3
#             (needs a human action you cannot take — node reboot, physical,
#             power, anything outside RBAC/egress) and case 4 (you tried and
#             failed). Put the EXACT human steps in the note.
#   failed    terminal failure. On wo-*/esc-* this pages directly and the
#             session stays joinable. On a rem-* order it is UPGRADED to
#             `escalate`: a headless session has no window for Tom to join, so
#             "failed" without a session would page him toward nothing.
#
# A fix you cannot VERIFY is a failed fix — escalate it, do not call it done.
set -uo pipefail
key="${1:?usage: order-status.sh <key> <done|failed|working|escalate> \"<note>\"}"
status="${2:?usage: order-status.sh <key> <done|failed|working|escalate> \"<note>\"}"
note="${3:-}"
case "$status" in done|failed|working|escalate) ;; *) echo "status must be done|failed|working|escalate" >&2; exit 2 ;; esac

CM="${WORK_ORDER_CM:-upgrade-work-orders}"
NS="${WORK_ORDER_NS:-upgrade-agent}"
oplog() { bash /opt/dev-env-ops/ops-log.sh "$@" 2>/dev/null || true; }

# A headless remediation has no joinable window, so a bare page would point Tom
# at nothing. Route it through the escalation lane instead, which spawns one.
if [ "$status" = "failed" ] && [ "${key#rem-}" != "$key" ]; then
  echo "order-status: rem-* 'failed' is upgraded to 'escalate' (a headless session has no window to join)." >&2
  status=escalate
fi

# `working` is a heartbeat: keep the recorded status, just bump `updated`.
write_status="$status"
[ "$status" = "escalate" ] && write_status=escalated

before="$(kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
  | jq -r --arg k "$key" '(.data[$k] // "{}") | (fromjson? // {}) | .status // "unknown"')"

if [ "$status" = "working" ]; then
  kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
    | jq --arg k "$key" --arg n "$note" --arg now "$(date -u +%s)" '
        .data[$k] = ((.data[$k] | fromjson? // {}) | .note=$n | .updated=$now | tojson)' \
    | kubectl -n "$NS" replace -f - >/dev/null \
    || { echo "order-status: could not heartbeat $CM/$key" >&2; exit 1; }
  oplog progress "$key" note="$note"
  echo "order $key -> heartbeat (status unchanged: $before)"
  exit 0
fi

kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
  | jq --arg k "$key" --arg s "$write_status" --arg n "$note" --arg now "$(date -u +%s)" '
      .data[$k] = ((.data[$k] | fromjson? // {}) | .status=$s | .note=$n | .updated=$now | tojson)' \
  | kubectl -n "$NS" replace -f - >/dev/null \
  || { echo "order-status: could not update $CM/$key" >&2; exit 1; }

case "$status" in
  done)
    # SILENT by contract. The digest (ops-digest.sh) is the only place this
    # surfaces, and it is batched — see the runbook's "who gets told" column.
    oplog fixed "$key" note="$note"
    ;;

  escalate)
    oplog escalated "$key" why="$note"
    # Reuse escalate.sh so the esc-* taxonomy, signature and dedup live in ONE
    # place. Keyed on the order's own signature (the responder passes
    # alertname@ns) so a promotion dedups against any other writer escalating
    # the same underlying condition.
    sig="$(kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
      | jq -r --arg k "$key" '(.data[$k] // "{}") | (fromjson? // {}) | .sig // empty')"
    [ -n "$sig" ] || sig="$key"
    if [ -x /opt/coordination/escalate.sh ] || [ -f /opt/coordination/escalate.sh ]; then
      ESCALATE_SIG="$sig" bash /opt/coordination/escalate.sh rem "order:${key}" \
        "AUTONOMOUS REMEDIATION COULD NOT FINISH (${key}). ${note}" \
        && oplog escalated "$key" esc=filed sig="$sig" \
        || { oplog escalated "$key" esc=FAILED sig="$sig"
             # Last resort: the promotion failed, so page directly rather than
             # let a case-3/4 incident die quiet. Silence must be EARNED by
             # fixing, never fallen into.
             curl -sf --max-time 10 https://api.pushover.net/1/messages.json \
               --form-string "token=${PUSHOVER_TOKEN:-}" \
               --form-string "user=${PUSHOVER_USER_KEY:-}" \
               --form-string "title=[dev-env-ops] ${key} needs you (escalation FAILED to file)" \
               --form-string "message=${note:-no note} — the automatic promotion to a joinable session did not land, so there is NO session to join. Check the dev-env-ops pod by hand." \
               --form-string "priority=1" >/dev/null; }
    else
      echo "order-status: /opt/coordination/escalate.sh is not mounted — paging directly." >&2
      curl -sf --max-time 10 https://api.pushover.net/1/messages.json \
        --form-string "token=${PUSHOVER_TOKEN:-}" \
        --form-string "user=${PUSHOVER_USER_KEY:-}" \
        --form-string "title=[dev-env-ops] ${key} needs you" \
        --form-string "message=${note:-no note} — (no coordination lib mounted, so no session was spawned)." \
        --form-string "priority=1" >/dev/null
    fi
    ;;

  failed)
    oplog closed "$key" status=failed note="$note"
    curl -sf --max-time 10 https://api.pushover.net/1/messages.json \
      --form-string "token=${PUSHOVER_TOKEN:-}" \
      --form-string "user=${PUSHOVER_USER_KEY:-}" \
      --form-string "title=[dev-env-ops] work order ${key} FAILED" \
      --form-string "message=${note:-no reason recorded} — session stays joinable: Remote Control '${key}' / tmux window '${key}' on the dev-env-ops pod." \
      --form-string "priority=0" >/dev/null || echo "order-status: page failed" >&2
    ;;
esac
echo "order $key -> $write_status"
