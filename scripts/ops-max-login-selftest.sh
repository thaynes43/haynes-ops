#!/usr/bin/env bash
# Self-test for dev-env-ops' own Max login wiring (haynes-ops#3414).
#
# WHY THIS EXISTS
# ---------------
# From 2026-09-03 to 2026-10-05 every wo-*/esc-* session in dev-env-ops was
# launched "with Remote Control" and none registered: the credential was the
# setup token, the pages promised a phone session anyway, and the only probe
# tested inference. Each piece of the fix fails SILENTLY if it rots:
#   - login-check.sh misreads the expiry  -> no 7-day page, or a nag forever
#   - session-launch.sh picks the wrong credential for a lane
#                                         -> a mute session, or curation on Tom's phone daily
#   - rc-state.sh matches the wrong record -> a link to a session that is not there
# so every decision is pinned below.
#
# Rule, same as scripts/work-order-lane-selftest.sh: the SHIPPED scripts run,
# never copies of their logic. Only the outside world is stubbed: `claude`,
# `kubectl` and `curl` on PATH, a private tmux server, and /opt/dev-env-ops
# rewritten to a scratch dir.
#
# Usage: scripts/ops-max-login-selftest.sh
# Requires: bash 4+, jq, tmux. No cluster access, no network, no real claude.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RES="$REPO_ROOT/kubernetes/main/apps/upgrade-agent/dev-env-ops/app/resources"
T="$(mktemp -d)"
cleanup() { TMUX_TMPDIR="$T/tmuxdir" tmux kill-server 2>/dev/null; rm -rf "$T"; }
trap cleanup EXIT

pass=0; fail=0
ok()  { pass=$((pass + 1)); printf 'ok   %s\n' "$1"; }
bad() { fail=$((fail + 1)); printf 'FAIL %s\n' "$1"; [ -n "${2:-}" ] && printf '     got: %s\n' "$2"; }
expect_rc()       { [ "$2" = "$3" ] && ok "$1" || bad "$1 (want rc $3)" "rc $2"; }
expect_contains() { case "$2" in *"$3"*) ok "$1" ;; *) bad "$1 (want '$3')" "$2" ;; esac; }
expect_absent()   { case "$2" in *"$3"*) bad "$1 (must not contain '$3')" "$2" ;; *) ok "$1" ;; esac; }

mkdir -p "$T/opt" "$T/bin" "$T/home/work/orders" "$T/cfg/sessions" "$T/tmuxdir"
for f in session-launch.sh login-check.sh ops-log.sh order-status.sh rc-state.sh; do
  sed -e "s#/opt/dev-env-ops#$T/opt#g" -e "s#> /proc/1/fd/1#>> $T/events.log#g" \
      -e 's#^exec bash$#exit 0#' "$RES/$f" > "$T/opt/$f"
done
chmod +x "$T/opt/"*.sh

now_ms=$(( $(date +%s) * 1000 ))
creds() {  # $1 = days of refresh-token life left (may be negative), or "none" / "synth"
  rm -f "$T/cfg/.credentials.json"
  case "$1" in
    none) ;;
    synth) jq -n '{claudeAiOauth:{accessToken:"setup-tok", expiresAt: 1, scopes:["user:inference","user:profile"], subscriptionType:"max"}}' \
             > "$T/cfg/.credentials.json" ;;
    *) jq -n --argjson e "$(( now_ms + $1 * 86400000 ))" \
         '{claudeAiOauth:{accessToken:"ACCESS-SECRET", refreshToken:"REFRESH-SECRET", expiresAt: 1, refreshTokenExpiresAt:$e, subscriptionType:"max"}}' \
         > "$T/cfg/.credentials.json" ;;
  esac
}

# ── 1. login-check.sh: the exit contract the watcher and the lanes rely on ──
lc() { CLAUDE_CONFIG_DIR="$T/cfg" bash "$T/opt/login-check.sh" "$@" >"$T/lc.out" 2>&1; echo $?; }
creds none;  expect_rc "login-check: no file -> 2 (no Max login)" "$(lc --quiet)" 2
creds synth; expect_rc "login-check: pre-#3414 synthesized file -> 2" "$(lc --quiet)" 2
creds 20;    expect_rc "login-check: 20 days left -> 0" "$(lc --quiet)" 0
expect_contains "login-check: names THIS pod's login" "$(cat "$T/lc.out")" "dev-env-ops Max login"
creds 3;     expect_rc "login-check: 3 days left -> 1 (page)" "$(lc --quiet)" 1
expect_rc "login-check --days 0: 3 days left is still usable -> 0" "$(lc --days 0 --quiet)" 0
creds -1;    expect_rc "login-check --days 0: expired -> 1" "$(lc --days 0 --quiet)" 1
expect_contains "login-check: expired says so" "$(cat "$T/lc.out")" "EXPIRED"
expect_absent "login-check: never prints token material" "$(creds 20; lc >/dev/null; cat "$T/lc.out")" "SECRET"

# ── 2. session-launch.sh: which credential, and whether --remote-control ─────
cat > "$T/bin/claude" <<'EOF'
#!/usr/bin/env bash
case " $* " in *" -p "*)
  if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ "${STUB_MAX_REJECT:-0}" = 1 ]; then
    echo "API Error: 401 OAuth token has been revoked"; exit 1; fi
  echo ok; exit 0 ;; esac
echo "LAUNCH env_token=${CLAUDE_CODE_OAUTH_TOKEN:+set} api_key=${ANTHROPIC_API_KEY:+set} args=$*" >> "$STUB_REC"
EOF
cat > "$T/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
case "$*" in *"get cm"*) jq -nc --arg k "$STUB_KEY" '{data:{($k):"{\"status\":\"done\"}"}}' ;; *) cat >/dev/null ;; esac
EOF
cat > "$T/bin/curl" <<'EOF'
#!/usr/bin/env bash
for a; do case "$a" in title=*|message=*) echo "PAGE $a" >> "$STUB_REC" ;; esac; done
EOF
# rc-state.sh's tmux/registry half is tested for real in section 3; here the
# lanes only need a registered/unregistered answer.
cat > "$T/opt/rc-state.sh" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = --join ]; then
  if [ "${STUB_RC:-}" = registered ]; then echo "Join from the phone: Remote Control '$2' — https://claude.ai/code/session_STUB"
  else echo "Remote Control is NOT available for this session ($(cat "$HOME/work/orders/$2.rc-why" 2>/dev/null))"; fi
  exit 0
fi
[ "${STUB_RC:-}" = registered ] && { echo "registered https://claude.ai/code/session_STUB"; exit 0; }
echo unregistered; exit 1
EOF
chmod +x "$T/bin/"* "$T/opt/rc-state.sh"

launch() {  # $1 key  $2 class  $3 creds  $4 max-login-rejected(0|1)  $5 rc-state
  creds "$3"; rm -f "$T/home/work/orders/"*; : > "$T/rec"
  jq -nc --arg c "$2" '{class:$c, source:"selftest", reason:"selftest order"}' > "$T/home/work/orders/$1.json"
  env -i PATH="$T/bin:/usr/bin:/bin" HOME="$T/home" CLAUDE_CONFIG_DIR="$T/cfg" \
    CLAUDE_CODE_OAUTH_TOKEN=setup-tok ANTHROPIC_API_KEY=metered PUSHOVER_TOKEN=t PUSHOVER_USER_KEY=u \
    OPS_RC_CONFIRM_SECONDS=6 STUB_REC="$T/rec" STUB_KEY="$1" STUB_MAX_REJECT="$4" STUB_RC="$5" \
    bash "$T/opt/session-launch.sh" "$1" claude-opus-5-5 xhigh </dev/null >/dev/null 2>&1
  # The launcher has returned; its background rc_confirm (same command line)
  # polls every 5 s until OPS_RC_CONFIRM_SECONDS, then pages. Wait for it.
  local i=0
  while pgrep -f "$T/opt/session-launch.sh $1 " >/dev/null 2>&1 && [ "$i" -lt 30 ]; do
    sleep 1; i=$((i + 1))
  done
  cat "$T/rec"
}

out="$(launch esc-selftest-aaaa1111 other 20 0 registered)"
expect_contains "esc + valid login: launches on the Max login (env token stripped)" "$out" "LAUNCH env_token= api_key= args=--remote-control esc-selftest-aaaa1111"
expect_contains "esc + registered: the page carries the phone link" "$out" "https://claude.ai/code/session_STUB"
expect_rc "esc: exactly one page" "$(grep -c '^PAGE title=' <<<"$out")" 1

out="$(launch esc-selftest-bbbb2222 other none 0 unregistered)"
expect_contains "esc + no login: falls back to the setup token" "$out" "LAUNCH env_token=set"
expect_absent "esc + no login: no --remote-control" "$out" "--remote-control"
expect_contains "esc + no login: the page says RC is NOT available and why" "$out" "NOT available for this session (dev-env-ops has no Max login"

out="$(launch esc-selftest-cccc3333 other -1 0 unregistered)"
expect_contains "esc + expired login: setup token, and the page says EXPIRED" "$out" "Max login has EXPIRED"
expect_absent "esc + expired login: no --remote-control" "$out" "--remote-control"

out="$(launch esc-selftest-dddd4444 other 20 1 unregistered)"
expect_contains "esc + login the API rejects: relaunches on the setup token" "$out" "LAUNCH env_token=set"
expect_contains "esc + login the API rejects: the page says REJECTED" "$out" "REJECTED by the API"

expect_contains "esc + no login: audit says rc_state=off, not unregistered" "$(grep 'key=esc-selftest-bbbb2222' "$T/events.log" | grep paged=esc)" "rc_state=off"

out="$(launch esc-selftest-eeee5555 other 20 0 unregistered)"
expect_contains "esc + login ok but no registration: page says NOT available" "$out" "NOT available for this session (Remote Control did not register"
expect_absent "esc + no registration: no link in the page" "$out" "claude.ai/code/"
expect_contains "esc + no registration: audit says rc_state=unregistered" "$(grep 'key=esc-selftest-eeee5555' "$T/events.log" | grep paged=esc)" "rc_state=unregistered"

out="$(launch wo-3999 other 20 0 registered)"
expect_contains "shepherd wo-*: Remote Control on the Max login" "$out" "LAUNCH env_token= api_key= args=--remote-control wo-3999"
expect_absent "shepherd wo-*: quiet on success (no page)" "$out" "PAGE"

out="$(launch wo-cigar-curate-20990101 curation 20 0 registered)"
expect_contains "curation: setup token even with a valid Max login" "$out" "LAUNCH env_token=set api_key= "
expect_absent "curation: never registers Remote Control" "$out" "--remote-control"
expect_absent "curation: no page" "$out" "PAGE"

# ── 3. rc-state.sh against a private tmux server and a fake CLI registry ─────
cp "$RES/rc-state.sh" "$T/rc-state.real.sh"
export TMUX_TMPDIR="$T/tmuxdir"; unset TMUX TMUX_PANE
rs() { HOME="$T/home" CLAUDE_CONFIG_DIR="$T/cfg" bash "$T/rc-state.real.sh" "$@"; }
tmux new-session -d -s ops -n bash "sleep 300"
tmux new-window -d -t ops -n esc-selftest-ffff6666 "sleep 300"
wp="$(tmux list-panes -t 'ops:=esc-selftest-ffff6666' -F '#{session_name}:#{window_id}.#{pane_id}')"
pid="$(tmux list-panes -t 'ops:=esc-selftest-ffff6666' -F '#{pane_pid}')"
pstart="$(sed 's/^.*) //' "/proc/$pid/stat" | cut -d' ' -f20)"
rec() {  # $1 = bridgeSessionId or "null"   $2 = procStart
  jq -n --argjson pid "$pid" --arg ps "$2" --arg wp "$wp" --argjson b "$1" --argjson t "$now_ms" \
    '{pid:$pid, procStart:$ps, tmux:$wp, kind:"interactive", name:"dev-ab", startedAt:$t, bridgeSessionId:$b}' \
    > "$T/cfg/sessions/$pid.json"
}
rec '"session_SELFTEST"' "$pstart"
expect_contains "rc-state: live pane with bridgeSessionId -> registered + link" "$(rs 'ops:=esc-selftest-ffff6666')" "registered https://claude.ai/code/session_SELFTEST"
rec null "$pstart"
rs 'ops:=esc-selftest-ffff6666' >/dev/null; expect_rc "rc-state: live pane, no bridgeSessionId -> unregistered (1)" "$?" 1
rec '"session_SELFTEST"' "12345"
rs 'ops:=esc-selftest-ffff6666' >/dev/null; expect_rc "rc-state: recycled pid (procStart differs) -> no-session (2)" "$?" 2
rec '"session_SELFTEST"' "$pstart"
rs 'ops:=no-such-window' >/dev/null; expect_rc "rc-state: missing window never falls back to another pane (2)" "$?" 2
expect_contains "rc-state --join: registered -> phone link" "$(rs --join esc-selftest-ffff6666)" "Join from the phone: Remote Control 'esc-selftest-ffff6666'"
printf 'dev-env-ops has no Max login yet' > "$T/home/work/orders/esc-gone.rc-why"
out="$(rs --join esc-gone)"
expect_contains "rc-state --join: gone -> NOT available, with the recorded reason" "$out" "NOT available for this session (dev-env-ops has no Max login yet)"
expect_contains "rc-state --join: gone -> attach command" "$out" "tmux attach -t 'ops:esc-gone'"

echo
echo "ops-max-login selftest: $pass passed, $fail failed"
[ "$fail" = 0 ]
