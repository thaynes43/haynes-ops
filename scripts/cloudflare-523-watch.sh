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

print_headers() {
  # Print status line + key Cloudflare headers when possible
  # (curl exits non-zero on some TLS/conn errors; we still want the headers it captured).
  curl -sS -o /dev/null -D - --max-time 10 "$1" 2>/dev/null \
    | tr -d '\r' \
    | awk 'BEGIN{IGNORECASE=1}
      /^HTTP\// || /^date:/ || /^server:/ || /^cf-ray:/ || /^cf-cache-status:/ || /^cf-connecting-ip:/ {print}'
}

while true; do
  for url in "${URL_LIST[@]}"; do
    # Get the status code and remote IP quickly.
    # Note: If curl fails at connect/TLS, code will be 000.
    out="$(curl -sS -o /dev/null --max-time 10 -w '%{http_code} %{remote_ip}\n' "$url" 2>/dev/null || true)"
    code="$(awk '{print $1}' <<<"$out")"
    rip="$(awk '{print $2}' <<<"$out")"

    if [[ "$code" == "523" || "$code" == "520" || "$code" == "521" || "$code" == "522" || "$code" == "525" || "$code" == "526" || "$code" == "530" || "$code" == 5* || "$code" == "000" ]]; then
      ts="$(date -Is)"
      echo "[$ts] url=$url code=$code remote_ip=$rip"
      print_headers "$url" | sed 's/^/  /'
    fi
  done
  sleep "$INTERVAL_SECONDS"
done

