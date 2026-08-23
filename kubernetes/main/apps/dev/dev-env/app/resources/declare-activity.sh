#!/usr/bin/env bash
# declare-activity — tell the remediation agent "this is me, not a fault".
#
# Tom, 2026-08-23: an autonomous remediation agent must "check dev-env and even
# interact with agents working there to not step on any toes if it was a false
# escalation triggered by dev work (need a way for dev-env agents to try and
# avoid false escalations)."
#
# This is that way. Before you do something in the cluster that can trip a
# critical alert — restarting a stateful app, suspending Flux, draining a node,
# deleting pods, rolling storage — you DECLARE it. A declaration is a scoped,
# TTL'd note on this pod's PVC; the dev-env-ops remediation session reads it
# (via `kubectl -n dev exec`) as EVIDENCE while triaging an alert.
#
#   declare-activity start "<what you are doing>" --scope <a,b,c> [--ttl 45m]
#   declare-activity end <id>          # ALWAYS end early when you finish
#   declare-activity list              # what is currently declared
#   declare-activity prune             # drop expired records (automatic anyway)
#
# WHAT IT IS NOT: it is not a mute. The remediation agent still investigates and
# still escalates if something is genuinely broken — a declaration only lets it
# conclude "expected, dev-caused" instead of "novel fault", and only when the
# alert is actually consistent with your declared work. Nothing you write here
# can silence a real incident, and nothing here suppresses an escalation when a
# human is needed. Lying to it just wastes an agent's time and yours.
#
# SCOPE IS MANDATORY, TTLs ARE CAPPED. A blanket, permanent suppression is the
# failure mode this design exists to avoid, so:
#   * --scope is required; use the alert's dimensions — namespace(s), app
#     name(s), node name(s). e.g. --scope downloads,qbittorrent,talosw01
#   * --ttl defaults to 45m and is capped at 8h
#   * --scope cluster (the wildcard) is permitted but capped at 2h, because a
#     cluster-wide "ignore everything" is exactly what must never linger
#
# Records live at ~/.local/state/dev-activity/<id>.json — PVC-local, no cluster
# write needed (the dev-env SA has no ConfigMap write anywhere: verified
# 2026-08-23, `kubectl auth can-i create configmaps -n dev` → no).
set -uo pipefail

DIR="${DEV_ACTIVITY_DIR:-$HOME/.local/state/dev-activity}"
DEFAULT_TTL_MIN=45
MAX_TTL_MIN=480          # 8h
CLUSTER_MAX_TTL_MIN=120  # 2h — the wildcard gets a short leash
mkdir -p "$DIR" 2>/dev/null

now="$(date -u +%s)"

# Expired records are dead weight and a liability (a stale declaration is how a
# real fault gets waved off), so every invocation prunes.
prune() {
  local n=0 f
  for f in "$DIR"/*.json; do
    [ -e "$f" ] || continue
    exp="$(jq -r '.expires // 0' "$f" 2>/dev/null)"
    if [ "${exp:-0}" -le "$now" ] 2>/dev/null; then rm -f "$f" && n=$((n+1)); fi
  done
  [ "$n" -gt 0 ] && echo "pruned $n expired declaration(s)"
  return 0
}

to_minutes() {  # 45 | 45m | 2h -> minutes
  case "$1" in
    *h) echo $(( ${1%h} * 60 )) ;;
    *m) echo "${1%m}" ;;
    *)  echo "$1" ;;
  esac
}

usage() {
  sed -n '15,33p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-2}"
}

cmd="${1:-list}"; shift 2>/dev/null || true

case "$cmd" in
  start)
    what="${1:-}"; shift 2>/dev/null || true
    scope=""; ttl=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --scope) scope="${2:-}"; shift 2 ;;
        --ttl)   ttl="${2:-}"; shift 2 ;;
        *) echo "unknown flag: $1" >&2; usage ;;
      esac
    done
    [ -n "$what" ]  || { echo "ERROR: describe what you are doing." >&2; usage; }
    [ -n "$scope" ] || { echo "ERROR: --scope is REQUIRED (namespaces/apps/nodes the work can disturb, comma-separated). An unscoped declaration would be a blanket mute, which this tool deliberately cannot express. Use --scope cluster only if you truly mean estate-wide." >&2; exit 2; }

    ttl_min="$(to_minutes "${ttl:-${DEFAULT_TTL_MIN}m}")"
    case "$ttl_min" in ''|*[!0-9]*) echo "ERROR: --ttl must look like 45m or 2h." >&2; exit 2 ;; esac
    [ "$ttl_min" -gt 0 ] || { echo "ERROR: --ttl must be positive." >&2; exit 2; }

    scope_json="$(printf '%s' "$scope" | tr ',' '\n' \
      | jq -Rc 'ascii_downcase | gsub("^\\s+|\\s+$";"") | select(length>0)' | jq -sc .)"
    if printf '%s' "$scope_json" | jq -e 'any(.[]; . == "cluster" or . == "*")' >/dev/null 2>&1; then
      if [ "$ttl_min" -gt "$CLUSTER_MAX_TTL_MIN" ]; then
        echo "NOTE: cluster-wide scope is capped at ${CLUSTER_MAX_TTL_MIN}m (asked for ${ttl_min}m)."
        ttl_min="$CLUSTER_MAX_TTL_MIN"
      fi
      echo "NOTE: cluster-wide scope declared — prefer naming the namespaces/apps you actually touch."
    elif [ "$ttl_min" -gt "$MAX_TTL_MIN" ]; then
      echo "NOTE: ttl capped at ${MAX_TTL_MIN}m (asked for ${ttl_min}m)."
      ttl_min="$MAX_TTL_MIN"
    fi

    id="act-$(date -u +%H%M%S)-$$"
    who="${DEV_ACTIVITY_WHO:-$(whoami 2>/dev/null || echo agent)}"
    sess="$(tmux display-message -p '#S' 2>/dev/null || echo '-')"
    expires=$(( now + ttl_min * 60 ))

    jq -nc --arg id "$id" --arg who "$who" --arg what "$(printf '%s' "$what" | tr -d '\000-\037' | cut -c1-300)" \
       --argjson scope "$scope_json" --arg sess "$sess" \
       --argjson started "$now" --argjson expires "$expires" --arg ttl "${ttl_min}m" \
       '{id:$id, who:$who, session:$sess, what:$what, scope:$scope,
         started:($started|tostring), expires:($expires|tostring), ttl:$ttl}' \
      > "$DIR/$id.json" || { echo "ERROR: could not write the declaration." >&2; exit 1; }

    prune >/dev/null
    echo "declared $id — scope=[$(printf '%s' "$scope_json" | jq -r 'join(",")')] ttl=${ttl_min}m (expires $(date -u -d "@$expires" +%FT%TZ 2>/dev/null || echo "in ${ttl_min}m"))"
    echo "END IT EARLY when you finish:  declare-activity end $id"
    ;;

  end)
    id="${1:-}"
    [ -n "$id" ] || { echo "ERROR: declare-activity end <id>" >&2; exit 2; }
    if [ "$id" = "all" ]; then
      rm -f "$DIR"/*.json 2>/dev/null; echo "ended all declarations."
    elif [ -f "$DIR/$id.json" ]; then
      rm -f "$DIR/$id.json"; echo "ended $id."
    else
      echo "no such declaration: $id (already ended or expired)."   # idempotent
    fi
    prune >/dev/null
    ;;

  list)
    prune >/dev/null
    found=0
    for f in "$DIR"/*.json; do
      [ -e "$f" ] || continue
      found=1
      jq -r --argjson now "$now" '"  \(.id)  by=\(.who)  scope=[\((.scope // [])|join(","))]  left=\((((.expires|tonumber) - $now)/60)|floor)m\n      \(.what)"' "$f" 2>/dev/null
    done
    [ "$found" = 1 ] || echo "  (nothing declared)"
    ;;

  prune) prune; echo "ok" ;;
  -h|--help|help) usage 0 ;;
  *) echo "unknown command: $cmd" >&2; usage ;;
esac
