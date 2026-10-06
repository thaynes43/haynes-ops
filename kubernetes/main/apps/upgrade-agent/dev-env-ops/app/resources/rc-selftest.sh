#!/usr/bin/env bash
# rc-selftest.sh [wait-seconds] — prove that a wo-*/esc-* style session on this
# pod REALLY registers with Remote Control on dev-env-ops' own Max login
# (haynes-ops#3414), WITHOUT filing a work order, sending a page, or touching a
# live session. Run it after every login ceremony:
#
#   kubectl -n upgrade-agent exec deploy/dev-env-ops -c app -- bash /opt/dev-env-ops/rc-selftest.sh
#
# It starts `claude --remote-control wo-rc-selftest-<stamp>` with NO prompt (no
# model turn) in its own tmux session `rcselftest` — never in `ops`, so the
# watcher, its lanes and its reap never see it — waits for the CLI's registry
# record to carry a bridgeSessionId (rc-state.sh, the same check the lanes use),
# prints the verdict, and ends the session. It never prints the session link.
#
# Side effect: one Remote Control entry on Tom's list, offline once this ends.
# It was never used, so the CLI archives it at the next Remote Control start
# from this home (R-02 §4, the placeholder sweep).
#
# exit 0 REGISTERED · 1 NOT registered · 2 nothing to test (no usable Max login)
set -uo pipefail
wait_s="${1:-90}"
name="wo-rc-selftest-$(date -u +%m%d-%H%M%S)"
tsess=rcselftest

lc="$(bash /opt/dev-env-ops/login-check.sh --days 0 --quiet 2>&1)"; lc_rc=$?
echo "$lc"
if [ "$lc_rc" != 0 ]; then
  echo "SELFTEST: no usable Max login on dev-env-ops — nothing to test. Run the login ceremony first (runbook agentic-remediation.md)."
  exit 2
fi

tmux kill-session -t "$tsess" 2>/dev/null
tmux new-session -d -s "$tsess" -x 220 -y 50 -c "$HOME" \
  "env -u CLAUDE_CODE_OAUTH_TOKEN -u ANTHROPIC_API_KEY claude --remote-control '$name'; sleep 30" \
  || { echo "SELFTEST: could not start tmux session $tsess"; exit 1; }

verdict=1; out=""
deadline=$(( $(date +%s) + wait_s ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  sleep 3
  out="$(bash /opt/dev-env-ops/rc-state.sh "$tsess" 2>/dev/null)" && { verdict=0; break; }
done

# Diagnostics from the pane, links masked (they open only for Tom, but they
# stay out of logs and chat all the same).
pane="$(tmux capture-pane -p -J -t "$tsess" 2>/dev/null \
  | grep -iE 'remote control|remote-control|login|rejected|subscription|organization' \
  | sed -E 's#https://[^ ]+#<link>#g' | tail -5)"
tmux kill-session -t "$tsess" 2>/dev/null

if [ "$verdict" = 0 ]; then
  echo "SELFTEST: REGISTERED — '$name' got a bridgeSessionId; wo-*/esc-* sessions will be on Tom's phone. (Session ended; its unused entry is archived at the next Remote Control start.)"
else
  echo "SELFTEST: NOT REGISTERED within ${wait_s}s (rc-state: ${out:-no-session})."
fi
[ -n "$pane" ] && printf 'pane:\n%s\n' "$pane"
exit "$verdict"
