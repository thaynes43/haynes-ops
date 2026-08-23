#!/usr/bin/env bash
# remediate.sh <source> <run-ref> <reason...> — file an AUTONOMOUS REMEDIATION
# order into the `upgrade-work-orders` ConfigMap (saga dev-env backlog 12/13,
# "autonomy first" policy 2026-08-23).
#
# THE POLICY THIS SERVES (Tom, verbatim): "The north star to any remediation or
# upgrades is to only page me if there is a problem… The agent should respond
# and evaluate / triage the issue… If the agent deems the issue to be real and
# can take action the agent should. If the agent fixes the issue NO ESCALATION
# or PAGE or RC SESSION is necessary."
#
# So this is escalate.sh's QUIET SIBLING. Same CM, same dedup discipline, same
# best-effort contract — but the order it files spawns a HEADLESS session
# (`claude -p`, no `--remote-control`) that never appears in Tom's Remote
# Control list and never pages. It pages only by PROMOTING itself to an esc-*
# escalation (order-status.sh <key> escalate), which is cases 3 and 4 of the
# decision table in .agents/runbooks/agentic-remediation.md.
#
#   escalate.sh  → esc-*  → RC session + page ON SPAWN   ("Tom is needed")
#   remediate.sh → rem-*  → headless, silent, digest only ("Tom is not needed")
#
# Ships as a third key of the `upgrade-coordination-lib` ConfigMap (health-gate
# kustomization), mounted at /opt/coordination in the gate, shepherd, triage and
# responder pods. EXECUTED, never sourced.
#
# KEY: rem-<source>-<sig8>, inside the CM-legal charset [-._a-zA-Z0-9]+ (the API
# server rejects the ENTIRE write otherwise — bit the responder live 2026-08-20).
# SIGNATURE: sha256 of $REMEDIATE_SIG when exported (the responder passes
# alertname@ns), else "<source>|<reason>". It must be STABLE across re-fires of
# the same condition — dedup keys on it.
#
# DEDUP / ANTI-LOOP:
#   pending|claimed|working → a session is already queued or running for exactly
#     this condition. Not refiled.
#   escalated → the agent already handed this condition to Tom and his session
#     may still be open. Not refiled for REM_ESCALATED_COOLDOWN_H (6h) — an
#     autonomous retry behind a human's back is how you get two actuators on one
#     incident. (The responder's own alertname cooldown normally gets here first;
#     this is the belt to that suspenders.)
#   done|failed → the condition came back after the last session closed out. A
#     fresh attempt IS warranted; the entry is overwritten.
#
# BEST-EFFORT BY CONTRACT: a write failure must NEVER break the primary run, and
# — critically — the CALLER MUST FAIL OPEN. If this script returns non-zero the
# caller has NOT handed the problem to anyone: it must fall back to whatever it
# would have done before (for the responder, that means page). Exits 0 on
# filed-or-already-queued, 1 on write/compute failure, 2 on bad arguments.
set -uo pipefail

CM=upgrade-work-orders
NS=upgrade-agent
MAX_REASON=400
MAX_DIAGNOSIS=1400
MAX_ALERT=900
ESCALATED_COOLDOWN_H="${REM_ESCALATED_COOLDOWN_H:-6}"

rlog() { printf 'remediate: %s %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

src="${1:-}"; run_ref="${2:-}"; shift 2 2>/dev/null || true
reason="${*:-no reason given}"

src="$(printf '%s' "$src" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9-' '-' | sed 's/^-*//; s/-*$//' | cut -c1-24)"
if [ -z "$src" ]; then
  rlog "REFUSED: empty/invalid <source> — nothing filed (caller must FAIL OPEN)."
  exit 2
fi

# Strip control characters. reason/diagnosis/alert originate partly from LLM
# output that read hostile input (alert annotations, release notes) — they are
# DATA, and the session prompt + ops-claude.md frame them so.
clean() { printf '%s' "$1" | tr -d '\000-\037' | cut -c1-"$2"; }
reason="$(clean "$reason" "$MAX_REASON")"
run_ref="$(clean "$run_ref" 120)"
diagnosis="$(clean "${REMEDIATE_DIAGNOSIS:-}" "$MAX_DIAGNOSIS")"
alert_ctx="$(clean "${REMEDIATE_ALERT:-}" "$MAX_ALERT")"

sig_src="${REMEDIATE_SIG:-${src}|${reason}}"
sig8="$(printf '%s' "$sig_src" \
  | { sha256sum 2>/dev/null || shasum -a 256 2>/dev/null || openssl dgst -sha256 2>/dev/null; } \
  | grep -oE '[0-9a-f]{64}' | head -1 | cut -c1-8)"
if [ -z "$sig8" ]; then
  rlog "REFUSED: could not compute a signature (no sha256 tool?) — nothing filed."
  exit 1
fi
key="rem-${src}-${sig8}"
now="$(date -u +%s)"

# NB: single-encoded value — jq -c object text, NO extra tojson. Double encoding
# makes the watcher's fromjson yield a string and the order sits pending forever
# (hit live on the wo-* synthetic test 2026-08-20).
entry="$(jq -nc --arg source "$src" --arg reason "$reason" --arg run_ref "$run_ref" \
  --arg diagnosis "$diagnosis" --arg alert "$alert_ctx" --arg sig "$sig_src" --arg now "$now" \
  '{source:$source, reason:$reason, run_ref:$run_ref, diagnosis:$diagnosis, alert:$alert,
    sig:$sig, class:"remediation", status:"pending", created:$now, updated:$now}')" || {
  rlog "FAILED to build the entry JSON — nothing filed."; exit 1; }

if kubectl -n "$NS" get cm "$CM" >/dev/null 2>&1; then
  prior="$(kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
    | jq -r --arg k "$key" '(.data[$k] // "{}") | (fromjson? // {})
        | "\(.status // "")|\(.updated // "0")"')"
  prior_status="${prior%%|*}"; prior_updated="${prior##*|}"
  case "$prior_status" in
    pending|claimed|working)
      rlog "$key already ${prior_status} — not refiling (a remediation for this condition is queued/running)."
      exit 0 ;;
    escalated)
      age=$(( now - ${prior_updated:-0} ))
      if [ "$age" -lt $(( ESCALATED_COOLDOWN_H * 3600 )) ]; then
        rlog "$key was ESCALATED ${age}s ago (<${ESCALATED_COOLDOWN_H}h) — not refiling; a human owns this condition."
        exit 0
      fi ;;
  esac
  kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
    | jq --arg k "$key" --arg v "$entry" '.data[$k] = $v' \
    | kubectl -n "$NS" replace -f - >/dev/null 2>&1 \
    || { rlog "FAILED to write $CM/$key (RBAC/conflict?) — NOT filed; caller must FAIL OPEN."; exit 1; }
else
  kubectl -n "$NS" create configmap "$CM" --from-literal="$key=$entry" >/dev/null 2>&1 \
    || { rlog "FAILED to create $CM (RBAC?) — NOT filed; caller must FAIL OPEN."; exit 1; }
fi
rlog "$key filed (source=$src sig=$sig_src) — dev-env-ops will run a SILENT headless remediation; no page unless it escalates."
exit 0
