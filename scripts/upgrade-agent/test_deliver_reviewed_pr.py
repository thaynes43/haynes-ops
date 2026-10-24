#!/usr/bin/env python3
"""Offline regressions for the public deliver_reviewed_pr.py entrypoint.

Fake gh and flux binaries record invocations. Nothing here reaches GitHub,
Flux, or the cluster. Each case runs the shipped CLI.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().with_name("deliver_reviewed_pr.py")
REPO = "thaynes43/haynes-ops"
PR = "4279"
HEAD = "3b9f6af6" + "c0" * 16
OTHER = "a" * 40
TOKEN_KEYS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_PAT",
)

FAKE_GH = """#!/usr/bin/env python3
import json, os, sys

log_path = os.environ["DELIVER_FAKE_LOG"]
sentinel = os.environ["DELIVER_FAKE_SENTINEL"]
mode = os.environ.get("DELIVER_FAKE_MODE", "green")
head = os.environ["DELIVER_FAKE_HEAD"]
live = os.environ.get("DELIVER_FAKE_LIVE_HEAD") or head

def record(*kinds):
    line = json.dumps(sys.argv)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(line + "\\n")
    for kind in kinds:
        with open(os.path.join(sentinel, kind), "a", encoding="utf-8") as handle:
            handle.write(line + "\\n")

def checks():
    flux = {
        "__typename": "CheckRun",
        "name": "Flux Local - Success",
        "status": "COMPLETED",
        "conclusion": "SUCCESS",
    }
    diff = {
        "__typename": "CheckRun",
        "name": "Diff Scope - Success",
        "status": "COMPLETED",
        "conclusion": "SUCCESS",
    }
    if mode == "ci_pending":
        diff["status"] = "IN_PROGRESS"
        diff["conclusion"] = None
    elif mode == "ci_nonzero":
        flux["conclusion"] = "FAILURE"
    elif mode == "ci_unknown":
        diff = None
    return [item for item in (flux, diff) if item is not None]

def emit(payload):
    sys.stdout.write(json.dumps(payload))
    raise SystemExit(0)

argv = sys.argv[1:]
if len(argv) >= 2 and argv[0] == "pr" and argv[1] == "view":
    record("gh-invoked")
    count_path = os.path.join(sentinel, "view-count")
    count = 1
    if os.path.exists(count_path):
        count = int(open(count_path, encoding="utf-8").read()) + 1
    with open(count_path, "w", encoding="utf-8") as handle:
        handle.write(str(count))
    if count >= 2:
        if mode == "verify_fail":
            emit({
                "state": "OPEN",
                "mergedAt": None,
                "headRefOid": live,
                "statusCheckRollup": checks(),
            })
        emit({
            "state": "MERGED",
            "mergedAt": "2026-10-24T17:00:00Z",
            "headRefOid": head,
            "statusCheckRollup": checks(),
        })
    emit({
        "state": "OPEN",
        "mergedAt": None,
        "headRefOid": live,
        "statusCheckRollup": checks(),
    })
if len(argv) >= 2 and argv[0] == "pr" and argv[1] == "merge":
    record("gh-invoked", "merge-invoked")
    code = int(os.environ.get("DELIVER_FAKE_MERGE_CODE", "0"))
    if code:
        sys.stderr.write("merge refused\\n")
        raise SystemExit(code)
    raise SystemExit(0)
record("gh-invoked")
sys.stderr.write("unexpected gh invocation\\n")
raise SystemExit(99)
"""

FAKE_FLUX = """#!/usr/bin/env python3
import json, os, sys

line = json.dumps(sys.argv)
with open(os.environ["DELIVER_FAKE_LOG"], "a", encoding="utf-8") as handle:
    handle.write(line + "\\n")
sentinel = os.path.join(os.environ["DELIVER_FAKE_SENTINEL"], "reconcile-invoked")
with open(sentinel, "a", encoding="utf-8") as handle:
    handle.write(line + "\\n")
code = int(os.environ.get("DELIVER_FAKE_FLUX_CODE", "0"))
if code:
    sys.stderr.write("reconcile refused\\n")
raise SystemExit(code)
"""


def load_log(path: Path) -> list:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            rows.append(json.loads(line))
    return rows


class DeliverReviewedPrTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / "log.ndjson"
        self.sentinel = self.root / "sentinel"
        self.sentinel.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.write_bin("gh", FAKE_GH)
        self.write_bin("flux", FAKE_FLUX)
        self.preflight = self.make_preflight()
        self.mode = "green"
        self.merge_code = 0
        self.flux_code = 0
        self.live_head = ""
        self.assertTrue(CLI.is_file(), f"missing public entrypoint {CLI}")

    def write_bin(self, name: str, content: str) -> None:
        path = self.bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def make_preflight(self, code: int = 0, delay: int = 0) -> Path:
        path = self.root / f"preflight-{code}-{delay}"
        quoted_log = json.dumps(str(self.log))
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import json, time\n"
            f"log = {quoted_log}\n"
            "with open(log, 'a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(['preflight']) + '\\n')\n"
            f"time.sleep({int(delay)})\n"
            f"raise SystemExit({int(code)})\n",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    def child_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in TOKEN_KEYS:
            env.pop(key, None)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["GH_CONFIG_DIR"] = str(self.root / "gh-config")
        (self.root / "gh-config").mkdir(exist_ok=True)
        env["DELIVER_FAKE_LOG"] = str(self.log)
        env["DELIVER_FAKE_SENTINEL"] = str(self.sentinel)
        env["DELIVER_FAKE_MODE"] = self.mode
        env["DELIVER_FAKE_HEAD"] = HEAD
        env["DELIVER_FAKE_MERGE_CODE"] = str(self.merge_code)
        env["DELIVER_FAKE_FLUX_CODE"] = str(self.flux_code)
        if self.live_head:
            env["DELIVER_FAKE_LIVE_HEAD"] = self.live_head
        else:
            env.pop("DELIVER_FAKE_LIVE_HEAD", None)
        return env

    def command(self, *middle: str, preflight: Path | None = None, reviewed: str | None = None, timeout: str = "120") -> list[str]:
        reviewed_head = HEAD if reviewed is None else reviewed
        return [
            "python3",
            str(CLI),
            "--repo",
            REPO,
            "--pr",
            PR,
            "--head",
            HEAD,
            "--reviewed-head",
            reviewed_head,
            "--preflight-timeout",
            timeout,
            *middle,
            "--",
            str(preflight or self.preflight),
        ]

    def run_cli(self, args: list[str], proc_timeout: int = 20) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                args,
                env=self.child_env(),
                cwd=self.root,
                text=True,
                capture_output=True,
                timeout=proc_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self.fail(f"CLI hung past the test timeout: {exc}")

    def rows(self) -> list:
        return load_log(self.log)

    def assert_no_merge_or_reconcile(self) -> None:
        self.assertFalse((self.sentinel / "merge-invoked").exists(), self.rows())
        self.assertFalse((self.sentinel / "reconcile-invoked").exists(), self.rows())
        for row in self.rows():
            self.assertNotEqual(row[1:3], ["pr", "merge"], row)
            self.assertFalse(row and Path(row[0]).name == "flux", row)

    def assert_preflight_started(self) -> None:
        self.assertTrue(self.rows(), "preflight was not started")
        self.assertEqual(self.rows()[0], ["preflight"])

    def test_failed_preflight_preserves_exit_and_skips_merge_and_reconcile(self) -> None:
        preflight = self.make_preflight(code=17)
        proc = self.run_cli(self.command("--reconcile", "frontend/haynes-network", preflight=preflight))
        self.assertEqual(proc.returncode, 17, proc.stderr)
        self.assert_preflight_started()
        self.assertFalse((self.sentinel / "gh-invoked").exists(), self.rows())
        self.assert_no_merge_or_reconcile()

    def test_required_ci_pending_blocks(self) -> None:
        self.mode = "ci_pending"
        proc = self.run_cli(self.command("--source-only"))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("running required check: Diff Scope - Success", proc.stderr)
        self.assert_preflight_started()
        self.assertTrue((self.sentinel / "gh-invoked").exists())
        self.assertEqual((self.sentinel / "view-count").read_text(encoding="utf-8"), "1")
        self.assert_no_merge_or_reconcile()

    def test_required_ci_nonzero_blocks(self) -> None:
        self.mode = "ci_nonzero"
        proc = self.run_cli(self.command("--source-only"))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("nonzero required check: Flux Local - Success", proc.stderr)
        self.assert_preflight_started()
        self.assertEqual((self.sentinel / "view-count").read_text(encoding="utf-8"), "1")
        self.assert_no_merge_or_reconcile()

    def test_required_ci_unknown_blocks_when_one_required_check_is_missing(self) -> None:
        self.mode = "ci_unknown"
        proc = self.run_cli(self.command("--source-only"))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("unknown required check missing: Diff Scope - Success", proc.stderr)
        self.assert_preflight_started()
        self.assert_no_merge_or_reconcile()

    def test_reviewed_head_mismatch_blocks_before_preflight(self) -> None:
        proc = self.run_cli(self.command("--source-only", reviewed=OTHER))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("reviewed head does not match intended head", proc.stderr)
        self.assertEqual(self.rows(), [])
        self.assertFalse((self.sentinel / "gh-invoked").exists())
        self.assert_no_merge_or_reconcile()

    def test_live_head_mismatch_blocks_merge(self) -> None:
        self.live_head = OTHER
        proc = self.run_cli(self.command("--source-only"))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("reviewed head evidence does not match intended head", proc.stderr)
        self.assert_preflight_started()
        self.assertTrue((self.sentinel / "gh-invoked").exists())
        self.assert_no_merge_or_reconcile()

    def test_merge_failure_preserves_exit_and_skips_reconcile(self) -> None:
        self.merge_code = 7
        proc = self.run_cli(
            self.command("--reconcile", "frontend/haynes-network", "--reconcile", "database/mosquitto")
        )
        self.assertEqual(proc.returncode, 7, proc.stderr)
        self.assert_preflight_started()
        self.assertTrue((self.sentinel / "merge-invoked").exists())
        self.assertEqual((self.sentinel / "view-count").read_text(encoding="utf-8"), "1")
        self.assertFalse((self.sentinel / "reconcile-invoked").exists(), self.rows())

    def test_postmerge_verification_failure_skips_reconcile(self) -> None:
        self.mode = "verify_fail"
        proc = self.run_cli(self.command("--reconcile", "frontend/haynes-network"))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("post-merge verification failed", proc.stderr)
        self.assertTrue((self.sentinel / "merge-invoked").exists())
        self.assertEqual((self.sentinel / "view-count").read_text(encoding="utf-8"), "2")
        self.assertFalse((self.sentinel / "reconcile-invoked").exists(), self.rows())

    def test_reconcile_failure_preserves_exit_and_stops(self) -> None:
        self.flux_code = 5
        proc = self.run_cli(
            self.command("--reconcile", "frontend/haynes-network", "--reconcile", "database/mosquitto")
        )
        self.assertEqual(proc.returncode, 5, proc.stderr)
        self.assertTrue((self.sentinel / "merge-invoked").exists(), self.rows())
        self.assertEqual((self.sentinel / "view-count").read_text(encoding="utf-8"), "2")
        flux_rows = [row for row in self.rows() if Path(row[0]).name == "flux"]
        self.assertEqual(
            [row[1:] for row in flux_rows],
            [["reconcile", "kustomization", "haynes-network", "--namespace", "frontend", "--with-source"]],
        )

    def test_success_ordered_sequence(self) -> None:
        proc = self.run_cli(
            self.command("--reconcile", "frontend/haynes-network", "--reconcile", "database/mosquitto")
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        tails = []
        for row in self.rows():
            if row == ["preflight"]:
                tails.append(row)
            else:
                tails.append(row[1:])
        self.assertEqual(
            tails,
            [
                ["preflight"],
                ["pr", "view", PR, "--repo", REPO, "--json", "state,headRefOid,mergedAt,statusCheckRollup"],
                [
                    "pr",
                    "merge",
                    PR,
                    "--repo",
                    REPO,
                    "--squash",
                    "--delete-branch",
                    "--match-head-commit",
                    HEAD,
                ],
                ["pr", "view", PR, "--repo", REPO, "--json", "state,headRefOid,mergedAt,statusCheckRollup"],
                ["reconcile", "kustomization", "haynes-network", "--namespace", "frontend", "--with-source"],
                ["reconcile", "kustomization", "mosquitto", "--namespace", "database", "--with-source"],
            ],
        )
        for row in self.rows():
            self.assertFalse(any(flag in row for flag in ("--admin", "--force", "--auto", "--auto-merge")))

    def test_source_only_merges_without_reconcile(self) -> None:
        proc = self.run_cli(self.command("--source-only"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("source-only", proc.stdout)
        names = []
        for row in self.rows():
            if row == ["preflight"]:
                names.append("preflight")
            elif Path(row[0]).name == "gh" and row[1:3] == ["pr", "view"]:
                names.append("view")
            elif Path(row[0]).name == "gh" and row[1:3] == ["pr", "merge"]:
                names.append("merge")
            elif Path(row[0]).name == "flux":
                names.append("flux")
        self.assertEqual(names, ["preflight", "view", "merge", "view"])
        self.assertFalse((self.sentinel / "reconcile-invoked").exists(), self.rows())

    def test_timeout_running_preflight_blocks_merge_and_reconcile(self) -> None:
        preflight = self.make_preflight(delay=30)
        proc = self.run_cli(
            self.command("--reconcile", "frontend/haynes-network", preflight=preflight, timeout="1"),
            proc_timeout=15,
        )
        self.assertEqual(proc.returncode, 124, proc.stderr)
        self.assertIn("still running", proc.stderr)
        self.assert_preflight_started()
        self.assertFalse((self.sentinel / "gh-invoked").exists(), self.rows())
        self.assert_no_merge_or_reconcile()

    def test_bypass_flags_and_missing_preflight_do_not_mutate(self) -> None:
        text = CLI.read_text(encoding="utf-8")
        self.assertNotIn("shell=True", text)
        self.assertNotIn("os.system", text)
        base = [
            "python3",
            str(CLI),
            "--repo",
            REPO,
            "--pr",
            PR,
            "--head",
            HEAD,
            "--reviewed-head",
            HEAD,
            "--preflight-timeout",
            "30",
            "--source-only",
        ]
        for flag in ("--admin", "--force", "--auto"):
            proc = self.run_cli([*base, flag, "--", str(self.preflight)])
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertEqual(self.rows(), [], flag)
            self.assert_no_merge_or_reconcile()
        missing = self.run_cli(base)
        self.assertEqual(missing.returncode, 2, missing.stderr)
        self.assertIn("preflight command argv is required", missing.stderr)
        self.assertEqual(self.rows(), [])
        self.assert_no_merge_or_reconcile()


if __name__ == "__main__":
    unittest.main()
