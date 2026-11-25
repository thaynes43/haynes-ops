"""Run each OP967 runbook block with no cluster access and errexit suppressed."""

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml


STUDIO = Path("kubernetes/main/apps/ai/studio")
RELEASE = yaml.safe_load((STUDIO / "release.yaml").read_text())
OLD = ("80975a9e958d0bed78ab530c2b20f1279bf7ebe6",
       "sha256:08ac310f246cdc43225478e9496e480af6430b53751f4ae93f3e34206714c3e7",
       "sha256:497e020b8846f09cdeab6d822aa1536dfeb325edd26205cd6e28f0061bb6d9ca")
HEAD = "a" * 40
REVIEWED_HEAD = "b" * 40
TREE = "c" * 40
NOW = 1760000050
SNAPSHOT = {"store": {}, "runs": {}, "deliveries": {}, "delivery media": {}, "media jobs": {}}
POST_MERGE = ("migration", "roll", "postflight", "rollback-baseline", "compare")

# Every fixture command is selected via PATH. The stubs never shell out.
COMMANDS = r'''
import json
import os
import re
import sys
from pathlib import Path

import yaml

cmd = Path(sys.argv[0]).name
args = sys.argv[1:]
env = os.environ
data = json.loads(env["FIXTURE"])
revision, api, web = data["new"] if data["workloads"] == "new" else data["old"]
sidecar = data["sidecar"]

def emit(value):
    print(json.dumps(value))

def pods(workload):
    names = {
        "api": {"api": api, "media-worker-routes": web},
        "mcp": {"mcp": api}, "web": {"nginx": web}, "worker": {"worker": api},
        "voice-worker": {"worker": api}, "image-worker": {"worker": api},
        "matte-worker": {"worker": api, "matting": sidecar},
        "render": {"app": api},
    }[workload]
    instances = 2 if workload in {"api", "mcp", "web"} else 1
    result = []
    for index in range(instances):
        pod = f"{workload}-{index}"
        containers = {
            name: (digest if name == "matting" else
                   "ghcr.io/thaynes43/haynes-studio" + ("-web" if digest == web else "") + "@" + digest)
            for name, digest in names.items()
        }
        result.append({
            "metadata": {"name": pod, "uid": pod},
            "spec": {
                "nodeName": "talosw01" if workload in {"worker", "matte-worker", "render"} else f"other-{index}",
                "containers": [
                    {"name": name, "image": image,
                     "resources": {"limits": {"cpu": "17"}},
                     "command": ["/usr/local/bin/python", "-B", "/config/render-init.py"]}
                    for name, image in containers.items()
                ],
            },
            "status": {
                "conditions": [{"type": "Ready", "status": "False" if data.get("api_unready") and workload == "api"
                                else "True"}],
                "containerStatuses": [
                    {"name": name, "imageID": image, "ready": True, "restartCount": 0}
                    for name, image in containers.items()
                ],
            },
        })
    return result

if cmd in {"kubectl", "flux"}:
    with open(env["COMMAND_LOG"], "a") as log:
        log.write(json.dumps([cmd, *args]) + "\n")

if cmd == "git":
    match args:
        case ["rev-parse", "--verify", "HEAD"]:
            print(data["head"])
        case ["rev-parse", "--verify", "HEAD^{tree}"]:
            print(data["tree"])
        case ["status", "--porcelain", "--untracked-files=no"]:
            print(data.get("dirty", ""))
        case ["merge-base", "--is-ancestor", _, "refs/remotes/origin/main"]:
            sys.exit(not data.get("on_main", True))
        case ["show", "-s", "--format=%ct", _]:
            print(data["merged_at"])
        case _:
            sys.exit(99)
elif cmd == "date":
    print(data["now"] if args[-1] == "+%s" else "2026-11-25T17:00:00Z")
elif cmd == "yq":
    query, filename = args[-2:]
    docs = [doc for doc in yaml.safe_load_all(Path(filename).read_text()) if doc]
    if query.startswith("select"):
        print(next(doc["metadata"]["name"] for doc in docs if doc["kind"] == "Job"))
    else:
        result = docs[0]
        for key in query.lstrip(".").split("."):
            result = result[key]
        print(result)
elif cmd == "curl":
    print("401" if args[-1].endswith("/v1/me") else "200")
elif cmd == "flux":
    sys.exit(bool(data.get("flux_failure")))
elif cmd == "kubectl":
    if args[:1] == ["-n"]:
        args = args[2:]
    match args:
        case ["get", "gitrepository", "haynes-ops", "-o", "json"]:
            emit({"status": {"artifact": {"revision": "main@sha1:" + data["artifact"]}}})
        case ["get", "pods", "-l", label, "-o", "json"]:
            emit({"items": pods(label.split("=studio-", 1)[1])})
        case ["get", "pods", *_]:
            print("sample")
        case ["get", "endpointslices", *_]:
            emit({"items": [{"endpoints": [
                {"conditions": {"ready": True}, "targetRef": {"uid": pod["metadata"]["uid"]}}
                for pod in pods("api")
            ]}]})
        case ["get", "cluster", "postgres16", *_]:
            emit({"spec": {"instances": 3}, "status": {"readyInstances": 3}})
        case ["get", "node", "talosw01", *_]:
            emit({"status": {"allocatable": {"cpu": "39", "memory": "111129616Ki"}}})
        case ["get", "kustomization", *stages, "-o", "json"]:
            emit({"items": [
                {"metadata": {"name": stage}, "status": {"lastAppliedRevision": "main@sha1:" + data["artifact"],
                 "conditions": [{"type": "Ready", "status": "True"}]}} for stage in stages
            ]})
        case ["get", "deploy/studio-matte-worker", *_]:
            pass
        case ["top", "node", *_]:
            print("talosw01 100m 2% 100Mi 10%")
        case ["top", "pod", *_]:
            print("render-0 app 50m 100Mi")
        case ["wait", *_]:
            sys.exit(bool(data.get("migration_wait_failure") and "condition=Complete" in " ".join(args)))
        case ["logs", job, "--all-pods=true", "-c", _]:
            if data.get("bad_logs") and "voice" in job:
                sys.exit(1)
            print("saved predecessor job audit")
        case ["logs", job, "-c", "migrate"]:
            print("studio_migrator: connected to studio over TLS")
            print("studio_migrator: HBA rejected first check")
            print("studio_migrator: HBA rejected second check")
            print("Alembic revision: 0013_matte_jobs")
        case ["logs", job, "-c", "register"]:
            print("diagnostic before JSON")
            emit({"worker_id": "irrelevant", "status": 500})
            worker = re.sub(r"-[a-f0-9]{12}(?:-r[1-9][0-9]*)?$", "",
                            job.removeprefix("job/studio-register-"))
            emit({"worker_id": "studio-" + worker, "status": 201 if worker == "matte-worker" else 200})
        case ["exec", *options]:
            if "/v1/ready" in options[-1]:
                emit({"ready": True, "active_video_job": None, "active_foley_job": data.get("active_foley")})
            elif options[-1].endswith("/queue"):
                emit({"queue_running": [], "queue_pending": []})
            elif options[-2] in {"preflight", "postflight", "rollback-baseline", "compare"}:
                mode, expected_build = options[-2:]
                if expected_build != revision or options[1] not in {"api-0", "api-1"}:
                    sys.exit(87)
                if data.get("wrong_schema") and mode == "rollback-baseline":
                    print("FAIL 0013 release column visible", file=sys.stderr)
                    sys.exit(1)
                print("diagnostic before JSON")
                emit({"wrong": "first JSON object"})
                emit(data["snapshot"])
            elif any("other render-container processes" in arg for arg in options):
                sys.exit(bool(data.get("render_busy")))
            elif any("av.library_versions" in arg for arg in options):
                emit({"pyav": "18.1.1", "libraries": {f"libav{name}": [1, 2, 3] for name in (
                    "codec", "format", "filter", "util", "device", "video", "audio"
                )}})
        case _:
            sys.exit(99)
else:
    sys.exit(99)
'''


class PhaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        page = Path("docs/reference/studio-fourth-release.md").read_text()
        cls.blocks = {}
        for block in re.findall(r"(?ms)^```bash\n(.*?)^```$", page):
            phase = re.search(r"bash scripts/studio-release/op967\.sh ([\w-]+)", block)
            if phase:
                cls.blocks[phase[1]] = block
        cls.assert_blocks = set(cls.blocks) == {"preflight", *POST_MERGE}

    def setUp(self):
        self.assertTrue(self.assert_blocks, self.blocks)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="op967-phases-")))
        self.receipt = self.root / "receipt.json"
        self.baseline = self.root / "baseline.json"
        self.log = self.root / "commands"
        stub = self.root / "command"
        stub.write_text(f"#!{sys.executable}\n" + COMMANDS)
        stub.chmod(0o700)
        for name in ("git", "date", "kubectl", "flux", "curl", "yq"):
            (self.root / name).symlink_to(stub)
        self.data = {
            "head": HEAD, "tree": TREE, "artifact": HEAD, "merged_at": NOW - 10, "now": NOW,
            "new": [RELEASE[key] for key in ("revision", "apiDigest", "webDigest")],
            "old": OLD, "workloads": "old", "snapshot": SNAPSHOT,
            "sidecar": yaml.safe_load((STUDIO / "matting-pending/release/release.yaml").read_text())["data"]["image"],
        }

    def admit(self, **changes):
        data = {
            "schema": "studio-op967/v1", "ops_revision": REVIEWED_HEAD, "ops_tree": TREE,
            "started_epoch": NOW - 100, "finished_epoch": NOW - 50, "passed": True,
            "baseline_sha256": hashlib.sha256(self.baseline.read_bytes()).hexdigest()
            if self.baseline.exists() else "0" * 64,
            "checks": [{"label": label, "result": "PASS"} for label in (
                "predecessor Job logs saved", "empty queues, archived data and store baseline",
            )],
            **changes,
        }
        self.receipt.write_text(json.dumps(data))

    def run_phase(self, phase, *, expected=0, selected=HEAD, **changes):
        self.log.write_text("")
        env = {
            "PATH": f"{self.root}:{os.environ['PATH']}", "FIXTURE": json.dumps({**self.data, **changes}),
            "COMMAND_LOG": str(self.log), "STUDIO_OP967_RECEIPT": str(self.receipt),
            "STUDIO_OP967_BASELINE": str(self.baseline), "MERGED_OPS_REVISION": selected,
        }
        # The documented if ! bash ... suppresses errexit throughout the sourced
        # phase. A naked failed check followed by Flux would still be observed.
        runner = 'set -e\nbash() { ( source "$@" ); }\n' + self.blocks[phase]
        result = subprocess.run(
            ["bash", "--noprofile", "--norc"], input=runner, env=env,
            text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        return result, [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_preflight_records_checks_baseline_and_four_old_job_logs(self):
        self.run_phase("preflight", head=REVIEWED_HEAD)
        receipt = json.loads(self.receipt.read_text())
        self.assertTrue(receipt["passed"])
        self.assertEqual((receipt["ops_revision"], receipt["ops_tree"]), (REVIEWED_HEAD, TREE))
        self.assertEqual(receipt["finished_epoch"], NOW)
        self.assertEqual(set(item["result"] for item in receipt["checks"]), {"PASS"})
        self.assertGreater(len(receipt["checks"]), 15)
        self.assertEqual(json.loads(self.baseline.read_text()), SNAPSHOT)
        logdir = self.receipt.with_suffix(".json.jobs")
        self.assertEqual(sorted(path.name for path in logdir.iterdir()), [
            "migrate.log", "register-image-worker.log", "register-voice-worker.log", "register-worker.log",
        ])
        self.run_phase("migration", merged_at=NOW + 10, now=NOW + 20)

    def test_preflight_failed_checks_leave_a_failed_receipt(self):
        for failed_check, override in (
            ("Wan ready and both active jobs null", {"active_foley": "busy"}),
            ("offline render app idle and predecessor venv complete", {"render_busy": True}),
            ("predecessor Job logs saved", {"bad_logs": True}),
        ):
            with self.subTest(check=failed_check):
                self.receipt = self.root / failed_check
                self.baseline = self.root / (failed_check + "-baseline")
                _, calls = self.run_phase("preflight", expected=1, **override)
                receipt = json.loads(self.receipt.read_text())
                self.assertFalse(receipt["passed"])
                self.assertIn({"label": failed_check, "result": "FAIL"}, receipt["checks"])
                self.assertFalse(any(call[0] == "flux" for call in calls))

    def test_all_later_blocks_refuse_missing_failed_stale_and_mismatched_receipts(self):
        for phase in POST_MERGE:
            for label, receipt, extra in (
                ("missing", None, {}),
                ("failed", {"passed": False}, {}),
                ("stale", {"started_epoch": NOW - 3700}, {}),
                ("future", {"finished_epoch": NOW + 1}, {}),
                ("wrong tree", {"ops_tree": "d" * 40}, {}),
                ("wrong selected revision", {}, {"selected": "d" * 40}),
                ("not merged", {}, {"on_main": False}),
                ("dirty checkout", {}, {"dirty": " M docs/reference/studio-fourth-release.md"}),
            ):
                with self.subTest(phase=phase, case=label):
                    self.receipt.unlink(missing_ok=True)
                    if receipt is not None:
                        self.admit(**receipt)
                    _, calls = self.run_phase(phase, expected=1, **extra)
                    self.assertEqual(calls, [])

    def test_migration_and_roll_check_flux_artifact_before_reconciliation(self):
        self.admit()
        for phase in ("migration", "roll"):
            with self.subTest(phase=phase):
                _, calls = self.run_phase(phase, expected=1, artifact="d" * 40)
                self.assertTrue(any("gitrepository" in call for call in calls))
                self.assertFalse(any(call[0] == "flux" for call in calls))
        _, calls = self.run_phase("migration")
        self.assertEqual([call[3] for call in calls if call[0] == "flux"], ["cluster-apps", "studio-migration"])
        _, calls = self.run_phase("roll")
        self.assertEqual([call[3] for call in calls if call[0] == "flux"], [
            "studio-render", "studio", "studio-worker-registration", "studio-worker",
            "studio-media-worker-registration", "studio-media-workers",
            "studio-matte-worker", "studio-matte-worker-registration",
        ])
        _, calls = self.run_phase("roll", expected=1, migration_wait_failure=True)
        self.assertFalse(any(call[0] == "flux" for call in calls))

    def test_postflight_rechecks_baseline_and_holds_idle_claim_on_difference(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        self.admit()
        _, calls = self.run_phase("postflight", workloads="new")
        self.assertTrue(any("idle-claim" in call for call in calls))
        _, calls = self.run_phase("postflight", expected=1, workloads="new", snapshot={**SNAPSHOT, "runs": 3})
        self.assertFalse(any("idle-claim" in call for call in calls))
        self.baseline.write_text("{}")
        _, calls = self.run_phase("postflight", expected=1, workloads="new")
        self.assertEqual(calls, [])

    def test_rollback_accepts_either_ready_verified_build_and_requires_committed_schema(self):
        self.admit()
        for pods, revision in (("old", OLD[0]), ("new", RELEASE["revision"])):
            with self.subTest(pods=pods):
                self.baseline.unlink(missing_ok=True)
                _, calls = self.run_phase("rollback-baseline", workloads=pods)
                self.assertEqual([call[-1] for call in calls if "rollback-baseline" in call], [revision])
                self.assertEqual(json.loads(self.baseline.read_text()), SNAPSHOT)
        self.run_phase("compare")
        self.run_phase("compare", expected=1, snapshot={**SNAPSHOT, "runs": 3})
        self.baseline.unlink()
        self.run_phase("rollback-baseline", expected=1, wrong_schema=True)
        self.run_phase("rollback-baseline", expected=1, api_unready=True)
        self.assertFalse(self.baseline.exists())


class StartupTest(unittest.TestCase):
    def test_matte_start_reads_private_file_and_waits_for_grant_before_exec(self):
        path = STUDIO / "matting-pending/activation/worker/matte-start.py"
        spec = importlib.util.spec_from_file_location("matte_start", path)
        launcher = importlib.util.module_from_spec(spec)
        secret = "s" * 64
        progress = Mock()
        post = Mock(side_effect=[
            SimpleNamespace(status_code=403),
            SimpleNamespace(status_code=422, json=lambda: {
                "error": {"code": "schema.unknown_field", "path": "/registration_probe"},
            }),
        ])
        context = Mock()
        context.__enter__ = Mock(return_value=SimpleNamespace(post=post))
        context.__exit__ = Mock(return_value=False)
        client = Mock(return_value=context)
        dependencies = {
            "httpx": SimpleNamespace(Client=client, RequestError=RuntimeError),
            "haynes_studio.queue.progress": SimpleNamespace(ProgressFile=Mock(return_value=progress)),
            "haynes_studio.queue.worker_tokens": SimpleNamespace(worker_bearer=lambda raw: "worker_" + raw),
        }
        with patch.dict(sys.modules, dependencies):
            spec.loader.exec_module(launcher)
        with (
            patch.object(launcher, "Path", return_value=SimpleNamespace(read_text=lambda: secret)) as token_path,
            patch.object(launcher, "sleep") as sleep,
            patch.object(launcher.os, "execvpe") as execute,
            patch.object(launcher.sys, "argv", [str(path), "studio-worker", "--lane", "matte"]),
            patch.dict(os.environ, {"STUDIO_WORKER_PROGRESS_FILE": "/run/studio/worker-progress",
                                    "STUDIO_API_URL": "http://studio-api:8002"}, clear=True),
        ):
            launcher.main()
        token_path.assert_called_once_with("/run/secrets/studio-worker/token")
        sleep.assert_called_once_with(5)
        self.assertEqual(client.call_args.kwargs["headers"], {"Authorization": "Bearer worker_" + secret})
        self.assertEqual(post.call_count, 2)
        post.assert_called_with("/internal/v1/work/claim", json={"lane": "matte", "registration_probe": True})
        command, args, environment = execute.call_args.args
        self.assertEqual((command, args), ("studio-worker", ["studio-worker", "--lane", "matte"]))
        self.assertEqual(environment["STUDIO_WORKER_TOKEN"], secret)

    def test_render_startup_keeps_previous_venv_on_failed_install_and_rollback(self):
        app = STUDIO.with_name("studio-render") / "app"
        release = yaml.safe_load((app / "helmrelease.yaml").read_text())
        controller = release["spec"]["values"]["controllers"]["studio-render"]
        startup = controller["containers"]["app"]["args"][-1]
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="op967-venv-")))
        for name in ("config", "studio", "pod", "bin"):
            (root / name).mkdir()
        requirements = root / "config/requirements.txt"
        startup = (startup.replace("/studio/.venv", f"{root}/studio/.venv")
                   .replace("/config/requirements.txt", str(requirements))
                   .replace("/tmp/venv", f"{root}/pod/venv")
                   .replace("/tmp/ready", f"{root}/pod/ready"))
        sleep = root / "bin/sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n")
        sleep.chmod(0o700)
        python = root / "bin/python"
        python.write_text(r"""#!/bin/sh
if [ "$1" = "-c" ]; then
  printf '312\n'; exit 0
fi
[ "$1" = "-m" ] && [ "$2" = "venv" ] || exit 1
mkdir -p "$3/bin" || exit 1
cat > "$3/bin/python" <<'SH'
#!/bin/sh
[ "$5" = "$(cat "$(dirname "$0")/version")" ]
SH
cat > "$3/bin/pip" <<'SH'
#!/bin/sh
[ "$PIP_FAIL" = no ] || exit 1
sed -n 's/^av==//p' "$5" > "$(dirname "$0")/version"
SH
chmod +x "$3/bin/python" "$3/bin/pip"
""")
        python.chmod(0o700)

        def start(requirements_source, fail, status):
            payload = (app / requirements_source).read_bytes()
            requirements.write_bytes(payload)
            (root / "pod/ready").unlink(missing_ok=True)
            result = subprocess.run(["sh", "-c", startup], capture_output=True, text=True, timeout=5, env={
                "PATH": f"{root}/bin:{os.environ['PATH']}", "PIP_FAIL": "yes" if fail else "no",
            })
            self.assertEqual(result.returncode, status, result.stderr)
            self.assertEqual((root / "pod/ready").exists(), status == 0)
            return root / "studio/.venv" / f"py312-{hashlib.sha256(payload).hexdigest()[:12]}"

        old = start("requirements-op964.txt", fail=False, status=0)
        new = start("requirements.txt", fail=True, status=1)
        self.assertTrue((old / ".complete").is_file())
        self.assertFalse((new / ".complete").exists())
        start("requirements.txt", fail=False, status=0)
        start("requirements-op964.txt", fail=True, status=0)
        self.assertEqual((root / "pod/venv").resolve(), old)
        self.assertTrue((new / ".complete").is_file())


if __name__ == "__main__":
    unittest.main()
