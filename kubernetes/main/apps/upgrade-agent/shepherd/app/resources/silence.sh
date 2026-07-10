#!/usr/bin/env bash
# silence.sh <component> [minutes] — set a BOUNDED Alertmanager silence for the
# EXPECTED rollout-transient alerts of a cluster-infra component the shepherd just
# auto-merged, so a normal reconcile/roll (OSD restart, mgr swap, disk-IO backfill,
# pod restart) does not page the human. Added 2026-07-08 after a rook auto-merge
# double-paged (CephOSDDownHigh + PrometheusRuleFailures) for its own normal rollout.
#
# CONTAINMENT (the shepherd LLM is prompt-injectable): the LLM passes ONLY a component
# NAME from the hardcoded case below — never matchers, never a target. The matcher set
# and the max duration are hardcoded HERE, so an injected agent cannot silence arbitrary
# alerts (e.g. it can NOT silence KubeAPIDown, Watchdog, a real critical, or all alerts)
# nor for an arbitrary time. Unknown component => no-op. Every silence is logged.
set -uo pipefail
AM="${ALERTMANAGER_URL:-http://kube-prometheus-stack-alertmanager.observability.svc.cluster.local:9093}"
COMP="${1:-}"
MINS="${2:-90}"
log(){ printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
[ -n "$COMP" ] || { log "silence.sh: usage: silence.sh <component> [minutes]"; exit 2; }

# Clamp duration to [10,120] minutes — a rollout window, never open-ended.
case "$MINS" in ''|*[!0-9]*) MINS=90;; esac
[ "$MINS" -lt 10 ]  && MINS=10
[ "$MINS" -gt 120 ] && MINS=120

# component -> HARDCODED matcher array. alertname regexes cover ONLY expected
# rollout-transient alerts; pod alerts are namespace-scoped so we never blanket-silence
# unrelated apps. NEVER add Watchdog / KubeAPIDown / KubeletDown / gate-blind / a
# standing critical here.
case "$COMP" in
  rook-ceph)
    # Ceph daemon churn + the mgr-restart PrometheusRuleFailures artifact + backfill IO.
    MATCHERS='[{"name":"alertname","value":"Ceph(OSD|Mon|Mgr|Daemon).*|PrometheusRuleFailures|NodeDiskIOSaturation","isRegex":true,"isEqual":true}]' ;;
  cnpg|dragonfly-operator)
    MATCHERS='[{"name":"alertname","value":"KubePod(NotReady|CrashLooping)","isRegex":true,"isEqual":true},{"name":"namespace","value":"database","isRegex":false,"isEqual":true}]' ;;
  authentik)
    MATCHERS='[{"name":"alertname","value":"KubePod(NotReady|CrashLooping)","isRegex":true,"isEqual":true},{"name":"namespace","value":"network","isRegex":false,"isEqual":true}]' ;;
  cilium)
    MATCHERS='[{"name":"alertname","value":"KubePod(NotReady|CrashLooping)","isRegex":true,"isEqual":true},{"name":"namespace","value":"kube-system","isRegex":false,"isEqual":true}]' ;;
  cert-manager)
    MATCHERS='[{"name":"alertname","value":"KubePod(NotReady|CrashLooping)","isRegex":true,"isEqual":true},{"name":"namespace","value":"cert-manager","isRegex":false,"isEqual":true}]' ;;
  *)
    log "silence.sh: '$COMP' has no rollout-transient set — no silence (fine for stateless clean-tier: coredns/traefik/multus/device-plugins/flux self-heal fast)."; exit 0 ;;
esac

START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
END="$(date -u -d "+${MINS} minutes" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" || { log "silence.sh: date math failed"; exit 1; }
BODY="$(jq -cn --argjson m "$MATCHERS" --arg s "$START" --arg e "$END" --arg c "$COMP" \
  '{matchers:$m, startsAt:$s, endsAt:$e, createdBy:"upgrade-shepherd",
    comment:("auto: expected rollout-transient alerts during \($c) upgrade the shepherd just auto-merged; bounded \($e). Real regressions the health gate catches (Flux NotReady / HEALTH_ERR) are NOT in this set.")}' 2>/dev/null)"
[ -n "$BODY" ] || { log "silence.sh: could not build request body"; exit 1; }

RESP="$(curl -sf --max-time 15 -X POST "$AM/api/v2/silences" -H 'Content-Type: application/json' -d "$BODY" 2>/dev/null)" || {
  log "silence.sh: POST to Alertmanager failed (egress/AM down?) — proceeding WITHOUT a silence (the gate still pages; worst case is a transient page)."; exit 0; }
SID="$(printf '%s' "$RESP" | jq -r '.silenceID // .id // ""' 2>/dev/null)"
log "silence.sh: set ${MINS}m silence for '$COMP' rollout-transients (id=${SID:-?}) until $END."
