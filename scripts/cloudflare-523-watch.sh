#!/usr/bin/env bash
set -euo pipefail

# Watches Cloudflare-fronted endpoints and logs transient 5xx (esp. 523).
#
# Why this exists:
# - Cloudflare 523 can be POP/route dependent.
# - Gatus failures can be short (2–5 minutes) and hard to catch interactively.
# - Capturing `cf-ray` and the response code during failures makes it actionable.
#
# Usage (WSL / Linux):
#   # from repo root:
#   ./scripts/cloudflare-523-watch.sh
#
#   # or from within ./scripts:
#   ./cloudflare-523-watch.sh
#
# Optional env vars:
#   INTERVAL_SECONDS=2
#   URLS="https://authentik.haynesnetwork.com/ https://immich.haynesnetwork.com/"

INTERVAL_SECONDS="${INTERVAL_SECONDS:-2}"
FAIL_THRESHOLD="${FAIL_THRESHOLD:-3}"   # consecutive failures before logging
RELOG_EVERY="${RELOG_EVERY:-10}"        # once failing, re-log every Nth fail

DEFAULT_URLS=(
  "https://authentik.haynesnetwork.com/"
  "https://immich.haynesnetwork.com/"
  "https://paperless.haynesnetwork.com/"
  "https://ai.haynesnetwork.com/"
  "https://k8plex.haynesnetwork.com/identity"
)

if [[ "${URLS:-}" != "" ]]; then
  # shellcheck disable=SC2206
  URL_LIST=(${URLS})
else
  URL_LIST=("${DEFAULT_URLS[@]}")
fi

probe() {
  # Single curl invocation so status + headers always match.
  # Includes curl stderr so code=000 cases are explainable (timeout/TLS/etc).
  local url="$1"
  local out rc

  set +e
  out="$(
    curl -sS -D - -o /dev/null \
      --connect-timeout 5 \
      --max-time 10 \
      -w '\n__CURLMETRICS__ http_code=%{http_code} remote_ip=%{remote_ip} time_total=%{time_total}\n' \
      "$url" 2>&1
  )"
  rc="$?"
  set -e

  printf '%s\n' "$out" | tr -d '\r'
  return "$rc"
}

print_interesting() {
  awk 'BEGIN{IGNORECASE=1}
    /^HTTP\// ||
    /^date:/ ||
    /^server:/ ||
    /^cf-ray:/ ||
    /^cf-cache-status:/ ||
    /^cf-connecting-ip:/ ||
    /^__CURLMETRICS__/ ||
    /^curl: / {print}'
}

declare -A FAIL_COUNTS

while true; do
  for url in "${URL_LIST[@]}"; do
    probe_out="$(probe "$url" || true)"
    metrics="$(awk '/^__CURLMETRICS__/ {print; exit}' <<<"$probe_out")"
    code="$(awk -F'[ =]' '{for (i=1;i<=NF;i++) if ($i=="http_code") {print $(i+1); exit}}' <<<"$metrics")"
    rip="$(awk -F'[ =]' '{for (i=1;i<=NF;i++) if ($i=="remote_ip") {print $(i+1); exit}}' <<<"$metrics")"
    total="$(awk -F'[ =]' '{for (i=1;i<=NF;i++) if ($i=="time_total") {print $(i+1); exit}}' <<<"$metrics")"

    is_fail=false
    if [[ "$code" == "523" || "$code" == "520" || "$code" == "521" || "$code" == "522" || "$code" == "525" || "$code" == "526" || "$code" == "530" || "$code" == 5* || "$code" == "000" || "$code" == "" ]]; then
      is_fail=true
    fi

    if [[ "$is_fail" == "true" ]]; then
      FAIL_COUNTS["$url"]="$(( ${FAIL_COUNTS["$url"]:-0} + 1 ))"
    else
      FAIL_COUNTS["$url"]=0
    fi

    fail_count="${FAIL_COUNTS["$url"]:-0}"

    if [[ "$is_fail" == "true" && "$fail_count" -ge "$FAIL_THRESHOLD" ]]; then
      should_log=false
      if [[ "$fail_count" -eq "$FAIL_THRESHOLD" ]]; then
        should_log=true
      elif (( (fail_count - FAIL_THRESHOLD) % RELOG_EVERY == 0 )); then
        should_log=true
      fi

      if [[ "$should_log" != "true" ]]; then
        continue
      fi

      ts="$(date -Is)"
      echo "[$ts] url=$url code=${code:-unknown} remote_ip=${rip:-unknown} time_total=${total:-unknown} fail_count=$fail_count"
      print_interesting <<<"$probe_out" | sed 's/^/  /'
    fi
  done
  sleep "$INTERVAL_SECONDS"
done

