#!/usr/bin/env bash
# gh-refresher sidecar (dev-env-ops) — keeps a short-lived (1h) OPS-bot installation
# token fresh at /creds/gh_token. COPY of dev/dev-env/app/resources/gh-token-refresh.sh
# (kustomize cannot reference files across app roots) — KEEP IN SYNC. Differences:
# none in behavior; the identity comes from the mounted secret (upgrade-shepherd-bot,
# haynes-ops scope) instead of the dev bot. /opt/dev-env/github-app-token.sh is baked
# into the shared dev-env image.
# Refresh at 40min < 60min TTL so a token read by a shell is always ≥20min from expiry.
set -uo pipefail
log() { printf 'gh-refresher: %s %s\n' "$(date -u +%FT%TZ)" "$*"; }

while true; do
  if /opt/dev-env/github-app-token.sh > /creds/.gh_token.tmp 2>/tmp/mint.err \
       && [ -s /creds/.gh_token.tmp ]; then
    mv /creds/.gh_token.tmp /creds/gh_token
    log "token refreshed"
  else
    log "WARN mint failed: $(tail -c 200 /tmp/mint.err 2>/dev/null)"
  fi
  sleep 2400
done
