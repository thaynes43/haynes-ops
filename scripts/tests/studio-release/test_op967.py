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
REWIRED = "3" * 40
EDITED = "4" * 40
ROLLBACK = "5" * 40
UNRELATED = "6" * 40
STAGES = ("studio-render", "studio-migration", "studio", "studio-worker-registration", "studio-worker",
          "studio-media-worker-registration", "studio-media-workers", "studio-matte-worker",
          "studio-matte-worker-registration")
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
    "retained matting allocation exact profile",
    "installed effective policy for the projected matte pod",
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
    return {"kind": "Kustomization", "metadata": {"name": name, "namespace": namespace},
            "spec": {"path": path, "sourceRef": {"kind": "GitRepository",
                     "namespace": "flux-system", "name": "haynes-ops"}}}
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
    document = definition(name, data["artifact"])
    document["metadata"]["generation"] = 2
    document["spec"]["suspend"] = name == data.get("suspended_stage")
    if name == data.get("rewired_live_stage"):
        document["spec"]["path"] = "./wrong-path"
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
        case ["merge-base", "--is-ancestor", _, "refs/remotes/origin/main"]:
            sys.exit(not data.get("on_main", True))
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
    if args[:2] != ["build", "kustomization"] or "--dry-run" not in args:
        sys.exit(99)
    name = args[2]
    patch = Path("kubernetes/main/flux/stage-path-patch")
    commit = patch.read_text() if patch.exists() else ""
    if name == "cluster":
        print(yaml.safe_dump(definition("cluster-apps", commit)))
    elif name == "cluster-apps":
        stages = data["definitions"]
        if commit == data["rollback"]:
            stages = [stage for stage in stages if not stage.startswith("studio-matte-worker")]
        print(yaml.safe_dump_all([definition(stage, commit) for stage in stages]))
    elif name == "studio-matte-worker" and not commit:
        files = [studio / "matting-pending/activation/worker" / file
                 for file in ("deployment.yaml", "networkpolicy.yaml")]
        documents = [doc for file in files for doc in yaml.safe_load_all(file.read_text()) if doc]
        for doc in documents:
            doc["metadata"]["namespace"] = "ai"
        print(yaml.safe_dump_all(documents))
elif cmd == "kubectl":
    if args[:1] == ["-n"]:
        args = args[2:]
    match args:
        case ["get", "gitrepository", "haynes-ops", "-o", "json"]:
            emit({**source, "status": {"artifact": {"revision": "main@sha1:" + data["artifact"]}}})
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
        case ["get", "pods", "-l", "app.kubernetes.io/name=studio-matte-worker", "-o", "name"]:
            if data.get("matte_pod_remaining"):
                print("pod/studio-matte-worker-still-terminating")
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
        case ["get", "networkpolicies.networking.k8s.io,ciliumnetworkpolicies.cilium.io", "--all-namespaces", "-o", "json"]:
            manifests = [studio / "app/networkpolicy.yaml"]
            if data["workloads"] == "new":
                manifests.append(studio / "matting-pending/activation/worker/networkpolicy.yaml")
            items = [doc for path in manifests for doc in yaml.safe_load_all(path.read_text()) if doc]
            for policy in items:
                policy["metadata"]["namespace"] = "ai"
            if kind := data.get("helm_policy"):
                metadata = {"namespace": data.get("helm_policy_namespace", "ai"), "name": "example-helm",
                            "labels": {"app.kubernetes.io/managed-by": "Helm"}}
                matte = {"matchLabels": {"app.kubernetes.io/name": "studio-matte-worker"}}
                spec = ({"podSelector": matte, "policyTypes": ["Egress"], "egress": [{}]}
                        if kind == "NetworkPolicy" else
                        {"endpointSelector": matte, "ingress": [{}]})
                items.append({"kind": kind, "metadata": metadata, "spec": spec})
            emit({"items": items})
        case ["get", "configmap", "cilium-config", "-o", "json"]:
            emit({"data": {"cluster-name": "haynes-ops", "enable-policy": data.get("enforcement", "default")}})
        case ["get", "namespace", "ai", "-o", "json"]:
            emit({"metadata": {"labels": {"kubernetes.io/metadata.name": "ai"}}})
        case ["get", "ciliumclusterwidenetworkpolicies.cilium.io", "-o", "json"]:
            emit({"items": [{"kind": "CiliumClusterwideNetworkPolicy", "metadata": {"name": "endpoint-bypass"},
                             "spec": {"endpointSelector": {"matchLabels": {"k8s:runtime-only": "present"}},
                                      "egress": [{}]}}] if data.get("cilium_bypass_policy") else
                            [{"kind": "CiliumClusterwideNetworkPolicy",
                              "metadata": {"name": "namespace-clusterwide"},
                              "specs": [{"endpointSelector": {"matchExpressions": [{
                                  "key": "k8s:io.cilium.k8s.namespace.labels.kubernetes.io/metadata.name",
                                  "operator": "In", "values": ["ai"],
                              }]}, "egressDeny": [{}]}]}] if data.get("clusterwide_projected") else []})
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
                         *([HEAD, sha] for sha in (DESCENDANT, EDITED, REWIRED, ROLLBACK))],
            "stages": STAGES, "definitions": STAGES,
            "paths": {stage: f"./kubernetes/main/apps/ai/studio/{stage}" for stage in STAGES}
            | {"studio": "./kubernetes/main/apps/ai/studio/app"},
            "edited": EDITED, "rewired": REWIRED, "rollback": ROLLBACK,
            "new": [RELEASE[key] for key in ("revision", "apiDigest", "webDigest")],
            "old": OLD, "workloads": "old", "snapshot": SNAPSHOT,
            "sidecar": yaml.safe_load((STUDIO / "matting-pending/release/release.yaml").read_text())["data"]["image"],
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
        self.baseline.write_text(json.dumps(SNAPSHOT))
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
            ("wrong schema", {"schema": "studio-op967/v2"}, {}),
            ("partial checks", {"checks": passing[-2:]}, {}),
            ("duplicate check", {"checks": [*passing, passing[0]]}, {}),
            ("failed check", {"checks": [{**passing[0], "result": "FAIL"}, *passing[1:]]}, {}),
            ("dirty", {}, {"dirty": " M docs/reference/studio-fourth-release.md"}),
        ):
            with self.subTest(case=case):
                self.receipt.unlink(missing_ok=True)
                if receipt is not None:
                    self.admit(**receipt)
                _, calls = self.run_phase("admit-merge", expected=1, head=REVIEWED_HEAD, **overrides)
                self.assertFalse(any(call[0] == "kubectl" for call in calls))

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
        result, _ = self.run_phase("wait", expected=1, missing_stages=["studio-matte-worker"])
        self.assertIn("ready Flux inventory is missing Studio stages", result.stderr)
        result, _ = self.run_phase("wait", missing_stages=list(STAGES[-2:]),
                                   stage_revisions={"cluster-apps": PREVIOUS},
                                   after_sleep={"missing_stages": [], "stage_revisions": {}})
        self.assertIn("ready at pin False", result.stdout)
        self.assertIn("ready at pin True", result.stdout)

    def test_wait_checks_matte_image_worker_order_and_migration_audit(self):
        for changes in (
            {"wrong_deployment": "matte-worker"},
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
                self.assertEqual(len(logs), 5)
                self.assertTrue(all(name.endswith("-succeeded") for name in logs), logs)
                self.assertEqual(len([name for name in logs if "-register-" in name]), 4)

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

    def test_postflight_rechecks_baseline_and_gates_final_probe(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        _, calls = self.run_phase("postflight", workloads="new")
        self.assertTrue(any("claim-validation" in call for call in calls))
        _, calls = self.run_phase("postflight", expected=1, workloads="new", snapshot={**SNAPSHOT, "runs": 3})
        self.assertFalse(any("claim-validation" in call for call in calls))
        result, calls = self.run_phase("postflight", expected=1, workloads="new", helm_policy="NetworkPolicy")
        self.assertIn("matte worker effective egress", result.stderr)
        self.assertFalse(any("claim-validation" in call for call in calls))
        result, calls = self.run_phase("postflight", expected=1, workloads="new", cilium_bypass_policy=True)
        self.assertIn("unexpected Cilium policy endpoint-bypass", result.stderr)
        self.assertFalse(any("claim-validation" in call for call in calls))
        result, calls = self.run_phase("postflight", expected=1, workloads="new", bad_endpoint_uid=True)
        self.assertIn("missing installed Cilium endpoint", result.stderr)
        self.assertFalse(any("claim-validation" in call for call in calls))
        self.baseline.write_text("{}")
        _, calls = self.run_phase("postflight", expected=1, workloads="new")
        self.assertEqual(self.route_calls(calls), [])

    @staticmethod
    def route_calls(calls):
        return [call for call in calls
                if call[-2] == "-" and call[-1] in {"voice", "image", "run", "matte"}]

    def test_verify_without_a_receipt_reads_every_audit(self):
        result, calls = self.run_phase("verify", now=NOW + 7200, workloads="new",
                                       extra_env={"STUDIO_OP967_BASELINE": ""})
        self.assertIn("PASS OP967 verify", result.stderr)
        for expected in ("gitrepository", "cluster-apps", "kustomizations", "ciliumendpoints.cilium.io", "postflight"):
            self.assertTrue(any(expected in call for call in calls), expected)
        logs = [call[4] for call in calls if call[:4] == ["kubectl", "-n", "ai", "logs"]]
        for job in ("migrate", "register-worker", "register-voice-worker", "register-image-worker",
                    "register-matte-worker"):
            self.assertTrue(any(pod.startswith(f"studio-{job}-") for pod in logs), job)
        self.assertEqual({call[-1] for call in self.route_calls(calls)}, {"voice", "image", "run", "matte"})
        self.assertEqual(len([call for call in calls if call[0] == "curl"]), 2)
        self.assertFalse(any(call[0] == "flux" or "claim-validation" in call for call in calls))

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
                    self.assertFalse(any("claim-validation" in call for call in calls))

    def test_postflight_rechecks_matte_queue_after_route_probes(self):
        self.baseline.write_text(json.dumps(SNAPSHOT))
        result, calls = self.run_phase("postflight", expected=1, workloads="new",
                                       now=NOW + 7200, matte_queue_refilled=True)
        self.assertIn("FAIL matte queue still idle before the claim", result.stderr)
        recheck = next(index for index, call in enumerate(calls)
                       if any("Matte queue is no longer idle" in arg for arg in call))
        self.assertEqual(len(self.route_calls(calls[:recheck])), 4)
        self.assertFalse(any("claim-validation" in call for call in calls))

    def test_preflight_refuses_installed_helm_policy_for_projected_matte_labels(self):
        for override, reason in (
            ({"helm_policy": "NetworkPolicy"}, "matte worker effective egress"),
            ({"helm_policy": "CiliumNetworkPolicy"}, "unexpected Cilium policy example-helm"),
            ({"clusterwide_projected": True}, "unexpected Cilium policy namespace-clusterwide"),
            ({"enforcement": "never"}, "does not enforce network policy"),
        ):
            with self.subTest(override=override):
                self.receipt.unlink(missing_ok=True)
                result, calls = self.run_phase("preflight", expected=1, **override)
                self.assertIn(reason, result.stderr)
                self.assertTrue(any("--all-namespaces" in call for call in calls))
                self.assertFalse(json.loads(self.receipt.read_text())["passed"])
        self.receipt.unlink()
        self.run_phase("preflight", helm_policy="NetworkPolicy", helm_policy_namespace="network")

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
        rollback = {"artifact": ROLLBACK, "stages": STAGES[:-2], "now": NOW + 14400}
        _, calls = self.run_phase("compare", **rollback)
        for engine in ("image", "voice", "run"):
            self.assertTrue(any("exec" in call and call[-1] == engine for call in calls), engine)
        self.run_phase("compare", expected=1, matte_pod_remaining=True, **rollback)
        self.run_phase("compare", expected=1, snapshot={**SNAPSHOT, "runs": 3}, **rollback)
        self.run_phase("compare", expected=1, **{**rollback, "artifact": HEAD})
        self.baseline.unlink()
        for override in ({"wrong_schema": True}, {"api_unready": True}):
            self.receipt.unlink(missing_ok=True)
            self.run_phase("rollback-preflight", expected=1, head=REVIEWED_HEAD, **override)
            self.assertFalse(self.baseline.exists())

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

    def test_runbook_and_entrypoint_have_no_cluster_writes(self):
        # Fingerprint each reviewed exec, including the inline payload or local
        # script redirection, so a new remote command requires explicit review.
        readonly_exec = {
            "09c42126e3f485ee5542663ef8f2422bb1c2a58db63289c6d97e502b3336eafe": "dependency HTTP reads",
            "935b3f7211df9a00c285156bb5f33335401c4ab1c817035ca931436f18253c4c": "idle render venv",
            "d366be0a7bd0d5bda7ddb97617b8b45cecd49f27a0668b459c428cc60721cdc7": "saved weights SHA-256",
            "6966ba8a89e8ef6073e89faa925d927c30629597cc1a97d376191a3ea4b91bf7": "read-only data snapshot",
            "180457d1c23c00daa288208d3ecb17f450686a76594168ff54004486167bbea5": "sidecar readiness",
            "913353e5c2d14c72be009bddeb515da6701642adecbf3d7b590e0d72d8134cc1": "Unix HTTP readiness",
            "bcffdfcb7e9b3632ecb77f6ab82ea3a730f8f6092f39da0c59717c865e16e41e": "worker process and progress",
            "2af902c4e749c0be06cd40e79fd6c3690a86529e0dfa9f2d961d6c65400c5704": "encoder version",
            "7f7fbd7d7bf91eb88d3261d894f6163208899de07d1c026f9ed6adc953a462a5": "worker engine reachability",
            "f930af22ada1d21c27735168a2c07dd07df3a042a60fa454fa2690b4b0ebdc03": "read-only matte queue",
            "c971578fe32445f9b02732bdaa4f43a4aed4472e0358f29f96f47ca5ba84b17e": "listener validation probes",
            "4b5389b0cbeb2e21d488fcc765aada9f2944ef0b0556a33bbc974dc0ef66ebc1": "final rejected claim",
        }
        script = Path("scripts/studio-release/op967.sh").read_text()
        page = Path("docs/reference/studio-fourth-release.md").read_text()
        blocks = re.findall(r"(?ms)^```[^\n]*\n(.*?)^```$", page)
        commands = re.compile(r"""\b(kubectl|flux)[ \t]+((?:'[^']*'|"[^"\n]*"|[^'"\n])*)""")
        mutations = {
            "flux": r"\b(?:reconcile|suspend|resume)\b",
            "kubectl": r"\b(?:create|apply|delete|patch|replace|scale|rollout|annotate|label)\b",
        }
        seen = set()
        for text in [script, *blocks]:
            for tool, command in commands.findall(text.replace("\\\n", " ")):
                prefix = command.split("-- ", 1)[0].split("|", 1)[0]
                self.assertNotRegex(prefix, mutations[tool], command)
                if tool == "kubectl" and re.search(r"\bexec\b", prefix):
                    fingerprint = hashlib.sha256(command.strip().encode()).hexdigest()
                    self.assertIn(fingerprint, readonly_exec, command)
                    seen.add(fingerprint)
        self.assertEqual(seen, readonly_exec.keys())
        for name, checksum in (
            ("op967-data.py", "67285ff1c8c65008a95266ddeb50c653f98666c53b9cb74fb00af8926bc9a6e1"),
            ("op967-routes.py", "b39a8f0afcb09accd8272afa69ec339495eb4a50659791d7e0384273f215da50"),
        ):
            self.assertEqual(hashlib.sha256(Path("scripts/studio-release", name).read_bytes()).hexdigest(),
                             checksum, name)

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
