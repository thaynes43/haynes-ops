"""Select fresh isolation evidence from timestamped Kubernetes container logs."""

import json
import sys
from datetime import datetime, timezone


def audit(started, lines, now):
    proof = {}
    for line in lines:
        timestamp, _, payload = line.partition(" ")
        if not payload.startswith("{"):
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("event") != "matte_isolation":
            continue
        recorded = datetime.fromisoformat(timestamp)
        if recorded <= started:
            continue
        phase = event.get("phase")
        if phase not in {"startup", "recheck"} or event.get("result") != "pass":
            print(f"FAIL matte isolation: {event.get('reason', 'unrecognized result')}", file=sys.stderr)
            return 1
        if (phase == "startup" and proof) or (phase == "recheck" and "startup" not in proof):
            print("FAIL isolation logs out of order for the worker container", file=sys.stderr)
            return 1
        proof[phase] = (recorded, line.rstrip())
    if "startup" not in proof:
        print("FAIL no startup isolation proof newer than the worker container start", file=sys.stderr)
        return 1
    if "recheck" not in proof:
        return 2
    initial, recent = proof["startup"], proof["recheck"]
    if recent[0] <= initial[0] or not 0 <= (now - recent[0]).total_seconds() <= 90:
        print("FAIL no current isolation recheck after startup proof", file=sys.stderr)
        return 1
    print(initial[1])
    print(recent[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(audit(datetime.fromisoformat(sys.argv[1]), sys.stdin, datetime.now(timezone.utc)))
