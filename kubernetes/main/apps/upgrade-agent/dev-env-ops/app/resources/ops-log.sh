#!/usr/bin/env bash
# ops-log.sh <event> <key> [k=v ...] — the dev-env-ops AUDIT TRAIL.
#
# WHY THIS EXISTS: sessions run inside tmux windows, so their stdout goes to a
# tmux pane, NOT to the pod's stdout — which means promtail never sees it and
# Loki has no record that a session ever decided anything. Before this, the pod
# emitted ~41 lines in 32h and ZERO esc-* traces: the executor's behaviour was
# effectively unfalsifiable ("it was tested, honest"). Every meaningful decision
# now writes one greppable line to PID 1's stdout, which IS the pod's stdout.
#
# Query it:
#   {namespace="upgrade-agent", pod=~"dev-env-ops.*"} |= "ops-event:"
#   {namespace="upgrade-agent", pod=~"dev-env-ops.*"} |= "ops-event:" |= "rem-"
#
# LINE FORMAT (logfmt-ish, one line, stable field order first):
#   ops-event: <utc> event=<event> key=<key> lane=<wo|esc|rem|-> [k=v ...]
# Values are control-char-stripped and quoted when they contain spaces, so a
# multi-line LLM note can never forge a second event line.
#
# EVENT VOCABULARY (keep this list and the runbook in sync):
#   filed        an order appeared in the CM (writer side; logged by the watcher)
#   claimed      the watcher took the order and is about to spawn
#   spawned      a session process started              (attrs: mode, model, effort)
#   triage       the session finished triaging          (attrs: verdict)
#   dev-activity the dev-env awareness check ran        (attrs: hits, matched)
#   fix          the session performed a fix action     (attrs: what)
#   verify       the session re-checked the condition   (attrs: result=pass|fail)
#   fixed        remediation succeeded AND verified     (silent close)
#   escalated    promoted to esc-* (a human is needed)  (attrs: esc, why)
#   closed       terminal status recorded               (attrs: status)
#   digest       the quiet summary channel flushed      (attrs: n, channel)
#   reaped       window/artifacts cleaned               (attrs: why)
#   watchdog     a stale-lane guard fired               (attrs: what)
#
# BEST-EFFORT: logging must never break a session. Always exits 0.
set -uo pipefail

ev="${1:-unknown}"; key="${2:--}"; shift 2 2>/dev/null || true

san() { printf '%s' "${1:-}" | tr -d '\000-\037' | tr -s ' ' ' ' | cut -c1-400; }
ev="$(printf '%s' "$ev" | tr -cd 'a-z0-9-' | cut -c1-24)"; [ -n "$ev" ] || ev=unknown
key="$(printf '%s' "$key" | tr -cd 'a-zA-Z0-9._-' | cut -c1-64)"; [ -n "$key" ] || key='-'

lane='-'
case "$key" in wo-*) lane=wo ;; esc-*) lane=esc ;; rem-*) lane=rem ;; esac

line="ops-event: $(date -u +%FT%TZ) event=${ev} key=${key} lane=${lane}"
for kv in "$@"; do
  k="${kv%%=*}"; v="$(san "${kv#*=}")"
  k="$(printf '%s' "$k" | tr -cd 'a-z0-9_' | cut -c1-24)"
  [ -n "$k" ] || continue
  case "$v" in *" "*|"") line="${line} ${k}=\"${v}\"" ;; *) line="${line} ${k}=${v}" ;; esac
done

# PID 1 is the watcher loop; its stdout is the container's stdout (what promtail
# ships). Fall back to our own stderr if /proc/1/fd/1 is not writable — better a
# lost line than a failed session.
printf '%s\n' "$line" > /proc/1/fd/1 2>/dev/null || printf '%s\n' "$line" >&2
exit 0
