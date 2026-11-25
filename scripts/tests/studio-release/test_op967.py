"""Run each OP967 runbook block with no cluster access and errexit suppressed."""

import hashlib
import importlib.util
import json
import os
import re
import shutil
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
FAILED_HEAD = "d" * 40
FAILED_TREE = "e" * 40
RETRY_HEAD = "f" * 40
DESCENDANT = "1" * 40
PREVIOUS = "2" * 40
UNRELATED = "3" * 40
STAGES = ("studio-render", "studio-migration", "studio", "studio-worker-registration", "studio-worker",
          "studio-media-worker-registration", "studio-media-workers", "studio-matte-worker",
          "studio-matte-worker-registration")
NOW = 1760000050
SNAPSHOT = {"store": {}, "runs": {}, "deliveries": {}, "delivery media": {}, "media jobs": {}}
ROLLOUT = ("migration", "roll", "postflight")
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
    "retained matting allocation exact profile",
    "LAN root available",
    "LAN API anonymous access refused",
    "predecessor Job logs saved",
    "empty queues, archived data and store baseline",
)

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
studio = Path("kubernetes/main/apps/ai/studio")
old_job_suffix = data["old"][1][7:19]

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
            "metadata": {"name": pod, "uid": pod, "labels": {"app.kubernetes.io/name": "studio-" + workload}},
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
    return {
        "metadata": {"name": name, "generation": 2},
        "spec": {"sourceRef": {"kind": "GitRepository", "namespace": "flux-system", "name": "haynes-ops"},
                 "suspend": name == data.get("suspended_stage")},
        "status": {"lastAppliedRevision": "main@sha1:" + revision,
                   "lastAttemptedRevision": "main@sha1:" + data.get("attempted_revisions", {}).get(name, revision),
                   "observedGeneration": 2,
                   "conditions": [{"type": "Ready", "status": "False" if name == data.get("unready_stage") else "True"}]},
    }

if cmd in {"kubectl", "flux", "curl"}:
    with open(env["COMMAND_LOG"], "a") as log:
        log.write(json.dumps([cmd, *args]) + "\n")

if cmd == "git":
    match args:
        case ["rev-parse", "--verify", "HEAD"]:
            print(data["head"])
        case ["rev-parse", "--verify", "HEAD^{tree}"]:
            print(data["tree"])
        case ["rev-parse", "--verify", ref] if ref.endswith("^{tree}"):
            tree = data["known_trees"].get(ref.removesuffix("^{tree}"))
            if tree is None:
                sys.exit(1)
            print(tree)
        case ["status", "--porcelain", "--untracked-files=no"]:
            print(data.get("dirty", ""))
        case ["merge-base", "--is-ancestor", _, "refs/remotes/origin/main"]:
            sys.exit(not data.get("on_main", True))
        case ["merge-base", "--is-ancestor", "refs/remotes/origin/main", _]:
            sys.exit(not data.get("includes_main", True))
        case ["merge-base", "--is-ancestor", ancestor, descendant]:
            sys.exit([ancestor, descendant] not in data["ancestry"])
        case ["diff", "--quiet", expected, actual, "--", *_]:
            sys.exit(expected != actual and data.get("descendant_pin_drift", False))
        case ["show", "-s", "--format=%ct", ref]:
            print(data.get("known_merges", {}).get(ref, data["merged_at"]))
        case ["show", ref] if ":kubernetes/main/" in ref:
            print(data["previous_source"][ref.split(":", 1)[1]], end="")
        case ["diff", "--name-only", *_]:
            print(f"{studio}/ks.yaml\n{studio}/migration/job.yaml")
            if data.get("retry_changed_paths"):
                print(f"{studio}/app/api.yaml")
        case _:
            sys.exit(99)
elif cmd == "date":
    print(data["now"] if args[-1] == "+%s" else "2026-11-25T17:00:00Z")
elif cmd == "yq":
    query, filename = args[-2:]
    docs = [doc for doc in yaml.safe_load_all(sys.stdin.read() if filename == "-" else Path(filename).read_text()) if doc]
    if "healthChecks" in query:
        stage = next(doc for doc in docs if doc["metadata"]["name"] == "studio-migration")
        print(stage["spec"]["healthChecks"][0]["name"])
    elif query.startswith("select"):
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
        case ["get", "kustomization", "cluster-apps", "-o", "json"]:
            emit(stage("cluster-apps"))
        case ["get", "kustomizations", "-o", "json"]:
            emit({"items": [stage(name) for name in data["stages"] if name not in data.get("missing_stages", [])]})
        case ["get", "deployments", "-o", "json"]:
            workloads = ("api", "mcp", "web", "worker", "voice-worker", "image-worker")
            if data["workloads"] == "new":
                workloads += ("matte-worker",)
            items = []
            for workload in workloads:
                spec = pods(workload)[0]["spec"]
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
        case ["get", "networkpolicies.networking.k8s.io,ciliumnetworkpolicies.cilium.io", "-o", "json"]:
            manifests = (studio / "app/networkpolicy.yaml", studio / "matting-pending/activation/worker/networkpolicy.yaml")
            items = [doc for path in manifests for doc in yaml.safe_load_all(path.read_text()) if doc]
            for policy in items:
                policy["metadata"]["namespace"] = "ai"
            if data.get("bypass_policy"):
                items[-1]["spec"]["egress"].append({})
            emit({"items": items})
        case ["get", "ciliumclusterwidenetworkpolicies.cilium.io", "-o", "json"]:
            emit({"items": [{"kind": "CiliumClusterwideNetworkPolicy", "metadata": {"name": "endpoint-bypass"},
                             "spec": {"endpointSelector": {"matchLabels": {"k8s:runtime-only": "present"}},
                                      "egress": [{}]}}] if data.get("cilium_bypass_policy") else []})
        case ["get", "ciliumendpoints.cilium.io", "-o", "json"]:
            items = []
            for workload in ("api", "matte-worker"):
                for pod in pods(workload):
                    pod_name = pod["metadata"]["name"]
                    items.append({"metadata": {"name": pod_name, "ownerReferences": [
                        {"kind": "Pod", "uid": "wrong-uid" if data.get("bad_endpoint_uid") else pod_name},
                    ]}, "status": {"identity": {"labels": [f"k8s:app.kubernetes.io/name=studio-{workload}",
                        *(["k8s:runtime-only=present"] if workload == "matte-worker" else [])]}}})
            emit({"items": items})
        case ["get", "endpointslices", *_]:
            emit({"items": [{"endpoints": [
                {"conditions": {"ready": True}, "targetRef": {"uid": pod["metadata"]["uid"]}}
                for pod in pods("api")
            ]}]})
        case ["get", "cluster", "postgres16", *_]:
            emit({"spec": {"instances": 3}, "status": {"readyInstances": 3}})
        case ["get", "node", "talosw01", *_]:
            emit({"status": {"allocatable": {"cpu": "39", "memory": "111129616Ki"}}})
        case ["get", "deploy/studio-matte-worker", *_]:
            if data.get("matte_present"):
                print("deployment.apps/studio-matte-worker")
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
                status = 201 if worker == "matte-worker" else 200
                if worker == "image-worker" and data.get("bad_registration_audit"):
                    status = 409
                emit({"action": "worker_tokens.register", "worker_id": "studio-" + worker, "status": status})
                result = "wrong" if worker == "image-worker" and data.get("bad_registration_result") else worker
                emit({"worker_id": "studio-" + result})
        case ["exec", *options]:
            if "/v1/ready" in options[-1]:
                emit({"ready": True, "active_video_job": None, "active_foley_job": data.get("active_foley")})
            elif options[-1].endswith("/queue"):
                emit({"queue_running": [], "queue_pending": []})
            elif options[-2] in {"preflight", "retry-preflight", "postflight", "rollback-baseline", "compare"}:
                mode, expected_build = options[-2:]
                if expected_build != revision or options[1] not in {"api-0", "api-1"}:
                    sys.exit(87)
                if data.get("wrong_schema") and mode == "rollback-baseline":
                    print("FAIL expected schema", file=sys.stderr)
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
            elif any("Matte queue is no longer idle" in arg for arg in options):
                sys.exit(bool(data.get("matte_queue_refilled")))
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
        cls.assert_blocks = set(cls.blocks) == {
            "preflight", "retry-preflight", "admit-merge", *ROLLOUT, "verify", "rollback-baseline", "compare",
        }

    def setUp(self):
        self.assertTrue(self.assert_blocks, self.blocks)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="op967-phases-")))
        self.receipt = self.root / "receipt.json"
        self.baseline = self.root / "baseline.json"
        self.rollback_receipt = self.root / "rollback-receipt.json"
        self.rollback_baseline = self.root / "rollback-baseline.json"
        self.log = self.root / "commands"
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        shutil.copytree(STUDIO, self.checkout / STUDIO)
        (self.checkout / "scripts").symlink_to(Path("scripts").resolve())
        stub = self.root / "command"
        stub.write_text(f"#!{sys.executable}\n" + COMMANDS)
        stub.chmod(0o700)
        for name in ("git", "date", "kubectl", "flux", "curl", "yq"):
            (self.root / name).symlink_to(stub)
        self.data = {
            "head": HEAD, "tree": TREE, "artifact": HEAD, "merged_at": NOW - 10, "now": NOW,
            "known_trees": {REVIEWED_HEAD: TREE, HEAD: TREE, FAILED_HEAD: FAILED_TREE},
            "known_merges": {DESCENDANT: NOW + 1800},
            "ancestry": [[PREVIOUS, HEAD], [HEAD, DESCENDANT], [PREVIOUS, DESCENDANT]],
            "stages": STAGES,
            "new": [RELEASE[key] for key in ("revision", "apiDigest", "webDigest")],
            "old": OLD, "workloads": "old", "snapshot": SNAPSHOT,
            "sidecar": yaml.safe_load((STUDIO / "matting-pending/release/release.yaml").read_text())["data"]["image"],
        }

    def admit(self, **changes):
        data = {
            "schema": "studio-op967/v2", "mode": "release", "ops_revision": REVIEWED_HEAD, "ops_tree": TREE,
            "started_epoch": NOW - 100, "finished_epoch": NOW - 50, "passed": True,
            "baseline_path": str(self.baseline),
            "baseline_sha256": hashlib.sha256(self.baseline.read_bytes()).hexdigest()
            if self.baseline.exists() else "0" * 64,
            "checks": [{"label": label, "result": "PASS"} for label in RELEASE_CHECKS],
            **changes,
        }
        self.receipt.write_text(json.dumps(data))

    def run_phase(self, phase, *, expected=0, selected=HEAD, extra_env=None, **changes):
        self.log.write_text("")
        env = {
            "PATH": f"{self.root}:{Path(sys.executable).parent}:{os.environ['PATH']}",
            "FIXTURE": json.dumps({**self.data, **changes}),
            "COMMAND_LOG": str(self.log), "STUDIO_OP967_RECEIPT": str(self.receipt),
            "STUDIO_OP967_BASELINE": str(self.baseline), "MERGED_OPS_REVISION": selected,
            "STUDIO_OP967_ROLLBACK_RECEIPT": str(self.rollback_receipt),
            "STUDIO_OP967_ROLLBACK_BASELINE": str(self.rollback_baseline),
            "ROLLBACK_OPS_REVISION": DESCENDANT, **(extra_env or {}),
        }
        # The documented if ! bash ... suppresses errexit throughout the sourced
        # phase. A naked failed check followed by Flux would still be observed.
        runner = 'set -e\nbash() { ( source "$@" ); }\n' + self.blocks[phase]
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
        _, calls = self.run_phase("admit-merge", head=REVIEWED_HEAD, now=NOW + 3600, expected=1)
        self.assertEqual(calls, [])
        _, calls = self.run_phase("admit-merge", head=REVIEWED_HEAD, includes_main=False, expected=1)
        self.assertEqual(calls, [])
        self.run_phase("migration", merged_at=NOW + 10, now=NOW + 20)

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
                self.assertFalse(any(call[0] == "flux" for call in calls))

    def test_mutating_blocks_refuse_missing_failed_stale_and_mismatched_receipts(self):
        passing_checks = [{"label": label, "result": "PASS"} for label in RELEASE_CHECKS]
        for phase in (*ROLLOUT, "rollback-baseline", "compare"):
            for label, receipt, extra in (
                ("missing", None, {}),
                ("failed", {"passed": False}, {}),
                ("stale", {"started_epoch": NOW - 3700}, {}),
                ("future", {"finished_epoch": NOW + 1}, {}),
                ("wrong tree", {"ops_tree": FAILED_TREE}, {}),
                ("wrong admitted revision", {"ops_revision": FAILED_HEAD}, {}),
                ("wrong selected revision", {}, {"selected": FAILED_HEAD}),
                ("invalid selected revision", {}, {"selected": "d" * 12}),
                ("not merged", {}, {"on_main": False}),
                ("dirty checkout", {}, {"dirty": " M docs/reference/studio-fourth-release.md"}),
                ("schema", {"schema": "studio-op967/v1"}, {}),
                ("non-integer epoch", {"started_epoch": NOW - 100.5}, {}),
                ("invalid revision syntax", {"ops_revision": "not-a-sha"}, {"known_trees": {"not-a-sha": TREE}}),
                ("invalid hash syntax", {"baseline_sha256": "wrong"}, {}),
                ("partial checks", {"checks": passing_checks[-2:]}, {}),
                ("duplicate check", {"checks": [*passing_checks, passing_checks[0]]}, {}),
                ("failed required check", {"checks": [{**passing_checks[0], "result": "FAIL"}, *passing_checks[1:]]}, {}),
            ):
                with self.subTest(phase=phase, case=label):
                    self.receipt.unlink(missing_ok=True)
                    self.baseline.write_text(json.dumps(SNAPSHOT))
                    if receipt is not None:
                        self.admit(**receipt)
                    _, calls = self.run_phase(phase, expected=1, **extra)
                    self.assertEqual(calls, [])

    def test_mutating_blocks_require_named_unchanged_baseline(self):
        for phase in ROLLOUT:
            for damage in ("missing", "changed", "different path"):
                with self.subTest(phase=phase, damage=damage):
                    self.baseline.write_text(json.dumps(SNAPSHOT))
                    self.admit()
                    extra = {}
                    if damage == "missing":
                        self.baseline.unlink()
                    elif damage == "changed":
                        self.baseline.write_text("{}")
                    else:
                        other = self.root / "other-baseline"
                        other.write_bytes(self.baseline.read_bytes())
                        extra["STUDIO_OP967_BASELINE"] = str(other)
                    _, calls = self.run_phase(phase, expected=1, extra_env=extra)
                    self.assertEqual(calls, [])

    def test_release_receipt_valid_at_merge_completes_two_hours_later(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        self.admit()
        for phase in (*ROLLOUT, "rollback-baseline"):
            with self.subTest(phase=phase):
                result, calls = self.run_phase(phase, now=NOW + 7200,
                                               workloads="new" if phase == "postflight" else "old")
                self.assertIn("PASS OP967 " + phase, result.stderr)
                if phase in ("migration", "roll"):
                    self.assertTrue(any(call[0] == "flux" for call in calls))
                if phase == "postflight":
                    self.assertTrue(any("idle-claim" in call for call in calls))
        self.run_phase("compare", artifact=DESCENDANT, stages=STAGES[:-2], now=NOW + 14400,
                       known_merges={DESCENDANT: NOW + 7210})

    def test_missing_receipt_cannot_start_a_rollout_flux_has_not_reached(self):
        for phase in ROLLOUT:
            with self.subTest(phase=phase):
                _, calls = self.run_phase(phase, expected=1, artifact=PREVIOUS)
                self.assertEqual(calls, [])

    def test_rollout_can_hurry_pending_stages_on_a_matching_descendant(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        self.admit()
        _, calls = self.run_phase(
            "migration", artifact=DESCENDANT,
            stage_revisions={stage: PREVIOUS for stage in ("cluster-apps", *STAGES)},
            missing_stages=STAGES[-2:],
        )
        self.assertEqual([call[3] for call in calls if call[0] == "flux"], ["cluster-apps", "studio-migration"])
        for changes in (
            {"artifact": DESCENDANT, "descendant_pin_drift": True},
            {"attempted_revisions": {"studio": UNRELATED}},
            {"suspended_stage": "studio"},
            {"wrong_deployment": "api"},
        ):
            with self.subTest(changes=changes):
                _, calls = self.run_phase("migration", expected=1, **changes)
                self.assertFalse(any(call[0] == "flux" for call in calls))

    def test_migration_and_roll_check_flux_artifact_before_reconciliation(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
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
        for phase, failure, first_stage in (("migration", "sample_failure", "cluster-apps"),
                                            ("roll", "flux_failure", "studio-render")):
            _, calls = self.run_phase(phase, expected=1, **{failure: True})
            self.assertEqual([call[3] for call in calls if call[0] == "flux"], [first_stage])

    def test_jobs_select_the_succeeded_pod_of_each_job_in_both_orders(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        self.admit()
        for order in ("normal", "reverse"):
            with self.subTest(order=order):
                _, calls = self.run_phase("roll", pod_order=order)
                logs = [call[4] for call in calls if call[:4] == ["kubectl", "-n", "ai", "logs"]]
                self.assertEqual(len(logs), 5)
                self.assertTrue(all(name.endswith("-succeeded") for name in logs), logs)
                self.assertEqual(len([name for name in logs if "-register-" in name]), 4)

    def test_complete_job_refuses_missing_or_multiple_succeeded_pods(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        self.admit()
        for selection, detail in (
            ("none", "no succeeded pod"), ("many", "more than one succeeded pod"),
            ("wrong-phase", "no succeeded pod"), ("wrong-exit", "no succeeded pod"),
        ):
            with self.subTest(selection=selection):
                result, calls = self.run_phase("roll", expected=1, pod_selection=selection)
                self.assertIn(detail + " for UID", result.stderr)
                self.assertFalse(any(call[0] == "flux" or "logs" in call for call in calls))

    def test_registration_requires_the_last_worker_result_and_matching_status_audit(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        self.admit()
        for failure in ("bad_registration_audit", "bad_registration_result"):
            with self.subTest(failure=failure):
                result, _ = self.run_phase("roll", expected=1, **{failure: True})
                self.assertIn("FAIL image worker registration reused its token", result.stderr)

    def test_postflight_rechecks_baseline_and_holds_idle_claim_on_difference(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        self.admit()
        _, calls = self.run_phase("postflight", workloads="new")
        self.assertTrue(any("idle-claim" in call for call in calls))
        _, calls = self.run_phase("postflight", expected=1, workloads="new", snapshot={**SNAPSHOT, "runs": 3})
        self.assertFalse(any("idle-claim" in call for call in calls))
        result, calls = self.run_phase("postflight", expected=1, workloads="new", bypass_policy=True)
        self.assertIn("matte worker effective egress", result.stderr)
        self.assertFalse(any("idle-claim" in call for call in calls))
        result, calls = self.run_phase("postflight", expected=1, workloads="new", cilium_bypass_policy=True)
        self.assertIn("unexpected Cilium policy endpoint-bypass", result.stderr)
        self.assertFalse(any("idle-claim" in call for call in calls))
        result, calls = self.run_phase("postflight", expected=1, workloads="new", bad_endpoint_uid=True)
        self.assertIn("missing installed Cilium endpoint", result.stderr)
        self.assertFalse(any("idle-claim" in call for call in calls))
        self.baseline.write_text("{}")
        _, calls = self.run_phase("postflight", expected=1, workloads="new")
        self.assertEqual(calls, [])

    @staticmethod
    def route_calls(calls):
        return [call for call in calls
                if call[-2] == "-" and call[-1] in {"voice", "image", "run", "matte"}]

    def test_verify_without_a_receipt_reads_every_audit(self):
        result, calls = self.run_phase("verify", now=NOW + 7200, workloads="new",
                                       extra_env={"STUDIO_OP967_BASELINE": ""})
        self.assertIn("PASS read-only release verification", result.stdout)
        for expected in ("gitrepository", "cluster-apps", "kustomizations", "ciliumendpoints.cilium.io", "postflight"):
            self.assertTrue(any(expected in call for call in calls), expected)
        logs = [call[4] for call in calls if call[:4] == ["kubectl", "-n", "ai", "logs"]]
        for job in ("migrate", "register-worker", "register-voice-worker", "register-image-worker",
                    "register-matte-worker"):
            self.assertTrue(any(pod.startswith(f"studio-{job}-") for pod in logs), job)
        self.assertEqual({call[-1] for call in self.route_calls(calls)}, {"voice", "image", "run", "matte"})
        self.assertEqual(len([call for call in calls if call[0] == "curl"]), 2)
        self.assertFalse(any(call[0] == "flux" or "idle-claim" in call for call in calls))

    def test_failed_state_stops_before_the_first_listener_post(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        self.admit()
        for phase in ("postflight", "verify"):
            for changes in (
                {"unready_stage": "studio"},
                {"migration_wait_failure": True},
                {"bad_registration_audit": True},
                {"snapshot": {**SNAPSHOT, "runs": 3}},
            ):
                with self.subTest(phase=phase, changes=changes):
                    _, calls = self.run_phase(phase, expected=1, workloads="new", **changes)
                    self.assertTrue(any("postflight" in call for call in calls))
                    self.assertEqual(self.route_calls(calls), [])
                    self.assertFalse(any("idle-claim" in call for call in calls))

    def test_postflight_rechecks_matte_queue_after_route_probes(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        self.admit()
        result, calls = self.run_phase("postflight", expected=1, workloads="new",
                                       now=NOW + 7200, matte_queue_refilled=True)
        self.assertIn("FAIL matte queue still idle before the claim", result.stderr)
        recheck = next(index for index, call in enumerate(calls)
                       if any("Matte queue is no longer idle" in arg for arg in call))
        self.assertEqual(len(self.route_calls(calls[:recheck])), 4)
        self.assertFalse(any("idle-claim" in call for call in calls))

    def test_rollback_accepts_either_ready_verified_build_and_requires_committed_schema(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        self.admit()
        for pods, revision in (("old", OLD[0]), ("new", RELEASE["revision"])):
            with self.subTest(pods=pods):
                self.rollback_baseline.unlink(missing_ok=True)
                self.rollback_receipt.unlink(missing_ok=True)
                _, calls = self.run_phase("rollback-baseline", workloads=pods, now=NOW + 600)
                self.assertEqual([call[-1] for call in calls if "rollback-baseline" in call], [revision])
                self.assertEqual(json.loads(self.rollback_baseline.read_text()), SNAPSHOT)
                self.assertEqual(json.loads(self.rollback_receipt.read_text())["mode"], "rollback")
        rollback = {"artifact": DESCENDANT, "stages": STAGES[:-2], "now": NOW + 14400}
        self.run_phase("compare", **rollback)
        self.run_phase("compare", expected=1, snapshot={**SNAPSHOT, "runs": 3}, **rollback)
        self.run_phase("compare", expected=1, **{**rollback, "artifact": HEAD})
        self.rollback_baseline.unlink()
        self.rollback_receipt.unlink()
        self.run_phase("rollback-baseline", expected=1, wrong_schema=True)
        self.rollback_receipt.unlink()
        self.run_phase("rollback-baseline", expected=1, api_unready=True)
        self.assertFalse(self.rollback_baseline.exists())

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
        for version in ("18.1.0", "18.1.1"):
            with self.subTest(render_version=version):
                self.receipt.unlink(missing_ok=True)
                self.baseline.unlink(missing_ok=True)
                result, calls = self.run_phase("retry-preflight", extra_env=env, render_version=version)
                self.assertIn(f"Idle render venv complete (PyAV {version})", result.stdout)
                self.assertTrue(any(f"job/{self.data['terminal_job']}" in call for call in calls))
                self.assertFalse(any(call[0] == "flux" or "logs" in call or
                                     any(OLD[1][7:19] in arg for arg in call) for call in calls))
                previous = json.loads(first_receipt.read_text())
                retry = json.loads(self.receipt.read_text())
                self.assertEqual((retry["mode"], retry["ops_tree"]), ("retry", TREE))
                self.assertEqual((retry["jobs_path"], retry["jobs_sha256"]),
                                 (previous["jobs_path"], previous["jobs_sha256"]))
                self.assertEqual(self.baseline.read_bytes(), first_baseline.read_bytes())
                self.run_phase("migration", head=RETRY_HEAD, selected=RETRY_HEAD, artifact=RETRY_HEAD,
                               merged_at=NOW + 10, now=NOW + 20)

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
            ("matte workload absent", {"matte_present": True}, ""),
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
