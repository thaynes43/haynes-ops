"""One-shot: load the saved MAM_ID_PROWLARR cookie into Prowlarr's MyAnonamouse indexer 17.

Reads two env values (never printed), GETs indexer 17, checks it is the MyAnonamouse
indexer with exactly one mamId field, replaces only that field's value, and PUTs the
same resource back, so every other field and the enabled state are kept. Prowlarr
tests the indexer on save. It prints only HTTP status codes and fixed messages.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://prowlarr.downloads.svc.cluster.local:9696"
INDEXER = 17


def fail(msg):
    print(f"mam-cookie-apply: {msg}")
    sys.exit(1)


def call(method, key, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE}/api/v1/indexer/{INDEXER}", data=data, method=method,
                                 headers={"X-Api-Key": key, "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(4_000_000)
    except urllib.error.HTTPError as e:
        return e.code, b""  # the error body may echo fields; never read or print it
    except Exception:
        return None, b""


def main():
    key = os.environ.get("PROWLARR_API_KEY", "").strip()
    cookie = os.environ.get("MAM_ID_PROWLARR", "").strip()
    if not key or not cookie:
        fail("missing input value")
    status, raw = call("GET", key)
    if status != 200:
        fail(f"GET indexer {INDEXER}: HTTP {status}")
    try:
        ix = json.loads(raw)
    except ValueError:
        fail("GET returned invalid JSON")
    if ix.get("id") != INDEXER or ix.get("implementation") != "MyAnonamouse":
        fail(f"indexer {INDEXER} is not the MyAnonamouse indexer")
    hits = [f for f in ix.get("fields") or [] if isinstance(f, dict) and f.get("name") == "mamId"]
    if len(hits) != 1:
        fail("indexer does not have exactly one mamId field")
    hits[0]["value"] = cookie
    status, _ = call("PUT", key, ix)
    print(f"mam-cookie-apply: PUT indexer {INDEXER}: HTTP {status}")
    sys.exit(0 if status in (200, 202) else 1)


if __name__ == "__main__":
    main()
