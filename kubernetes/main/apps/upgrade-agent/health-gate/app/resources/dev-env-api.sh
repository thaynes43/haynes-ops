#!/usr/bin/env bash
# dev-env-api.sh: the upgrade-agent callers' client for the dev-env v2 operator API
# (dev-env DESIGN-064 6.5, OpenProject #739). curl and jq only. ConfigMap
# dev-env-api-client, mounted read-only at /opt/dev-env-api in the caller pods
# (alert-responder, upgrade-shepherd and triage, upgrade-health-gate). dev-env-ops
# does not mount it.
#
# Usage:
#   dev-env-api.sh create < request.json
#   dev-env-api.sh get <name>
#   dev-env-api.sh list
#   dev-env-api.sh wait-registered <name> --for <seconds>
#   dev-env-api.sh selftest
#
# create sends one JSON object on stdin to POST /v1/sessions. It may carry only the
# keys DESIGN-064 6.4 lists: name, mode, lane, idempotencyKey and data, and agent,
# model and profile only to repeat the policy entry's value. Never a prompt: the
# platform renders the brief from the CallerPolicy's template (5.6). Any other key is
# refused here, before anything is sent. The original bytes are sent, so the API
# sees (and refuses) a duplicated key.
#
# wait-registered reads GET /v1/sessions/<name> every 5 seconds until its Remote
# Control registration settles (6.10): registered with an https://claude.ai/code/
# link is 0 and prints the session; login-required or offline before registering
# is 2; still waiting at --for seconds (1 to 900) is 3.
#
# selftest checks this pod, offline: bash, curl (with https), jq, tr and mktemp are
# present, and the token and CA files are mounted and readable. It sends nothing and
# prints no token.
#
# Exit codes (6.5):
#   0  created (201), reused (200) or read.
#   2  refused: a 4xx (the API's code and message go to stderr), or refused here
#      before sending (usage, a missing token or CA, a key 6.4 does not allow).
#   3  unconfirmed: a 5xx, a timeout, no connection, or any other status. A create
#      that exits 3 is retried with the same idempotencyKey; the API makes that safe.
#
# Output: on success stdout is the API's JSON body. stderr gets one line per HTTP
# call, "dev-env-api: <METHOD> <path> HTTP <code>" (000 when no response came), and
# on a refusal one line with the API's error code and message.
#
# The token is never printed, in any mode. It is never an argument (so never in a
# process list or an xtrace line) and never a shell variable: curl reads the
# Authorization header from a pipe that tr fills from the token file. xtrace is
# switched off first, whatever the caller set (set -x, bash -x, SHELLOPTS). curl
# runs with -q, so no .curlrc can turn on --verbose or --trace, and this script never
# passes them. Every call has a connect limit and a total limit.
#
# Environment (defaults are the in-cluster values; the selftest overrides them):
#   DEV_ENV_API_URL              https://dev-env-operator.dev-env-system.svc.cluster.local:8443
#                                (the URL the front door uses)
#   DEV_ENV_API_TOKEN_FILE       /var/run/secrets/dev-env/token (projected, audience
#                                dev-env-operator, 3600 s)
#   DEV_ENV_API_CA_FILE          /etc/dev-env/ca.crt (ConfigMap dev-env-api-ca)
#   DEV_ENV_API_CONNECT_TIMEOUT  5 seconds
#   DEV_ENV_API_MAX_TIME         30 seconds for a read; 130 for create, because the
#                                API gives a summoning create up to 120 seconds
{ set +x; } 2>/dev/null
set -uo pipefail
umask 077

API_URL="${DEV_ENV_API_URL:-https://dev-env-operator.dev-env-system.svc.cluster.local:8443}"
TOKEN_FILE="${DEV_ENV_API_TOKEN_FILE:-/var/run/secrets/dev-env/token}"
CA_FILE="${DEV_ENV_API_CA_FILE:-/etc/dev-env/ca.crt}"
CONNECT_TIMEOUT="${DEV_ENV_API_CONNECT_TIMEOUT:-5}"
MAX_TIME_SET="${DEV_ENV_API_MAX_TIME:-}"

# The keys a summoning create may carry (DESIGN-064 6.4; the API's summonCreateKeys).
CREATE_KEYS='["name","mode","lane","idempotencyKey","data","agent","model","profile"]'

say() { printf 'dev-env-api: %s\n' "$*" >&2; }
refuse() { say "refused: $*"; exit 2; }

usage() {
  cat >&2 <<'USAGE'
usage: dev-env-api.sh create < request.json
       dev-env-api.sh get <name>
       dev-env-api.sh list
       dev-env-api.sh wait-registered <name> --for <seconds>
       dev-env-api.sh selftest
USAGE
  exit 2
}

need_tools() {
  command -v curl >/dev/null 2>&1 || refuse "curl is not installed"
  command -v jq >/dev/null 2>&1 || refuse "jq is not installed"
}

need_files() {
  [ -r "$TOKEN_FILE" ] && [ -s "$TOKEN_FILE" ] || refuse "no readable token at $TOKEN_FILE"
  [ -r "$CA_FILE" ] && [ -s "$CA_FILE" ] || refuse "no readable CA certificate at $CA_FILE"
}

whole_seconds() { case "$1" in '' | *[!0-9]*) return 1 ;; esac; [ "${#1}" -le 4 ]; }

valid_name() {  # a DNS label of at most 63 characters, as the API requires
  [ "${#1}" -le 63 ] && [[ "$1" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]
}

TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD"; }
trap cleanup EXIT

# The Authorization header, written into a pipe by printf and tr. No argument and
# no variable ever holds the token.
auth_header() {
  printf 'Authorization: Bearer '
  tr -d '\r\n' < "$TOKEN_FILE"
  printf '\n'
}

# call METHOD PATH MAX_TIME [BODY_FILE]
# Writes the response body to $TMPD/body, sets HTTP_CODE, logs one status line.
HTTP_CODE=000
call() {
  local method="$1" path="$2" max_time="$3" body="${4:-}" code rc
  local args=(-q -sS --proto '=https' --noproxy '*' --cacert "$CA_FILE"
    --connect-timeout "$CONNECT_TIMEOUT" --max-time "$max_time"
    -X "$method" -H 'Accept: application/json' -o "$TMPD/body" -w '%{http_code}')
  if [ -n "$body" ]; then
    args+=(-H 'Content-Type: application/json' --data-binary "@$body")
  fi
  : > "$TMPD/body"
  code="$(curl "${args[@]}" -H @<(auth_header) "${API_URL%/}${path}" 2>"$TMPD/curl.err")"
  rc=$?
  case "$code" in [0-9][0-9][0-9]) ;; *) code=000 ;; esac
  HTTP_CODE="$code"
  say "$method $path HTTP $code"
  if [ "$rc" -ne 0 ] && [ "$code" = 000 ]; then
    # curl's own message names the URL and the failure, never a header.
    head -c 300 "$TMPD/curl.err" | tr -d '\r' | head -n 1 | sed 's/^/dev-env-api: /' >&2
  fi
  return 0
}

# Exit code for HTTP_CODE: 2xx 0, 4xx 2, anything else 3.
status_class() {
  case "$HTTP_CODE" in
    2??) return 0 ;;
    4??) return 2 ;;
    *) return 3 ;;
  esac
}

print_refusal() {
  local msg
  msg="$(jq -r 'if (.error | type) == "object" then "\(.error.code // "error"): \(.error.message // "")" else empty end' "$TMPD/body" 2>/dev/null | head -c 2000)"
  [ -n "$msg" ] || msg="$(head -c 300 "$TMPD/body" | tr -d '\r' | head -n 1)"
  say "refused: $msg"
}

finish() {  # print the body on success, the refusal on 4xx; exit by class
  status_class
  local c=$?
  case "$c" in
    0) cat "$TMPD/body"; [ -s "$TMPD/body" ] && printf '\n' ;;
    2) print_refusal ;;
    *) say "unconfirmed: HTTP $HTTP_CODE" ;;
  esac
  exit "$c"
}

read_max_time() {  # $1 = this command's default
  local t="${MAX_TIME_SET:-$1}"
  whole_seconds "$t" && [ "$t" -ge 1 ] || refuse "DEV_ENV_API_MAX_TIME must be whole seconds"
  printf '%s' "$t"
}

cmd_create() {
  [ "$#" -eq 0 ] || usage
  need_tools; need_files
  local max_time; max_time="$(read_max_time 130)" || exit 2
  TMPD="$(mktemp -d)" || refuse "cannot make a private temporary directory"
  cat > "$TMPD/request" || refuse "cannot read the request on stdin"
  jq -e 'type == "object"' "$TMPD/request" >/dev/null 2>&1 \
    || refuse "the request on stdin must be one JSON object"
  if jq -e 'has("prompt")' "$TMPD/request" >/dev/null 2>&1; then
    refuse "a create never carries a prompt; the platform renders the brief from the policy (DESIGN-064 5.6)"
  fi
  local extra
  extra="$(jq -r --argjson allow "$CREATE_KEYS" '[keys_unsorted[] | select(. as $k | $allow | index($k) | not)] | join(", ")' "$TMPD/request")"
  [ -z "$extra" ] || refuse "a summoning create cannot carry: $extra (DESIGN-064 6.4)"
  call POST /v1/sessions "$max_time" "$TMPD/request"
  finish
}

cmd_get() {
  [ "$#" -eq 1 ] || usage
  valid_name "$1" || refuse "a session name is a DNS label of at most 63 characters"
  need_tools; need_files
  local max_time; max_time="$(read_max_time 30)" || exit 2
  TMPD="$(mktemp -d)" || refuse "cannot make a private temporary directory"
  call GET "/v1/sessions/$1" "$max_time"
  finish
}

cmd_list() {
  [ "$#" -eq 0 ] || usage
  need_tools; need_files
  local max_time; max_time="$(read_max_time 30)" || exit 2
  TMPD="$(mktemp -d)" || refuse "cannot make a private temporary directory"
  call GET /v1/sessions "$max_time"
  finish
}

cmd_wait_registered() {
  [ "$#" -eq 3 ] && [ "$2" = "--for" ] || usage
  local name="$1" wait_for="$3"
  valid_name "$name" || refuse "a session name is a DNS label of at most 63 characters"
  whole_seconds "$wait_for" && [ "$wait_for" -ge 1 ] && [ "$wait_for" -le 900 ] \
    || refuse "--for takes whole seconds, 1 to 900"
  need_tools; need_files
  local max_time; max_time="$(read_max_time 30)" || exit 2
  TMPD="$(mktemp -d)" || refuse "cannot make a private temporary directory"
  local deadline=$(( $(date +%s) + wait_for )) now left t state url c
  while :; do
    now="$(date +%s)"; left=$(( deadline - now ))
    [ "$left" -gt 0 ] || break
    t="$max_time"; [ "$left" -lt "$t" ] && t="$left"
    call GET "/v1/sessions/$name" "$t"
    status_class; c=$?
    if [ "$c" -eq 2 ]; then print_refusal; exit 2; fi
    if [ "$c" -eq 0 ]; then
      state="$(jq -r '.remoteControl.state // ""' "$TMPD/body" 2>/dev/null)"
      url="$(jq -r '.remoteControl.url // ""' "$TMPD/body" 2>/dev/null)"
      if [ "$state" = registered ] && [[ "$url" =~ ^https://claude\.ai/code/[A-Za-z0-9_-]+$ ]]; then
        cat "$TMPD/body"; printf '\n'; exit 0
      fi
      case "$state" in
        login-required | offline)
          say "refused: Remote Control registration failed: state $state (DESIGN-064 6.10)"
          exit 2 ;;
      esac
    fi
    now="$(date +%s)"; left=$(( deadline - now ))
    [ "$left" -gt 0 ] || break
    sleep "$(( left < 5 ? left : 5 ))"
  done
  say "unconfirmed: $name not registered within ${wait_for}s"
  exit 3
}

cmd_selftest() {
  [ "$#" -eq 0 ] || usage
  local bad=0 t
  say "selftest: bash ${BASH_VERSION}"
  for t in curl jq tr mktemp; do
    if command -v "$t" >/dev/null 2>&1; then say "selftest: ok $t"; else say "selftest: missing $t"; bad=1; fi
  done
  if command -v curl >/dev/null 2>&1; then
    if curl -q -V 2>/dev/null | grep -qi '^Protocols:.* https'; then say "selftest: ok curl https"
    else say "selftest: missing curl https"; bad=1; fi
  fi
  if [ -r "$TOKEN_FILE" ] && [ -s "$TOKEN_FILE" ]; then say "selftest: ok token file $TOKEN_FILE"
  else say "selftest: missing token file $TOKEN_FILE"; bad=1; fi
  if [ -r "$CA_FILE" ] && grep -q -- '-----BEGIN CERTIFICATE-----' "$CA_FILE" 2>/dev/null; then say "selftest: ok CA file $CA_FILE"
  else say "selftest: missing CA file $CA_FILE"; bad=1; fi
  [ "$bad" -eq 0 ] && { say "selftest: ok"; exit 0; }
  say "selftest: refused"
  exit 2
}

[ "$#" -ge 1 ] || usage
sub="$1"; shift
case "$sub" in
  create) cmd_create "$@" ;;
  get) cmd_get "$@" ;;
  list) cmd_list "$@" ;;
  wait-registered) cmd_wait_registered "$@" ;;
  selftest) cmd_selftest "$@" ;;
  *) usage ;;
esac
