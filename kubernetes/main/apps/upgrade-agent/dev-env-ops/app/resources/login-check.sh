#!/usr/bin/env bash
# login-check.sh — how much life is left in dev-env-ops' OWN Claude Max login
# (~/.claude/.credentials.json on the dev-env-ops-home PVC), WITHOUT printing
# anything secret. The dev-env-ops twin of the dev-env pod's `claude-login-check`
# (kubernetes/main/apps/dev/dev-env/app/resources/login-check.sh): same exit
# contract, same field, but a DIFFERENT login. Keep the two in step.
#
# Why (haynes-ops#3414, Tom 2026-10-06): Remote Control registration needs a
# `/login` credential (scope user:sessions:claude_code); the setup token in
# CLAUDE_CODE_OAUTH_TOKEN is inference-only and is refused. So the wo-*/esc-*
# lanes ride this pod's own monthly Max login. Its refresh token lapses ~30 days
# after each `claude auth login` (claudeAiOauth.refreshTokenExpiresAt).
#
# Callers:
#   work-order-watch.sh step 5 — daily; pages Tom at <= 7 days, naming THIS pod
#   session-launch.sh          — `--days 0`: is the login still alive at all?
#   the renewal ceremony       — verify step (.agents/runbooks/agentic-remediation.md)
#
#   login-check.sh [--days N] [--quiet]
#     exit 0  fine: more than N days left (default N=7)
#     exit 1  LOGIN NEEDED: N days or fewer left, or already expired
#     exit 2  no Max login: no credential file, or a file with no refresh-token
#             expiry (the pre-#3414 synthesized setup-token file looked like that)
#   Output is timestamps and day counts only — safe to quote in a page or a PR.
set -uo pipefail

days=7; quiet=0
while [ $# -gt 0 ]; do
  case "$1" in
    --days) days="${2:?--days needs a number}"; shift 2 ;;
    --days=*) days="${1#--days=}"; shift ;;
    --quiet|-q) quiet=1; shift ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "login-check: unknown option '$1'" >&2; exit 2 ;;
  esac
done

label="dev-env-ops Max login"
file="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.credentials.json"
if [ ! -r "$file" ]; then
  echo "$label: NONE (no $file) — wo-*/esc-* run on the setup token WITHOUT Remote Control until the dev-env-ops Max login ceremony is run"
  exit 2
fi

read -r refresh_ms access_ms sub < <(jq -r '(.claudeAiOauth // {}) as $o
  | [($o.refreshTokenExpiresAt // "null"), ($o.expiresAt // "null"), ($o.subscriptionType // "?")]
  | @tsv' "$file" 2>/dev/null) || true
if [ -z "${refresh_ms:-}" ] || [ "$refresh_ms" = null ]; then
  echo "$label: NONE ($file has no claudeAiOauth.refreshTokenExpiresAt — not a /login credential) — wo-*/esc-* run WITHOUT Remote Control until the ceremony is run"
  exit 2
fi

now=$(date +%s)
ms_to_s() { awk -v ms="$1" 'BEGIN { printf "%d", ms / 1000 }'; }
fmt() { date -u -d "@$1" +'%Y-%m-%d %H:%M UTC'; }
refresh_s=$(ms_to_s "$refresh_ms")
left=$(awk -v e="$refresh_s" -v n="$now" 'BEGIN { printf "%.1f", (e - n) / 86400 }')

state="OK (threshold ${days} d)"; rc=0
if awk -v l="$left" 'BEGIN { exit !(l <= 0) }'; then
  state="EXPIRED — LOGIN NEEDED NOW"; rc=1
elif awk -v l="$left" -v d="$days" 'BEGIN { exit !(l <= d) }'; then
  state="LOGIN NEEDED within ${days} days"; rc=1
fi

echo "$label (plan=$sub) expires $(fmt "$refresh_s") — ${left} days left — $state"
if [ "$quiet" = 0 ]; then
  [ "$access_ms" != null ] && echo "  access token expires $(fmt "$(ms_to_s "$access_ms")") (self-refreshes while the login above is valid)"
  echo "  (this is the dev-env-ops pod's login, NOT the dev-env pod's; rem-* and curation use CLAUDE_CODE_OAUTH_TOKEN and are not affected)"
fi
exit $rc
