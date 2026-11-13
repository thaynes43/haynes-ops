#!/usr/bin/env python3
"""Preflight for deliver_reviewed_pr.py: fail closed unless the PR is exactly what was reviewed.

usage: hand_run_preflight.py <owner/repo> <pr> <40-char head> [--expect "<check name>"]...

Exit 0 only when: the PR is open and not a draft; its head is the reviewed head; every
commit is authored by Renovate; no check on the head is failing, cancelled or still
running (a commit status in state PENDING or EXPECTED counts as still running); the two
required aggregates and every --expect check concluded SUCCESS.
"""
# Moved into git from the hands-off-upkeep handoff's preflight.py (sha256 4c54ac09..., used
# on the hand-run nights of 2026-11-10 and 2026-11-11), so that
# .agents/runbooks/upgrade-approved-by-prompt.md does not depend on a file in ~/.shared
# (story 712). Tests: scripts/upgrade-agent/test_hand_run_preflight.py.
# It differs from that copy in one rule: a commit status (StatusContext, which has a state
# and no status) in state PENDING or EXPECTED is refused as still running. The copy read it
# as completed ("gap 1" of upgrade-night-lane.md). Now it fails closed and agrees with
# night-merge.mjs --check-only, which refuses a pending status too.
import json
import subprocess
import sys

REQUIRED = ["Flux Local - Success", "Diff Scope - Success"]
BAD = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}
IGNORE = {"Claude Review (advisory)"}
PENDING_STATES = {"PENDING", "EXPECTED"}


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    repo, pr, head = argv[1], argv[2], argv[3]
    expect, rest = [], argv[4:]
    while rest:
        if rest[0] == "--expect" and len(rest) > 1:
            expect.append(rest[1])
            rest = rest[2:]
        else:
            print(f"preflight: unknown argument {rest[0]!r}")
            return 2
    out = subprocess.run(
        ["gh", "pr", "view", pr, "-R", repo, "--json",
         "state,isDraft,headRefOid,commits,statusCheckRollup,mergeable"],
        capture_output=True, text=True, timeout=90, check=True).stdout
    d = json.loads(out)
    problems = []
    if d["state"] != "OPEN":
        problems.append(f"state is {d['state']}")
    if d["isDraft"]:
        problems.append("PR is a draft")
    if d["headRefOid"] != head:
        problems.append(f"head moved: {d['headRefOid']} != reviewed {head}")
    if d.get("mergeable") == "CONFLICTING":
        problems.append("PR is conflicting")
    for c in d["commits"]:
        logins = {a.get("login") for a in c.get("authors", [])}
        if not logins or not logins <= {"renovate", "renovate[bot]", "app/renovate"}:
            problems.append(f"commit {c['oid'][:10]} has non-Renovate author(s) {sorted(str(x) for x in logins)}")
    seen = {}
    for s in d["statusCheckRollup"] or []:
        name = s.get("name") or s.get("context")
        concl = s.get("conclusion") or s.get("state") or ""
        status = s.get("status") or ("COMPLETED" if concl else "")
        if not s.get("status") and s.get("state") in PENDING_STATES:
            status = s["state"]  # a commit status not finished yet: still running
        if name in IGNORE:
            continue
        seen.setdefault(name, []).append(concl or status)
        if status and status != "COMPLETED":
            problems.append(f"check still running: {name} ({status})")
        elif concl in BAD:
            problems.append(f"check failed: {name} ({concl})")
    for name in REQUIRED + expect:
        if "SUCCESS" not in seen.get(name, []):
            problems.append(f"expected check not SUCCESS: {name} -> {seen.get(name, 'absent')}")
    ok = sorted(n for n, v in seen.items() if "SUCCESS" in v)
    print(f"preflight #{pr} head {head[:10]}: {len(ok)} checks SUCCESS: " + "; ".join(ok))
    if problems:
        for p in problems:
            print("preflight REFUSED:", p)
        return 1
    print("preflight OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
