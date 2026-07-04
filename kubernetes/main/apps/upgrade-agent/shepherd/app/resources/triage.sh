#!/usr/bin/env bash
# upgrade-shepherd-triage — the Phase-4b.3 auto-summon. DETERMINISTIC, NO LLM on the
# common path: runs on a schedule and summons the LLM (MODE=remediate) ONLY when a real
# regression correlates with a recent merge to main. On a healthy cluster it costs
# nothing (a few kubectl/curl calls, then exit 0).
#
# WHY it lives here and not in the gate: the health-gate is deliberately read-only +
# GitHub-less + Pushover-only (an independent tripwire) and CANNOT create work. The
# summon therefore runs under the SHEPHERD's identity (bot token + LLM key + the
# spend-guarded run-shepherd.sh). This is a SEPARATE, decoupled check — it does NOT read
# or depend on the gate; the gate keeps paging independently.
#
# CONTAINMENT: same as the shepherd — read-only cluster SA (+ the spend ConfigMap Role),
# egress CNP (GitHub/Anthropic/cluster-read), bot token minted by the initContainer to
# /creds (PEM never here), the monthly spend guard inside run-shepherd.sh.
set -uo pipefail

PROM="${PROMETHEUS_URL:-http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090}"
LOOKBACK_HOURS="${TRIAGE_MERGE_LOOKBACK_HOURS:-3}"
REPO="${REPO:-thaynes43/haynes-ops}"
NOW="$(date -u +%s)"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

# ── 1. Was there a merge to main recently? (Flux applies within ~30m; a bad upgrade
#       surfaces within ~1h.) No recent merge => nothing an upgrade could have broken. ──
GH_TOKEN="$(cat /creds/gh_token 2>/dev/null || true)"
[ -n "$GH_TOKEN" ] || { log "FATAL: /creds/gh_token missing (mint failed) — cannot check merges."; exit 1; }
SINCE="$(date -u -d "-${LOOKBACK_HOURS} hours" +%FT%TZ 2>/dev/null || date -u +%FT%TZ)"
recent="$(curl -sf -H "Authorization: Bearer ${GH_TOKEN}" -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${REPO}/commits?sha=main&since=${SINCE}&per_page=10" 2>/dev/null | jq 'length' 2>/dev/null)"
if ! [ "${recent:-0}" -gt 0 ] 2>/dev/null; then
  log "no merge to main in the last ${LOOKBACK_HOURS}h — nothing to triage. exit."
  exit 0
fi
log "detected ${recent} commit(s) on main in the last ${LOOKBACK_HOURS}h — checking for a regression."

# ── 2. Is there a regression NOW? (deterministic subset of the gate; its own check) ──
REG=""
# Flux Kustomizations/HelmReleases NotReady past a settle window (>10m).
notready="$(kubectl get kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io -A -o json 2>/dev/null \
  | jq -r --argjson now "$NOW" '.items[] | . as $i
      | (.status.conditions[]? | select(.type=="Ready" and .status!="True")) as $c
      | ($c.lastTransitionTime | sub("\\.[0-9]+";"") | fromdateiso8601) as $t
      | select(($now - $t) > 600)
      | "\(.kind)/\($i.metadata.namespace)/\($i.metadata.name)"' 2>/dev/null | head -5 | tr '\n' ' ')"
[ -n "$notready" ] && REG="Flux NotReady>10m: ${notready}"
# Firing severity=critical alerts (already 'for:'-debounced by the rules).
crit="$(curl -sf --max-time 15 "$PROM/api/v1/query" --data-urlencode 'query=ALERTS{alertstate="firing",severity="critical"}' 2>/dev/null \
  | jq -r '[.data.result[].metric.alertname] | unique | join(",")' 2>/dev/null)"
[ -n "$crit" ] && REG="${REG:+$REG; }Critical alerts: ${crit}"
# Pods crashlooping/failed now AND 10m ago (persisted past a cycle).
# PromQL gotcha (bit us 2026-07-04): `offset` must directly follow a SELECTOR —
# `(expr) offset 10m` is a parse error → Prometheus 400 → `curl -sf` empty → the
# check silently read as healthy. Keep offset per-selector; log if the query fails.
w='kube_pod_container_status_waiting_reason{reason=~"CrashLoopBackOff|ImagePullBackOff|CreateContainerError"}'
p='kube_pod_status_phase{phase=~"Failed|Unknown"}'
q="( (${w} == 1) or (${p} == 1) ) and ( (${w} offset 10m == 1) or (${p} offset 10m == 1) )"
pods_json="$(curl -sf --max-time 15 "$PROM/api/v1/query" --data-urlencode "query=$q" 2>/dev/null)"
if [ -z "$pods_json" ]; then
  log "WARN: persisted-pods query failed (parse/timeout) — pods dimension blind this run."
else
  pods="$(echo "$pods_json" | jq -r '[.data.result[].metric.pod] | unique | join(",")' 2>/dev/null)"
  [ -n "$pods" ] && REG="${REG:+$REG; }Pods unhealthy (persisted): ${pods}"
fi

if [ -z "$REG" ]; then
  log "recent merge but cluster is healthy — no regression. exit."
  exit 0
fi

# ── 3. Regression correlated with a recent merge => summon the LLM remediate mode.
#       run-shepherd.sh enforces the monthly spend guard + the merge-less tool allowlist. ──
log "REGRESSION correlated with a recent merge: ${REG} — summoning MODE=remediate."
export UPGRADE_AGENT_MODE=remediate
export UPGRADE_AGENT_PROMPT="A regression appeared after a merge to main within the last ${LOOKBACK_HOURS}h. Detected signals: ${REG}. Diagnose (read-only) and remediate per .agents/runbooks/upgrade-shepherd.md Mode 2 — forward-fix or git revert/re-pin the culprit bump and enable auto-merge; if git alone cannot converge (immutable field, wedged HelmRelease, stuck finalizer, one-way major), record a hold if applicable and STOP with a diagnosis for a human. Do NOT push to main; stay inside kubernetes/**."
exec /bin/bash /opt/shepherd/run-shepherd.sh
