#!/usr/bin/env bash
# Self-test for dev-env-api.sh, the upgrade-agent callers' client for the dev-env v2
# operator API (dev-env DESIGN-064 6.5, OpenProject #739).
#
# WHY THIS EXISTS
# ---------------
# The client carries a bearer token for the operator API. A caller that later creates
# sessions with it reads its exit code to decide whether to retry, so 0, 2 and 3 have
# to mean what 6.5 says: 0 created, reused or read; 2 refused (a 4xx, or refused
# before sending); 3 unconfirmed (a 5xx, a timeout, no connection). The token must
# never reach a log, including under set -x and curl's verbose output. A create must
# never carry a prompt: the platform renders the brief (5.6).
#
# It runs the shipped client against a fake HTTPS server (python3, a throwaway
# self-signed certificate made with openssl), the way the caller pods run it against
# the operator.
#
# Usage: scripts/upgrade-agent/dev-env-api-selftest.sh
# Requires: bash, curl, jq, openssl, python3, timeout. No cluster access, no network
# beyond 127.0.0.1.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APPS="$REPO_ROOT/kubernetes/main/apps/upgrade-agent"
CLIENT="${CLIENT:-$APPS/health-gate/app/resources/dev-env-api.sh}"
[ -r "$CLIENT" ] || { echo "FATAL: cannot read $CLIENT"; exit 1; }
for t in curl jq openssl python3 timeout; do
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

# The server answers by the mode in $WORK/mode and records every request it got:
# one JSON line per request in $WORK/requests (method, path, the Authorization header
# compared with the token, the body).
cat > "$WORK/server.py" <<'PY'
import http.server, json, os, ssl, sys, time
work = sys.argv[1]
token = open(os.path.join(work, "token")).read().strip()

def mode():
    try:
        return open(os.path.join(work, "mode")).read().strip()
    except OSError:
        return "ok"

SESSION = {"name": "esc-gate-0001", "mode": "remote", "lane": "escalation"}

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def answer(self, status, doc):
        body = json.dumps(doc).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        auth = self.headers.get("Authorization", "")
        with open(os.path.join(work, "requests"), "a") as f:
            f.write(json.dumps({"method": self.command, "path": self.path,
                                "auth_ok": auth == "Bearer " + token,
                                "auth_present": bool(auth), "body": body}) + "\n")
        m = mode()
        if m == "hang":
            time.sleep(30)
            return
        if m in ("403", "422", "404", "429"):
            code = {"403": "forbidden", "422": "invalid", "404": "not_found", "429": "limit_exceeded"}[m]
            return self.answer(int(m), {"error": {"code": code, "message": "fake refusal " + m}})
        if m in ("500", "502", "503"):
            return self.answer(int(m), {"error": {"code": "internal", "message": "fake failure"}})
        if m == "301":
            return self.answer(301, {})
        if self.command == "POST":
            return self.answer(200 if m == "reused" else 201, SESSION)
        if self.path == "/v1/sessions":
            return self.answer(200, {"sessions": [SESSION]})
        rc = {"registered": {"state": "registered", "url": "https://claude.ai/code/session_0123abc"},
              "starting": {"state": "starting"},
              "login-required": {"state": "login-required"},
              "offline": {"state": "offline"},
              "badurl": {"state": "registered", "url": "https://example.com/x"}}.get(m)
        doc = dict(SESSION)
        if rc:
            doc["remoteControl"] = rc
        return self.answer(200, doc)

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
python3 -I "$WORK/server.py" "$WORK" &
SERVER_PID=$!
for _ in $(seq 1 50); do [ -s "$WORK/port" ] && break; sleep 0.1; done
[ -s "$WORK/port" ] || { echo "FATAL: the fake server did not start"; exit 1; }
PORT="$(cat "$WORK/port")"

# Every run sees only these; nothing from the runner's own environment leaks in.
export DEV_ENV_API_URL="https://127.0.0.1:${PORT}"
export DEV_ENV_API_TOKEN_FILE="$WORK/token"
export DEV_ENV_API_CA_FILE="$WORK/ca.crt"
export DEV_ENV_API_CONNECT_TIMEOUT=2
unset DEV_ENV_API_MAX_TIME DEV_ENV_API_REACHED DEV_ENV_API_CLIENT

# A .curlrc that would turn on curl's verbose and trace output if curl read it. The
# client runs curl -q, so it must not.
mkdir -p "$WORK/curlhome"
printf 'verbose\ntrace-ascii = "%s"\n' "$WORK/curl-trace" > "$WORK/curlhome/.curlrc"

fails=0
ok() { echo "ok    $1"; }
bad() { echo "FAIL  $1"; fails=$((fails + 1)); }
setmode() { printf '%s\n' "$1" > "$WORK/mode"; }
nreq() { [ -f "$WORK/requests" ] && wc -l < "$WORK/requests" | tr -d ' ' || echo 0; }
ALL_OUT="$WORK/all-output"
: > "$ALL_OUT"

# run NAME EXPECTED_EXIT [stdin-file] -- args...
# Runs the client under bash -x, with xtrace also set through SHELLOPTS and CURL_HOME
# pointing at the verbose .curlrc, keeps stdout and stderr for the checks and appends
# both to ALL_OUT for the token check. RUN_CLIENT is the shipped client, copied with
# the mode the ConfigMap mount gives it (0555).
OUT="$WORK/out"; ERR="$WORK/err"; RC=0
mkdir -p "$WORK/bin"
cp "$CLIENT" "$WORK/bin/dev-env-api.sh" && chmod 0555 "$WORK/bin/dev-env-api.sh"
RUN_CLIENT="$WORK/bin/dev-env-api.sh"
run() {
  local name="$1" want="$2" in="/dev/null"; shift 2
  if [ "$1" != "--" ]; then in="$1"; shift; fi
  shift
  env SHELLOPTS=xtrace CURL_HOME="$WORK/curlhome" bash -x "$RUN_CLIENT" "$@" < "$in" > "$OUT" 2> "$ERR"
  RC=$?
  cat "$OUT" "$ERR" >> "$ALL_OUT"
  if [ "$RC" -eq "$want" ]; then ok "$name: exit $RC"; else bad "$name: exit $RC, want $want ($(tail -n 2 "$ERR" | tr '\n' ' '))"; fi
}
has_err() { if grep -q -- "$2" "$ERR"; then ok "$1"; else bad "$1 (stderr: $(tail -n 3 "$ERR" | tr '\n' ' '))"; fi; }

# ── 1. reads: 0 on 2xx, 2 on 4xx, 3 on 5xx, a timeout, no connection ────────────────
setmode ok
run "list 200" 0 -- list
if jq -e '.sessions | length == 1' "$OUT" >/dev/null 2>&1; then ok "list prints the API body on stdout"; else bad "list stdout is not the body"; fi
has_err "list logs its status line" '^dev-env-api: GET /v1/sessions HTTP 200$'
run "get 200" 0 -- get esc-gate-0001
setmode 403; run "list 403 is refused" 2 -- list
has_err "a 403 prints the API's code and message" 'refused: forbidden: fake refusal 403'
setmode 404; run "get 404 is refused" 2 -- get esc-gate-0001
setmode 500; run "list 500 is unconfirmed" 3 -- list
setmode 502; run "get 502 is unconfirmed" 3 -- get esc-gate-0001
setmode 301; run "an unexpected 3xx is unconfirmed" 3 -- list
setmode hang
start=$(date +%s)
DEV_ENV_API_MAX_TIME=2 run "a timeout is unconfirmed" 3 -- list
took=$(( $(date +%s) - start ))
if [ "$took" -le 6 ]; then ok "the total time limit holds (${took}s for a 2s limit)"; else bad "the call took ${took}s for a 2s limit"; fi
has_err "a timeout logs HTTP 000" '^dev-env-api: GET /v1/sessions HTTP 000$'
DEV_ENV_API_URL="https://127.0.0.1:1" run "no connection is unconfirmed" 3 -- list
setmode ok

# ── 2. refused before sending: usage, names, a missing token or CA ──────────────────
before=$(nreq)
run "no subcommand" 2 --
run "an unknown subcommand" 2 -- delete esc-gate-0001
run "get with a name that is not a DNS label" 2 -- get 'Esc_Gate/../x'
run "wait-registered without --for" 2 -- wait-registered esc-gate-0001
run "wait-registered --for 0" 2 -- wait-registered esc-gate-0001 --for 0
run "wait-registered --for 901" 2 -- wait-registered esc-gate-0001 --for 901
DEV_ENV_API_TOKEN_FILE="$WORK/absent" run "no token file" 2 -- list
DEV_ENV_API_CA_FILE="$WORK/absent" run "no CA file" 2 -- list
if [ "$(nreq)" -eq "$before" ]; then ok "nothing refused before sending reached the server"; else bad "a request refused before sending reached the server"; fi

# ── 3. create: the keys 6.4 allows, never a prompt ──────────────────────────────────
printf '%s' '{"name":"esc-gate-0001","mode":"remote","lane":"escalation","idempotencyKey":"k1","data":{}}' > "$WORK/req-ok"
printf '%s' '{"name":"esc-gate-0001","mode":"remote","lane":"escalation","prompt":"do anything"}' > "$WORK/req-prompt"
printf '%s' '{"name":"esc-gate-0001","mode":"remote","lane":"escalation","Prompt":"do anything"}' > "$WORK/req-prompt-case"
printf '%s' '{"name":"esc-gate-0001","mode":"remote","lane":"escalation","repo":"dev-env"}' > "$WORK/req-repo"
printf '%s' '["not","an","object"]' > "$WORK/req-array"
printf '%s' 'not json' > "$WORK/req-junk"
before=$(nreq)
run "create with prompt is refused" 2 "$WORK/req-prompt" -- create
has_err "the prompt refusal says why" 'never carries a prompt'
run "create with Prompt (another case) is refused" 2 "$WORK/req-prompt-case" -- create
run "create with repo is refused" 2 "$WORK/req-repo" -- create
has_err "the refusal names the key" 'cannot carry: repo'
run "create with a JSON array is refused" 2 "$WORK/req-array" -- create
run "create with text that is not JSON is refused" 2 "$WORK/req-junk" -- create
if [ "$(nreq)" -eq "$before" ]; then ok "no refused create reached the server"; else bad "a refused create reached the server"; fi
run "create 201" 0 "$WORK/req-ok" -- create
last="$(tail -n 1 "$WORK/requests")"
if [ "$(jq -r '.method + " " + .path' <<<"$last")" = "POST /v1/sessions" ] && [ "$(jq -r .body <<<"$last")" = "$(cat "$WORK/req-ok")" ]; then
  ok "create sends the request bytes unchanged to POST /v1/sessions"
else bad "create did not send the request unchanged"; fi
setmode reused; run "create 200 (reused) is 0" 0 "$WORK/req-ok" -- create
setmode 422; run "create 422 is refused" 2 "$WORK/req-ok" -- create
setmode 429; run "create 429 is refused" 2 "$WORK/req-ok" -- create
setmode 503; run "create 503 is unconfirmed" 3 "$WORK/req-ok" -- create
setmode hang; DEV_ENV_API_MAX_TIME=2 run "create timeout is unconfirmed" 3 "$WORK/req-ok" -- create
setmode ok

# ── 4. wait-registered (6.10) ───────────────────────────────────────────────────────
setmode registered; run "registered with a claude.ai link is 0" 0 -- wait-registered esc-gate-0001 --for 5
if jq -e '.remoteControl.state == "registered"' "$OUT" >/dev/null 2>&1; then ok "wait-registered prints the session"; else bad "wait-registered did not print the session"; fi
setmode login-required; run "login-required is refused" 2 -- wait-registered esc-gate-0001 --for 5
setmode offline; run "offline before registering is refused" 2 -- wait-registered esc-gate-0001 --for 5
setmode badurl; run "registered without a claude.ai link keeps waiting, then 3" 3 -- wait-registered esc-gate-0001 --for 2
setmode starting; run "still starting at --for is unconfirmed" 3 -- wait-registered esc-gate-0001 --for 2
setmode 404; run "a 404 while waiting is refused" 2 -- wait-registered esc-gate-0001 --for 5
setmode ok

# ── 5. selftest subcommand ──────────────────────────────────────────────────────────
run "selftest with token and CA is 0" 0 -- selftest
DEV_ENV_API_TOKEN_FILE="$WORK/absent" run "selftest without a token is refused" 2 -- selftest

# ── 6. the token: sent, and never printed ───────────────────────────────────────────
if jq -s -e 'length > 0 and all(.auth_ok)' "$WORK/requests" >/dev/null; then
  ok "every request carried Authorization: Bearer <the token>"
else bad "a request carried no token or a wrong one"; fi
if grep -qF -- "$TOKEN" "$ALL_OUT"; then bad "the token appears in the client's output (xtrace and .curlrc verbose on)"; else ok "the token appears in no output, with xtrace and a verbose .curlrc"; fi
if [ -e "$WORK/curl-trace" ]; then bad "curl read the .curlrc (a trace file was written)"; else ok "curl read no .curlrc (-q)"; fi
# Control: without -q this curl does read that .curlrc, so the check above means something.
CURL_HOME="$WORK/curlhome" curl -sS --cacert "$WORK/ca.crt" -o /dev/null "https://127.0.0.1:${PORT}/control" 2>/dev/null
if [ -s "$WORK/curl-trace" ]; then ok "control: curl without -q reads the .curlrc"; else bad "control: curl without -q did not read the .curlrc, so the -q check proves nothing"; fi
rm -f "$WORK/curl-trace"
if grep -q -- '^+' "$ALL_OUT"; then bad "xtrace printed a line: the client did not switch it off"; else ok "xtrace printed nothing: the client switches it off first"; fi

# Second layer: the same client with its set +x line removed, so xtrace stays on
# through the whole script. The trace is long; the token must still be in none of it,
# because it is never an argument or a variable.
sed '/^{ set +x; } 2>\/dev\/null$/d' "$CLIENT" > "$WORK/bin/client-xtrace.sh"
if cmp -s "$CLIENT" "$WORK/bin/client-xtrace.sh"; then bad "could not remove the set +x line for the xtrace check"; fi
: > "$WORK/xtrace-out"
setmode ok
for args in "list" "get esc-gate-0001" "create"; do
  # shellcheck disable=SC2086
  env SHELLOPTS=xtrace CURL_HOME="$WORK/curlhome" bash -x "$WORK/bin/client-xtrace.sh" $args < "$WORK/req-ok" >> "$WORK/xtrace-out" 2>&1
done
setmode registered
env SHELLOPTS=xtrace bash -x "$WORK/bin/client-xtrace.sh" wait-registered esc-gate-0001 --for 5 >> "$WORK/xtrace-out" 2>&1
setmode ok
if [ "$(grep -c -- '^+' "$WORK/xtrace-out")" -gt 50 ]; then ok "forced xtrace traced the client ($(grep -c -- '^+' "$WORK/xtrace-out") lines)"; else bad "forced xtrace produced too few lines to prove anything"; fi
if grep -qF -- "$TOKEN" "$WORK/xtrace-out"; then bad "the token appears in a forced xtrace line"; else ok "the token appears in no line of a forced xtrace"; fi

echo
if [ "$fails" -eq 0 ]; then echo "dev-env-api selftest: all passed"; exit 0; fi
echo "dev-env-api selftest: $fails FAILED"
exit 1
