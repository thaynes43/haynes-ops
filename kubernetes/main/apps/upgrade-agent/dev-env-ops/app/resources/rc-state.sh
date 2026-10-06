#!/usr/bin/env bash
# rc-state.sh — did the claude session in a tmux pane REALLY register with
# Remote Control? (haynes-ops#3414: for a month every esc-* page promised a
# phone session that never existed — "Claude.ai login was rejected" — and
# nothing checked.) Read-only; never touches a session.
#
#   rc-state.sh <tmux-target>     e.g. 'ops:=esc-gate-1a2b3c4d' (= exact name), or a %pane id
#     prints  registered https://claude.ai/code/<bridgeSessionId>   exit 0
#             unregistered                                          exit 1
#             no-session                                            exit 2
#   rc-state.sh --join <key>
#     prints ONE sentence for a page: the phone link when the session in
#     window ops:<key> is registered, otherwise says plainly that Remote Control
#     is NOT available (and why, when session-launch recorded it) and gives the
#     attach command. Always exit 0.
#
# How (per thaynes43/dev-env R-02): claude keeps a registry record per live
# process in $CLAUDE_CONFIG_DIR/sessions/<pid>.json. A registered Remote Control
# session carries `bridgeSessionId` (session_…); its link is
# https://claude.ai/code/<bridgeSessionId>. The record's `name` is derived from
# the cwd, NOT the --remote-control name, so match on the record's `tmux` field
# (<session>:<window_id>.<pane_id>) instead, and prove the pid is the same live
# process (procStart = /proc/<pid>/stat starttime) — records from before a pod
# restart can carry a recycled pid and a recycled pane id.
set -uo pipefail
SESS_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions"
ORDERS_DIR="${HOME}/work/orders"

attach_cmd() { printf "kubectl -n upgrade-agent exec -it deploy/dev-env-ops -c app -- tmux attach -t 'ops:%s'" "$1"; }

state() {  # $1 = tmux target
  local wp best="" best_t=0 f rec pid ps t st
  # list-panes, NOT display-message: display-message silently falls back to the
  # session's CURRENT pane when the target window does not exist, which would
  # report another session's state. list-panes fails on a missing target.
  wp="$(tmux list-panes -t "$1" -F '#{session_name}:#{window_id}.#{pane_id}' 2>/dev/null \
        | awk -F. -v p="$1" 'p !~ /^%/ || $NF == p' | head -1)"
  [ -n "$wp" ] || { echo no-session; return 2; }
  for f in "$SESS_DIR"/*.json; do
    [ -e "$f" ] || continue
    rec="$(jq -r --arg wp "$wp" '
        select((.tmux // "") == $wp and (.kind // "interactive") == "interactive")
        | [(.pid // 0), (.procStart // "0"), (.startedAt // 0), (.bridgeSessionId // "-")] | @tsv' \
        "$f" 2>/dev/null)" || continue
    [ -n "$rec" ] || continue
    IFS=$'\t' read -r pid ps t b <<<"$rec"
    [ -r "/proc/$pid/stat" ] || continue
    st="$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | cut -d' ' -f20)"
    [ "$ps" != 0 ] && [ "$st" = "$ps" ] || continue
    if [ "${t%.*}" -ge "$best_t" ] 2>/dev/null; then best_t="${t%.*}"; best="$b"; fi
  done
  [ -n "$best" ] || { echo no-session; return 2; }
  if [ "$best" != "-" ]; then
    echo "registered https://claude.ai/code/${best}"; return 0
  fi
  echo unregistered; return 1
}

if [ "${1:-}" = "--join" ]; then
  key="${2:?usage: rc-state.sh --join <key>}"
  out="$(state "ops:=${key}")"   # =: exact window name, never a prefix match
  case "$out" in
    registered*)
      printf "Join from the phone: Remote Control '%s' — %s (or attach from a dev-env session: %s)\n" \
        "$key" "${out#registered }" "$(attach_cmd "$key")" ;;
    *)
      why="$(cat "$ORDERS_DIR/$key.rc-why" 2>/dev/null | head -c 300)"
      if [ -z "$why" ]; then
        case "$out" in
          unregistered) why="the session did not register" ;;
          *)            why="no live claude session in that window" ;;
        esac
      fi
      printf "Remote Control is NOT available for this session (%s), so it is NOT on your phone. Join from a dev-env session: %s (detach C-b d; never kill the window)\n" \
        "$why" "$(attach_cmd "$key")" ;;
  esac
  exit 0
fi

state "${1:?usage: rc-state.sh <tmux-target> | --join <key>}"
