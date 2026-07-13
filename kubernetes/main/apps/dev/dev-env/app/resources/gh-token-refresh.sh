#!/usr/bin/env bash
# gh-refresher sidecar — keeps a short-lived (1h) haynes-ops-bot installation token
# fresh at /creds/gh_token for the app container (saga Decision #5). The PEM lives
# ONLY in THIS container's env; the agents only ever see the minted ghs_ token.
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
