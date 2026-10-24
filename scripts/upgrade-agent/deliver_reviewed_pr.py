#!/usr/bin/env python3
"""Deliver one reviewed pull request, stopping before a mutation when a gate fails.

Interactive source delivery for a single pull request. The caller supplies the
preflight as an argv list after ``--`` (no shell). That process must exit 0
before ``gh pr merge`` or ``flux``. Both required GitHub checks must be green
on the exact head, the squash merge is pinned with ``--match-head-commit``,
and Flux reconcile runs only after the pull request is verified merged.

``--reviewed-head`` is the operator's evidence of which commit was reviewed.
It must equal ``--head`` and the pull request head. A native Sol approval is
source-review evidence for that flag. This command does not read GitHub
review decisions or agent comments, and it does not treat them as a required
check.

``--source-only`` merges the source pull request and does not reconcile.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REQUIRED_CHECKS = ("Flux Local - Success", "Diff Scope - Success")
GH_TIMEOUT_S = 120
FLUX_TIMEOUT_S = 300
BYPASS_FLAGS = ("--admin", "--force", "--auto", "--auto-merge")
SHA_RE = re.compile(r"[0-9a-f]{40}")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
DNS_LABEL_RE = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")
VIEW_FIELDS = "state,headRefOid,mergedAt,statusCheckRollup"
SECRET_MARKERS = ("token", "bearer", "authorization", "password", "secret")


class DeliveryError(Exception):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def failing_code(returncode: int | None) -> int:
    if returncode is None:
        return 124
    if returncode < 0:
        return 128 + min(127, -returncode)
    if returncode == 0:
        return 1
    return returncode


def public_detail(text: str | None) -> str:
    line = ""
    for candidate in reversed((text or "").splitlines()):
        if candidate.strip():
            line = candidate.strip()
            break
    lowered = line.lower()
    if any(marker in lowered for marker in SECRET_MARKERS):
        return "command failed"
    return line[:180] or "command failed"


def run_checked(argv: list[str], timeout: int, capture: bool) -> subprocess.CompletedProcess[str]:
    """Run argv to completion. Nonzero, timeout, and an unknown exit all raise."""
    if not argv or not argv[0]:
        raise DeliveryError("refusing an empty command", 2)
    if any(flag in argv for flag in BYPASS_FLAGS):
        raise DeliveryError("refusing bypass flag", 2)
    name = Path(argv[0]).name
    try:
        return subprocess.run(
            argv,
            check=True,
            timeout=timeout,
            capture_output=capture,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeliveryError(
            f"{name} still running after {timeout}s; later steps are not authorized",
            124,
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = public_detail(exc.stderr if capture else None)
        raise DeliveryError(
            f"{name} failed: {detail}",
            failing_code(exc.returncode),
        ) from exc
    except OSError as exc:
        raise DeliveryError(f"{name} could not be started: {exc.strerror or exc}", 127) from exc


def check_label(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("name") or item.get("context") or "")


def check_outcome(item: object) -> str:
    if not isinstance(item, dict):
        return "unknown"
    status = str(item.get("status") or "").upper()
    conclusion = str(item.get("conclusion") or "").upper()
    state = str(item.get("state") or "").upper()
    if status:
        if status in {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED"}:
            return "running"
        if status != "COMPLETED":
            return "unknown"
        if conclusion == "SUCCESS":
            return "green"
        if conclusion in {
            "FAILURE",
            "CANCELLED",
            "TIMED_OUT",
            "ACTION_REQUIRED",
            "STALE",
            "STARTUP_FAILURE",
        }:
            return "nonzero"
        return "unknown"
    if state == "SUCCESS":
        return "green"
    if state in {"PENDING", "EXPECTED"}:
        return "running"
    if state in {"FAILURE", "ERROR"}:
        return "nonzero"
    return "unknown"


def required_ci_blocker(checks: object) -> str | None:
    if not isinstance(checks, list):
        return "unknown required CI payload"
    for name in REQUIRED_CHECKS:
        matched = [item for item in checks if check_label(item) == name]
        if not matched:
            return f"unknown required check missing: {name}"
        outcomes = {check_outcome(item) for item in matched}
        if "running" in outcomes:
            return f"running required check: {name}"
        if "nonzero" in outcomes:
            return f"nonzero required check: {name}"
        if outcomes != {"green"}:
            return f"unknown required check: {name}"
    return None


def parse_target(value: str) -> tuple[str, str]:
    namespace, separator, name = value.partition("/")
    if separator != "/" or not namespace or not name or "/" in name:
        raise argparse.ArgumentTypeError("expected namespace/name")
    if not DNS_LABEL_RE.fullmatch(namespace) or not DNS_LABEL_RE.fullmatch(name):
        raise argparse.ArgumentTypeError("expected namespace/name")
    return namespace, name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deliver_reviewed_pr.py",
        description=(
            "Merge one reviewed pull request after a preflight and required CI "
            "pass on the exact head. Reconcile only named Flux targets, and "
            "only after the merge is verified."
        ),
    )
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr", required=True, type=int, help="pull request number")
    parser.add_argument("--head", required=True, help="intended 40-character head")
    parser.add_argument(
        "--reviewed-head",
        required=True,
        help="commit that was reviewed; must equal --head",
    )
    parser.add_argument(
        "--preflight-timeout",
        required=True,
        type=int,
        help="seconds, 1..600, after which a still-running preflight fails closed",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--source-only",
        action="store_true",
        help="merge the source pull request and do not reconcile",
    )
    mode.add_argument(
        "--reconcile",
        action="append",
        type=parse_target,
        dest="reconcile",
        help="Flux kustomization namespace/name; repeat for more than one",
    )
    return parser


def require_sha(value: str, label: str) -> None:
    if not SHA_RE.fullmatch(value):
        raise DeliveryError(f"{label} must be a 40-character lowercase sha", 2)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    if argv is None:
        argv = sys.argv[1:]
    if "--" not in argv:
        raise DeliveryError("preflight command argv is required after --", 2)
    split = argv.index("--")
    preflight = argv[split + 1 :]
    if not preflight or any(part == "" for part in preflight):
        raise DeliveryError("preflight command argv is required after --", 2)
    args = build_parser().parse_args(argv[:split])
    if not REPO_RE.fullmatch(args.repo):
        raise DeliveryError("repo must be owner/name", 2)
    if args.pr < 1:
        raise DeliveryError("pr must be a positive number", 2)
    if args.preflight_timeout < 1 or args.preflight_timeout > 600:
        raise DeliveryError("preflight-timeout must be from 1 to 600 seconds", 2)
    require_sha(args.head, "head")
    require_sha(args.reviewed_head, "reviewed head")
    if args.reviewed_head != args.head:
        raise DeliveryError("reviewed head does not match intended head", 2)
    args.preflight = preflight
    return args


def gh_view(repo: str, pr: int) -> dict:
    proc = run_checked(
        [
            "gh",
            "pr",
            "view",
            str(pr),
            "--repo",
            repo,
            "--json",
            VIEW_FIELDS,
        ],
        GH_TIMEOUT_S,
        capture=True,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DeliveryError("unknown pull request view", 2) from exc
    if not isinstance(payload, dict):
        raise DeliveryError("unknown pull request view", 2)
    return payload


def require_live_head(payload: dict, head: str) -> None:
    if payload.get("headRefOid") != head:
        raise DeliveryError("reviewed head evidence does not match intended head", 2)


def ensure_ready_to_merge(payload: dict, head: str) -> None:
    require_live_head(payload, head)
    if payload.get("state") != "OPEN":
        raise DeliveryError("pull request is not open", 2)
    blocker = required_ci_blocker(payload.get("statusCheckRollup"))
    if blocker:
        raise DeliveryError(blocker, 2)


def merge_argv(repo: str, pr: int, head: str) -> list[str]:
    return [
        "gh",
        "pr",
        "merge",
        str(pr),
        "--repo",
        repo,
        "--squash",
        "--delete-branch",
        "--match-head-commit",
        head,
    ]


def ensure_merged(payload: dict, head: str) -> None:
    require_live_head(payload, head)
    if payload.get("state") != "MERGED":
        raise DeliveryError("post-merge verification failed: pull request is not merged", 2)
    if not payload.get("mergedAt"):
        raise DeliveryError("post-merge verification failed: merged time is missing", 2)


def flux_argv(namespace: str, name: str) -> list[str]:
    return [
        "flux",
        "reconcile",
        "kustomization",
        name,
        "--namespace",
        namespace,
        "--with-source",
    ]


def deliver(args: argparse.Namespace) -> None:
    # Preflight is the required predecessor of every mutation. A running,
    # unknown, or nonzero result raises and the caller returns that exit.
    run_checked(list(args.preflight), args.preflight_timeout, capture=False)
    ensure_ready_to_merge(gh_view(args.repo, args.pr), args.head)
    run_checked(merge_argv(args.repo, args.pr, args.head), GH_TIMEOUT_S, capture=True)
    ensure_merged(gh_view(args.repo, args.pr), args.head)
    print(f"merged {args.repo}#{args.pr} at {args.head}")
    if args.source_only:
        print("source-only: reconcile skipped")
        return
    for namespace, name in args.reconcile:
        run_checked(flux_argv(namespace, name), FLUX_TIMEOUT_S, capture=False)
        print(f"reconciled {namespace}/{name}")


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        deliver(args)
    except DeliveryError as exc:
        print(f"deliver-reviewed-pr: {exc}", file=sys.stderr)
        return exc.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
