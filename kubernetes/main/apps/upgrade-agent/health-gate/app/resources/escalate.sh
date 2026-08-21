#!/usr/bin/env bash
# escalate.sh <source> <run-ref> <reason...> — file a FAILURE ESCALATION into the
# `upgrade-work-orders` ConfigMap (saga dev-env backlog 12, revised into 13's
# executor). The dev-env-ops watcher turns the entry into a joinable Remote
# Control session (tmux window + `claude --remote-control <key>`, model fable,
# effort xhigh) and PAGES ON SPAWN with the session name — escalations ARE
# failures, so they invert the wo-* quiet-on-success contract.
#
# Ships as a second key of the `upgrade-coordination-lib` ConfigMap (health-gate
# kustomization) and is mounted at /opt/coordination in the gate, shepherd,
# triage, and responder pods. EXECUTED, never sourced.
#
# NAMING TAXONOMY (enforced here + in the watcher; Tom's requirement — the
# Remote Control list must sort cleanly on a phone):
#   haynes-ops-*  dev work in the dev-env pod (agent-run)   — not this script
#   wo-*          shepherd work orders (work-order.sh)       — not this script
#   esc-*         failure escalations: esc-<source>-<sig8>   — THIS script
# <source> is sanitized to lowercase [a-z0-9-]; <sig8> is 8 hex of a stable
# signature so the SAME failure dedups and DIFFERENT failures don't collide.
# The whole key stays inside the CM-legal charset [-._a-zA-Z0-9]+ (the API
# server rejects the entire write otherwise — bit the responder live 2026-08-20).
#
# SIGNATURE: sha256 of $ESCALATE_SIG when the caller exports it (triage/gate
# pass the coordination signature; the responder passes alertname@ns), else of
# "<source>|<reason>". Callers should prefer a signature that is STABLE across
# re-fires of the same failure — dedup keys on it.
#
# DEDUP: an existing esc entry for the same source+sig that is still
# pending/claimed is NOT refiled (the responder cooldown pattern — a session is
# already open or queued for exactly this failure). A done/failed entry IS
# overwritten: the failure came back after the last session closed out.
#
# BEST-EFFORT BY CONTRACT: an escalation write failure must NEVER break the
# primary run. This script traps its own errors, logs them, and exits 0/1/2
# without side effects; call sites do not gate on the rc (no caller runs -e).
set -uo pipefail

CM=upgrade-work-orders
NS=upgrade-agent
MAX_REASON=400

elog() { printf 'escalate: %s %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

src="${1:-}"; run_ref="${2:-}"; shift 2 2>/dev/null || true
reason="${*:-no reason given}"

# Sanitize the source into the key namespace (lowercase [a-z0-9-]).
src="$(printf '%s' "$src" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9-' '-' | sed 's/^-*//; s/-*$//' | cut -c1-24)"
if [ -z "$src" ]; then
  elog "REFUSED: empty/invalid <source> — nothing filed (best-effort; caller unaffected)."
  exit 2
fi

# Strip control characters (reason + run_ref originate partly from LLM output
# that read hostile input — they are DATA; the session prompt frames them so).
reason="$(printf '%s' "$reason" | tr -d '\000-\037' | cut -c1-"$MAX_REASON")"
run_ref="$(printf '%s' "$run_ref" | tr -d '\000-\037' | cut -c1-120)"

sig_src="${ESCALATE_SIG:-${src}|${reason}}"
sig8="$(printf '%s' "$sig_src" \
  | { sha256sum 2>/dev/null || shasum -a 256 2>/dev/null || openssl dgst -sha256 2>/dev/null; } \
  | grep -oE '[0-9a-f]{64}' | head -1 | cut -c1-8)"
if [ -z "$sig8" ]; then
  elog "REFUSED: could not compute a signature (no sha256 tool?) — nothing filed."
  exit 1
fi
key="esc-${src}-${sig8}"
now="$(date -u +%s)"

# NB: single-encoded value — jq -c object text, NO extra tojson (double encoding
# makes the watcher's fromjson yield a string and the entry sits pending forever;
# hit live on the wo-* synthetic test 2026-08-20).
entry="$(jq -nc --arg source "$src" --arg reason "$reason" --arg run_ref "$run_ref" --arg now "$now" \
  '{source:$source, reason:$reason, run_ref:$run_ref, class:"escalation",
    model:"fable", effort:"xhigh", status:"pending", created:$now, updated:$now}')" || {
  elog "FAILED to build the entry JSON — nothing filed."; exit 1; }

if kubectl -n "$NS" get cm "$CM" >/dev/null 2>&1; then
  existing="$(kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
    | jq -r --arg k "$key" '(.data[$k] // "{}") | (fromjson? // {}) | .status // empty')"
  case "$existing" in
    pending|claimed)
      elog "$key already ${existing} — not refiling (a session for this failure is already open/queued)."
      exit 0 ;;
  esac
  kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
    | jq --arg k "$key" --arg v "$entry" '.data[$k] = $v' \
    | kubectl -n "$NS" replace -f - >/dev/null 2>&1 \
    || { elog "FAILED to write $CM/$key (RBAC/conflict?) — escalation NOT filed; primary run unaffected."; exit 1; }
else
  # The gate's Role is name-scoped get+update (no create) — creation is expected
  # to fail there; the shepherd/responder namespace Roles can create. Best-effort.
  kubectl -n "$NS" create configmap "$CM" --from-literal="$key=$entry" >/dev/null 2>&1 \
    || { elog "FAILED to create $CM (RBAC?) — escalation NOT filed; primary run unaffected."; exit 1; }
fi
elog "$key filed (source=$src) — dev-env-ops will spawn a joinable fable/xhigh session and PAGE with this name."
exit 0
