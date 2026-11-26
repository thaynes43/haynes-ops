"""Probe port 8002 or the run listener using the token already in this pod."""

import os
import sys
from pathlib import Path

import httpx

from haynes_studio.queue.worker_tokens import worker_bearer


WORKER_LANES = ("voice", "image", "run", "matte")
PREFIX = "/internal/v1/work/"
FORBIDDEN = "work.lane_forbidden"
WORK_ROUTES = ("heartbeat", "publish", "complete", "fail")


def check(client, label, route, expected, code=None, *, json=None, content=None,
          method="POST", authenticated=True):
    headers = {"Content-Type": "application/json"}
    if authenticated:
        headers["Authorization"] = "Bearer " + worker_bearer(os.environ["STUDIO_WORKER_TOKEN"])
    try:
        response = client.request(method, PREFIX + route, headers=headers, json=json, content=content)
        passed = response.status_code == expected and (
            code is None or response.json().get("error", {}).get("code") == code
        )
        print("PASS" if passed else "FAIL", label, response.status_code, code if passed and code else "")
        return passed
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        print("FAIL", label, type(error).__name__)
        return False


def probe(client, lane):
    malformed = b"{"
    results = [
        check(client, f"{lane} anonymous claim", "claim", 401, "auth.required",
              content=malformed, authenticated=False),
        check(client, f"{lane} malformed resume", "resume", 422 if lane == "run" else 403,
              content=malformed),
        check(client, f"{lane} invalid JSON claim", "claim", 422, content=malformed),
        check(client, f"{lane} unknown lane claim", "claim", 422, json={"lane": "unknown"}),
        check(client, f"{lane} no lane, extra claim field", "claim", 422 if lane == "run" else 403,
              FORBIDDEN if lane != "run" else None, json={"unexpected": True}),
    ]
    for other in WORKER_LANES:
        if other != lane:
            results.append(check(client, f"{lane} wrong lane {other} plus invalid fields", "claim", 403,
                                 FORBIDDEN, json={"lane": other, "unexpected": True}))

    if lane != "matte":
        results.append(check(client, f"{lane} settle_only", "claim", 403, FORBIDDEN,
                             json={"lane": lane, "settle_only": True}))
    else:
        results += [
            check(client, "matte claim with invalid field", "claim", 422, "schema.unknown_field",
                  json={"lane": "matte", "unexpected": True}),
            check(client, "matte anonymous release", "release-matte", 401, "auth.required",
                  content=malformed, authenticated=False),
            check(client, "matte malformed release", "release-matte", 422, content=malformed),
            check(client, "matte GET release refused", "release-matte", 403, method="GET"),
        ]
    if lane != "run":
        for route in WORK_ROUTES:
            results.append(check(client, f"{lane} malformed default-run {route}", route, 403, FORBIDDEN,
                                 json={"unexpected": True}))

    return all(results)


def main():
    (lane,) = sys.argv[1:]
    if lane not in WORKER_LANES:
        return 1
    if lane == "matte":
        os.environ["STUDIO_WORKER_TOKEN"] = Path("/run/secrets/studio-worker/token").read_text().strip()
    base = os.environ["STUDIO_API_URL"]
    port = 8000 if lane == "run" else 8002
    if base != f"http://studio-api.ai.svc.cluster.local:{port}":
        print(f"FAIL {lane} listener port")
        return 1
    with httpx.Client(base_url=base, trust_env=False, timeout=5) as client:
        passed = probe(client, lane)
    print("PASS" if passed else "FAIL", lane, "route checks")
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
