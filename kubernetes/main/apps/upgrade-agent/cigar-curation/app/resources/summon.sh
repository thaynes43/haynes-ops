#!/usr/bin/env bash
# summon.sh: the daily cigar-catalog curation's v2 caller (dev-env DESIGN-064 6.11,
# OpenProject #746 slice a, story 805). ConfigMap cigar-curation-scripts, run by the
# CronJob cigar-curation in upgrade-agent at 10:15 America/New_York.
#
# It asks the dev-env v2 operator API for one summoned task session a day, through the
# callers' client dev-env-api.sh (ConfigMap dev-env-api-client). It sends data only:
#   name            wo-cigar-curate-<UTC date YYYYMMDD>   (v1's key, date -u)
#   mode, lane      task, curation
#   idempotencyKey  cigar-curate-<UTC date>
#   data            date (the UTC date) and run (this Job pod's name)
# The CallerPolicy dev-env-system/upgrade-agent.cigar-curation fixes everything else:
# repo, profile, model, effort, timeout, and the brief, which the platform renders.
# The API authenticates this ServiceAccount and its CallerPolicy, not this CronJob
# or a pod-bound token. Any holder of an operator-audience token for the account,
# with a network path to the API, can choose the name/date/run and choose or omit
# idempotencyKey, re-fire a terminal key and use up the caller's creates. The
# one-hour, pod-bound projection describes this manifest's token only.
#
# What it does:
#   1. Reads its own sessions (GET /v1/sessions). A session of today's date, by name or
#      by key, in any state, means there is nothing to do: exit 0. Every list entry
#      must be well-formed; a malformed entry stops the run with exit 3, no retry.
#   2. Creates, with today's key. An answer with exactly the requested name (201
#      created, or 200 for a live session under the key) is exit 0. Another name is
#      a duplicate start: exit 4, no retry and no further POST.
#   3. A refusal (any 4xx: 403 not switched on or no policy, 409, 422, 429) is exit 2
#      with no retry that day. A 409 is not "already started" (item 746 comment 7428):
#      only an answer that names a session means the work is under way. Tomorrow's key
#      differs, so the next run uses a new key by itself.
#   4. An unconfirmed answer (5xx, timeout, no connection, or a 2xx that names no
#      session) is retried with the same key, at most three attempts, 60 seconds
#      apart. Every attempt reads the list first and creates only when that read
#      answered and showed no session of today: the key keeps a retry from making a
#      second session only while the first is live (a key re-fired after its session
#      ended gets a -2 name). The list check is not atomic with the create.
#   5. After the last unconfirmed attempt it reads the list once more: a session of
#      today is exit 0, a refusal is exit 2, else exit 3.
#
# Coordinator ruling D-7 (item 746 comment 7709), restated for OP805 review round 2:
# at most one session a day does catalog work. Two Jobs can both list empty, then
# A creates and finishes before B POSTs. B can still start a duplicate Session,
# costing one create charge and one model start. The contract requires the full
# exact dated name before any catalog read or write; a suffixed session reports
# duplicate done and does no catalog work. Exit 4 makes this Job fail visibly but
# cannot undo that start. This is no atomic daily lock: it relies on retaining the
# original dated Session record, so deleting that record can allow the name again.
#
# Exit codes: 0 a session of today exists or the exact requested name was created;
# 2 refused (a 4xx, or refused here or by the client before sending: missing client, token or
# CA); 3 unconfirmed (including a malformed list); 4 duplicate start (a create
# answered with a different name). Codes 0, 2 and 3 follow the client's convention;
# 4 belongs to this script.
#
# Time: the client allows a read 30 seconds and a create 130 seconds. The worst case
# is three attempts of one read and one create, two 60-second pauses and one last read:
# 3 x (30 + 130) + 2 x 60 + 30 = 630 seconds, inside the Job's activeDeadlineSeconds
# (900). scripts/upgrade-agent/cigar-curation-selftest.sh checks that sum against the
# HelmRelease.
#
# The token: this script never opens the token file, and never prints the environment.
# The client sends the token and never prints it. xtrace is switched off first,
# whatever the caller set.
#
# Environment: DEV_ENV_API_CLIENT, the client's path (default
# /opt/dev-env-api/dev-env-api.sh; the selftest sets it). The client reads its own
# DEV_ENV_API_* variables.
{ set +x; } 2>/dev/null
set -uo pipefail

CLIENT="${DEV_ENV_API_CLIENT:-/opt/dev-env-api/dev-env-api.sh}"
ATTEMPTS=3
PAUSE=60

day="$(date -u +%Y%m%d)"
name="wo-cigar-curate-${day}"
key="cigar-curate-${day}"

say() { printf 'cigar-curation: %s\n' "$*"; }

if [ ! -x "$CLIENT" ]; then
  say "the client $CLIENT is missing (ConfigMap dev-env-api-client); nothing was sent"
  exit 2
fi

# today: reads this caller's own sessions. Returns 0 when one of today's date exists
# (by name or by key, any state) and prints its name and state; 1 when the read
# answered and none exists; 2 when the read was refused; 3 when it is unconfirmed.
# Internal return 4 means malformed list entries: the caller exits 3 without retry.
today() {
  local out rc
  out="$("$CLIENT" list)"
  rc=$?
  case "$rc" in
    0) ;;
    2) return 2 ;;
    *) return 3 ;;
  esac
  printf '%s' "$out" | jq -e 'type == "object" and (.sessions | type == "array")' >/dev/null 2>&1 || return 3
  # Session.name is required. The other fields read below are omitempty in the
  # API's Session/Outcome wire types; when present, their types must be usable.
  # Validate EVERY entry before either finding today's session or claiming absence.
  printf '%s' "$out" | jq -e '
    def optional_string($field): (has($field) | not) or (.[$field] | type == "string");
    .sessions | all(.[];
      type == "object" and (.name | type == "string" and length > 0)
      and optional_string("idempotencyKey") and optional_string("phase")
      and ((has("outcome") | not) or (.outcome | type == "object" and optional_string("state"))))' >/dev/null 2>&1 || return 4
  printf '%s' "$out" | jq -er --arg n "$name" --arg k "$key" '
    [.sessions[] | select(.name == $n or .idempotencyKey == $k)][0]
    | select(. != null) | "\(.name) \(.outcome.state // .phase // "pending")"' 2>/dev/null
  rc=$?
  case "$rc" in
    0) return 0 ;;
    4) return 1 ;; # jq -e: no matching entry, in the validated list
    *) return 3 ;;
  esac
}

req="$(jq -cn --arg n "$name" --arg k "$key" --arg d "$day" --arg r "${HOSTNAME:-unknown}" \
  '{name: $n, mode: "task", lane: "curation", idempotencyKey: $k, data: {date: $d, run: $r}}')" || {
  say "could not build the request; nothing was sent"
  exit 2
}

attempt=1
while :; do
  found="$(today)"
  t=$?
  case "$t" in
    0)
      if [ "$attempt" -eq 1 ]; then
        say "a session for ${day} exists (${found}); nothing to do"
      else
        say "a session for ${day} exists after an unconfirmed attempt (${found})"
      fi
      exit 0 ;;
    2)
      say "refused reading this caller's sessions (the client's line above); no create for ${day}"
      exit 2 ;;
    4)
      say "session list has malformed entries; no create for ${day}"
      exit 3 ;;
    1)
      out="$(printf '%s' "$req" | "$CLIENT" create)"
      rc=$?
      case "$rc" in
        0)
          created_name="$(printf '%s' "$out" | jq -r 'if (.name | type) == "string" then .name else empty end' 2>/dev/null)"
          if [ -n "$created_name" ] && [ "$created_name" != "$name" ]; then
            say "duplicate start for ${day}: got ${created_name}; no retry"
            exit 4
          fi
          created="$(printf '%s' "$out" | jq -r 'if (.name | type) == "string" and .name != "" then "\(.name) \(.outcome.state // .phase // "pending")" else empty end' 2>/dev/null)"
          if [ -n "$created" ]; then
            say "session ${created}"
            exit 0
          fi
          say "the API answered without naming a session; unconfirmed (attempt ${attempt} of ${ATTEMPTS})" ;;
        2)
          say "create refused (the client's line above); no retry for ${day}"
          exit 2 ;;
        *)
          say "create unconfirmed (attempt ${attempt} of ${ATTEMPTS})" ;;
      esac ;;
    *)
      say "could not read this caller's sessions; no create on this attempt (attempt ${attempt} of ${ATTEMPTS})" ;;
  esac
  [ "$attempt" -lt "$ATTEMPTS" ] || break
  attempt=$((attempt + 1))
  sleep "$PAUSE"
done

found="$(today)"
t=$?
case "$t" in
  0)
    say "a session for ${day} exists after an unconfirmed attempt (${found})"
    exit 0 ;;
  2)
    say "refused reading this caller's sessions (the client's line above); no further create for ${day}"
    exit 2 ;;
  4)
    say "session list has malformed entries; no create for ${day}"
    exit 3 ;;
esac
say "no session for ${day} confirmed after ${ATTEMPTS} attempts"
exit 3
