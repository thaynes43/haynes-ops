"""Run each OP967 runbook block with no cluster access and errexit suppressed."""

import hashlib
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


STUDIO = Path("kubernetes/main/apps/ai/studio")
RELEASE = yaml.safe_load((STUDIO / "release.yaml").read_text())
OLD = ("80975a9e958d0bed78ab530c2b20f1279bf7ebe6",
       "sha256:08ac310f246cdc43225478e9496e480af6430b53751f4ae93f3e34206714c3e7",
       "sha256:497e020b8846f09cdeab6d822aa1536dfeb325edd26205cd6e28f0061bb6d9ca")
HEAD = "a" * 40
REVIEWED_HEAD = "b" * 40
TREE = "c" * 40
FAILED_HEAD = "d" * 40
FAILED_TREE = "e" * 40
RETRY_HEAD = "f" * 40
DESCENDANT = "1" * 40
PREVIOUS = "2" * 40
REWIRED = "3" * 40
EDITED = "4" * 40
ROLLBACK = "5" * 40
UNRELATED = "6" * 40
OFF_MAIN = "7" * 40
STAGES = ("studio-render", "studio-migration", "studio", "studio-worker-registration", "studio-worker",
          "studio-media-worker-registration", "studio-media-workers")
NOW = 1760000050
SNAPSHOT = {"store": {}, "runs": {}, "deliveries": {}, "delivery media": {}, "media jobs": {}}
RELEASE_CHECKS = (
    "new private receipt and baseline paths supplied",
    "reviewed Ops checkout has a clean tracked tree",
    "three postgres16 instances Ready",
    "installed dependencies and predecessor credential projections Ready",
    *(f"studio-{workload} pod image, placement and restart counts"
      for workload in ("api", "mcp", "web", "worker", "voice-worker", "image-worker")),
    "third-pin workload set",
    "old API endpoints",
    "talosw01 capacity",
    "talosw01 CPU <=10%, memory <=25%",
    "offline render app idle and admitted venv complete",
    "Wan ready and both active jobs null",
    "shared ComfyUI queue empty",
    "LAN root available",
    "LAN API anonymous access refused",
    "predecessor Job logs saved",
    "empty queues, archived data and store baseline",
)

# Every fixture command is selected via PATH. The stubs never shell out.
COMMANDS = r'''
import io
import json
import os
import re
import sys
import tarfile
from pathlib import Path

import yaml

cmd = Path(sys.argv[0]).name
args = sys.argv[1:]
env = os.environ
data = json.loads(env["FIXTURE"])
clock = Path(env["CLOCK_FILE"])
sleeps = int(clock.read_text()) if clock.exists() else 0
if sleeps:
    data.update(data.get("after_sleep", {}))
# A revision only becomes visible to mocked git after the operator fetches.
fetched = clock.with_name("fetched")
source = {"kind": "GitRepository", "metadata": {"name": "haynes-ops", "namespace": "flux-system"},
          "spec": {"url": "https://github.com/thaynes43/haynes-ops", "ref": {"branch": "main"}}}
inventory_path = "./kubernetes/main/apps"

def definition(name, commit):
    if name == "cluster-apps":
        path, namespace = inventory_path, "flux-system"
    else:
        path, namespace = data["paths"][name], "ai"
    if commit == data["rewired"] and name == "studio":
        path = "./kubernetes/main/apps/ai/studio-worker-rewired"
    spec = {"path": path, "sourceRef": {"kind": "GitRepository", "namespace": "flux-system", "name": "haynes-ops"}}
    if name == "studio":
        spec.update(targetNamespace="ai", postBuild={"substitute": {"APP": "studio"}})
    if name == data.get("suspended_stage"):
        spec["suspend"] = True
    return {"kind": "Kustomization", "metadata": {"name": name, "namespace": namespace}, "spec": spec}
revision, api, web = data["new"] if data["workloads"] == "new" else data["old"]
studio = Path("kubernetes/main/apps/ai/studio")
old_job_suffix = data["old"][1][7:19]

def emit(value):
    print(json.dumps(value))

def pods(workload):
    names = {
        "api": {"api": api, "media-worker-routes": web},
        "mcp": {"mcp": api}, "web": {"nginx": web}, "worker": {"worker": api},
        "voice-worker": {"worker": api}, "image-worker": {"worker": api},
        "render": {"app": api},
    }[workload]
    instances = 2 if workload in {"api", "mcp", "web"} else 1
    result = []
    for index in range(instances):
        pod = f"{workload}-{index}"
        containers = {
            name: "ghcr.io/thaynes43/haynes-studio" + ("-web" if digest == web else "") + "@" + digest
            for name, digest in names.items()
        }
        result.append({
            "metadata": {"name": pod, "uid": pod, "labels": {"app.kubernetes.io/name": "studio-" + workload}},
            "spec": {
                "nodeName": "talosw01" if workload in {"worker", "render"} else f"other-{index}",
                "containers": [
                    {"name": name, "image": image,
                     "resources": {"limits": {"cpu": data.get("render_cpus", "20")}},
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

def job_pods(job):
    container = "migrate" if job.startswith("studio-migrate-") else "register"
    modes = {"none": ("failed",), "many": ("failed", "succeeded", "also-succeeded"),
             "wrong-phase": ("wrong-phase",), "wrong-exit": ("wrong-exit",)}
    states = modes.get(data.get("pod_selection"), ("failed", "succeeded"))
    if data.get("pod_order") == "reverse":
        states = states[::-1]
    def pod(state, uid):
        failed = state == "failed"
        return {
            "metadata": {"name": f"{job}-{state}",
                         "ownerReferences": [{"uid": uid, "kind": "Job", "controller": True}]},
            "status": {"phase": "Failed" if failed or state == "wrong-phase" else "Succeeded",
                       "containerStatuses": [{"name": container, "state": {"terminated": {
                           "exitCode": 1 if failed or state == "wrong-exit" else 0}}},
                           {"name": "unrelated", "state": {"terminated": {"exitCode": 0}}}]},
        }
    # A stale pod can carry the selector label. Ownership still must match.
    return [pod(state, job) for state in states] + [pod("unrelated", "different-uid")]

def stage(name):
    revision = data.get("stage_revisions", {}).get(name, data["artifact"])
    document = definition(name, data["artifact"])
    document["metadata"]["generation"] = 2
    document["spec"]["force"] = False
    if name == "studio":
        document["spec"].update(data.get("live_spec_override", {}))
    document["status"] = {
        "lastAppliedRevision": "main@sha1:" + revision,
        "lastAttemptedRevision": "main@sha1:" + data.get("attempted_revisions", {}).get(name, revision),
        "observedGeneration": 1 if name == data.get("stale_stage") else 2,
        "conditions": [{"type": "Ready", "status": "False" if name == data.get("unready_stage") else "True",
                        "observedGeneration": 1 if name == data.get("stale_ready_condition") else 2}],
    }
    return document

if cmd in {"kubectl", "curl", "sleep"} or (cmd == "git" and args and args[0] in {"fetch", "cat-file"}):
    with open(env["COMMAND_LOG"], "a") as log:
        log.write(json.dumps([cmd, *args]) + "\n")

if cmd == "git":
    match args:
        case ["fetch", "--quiet", "origin", "main:refs/remotes/origin/main"]:
            fetched.touch()
            sys.exit(bool(data.get("fetch_failed")))
        case ["cat-file", "-e", commit]:
            commit = commit.removesuffix("^{commit}")
            unknown = commit == data.get("missing_commit") or (
                commit == data.get("need_fetch") and not fetched.exists())
            sys.exit(unknown)
        case ["archive", "--output", output, commit, "kubernetes"]:
            cluster = definition("cluster-apps", commit)
            cluster["metadata"]["name"] = "cluster"
            archive_files = {
                "kubernetes/main/flux/config/cluster.yaml": yaml.safe_dump_all([source, cluster]),
                # This represents a patch to the stage outside the Studio directories.
                "kubernetes/main/flux/stage-path-patch": commit,
            }
            with tarfile.open(output, "w") as archive:
                for name, content in archive_files.items():
                    payload = content.encode()
                    info = tarfile.TarInfo(name)
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
        case ["rev-parse", "--verify", "HEAD"]:
            print(data["head"])
        case ["rev-parse", "--verify", "HEAD^{tree}"]:
            print(data["tree"])
        case ["rev-parse", "--verify", ref] if ref.endswith("^{tree}"):
            tree = data["known_trees"].get(ref.removesuffix("^{tree}"))
            if tree is None:
                sys.exit(1)
            print(tree)
        case ["rev-parse", "--verify", tree] if ":kubernetes/" in tree:
            commit, path = tree.split(":", 1)
            print("changed-tree" if commit == data["edited"] and path.endswith("/studio/app") else path)
        case ["status", "--porcelain", "--untracked-files=no"]:
            print(data.get("dirty", ""))
        case ["merge-base", "--is-ancestor", commit, "refs/remotes/origin/main"]:
            sys.exit(not data.get("on_main", True) or commit in data.get("off_main", []))
        case ["merge-base", "--is-ancestor", "refs/remotes/origin/main", _]:
            sys.exit(not data.get("includes_main", True))
        case ["merge-base", "--is-ancestor", ancestor, descendant]:
            sys.exit(ancestor != descendant and [ancestor, descendant] not in data["ancestry"])
        case ["show", "-s", "--format=%ct", ref]:
            print(data.get("known_merges", {}).get(ref, data["merged_at"]))
        case ["show", ref] if ":kubernetes/main/" in ref:
            path = ref.split(":", 1)[1]
            print(data.get("previous_source", {}).get(path) or Path(path).read_text(), end="")
        case ["diff", "--name-only", *_]:
            print(f"{studio}/ks.yaml\n{studio}/migration/job.yaml")
            if data.get("retry_changed_paths"):
                print(f"{studio}/app/api.yaml")
        case _:
            sys.exit(99)
elif cmd == "date":
    print(data["now"] + sleeps * data.get("clock_step", 15)
          if args[-1] == "+%s" else "2026-11-25T17:00:00Z")
elif cmd == "sleep":
    if sleeps >= 2:
        sys.exit(99)
    clock.write_text(str(sleeps + 1))
elif cmd == "yq":
    query, filename = args[-2:]
    docs = [doc for doc in yaml.safe_load_all(sys.stdin.read() if filename == "-" else Path(filename).read_text()) if doc]
    if "healthChecks" in query:
        stage = next(doc for doc in docs if doc["metadata"]["name"] == "studio-migration")
        checks = stage["spec"].get("healthChecks", [])
        print(len(checks) if query.endswith("| length") else checks[0]["name"])
    elif query.startswith("select"):
        print(next(doc["metadata"]["name"] for doc in docs if doc["kind"] == "Job"))
    else:
        result = docs[0]
        for key in query.lstrip(".").split("."):
            result = result[key]
        print(result)
elif cmd == "curl":
    sys.exit(99)
elif cmd == "flux":
    if args[:2] != ["build", "kustomization"] or args[-2:] != ["--dry-run", "--in-memory-build"]:
        sys.exit(99)
    name = args[2]
    patch = Path("kubernetes/main/flux/stage-path-patch")
    commit = patch.read_text() if patch.exists() else ""
    if name == "cluster":
        print(yaml.safe_dump(definition("cluster-apps", commit)))
    elif name == "cluster-apps":
        print(yaml.safe_dump_all([definition(stage, commit) for stage in data["definitions"]]))
elif cmd == "kubectl":
    if args[:1] == ["-n"]:
        args = args[2:]
    match args:
        case ["get", "gitrepository", "haynes-ops", "-o", "json"]:
            emit({**source, "spec": {**source["spec"], **data.get("live_source_override", {})},
                  "status": {"artifact": {"revision": "main@sha1:" + data["artifact"]}}})
        case ["get", "kustomization", "cluster-apps", "-o", "json"]:
            emit(stage("cluster-apps"))
        case ["get", "kustomizations", "-o", "json"]:
            if data.get("second_flux_read_unready"):
                reads = [line for line in Path(env["COMMAND_LOG"]).read_text().splitlines()
                         if json.loads(line) == ["kubectl", "-n", "ai", "get", "kustomizations", "-o", "json"]]
                if len(reads) == 2:
                    data["unready_stage"] = "studio"
            emit({"items": [stage(name) for name in data["stages"] if name not in data.get("missing_stages", [])]})
        case ["get", "deployments", "-o", "json"]:
            workloads = ("api", "mcp", "web", "worker", "voice-worker", "image-worker")
            items = []
            for workload in workloads:
                pod_set = pods(workload)
                spec = pod_set[0]["spec"]
                if workload == "api" and data.get("worker_before_api"):
                    spec["containers"][0]["image"] = "ghcr.io/thaynes43/haynes-studio@" + data["old"][1]
                    spec["containers"][1]["image"] = "ghcr.io/thaynes43/haynes-studio-web@" + data["old"][2]
                if workload == data.get("wrong_deployment"):
                    spec["containers"][0]["image"] = "unexpected"
                items.append({"metadata": {"name": "studio-" + workload}, "spec": {"template": {"spec": spec}}})
            emit({"items": items})
        case ["get", "pods", "-l", label, "-o", "json"] if label.startswith("batch.kubernetes.io/controller-uid="):
            emit({"items": job_pods(label.split("=", 1)[1])})
        case ["get", "pods", "-l", label, "-o", "json"]:
            emit({"items": pods(label.split("=studio-", 1)[1])})
        case ["get", "pods", *_]:
            if data.get("sample_failure"):
                sys.exit(1)
            print("sample")
        case ["get", resource, "-o", "json"] if resource.startswith("job/"):
            job = resource.removeprefix("job/")
            if data.get("predecessor_pruned") and job.endswith(old_job_suffix):
                sys.exit(1)
            condition = "Failed" if job == data.get("terminal_job") else "Complete"
            emit({"metadata": {"uid": job}, "status": {"conditions": [{"type": condition,"status": "True"}],
                                                      "active": data.get("terminal_active", 0)},
                  "spec": {"template": {"spec": {"containers": [{"name": "migrate",
                      "image": "ghcr.io/thaynes43/haynes-studio@" + data["new"][1]}]}}}})
        case ["get", "endpointslices", *_]:
            emit({"items": [{"endpoints": [
                {"conditions": {"ready": True}, "targetRef": {"uid": pod["metadata"]["uid"]}}
                for pod in pods("api")
            ]}]})
        case ["get", "cluster", "postgres16", *_]:
            emit({"spec": {"instances": 3}, "status": {"readyInstances": 3, "currentPrimary": "postgres16-primary"}})
        case ["get", "node", "talosw01", *_]:
            emit({"status": {"allocatable": {"cpu": "39", "memory": "111129616Ki"}}})
        case ["top", "node", *_]:
            print("talosw01 100m 2% 100Mi 10%")
        case ["top", "pod", *_]:
            print("render-0 app 50m 100Mi")
        case ["wait", *_]:
            sys.exit(bool(data.get("migration_wait_failure") and "condition=Complete" in " ".join(args)))
        case ["logs", pod, "-c", container] if pod.endswith(("-succeeded", "-wrong-phase", "-wrong-exit")):
            job = re.sub(r"-(succeeded|wrong-phase|wrong-exit)$", "", pod)
            if data.get("bad_logs") and "voice" in job:
                sys.exit(1)
            print("diagnostic before JSON")
            emit({"irrelevant": "first JSON object"})
            if container == "migrate":
                if job.endswith(old_job_suffix):
                    print("Alembic revision: 0012_release_constraints")
                else:
                    emit({"revision": "0013_matte_jobs", "user": "studio_migrator", "database": "studio",
                          "tls": True, "hba_rejected": ["other database", "plaintext"]})
            else:
                worker = re.sub(r"-[a-f0-9]{12}(?:-r[1-9][0-9]*)?$", "", job.removeprefix("studio-register-"))
                status = 200
                if worker == "image-worker" and data.get("bad_registration_audit"):
                    status = 409
                emit({"action": "worker_tokens.register", "worker_id": "studio-" + worker, "status": status})
                result = "wrong" if worker == "image-worker" and data.get("bad_registration_result") else worker
                emit({"worker_id": "studio-" + result})
        case ["exec", *options]:
            if options[:2] == ["-i", "deploy/studio-mcp"]:
                program = options[-2]
                if not all(expected in program for expected in (
                    "traefik-internal.network.svc.cluster.local", "server_hostname=host",
                    'host = "studio.haynesops.com"', "GET {path}", "Host: {host}",
                )):
                    sys.exit(88)
                print("401" if options[-1] == "/v1/me" else "200")
            elif options[:2] == ["-i", "postgres16-primary"]:
                if "default_transaction_read_only=on" not in " ".join(options):
                    sys.exit(89)
                print(data.get("catalogue", "0012_release_constraints"))
            elif "/v1/ready" in options[-1]:
                emit({"ready": True, "active_video_job": None, "active_foley_job": data.get("active_foley")})
            elif options[-1].endswith("/queue"):
                emit({"queue_running": [], "queue_pending": []})
            elif options[-2] in {"preflight", "retry-preflight", "retry-preflight-0013",
                                "postflight", "rollback-baseline", "compare"}:
                mode, expected_build = options[-2:]
                if expected_build != revision or options[1] not in {"api-0", "api-1"}:
                    sys.exit(87)
                if data.get("wrong_schema") and mode == "rollback-baseline":
                    print("FAIL expected schema", file=sys.stderr)
                    sys.exit(1)
                if mode == "postflight" and data.get("matte_queue_busy"):
                    print("FAIL matte queue empty (queued, running or held)", file=sys.stderr)
                    sys.exit(1)
                print("diagnostic before JSON")
                emit({"wrong": "first JSON object"})
                emit(data["snapshot"])
            elif any("Idle render venv complete" in arg for arg in options):
                if "Idle render venv complete" in options[-2]:
                    version = data.get("render_version", "18.1.0")
                    if (data.get("render_busy") or version not in {"18.1.0", "18.1.1"}
                            or (version == "18.1.1" and options[-1] != "retry")):
                        sys.exit(1)
                    print(f"Idle render venv complete (PyAV {version})")
            elif any("av.library_versions" in arg for arg in options):
                emit({"pyav": "18.1.0" if options[0] == data.get("stale_encoder") else "18.1.1", "libraries": {f"libav{name}": [1, 2, 3] for name in (
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

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="op967-phases-")))
        self.receipt = self.root / "receipt.json"
        self.baseline = self.root / "baseline.json"
        self.rollback_baseline = self.root / "rollback-baseline.json"
        self.log = self.root / "commands"
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        shutil.copytree(STUDIO, self.checkout / STUDIO)
        bootstrap = Path("kubernetes/main/flux/config/cluster.yaml")
        (self.checkout / bootstrap.parent).mkdir(parents=True)
        (self.checkout / bootstrap).write_text(bootstrap.read_text())
        (self.checkout / "scripts").symlink_to(Path("scripts").resolve())
        stub = self.root / "command"
        stub.write_text(f"#!{sys.executable}\n" + COMMANDS)
        stub.chmod(0o700)
        for name in ("git", "date", "kubectl", "flux", "curl", "yq", "sleep"):
            (self.root / name).symlink_to(stub)
        self.data = {
            "head": HEAD, "tree": TREE, "artifact": HEAD, "merged_at": NOW - 10, "now": NOW,
            "known_trees": {REVIEWED_HEAD: TREE, HEAD: TREE, FAILED_HEAD: FAILED_TREE},
            "known_merges": {},
            "ancestry": [[PREVIOUS, HEAD], [HEAD, REVIEWED_HEAD],
                         *([HEAD, sha] for sha in (DESCENDANT, EDITED, REWIRED, ROLLBACK, OFF_MAIN))],
            "stages": STAGES, "definitions": STAGES,
            "paths": {stage: f"./kubernetes/main/apps/ai/studio/{stage}" for stage in STAGES}
            | {"studio": "./kubernetes/main/apps/ai/studio/app"},
            "edited": EDITED, "rewired": REWIRED,
            "new": [RELEASE[key] for key in ("revision", "apiDigest", "webDigest")],
            "old": OLD, "workloads": "old", "snapshot": SNAPSHOT,
        }

    def admit(self, **changes):
        data = {
            "schema": "studio-op967/v3", "mode": "release", "ops_revision": REVIEWED_HEAD, "ops_tree": TREE,
            "started_epoch": NOW - 100, "finished_epoch": NOW - 50, "passed": True,
            "baseline_path": str(self.baseline),
            "baseline_sha256": hashlib.sha256(self.baseline.read_bytes()).hexdigest()
            if self.baseline.exists() else "0" * 64,
            "checks": [{"label": label, "result": "PASS"} for label in RELEASE_CHECKS],
            **changes,
        }
        self.receipt.write_text(json.dumps(data))

    def run_phase(self, phase, *, expected=0, selected=HEAD, extra_env=None, mode="release", **changes):
        self.log.write_text("")
        clock = self.root / "clock"
        clock.unlink(missing_ok=True)
        clock.with_name("fetched").unlink(missing_ok=True)
        env = {
            "PATH": f"{self.root}:{Path(sys.executable).parent}:{os.environ['PATH']}",
            "FIXTURE": json.dumps({**self.data, **changes}),
            "COMMAND_LOG": str(self.log), "CLOCK_FILE": str(clock),
            "STUDIO_OP967_RECEIPT": str(self.receipt),
            "STUDIO_OP967_BASELINE": str(self.baseline), "MERGED_OPS_REVISION": selected,
            "STUDIO_OP967_ROLLBACK_BASELINE": str(self.rollback_baseline),
            "ROLLBACK_OPS_REVISION": ROLLBACK if phase == "compare" else "", **(extra_env or {}),
        }
        # The documented if ! bash ... suppresses errexit throughout the sourced
        # phase. This exercises the explicit gates in the same shell context.
        block = (f'if ! bash scripts/studio-release/op967.sh admit-merge {mode}; then exit 1; fi'
                 if phase == "admit-merge" else self.blocks[phase])
        runner = 'set -e\nbash() { ( source "$@" ); }\n' + block
        result = subprocess.run(
            ["bash", "--noprofile", "--norc"], input=runner, env=env,
            text=True, capture_output=True, timeout=20, cwd=self.checkout,
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
        self.assertCountEqual([item["label"] for item in receipt["checks"]], RELEASE_CHECKS)
        self.assertEqual(Path(receipt["baseline_path"]), self.baseline)
        self.assertEqual(json.loads(self.baseline.read_text()), SNAPSHOT)
        logdir = self.receipt.with_suffix(".json.jobs")
        self.assertEqual(sorted(path.name for path in logdir.iterdir()), [
            "migrate.log", "register-image-worker.log", "register-voice-worker.log", "register-worker.log",
        ])
        self.run_phase("admit-merge", head=REVIEWED_HEAD)
        self.assert_archive_damage_refused(logdir, mode="release", head=REVIEWED_HEAD)
        _, calls = self.run_phase("admit-merge", head=REVIEWED_HEAD, now=NOW + 3600, expected=1)
        self.assertFalse(any(call[0] == "kubectl" for call in calls))
        _, calls = self.run_phase("admit-merge", head=REVIEWED_HEAD, includes_main=False, expected=1)
        self.assertFalse(any(call[0] == "kubectl" for call in calls))

    def test_preflight_failed_checks_leave_a_failed_receipt(self):
        for failed_check, override in (
            ("Wan ready and both active jobs null", {"active_foley": "busy"}),
            ("offline render app idle and admitted venv complete", {"render_busy": True}),
            ("predecessor Job logs saved", {"bad_logs": True}),
        ):
            with self.subTest(check=failed_check):
                self.receipt = self.root / failed_check
                self.baseline = self.root / (failed_check + "-baseline")
                _, calls = self.run_phase("preflight", expected=1, **override)
                receipt = json.loads(self.receipt.read_text())
                self.assertFalse(receipt["passed"])
                self.assertIn({"label": failed_check, "result": "FAIL"}, receipt["checks"])
                self.assertFalse(any("exec" in call and "postflight" in call for call in calls))

    def test_merge_requires_fresh_passing_complete_receipt_for_reviewed_head_and_baseline(self):
        self.run_phase("preflight", head=REVIEWED_HEAD)
        original = json.loads(self.receipt.read_text())
        archive = {key: original[key] for key in ("jobs_path", "jobs_sha256")}
        passing = [{"label": label, "result": "PASS"} for label in RELEASE_CHECKS]
        for case, receipt, overrides in (
            ("missing", None, {}),
            ("failed", {"passed": False}, {}),
            ("wrong mode", {"mode": "retry"}, {}),
            ("wrong tree", {"ops_tree": FAILED_TREE}, {}),
            ("wrong review", {"ops_revision": FAILED_HEAD}, {}),
            ("stale", {"started_epoch": NOW - 3700}, {}),
            ("future", {"finished_epoch": NOW + 1}, {}),
            ("non-integer", {"started_epoch": NOW - 100.5}, {}),
            ("wrong hash", {"baseline_sha256": "0" * 64}, {}),
            ("wrong path", {"baseline_path": str(self.root / "other")}, {}),
            ("relative archive path", {"jobs_path": os.path.relpath(archive["jobs_path"], self.checkout)}, {}),
            ("wrong schema", {"schema": "studio-op967/v2"}, {}),
            ("partial checks", {"checks": passing[-2:]}, {}),
            ("duplicate check", {"checks": [*passing, passing[0]]}, {}),
            ("failed check", {"checks": [{**passing[0], "result": "FAIL"}, *passing[1:]]}, {}),
            ("dirty", {}, {"dirty": " M docs/reference/studio-fourth-release.md"}),
        ):
            with self.subTest(case=case):
                self.receipt.unlink(missing_ok=True)
                if receipt is not None:
                    self.admit(**{**archive, **receipt})
                _, calls = self.run_phase("admit-merge", expected=1, head=REVIEWED_HEAD, **overrides)
                self.assertFalse(any(call[0] == "kubectl" for call in calls))

    def test_retry_admission_refuses_archive_lost_after_preflight(self):
        previous, _, env = self.prepare_retry()
        archive = Path(json.loads(previous.read_text())["jobs_path"])
        self.run_phase("retry-preflight", head=RETRY_HEAD, extra_env=env)
        self.run_phase("admit-merge", mode="retry", head=RETRY_HEAD)
        self.assert_archive_damage_refused(archive, mode="retry", head=RETRY_HEAD)

    def assert_archive_damage_refused(self, archive, *, mode, head):
        saved_job = archive / "migrate.log"
        original = saved_job.read_bytes()
        parked = archive.with_suffix(".parked")
        for damage in ("missing", "altered"):
            with self.subTest(mode=mode, damage=damage):
                if damage == "missing":
                    archive.rename(parked)
                else:
                    saved_job.write_bytes(b"edited")
                try:
                    result, _ = self.run_phase("admit-merge", expected=1, mode=mode, head=head)
                    self.assertIn("predecessor Job archive missing or changed", result.stderr)
                finally:
                    if damage == "missing":
                        parked.rename(archive)
                    else:
                        saved_job.write_bytes(original)

    def test_flux_waits_for_predecessor_and_fails_at_pickup_deadline(self):
        result, calls = self.run_phase("wait", artifact=PREVIOUS, now=NOW + 35 * 60, expected=1)
        self.assertIn("within 35 minutes", result.stderr)
        self.assertFalse(any(call[0] == "sleep" for call in calls))
        result, calls = self.run_phase("wait", artifact=PREVIOUS, after_sleep={"artifact": HEAD})
        self.assertIn("Pending Flux source pickup", result.stdout)
        self.assertEqual([call for call in calls if call[0] == "sleep"], [["sleep", "15"]])

    def test_flux_accepts_merge_and_matching_descendant_fetching_before_comparison(self):
        for observed in (HEAD, DESCENDANT):
            with self.subTest(observed=observed):
                result, calls = self.run_phase("wait", artifact=observed, need_fetch=observed)
                self.assertIn("ready at pin True", result.stdout)
                first_object = next(i for i, call in enumerate(calls) if call[:2] == ["git", "cat-file"])
                self.assertEqual(calls[first_object - 1],
                                 ["git", "fetch", "--quiet", "origin", "main:refs/remotes/origin/main"])
                self.assertFalse(self.receipt.exists())

    def test_flux_refuses_changed_stage_content_and_path_and_diagnoses_missing_revision(self):
        for observed, reason in ((EDITED, "different pin"), (REWIRED, "different pin"),
                                 (UNRELATED, "missing after fetching main")):
            with self.subTest(observed=observed):
                result, calls = self.run_phase("verify", artifact=observed, missing_commit=UNRELATED, expected=1)
                self.assertIn(reason, result.stderr)
                self.assertIn(observed, result.stderr)
                self.assertFalse(any("logs" in call or "exec" in call for call in calls))

    def test_flux_requires_the_exact_selected_checkout_and_observed_revision_on_main(self):
        result, calls = self.run_phase("wait", head=DESCENDANT, expected=1)
        self.assertIn("FAIL selected fourth-pin Ops revision on main", result.stderr)
        self.assertFalse(any(call[0] == "kubectl" for call in calls))
        result, _ = self.run_phase("wait", artifact=OFF_MAIN, off_main=[OFF_MAIN], expected=1)
        self.assertIn(f"Flux revision {OFF_MAIN} is outside fetched main", result.stderr)

    def test_flux_refuses_live_spec_changes_with_the_field_named(self):
        result, _ = self.run_phase("wait", expected=1, live_source_override={"ref": {"branch": "other"}})
        self.assertIn("live GitRepository spec differs", result.stderr)
        for override, field in (
            ({"postBuild": {"substitute": {"APP": "altered"}}}, "postBuild.substitute.APP"),
            ({"patches": [{"patch": "[]", "target": {"kind": "Deployment"}}]}, "patches"),
            ({"targetNamespace": "other"}, "targetNamespace"),
            ({"path": "./wrong"}, "path"),
            ({"sourceRef": {"kind": "GitRepository", "name": "other", "namespace": "flux-system"}}, "sourceRef.name"),
            ({"force": True}, "force"),
            ({"suspend": True}, "suspend"),
        ):
            with self.subTest(field=field):
                result, _ = self.run_phase("wait", expected=1, live_spec_override=override)
                self.assertIn(f"live studio spec.{field} differs", result.stderr)

    def test_flux_refuses_ready_with_suspension_or_an_extra_studio_stage(self):
        result, _ = self.run_phase("verify", expected=1, suspended_stage="studio")
        self.assertIn(f"Flux studio: applied main@sha1:{HEAD}, ready at pin False", result.stdout)
        extra = "studio-extra"
        result, _ = self.run_phase("verify", expected=1, stages=(*STAGES, extra),
                                   paths={**self.data["paths"], extra: "./extra"})
        self.assertIn("FAIL Flux stages Ready at the selected release", result.stderr)

    def test_flux_stops_at_readiness_deadline_after_pickup(self):
        result, calls = self.run_phase("wait", expected=1, unready_stage="studio", clock_step=35 * 60)
        self.assertIn("still pending 35 minutes after observing pickup", result.stderr)
        self.assertEqual([call for call in calls if call[0] == "sleep"], [["sleep", "15"]])

    def test_flux_requires_applied_pin_and_current_generation(self):
        for changes in (
            {"stage_revisions": {"studio": PREVIOUS}, "attempted_revisions": {"studio": HEAD}},
            {"stale_stage": "studio"},
            {"stale_ready_condition": "studio"},
            {"unready_stage": "cluster-apps"},
        ):
            with self.subTest(changes=changes):
                result, _ = self.run_phase("verify", expected=1, **changes)
                self.assertIn("ready at pin False", result.stdout)
        result, _ = self.run_phase("verify", expected=1, stage_revisions={"studio": EDITED})
        self.assertIn("different pin", result.stderr)

    def test_flux_requires_stage_in_ready_inventory_but_waits_for_pending_inventory(self):
        result, _ = self.run_phase("wait", expected=1, missing_stages=["studio-worker"])
        self.assertIn("ready Flux inventory is missing Studio stages", result.stderr)
        result, _ = self.run_phase("wait", missing_stages=list(STAGES[-2:]),
                                   stage_revisions={"cluster-apps": PREVIOUS},
                                   after_sleep={"missing_stages": [], "stage_revisions": {}})
        self.assertIn("ready at pin False", result.stdout)
        self.assertIn("ready at pin True", result.stdout)

    def test_wait_checks_worker_image_worker_order_and_migration_audit(self):
        for changes in (
            {"wrong_deployment": "voice-worker"},
            {"worker_before_api": True},
            {"migration_wait_failure": True},
        ):
            with self.subTest(changes=changes):
                _, calls = self.run_phase("wait", expected=1, workloads="new", **changes)
                self.assertFalse(any(call[0] == "sleep" for call in calls))
        _, calls = self.run_phase("wait", workloads="new")
        self.assertTrue(any("logs" in call and "migrate" in call for call in calls))
        result, calls = self.run_phase("wait", expected=1, workloads="new", artifact=PREVIOUS,
                                       after_sleep={"artifact": HEAD})
        self.assertIn("workload advanced before the source reached", result.stderr)
        self.assertFalse(any(call[0] == "sleep" for call in calls))

    def test_jobs_select_the_succeeded_pod_of_each_job_in_both_orders(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        for order in ("normal", "reverse"):
            with self.subTest(order=order):
                _, calls = self.run_phase("postflight", workloads="new", pod_order=order)
                logs = [call[4] for call in calls if call[:4] == ["kubectl", "-n", "ai", "logs"]]
                self.assertEqual(len(logs), 4)
                self.assertTrue(all(name.endswith("-succeeded") for name in logs), logs)
                self.assertEqual(len([name for name in logs if "-register-" in name]), 3)

    def test_complete_job_refuses_missing_or_multiple_succeeded_pods(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        for selection, detail in (
            ("none", "no succeeded pod"), ("many", "more than one succeeded pod"),
            ("wrong-phase", "no succeeded pod"), ("wrong-exit", "no succeeded pod"),
        ):
            with self.subTest(selection=selection):
                result, calls = self.run_phase("postflight", workloads="new", expected=1, pod_selection=selection)
                self.assertIn(detail + " for UID", result.stderr)
                self.assertFalse(any("logs" in call for call in calls))

    def test_registration_requires_the_last_worker_result_and_matching_status_audit(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        for failure in ("bad_registration_audit", "bad_registration_result"):
            with self.subTest(failure=failure):
                result, _ = self.run_phase("postflight", workloads="new", expected=1, **{failure: True})
                self.assertIn("FAIL image worker registration reused its token", result.stderr)

    def test_postflight_requires_matching_baseline_and_empty_matte_queue(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        _, calls = self.run_phase("postflight", workloads="new")
        self.assertEqual(len(self.route_calls(calls)), 3)
        for changes in ({"snapshot": {**SNAPSHOT, "runs": 3}}, {"matte_queue_busy": True}):
            with self.subTest(changes=changes):
                _, calls = self.run_phase("postflight", expected=1, workloads="new", **changes)
                self.assertEqual(self.route_calls(calls), [])
        self.baseline.write_text("{}")
        _, calls = self.run_phase("postflight", expected=1, workloads="new")
        self.assertEqual(self.route_calls(calls), [])

    def test_postflight_requires_pyav_on_both_api_pods_all_workers_and_render(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        expected_pods = {"api-0", "api-1", "worker-0", "voice-worker-0", "image-worker-0", "render-0"}
        _, calls = self.run_phase("postflight", workloads="new")
        self.assertEqual({call[4] for call in calls if any("av.library_versions" in arg for arg in call)}, expected_pods)
        result, _ = self.run_phase("postflight", workloads="new", expected=1, stale_encoder="image-worker-0")
        self.assertIn("FAIL one PyAV 18.1.1 encoder and bundled libraries", result.stderr)
        result, _ = self.run_phase("postflight", workloads="new", expected=1, render_cpus="17")
        self.assertIn("FAIL offline render app at 20 CPUs with forwarding init", result.stderr)

    @staticmethod
    def route_calls(calls):
        return [call for call in calls
                if call[-2] == "-" and call[-1] in {"voice", "image", "run"}]

    def test_verify_without_a_receipt_reads_every_audit(self):
        result, calls = self.run_phase("verify", now=NOW + 7200, workloads="new",
                                       extra_env={"STUDIO_OP967_BASELINE": ""})
        self.assertIn("PASS OP967 verify", result.stderr)
        for expected in ("gitrepository", "cluster-apps", "kustomizations", "postflight"):
            self.assertTrue(any(expected in call for call in calls), expected)
        logs = [call[4] for call in calls if call[:4] == ["kubectl", "-n", "ai", "logs"]]
        for job in ("migrate", "register-worker", "register-voice-worker", "register-image-worker"):
            self.assertTrue(any(pod.startswith(f"studio-{job}-") for pod in logs), job)
        self.assertEqual({call[-1] for call in self.route_calls(calls)}, {"voice", "image", "run"})
        ingress = [call[-1] for call in calls if call[:6] == ["kubectl", "-n", "ai", "exec", "-i", "deploy/studio-mcp"]]
        self.assertEqual(ingress, ["/", "/v1/me"])
        self.assertFalse(any(call[0] == "curl" for call in calls))
        self.assertFalse(any(call[0] == "flux" for call in calls))

    def test_failed_state_stops_before_the_first_listener_post(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        for phase in ("postflight", "verify"):
            for changes in (
                {"unready_stage": "studio"},
                {"migration_wait_failure": True},
                {"bad_registration_audit": True},
                {"snapshot": {**SNAPSHOT, "runs": 3}},
            ):
                with self.subTest(phase=phase, changes=changes):
                    _, calls = self.run_phase(phase, expected=1, workloads="new", **changes)
                    self.assertEqual(self.route_calls(calls), [])

    def test_postflight_second_flux_read_gates_every_listener_post(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        result, calls = self.run_phase("postflight", expected=1, workloads="new", second_flux_read_unready=True)
        self.assertIn("PASS all queues idle, grants present and archived data and store identical", result.stderr)
        self.assertIn("FAIL Flux stages still Ready before listener probes", result.stderr)
        self.assertEqual(self.route_calls(calls), [])

    def test_rollback_accepts_either_ready_verified_build_and_requires_committed_schema(self):
        for pods, revision in (("old", OLD[0]), ("new", RELEASE["revision"])):
            with self.subTest(pods=pods):
                self.baseline.unlink(missing_ok=True)
                self.receipt.unlink(missing_ok=True)
                _, calls = self.run_phase("rollback-preflight", head=REVIEWED_HEAD, workloads=pods)
                self.assertEqual([call[-1] for call in calls if "rollback-baseline" in call], [revision])
                self.assertEqual(json.loads(self.baseline.read_text()), SNAPSHOT)
                self.assertEqual(json.loads(self.receipt.read_text())["mode"], "rollback")
        self.rollback_baseline.write_bytes(self.baseline.read_bytes())
        self.receipt.unlink()
        rollback = {"artifact": ROLLBACK, "stages": STAGES, "now": NOW + 14400}
        _, calls = self.run_phase("compare", **rollback)
        for engine in ("image", "voice", "run"):
            self.assertTrue(any("exec" in call and call[-1] == engine for call in calls), engine)
        self.run_phase("compare", expected=1, snapshot={**SNAPSHOT, "runs": 3}, **rollback)
        self.run_phase("compare", expected=1, **{**rollback, "artifact": HEAD})
        self.baseline.unlink()
        for override in ({"wrong_schema": True}, {"api_unready": True}):
            self.receipt.unlink(missing_ok=True)
            self.run_phase("rollback-preflight", expected=1, head=REVIEWED_HEAD, **override)
            self.assertFalse(self.baseline.exists())

    def test_rollback_admits_a_terminal_job_only_with_verified_0013_and_released_health_gate(self):
        job = f"studio-migrate-{RELEASE['apiDigest'][7:19]}"
        terminal = {"terminal_job": job, "unready_stage": "studio-migration", "migration_wait_failure": True}
        result, _ = self.run_phase("rollback-preflight", expected=1, head=REVIEWED_HEAD,
                                   catalogue="0013_matte_jobs", **terminal)
        self.assertIn("rollback must remove the terminal migration Job health check", result.stderr)
        stages_path = self.checkout / STUDIO / "ks.yaml"
        stages = list(yaml.safe_load_all(stages_path.read_text()))
        migration = next(stage for stage in stages if stage["metadata"]["name"] == "studio-migration")
        del migration["spec"]["healthChecks"]
        stages_path.write_text(yaml.safe_dump_all(stages))
        for state in ("0012_release_constraints", "unverified", "0013_matte_jobs"):
            with self.subTest(catalogue=state):
                self.receipt.unlink(missing_ok=True)
                complete = state == "0013_matte_jobs"
                result, calls = self.run_phase("rollback-preflight", expected=0 if complete else 1,
                                               head=REVIEWED_HEAD, catalogue=state, **terminal)
                self.assertIn(f"Terminal migration Job {job}, catalogue: {state}", result.stdout)
                self.assertFalse(any("logs" in call for call in calls))
                self.assertEqual(self.baseline.exists(), complete)

    def test_rollback_preflight_checks_forward_merge_ancestry_and_pin(self):
        pin = STUDIO / "release.yaml"
        for changes in (
            {"off_main": [HEAD]},
            {"head": HEAD},
            {"ancestry": []},
            {"previous_source": {str(pin): yaml.safe_dump({**RELEASE, "webDigest": OLD[2]})}},
        ):
            with self.subTest(changes=changes):
                result, calls = self.run_phase("rollback-preflight", expected=1,
                                               **{"head": REVIEWED_HEAD, **changes})
                self.assertIn("FAIL selected fourth-pin Ops revision on main", result.stderr)
                self.assertFalse(any(call[0] == "kubectl" for call in calls))

    def prepare_retry(self):
        failed_merge_time = NOW - 7200
        self.run_phase("preflight", head=REVIEWED_HEAD, tree=FAILED_TREE, now=failed_merge_time - 100)
        first_receipt = self.receipt
        first_baseline = self.baseline
        paths = (STUDIO / "migration/job.yaml", STUDIO / "ks.yaml")
        previous_source = {str(path): (self.checkout / path).read_text() for path in paths}
        old_job = next(doc["metadata"]["name"] for doc in yaml.safe_load_all(previous_source[str(paths[0])])
                       if doc["kind"] == "Job")
        for path in paths:
            (self.checkout / path).write_text(previous_source[str(path)].replace(f"name: {old_job}", f"name: {old_job}-r1"))
        self.data.update(
            terminal_job=old_job, predecessor_pruned=True, previous_source=previous_source,
            known_merges={FAILED_HEAD: failed_merge_time},
            ancestry=[[FAILED_HEAD, HEAD], [FAILED_HEAD, RETRY_HEAD]],
            known_trees={REVIEWED_HEAD: FAILED_TREE, FAILED_HEAD: FAILED_TREE, HEAD: TREE, RETRY_HEAD: TREE},
        )
        self.receipt = self.root / "retry-receipt"
        self.baseline = self.root / "retry-baseline"
        return first_receipt, first_baseline, {
            "STUDIO_OP967_FAILED_OPS_REVISION": FAILED_HEAD, "STUDIO_OP967_PREVIOUS_RECEIPT": str(first_receipt),
        }

    def test_retry_preflight_uses_archives_and_accepts_either_idle_render_environment(self):
        first_receipt, first_baseline, env = self.prepare_retry()
        for version, state, snapshot_phase in (
            ("18.1.0", "0012_release_constraints", "retry-preflight"),
            ("18.1.1", "0013_matte_jobs", "retry-preflight-0013"),
        ):
            with self.subTest(render_version=version, catalogue=state):
                self.receipt.unlink(missing_ok=True)
                self.baseline.unlink(missing_ok=True)
                result, calls = self.run_phase("retry-preflight", extra_env=env, render_version=version, catalogue=state)
                self.assertIn(f"Idle render venv complete (PyAV {version})", result.stdout)
                self.assertIn(f"Migration catalogue: {state}", result.stdout)
                self.assertTrue(any(snapshot_phase in call for call in calls))
                self.assertTrue(any(f"job/{self.data['terminal_job']}" in call for call in calls))
                self.assertFalse(any(call[0] == "flux" or "logs" in call or
                                     any(OLD[1][7:19] in arg for arg in call) for call in calls))
                previous = json.loads(first_receipt.read_text())
                retry = json.loads(self.receipt.read_text())
                self.assertEqual((retry["mode"], retry["ops_tree"]), ("retry", TREE))
                self.assertEqual((retry["jobs_path"], retry["jobs_sha256"]),
                                 (previous["jobs_path"], previous["jobs_sha256"]))
                self.assertEqual(self.baseline.read_bytes(), first_baseline.read_bytes())

    def test_retry_preflight_refuses_missing_or_changed_evidence_and_wrong_live_state(self):
        first_receipt, _, env = self.prepare_retry()
        archive_file = Path(json.loads(first_receipt.read_text())["jobs_path"]) / "migrate.log"
        original = archive_file.read_bytes()
        for detail, changes, damage in (
            ("archived predecessor Job logs verified", {}, "missing archive"),
            ("archived predecessor Job logs verified", {}, "changed archive"),
            ("replacement migration Job and Flux health check", {"terminal_job": ""}, ""),
            ("replacement migration Job and Flux health check", {"terminal_active": 1}, ""),
            ("replacement migration Job and Flux health check", {"retry_changed_paths": True}, ""),
            ("replacement migration Job and Flux health check", {}, "changed health gate"),
            ("replacement migration Job and Flux health check", {}, "changed Job spec"),
            ("catalogue is complete 0012 or verified 0013", {"catalogue": "unverified"}, ""),
            ("empty queues, archived data and store baseline", {"snapshot": {**SNAPSHOT, "runs": 3}}, ""),
            ("old API endpoints", {"workloads": "new"}, ""),
            ("offline render app idle", {"render_busy": True}, ""),
            ("offline render app idle", {"render_version": "18.0.0"}, ""),
        ):
            with self.subTest(check=detail, changes=changes, damage=damage):
                self.receipt.unlink(missing_ok=True)
                path, clean_source = None, None
                if damage == "missing archive":
                    archive_file.unlink()
                elif damage == "changed archive":
                    archive_file.write_text("changed")
                elif damage == "changed health gate":
                    path = self.checkout / STUDIO / "ks.yaml"
                    clean_source = path.read_text()
                    path.write_text(clean_source.replace(self.data["terminal_job"] + "-r1", "wrong-job"))
                elif damage == "changed Job spec":
                    path = self.checkout / STUDIO / "migration/job.yaml"
                    clean_source = path.read_text()
                    path.write_text(clean_source.replace("backoffLimit: 2", "backoffLimit: 3"))
                try:
                    result, calls = self.run_phase("retry-preflight", expected=1, extra_env=env, **changes)
                    self.assertRegex(result.stderr, rf"FAIL [^\n]*{re.escape(detail)}")
                    self.assertFalse(any(call[0] == "flux" for call in calls))
                    self.assertFalse(json.loads(self.receipt.read_text())["passed"])
                    self.assertFalse(self.baseline.exists())
                finally:
                    if damage.endswith("archive"):
                        archive_file.write_bytes(original)
                    if path is not None:
                        path.write_text(clean_source)


class StartupTest(unittest.TestCase):
    def test_runbook_and_entrypoint_parse_as_bash(self):
        subprocess.run(["bash", "-n", "scripts/studio-release/op967.sh"], check=True, capture_output=True)
        page = Path("docs/reference/studio-fourth-release.md").read_text()
        for block in re.findall(r"(?ms)^```bash\n(.*?)^```$", page):
            subprocess.run(["bash", "-n"], input=block, text=True, check=True, capture_output=True)

    # Each exec fingerprint includes its argv, inline payload and stdin path.
    READ_ONLY_EXEC = {
        "5ea47b89e3acb3c5705d891b641eecc557b7fd48553acf844c692ce38befdc92": "primary catalogue",
        "54f3c38253799b5510413b52393d6e2404be9ed542bf6930a557c00b981b42cf": "dependency HTTP GET",
        "6bfee002bd87f6b4df61c5ea4143d527ae66e28315da4ccb6954a25f476c0408": "idle render venv",
        "fe7b2a92d56c9af4d76fbdb0a7226d5207d5baaa83685ee51e124e492e29cb75": "anonymous ingress GET",
        "f5cb0a67b4ece341568593db5ceb79ac603a55cbf33fb310850ba43bf401a5a1": "data snapshot",
        "20594027b779a9b6497fc7796dbd64f1e2124b2ec52f46a678769b0a236ec712": "encoder version",
        "b23bd15302a967ce2823defe1ada2ea092df7d685920bd4ebe53aa5411ebebf7": "worker engine reachability",
        "dabba0cfe9628d2a4f5a30d1e5eebe4fb5ed514c2ddeb89c3b6b99dff2b065d0": "listener validation refusals",
    }
    DEPENDENCY_READS = (
        ["deploy/wan-video", "api", "http://127.0.0.1:8080/v1/ready"],
        ["comfyui-0", "app", "http://127.0.0.1:8188/queue"],
    )

    def audit_commands(self, source):
        # Keep quoted payloads as single arguments. Preserve shell separators
        # and line endings so a command cannot borrow the next command's flags.
        source = re.sub(r"(?m)^[ \t]*#.*$", "", source.replace("\\\n", " "))
        lexer = shlex.shlex(source, posix=True, punctuation_chars="();|&<>\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
        separators = re.compile(r"[();|&>\n]+")
        tools = {"kubectl", "flux", "curl", "wget", "http", "https", "xh", "read_endpoint",
                 "bash", "sh", "eval"}
        seen = set()
        index = 0
        while index < len(tokens):
            token = tokens[index]
            index += 1
            tool = Path(token).name
            if tool == "read_endpoint" and tokens[index:index + 1] == ["()"]:
                continue
            if tool in tools:
                start = index
                while index < len(tokens) and not separators.fullmatch(tokens[index]):
                    index += 1
                argv = tokens[start:index]
                seen.update(self.audit_call(tool, argv))
                for argument in argv:
                    if "$(" in argument or "`" in argument:
                        seen.update(self.audit_commands(argument.replace("`", "")))
            elif "$(" in token or "`" in token:
                seen.update(self.audit_commands(token.replace("`", "")))
            else:
                self.assertNotRegex(token,
                                    r"https?://|\b(?:https?\.client|httpx|requests|urllib|urlopen)\b|\bHTTP/\d"
                                    r"|\b(?:kubectl|flux|curl|wget)\s+|\b(?:POST|PUT|PATCH|DELETE) /")
        return seen

    def audit_call(self, tool, argv):
        detail = f"{tool} {argv!r}"
        match tool:
            case "kubectl":
                args = argv
                if args[:1] == ["-n"]:
                    self.assertIn(args[1], {"ai", "database", "flux-system"}, detail)
                    args = args[2:]
                if args[:1] == ["exec"]:
                    fingerprint = hashlib.sha256("\0".join(argv).encode()).hexdigest()
                    self.assertIn(fingerprint, self.READ_ONLY_EXEC, detail)
                    return {fingerprint}
                name = r"[\w./$@-]+"
                self.assertRegex(" ".join(args),
                                 rf"^(?:get {name}(?: {name})?(?: -l [\w./=$-]+)?(?: -o (?:json|custom-columns=\S+))?"
                                 rf"|logs {name}(?: -c {name})?"
                                 rf"|wait --for=condition=(?:Complete|Ready)(?: --timeout=\d+s)? {name}"
                                 rf"|top node {name} --no-headers|top pod --containers -l [\w./=-]+ --no-headers)$")
            case "flux":
                self.assertRegex(" ".join(argv),
                                 r"^build kustomization \S+ --namespace \S+ --path \S+ "
                                 r"--kustomization-file \S+ --dry-run --in-memory-build$")
            case "curl":
                # Disable curlrc and accept only read flags followed by one URL.
                self.assertEqual(argv[:1], ["-q"], detail)
                self.assertRegex(argv[-1], r"^https?://[\w./?%=+#@~:-]+$")
                flags = " ".join(argv[1:-1])
                self.assertRegex(flags, r"^(?:(?:--fail|--silent|--show-error|--head|--request (?:GET|HEAD))(?: |$))*$")
            case "read_endpoint":
                self.assertIn(argv, self.DEPENDENCY_READS, detail)
            case "bash":
                self.assertRegex(" ".join(argv), r"^scripts/studio-release/op967\.sh "
                                 r"(?:preflight|retry-preflight|rollback-preflight|wait|postflight|verify|compare"
                                 r"|admit-merge(?: retry| rollback)?)$")
            case _:
                self.fail(f"unlisted command: {detail}")
        return set()

    def test_static_guard_accepts_read_forms_and_rejects_unlisted_calls(self):
        for command in (
            "kubectl -n ai get pods | jq .",
            "kubectl logs pod",
            "kubectl wait --for=condition=Ready pod/test",
            "flux build kustomization studio --namespace ai --path app --kustomization-file stage.yaml --dry-run --in-memory-build",
            "curl -q --request GET https://studio.haynesops.com/v1/example",
            "curl -q --head https://studio.haynesops.com/v1/example",
        ):
            with self.subTest(command=command):
                self.audit_commands(command)
        for command in (
            "kubectl cordon node",
            "kubectl drain node",
            "kubectl -n ai cp report.txt pod:/tmp/report.txt",
            "kubectl -n ai port-forward svc/studio 8000:8000",
            'if "kubectl" -n ai set env deploy/studio KEY=value; then true; fi',
            'echo "$(kubectl edit deployment studio)"',
            'kubectl get "$(kubectl edit deployment studio)"',
            "kubectl -n ai exec pod -- touch /tmp/changed",
            "flux reconcile kustomization studio",
            "flux build kustomization studio --namespace ai --path app --kustomization-file stage.yaml",
            "curl -q -X POST https://studio.haynesops.com/v1/example",
            "curl -q -XPOST https://studio.haynesops.com/v1/example",
            "curl -q --data '{}' https://studio.haynesops.com/v1/example",
            "curl --upload-file report.txt https://studio.haynesops.com/v1/example",
            "curl -q --upload-file report.txt https://studio.haynesops.com/v1/example",
            "python -c 'import httpx; httpx.post(url)'",
            "bash -c 'kubectl delete pods'",
            "eval 'kubectl edit deployment studio'",
        ):
            with self.subTest(command=command), self.assertRaises(AssertionError):
                self.audit_commands(command)

    def test_runbook_and_entrypoint_have_no_cluster_writes(self):
        script = Path("scripts/studio-release/op967.sh").read_text()
        page = Path("docs/reference/studio-fourth-release.md").read_text()
        blocks = re.findall(r"(?ms)^```[^\n]*\n(.*?)^```$", page)
        seen = set().union(*(self.audit_commands(source) for source in [script, *blocks]))
        self.assertEqual(seen, self.READ_ONLY_EXEC.keys())
        for name, checksum in (
            ("op967-data.py", "631cf55975dd6a955b3024380f3d30d2b79bb0b704e6006a4f5804b76fc27bc8"),
            ("op967-catalogue.sql", "d0fc7dcf3a6bb140578b3ce0e4dd39f6d8515068f54c1fd07fef2caaf5750aa6"),
            ("op967-routes.py", "c88b91bc1d995f937de076b6a31bba5012cc28ec17cb8130779a0a8b00379359"),
        ):
            self.assertEqual(hashlib.sha256(Path("scripts/studio-release", name).read_bytes()).hexdigest(),
                             checksum, name)

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
