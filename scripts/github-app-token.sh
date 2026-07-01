#!/usr/bin/env bash
# Mint a short-lived GitHub App installation access token (ghs_…, ~1h) for the
# haynes-ops-bot App, for use by the Tier-4 upgrade-shepherd agent in ANY runtime.
#
# Why this exists: an unattended bot must push branches / open + merge PRs / push
# reverts with a NON-GITHUB_TOKEN identity (so the pushes/PRs trigger the
# flux-local check) and with a scoped, auto-expiring, non-human credential. `gh`
# cannot authenticate AS a GitHub App, so we mint an installation token from the
# App private key (JWT -> /access_tokens) and feed it to gh/git.
#
# Credential-source-agnostic: it reads the App credentials from the environment,
# so the same script works whether the env is populated by 1Password (op run /
# direnv) for a local/cloud run, or by a Kubernetes Secret for the in-cluster
# CronJob. Inside GitHub Actions, prefer actions/create-github-app-token instead.
#
# Required env (names match the 1Password 'github-bot' item fields):
#   GITHUB_BOT_APP_CLIENT_ID  (preferred)  or  GITHUB_BOT_APP_ID   — JWT issuer
#   GITHUB_BOT_APP_INSTALLATION_ID                                 — numeric
#   GITHUB_BOT_APP_PRIVATE_KEY  (full PEM)  or  GITHUB_BOT_APP_PRIVATE_KEY_FILE
#
# Optional env:
#   GITHUB_BOT_TOKEN_PERMISSIONS  — JSON object to DOWN-SCOPE the minted token
#       (default: contents:write, pull_requests:write, checks:read). Can only
#       narrow the App's grant, never exceed it.
#
# Output: prints ONLY the ghs_ token to stdout (capturable); diagnostics to stderr.
#
# Usage:
#   export GH_TOKEN="$(scripts/github-app-token.sh)"
#   gh pr list                     # now acts as haynes-ops-bot
#   git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/thaynes43/haynes-ops.git"
set -euo pipefail

err() { printf '%s\n' "$*" >&2; }
need() { command -v "$1" >/dev/null 2>&1 || { err "missing required command: $1"; exit 1; }; }
need openssl; need curl; need jq
b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

iss="${GITHUB_BOT_APP_CLIENT_ID:-${GITHUB_BOT_APP_ID:-}}"
install_id="${GITHUB_BOT_APP_INSTALLATION_ID:-}"
[ -n "$iss" ]        || { err "set GITHUB_BOT_APP_CLIENT_ID or GITHUB_BOT_APP_ID"; exit 1; }
[ -n "$install_id" ] || { err "set GITHUB_BOT_APP_INSTALLATION_ID"; exit 1; }

# Materialize the PEM into a private temp file.
keyfile="$(mktemp)"; chmod 600 "$keyfile"
trap 'rm -f "$keyfile"' EXIT
if [ -n "${GITHUB_BOT_APP_PRIVATE_KEY_FILE:-}" ]; then
  cat "$GITHUB_BOT_APP_PRIVATE_KEY_FILE" > "$keyfile"
elif [ -n "${GITHUB_BOT_APP_PRIVATE_KEY:-}" ]; then
  printf '%s\n' "$GITHUB_BOT_APP_PRIVATE_KEY" > "$keyfile"
else
  err "set GITHUB_BOT_APP_PRIVATE_KEY or GITHUB_BOT_APP_PRIVATE_KEY_FILE"; exit 1
fi

# Build and sign the App JWT (RS256). iat backdated 60s for clock skew; exp <=10m.
now="$(date +%s)"
header='{"alg":"RS256","typ":"JWT"}'
payload="$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' "$((now - 60))" "$((now + 540))" "$iss")"
unsigned="$(printf '%s' "$header" | b64url).$(printf '%s' "$payload" | b64url)"
sig="$(printf '%s' "$unsigned" | openssl dgst -sha256 -sign "$keyfile" | b64url)"
jwt="${unsigned}.${sig}"

# NB: do NOT use an inline brace-containing default (${VAR:-{...}}). When VAR is SET,
# bash mis-parses the nested braces and appends a stray '}', producing malformed JSON
# ({"permissions":{...}}}) -> HTTP 400 from GitHub. Split the default onto its own line.
perms="${GITHUB_BOT_TOKEN_PERMISSIONS:-}"
[ -n "$perms" ] || perms='{"contents":"write","pull_requests":"write","checks":"read"}'

resp="$(curl -fsS -X POST \
  -H "Authorization: Bearer ${jwt}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/app/installations/${install_id}/access_tokens" \
  -d "{\"permissions\":${perms}}")" || { err "installation-token request failed"; exit 1; }

token="$(printf '%s' "$resp" | jq -r '.token // empty')"
[ -n "$token" ] || { err "no token in response: $(printf '%s' "$resp" | jq -c '{message,status}' 2>/dev/null || printf '%s' "$resp")"; exit 1; }
printf '%s\n' "$token"
