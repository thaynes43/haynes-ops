#!/usr/bin/env bash
# loki-query.sh — deterministic LogQL wrapper for the responder's LLM (pod logs come
# from Loki, NOT kubectl logs — the read-only ClusterRole deliberately denies pods/log).
# Usage: loki-query.sh '<logql>' [lookback-minutes=60] [limit=100]
set -uo pipefail
# Default = the loki Service DIRECT (:3100). loki-gateway runs replicas:0 here — a
# Service with no endpoints that resolves fine and then times out every query.
LOKI="${LOKI_URL:-http://loki.observability.svc.cluster.local:3100}"
q="${1:?usage: loki-query.sh '<logql>' [minutes] [limit]}"
mins="${2:-60}"; limit="${3:-100}"
start="$(date -u -d "-${mins} minutes" +%s)000000000"
end="$(date -u +%s)000000000"
curl -sf --max-time 20 -G "$LOKI/loki/api/v1/query_range" \
    --data-urlencode "query=$q" \
    --data-urlencode "start=$start" \
    --data-urlencode "end=$end" \
    --data-urlencode "limit=$limit" \
    --data-urlencode "direction=backward" 2>/dev/null \
  | jq -r '.data.result[]?.values[]? | .[1]' 2>/dev/null | head -n "$limit" \
  || { echo "QUERY FAILED (LogQL parse error or Loki unreachable)"; exit 1; }
