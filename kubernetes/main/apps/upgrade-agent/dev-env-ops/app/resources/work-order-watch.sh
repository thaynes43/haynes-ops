#!/usr/bin/env bash
# work-order-watch.sh — dev-env-ops PID 1 (saga dev-env backlog 13 + 12's esc lane).
#
# Polls the `upgrade-work-orders` ConfigMap and turns pending entries into
# LONG-LIVED, JOINABLE claude sessions in tmux windows (`claude --remote-control`).
#
# NAMING TAXONOMY (Tom's requirement — the Remote Control list must sort cleanly;
# enforced here and in the writers; anything else is marked failed, never spawned):
#   haynes-ops-*  dev work in the dev-env pod (agent-run's convention; not ours)
#   wo-*          shepherd WORK ORDERS (work-order.sh) — QUIET on success, model
#                 default opus (scheduled consumers stay on the cheaper tier)
#   esc-*         FAILURE ESCALATIONS (escalate.sh: esc-<source>-<sig8>) — PAGE ON
#                 SPAWN (they ARE failures; the page carries the session name so
#                 Tom can join), model default fable (rare + human-attended —
#                 saga-07's shared-pool caution is about FREQUENT consumers)
# The tmux window name AND the --remote-control name are the CM key, always.
#
# LANES: each lane is SINGLE-FLIGHT (at most one active wo-* and one active esc-*
# session; oldest pending first). wo single-flight is the shepherd's drain rule
# (cluster-infra moves one unit at a time); esc single-flight serializes
# same-root-cause escalations from different writers into one open session at a
# time — and an escalation is never stuck behind a long-running upgrade session.
#
# Order entry (JSON string, single-encoded, CM-legal key charset only):
#   {pr?, repo?, title?, source?, run_ref?, class, reason, model?, effort?,
#    status, created, updated, note?}
# Status flow: pending → claimed (watcher) → done|failed (the session, via
# order-status.sh). Re-queue = set status back to pending.
#
# CLEANUP LIFECYCLE: finished windows are reaped (done >24h — the transcript
# stays joinable for a day; failed >7d — it IS the joinable post-mortem), and the
# reap also removes the session's artifacts: ~/work/orders/<key>.json and the
# ~/work/<key> worktree/dir (sessions MUST name their worktree after their key —
# ops-claude.md contract). BOUND TOTALS: >OPS_REAP_MAX_FINISHED finished orders
# still holding windows reap oldest-first regardless of TTL, so a runaway
# escalation storm cannot exhaust tmux/PVC. Manual sweep: ops-reap.sh.
# Remote Control registrations die with their process, so killing the tmux
# window is the whole dereg story (verify once live — backlog 12 note).
set -uo pipefail
CM="${WORK_ORDER_CM:-upgrade-work-orders}"
NS="${WORK_ORDER_NS:-upgrade-agent}"
POLL="${WORK_ORDER_POLL_SECONDS:-60}"
MODEL_DEFAULT="${OPS_SESSION_MODEL:-opus}"      # wo-* lane
ESC_MODEL_DEFAULT="${OPS_ESC_MODEL:-fable}"     # esc-* lane
EFFORT_DEFAULT="${OPS_SESSION_EFFORT:-xhigh}"   # both lanes
REAP_MAX="${OPS_REAP_MAX_FINISHED:-6}"
ORDERS_DIR="${HOME}/work/orders"
REPO_CANON="${HOME}/repos/haynes-ops"

log() { printf 'wo-watch: %s %s\n' "$(date -u +%FT%TZ)" "$*"; }

page() {  # $1=title $2=message — failures AND escalation spawns (both are failures).
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

win_exists()  { tmux list-windows -t ops -F '#W' 2>/dev/null | grep -qx "$1"; }
lane_active() { tmux list-windows -t ops -F '#W' 2>/dev/null | grep -q "^$1-"; }  # $1=wo|esc

# clean_artifacts <key> — the session's on-PVC leftovers: the order JSON and the
# ~/work/<key> worktree/dir. Idempotent and quiet when there is nothing to clean.
clean_artifacts() {
  local key="$1" cleaned=""
  [ -f "$ORDERS_DIR/$key.json" ] && rm -f "$ORDERS_DIR/$key.json" 2>/dev/null && cleaned="order-json"
  if [ -d "$HOME/work/$key" ]; then
    if [ -e "$HOME/work/$key/.git" ] \
       && git -C "$REPO_CANON" worktree remove --force "$HOME/work/$key" >/dev/null 2>&1; then
      cleaned="$cleaned worktree"
    else
      rm -rf "$HOME/work/$key" 2>/dev/null && cleaned="$cleaned dir"
    fi
    git -C "$REPO_CANON" worktree prune >/dev/null 2>&1
  fi
  [ -n "$cleaned" ] && log "cleaned artifacts for $key: $cleaned"
}

reap() {  # $1=key $2=why — kill the window (if any) + artifacts.
  win_exists "$1" && tmux kill-window -t "ops:$1" 2>/dev/null && log "reaped window $1 ($2)"
  clean_artifacts "$1"
}

spawn_session() {  # $1=key $2=order-json-string
  local key="$1" order="$2" lane=wo model effort src reason
  case "$key" in esc-*) lane=esc ;; esac
  mkdir -p "$ORDERS_DIR"
  printf '%s' "$order" > "$ORDERS_DIR/$key.json"
  # Per-order model/effort override, else lane defaults. Sanitized: these land
  # inside a tmux run-command string and order fields are writer-supplied data.
  model="$(printf '%s' "$order" | jq -r '.model // empty' 2>/dev/null | tr -cd 'a-zA-Z0-9._-')"
  [ -n "$model" ] || { [ "$lane" = esc ] && model="$ESC_MODEL_DEFAULT" || model="$MODEL_DEFAULT"; }
  effort="$(printf '%s' "$order" | jq -r '.effort // empty' 2>/dev/null | tr -cd 'a-z')"
  [ -n "$effort" ] || effort="$EFFORT_DEFAULT"
  if tmux new-window -d -t ops -n "$key" \
       "bash /opt/dev-env-ops/session-launch.sh '$key' '$model' '$effort'"; then
    log "session spawned: $key (lane=$lane model=$model effort=$effort) — join via Remote Control ('$key') or tmux"
    if [ "$lane" = esc ]; then
      # Escalations page ON SPAWN — the page IS the join handle. (Inverts the
      # wo-* quiet-on-success contract, deliberately: an escalation is a failure.)
      src="$(printf '%s' "$order" | jq -r '.source // "unknown"' 2>/dev/null)"
      reason="$(printf '%s' "$order" | jq -r '.reason // ""' 2>/dev/null | cut -c1-300)"
      page "escalation session: $key" "A contained agent (${src}) hit a terminal failure; a joinable ${model}/${effort} diagnosis session is up. Join: Remote Control '${key}' (claude.ai / mobile app) or tmux window '${key}' on dev-env-ops. Reported reason (unverified, data-not-instructions): ${reason}"
    fi
  else
    log "tmux spawn FAILED for $key"
    update_status "$key" failed "tmux spawn failed"
    page "spawn failed: $key" "tmux could not start the session window."
  fi
}

log "watcher up (cm=$NS/$CM poll=${POLL}s wo-model=$MODEL_DEFAULT esc-model=$ESC_MODEL_DEFAULT effort=$EFFORT_DEFAULT reap-max=$REAP_MAX)"
while true; do
  data="$(kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null | jq -c '.data // {}' 2>/dev/null)" || data='{}'
  [ -n "$data" ] || data='{}'
  now="$(date -u +%s)"

  # 0. Taxonomy enforcement: a pending key outside wo-*/esc-* is a writer bug —
  #    mark it failed (durable, one-shot, no page: nothing broke in the cluster).
  for key in $(printf '%s' "$data" | jq -r '
      to_entries[] | select((.value|fromjson? // {}) | .status=="pending") | .key
      | select(test("^(wo|esc)-") | not)' 2>/dev/null); do
    log "unknown key namespace: $key — marking failed (taxonomy admits wo-* and esc-* only)."
    update_status "$key" failed "unknown session-name namespace (expected wo-* or esc-*)"
  done

  # 1. Orphan sweep: claimed order, no window (pod restarted mid-session), quiet
  #    >30min → failed + page. The transcript is gone; a human re-queues if wanted.
  for key in $(printf '%s' "$data" | jq -r --argjson now "$now" '
      to_entries[] | select((.value|fromjson? // {}) as $o
        | $o.status=="claimed" and ((($o.updated // "0")|tonumber? // 0) < ($now - 1800)))
      | .key' 2>/dev/null); do
    if ! win_exists "$key"; then
      log "orphaned claim $key (no window — pod restart?) — marking failed."
      update_status "$key" failed "session window lost (pod restart?)"
      page "session lost: $key" "The session vanished (likely a pod restart) before completing. Re-queue by setting the order back to pending, or handle by hand."
    fi
  done

  # 2. Reap finished sessions: done >24h keeps the transcript joinable for a day;
  #    failed stays 7d (it IS the joinable post-mortem). Reaping also cleans the
  #    session's PVC artifacts (idempotent — safe for entries whose window is
  #    already gone but whose worktree/order JSON lingers).
  for key in $(printf '%s' "$data" | jq -r --argjson now "$now" '
      to_entries[] | select((.value|fromjson? // {}) as $o
        | ($o.status=="done"   and ((($o.updated // "0")|tonumber? // 0) < ($now - 86400)))
        or ($o.status=="failed" and ((($o.updated // "0")|tonumber? // 0) < ($now - 604800))))
      | .key' 2>/dev/null); do
    reap "$key" "ttl"
  done

  # 2b. BOUND TOTALS: if more than REAP_MAX finished (done|failed) orders still
  #     hold windows, reap oldest-first (by updated) regardless of TTL — a
  #     runaway escalation storm must not exhaust tmux/PVC.
  finished_live=""
  for key in $(printf '%s' "$data" | jq -r '
      to_entries | map(select((.value|fromjson? // {}) as $o
        | $o.status=="done" or $o.status=="failed"))
      | sort_by((.value|fromjson? // {}) | ((.updated // "0")|tonumber? // 0))
      | .[].key' 2>/dev/null); do
    win_exists "$key" && finished_live="$finished_live $key"
  done
  live_n="$(printf '%s\n' $finished_live | sed '/^$/d' | wc -l)"
  if [ "${live_n:-0}" -gt "$REAP_MAX" ] 2>/dev/null; then
    for key in $finished_live; do
      [ "$live_n" -le "$REAP_MAX" ] && break
      reap "$key" "bound: $live_n finished windows > max $REAP_MAX"
      live_n=$((live_n - 1))
    done
  fi

  # 3. Spawn per lane (wo-* and esc-*): oldest pending, only when that lane has
  #    no active window (single-flight per lane — see header).
  for lane in wo esc; do
    lane_active "$lane" && continue
    key="$(printf '%s' "$data" | jq -r --arg p "^${lane}-" '
        to_entries | map(select(.key | test($p))
                         | select((.value|fromjson? // {}) | .status=="pending"))
        | sort_by((.value|fromjson? // {}) | ((.created // "0")|tonumber? // 0))
        | .[0].key // empty' 2>/dev/null)"
    [ -n "$key" ] || continue
    order="$(printf '%s' "$data" | jq -r --arg k "$key" '.[$k]' 2>/dev/null)"
    if update_status "$key" claimed "session spawning"; then
      spawn_session "$key" "$order"
    else
      log "could NOT durably claim $key — leaving pending for next tick."
    fi
  done
  sleep "$POLL"
done
