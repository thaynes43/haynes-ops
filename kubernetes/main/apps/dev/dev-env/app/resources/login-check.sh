#!/usr/bin/env bash
# claude-login-check — how much life is left in the pod's Claude Max login
# (~/.claude/.credentials.json on the PVC), WITHOUT printing anything secret.
#
# Why (2026-09-23): that login's refresh token expires ~30 days after each /login
# (claudeAiOauth.refreshTokenExpiresAt — the CLI's "Your login expires in N days"
# banner counts down to it). The access token self-refreshes only until then;
# afterwards every remote-control ("both") session fails to start and post-ready
# skips the standby. CLAUDE_CODE_OAUTH_TOKEN (headless/task sessions, ~1 yr, from
# 1Password) is a separate credential and is NOT what this checks.
#
# Agents run it at the start of every session (pod CLAUDE.md, "Max login renewal");
# the auth-watch sidecar runs it daily and pages Tom at <= 7 days.
#
#   claude-login-check [--days N] [--quiet]
#     exit 0  fine: more than N days left (default N=7)
#     exit 1  LOGIN NEEDED: N days or fewer left, or already expired
#     exit 2  cannot tell: no credential file, or no refreshTokenExpiresAt in it
#             (claude-code changed the format? check `claude auth status`)
#   Output is timestamps and day counts only — safe to quote in a reply or a PR.
set -uo pipefail

usage() {
  cat <<'USAGE'
usage: claude-login-check [--days N] [--quiet]
  --days N   warn when the Max login has N days or fewer left (default 7)
  --quiet    one line only (what the auth-watch sidecar pages with)
exit 0 = fine, 1 = login needed within N days (or expired), 2 = cannot tell
USAGE
}

days=7; quiet=0
while [ $# -gt 0 ]; do
  case "$1" in
    --days) days="${2:?--days needs a number}"; shift 2 ;;
    --days=*) days="${1#--days=}"; shift ;;
    --quiet|-q) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "claude-login-check: unknown option '$1'" >&2; usage >&2; exit 2 ;;
  esac
done

file="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.credentials.json"
if [ ! -r "$file" ]; then
  echo "claude login: NO credential file at $file — remote-control sessions cannot start; run the Max login renewal (pod CLAUDE.md)"
  exit 2
fi

# One jq pass, defaults instead of empties so `read` keeps its columns.
read -r refresh_ms access_ms sub < <(jq -r '(.claudeAiOauth // {}) as $o
  | [($o.refreshTokenExpiresAt // "null"), ($o.expiresAt // "null"), ($o.subscriptionType // "?")]
  | @tsv' "$file" 2>/dev/null) || true
if [ -z "${refresh_ms:-}" ] || [ "$refresh_ms" = null ]; then
  echo "claude login: $file has no claudeAiOauth.refreshTokenExpiresAt — cannot tell when the login lapses (format change? check: env -u CLAUDE_CODE_OAUTH_TOKEN claude auth status)"
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

echo "claude login: Max login (refresh token, plan=$sub) expires $(fmt "$refresh_s") — ${left} days left — $state"
if [ "$quiet" = 0 ]; then
  [ "$access_ms" != null ] && echo "  access token expires $(fmt "$(ms_to_s "$access_ms")") (self-refreshes while the login above is valid)"
  [ "$rc" = 1 ] && echo "  -> tell Tom in your next reply, then run the Max login renewal in the pod CLAUDE.md (you relay the OAuth link, he pastes the code back; ~1 minute of his time)"
  echo "  (headless/task sessions use CLAUDE_CODE_OAUTH_TOKEN and are not affected by this)"
fi
exit $rc
