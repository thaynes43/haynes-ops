#!/usr/bin/env bash
# work-order-watch.sh — dev-env-ops PID 1 (saga dev-env backlog 13).
#
# Polls the `upgrade-work-orders` ConfigMap for entries the shepherd wrote
# (status=pending) and turns each into a LONG-LIVED, JOINABLE claude session in a
# tmux window (`claude --remote-control`). SINGLE-FLIGHT: at most one active
# work-order session at a time — cluster-infra moves one unit at a time, same as
# the shepherd's drain rule. QUIET ON SUCCESS: a session that delivers + verifies
# its PR ends with order-status.sh done and nothing pages; only failures page.
#
# Order entry (JSON string keyed `wo-<pr>[-suffix]`, CM-legal key charset only):
#   {pr, repo, title, class, reason, model?, status, created, updated, note?}
# Status flow: pending → claimed (watcher) → done|failed (the session, via
# order-status.sh). A pod restart kills tmux — claimed orders whose window is
# gone are marked failed + paged (the human decides whether to re-queue).
set -uo pipefail
CM="${WORK_ORDER_CM:-upgrade-work-orders}"
NS="${WORK_ORDER_NS:-upgrade-agent}"
POLL="${WORK_ORDER_POLL_SECONDS:-60}"
MODEL_DEFAULT="${OPS_SESSION_MODEL:-opus}"
ORDERS_DIR="${HOME}/work/orders"

log() { printf 'wo-watch: %s %s\n' "$(date -u +%FT%TZ)" "$*"; }

page() {  # $1=title $2=message — FAILURE path only.
  curl -sf --max-time 10 https://api.pushover.net/1/messages.json \
    --form-string "token=${PUSHOVER_TOKEN:-}" \
    --form-string "user=${PUSHOVER_USER_KEY:-}" \
    --form-string "title=[dev-env-ops] $1" \
    --form-string "message=$2" \
    --form-string "priority=0" >/dev/null \
    && log "paged: $1" || log "PAGE FAILED: $1"
}

update_status() {  # $1=key $2=status $3=note ; rc!=0 = did not durably land.
  kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
    | jq --arg k "$1" --arg s "$2" --arg n "${3:-}" --arg now "$(date -u +%s)" '
        .data[$k] = ((.data[$k] | fromjson? // {}) | .status=$s | .note=$n | .updated=$now | tojson)' 2>/dev/null \
    | kubectl -n "$NS" replace -f - >/dev/null 2>&1
}

win_exists() { tmux list-windows -t ops -F '#W' 2>/dev/null | grep -qx "$1"; }
any_active() { tmux list-windows -t ops -F '#W' 2>/dev/null | grep -q '^wo-'; }

spawn_session() {  # $1=key $2=order-json-string
  local key="$1" model
  mkdir -p "$ORDERS_DIR"
  printf '%s' "$2" > "$ORDERS_DIR/$key.json"
  model="$(printf '%s' "$2" | jq -r '.model // empty' 2>/dev/null)"
  [ -n "$model" ] || model="$MODEL_DEFAULT"
  tmux new-window -d -t ops -n "$key" \
    "bash /opt/dev-env-ops/session-launch.sh '$key' '$model'" \
    && log "session spawned: $key (model=$model) — join via Remote Control ('$key') or tmux" \
    || { log "tmux spawn FAILED for $key"; update_status "$key" failed "tmux spawn failed"; page "spawn failed: $key" "tmux could not start the session window."; }
}

log "watcher up (cm=$NS/$CM poll=${POLL}s model=$MODEL_DEFAULT)"
while true; do
  data="$(kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null | jq -c '.data // {}' 2>/dev/null)" || data='{}'
  [ -n "$data" ] || data='{}'
  now="$(date -u +%s)"

  # 1. Orphan sweep: claimed order, no window (pod restarted mid-session), quiet
  #    >30min → failed + page. The transcript is gone; a human re-queues if wanted.
  for key in $(printf '%s' "$data" | jq -r --argjson now "$now" '
      to_entries[] | select((.value|fromjson? // {}) as $o
        | $o.status=="claimed" and ((($o.updated // "0")|tonumber? // 0) < ($now - 1800)))
      | .key' 2>/dev/null); do
    if ! win_exists "$key"; then
      log "orphaned claim $key (no window — pod restart?) — marking failed."
      update_status "$key" failed "session window lost (pod restart?)"
      page "session lost: $key" "The work-order session vanished (likely a pod restart) before completing. Re-queue by setting the order back to pending, or handle by hand."
    fi
  done

  # 2. Reap finished windows: done >24h keeps the transcript joinable for a day;
  #    failed windows stay 7d (they ARE the joinable post-mortem).
  for key in $(printf '%s' "$data" | jq -r --argjson now "$now" '
      to_entries[] | select((.value|fromjson? // {}) as $o
        | ($o.status=="done"   and ((($o.updated // "0")|tonumber? // 0) < ($now - 86400)))
        or ($o.status=="failed" and ((($o.updated // "0")|tonumber? // 0) < ($now - 604800))))
      | .key' 2>/dev/null); do
    if win_exists "$key"; then tmux kill-window -t "ops:$key" 2>/dev/null && log "reaped window $key"; fi
  done

  # 3. Spawn: oldest pending order, only when nothing is active (single-flight).
  if ! any_active; then
    key="$(printf '%s' "$data" | jq -r '
        to_entries | map(select((.value|fromjson? // {}) | .status=="pending"))
        | sort_by((.value|fromjson? // {}) | ((.created // "0")|tonumber? // 0))
        | .[0].key // empty' 2>/dev/null)"
    if [ -n "$key" ]; then
      order="$(printf '%s' "$data" | jq -r --arg k "$key" '.[$k]' 2>/dev/null)"
      if update_status "$key" claimed "session spawning"; then
        spawn_session "$key" "$order"
      else
        log "could NOT durably claim $key — leaving pending for next tick."
      fi
    fi
  fi
  sleep "$POLL"
done
