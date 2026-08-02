#!/usr/bin/env bash
# DNS A-record loss canary (2026-08-01, diagnostic — remove when solved).
#
# Hunts the chronic ~0.1% egress-fetch failures where Flux controllers end up
# dialing an IPv6-only address list ("dial tcp [2606:...]: network is
# unreachable", instant failure => the A answer was missing at that moment;
# see the 2026-07-30 dragonfly incident). WAN counters, CoreDNS SERVFAILs and
# spot probes are all clean, so the suspect is an invisible NOERROR-empty
# (NODATA) A response. This canary queries the affected chart hosts via BOTH
# the cluster DNS path (what real pods use) AND the UDM directly, and logs
# ONLY anomalies plus a 15-minute heartbeat. Promtail ships it to Loki.
#
# Correlate with:
#   {namespace="observability", pod=~"dns-canary.*"} |= "ANOMALY"
#   {namespace="flux-system"} |= "network is unreachable"
# cluster-hit + udm-clean  => CoreDNS/cache layer
# both-hit                 => UDM upstream resolver
# canary-clean while flux still errors => not DNS; look at conntrack/dial path
set -u

HOSTS="${CANARY_HOSTS:-charts.goauthentik.io helm.openwebui.com raw.githubusercontent.com}"
UPSTREAM="${CANARY_UPSTREAM:-192.168.0.1}"
INTERVAL="${CANARY_INTERVAL_SECONDS:-15}"
HEARTBEAT_EVERY=$(( 900 / INTERVAL ))

ts() { date -u +%FT%TZ; }

# $1 resolver-label, $2 host, $3 dig server arg ("" = pod resolv.conf path).
# On anomaly, also grab the AAAA answer at the same instant — the flux failure
# mode is exactly "AAAA present, A missing", so this captures the asymmetry.
probe() {
  local out rcode aaaa
  out="$(dig +time=2 +tries=1 A "$2" $3 2>/dev/null)"
  rcode="$(printf '%s' "$out" | sed -n 's/.*status: \([A-Z]*\).*/\1/p' | head -1)"
  if [ "$rcode" = "NOERROR" ] && printf '%s' "$out" | grep -qE 'IN[[:space:]]+A[[:space:]]+[0-9]'; then
    return 0
  fi
  aaaa="$(dig +short +time=2 +tries=1 AAAA "$2" $3 2>/dev/null | head -1)"
  if [ "$rcode" = "NOERROR" ]; then
    echo "$(ts) ANOMALY resolver=$1 host=$2 kind=empty-A-NODATA aaaa=${aaaa:-none}"
  else
    echo "$(ts) ANOMALY resolver=$1 host=$2 kind=${rcode:-timeout} aaaa=${aaaa:-none}"
  fi
  return 1
}

i=0; anomalies=0; total=0
echo "$(ts) dns-canary start hosts=[$HOSTS] upstream=$UPSTREAM interval=${INTERVAL}s"
while true; do
  for h in $HOSTS; do
    probe cluster "$h" ""           || anomalies=$((anomalies+1))
    probe udm     "$h" "@$UPSTREAM" || anomalies=$((anomalies+1))
    total=$((total+2))
  done
  i=$((i+1))
  if [ $(( i % HEARTBEAT_EVERY )) -eq 0 ]; then
    echo "$(ts) heartbeat queries=$total anomalies=$anomalies"
    anomalies=0; total=0
  fi
  sleep "$INTERVAL"
done
