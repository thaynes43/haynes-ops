#!/usr/bin/env bash
# Self-test for the cigar-journal credential-expiry CronJob.
#
# WHY THIS EXISTS AND WHY IT LOOKS LIKE THIS
# ------------------------------------------
# The first cut of that CronJob shipped with `psql ... -c "... :'cid' ..."`.
# psql interpolates :'var' in its own lexer, which -c bypasses, so every run
# would have died with `syntax error at or near ":"` and reported
# status=db-unreachable — a monitor that pages every morning and never once
# looks at a credential. It was tested "through all 11 branches" with a STUBBED
# psql, which is exactly why the suite missed it: a stub cannot have psql's
# argv semantics. So this harness has two rules:
#
#   1. The scripts under test are EXTRACTED FROM THE MANIFEST with yq. Nothing
#      is copy-pasted, so the test cannot drift from what the cluster runs.
#   2. The database half runs the REAL psql binary with the REAL argv, against
#      the live cigar_journal database (read-only SELECTs). Since this pod has
#      no psql, the runner is piped into the CNPG primary via `kubectl exec -i`,
#      which is where a real psql lives.
#
# The only edit made to the extracted scripts is the `WORK=/work` line, which is
# repointed at a temp dir because /work is an emptyDir that only exists inside
# the Job. The psql/curl argv is executed verbatim.
#
# The GitHub half stubs `curl` (there is no way to conjure a token of each shape
# on demand) but stubs it at the HTTP boundary only: the header parsing, the
# token-shape discrimination and the status mapping all run for real. Set
# SELFTEST_GITHUB_TOKEN_FILE to also run the probe unstubbed against
# api.github.com with a real token.
#
# Usage:
#   scripts/credential-expiry-selftest.sh
#   SELFTEST_GITHUB_TOKEN_FILE=/creds/gh_token scripts/credential-expiry-selftest.sh
#
# Requires: yq, kubectl (cluster access), curl, bash 4+.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${MANIFEST:-$REPO_ROOT/kubernetes/main/apps/frontend/cigar-journal/app/credential-expiry-cronjob.yaml}"
PG_NAMESPACE="${PG_NAMESPACE:-database}"
PG_CLUSTER="${PG_CLUSTER:-postgres16}"
PG_DB="${PG_DB:-cigar_journal}"
# CNPG puts the unix socket here, not in /var/run/postgresql.
PG_SOCKET_DIR="${PG_SOCKET_DIR:-/controller/run}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
pass=0
fail=0

ok()      { pass=$((pass + 1)); printf '  PASS  %s\n' "$1"; }
bad()     { fail=$((fail + 1)); printf '  FAIL  %s\n' "$1"; printf '        %s\n' "$2"; }
section() { printf '\n== %s ==\n' "$1"; }

# ── extract, do not copy ──────────────────────────────────────────────────────
yq '.spec.jobTemplate.spec.template.spec.initContainers[] | select(.name == "pat") | .command[2]' \
  "$MANIFEST" > "$TMP/pat.sh"
yq '.spec.jobTemplate.spec.template.spec.containers[] | select(.name == "expiry") | .command[2]' \
  "$MANIFEST" > "$TMP/db.sh"
[ -s "$TMP/pat.sh" ] && [ -s "$TMP/db.sh" ] || { echo "extraction from $MANIFEST failed" >&2; exit 2; }
sh -n "$TMP/pat.sh" && bash -n "$TMP/db.sh"

# The scripts declare their emptyDir once as `WORK=/work`; repointing that single
# line is the whole edit. Revisions that predate it (or a future one that drops
# it) fall back to a global path rewrite, so this harness can still be pointed at
# an older manifest to demonstrate a regression.
if grep -q "^WORK=/work" "$TMP/db.sh"; then
  WORK_SED='s#^WORK=/work.*#WORK=%s#'
else
  echo "  note: no 'WORK=/work' line; falling back to a global /work rewrite"
  WORK_SED='s#/work#%s#g'
fi
work_sed() { printf "$WORK_SED" "$1"; }

##############################################################################
# PHASE 1 — the GitHub probe (init container), locally, real curl binary for
# the live case and a stub for the HTTP shapes we cannot mint on demand.
#
# Asserts the VERDICT FILE the init container hands to the main container.
##############################################################################
section "phase 1: GitHub probe -> /work/pat-status"

mkdir -p "$TMP/stubbin"
cat > "$TMP/stubbin/curl" <<'STUB'
#!/usr/bin/env bash
# Stub at the HTTP boundary only: writes STUB_HEADERS to the file named by -D
# and prints STUB_CODE, exactly as `curl -w '%{http_code}'` would. STUB_CODE=000
# means "curl itself failed", so exit non-zero and print nothing.
dump=""
while [ $# -gt 0 ]; do
  case "$1" in -D) dump="$2"; shift 2 ;; *) shift ;; esac
done
[ -n "$dump" ] && printf '%b' "${STUB_HEADERS:-}" > "$dump"
[ "${STUB_CODE:-200}" = "000" ] && exit 7
printf '%s' "${STUB_CODE:-200}"
STUB
chmod +x "$TMP/stubbin/curl"

EXP_DATE="$(date -u -d '+400 days' '+%Y-%m-%d %H:%M:%S UTC')"
HDR_OK="HTTP/2 200\r\ngithub-authentication-token-expiration: ${EXP_DATE}\r\nserver: github.com\r\n"
# curl --retry appends every attempt's headers to the -D dump, so a retried blip
# yields two matching lines. Only the last may reach `date -d`.
HDR_RETRIED="HTTP/2 500\r\ngithub-authentication-token-expiration: 2020-01-01 00:00:00 UTC\r\n\r\n${HDR_OK}"
HDR_NONE="HTTP/2 200\r\nserver: github.com\r\nx-ratelimit-limit: 5300\r\n"

probe_case() { # name token code headers expected-status-file
  local name="$1" token="$2" code="$3" headers="$4" want="$5" got
  local w="$TMP/w-probe"
  rm -rf "$w"; mkdir -p "$w"
  sed "$(work_sed "$w")" "$TMP/pat.sh" > "$w/pat.sh"
  ( export PATH="$TMP/stubbin:$PATH" PAT_PROBE_REPO="thaynes43/cigar-journal" \
      STUB_CODE="$code" STUB_HEADERS="$headers"
    if [ -n "$token" ]; then export RELEASE_PLEASE_TOKEN="$token"; else unset RELEASE_PLEASE_TOKEN; fi
    sh "$w/pat.sh" >/dev/null 2>&1 ) || true
  got="$(cat "$w/pat-status" 2>/dev/null || echo '<no file>')"
  if [ "$got" = "$want" ]; then ok "$name"; else bad "$name" "want [$want] got [$got]"; fi
}

probe_case "no token configured -> skip"                ""                     200 ""              skip
probe_case "200 + expiry header -> the date"            "github_pat_STUB"      200 "$HDR_OK"       "$EXP_DATE"
probe_case "200 + retried dump -> LAST header only"     "github_pat_STUB"      200 "$HDR_RETRIED"  "$EXP_DATE"
probe_case "200, no header, fine-grained PAT -> none"   "github_pat_STUB"      200 "$HDR_NONE"     none
probe_case "200, no header, classic PAT -> none"        "ghp_STUB"             200 "$HDR_NONE"     none
# The regression the reviewer caught: a GitHub App installation token returns
# 200 with no expiry header and dies within the hour. Reported green before.
probe_case "200, no header, App token -> ephemeral"     "ghs_STUB"             200 "$HDR_NONE"     ephemeral
probe_case "200, no header, user-to-server -> ephemeral" "ghu_STUB"            200 "$HDR_NONE"     ephemeral
probe_case "200, no header, unknown shape -> fail closed" "wat_STUB"           200 "$HDR_NONE"     unknown-shape
probe_case "401 -> unauthorized"                        "github_pat_STUB"      401 ""              unauthorized
probe_case "curl failure -> unreachable"                "github_pat_STUB"      000 ""              unreachable

if [ -n "${SELFTEST_GITHUB_TOKEN_FILE:-}" ] && [ -r "${SELFTEST_GITHUB_TOKEN_FILE}" ]; then
  # Unstubbed: real curl, real api.github.com, a real token. Which verdict is
  # correct depends on the token supplied, so this asserts only that the probe
  # produced a verdict the main container knows how to map, and prints it.
  w="$TMP/w-live"; mkdir -p "$w"
  sed "$(work_sed "$w")" "$TMP/pat.sh" > "$w/pat.sh"
  ( export PAT_PROBE_REPO="thaynes43/cigar-journal"
    RELEASE_PLEASE_TOKEN="$(cat "$SELFTEST_GITHUB_TOKEN_FILE")"; export RELEASE_PLEASE_TOKEN
    sh "$w/pat.sh" ) || true
  got="$(cat "$w/pat-status" 2>/dev/null || echo '<no file>')"
  case "$got" in
    skip|none|ephemeral|unknown-shape|unauthorized|unreachable|20*)
      ok "live api.github.com probe -> [$got]" ;;
    *)
      bad "live api.github.com probe" "unmappable verdict [$got]" ;;
  esac
else
  printf '  SKIP  live api.github.com probe (set SELFTEST_GITHUB_TOKEN_FILE)\n'
fi

##############################################################################
# PHASE 2 — the main container, with a REAL psql and the REAL argv, against the
# live database. This is the phase a stubbed psql cannot substitute for.
##############################################################################
section "phase 2: expiry script against real psql / live $PG_DB"

PG_POD="${PG_POD:-$(kubectl get cluster -n "$PG_NAMESPACE" "$PG_CLUSTER" -o jsonpath='{.status.currentPrimary}')}"
[ -n "$PG_POD" ] || { echo "could not resolve the $PG_CLUSTER primary" >&2; exit 2; }
echo "  primary: $PG_NAMESPACE/$PG_POD"

# Real fixtures, chosen from live data so the filters (revoked_at, expires_at)
# are exercised against real rows rather than a stub's canned output.
LIVE_CLIENT="${LIVE_CLIENT:-d784af277ef8376ed9b621d8f0b5ffac}"   # dev-env-cli, has a live token
# A registered client with zero live tokens -> the `expired` branch. Picked at
# run time: which clients are dormant changes as the estate is used.
DEAD_CLIENT="$(kubectl exec -i -n "$PG_NAMESPACE" "$PG_POD" -c postgres -- \
  psql -X -At -d "$PG_DB" <<SQL || true
SELECT c.client_id FROM oauth_client c
 WHERE NOT EXISTS (SELECT 1 FROM oauth_access_token t
                    WHERE t.client_id = c.client_id
                      AND t.revoked_at IS NULL AND t.expires_at > now())
 ORDER BY c.client_id LIMIT 1;
SQL
)"

runner="$TMP/runner.sh"
{
  echo 'set -u'
  echo 'W=$(mktemp -d); trap "rm -rf $W" EXIT'
  echo "DB_B64='$(base64 -w0 "$TMP/db.sh")'"
  echo 'echo "$DB_B64" | base64 -d > "$W/db.sh"'
  # The one and only edit to the extracted script.
  printf 'sed -i "%s" "$W/db.sh"\n' "$(work_sed '$W')"
  echo 'command -v psql >/dev/null || { echo "NO REAL PSQL IN THIS CONTAINER" >&2; exit 2; }'
  echo 'psql --version | sed "s/^/  /"'
  cat <<'RUNNER'
# run_case <label> <pat-status> <client> <lead> <dsn>
run_case() {
  printf 'CASE\t%s\n' "$1"
  printf '%s' "$2" > "$W/pat-status"
  ( export OAUTH_CLIENT_ID="$3" OAUTH_LEAD_DAYS="$4" PAT_LEAD_DAYS=14 DATABASE_URL="$5"
    bash "$W/db.sh" ) 2>&1 | sed 's/^/OUT\t/'
  printf 'EXIT\t%s\n' "${PIPESTATUS[0]}"
}
RUNNER
  echo "GOOD_DSN='postgresql:///${PG_DB}?host=${PG_SOCKET_DIR}'"
  echo "BAD_DSN='postgresql://127.0.0.1:1/nope?connect_timeout=3'"
  # lead 0 / lead 99999 rather than a hardcoded day count, so the assertions do
  # not rot as the real token counts down.
  echo "run_case oauth-ok           skip '$LIVE_CLIENT' 0     \"\$GOOD_DSN\""
  echo "run_case oauth-expiring     skip '$LIVE_CLIENT' 99999 \"\$GOOD_DSN\""
  echo "run_case oauth-client-missing skip '00000000000000000000000000000000' 7 \"\$GOOD_DSN\""
  [ -n "$DEAD_CLIENT" ] && echo "run_case oauth-expired skip '$DEAD_CLIENT' 7 \"\$GOOD_DSN\""
  echo "run_case oauth-db-unreachable skip '$LIVE_CLIENT' 7 \"\$BAD_DSN\""
  for st in skip none ephemeral unknown-shape unauthorized unreachable; do
    echo "run_case pat-$st '$st' '$LIVE_CLIENT' 0 \"\$GOOD_DSN\""
  done
  echo "run_case pat-ok '$(date -u -d '+400 days' '+%Y-%m-%d %H:%M:%S UTC')' '$LIVE_CLIENT' 0 \"\$GOOD_DSN\""
  echo "run_case pat-expiring '$(date -u -d '+3 days' '+%Y-%m-%d %H:%M:%S UTC')' '$LIVE_CLIENT' 0 \"\$GOOD_DSN\""
  echo "run_case pat-garbage 'not-a-date' '$LIVE_CLIENT' 0 \"\$GOOD_DSN\""
} > "$runner"

kubectl exec -i -n "$PG_NAMESPACE" "$PG_POD" -c postgres -- bash -s < "$runner" > "$TMP/out.txt" 2>"$TMP/err.txt" || {
  echo "runner failed inside $PG_POD:"; cat "$TMP/err.txt"; exit 2; }
sed -n '1,2p' "$TMP/out.txt"

# expect <case> <substring the OUT lines must contain> <exit code>
expect() {
  local case="$1" want="$2" want_exit="$3" block got_exit
  block="$(awk -v c="$case" '$0=="CASE\t"c{f=1;next} /^CASE\t/{f=0} f' "$TMP/out.txt")"
  if [ -z "$block" ]; then bad "$case" "no output block (case did not run)"; return; fi
  got_exit="$(printf '%s\n' "$block" | awk -F'\t' '$1=="EXIT"{print $2}')"
  if printf '%s\n' "$block" | grep -qE "$want" && [ "$got_exit" = "$want_exit" ]; then
    ok "$case  ($want, exit $got_exit)"
  else
    bad "$case" "want [$want] exit $want_exit; got exit $got_exit in: $(printf '%s' "$block" | tr '\n' '|')"
  fi
}

# The blocker regression guard: with the -c form these three report
# db-unreachable and exit 1 instead of reading the credential at all.
O='credential=dev-env-cli days_left=[0-9-]+ lead=[0-9]+ status='
P='credential=RELEASE_PLEASE_TOKEN days_left=[0-9-]+ lead=[0-9]+ status='

expect oauth-ok             "${O}ok"                    0
expect oauth-expiring       "${O}expiring"              1
expect oauth-client-missing "${O}client-missing"        1
if [ -n "$DEAD_CLIENT" ]; then
  expect oauth-expired      "${O}expired"               1
else
  printf '  SKIP  oauth-expired (no registered client without a live token today)\n'
fi
expect oauth-db-unreachable "${O}db-unreachable"        1
expect oauth-db-unreachable '\[psql\] '                 1

expect pat-skip             "${P}not-configured"        0
expect pat-none             "${P}no-expiry"             0
expect pat-ephemeral        "${P}ephemeral-token"       1
expect pat-unknown-shape    "${P}unknown-token-shape"   1
expect pat-unauthorized     "${P}unauthorized"          1
expect pat-unreachable      "${P}uncheckable"           1
expect pat-ok               "${P}ok"                    0
expect pat-expiring         "${P}expiring"              1
expect pat-garbage          "${P}unparseable-expiry"    1

##############################################################################
# PHASE 3 — the aggregate. dev-env-cli happens to have exactly one live row
# today, so no live-data case can tell min() from max(). This runs the SAME SQL
# TEXT the Job runs, with the two tables swapped for a VALUES fixture holding
# two unrevoked live rows 29 days apart — which is the state prod reaches the
# moment a token is re-minted, since cigar-journal never revokes the superseded
# row. min() answers ~0 days (and keeps paging for a month after the problem was
# fixed); max() answers ~29.
##############################################################################
section "phase 3: newest live token wins (max, not min)"

awk '/if ! row=/{g=1} g && /SELECT/{f=1} f{print} f && /\);/{exit}' "$TMP/db.sh" \
  | sed 's/);.*/);/' > "$TMP/agg.sql"
sed -i \
  -e "s#FROM oauth_client#FROM (VALUES (:'cid')) AS oauth_client(client_id)#" \
  -e "s#FROM oauth_access_token#FROM (VALUES (:'cid', now() + interval '1 day', NULL::timestamptz), (:'cid', now() + interval '30 days', NULL::timestamptz)) AS oauth_access_token(client_id, expires_at, revoked_at)#" \
  "$TMP/agg.sql"

agg_row="$(kubectl exec -i -n "$PG_NAMESPACE" "$PG_POD" -c postgres -- \
  psql -X -At -F '|' -v ON_ERROR_STOP=1 -v cid=fixture -d "$PG_DB" < "$TMP/agg.sql" 2>&1)" || agg_row="ERROR: $agg_row"
agg_days=$(( ${agg_row##*|} / 86400 )) 2>/dev/null || agg_days=-1
if [ "$agg_days" -ge 20 ]; then
  ok "two live rows 29d apart -> days_left=$agg_days (newest)"
else
  bad "two live rows 29d apart" "days_left=$agg_days (oldest row won; expected the newest). raw: $agg_row"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
