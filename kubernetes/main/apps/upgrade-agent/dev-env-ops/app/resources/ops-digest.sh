#!/usr/bin/env bash
# ops-digest.sh [preview|flush|status] — the QUIET SUMMARY CHANNEL.
#
# Tom, 2026-08-23: "If the agent fixes the issue NO ESCALATION or PAGE or RC
# SESSION is necessary — a summary non-critical pushover or even an
# admin@haynesnetwork.com email to manofoz@gmail.com is fine for summaries of
# issues that happened and were resolved."
#
# So: silence at the moment of the fix, and a BATCHED after-the-fact summary.
# Never one message per event — that is just paging with extra steps.
#
# WHAT IT SENDS: every order that reached a terminal state and has not been
# digested yet — what fired, what the agent did, whether it VERIFIED, and the
# key you can use to find the transcript. Marks each entry `digested` so a
# re-flush never double-reports.
#
# CHANNEL — Pushover priority -1 (silent: it lands in the app with no sound, no
# vibration, no notification), NOT email. This is a deliberate decision, not a
# missing feature:
#   * A usable SMTP credential DOES exist — the estate-shared 1Password `smtp`
#     item (SMTP_HOST/PORT/USER/PASS/FROM), already proven green in
#     `frontend/haynesnetwork`'s ExternalSecret.
#   * But this pod is the saga-07 INJECTION SURFACE: its sessions read release
#     notes and alert annotations, and its containment is exactly the CNP
#     allowlist + "no secrets". Mounting SMTP auth here would hand a
#     prompt-injectable agent an authenticated relay that can mail arbitrary
#     content to arbitrary addresses — a brand-new exfil channel, and a direct
#     violation of the "no new powers, no secrets" guardrail on this work.
#     Pushover cannot exfiltrate: it only ever reaches Tom's own devices.
#   * If email is wanted later, the shape that does NOT widen this boundary is a
#     separate, NON-agentic `ops-digest-mail` CronJob in `upgrade-agent` that
#     reads the same ConfigMap, holds the smtp secret, has no LLM and no
#     GitHub token, and egresses only to the relay. Documented in
#     .agents/runbooks/agentic-remediation.md; not built, because unbuilt is
#     honest and half-built-untested is what this whole exercise is correcting.
set -uo pipefail

CM="${WORK_ORDER_CM:-upgrade-work-orders}"
NS="${WORK_ORDER_NS:-upgrade-agent}"
CHANNEL="${OPS_DIGEST_CHANNEL:-pushover}"
INTERVAL_H="${OPS_DIGEST_INTERVAL_H:-24}"
MAX_PENDING="${OPS_DIGEST_MAX_PENDING:-8}"
MAX_CHARS="${OPS_DIGEST_MAX_CHARS:-950}"
STATE_KEY="digest.last"
mode="${1:-preview}"
now="$(date -u +%s)"
oplog() { bash /opt/dev-env-ops/ops-log.sh "$@" 2>/dev/null || true; }

data="$(kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null | jq -c '.data // {}' 2>/dev/null)" || data='{}'
[ -n "$data" ] || data='{}'

# Undigested terminal orders, oldest first. `digest.last` is a meta key (status
# "meta") and is excluded by the status filter, not by name.
pending_keys="$(printf '%s' "$data" | jq -r '
  to_entries
  | map(select((.value | fromjson? // {}) as $o
      | ($o.status == "done" or $o.status == "failed" or $o.status == "escalated")
        and (($o.digested // "") == "")))
  | sort_by((.value | fromjson? // {}) | ((.updated // "0") | tonumber? // 0))
  | .[].key' 2>/dev/null)"
n="$(printf '%s\n' $pending_keys | sed '/^$/d' | wc -l | tr -d ' ')"

last_flush="$(printf '%s' "$data" | jq -r --arg k "$STATE_KEY" \
  '(.[$k] // "{}") | (fromjson? // {}) | .last_flush // "0"' 2>/dev/null)"
age=$(( now - ${last_flush:-0} ))

build() {
  local body="" key o line verdict
  for key in $pending_keys; do
    o="$(printf '%s' "$data" | jq -r --arg k "$key" '.[$k]' 2>/dev/null | jq -c 'fromjson? // {}' 2>/dev/null)"
    [ -n "$o" ] || continue
    case "$(printf '%s' "$o" | jq -r '.status')" in
      done)      verdict="FIXED" ;;
      escalated) verdict="ESCALATED (you were paged)" ;;
      failed)    verdict="FAILED" ;;
      *)         verdict="?" ;;
    esac
    line="$(printf '%s' "$o" | jq -r --arg k "$key" --arg v "$verdict" '
      "• \($k) [\($v)] \(.reason // .title // "" | .[0:90])\n  → \(.note // "no note" | .[0:170])"' 2>/dev/null)"
    body="${body}${line}
"
  done
  printf '%s' "$body"
}

case "$mode" in
  status)
    echo "undigested terminal orders: $n"
    echo "last flush: $( [ "${last_flush:-0}" = 0 ] && echo never || date -u -d "@$last_flush" +%FT%TZ 2>/dev/null || echo "$last_flush")  (${age}s ago)"
    echo "would flush now: $( { [ "$n" -ge "$MAX_PENDING" ] || { [ "$n" -gt 0 ] && [ "$age" -ge $(( INTERVAL_H * 3600 )) ]; }; } && echo yes || echo no )"
    exit 0 ;;

  preview)
    [ "$n" -gt 0 ] || { echo "(nothing to digest)"; exit 0; }
    echo "--- digest preview ($n item(s)) ---"; build; exit 0 ;;

  flush) ;;
  *) echo "usage: ops-digest.sh [preview|flush|status]" >&2; exit 2 ;;
esac

[ "$n" -gt 0 ] || { echo "ops-digest: nothing to flush."; exit 0; }

body="$(build)"
shown="$n"
if [ "${#body}" -gt "$MAX_CHARS" ]; then
  body="$(printf '%s' "$body" | cut -c1-"$MAX_CHARS")
…truncated — full detail: kubectl -n ${NS} get cm ${CM} -o json"
fi
title="ops digest: ${n} handled"

case "$CHANNEL" in
  pushover)
    # priority -1 = delivered silently (no sound/vibration). The whole contract
    # of this channel is that it must never wake anyone.
    if curl -sf --max-time 15 https://api.pushover.net/1/messages.json \
        --form-string "token=${PUSHOVER_TOKEN:-}" \
        --form-string "user=${PUSHOVER_USER_KEY:-}" \
        --form-string "title=[dev-env-ops] $title" \
        --form-string "message=$body" \
        --form-string "priority=-1" >/dev/null; then
      echo "ops-digest: sent ($n items, priority -1)."
    else
      echo "ops-digest: SEND FAILED — leaving entries undigested for the next attempt." >&2
      oplog digest '-' n="$n" channel="$CHANNEL" result=send-failed
      exit 1
    fi ;;
  none)
    echo "ops-digest: channel=none — logging only."; printf '%s\n' "$body" ;;
  *)
    echo "ops-digest: unknown channel '$CHANNEL' (implemented: pushover, none). Not sending." >&2
    exit 1 ;;
esac

# Mark digested + record the flush. One write for the whole batch; if it fails
# the worst case is a duplicate digest next cycle, which is noise, not loss.
marks="$(printf '%s\n' $pending_keys | sed '/^$/d' | jq -R . | jq -sc .)"
kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
  | jq --argjson keys "$marks" --arg now "$now" --arg sk "$STATE_KEY" '
      reduce $keys[] as $k (.;
        .data[$k] = ((.data[$k] | fromjson? // {}) | .digested = $now | tojson))
      | .data[$sk] = ({status:"meta", last_flush:$now, updated:$now} | tojson)' \
  | kubectl -n "$NS" replace -f - >/dev/null 2>&1 \
  || echo "ops-digest: WARN could not mark entries digested (a duplicate digest may follow)." >&2

oplog digest '-' n="$shown" channel="$CHANNEL" result=sent
exit 0
