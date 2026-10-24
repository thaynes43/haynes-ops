#!/usr/bin/env python3
"""Deliver one reviewed pull request, stopping before a mutation when a gate fails.

Interactive source delivery for one allowlisted repository. The caller supplies
the preflight as an argv list after ``--`` (no shell). That process must exit 0
before ``gh pr merge`` or ``flux``. Required checks come from the fixed
repository profile, not from flags, the environment, or ``REQUIRED_CHECKS``.
The squash merge is pinned with ``--match-head-commit``.

``thaynes43/haynes-ops`` requires ``Flux Local - Success`` and
``Diff Scope - Success``. Its merge deletes the branch. Flux reconcile runs
only after the pull request is verified merged, and only for named targets.

``thaynes43/dev-env`` requires ``CI - Success``, is source-only, refuses
``--reconcile`` before preflight or any later command, and does not delete the
branch. Any other repository is refused. Source-only delivery is not a
deployment.

``--reviewed-head`` is the operator's evidence of which commit was reviewed.
It must equal ``--head`` and the pull request head. It grants no authority by
itself. A native Sol approval is source-review evidence for that flag. This
command does not read GitHub review decisions or agent comments, and it does
not treat an advisory check as a required check or as a native verdict.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Documented haynes-ops names. policy_for uses its own literals and does not
# read this name, so a caller cannot retarget delivery by replacing it.
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


def required_ci_blocker(checks: object, required: tuple[str, ...]) -> str | None:
    if not isinstance(checks, list):
        return "unknown required CI payload"
    for name in required:
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


def policy_for(
    repo: str,
    reconcile: list[tuple[str, str]] | None,
    source_only: bool,
) -> tuple[tuple[str, ...], bool]:
    """Return (required check names, delete_branch) for a fixed repository.

    dev-env is source-only. A reconcile request raises before the caller can
    start preflight. Unknown repositories raise. The check names are literals
    so flags, the environment, and REQUIRED_CHECKS cannot replace them.
    """
    if repo == "thaynes43/haynes-ops":
        return ("Flux Local - Success", "Diff Scope - Success"), True
    if repo == "thaynes43/dev-env":
        if reconcile or not source_only:
            raise DeliveryError("dev-env delivery is source-only; refusing reconcile", 2)
        return ("CI - Success",), False
    raise DeliveryError(f"unknown repository: {repo}", 2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deliver_reviewed_pr.py",
        description=(
            "Merge one reviewed pull request after a preflight and that "
            "repository's fixed required checks pass on the exact head. "
            "haynes-ops may reconcile named Flux targets only after the merge "
            "is verified. dev-env is source-only."
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
        help="Flux kustomization namespace/name; repeat for more than one; refused for dev-env",
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


def ensure_ready_to_merge(payload: dict, head: str, required: tuple[str, ...]) -> None:
    require_live_head(payload, head)
    if payload.get("state") != "OPEN":
        raise DeliveryError("pull request is not open", 2)
    blocker = required_ci_blocker(payload.get("statusCheckRollup"), required)
    if blocker:
        raise DeliveryError(blocker, 2)


def merge_argv(repo: str, pr: int, head: str, delete_branch: bool) -> list[str]:
    argv = [
        "gh",
        "pr",
        "merge",
        str(pr),
        "--repo",
        repo,
        "--squash",
    ]
    if delete_branch:
        argv.append("--delete-branch")
    argv.extend(["--match-head-commit", head])
    return argv


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
    # Profile refusal is the required predecessor of preflight and every
    # mutation. A running, unknown, or nonzero preflight raises after that
    # and the caller returns that exit without a later command.
    required, delete_branch = policy_for(args.repo, args.reconcile, args.source_only)
    run_checked(list(args.preflight), args.preflight_timeout, capture=False)
    ensure_ready_to_merge(gh_view(args.repo, args.pr), args.head, required)
    run_checked(
        merge_argv(args.repo, args.pr, args.head, delete_branch),
        GH_TIMEOUT_S,
        capture=True,
    )
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
