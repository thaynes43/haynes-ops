#!/usr/bin/env bash
# The function under test arrives via eval, so shellcheck cannot see it call the stubs
# (SC2329) or read the variables set here (SC2034); '$…' strings are literal (SC2016).
# shellcheck disable=SC2016,SC2034,SC2329
# Self-test for the shepherd pre-filter's vet-marker check (story 549,
# https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/549).
#
# WHY THIS EXISTS
# ---------------
# The pre-filter skips the paid LLM for a ramp PR whose current head already carries a
# `<!-- shepherd-vet sha=<head> -->` marker. The repository is public, and before story 549
# a marker counted from any comment author: anyone could stop the shepherd vetting a PR by
# pasting one. A marker now counts only from the shepherd's own app, read over REST
# (user.login haynes-ops-bot[bot], type Bot), first in the body. When the comments cannot be
# read, no marker counts and the LLM runs (the safe direction). Removing any guard in
# run-shepherd.sh fails a case here:
#   1. every one of the 64 genuine markers on record (fixtures/vet-markers-20261112.json,
#      read from GitHub on 2026-11-12) still counts for its own head: no change for them;
#   2. forged copies do not count: the same body by the plain user haynes-ops-bot (what
#      GraphQL reports for the real bot too), by another app, by a non-Bot account with the
#      bot's login, quoted inside a longer comment, or for another head;
#   3. an unreadable comment list counts nothing and is logged; a marker on a later page
#      counts; a forged marker next to a genuine one does not hide it;
#   4. the PR view no longer asks GraphQL for comments, and a PR off the ramp reads none.
# The function is EXTRACTED from the shipped script, never copied. Stubbed: log, gh.
#
# Usage: scripts/upgrade-agent/shepherd-prefilter-selftest.sh
# Requires: bash 4+, jq. No cluster or GitHub access.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN="${RUN:-$REPO_ROOT/kubernetes/main/apps/upgrade-agent/shepherd/app/resources/run-shepherd.sh}"
FX="$REPO_ROOT/scripts/upgrade-agent/fixtures/vet-markers-20261112.json"
command -v jq >/dev/null 2>&1 || { echo "FATAL: jq is required"; exit 1; }

extract() {  # $1=file $2=function name — its definition through the first column-0 '}'
  awk -v fn="$2" '
    $0 ~ "^" fn "\\(\\) \\{" { inside = 1 }
    inside { print }
    inside && /^}$/ { exit }
  ' "$1"
}
body="$(extract "$RUN" prefilter_should_run)"
case "$body" in *"prefilter_should_run() {"*"}") ;; *) echo "FATAL: could not extract prefilter_should_run() from $RUN"; exit 1 ;; esac
eval "$body" || { echo "FATAL: extracted prefilter_should_run() does not parse"; exit 1; }
defaults="$(grep -E '^SHEPHERD_BOT_LOGIN=' "$RUN")"
[ "$(printf '%s\n' "$defaults" | wc -l)" -eq 1 ] || { echo "FATAL: SHEPHERD_BOT_LOGIN default not found in $RUN"; exit 1; }
unset UPGRADE_AGENT_BOT_LOGIN
eval "$defaults"
REPO="thaynes43/haynes-ops"

fails=0; passes=0
ok()   { passes=$((passes+1)); }
bad()  { printf 'FAIL  %s: %s\n' "$1" "$2"; fails=$((fails+1)); }
expect() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "got '$2', want '$3'"; fi; }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
log() { printf '%s\n' "$*" >> "$T/log"; }

# The stub world: one open Renovate PR (#100) whose files touch the ramp glob, at head
# $HEAD; its REST comments are the file $T/comments (raw `gh api --paginate` output: one
# JSON array per page). COMMENTS_FAIL=1 makes the comment read fail.
RAMP="kubernetes/main/apps/network/traefik"
FILES='[{"path":"kubernetes/main/apps/network/traefik/app/helmrelease.yaml"}]'
HEAD=""; COMMENTS_FAIL=0
gh() {
  printf '%s\n' "$*" >> "$T/calls"
  if [ "$1 $2" = "pr list" ]; then printf '100\n'; return 0; fi
  if [ "$1 $2" = "pr view" ]; then printf '{"files":%s,"headRefOid":"%s"}' "$FILES" "$HEAD"; return 0; fi
  if [ "$1" = "api" ]; then
    [ "$COMMENTS_FAIL" = 1 ] && { echo 'gh: Server Error (HTTP 502)' >&2; return 1; }
    cat "$T/comments"; return 0
  fi
  echo "unexpected gh call: $*" >&2; return 1
}
# run the pre-filter; prints "llm" (return 0: summon) or "skip" (return 1: vetted)
decide() { : > "$T/log"; : > "$T/calls"; if prefilter_should_run "$RAMP" >/dev/null 2>&1; then echo llm; else echo skip; fi; }
page() { jq -c "$1" "$FX" > "$T/comments"; }   # one page built from the fixture

# ── 1. every genuine marker on record still counts for its own head ──
n="$(jq '.comments | length' "$FX")"
[ "$n" -eq 64 ] || { echo "FATAL: the fixture has $n genuine markers, not 64"; exit 1; }
counted=0
for i in $(seq 0 $((n - 1))); do
  HEAD="$(jq -r ".comments[$i].body | capture(\"^<!-- shepherd-vet sha=(?<s>[0-9a-f]{40}) -->\").s" "$FX")"
  page "[.comments[$i]]"
  [ "$(decide)" = skip ] && counted=$((counted+1)) || bad "genuine marker $(jq -r ".comments[$i] | \"#\(.pr) comment \(.id)\"" "$FX")" "not counted"
done
expect "1: all 64 genuine markers (haynes-ops-bot[bot], Bot) count for their own head" "$counted" "64"
expect "1: the bot's REST login is SHEPHERD_BOT_LOGIN plus [bot]" "${SHEPHERD_BOT_LOGIN}[bot]" "haynes-ops-bot[bot]"

# ── 2. forged copies do not count ──
g0='.comments[0]'
HEAD="$(jq -r "$g0.body | capture(\"^<!-- shepherd-vet sha=(?<s>[0-9a-f]{40}) -->\").s" "$FX")"
page "[$g0]"; expect "2: control: the first genuine marker counts" "$(decide)" "skip"
page "[$g0 | .user = {login: \"haynes-ops-bot\", type: \"User\", id: 1}]"
expect "2: the same body by the user haynes-ops-bot (GraphQL's name for the bot) does not count" "$(decide)" "llm"
page "[$g0 | .user = {login: \"github-actions[bot]\", type: \"Bot\", id: 41898282}]"
expect "2: the same body by another app does not count" "$(decide)" "llm"
page "[$g0 | .user.type = \"User\"]"
expect "2: the bot's login on a non-Bot account does not count" "$(decide)" "llm"
page "[$g0 | .user = {login: \"Haynes-Ops-Bot[bot]\", type: \"Bot\", id: 2}]"
expect "2: a login that differs in case does not count" "$(decide)" "llm"
page "[$g0 | del(.user)]"
expect "2: a comment with no author does not count" "$(decide)" "llm"
page "[$g0 | .body = (\"Quoting the shepherd:\n\" + .body)]"
expect "2: the bot's marker quoted inside a longer comment does not count" "$(decide)" "llm"
page "[$g0 | .body = (\"  \" + .body)]"
expect "2: a marker not first in the body does not count" "$(decide)" "llm"
HEAD="$(printf 'a%.0s' $(seq 40))"
page "[$g0]"; expect "2: a genuine marker for another head does not count" "$(decide)" "llm"

# ── 3. reads ──
HEAD="$(jq -r "$g0.body | capture(\"^<!-- shepherd-vet sha=(?<s>[0-9a-f]{40}) -->\").s" "$FX")"
page "[$g0]"; COMMENTS_FAIL=1
expect "3: comments unreadable: no marker counts (summon the LLM)" "$(decide)" "llm"
grep -q "pre-filter: PR #100 comments unreadable" "$T/log" && ok "3: the unreadable comments are logged" || bad "3: the unreadable comments are logged" "$(cat "$T/log")"
COMMENTS_FAIL=0
{ jq -c "[$g0 | .id = 1 | .user = {login: \"haynes-ops-bot\", type: \"User\", id: 1}]" "$FX"; jq -c "[$g0]" "$FX"; } > "$T/comments"
expect "3: a genuine marker on the second page counts, next to a forged one" "$(decide)" "skip"
echo '[]' > "$T/comments"
expect "3: no comments: not vetted" "$(decide)" "llm"
grep -q "^api --paginate repos/thaynes43/haynes-ops/issues/100/comments?per_page=100$" "$T/calls" && ok "3: comments are read over REST, paginated" || bad "3: comments are read over REST, paginated" "$(cat "$T/calls")"

# ── 4. wiring ──
page "[$g0]"; decide >/dev/null
grep -q "^pr view 100 -R thaynes43/haynes-ops --json files,headRefOid$" "$T/calls" && ok "4: the PR view asks for files and head only (no GraphQL comments)" || bad "4: the PR view asks for files and head only" "$(cat "$T/calls")"
FILES='[{"path":"kubernetes/main/apps/media/plex/app/helmrelease.yaml"}]'
expect "4: a PR off the ramp is not a summon reason" "$(decide)" "skip"
grep -q "^api " "$T/calls" && bad "4: a PR off the ramp reads no comments" "$(cat "$T/calls")" || ok "4: a PR off the ramp reads no comments"
grep -q 'contains($m)' "$RUN" && bad "4: the old any-author contains() check is gone" "still in run-shepherd.sh" || ok "4: the old any-author contains() check is gone"

echo
echo "$passes passed, $fails failed"
[ "$fails" -eq 0 ]
