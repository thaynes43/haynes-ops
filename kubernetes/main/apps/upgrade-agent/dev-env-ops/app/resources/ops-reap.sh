#!/usr/bin/env bash
# ops-reap.sh — manual sweep of dev-env-ops sessions (backlog 12→13 cleanup).
#
#   ops-reap.sh                 list every order: status, age, window?, artifacts?
#   ops-reap.sh clean all       reap EVERY finished (done|failed|escalated) order
#                               now: kill its tmux window + remove
#                               ~/work/orders/<key>.json, <key>.log and the
#                               ~/work/<key> worktree/dir — no TTL wait
#   ops-reap.sh clean <key>     reap one finished order (refuses pending/claimed —
#                               close those via order-status.sh first)
#
# The watcher does the same cleanup automatically on its TTLs (done >24h,
# failed/escalated >7d, bound-total >OPS_REAP_MAX_FINISHED, plus CM-entry
# retention at OPS_ORDER_RETENTION_DAYS); this exists so Tom or an agent can tidy
# on demand without waiting. Safe to run any time — it never touches
# pending/claimed orders or their windows.
#
# Reaping a wo-*/esc-* window also drops its Remote Control registration: the
# registration is owned by the claude PROCESS, so killing the window kills it.
# Verified live 2026-08-23 (the open question left by backlog 12). rem-* sessions
# never register at all.
set -uo pipefail
CM="${WORK_ORDER_CM:-upgrade-work-orders}"
NS="${WORK_ORDER_NS:-upgrade-agent}"
ORDERS_DIR="${HOME}/work/orders"
REPO_CANON="${HOME}/repos/haynes-ops"

win_exists() { tmux list-windows -t ops -F '#W' 2>/dev/null | grep -qx "$1"; }

clean_artifacts() {  # same shape as the watcher's — idempotent, quiet on no-op.
  local key="$1" cleaned=""
  [ -f "$ORDERS_DIR/$key.json" ] && rm -f "$ORDERS_DIR/$key.json" 2>/dev/null && cleaned="order-json"
  [ -f "$ORDERS_DIR/$key.log" ] && rm -f "$ORDERS_DIR/$key.log" 2>/dev/null && cleaned="$cleaned transcript"
  if [ -d "$HOME/work/$key" ]; then
    if [ -e "$HOME/work/$key/.git" ] \
       && git -C "$REPO_CANON" worktree remove --force "$HOME/work/$key" >/dev/null 2>&1; then
      cleaned="$cleaned worktree"
    else
      rm -rf "$HOME/work/$key" 2>/dev/null && cleaned="$cleaned dir"
    fi
    git -C "$REPO_CANON" worktree prune >/dev/null 2>&1
  fi
  [ -n "$cleaned" ] && echo "  cleaned: $key ($cleaned)"
}

reap_key() {  # $1=key — window + artifacts.
  win_exists "$1" && tmux kill-window -t "ops:$1" 2>/dev/null && echo "  killed window: $1"
  clean_artifacts "$1"
}

data="$(kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null | jq -c '.data // {}' 2>/dev/null)" || data='{}'
[ -n "$data" ] || data='{}'
now="$(date -u +%s)"

status_of() { printf '%s' "$data" | jq -r --arg k "$1" '(.[$k] // "{}") | (fromjson? // {}) | .status // "?"'; }

case "${1:-list}" in
  list)
    echo "orders in $NS/$CM:"
    printf '%s' "$data" | jq -r --argjson now "$now" '
        to_entries[] | (.value|fromjson? // {}) as $o
        | [.key, ($o.status // "?"), ($o.class // "?"),
           ((($now - (($o.updated // "0")|tonumber? // 0)) / 3600) | floor | tostring) + "h"]
        | @tsv' 2>/dev/null \
      | while IFS=$'\t' read -r key status class age; do
          w="-"; win_exists "$key" && w="window"
          a=""
          [ -f "$ORDERS_DIR/$key.json" ] && a="json"
          [ -f "$ORDERS_DIR/$key.log" ] && a="$a log"
          [ -d "$HOME/work/$key" ] && a="$a worktree"
          printf '  %-28s %-8s %-16s age=%-6s %-8s artifacts:%s\n' "$key" "$status" "$class" "$age" "$w" "${a:-none}"
        done
    echo "windows without a CM entry (stray — clean by hand if unexpected):"
    tmux list-windows -t ops -F '#W' 2>/dev/null | grep -E '^(wo|esc|rem)-' | while read -r w; do
      printf '%s' "$data" | jq -e --arg k "$w" '.[$k] // empty' >/dev/null 2>&1 || echo "  $w"
    done
    ;;
  clean)
    target="${2:?usage: ops-reap.sh clean <key|all>}"
    if [ "$target" = "all" ]; then
      for key in $(printf '%s' "$data" | jq -r '
          to_entries[] | select((.value|fromjson? // {}) as $o
            | $o.status=="done" or $o.status=="failed" or $o.status=="escalated") | .key' 2>/dev/null); do
        reap_key "$key"
      done
      echo "done — finished orders reaped (pending/claimed untouched)."
    else
      st="$(status_of "$target")"
      case "$st" in
        done|failed|escalated) reap_key "$target"; echo "done." ;;
        pending|claimed) echo "REFUSED: $target is $st — close it via order-status.sh first." >&2; exit 1 ;;
        *) echo "no CM entry for $target — cleaning window/artifacts only."; reap_key "$target" ;;
      esac
    fi
    ;;
  *)
    echo "usage: ops-reap.sh [list | clean <key|all>]" >&2; exit 2 ;;
esac
