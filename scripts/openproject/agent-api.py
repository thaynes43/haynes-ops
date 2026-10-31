"""Use inside existing dev-env: restricted private API, no token output or disk copy.

python3 agent-api.py GET /api/v3/users/me
python3 agent-api.py PATCH /api/v3/work_packages/123 < fixture-update.json
Credential discovery and exec stay inside the cluster, using existing authority.
No redirects are followed. Only this API origin can receive the Bearer token.
"""
from datetime import datetime, timezone
import errno
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ORIGIN = "http://openproject-web.frontend.svc.cluster.local:8082"
ALLOWED = ("/api/v3/projects", "/api/v3/work_packages", "/api/v3/types", "/api/v3/statuses", "/api/v3/users/me", "/api/v3/relations", "/api/v3/attachments", "/api/v3/queries")

GET_BUDGET = 120
BACKOFF_CAP = 16
HELPER_BUDGET = 30  # seconds for the token-only helper read before a POST/PATCH: nothing was sent, so nothing replays
HELPER_TIMEOUT = 30  # seconds for one helper read
DENIAL_MARKERS = ("forbidden", "unauthorized")  # in the helper's stderr, case-insensitively: an authorization denial is not retried
RETRY_FLOOR = 1  # seconds: no further GET attempt starts with less than this left of the budget
DESTINATION_HOST = "openproject-web.frontend.svc.cluster.local"
TRANSIENT_ERRNOS = {errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH,
                    errno.ETIMEDOUT, errno.ECONNRESET}


class ApiUnavailable(Exception):
    """Credential helper, network or server is unreachable. Not an API answer."""

_token = None

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def read_helper(timeout):
    """One read of the dedicated token-only helper: (token, None) or (None, category).

    The category is one of denied, transient, timeout or invalid. Neither stdout nor stderr is
    printed, stored or put in an exception: stderr is only matched against a fixed allowlist.
    """
    try:
        result = subprocess.run(["kubectl", "exec", "-n", "dev", "deploy/openproject-agent-credentials", "-c", "credentials", "--", "cat", "/run/openproject-agent/token"], capture_output=True, check=False, timeout=timeout, env=dict(os.environ, KUBECTL_REMOTE_COMMAND_WEBSOCKETS="false"))
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "transient"
    if result.returncode != 0:
        stderr = (result.stderr or b"").decode(errors="replace").lower()
        return None, "denied" if any(marker in stderr for marker in DENIAL_MARKERS) else "transient"
    try:
        value = result.stdout.decode().strip()
    except UnicodeDecodeError:
        return None, "invalid"
    if not (len(value) == 70 and value.startswith("opapi-") and all(c in "0123456789abcdefABCDEF" for c in value[6:])):
        return None, "invalid"
    return value, None

def token(deadline):
    """The helper's token, cached per process. A failed attempt caches nothing.

    Transient and timeout failures are retried with the capped backoff until `deadline`; nothing
    has been sent to the API yet, so nothing is replayed. A denial or a token in the wrong
    format is never retried.
    """
    global _token
    backoff, attempts = 1, 0
    while _token is None:
        attempts += 1
        value, category = read_helper(min(HELPER_TIMEOUT, max(1, deadline - time.monotonic())))
        if value is not None:
            _token = value
            break
        remaining = deadline - time.monotonic()
        retrying = category in ("transient", "timeout") and remaining > RETRY_FLOOR
        # Fixed fields only: never helper output or exception text.
        print("%s helper category=%s attempt=%d" % (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), category, attempts), file=sys.stderr)
        if not retrying:
            detail = " (retry budget exhausted)" if category in ("transient", "timeout") else ""
            raise ApiUnavailable("Dedicated token-only helper unavailable: %s after %d attempt(s)%s" % (category, attempts, detail))
        time.sleep(min(backoff, remaining - RETRY_FLOOR))
        backoff = min(backoff * 2, BACKOFF_CAP)
    return _token

def request(method, path, payload=None):
    assert method in ("GET", "POST", "PATCH"), "No deletion or arbitrary method"
    assert path.startswith(ALLOWED) and not any(x in path for x in ("\r", "\n", "\\", "..")), "Unsupported API path"
    body = None if payload is None else json.dumps(payload).encode()
    # A GET shares one budget between the helper read and the request; a mutation retries only the
    # helper read (before anything is sent) and never the HTTP call.
    deadline, backoff = time.monotonic() + (GET_BUDGET if method == "GET" else HELPER_BUDGET), 1
    req = urllib.request.Request(ORIGIN + path, data=body, method=method,
                                 headers={"Authorization": "Bearer " + token(deadline), "Content-Type": "application/json", "Accept": "application/hal+json"})
    opener = urllib.request.build_opener(NoRedirect())
    while True:
        response, result = None, None
        category, number, transient = None, None, False
        try:
            try:
                response = opener.open(req, timeout=min(30, max(0.001, deadline - time.monotonic())) if method == "GET" else 30)
            except urllib.error.HTTPError as error:
                response = error
            if response.code >= 400:
                category = "http_%d" % response.code
                transient = response.code in (502, 503, 504)
            if response.code < 500:
                raw = response.read()
                result = response.code, raw
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            cause = error.reason if isinstance(error, urllib.error.URLError) else error
            number = getattr(cause, "errno", None)
            number = number if isinstance(number, int) else None
            if isinstance(cause, TimeoutError):
                category = "timeout"
            elif isinstance(cause, socket.gaierror):
                category = "dns_failure"
            elif isinstance(cause, http.client.IncompleteRead):
                category = "incomplete_read"
            else:
                category = "connection_error"
            transient = isinstance(cause, (TimeoutError, ConnectionResetError, http.client.IncompleteRead)) or number in TRANSIENT_ERRNOS \
                or (isinstance(cause, socket.gaierror) and number == socket.EAI_AGAIN)
            if response is not None and 400 <= response.code < 500:
                category, transient = "http_%d" % response.code, False
            result = None
        finally:
            if response is not None:
                response.close()
        if result is not None:  # an API answer, including an expected 404/409/422: no diagnostic
            code, raw = result
            try:
                return code, json.loads(raw) if raw else None
            except ValueError:
                return code, {"_type": "NonJson", "raw": raw[:200].decode(errors="replace")}
        remaining = deadline - time.monotonic()
        retrying = method == "GET" and transient and remaining > RETRY_FLOOR
        # Fixed, bounded fields only: never format exception text, URLs, or bodies. One line per
        # attempt that will be retried or that ends the call as ApiUnavailable.
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print("%s %s host=%s category=%s errno=%s" % (
            stamp, method, DESTINATION_HOST, category, number if number is not None else "none"), file=sys.stderr)
        if not retrying:
            detail = "%d s retry budget exhausted" % GET_BUDGET if method == "GET" and transient else "unavailable"
            raise ApiUnavailable("OpenProject API %s %s (%s, errno=%s)" % (
                method, detail, category, number if number is not None else "none"))
        time.sleep(min(backoff, remaining - RETRY_FLOOR))  # the last attempt keeps at least the floor as its timeout
        backoff = min(backoff * 2, BACKOFF_CAP)

if __name__ == "__main__":
    assert len(sys.argv) == 3, "Usage: agent-api.py METHOD /api/v3/path"
    method, path = sys.argv[1:]
    payload = None if method == "GET" else json.load(sys.stdin)
    status, data = request(method, path, payload)
    print(json.dumps({"http_status": status, "data": data}))
    sys.exit(0 if 200 <= status < 300 else 1)
