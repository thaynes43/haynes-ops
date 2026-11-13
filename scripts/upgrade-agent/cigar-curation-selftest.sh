#!/usr/bin/env bash
# Self-test for summon.sh, the daily cigar-catalog curation's v2 caller (dev-env
# DESIGN-064 6.11, OpenProject #746 slice a, story 805).
#
# WHY THIS EXISTS
# ---------------
# summon.sh asks the dev-env v2 operator API for one summoned task session a day. A
# wrong answer to an API reply starts a second curation run on the shared catalog, or
# reads a refusal as "already started" and silently skips a day. So each reply has to
# lead where the script's header says:
#   201 or 200 naming a session  -> exit 0, no retry;
#   a session of today exists    -> exit 0 before any create;
#   403, 409, 422, 429           -> exit 2, one create, no retry that day;
#   503 (unconfirmed)            -> re-read the list, then retry with the same key;
#   still unconfirmed            -> exit 3 after three attempts.
# The token must never reach a log, and the worst case must fit the Job's
# activeDeadlineSeconds.
#
# It runs the SHIPPED summon.sh and the SHIPPED client (dev-env-api.sh) against a fake
# HTTPS server (python3, a throwaway self-signed certificate made with openssl) that
# answers from a per-case script. `date` and `sleep` are shims on PATH, so the date is
# fixed and the 60-second pauses are recorded, not slept. Nothing in summon.sh exists
# only for this test.
#
# Usage: scripts/upgrade-agent/cigar-curation-selftest.sh
# Requires: bash, curl, jq, openssl, python3. No cluster access, no network beyond
# 127.0.0.1.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APPS="$REPO_ROOT/kubernetes/main/apps/upgrade-agent"
SUMMON="$APPS/cigar-curation/app/resources/summon.sh"
CLIENT="$APPS/health-gate/app/resources/dev-env-api.sh"
HELMRELEASE="$APPS/cigar-curation/app/helmrelease.yaml"
for f in "$SUMMON" "$CLIENT" "$HELMRELEASE"; do
  [ -r "$f" ] || { echo "FATAL: cannot read $f"; exit 1; }
done
for t in curl jq openssl python3; do
  command -v "$t" >/dev/null 2>&1 || { echo "FATAL: $t is not installed"; exit 1; }
done

WORK="$(mktemp -d)"
SERVER_PID=""
cleanup() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

# ── fixtures: a fake token, a certificate for 127.0.0.1, the server ─────────────────
TOKEN="selftest-token-$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"
printf '%s\n' "$TOKEN" > "$WORK/token"
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 1 \
  -subj /CN=localhost -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
  -keyout "$WORK/key.pem" -out "$WORK/ca.crt" >/dev/null 2>&1 \
  || { echo "FATAL: openssl could not make the test certificate"; exit 1; }

# The server answers from $WORK/script.json: {"GET": [reply...], "POST": [reply...]},
# each reply {"status": N, "body": <JSON>}. The Nth request of a method gets the Nth
# reply, and the last reply repeats. It records every request as one JSON line in
# $WORK/requests (method, path, whether Authorization carried the token, the body).
cat > "$WORK/server.py" <<'PY'
import http.server, json, os, ssl, sys
work = sys.argv[1]
token = open(os.path.join(work, "token")).read().strip()

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        auth = self.headers.get("Authorization", "")
        path = os.path.join(work, "requests")
        seen = 0
        if os.path.exists(path):
            with open(path) as f:
                seen = sum(1 for line in f if json.loads(line)["method"] == self.command)
        with open(path, "a") as f:
            f.write(json.dumps({"method": self.command, "path": self.path,
                                "auth_ok": auth == "Bearer " + token, "body": body}) + "\n")
        with open(os.path.join(work, "script.json")) as f:
            replies = json.load(f).get(self.command) or [{"status": 500, "body": {}}]
        r = replies[min(seen, len(replies) - 1)]
        out = json.dumps(r.get("body", {})).encode()
        self.send_response(r["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = do_DELETE = handle_any

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
srv.daemon_threads = True
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(os.path.join(work, "ca.crt"), os.path.join(work, "key.pem"))
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
with open(os.path.join(work, "port.tmp"), "w") as f:
    f.write(str(srv.server_address[1]))
os.rename(os.path.join(work, "port.tmp"), os.path.join(work, "port"))
srv.serve_forever()
PY
printf '%s' '{"GET":[{"status":200,"body":{"sessions":[]}}]}' > "$WORK/script.json"
python3 -I "$WORK/server.py" "$WORK" &
SERVER_PID=$!
for _ in $(seq 1 50); do [ -s "$WORK/port" ] && break; sleep 0.1; done
[ -s "$WORK/port" ] || { echo "FATAL: the fake server did not start"; exit 1; }
PORT="$(cat "$WORK/port")"

# ── shims: a fixed UTC date, and a sleep that records instead of sleeping ───────────
DAY=20261114
YESTERDAY=20261113
REAL_DATE="$(command -v date)"
mkdir -p "$WORK/shim"
cat > "$WORK/shim/date" <<EOF
#!/usr/bin/env bash
if [ "\$*" = "-u +%Y%m%d" ]; then echo $DAY; exit 0; fi
exec "$REAL_DATE" "\$@"
EOF
cat > "$WORK/shim/sleep" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$WORK/sleeps"
EOF
chmod 0755 "$WORK/shim/date" "$WORK/shim/sleep"

# The shipped files, copied with the modes their ConfigMap mounts give them (0555).
mkdir -p "$WORK/bin"
cp "$CLIENT" "$WORK/bin/dev-env-api.sh" && chmod 0555 "$WORK/bin/dev-env-api.sh"
cp "$SUMMON" "$WORK/bin/summon.sh" && chmod 0555 "$WORK/bin/summon.sh"

# A .curlrc that would turn on curl's verbose and trace output if curl read it.
mkdir -p "$WORK/curlhome"
printf 'verbose\ntrace-ascii = "%s"\n' "$WORK/curl-trace" > "$WORK/curlhome/.curlrc"

# Every run sees only these; nothing from the runner's own environment leaks in.
export DEV_ENV_API_URL="https://127.0.0.1:${PORT}"
export DEV_ENV_API_TOKEN_FILE="$WORK/token"
export DEV_ENV_API_CA_FILE="$WORK/ca.crt"
export DEV_ENV_API_CONNECT_TIMEOUT=2
export DEV_ENV_API_CLIENT="$WORK/bin/dev-env-api.sh"
unset DEV_ENV_API_MAX_TIME
POD="cigar-curation-29400615-x7k2q"

fails=0
ok() { echo "ok    $1"; }
bad() { echo "FAIL  $1"; fails=$((fails + 1)); }
ALL_OUT="$WORK/all-output"
: > "$ALL_OUT"
OUT="$WORK/out"
RC=0
NAME="wo-cigar-curate-${DAY}"
KEY="cigar-curate-${DAY}"

# Replies used below.
EMPTY='{"status":200,"body":{"sessions":[]}}'
TODAY_RUNNING="{\"status\":200,\"body\":{\"sessions\":[{\"name\":\"$NAME\",\"idempotencyKey\":\"$KEY\",\"caller\":\"upgrade-agent.cigar-curation\",\"lane\":\"curation\",\"outcome\":{\"state\":\"running\"}}]}}"
TODAY_SUFFIX_DONE="{\"status\":200,\"body\":{\"sessions\":[{\"name\":\"$NAME-2\",\"idempotencyKey\":\"$KEY\",\"outcome\":{\"state\":\"done\"}}]}}"
TODAY_NAME_ONLY="{\"status\":200,\"body\":{\"sessions\":[{\"name\":\"$NAME\",\"outcome\":{\"state\":\"failed\"}}]}}"
YESTERDAY_DONE="{\"status\":200,\"body\":{\"sessions\":[{\"name\":\"wo-cigar-curate-$YESTERDAY\",\"idempotencyKey\":\"cigar-curate-$YESTERDAY\",\"outcome\":{\"state\":\"done\"}}]}}"
NO_LIST='{"status":200,"body":{"items":[]}}'
CREATED="{\"status\":201,\"body\":{\"name\":\"$NAME\",\"mode\":\"task\",\"lane\":\"curation\",\"outcome\":{\"state\":\"pending\"}}}"
REUSED="{\"status\":200,\"body\":{\"name\":\"$NAME\",\"mode\":\"task\",\"lane\":\"curation\",\"outcome\":{\"state\":\"running\"}}}"
NAMELESS='{"status":201,"body":{}}'
refusal() { printf '{"status":%s,"body":{"error":{"code":"%s","message":"%s"}}}' "$1" "$2" "$3"; }
R403="$(refusal 403 forbidden "summoning creates of kind task are not switched on: the operator's --summon-create-kinds does not name task (DESIGN-064 5.9)")"
R403_LIST="$(refusal 403 forbidden "upgrade-agent/cigar-curation may not call this API")"
R409="$(refusal 409 conflict "the idempotency key is held by a different request")"
R422="$(refusal 422 invalid "data.extra: not in the entry's brief.fields")"
R429="$(refusal 429 limit_exceeded "the caller's queued limit is reached")"
R503="$(refusal 503 unavailable "fake failure")"

# scenario NAME GET_REPLIES POST_REPLIES: the server's script for one run.
scenario() {
  printf '{"GET":[%s],"POST":[%s]}' "$2" "$3" > "$WORK/script.json"
  : > "$WORK/requests"
  : > "$WORK/sleeps"
  CASE="$1"
}
# run_summon EXPECTED_EXIT: the shipped summon.sh under bash -x, with xtrace also set
# through SHELLOPTS and a verbose .curlrc in CURL_HOME; stdout and stderr together.
run_summon() {
  env PATH="$WORK/shim:$PATH" HOSTNAME="$POD" SHELLOPTS=xtrace CURL_HOME="$WORK/curlhome" \
    bash -x "${SCRIPT:-$WORK/bin/summon.sh}" > "$OUT" 2>&1
  RC=$?
  cat "$OUT" >> "$ALL_OUT"
  if [ "$RC" -eq "$1" ]; then ok "$CASE: exit $RC"; else bad "$CASE: exit $RC, want $1 ($(tail -n 3 "$OUT" | tr '\n' ' '))"; fi
}
count() { jq -s --arg m "$1" '[.[] | select(.method == $m)] | length' "$WORK/requests"; }
n_posts() { local n; n="$(count POST)"; if [ "$n" = "$1" ]; then ok "$CASE: $1 create(s) sent"; else bad "$CASE: $n creates sent, want $1"; fi; }
n_gets() { local n; n="$(count GET)"; if [ "$n" = "$1" ]; then ok "$CASE: $1 list read(s)"; else bad "$CASE: $n list reads, want $1"; fi; }
pauses() { local p; p="$(tr '\n' ' ' < "$WORK/sleeps" | sed 's/ $//')"; if [ "$p" = "$1" ]; then ok "$CASE: pauses [${1}]"; else bad "$CASE: pauses [${p}], want [${1}]"; fi; }
says() { if grep -qF -- "$2" "$OUT"; then ok "$CASE: $1"; else bad "$CASE: $1 (output: $(tail -n 3 "$OUT" | tr '\n' ' '))"; fi; }
says_not() { if grep -qF -- "$2" "$OUT"; then bad "$CASE: $1"; else ok "$CASE: $1"; fi; }
order() {  # the methods of the requests, in order, as one word: GPGP...
  local o; o="$(jq -r '.method[0:1]' "$WORK/requests" | tr -d '\n')"
  if [ "$o" = "$1" ]; then ok "$CASE: request order $1"; else bad "$CASE: request order $o, want $1"; fi
}

# ── 1. 201: list, then one create with exactly the request 6.11 names ──────────────
scenario "201" "$EMPTY" "$CREATED"
run_summon 0
n_posts 1; order GP; pauses ""
says "logs the session it started" "cigar-curation: session $NAME pending"
says "the client logs the 201" "dev-env-api: POST /v1/sessions HTTP 201"
body="$(jq -s -r '[.[] | select(.method == "POST")][0].body' "$WORK/requests")"
if jq -e --arg n "$NAME" --arg k "$KEY" --arg d "$DAY" --arg r "$POD" '
    (keys == ["data","idempotencyKey","lane","mode","name"]) and .name == $n and .mode == "task"
    and .lane == "curation" and .idempotencyKey == $k and (.data | keys) == ["date","run"]
    and .data.date == $d and .data.run == $r' <<<"$body" >/dev/null 2>&1; then
  ok "201: the create carries name, mode task, lane curation, the date key and data {date, run} only"
else bad "201: the create body is not the one 6.11 names: $body"; fi
if jq -e 'has("prompt") or has("repo") or has("profile") or has("model")' <<<"$body" >/dev/null 2>&1; then
  bad "201: the create carries a field the policy fixes"
else ok "201: no prompt, repo, profile or model in the create"; fi
if jq -s -e 'all(.path == "/v1/sessions")' "$WORK/requests" >/dev/null; then ok "201: only /v1/sessions was called"; else bad "201: a path other than /v1/sessions was called"; fi

# ── 2. 200: a live session under the key is reused ──────────────────────────────────
scenario "200" "$EMPTY" "$REUSED"
run_summon 0
n_posts 1; pauses ""
says "logs the session it was given" "cigar-curation: session $NAME running"

# ── 3. a session of today exists before the create ──────────────────────────────────
scenario "exists by name and key" "$TODAY_RUNNING" "$CREATED"
run_summon 0
n_posts 0; n_gets 1
says "says there is nothing to do" "a session for $DAY exists ($NAME running); nothing to do"
scenario "exists by key, a -2 name, finished" "$TODAY_SUFFIX_DONE" "$CREATED"
run_summon 0
n_posts 0
says "a finished session of today is not re-run" "a session for $DAY exists ($NAME-2 done); nothing to do"
scenario "exists by name only, failed" "$TODAY_NAME_ONLY" "$CREATED"
run_summon 0
n_posts 0
scenario "yesterday's session does not count" "$YESTERDAY_DONE" "$CREATED"
run_summon 0
n_posts 1

# ── 4. refusals: exit 2, one create, no retry, no pause ─────────────────────────────
scenario "403 not switched on" "$EMPTY" "$R403"
run_summon 2
n_posts 1; n_gets 1; pauses ""
says "the client logs the 403" "dev-env-api: POST /v1/sessions HTTP 403"
says "the API's reason reaches the log" "summoning creates of kind task are not switched on"
says "says it will not retry" "create refused (the client's line above); no retry for $DAY"
scenario "409 is not already started" "$EMPTY" "$R409"
run_summon 2
n_posts 1; pauses ""
says_not "a 409 is never read as a session that exists" "cigar-curation: session "
says_not "a 409 is never read as nothing to do" "nothing to do"
scenario "422" "$EMPTY" "$R422"
run_summon 2
n_posts 1; pauses ""
scenario "429" "$EMPTY" "$R429"
run_summon 2
n_posts 1; pauses ""

# ── 5. unconfirmed: re-read the list, then retry with the same key ──────────────────
scenario "503 then 201" "$EMPTY" "$R503,$CREATED"
run_summon 0
n_posts 2; order GPGP; pauses "60"
if [ "$(jq -s -r '[.[] | select(.method == "POST") | .body] | unique | length' "$WORK/requests")" = 1 ]; then
  ok "503 then 201: the retry sends the same request, same key"
else bad "503 then 201: the retry changed the request"; fi
says "logs the session it started" "cigar-curation: session $NAME pending"
scenario "503 then the session exists" "$EMPTY,$TODAY_RUNNING" "$R503,$CREATED"
run_summon 0
n_posts 1; order GPG; pauses "60"
says "says the session exists after the unconfirmed attempt" "a session for $DAY exists after an unconfirmed attempt ($NAME running)"
scenario "503 three times" "$EMPTY" "$R503"
run_summon 3
n_posts 3; order GPGPGPG; pauses "60 60"
says "says nothing was confirmed" "no session for $DAY confirmed after 3 attempts"
scenario "503 three times, then the last read finds it" "$EMPTY,$EMPTY,$EMPTY,$TODAY_RUNNING" "$R503"
run_summon 0
n_posts 3; order GPGPGPG
scenario "a 201 that names no session is unconfirmed" "$EMPTY" "$NAMELESS,$CREATED"
run_summon 0
n_posts 2; order GPGP; pauses "60"
says "says the answer named no session" "the API answered without naming a session"

# ── 6. the list: refused, unreadable, or not a list ─────────────────────────────────
scenario "list refused" "$R403_LIST" "$CREATED"
run_summon 2
n_posts 0; pauses ""
says "says no create" "no create for $DAY"
scenario "list 503 then empty" "$R503,$EMPTY" "$CREATED"
run_summon 0
n_posts 1; order GGP; pauses "60"
says "no create on the unreadable attempt" "could not read this caller's sessions; no create on this attempt (attempt 1 of 3)"
scenario "a 200 without a sessions list is unreadable" "$NO_LIST" "$CREATED"
run_summon 3
n_posts 0; pauses "60 60"

# ── 7. refused before sending ───────────────────────────────────────────────────────
scenario "no client" "$EMPTY" "$CREATED"
DEV_ENV_API_CLIENT="$WORK/absent" run_summon 2
n_posts 0; n_gets 0
says "says the client is missing" "is missing (ConfigMap dev-env-api-client); nothing was sent"
scenario "no CA" "$EMPTY" "$CREATED"
DEV_ENV_API_CA_FILE="$WORK/absent" run_summon 2
n_posts 0; n_gets 0
scenario "no token" "$EMPTY" "$CREATED"
DEV_ENV_API_TOKEN_FILE="$WORK/absent" run_summon 2
n_posts 0; n_gets 0

# ── 8. the token: sent on every request, printed nowhere ────────────────────────────
scenario "token, all answers" "$EMPTY" "$R503,$CREATED"
run_summon 0
cat "$WORK/requests" >> "$WORK/all-requests"
for s in "$EMPTY" "$TODAY_RUNNING"; do
  scenario "token" "$s" "$R403"; run_summon "$([ "$s" = "$EMPTY" ] && echo 2 || echo 0)"
  cat "$WORK/requests" >> "$WORK/all-requests"
done
if jq -s -e 'length > 0 and all(.auth_ok)' "$WORK/all-requests" >/dev/null; then
  ok "every request carried Authorization: Bearer <the token>"
else bad "a request carried no token or a wrong one"; fi
if grep -qF -- "$TOKEN" "$ALL_OUT"; then bad "the token appears in summon.sh's output (xtrace and .curlrc verbose on)"
else ok "the token appears in no output of any run above, with xtrace and a verbose .curlrc"; fi
if [ -e "$WORK/curl-trace" ]; then bad "curl read the .curlrc (a trace file was written)"; else ok "curl read no .curlrc"; fi
if grep -q -- '^+' "$ALL_OUT"; then bad "xtrace printed a line: summon.sh did not switch it off"
else ok "xtrace printed nothing: summon.sh switches it off first"; fi
# Second layer: summon.sh with its set +x line removed, so xtrace stays on through the
# whole script. It never opens the token file, so the token is still in no line.
sed '/^{ set +x; } 2>\/dev\/null$/d' "$SUMMON" > "$WORK/bin/summon-xtrace.sh"
if cmp -s "$SUMMON" "$WORK/bin/summon-xtrace.sh"; then bad "could not remove the set +x line for the xtrace check"; fi
chmod 0555 "$WORK/bin/summon-xtrace.sh"
: > "$ALL_OUT"
scenario "forced xtrace" "$EMPTY" "$R503,$CREATED"
SCRIPT="$WORK/bin/summon-xtrace.sh" run_summon 0
if [ "$(grep -c -- '^+' "$OUT")" -gt 30 ]; then ok "forced xtrace traced summon.sh ($(grep -c -- '^+' "$OUT") lines)"
else bad "forced xtrace produced too few lines to prove anything"; fi
if grep -qF -- "$TOKEN" "$OUT"; then bad "the token appears in a forced xtrace line"; else ok "the token appears in no line of a forced xtrace"; fi

# ── 9. the worst case fits the Job's activeDeadlineSeconds ──────────────────────────
deadline="$(sed -n 's/^ *activeDeadlineSeconds: *\([0-9][0-9]*\).*/\1/p' "$HELMRELEASE")"
attempts="$(sed -n 's/^ATTEMPTS=\([0-9][0-9]*\)$/\1/p' "$SUMMON")"
pause="$(sed -n 's/^PAUSE=\([0-9][0-9]*\)$/\1/p' "$SUMMON")"
read_t="$(grep -c 'read_max_time 30)' "$CLIENT")"
create_t="$(grep -c 'read_max_time 130)' "$CLIENT")"
if [ -n "$deadline" ] && [ -n "$attempts" ] && [ -n "$pause" ] && [ "$read_t" -ge 1 ] && [ "$create_t" -eq 1 ]; then
  worst=$(( attempts * (30 + 130) + (attempts - 1) * pause + 30 ))
  # 120 seconds are left for the pod to be scheduled, pulled and started.
  if [ $(( worst + 120 )) -le "$deadline" ]; then
    ok "worst case ${worst}s (${attempts} x (30 + 130) + $((attempts - 1)) x ${pause} + 30) plus 120s to start fits activeDeadlineSeconds ${deadline}"
  else bad "worst case ${worst}s plus 120s to start exceeds activeDeadlineSeconds ${deadline}"; fi
else
  bad "could not read the deadline (${deadline:-none}), ATTEMPTS (${attempts:-none}), PAUSE (${pause:-none}) or the client's 30s and 130s limits"
fi

echo
if [ "$fails" -eq 0 ]; then echo "cigar-curation selftest: all passed"; exit 0; fi
echo "cigar-curation selftest: $fails FAILED"
exit 1
