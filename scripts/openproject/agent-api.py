"""Use inside existing dev-env: restricted private API, no token output or disk copy.

python3 agent-api.py GET /api/v3/users/me
python3 agent-api.py PATCH /api/v3/work_packages/123 < fixture-update.json
Credential discovery and exec stay inside the cluster, using existing authority.
No redirects are followed. Only this API origin can receive the Bearer token.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

ORIGIN = "http://openproject-web.frontend.svc.cluster.local:8082"
ALLOWED = ("/api/v3/projects", "/api/v3/work_packages", "/api/v3/types", "/api/v3/statuses", "/api/v3/users/me", "/api/v3/relations", "/api/v3/attachments", "/api/v3/queries")

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
    try:
        response = opener.open(req, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, OSError) as error:
        raise ApiUnavailable("OpenProject API unreachable: %s" % type(error).__name__)
    try:
        raw = response.read()
    except OSError as error:
        raise ApiUnavailable("OpenProject API read failed: %s" % type(error).__name__)
    if response.code >= 500:
        raise ApiUnavailable("OpenProject API error HTTP %d" % response.code)
    try:
        return response.code, json.loads(raw) if raw else None
    except ValueError:
        return response.code, {"_type": "NonJson", "raw": raw[:200].decode(errors="replace")}

if __name__ == "__main__":
    assert len(sys.argv) == 3, "Usage: agent-api.py METHOD /api/v3/path"
    method, path = sys.argv[1:]
    payload = None if method == "GET" else json.load(sys.stdin)
    status, data = request(method, path, payload)
    print(json.dumps({"http_status": status, "data": data}))
    sys.exit(0 if 200 <= status < 300 else 1)
