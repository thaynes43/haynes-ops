#!/usr/bin/env python3
"""Offline regressions for hand_run_preflight.py (story 712).

A fake gh on PATH answers `gh pr view` with a canned pull request and records its
argv. Nothing here reaches GitHub. Each case runs the shipped script as a process.
"""

from __future__ import annotations

import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().with_name("hand_run_preflight.py")
REPO = "thaynes43/haynes-ops"
PR = "4764"
HEAD = "d6a69455" + "0a" * 16
OTHER = "b" * 40

FAKE_GH = """#!{python}
import json, os, sys
with open(os.environ["PREFLIGHT_FAKE_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
with open(os.environ["PREFLIGHT_FAKE_VIEW"], encoding="utf-8") as handle:
    sys.stdout.write(handle.read())
"""


def check(name: str, conclusion: str = "SUCCESS", status: str = "COMPLETED") -> dict:
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def good_view() -> dict:
    """A pull request the preflight lets through."""
    return {
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": HEAD,
        "mergeable": "MERGEABLE",
        "commits": [
            {"oid": HEAD, "authors": [{"login": "renovate[bot]", "name": "renovate[bot]"}]},
        ],
        "statusCheckRollup": [
            check("Flux Local - Success"),
            check("Diff Scope - Success"),
            check("Flux Local - Filter"),
            {"__typename": "StatusContext", "context": "renovate/stability-days", "state": "SUCCESS"},
            check("Claude Review (advisory)"),
        ],
    }


class HandRunPreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.log = root / "gh.log"
        self.view = root / "view.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, view: dict | None, *args: str) -> subprocess.CompletedProcess[str]:
        if view is not None:
            self.view.write_text(json.dumps(view), encoding="utf-8")
        env = {
            "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "HOME": self.tmp.name,
            "PREFLIGHT_FAKE_LOG": str(self.log),
            "PREFLIGHT_FAKE_VIEW": str(self.view),
        }
        argv = [sys.executable, "-I", "-B", str(CLI), *(args or (REPO, PR, HEAD))]
        return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60, check=False)

    def gh_calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def assert_refused(self, view: dict, reason: str, *args: str) -> None:
        proc = self.run_cli(view, *args)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(f"preflight REFUSED: {reason}", proc.stdout)
        self.assertNotIn("preflight OK", proc.stdout)

    # exit 0

    def test_all_good_passes_and_reads_the_named_pr(self) -> None:
        proc = self.run_cli(good_view())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("preflight OK", proc.stdout)
        self.assertNotIn("REFUSED", proc.stdout)
        self.assertEqual(
            self.gh_calls(),
            [["pr", "view", PR, "-R", REPO, "--json",
              "state,isDraft,headRefOid,commits,statusCheckRollup,mergeable"]],
        )

    def test_advisory_review_is_ignored_when_failing_or_running(self) -> None:
        for conclusion, status in (("FAILURE", "COMPLETED"), ("", "IN_PROGRESS")):
            with self.subTest(status=status, conclusion=conclusion):
                view = good_view()
                view["statusCheckRollup"][-1] = check("Claude Review (advisory)", conclusion, status)
                proc = self.run_cli(view)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn("preflight OK", proc.stdout)
        with self.subTest(form="commit status, PENDING"):
            view = good_view()
            view["statusCheckRollup"][-1] = {
                "__typename": "StatusContext", "context": "Claude Review (advisory)", "state": "PENDING"}
            proc = self.run_cli(view)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_expected_check_present_passes(self) -> None:
        proc = self.run_cli(good_view(), REPO, PR, HEAD, "--expect", "Flux Local - Filter")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    # exit 1

    def test_not_open_is_refused(self) -> None:
        view = good_view()
        view["state"] = "CLOSED"
        self.assert_refused(view, "state is CLOSED")

    def test_draft_is_refused(self) -> None:
        view = good_view()
        view["isDraft"] = True
        self.assert_refused(view, "PR is a draft")

    def test_head_moved_is_refused(self) -> None:
        view = good_view()
        view["headRefOid"] = OTHER
        self.assert_refused(view, f"head moved: {OTHER} != reviewed {HEAD}")

    def test_conflicting_is_refused(self) -> None:
        view = good_view()
        view["mergeable"] = "CONFLICTING"
        self.assert_refused(view, "PR is conflicting")

    def test_non_renovate_commit_is_refused(self) -> None:
        for authors in ([{"login": "haynes-dev-bot[bot]"}], [{"login": "renovate[bot]"}, {"login": "someone"}], []):
            with self.subTest(authors=authors):
                view = good_view()
                view["commits"].append({"oid": OTHER, "authors": authors})
                self.assert_refused(view, f"commit {OTHER[:10]} has non-Renovate author(s)")

    def test_failing_check_is_refused(self) -> None:
        for conclusion in ("FAILURE", "CANCELLED", "TIMED_OUT"):
            with self.subTest(conclusion=conclusion):
                view = good_view()
                view["statusCheckRollup"].append(check("Build and Push ControlFoley Image / validate", conclusion))
                self.assert_refused(view, f"check failed: Build and Push ControlFoley Image / validate ({conclusion})")

    def test_running_check_is_refused(self) -> None:
        view = good_view()
        view["statusCheckRollup"].append(check("validate", "", "IN_PROGRESS"))
        self.assert_refused(view, "check still running: validate (IN_PROGRESS)")

    def test_pending_commit_status_is_refused(self) -> None:
        # A commit status has a state and no status. PENDING is not finished: fail closed,
        # as night-merge.mjs --check-only does. The hand-run nights' copy let it through.
        view = good_view()
        view["statusCheckRollup"].append(
            {"__typename": "StatusContext", "context": "renovate/stability-days", "state": "PENDING"})
        self.assert_refused(view, "check still running: renovate/stability-days (PENDING)")

    def test_expected_commit_status_is_refused(self) -> None:
        view = good_view()
        view["statusCheckRollup"].append(
            {"__typename": "StatusContext", "context": "renovate/stability-days", "state": "EXPECTED"})
        self.assert_refused(view, "check still running: renovate/stability-days (EXPECTED)")

    def test_required_check_absent_is_refused(self) -> None:
        for name in ("Flux Local - Success", "Diff Scope - Success"):
            with self.subTest(name=name):
                view = good_view()
                view["statusCheckRollup"] = [c for c in view["statusCheckRollup"] if c.get("name") != name]
                self.assert_refused(view, f"expected check not SUCCESS: {name} -> absent")

    def test_expected_check_absent_is_refused(self) -> None:
        self.assert_refused(good_view(), "expected check not SUCCESS: validate -> absent",
                            REPO, PR, HEAD, "--expect", "validate")

    def test_every_problem_is_listed(self) -> None:
        view = copy.deepcopy(good_view())
        view["isDraft"] = True
        view["mergeable"] = "CONFLICTING"
        proc = self.run_cli(view)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("preflight REFUSED: PR is a draft", proc.stdout)
        self.assertIn("preflight REFUSED: PR is conflicting", proc.stdout)

    # exit 2

    def test_unknown_argument_is_a_usage_error_before_gh(self) -> None:
        proc = self.run_cli(good_view(), REPO, PR, HEAD, "--force")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("unknown argument '--force'", proc.stdout)
        self.assertEqual(self.gh_calls(), [])

    def test_missing_arguments_print_usage(self) -> None:
        proc = self.run_cli(good_view(), REPO, PR)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("usage: hand_run_preflight.py", proc.stdout)
        self.assertEqual(self.gh_calls(), [])


if __name__ == "__main__":
    unittest.main()
