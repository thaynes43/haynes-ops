#!/usr/bin/env bash
# prom-query.sh — deterministic PromQL wrapper for the responder's LLM.
# The ONLY http the LLM can drive toward Prometheus (raw curl is not allowlisted).
# Usage: prom-query.sh '<promql instant query>'
set -uo pipefail
PROM="${PROMETHEUS_URL:-http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090}"
q="${1:?usage: prom-query.sh '<promql>'}"
curl -sf --max-time 15 "$PROM/api/v1/query" --data-urlencode "query=$q" 2>/dev/null \
  | jq -c '.data.result[:25] | map({metric, value: .value[1]})' 2>/dev/null \
  || { echo "QUERY FAILED (parse error or Prometheus unreachable)"; exit 1; }
