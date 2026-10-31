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
import subprocess
import sys
import time
import urllib.error
import urllib.request

ORIGIN = "http://openproject-web.frontend.svc.cluster.local:8082"
ALLOWED = ("/api/v3/projects", "/api/v3/work_packages", "/api/v3/types", "/api/v3/statuses", "/api/v3/users/me", "/api/v3/relations", "/api/v3/attachments", "/api/v3/queries")

GET_BUDGET = 120
BACKOFF_CAP = 16
DESTINATION_HOST = "openproject-web.frontend.svc.cluster.local"
TRANSIENT_ERRNOS = {errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH,
                    errno.ETIMEDOUT, errno.ECONNRESET}


class ApiUnavailable(Exception):
    """Credential helper, network or server is unreachable. Not an API answer."""

_token = None

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def token():
    global _token
    if _token is None:
        try:
            result = subprocess.run(["kubectl", "exec", "-n", "dev", "deploy/openproject-agent-credentials", "-c", "credentials", "--", "cat", "/run/openproject-agent/token"], capture_output=True, check=False, timeout=30, env=dict(os.environ, KUBECTL_REMOTE_COMMAND_WEBSOCKETS="false"))
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ApiUnavailable("Dedicated token-only helper unavailable: %s" % type(error).__name__)
        if result.returncode != 0:
            raise ApiUnavailable("Dedicated token-only helper unavailable")
        value = result.stdout.decode().strip()
        assert len(value) == 70 and value.startswith("opapi-") and all(c in "0123456789abcdefABCDEF" for c in value[6:]), "Invalid dedicated API token format"
        _token = value
    return _token

def request(method, path, payload=None):
    assert method in ("GET", "POST", "PATCH"), "No deletion or arbitrary method"
    assert path.startswith(ALLOWED) and not any(x in path for x in ("\r", "\n", "\\", "..")), "Unsupported API path"
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(ORIGIN + path, data=body, method=method,
                                 headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json", "Accept": "application/hal+json"})
    opener = urllib.request.build_opener(NoRedirect())
    deadline, backoff = time.monotonic() + GET_BUDGET, 1
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
            category = "timeout" if isinstance(cause, TimeoutError) else "connection_error"
            transient = isinstance(cause, (TimeoutError, ConnectionResetError)) or number in TRANSIENT_ERRNOS
            if response is not None and 400 <= response.code < 500:
                category, transient = "http_%d" % response.code, False
            result = None
        finally:
            if response is not None:
                response.close()
        if category:
            # Fixed, bounded fields only: never format exception text, URLs, or bodies.
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            print("%s %s host=%s category=%s errno=%s" % (
                stamp, method, DESTINATION_HOST, category, number if number is not None else "none"), file=sys.stderr)
        if not category or (category.startswith("http_") and result is not None and result[0] < 500):
            code, raw = result
            try:
                return code, json.loads(raw) if raw else None
            except ValueError:
                return code, {"_type": "NonJson", "raw": raw[:200].decode(errors="replace")}
        remaining = deadline - time.monotonic()
        if method != "GET" or not transient or remaining <= 0:
            detail = "120 s retry budget exhausted" if method == "GET" and transient and remaining <= 0 else "unavailable"
            raise ApiUnavailable("OpenProject API %s %s (%s, errno=%s)" % (
                method, detail, category, number if number is not None else "none"))
        time.sleep(min(backoff, remaining))
        if time.monotonic() >= deadline:
            raise ApiUnavailable("OpenProject API GET 120 s retry budget exhausted (%s, errno=%s)" % (
                category, number if number is not None else "none"))
        backoff = min(backoff * 2, BACKOFF_CAP)

if __name__ == "__main__":
    assert len(sys.argv) == 3, "Usage: agent-api.py METHOD /api/v3/path"
    method, path = sys.argv[1:]
    payload = None if method == "GET" else json.load(sys.stdin)
    status, data = request(method, path, payload)
    print(json.dumps({"http_status": status, "data": data}))
    sys.exit(0 if 200 <= status < 300 else 1)
