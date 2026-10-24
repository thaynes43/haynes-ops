#!/usr/bin/env python3
"""Offline regressions for the public deliver_reviewed_pr.py entrypoint.

Fake gh and flux binaries record invocations. Nothing here reaches GitHub,
Flux, or the cluster. Each case runs the shipped CLI.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().with_name("deliver_reviewed_pr.py")
REPO = "thaynes43/haynes-ops"
DEV_REPO = "thaynes43/dev-env"
PR = "4279"


def python_argv() -> list[str]:
    argv = [sys.executable]
    if sys.flags.optimize:
        argv.append("-" + ("O" * sys.flags.optimize))
    return argv


HEAD = "3b9f6af6" + "c0" * 16
OTHER = "a" * 40
TOKEN_KEYS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_PAT",
)

FAKE_GH = """#!/usr/bin/env python3
import json, os, sys, time

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

def check(name, status="COMPLETED", conclusion="SUCCESS"):
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
    }

def dev_checks():
    advisory = check("Claude Review (advisory)", conclusion="FAILURE")
    native = check("Sol native review")
    required = check("CI - Success")
    if mode in {"dev_green", "dev_verify_fail", "dev_verify_timeout", "dev_verify_head"}:
        return [required, advisory, native]
    if mode == "dev_advisory_unknown":
        return [required, check("Claude Review (advisory)", conclusion="NEUTRAL"), native]
    if mode == "dev_ci_pending":
        return [check("CI - Success", status="IN_PROGRESS", conclusion=None), advisory]
    if mode == "dev_ci_nonzero":
        return [check("CI - Success", conclusion="FAILURE"), advisory]
    if mode == "dev_ci_missing":
        return [
            check("Flux Local - Success"),
            check("Diff Scope - Success"),
            native,
            advisory,
        ]
    if mode == "dev_ci_unknown":
        return [check("CI - Success", conclusion="SKIPPED"), advisory]
    if mode == "dev_ci_duplicate_mixed":
        return [
            check("CI - Success", conclusion="SUCCESS"),
            check("CI - Success", conclusion="FAILURE"),
            advisory,
        ]
    return None

def checks():
    dev = dev_checks()
    if dev is not None:
        return dev
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
        if mode in {"verify_fail", "dev_verify_fail"}:
            emit({
                "state": "OPEN",
                "mergedAt": None,
                "headRefOid": live,
                "statusCheckRollup": checks(),
            })
        if mode == "dev_verify_timeout":
            time.sleep(360)
        if mode == "dev_verify_head":
            emit({
                "state": "MERGED",
                "mergedAt": "2026-10-24T17:00:00Z",
                "headRefOid": "b" * 40,
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

    def command(self, *middle: str, preflight: Path | None = None, reviewed: str | None = None, timeout: str = "120", repo: str | None = None) -> list[str]:
        reviewed_head = HEAD if reviewed is None else reviewed
        return [
            *python_argv(),
            str(CLI),
            "--repo",
            REPO if repo is None else repo,
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

    def clear_effects(self) -> None:
        if self.log.exists():
            self.log.unlink()
        for path in self.sentinel.iterdir():
            path.unlink()

    def merge_rows(self) -> list:
        return [row for row in self.rows() if len(row) >= 3 and row[1:3] == ["pr", "merge"]]

    def assert_stopped_before_effects(self) -> None:
        self.assertEqual(self.rows(), [])
        self.assertFalse((self.sentinel / "gh-invoked").exists())
        self.assert_no_merge_or_reconcile()

    def load_cli(self):
        spec = importlib.util.spec_from_file_location(
            f"deliver_reviewed_pr_isolated_{id(self)}",
            CLI,
        )
        if spec is None or spec.loader is None:
            self.fail(f"could not load {CLI}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_loaded(self, module, argv: list[str], env_extra: dict[str, str] | None = None) -> tuple[int, str, str]:
        desired = self.child_env()
        if env_extra:
            desired.update(env_extra)
        saved = dict(os.environ)
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            os.environ.clear()
            os.environ.update(desired)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = module.main(argv)
        finally:
            os.environ.clear()
            os.environ.update(saved)
        return code, stdout.getvalue(), stderr.getvalue()

    def module_argv(self, args: list[str]) -> list[str]:
        return args[args.index(str(CLI)) + 1 :]

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
        merge = next(row for row in self.rows() if row[1:3] == ["pr", "merge"])
        self.assertIn("--delete-branch", merge)
        self.assertIn("--squash", merge)
        self.assertIn("--match-head-commit", merge)

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
            *python_argv(),
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

    def test_runtime_guards_are_not_assert_statements(self) -> None:
        for lineno, line in enumerate(CLI.read_text(encoding="utf-8").splitlines(), start=1):
            self.assertFalse(line.lstrip().startswith("assert "), f"line {lineno} uses assert")

    def test_dev_env_green_advisory_failure_merges_once_without_delete_or_flux(self) -> None:
        self.mode = "dev_green"
        proc = self.run_cli(self.command("--source-only", repo=DEV_REPO))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"merged {DEV_REPO}#{PR} at {HEAD}", proc.stdout)
        self.assertIn("source-only: reconcile skipped", proc.stdout)
        self.assertEqual(
            self.merge_rows()[0][1:],
            [
                "pr",
                "merge",
                PR,
                "--repo",
                DEV_REPO,
                "--squash",
                "--match-head-commit",
                HEAD,
            ],
        )
        self.assertNotIn("--delete-branch", self.merge_rows()[0])
        self.assertEqual(len(self.merge_rows()), 1)
        self.assertEqual((self.sentinel / "view-count").read_text(encoding="utf-8"), "2")
        self.assertFalse((self.sentinel / "reconcile-invoked").exists(), self.rows())
        names = []
        for row in self.rows():
            if row == ["preflight"]:
                names.append("preflight")
            elif row[1:3] == ["pr", "view"]:
                names.append("view")
            elif row[1:3] == ["pr", "merge"]:
                names.append("merge")
            elif Path(row[0]).name == "flux":
                names.append("flux")
        self.assertEqual(names, ["preflight", "view", "merge", "view"])

    def test_dev_env_failed_and_unknown_preflight_skip_gh(self) -> None:
        self.mode = "dev_green"
        failed = self.make_preflight(code=17)
        proc = self.run_cli(self.command("--source-only", repo=DEV_REPO, preflight=failed))
        self.assertEqual(proc.returncode, 17, proc.stderr)
        self.assert_preflight_started()
        self.assertFalse((self.sentinel / "gh-invoked").exists(), self.rows())
        self.assert_no_merge_or_reconcile()

        self.clear_effects()
        missing = self.root / "missing-preflight"
        proc = self.run_cli(self.command("--source-only", repo=DEV_REPO, preflight=missing))
        self.assertEqual(proc.returncode, 127, proc.stderr)
        self.assertIn("could not be started", proc.stderr)
        self.assertEqual(self.rows(), [])
        self.assert_no_merge_or_reconcile()

        self.clear_effects()
        signal_path = self.root / "preflight-signal"
        signal_path.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, signal\n"
            f"log = {json.dumps(str(self.log))}\n"
            "with open(log, 'a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(['preflight']) + chr(10))\n"
            "os.kill(os.getpid(), signal.SIGKILL)\n",
            encoding="utf-8",
        )
        signal_path.chmod(signal_path.stat().st_mode | stat.S_IEXEC)
        proc = self.run_cli(self.command("--source-only", repo=DEV_REPO, preflight=signal_path))
        self.assertEqual(proc.returncode, 137, proc.stderr)
        self.assert_preflight_started()
        self.assertFalse((self.sentinel / "gh-invoked").exists(), self.rows())
        self.assert_no_merge_or_reconcile()

    def test_dev_env_timeout_preflight_skips_gh(self) -> None:
        self.mode = "dev_green"
        preflight = self.make_preflight(delay=30)
        proc = self.run_cli(
            self.command("--source-only", repo=DEV_REPO, preflight=preflight, timeout="1"),
            proc_timeout=15,
        )
        self.assertEqual(proc.returncode, 124, proc.stderr)
        self.assertIn("still running", proc.stderr)
        self.assert_preflight_started()
        self.assertFalse((self.sentinel / "gh-invoked").exists(), self.rows())
        self.assert_no_merge_or_reconcile()

    def test_dev_env_required_ci_states_block_merge(self) -> None:
        cases = (
            ("dev_ci_pending", "running required check: CI - Success"),
            ("dev_ci_nonzero", "nonzero required check: CI - Success"),
            ("dev_ci_missing", "unknown required check missing: CI - Success"),
            ("dev_ci_unknown", "unknown required check: CI - Success"),
            ("dev_ci_duplicate_mixed", "nonzero required check: CI - Success"),
        )
        for mode, message in cases:
            with self.subTest(mode=mode):
                self.clear_effects()
                self.mode = mode
                proc = self.run_cli(self.command("--source-only", repo=DEV_REPO))
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn(message, proc.stderr)
                self.assert_preflight_started()
                self.assertEqual((self.sentinel / "view-count").read_text(encoding="utf-8"), "1")
                self.assert_no_merge_or_reconcile()

    def test_dev_env_head_mismatches_do_not_merge(self) -> None:
        self.mode = "dev_green"
        reviewed = self.run_cli(self.command("--source-only", repo=DEV_REPO, reviewed=OTHER))
        self.assertEqual(reviewed.returncode, 2, reviewed.stderr)
        self.assertIn("reviewed head does not match intended head", reviewed.stderr)
        self.assert_stopped_before_effects()

        self.live_head = OTHER
        live = self.run_cli(self.command("--source-only", repo=DEV_REPO))
        self.assertEqual(live.returncode, 2, live.stderr)
        self.assertIn("reviewed head evidence does not match intended head", live.stderr)
        self.assert_preflight_started()
        self.assert_no_merge_or_reconcile()

    def test_unknown_repository_and_dev_env_reconcile_stop_before_effects(self) -> None:
        self.mode = "green"
        unknown = self.run_cli(self.command("--source-only", repo="thaynes43/not-a-profile"))
        self.assertEqual(unknown.returncode, 2, unknown.stderr)
        self.assertIn("unknown repository", unknown.stderr)
        self.assert_stopped_before_effects()

        self.clear_effects()
        self.mode = "dev_green"
        reconcile = self.run_cli(self.command("--reconcile", "frontend/haynes-network", repo=DEV_REPO))
        self.assertEqual(reconcile.returncode, 2, reconcile.stderr)
        self.assertIn("source-only", reconcile.stderr)
        self.assertIn("reconcil", reconcile.stderr)
        self.assert_stopped_before_effects()

    def test_dev_env_bypass_and_check_override_flags_stop_before_effects(self) -> None:
        self.mode = "dev_green"
        flags = (
            ("--admin",),
            ("--force",),
            ("--auto",),
            ("--auto-merge",),
            ("--skip-checks",),
            ("--required-check", "Flux Local - Success"),
            ("--checks", "CI - Success"),
        )
        for flag in flags:
            with self.subTest(flag=flag):
                self.clear_effects()
                proc = self.run_cli(self.command(*flag, "--source-only", repo=DEV_REPO))
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assert_stopped_before_effects()

    def test_dev_env_advisory_unknown_does_not_block_or_authorize(self) -> None:
        self.mode = "dev_advisory_unknown"
        proc = self.run_cli(self.command("--source-only", repo=DEV_REPO))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.merge_rows()), 1)
        self.assertNotIn("--delete-branch", self.merge_rows()[0])
        self.assertFalse((self.sentinel / "reconcile-invoked").exists(), self.rows())

        self.clear_effects()
        blocked = self.run_cli(self.command("--source-only", repo=DEV_REPO, reviewed=OTHER))
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        self.assertIn("reviewed head does not match intended head", blocked.stderr)
        self.assert_stopped_before_effects()

    def test_dev_env_postmerge_failure_does_not_retry(self) -> None:
        self.mode = "dev_verify_fail"
        proc = self.run_cli(self.command("--source-only", repo=DEV_REPO))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("post-merge verification failed", proc.stderr)
        self.assertEqual(len(self.merge_rows()), 1)
        self.assertEqual((self.sentinel / "view-count").read_text(encoding="utf-8"), "2")
        self.assertFalse((self.sentinel / "reconcile-invoked").exists(), self.rows())

        self.clear_effects()
        self.mode = "dev_verify_head"
        shifted = self.run_cli(self.command("--source-only", repo=DEV_REPO))
        self.assertEqual(shifted.returncode, 2, shifted.stderr)
        self.assertIn("does not match intended head", shifted.stderr)
        self.assertEqual(len(self.merge_rows()), 1)
        self.assertFalse((self.sentinel / "reconcile-invoked").exists(), self.rows())

    def test_dev_env_postmerge_timeout_does_not_retry(self) -> None:
        self.assertIn("GH_TIMEOUT_S = 120\n", CLI.read_text(encoding="utf-8"))
        self.mode = "dev_verify_timeout"
        proc = self.run_cli(self.command("--source-only", repo=DEV_REPO), proc_timeout=170)
        self.assertEqual(proc.returncode, 124, proc.stderr)
        self.assertIn("still running", proc.stderr)
        self.assertEqual(len(self.merge_rows()), 1)
        self.assertNotIn("--delete-branch", self.merge_rows()[0])
        self.assertEqual((self.sentinel / "view-count").read_text(encoding="utf-8"), "2")
        self.assertFalse((self.sentinel / "reconcile-invoked").exists(), self.rows())

    def test_env_cannot_replace_haynes_ops_checks(self) -> None:
        self.mode = "green"
        env = self.child_env()
        env["REQUIRED_CHECKS"] = "not-a-real-check"
        env["DELIVER_REQUIRED_CHECKS"] = "CI - Success"
        proc = subprocess.run(
            self.command("--source-only"),
            env=env,
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.merge_rows()), 1)
        self.assertIn("--delete-branch", self.merge_rows()[0])

    def test_env_and_monkeypatch_cannot_weaken_fixed_profiles(self) -> None:
        self.mode = "green"
        env = self.child_env()
        env["REQUIRED_CHECKS"] = "Flux Local - Success,Diff Scope - Success"
        env["DELIVER_REQUIRED_CHECKS"] = "Flux Local - Success"
        weakened = subprocess.run(
            self.command("--source-only", repo=DEV_REPO),
            env=env,
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(weakened.returncode, 2, weakened.stderr)
        self.assertIn("unknown required check missing: CI - Success", weakened.stderr)
        self.assert_preflight_started()
        self.assert_no_merge_or_reconcile()

        self.clear_effects()
        self.mode = "green"
        module = self.load_cli()
        module.REQUIRED_CHECKS = ("not-a-real-check",)
        code, _stdout, stderr = self.run_loaded(
            module,
            self.module_argv(self.command("--source-only")),
            {"REQUIRED_CHECKS": "not-a-real-check"},
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(self.merge_rows()), 1)
        self.assertIn("--delete-branch", self.merge_rows()[0])

        self.clear_effects()
        self.mode = "green"
        module.REQUIRED_CHECKS = ()
        code, _stdout, stderr = self.run_loaded(
            module,
            self.module_argv(self.command("--source-only", repo=DEV_REPO)),
            {"REQUIRED_CHECKS": ""},
        )
        self.assertEqual(code, 2, stderr)
        self.assertIn("unknown required check missing: CI - Success", stderr)
        self.assert_no_merge_or_reconcile()


if __name__ == "__main__":
    unittest.main()
