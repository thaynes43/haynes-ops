#!/usr/bin/env bash
# auth-watch sidecar — daily credential-freshness probe (saga plan 04): pages
# Pushover BEFORE an expired credential strands a dispatched agent. Distinguishes
# auth failure (page) from rate-limit (expected on a busy plan — NOT a page).
set -uo pipefail
log() { printf 'auth-watch: %s %s\n' "$(date -u +%FT%TZ)" "$*"; }

page() { # $1 title, $2 message — priority 0, best-effort
  curl -sf --max-time 10 https://api.pushover.net/1/messages.json \
    --form-string "token=${PUSHOVER_TOKEN}" \
    --form-string "user=${PUSHOVER_USER_KEY}" \
    --form-string "title=$1" \
    --form-string "message=$2" >/dev/null \
    && log "paged: $1" || log "WARN pushover send failed"
}

while true; do
  sleep 60   # let the pod settle on boot before the first probe
  fails=""

  # Claude (Max plan): a minimal real call. Auth errors mention login/OAuth/401;
  # rate-limit output mentions limit — treat only the former as failure.
  # Probe BOTH auth paths: the CLAUDE_CODE_OAUTH_TOKEN env (headless/task mode)
  # AND ~/.claude/.credentials.json (remote-control "both" sessions — the env
  # token cannot register /v1/code/sessions, so agent-run strips it there,
  # 2026-08-29). An expired login on either path strands agents.
  out="$(timeout 120 claude -p 'ok' --model haiku --max-turns 1 2>&1)" || true
  if printf '%s' "$out" | grep -qiE 'not logged in|/login|oauth|401|invalid.*(token|api key)'; then
    fails="${fails}claude: env-token auth invalid — re-mint via the setup-token ceremony in .agents/sagas/dev-env/backlog/04-auth.md\n"
  fi
  out="$(timeout 120 env -u CLAUDE_CODE_OAUTH_TOKEN claude -p 'ok' --model haiku --max-turns 1 2>&1)" || true
  if printf '%s' "$out" | grep -qiE 'not logged in|/login|oauth|401|invalid.*(token|api key)'; then
    fails="${fails}claude: credentials.json login invalid (remote-control sessions) — rerun the /login ceremony in .agents/sagas/dev-env/backlog/04-auth.md\n"
  fi

  # Codex (ChatGPT plan)
  codex login status >/dev/null 2>&1 \
    || fails="${fails}codex: not logged in — device-auth ceremony needed\n"

  # gh bot token: file freshness (refresher alive) + a real API round-trip.
  if [ ! -s /creds/gh_token ] || [ -n "$(find /creds/gh_token -mmin +120 2>/dev/null)" ]; then
    fails="${fails}gh: /creds/gh_token missing or stale (>2h) — refresher sidecar dead?\n"
  elif ! curl -sf --max-time 10 -H "Authorization: Bearer $(cat /creds/gh_token)" \
         https://api.github.com/installation/repositories >/dev/null; then
    fails="${fails}gh: bot token rejected by the GitHub API\n"
  fi

  if [ -n "$fails" ]; then
    page "dev-env credential failure" "$(printf '%b' "$fails")"
  else
    log "all credentials healthy (claude/codex/gh)"
  fi
  sleep 86340
done
