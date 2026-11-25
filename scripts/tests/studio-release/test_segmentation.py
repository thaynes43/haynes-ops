"""Offline tests for the staged release guard and create-only two-model provisioner."""

import copy
import hashlib
import importlib.util
import inspect
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from stat import S_IMODE
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from urllib.request import Request

import yaml

ROOT = Path(__file__).resolve().parents[3]
STUDIO = ROOT / "kubernetes/main/apps/ai/studio"
PENDING = STUDIO / "segmentation-pending"
ALLOCATION = "segmentation-op460-dino-sam-v1-01"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load("studio_release_check", Path(__file__).with_name("check.py"))
provision = load("segmentation_provision", PENDING / "policy/provision.py")
matting_provision = load("matting_provision", STUDIO / "matting-pending/policy/provision.py")


class Response(io.BytesIO):
    status = 200

    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


class FakeHub:
    contents = {"dino": b"dino-weights", "sam": b"sam-weights!"}
    urls = [
        f"https://huggingface.co/publisher/{name}/resolve/{'1' * 40}/model.safetensors"
        for name in contents
    ]

    def open(self, request, timeout):
        path = request.full_url
        if path not in self.urls:
            raise AssertionError("download URL escaped the fixture pins")
        return Response(self.contents[path.split("/")[4]])


class Profile:
    image_digest = "sha256:" + "1" * 64

    def model_files(self):
        return tuple({
            "directory": name, "repository": f"publisher/{name}", "revision": "1" * 40,
            "files": [{"name": "model.safetensors", "size_bytes": len(data),
                       "sha256": hashlib.sha256(data).hexdigest()}],
        } for name, data in FakeHub.contents.items())

    def manifest(self):
        return json.dumps(self.model_files(), sort_keys=True).encode()


class ProvisionTests(unittest.TestCase):
    def test_provisioner_report_is_last(self):
        profile_module = ModuleType("haynes_studio.segmentation.profile")
        profile_module.Profile = SimpleNamespace(installed=Profile)
        profile_module.SegmentationError = ValueError
        report = {"allocation": ALLOCATION, "download_bytes": 24}
        output = io.StringIO()

        def provision_fixture(*_):
            print("fixture download diagnostic", file=sys.stderr)
            return report

        with (
            patch.dict(sys.modules, {profile_module.__name__: profile_module}),
            patch.object(provision.os, "getuid", return_value=1000),
            patch.object(provision.os, "getgid", return_value=1000),
            patch.object(provision, "provision", side_effect=provision_fixture),
            redirect_stdout(output), redirect_stderr(output),
        ):
            self.assertEqual(provision.main(), 0)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1]), report)

    def test_two_models_one_create_only_allocation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(provision, "capacity"):
            path = Path(directory)
            profile = Profile()
            document = provision.provision(path, ALLOCATION, FakeHub(), profile)
            published = path / ALLOCATION / "weights"
            self.assertEqual(document["download_bytes"], 24)
            self.assertEqual((published / "profile.json").read_bytes(), profile.manifest())
            self.assertEqual((published / "profile.sha256").read_text().strip(), document["profile_sha256"])
            for name, data in FakeHub.contents.items():
                weights = published / name / "model.safetensors"
                self.assertEqual(weights.read_bytes(), data)
            for entry in [published.parent, published, *published.rglob("*")]:
                self.assertEqual(S_IMODE(entry.stat().st_mode), 0o555 if entry.is_dir() else 0o444, entry)
            with self.assertRaisesRegex(provision.ProvisionError, "allocation_already_exists"):
                provision.provision(path, ALLOCATION, FakeHub(), profile)

    def test_common_provisioning_code_matches_matting(self):
        for name in ("sync_dir", "settle"):
            with self.subTest(function=name):
                self.assertEqual(inspect.getsource(getattr(provision, name)),
                                 inspect.getsource(getattr(matting_provision, name)))
        for name in ("HUB", "CDN", "CHUNK", "FLUSH_EVERY", "READ_SECONDS", "RESERVE"):
            with self.subTest(setting=name):
                self.assertEqual(getattr(provision, name), getattr(matting_provision, name))
        self.assertEqual(provision.RESERVE, 32 * 1024**3)

    def test_capacity_requires_room_for_budget_above_reserve(self):
        space = SimpleNamespace(f_bavail=34 * 1024**2, f_frsize=1024, f_files=0)
        with patch.object(provision.os, "statvfs", return_value=space):
            provision.capacity(Path("/jobs"), 0, 0)
            space.f_bavail -= 1
            with self.assertRaisesRegex(provision.ProvisionError, "free_reserve_too_small"):
                provision.capacity(Path("/jobs"), 0, 0)

    def test_capacity_accepts_an_unreported_free_file_count_and_enforces_a_reported_one(self):
        room = {"f_bavail": 40 * 1024**2, "f_frsize": 1024}
        need = provision.ENTRY_RESERVE + provision.ENTRY_BUDGET
        # The Studio share: a file total, and the free count as all ones (-1 in Python).
        for unreported in ({"f_files": 1794936, "f_favail": -1}, {"f_files": 1794936, "f_favail": 2**64 - 1},
                           {"f_files": 0, "f_favail": 0}):
            with patch.object(provision.os, "statvfs", return_value=SimpleNamespace(**room, **unreported)):
                provision.capacity(Path("/jobs"), 0, 0)
        with patch.object(provision.os, "statvfs",
                          return_value=SimpleNamespace(**room, f_files=1794936, f_favail=need)):
            provision.capacity(Path("/jobs"), 0, 0)
        with patch.object(provision.os, "statvfs",
                          return_value=SimpleNamespace(**room, f_files=1794936, f_favail=need - 1)):
            with self.assertRaisesRegex(provision.ProvisionError, "free_file_reserve_too_small"):
                provision.capacity(Path("/jobs"), 0, 0)

    def test_second_model_failure_retains_quarantine(self):
        class WrongSam(FakeHub):
            contents = {**FakeHub.contents, "sam": b"else-weights"}

        with tempfile.TemporaryDirectory() as directory, patch.object(provision, "capacity"):
            root = Path(directory)
            with self.assertRaisesRegex(provision.ProvisionError, "download_hash_mismatch"):
                provision.provision(root, ALLOCATION, WrongSam(), Profile())
            self.assertTrue((root / ALLOCATION / ".quarantine/dino/model.safetensors").is_file())
            self.assertFalse((root / ALLOCATION / "weights").exists())

    def test_flush_every_interval_and_tail(self):
        first, _ = Profile().model_files()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(provision, "capacity"),
            patch.object(provision, "CHUNK", 4),
            patch.object(provision, "FLUSH_EVERY", 8),
            patch.object(provision, "settle", wraps=provision.settle) as settle,
        ):
            provision.download(
                FakeHub(), first, first["files"][0], Path(directory) / "file",
                provision.monotonic() + 5, 0, 1,
            )
            self.assertEqual(settle.call_count, 2)

    def test_redirect_is_pinned_to_publisher_and_cdn(self):
        handler = provision.PinnedRedirect()
        source = Request(FakeHub.urls[0])
        self.assertEqual(handler.redirect_request(
            source, None, 302, "Found", {}, "https://us.aws.cdn.hf.co/pinned",
        ).host, "us.aws.cdn.hf.co")
        with self.assertRaisesRegex(provision.ProvisionError, "unexpected_download_redirect"):
            handler.redirect_request(source, None, 302, "Found", {}, "https://example.invalid/path")
        config = Request(FakeHub.urls[0].replace("model.safetensors", "config.json"))
        cache = config.full_url.replace("huggingface.co/", "huggingface.co/api/resolve-cache/models/").replace(
            "/resolve/", "/",
        )
        self.assertEqual(handler.redirect_request(config, None, 302, "Found", {}, cache).full_url, cache)
        with self.assertRaisesRegex(provision.ProvisionError, "unexpected_download_redirect"):
            handler.redirect_request(config, None, 302, "Found", {}, cache.replace("config.json", "other.json"))


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.flux = list(yaml.safe_load_all((STUDIO / "ks.yaml").read_text()))
        self.pin, _ = guard.check_segmentation_stage(STUDIO, self.flux)

    def test_published_serving_contract(self):
        memory = {
            "schema": "studio.segmentation-resources/v1",
            "measurement": {"request_gib": 16, "limit_gib": 16},
            "serving": {"request_gib": 4, "limit_gib": 5, "weight_load": "mmap"},
        }
        config = {
            "os": "linux", "architecture": "amd64",
            "config": {"User": "10001:10001", "Cmd": ["studio-segmentation", "serve"], "Labels": {
                "org.opencontainers.image.source": "https://github.com/thaynes43/haynes-studio",
                "io.haynes-studio.segmentation.resources": json.dumps(memory),
            }},
        }
        digest = "sha256:" + "1" * 64
        with patch.object(guard, "check_segmentation_stage", return_value=(self.pin, digest)), (
            patch.object(guard, "published_config", return_value=config)
        ) as fetch:
            guard.check_segmentation(STUDIO, self.flux)
            fetch.assert_called_once_with(guard.SEGMENTATION_IMAGE, digest)
            for name, serving, reason in (
                ("serving limit", {**memory["serving"], "limit_gib": 6}, "resources must match"),
                ("load setting", {**memory["serving"], "weight_load": "buffered"}, "pin memory-mapped"),
                ("measurement only", None, "carry the Studio profile resource contract"),
            ):
                with self.subTest(mutation=name):
                    config["config"]["Labels"]["io.haynes-studio.segmentation.resources"] = json.dumps({
                        **memory, "serving": serving,
                    })
                    with self.assertRaisesRegex(ValueError, reason):
                        guard.check_segmentation(STUDIO, self.flux)
            config["config"]["Labels"]["io.haynes-studio.segmentation.resources"] = json.dumps(memory)
            config["config"]["User"] = "1000:1000"
            with self.assertRaisesRegex(ValueError, "default to 10001:10001"):
                guard.check_segmentation(STUDIO, self.flux)

    def test_release_refuses_changed_serving_limit(self):
        release = PENDING / "release/release.yaml"
        read_text = Path.read_text

        def changed(path, *args, **kwargs):
            original = read_text(path, *args, **kwargs)
            return original.replace("servingLimit: 5Gi", "servingLimit: 6Gi") if path == release else original

        with patch.object(Path, "read_text", changed):
            with self.assertRaisesRegex(ValueError, "serving request, limit and render limit"):
                guard.check_segmentation_stage(STUDIO, self.flux)

    def test_release_refuses_measuring_smoke_command(self):
        stage = PENDING / "smoke"
        render = guard.render
        documents = render(stage)
        job = guard.resource(documents, "Job")
        guard.container(job)["command"] += ["--compare-load-strategy", "buffered"]
        with patch.object(guard, "render", side_effect=lambda path: documents if path == stage else render(path)):
            with self.assertRaisesRegex(ValueError, "unsafe segmentation smoke Job envelope"):
                guard.check_segmentation_stage(STUDIO, self.flux)

    def test_activation_refuses_limits_over_the_combined_budget(self):
        stage = PENDING / "activation"
        render = guard.render
        documents = render(stage)
        for resource, excess in (("cpu", "17"), ("cpu", "16500m"), ("memory", "33280Mi")):
            with self.subTest(resource=resource):
                mutated = copy.deepcopy(documents)
                values = guard.resource(mutated, "HelmRelease", "studio-render")["spec"]["values"]
                values["controllers"]["studio-render"]["containers"]["app"]["resources"]["limits"][resource] = excess
                with patch.object(guard, "render",
                                  side_effect=lambda path: mutated if path == stage else render(path)):
                    with self.assertRaisesRegex(ValueError, "matting with segmentation exceeds.*24 CPU or 55 GiB"):
                        guard.check_segmentation_stage(STUDIO, self.flux)

    def test_activation_checks_matting_alone_and_accepts_equivalent_quantities(self):
        stage = STUDIO.with_name("studio-render") / "matting-pending"
        render = guard.render
        documents = render(stage)
        for cpu, memory, refusal in (
            ("18000m", "32768Mi", None),
            ("19", "32Gi", "matting alone exceeds.*24 CPU or 50 GiB"),
            ("18", "33Gi", "matting alone exceeds.*24 CPU or 50 GiB"),
            ("500m", "32Gi", "offline render must keep 32 GiB"),
            ("18mcpu", "32Gi", "invalid Kubernetes CPU limit"),
            ("18", "32GB", "invalid Kubernetes memory limit"),
        ):
            with self.subTest(cpu=cpu, memory=memory):
                mutated = copy.deepcopy(documents)
                values = guard.resource(mutated, "HelmRelease", "studio-render")["spec"]["values"]
                app = values["controllers"]["studio-render"]["containers"]["app"]
                app["resources"]["limits"] = {"cpu": cpu, "memory": memory}
                with patch.object(guard, "render", side_effect=lambda path: mutated if path == stage else render(path)):
                    if refusal:
                        with self.assertRaisesRegex(ValueError, refusal):
                            guard.check_segmentation_stage(STUDIO, self.flux)
                    else:
                        guard.check_segmentation_stage(STUDIO, self.flux)

    def test_activation_checks_termination_grace(self):
        stage = PENDING / "activation"
        render = guard.render
        documents = render(stage)
        values = guard.resource(documents, "HelmRelease", "studio-render")["spec"]["values"]
        values["controllers"]["studio-render"]["pod"]["terminationGracePeriodSeconds"] = 30
        with patch.object(guard, "render", side_effect=lambda path: documents if path == stage else render(path)):
            with self.assertRaisesRegex(ValueError, "150 s pod grace period"):
                guard.check_segmentation_stage(STUDIO, self.flux)

    def test_workload_rejects_weight_owner_or_writable_weights(self):
        render = guard.render
        for stage in ("smoke", "activation"):
            rendered = render(PENDING / stage)
            for mutation in ("uid", "mount"):
                with self.subTest(stage=stage, mutation=mutation):
                    documents = copy.deepcopy(rendered)
                    if stage == "smoke":
                        job = guard.resource(documents, "Job")
                        security = job["spec"]["template"]["spec"]["securityContext"]
                        mount = next(item for item in guard.container(job)["volumeMounts"] if item["name"] == "weights")
                    else:
                        values = guard.resource(documents, "HelmRelease")["spec"]["values"]
                        security = values["controllers"]["studio-render"]["containers"]["segmentation"]["securityContext"]
                        mount, = values["persistence"]["segmentation-weights"]["advancedMounts"]["studio-render"]["segmentation"]
                    if mutation == "uid":
                        security.update(runAsUser=1000, runAsGroup=1000)
                    else:
                        mount["readOnly"] = False
                    with patch.object(guard, "render", side_effect=lambda path: documents if path == PENDING / stage
                                      else render(path)), self.assertRaisesRegex(ValueError, "segmentation"):
                        guard.check_segmentation_stage(STUDIO, self.flux)

    def test_superseded_and_zero_digests_never_accepted(self):
        for digest in (
            "sha256:58b037970810333c5ca1350d7acc32e113e06494ab49b69c184c0a8a93458e53",
            "sha256:05de65b703506a4591887e3063dde04a03aa142f28bacde5b3101a495b6a53c0",
            "sha256:3261efedf5d52baa4c53aa0ce6fc8f538b40cb0675fc2dc7a0c2ca74fd8184be",
            "sha256:" + "0" * 64,
        ):
            with self.subTest(digest=digest), (
                patch.object(guard, "check_segmentation_stage", return_value=(self.pin, digest))
            ), patch.object(guard, "published_config") as fetch:
                with self.assertRaisesRegex(ValueError, "published segmentation digest"):
                    guard.check_segmentation(STUDIO, self.flux)
                fetch.assert_not_called()

    def test_cilium_identity_exclusions(self):
        policy = guard.render(PENDING / "policy")
        download = guard.resource(policy, "CiliumNetworkPolicy", "studio-segmentation-provision")["spec"]
        mutated = download["egress"][0]["toEndpoints"][0]["matchLabels"]
        mutated["k8s:statefulset.kubernetes.io/pod-name"] = "coredns-0"
        with self.assertRaisesRegex(ValueError, "Cilium leaves"):
            guard.check_policy_selectors(policy, app_labels_only=True)

    def test_flux_cannot_apply_smoke(self):
        flux = copy.deepcopy(self.flux)
        extra = copy.deepcopy(guard.resource(flux, "Kustomization", "studio-segmentation-policy"))
        extra["metadata"]["name"] = "studio-segmentation-smoke"
        extra["spec"]["path"] = extra["spec"]["path"].replace("/policy", "/smoke")
        with self.assertRaisesRegex(ValueError, "Flux may apply only"):
            guard.check_segmentation_stage(STUDIO, [*flux, extra])


class RenderOverlayTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.studio = Path(directory) / "ai/studio"
        self.render = self.studio.with_name("studio-render")
        self.pending = self.studio / "segmentation-pending"
        self.write(self.render / "app")
        self.write(self.render / "matting-pending", "../app")
        self.write(self.pending / "activation", "render")
        self.write(self.pending / "activation/render", "../../../../studio-render/matting-pending")
        (self.render / "ks.yaml").write_text(f"spec:\n  path: ./{guard.STUDIO_PATH.with_name('studio-render')}/app\n")

    def write(self, path, base=None, patches=()):
        path.mkdir(parents=True, exist_ok=True)
        (path / "kustomization.yaml").write_text(yaml.safe_dump({
            "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
            "resources": [base] if base else [], "patches": list(patches),
        }))

    def test_unknown_render_overlay_with_different_cpu_split_is_refused(self):
        guard._check_render_overlays(self.studio, self.pending)
        split = {"target": {"kind": "HelmRelease", "name": "studio-render"}, "patch": yaml.safe_dump([{
            "op": "replace", "path": "/spec/values/controllers/studio-render/containers/app/resources/limits/cpu",
            "value": "17",
        }])}
        for root, base in ((self.render, "../app"), (self.studio, "../../studio-render/matting-pending")):
            with self.subTest(root=root):
                extra = root / "other-pending"
                self.write(extra, base, [split])
                with self.assertRaisesRegex(ValueError, "unknown studio-render overlay.*other-pending"):
                    guard._check_render_overlays(self.studio, self.pending)
                (extra / "kustomization.yaml").unlink()

    def test_segmentation_without_matting_is_refused(self):
        self.write(self.pending / "activation/render", "../../../../studio-render/app")
        with self.assertRaisesRegex(ValueError, "segmentation activation must build on the matting render overlay"):
            guard._check_render_overlays(self.studio, self.pending)

    def test_unknown_render_overlay_can_supply_its_own_helmrelease(self):
        extra = self.studio.parent.parent / "other/render"
        self.write(extra, "release.json")
        (extra / "release.json").write_text(json.dumps({
            "apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease",
            "metadata": {"name": "studio-render", "namespace": "ai"},
        }))
        with self.assertRaisesRegex(ValueError, "unknown studio-render overlay.*other/render"):
            guard._check_render_overlays(self.studio, self.pending)


class IdleGateTests(unittest.TestCase):
    def test_wan_requires_ready_and_explicitly_idle_video_and_foley(self):
        gate = ROOT / "scripts/studio-segmentation/check-wan-idle.sh"
        shell = r"""
kubectl() {
    [[ "$1 $2 $3 $4 $5 $6" == '-n ai exec deploy/wan-video -c api' &&
       "${!#}" == 'http://127.0.0.1:8080/v1/ready' ]] || return 99
    printf '%s\n' "$reply"
    return "$api_exit"
}
export -f kubectl
export reply=$2 api_exit=$3
bash "$1"
"""
        ready = {"ready": True, "active_video_job": None, "active_foley_job": None}
        for label, reply, api_exit, passes in (
            ("idle", ready, 0, True),
            ("Foley active", {**ready, "active_foley_job": "foley-1"}, 0, False),
            ("video active", {**ready, "active_video_job": "video-1"}, 0, False),
            ("missing Foley state", {"ready": True, "active_video_job": None}, 0, False),
            ("missing video state", {"ready": True, "active_foley_job": None}, 0, False),
            ("not ready", {**ready, "ready": False}, 0, False),
            ("legacy health reply", {"api": "ok", "active_job": None}, 0, False),
            ("API call failed", ready, 22, False),
        ):
            with self.subTest(label=label):
                result = subprocess.run(
                    ["bash", "-c", shell, "test", str(gate), json.dumps(reply), str(api_exit)],
                    text=True, capture_output=True, timeout=5,
                )
                self.assertEqual(result.returncode == 0, passes, result.stderr)


class JobWaitTests(unittest.TestCase):
    def test_recovers_from_one_failed_read_and_stops_on_either_terminal_condition(self):
        wait = ROOT / "scripts/studio-segmentation/wait-job.sh"
        shell = r"""
kubectl() {
    [[ "$1 $2 $3" == '-n ai --request-timeout=10s' ]] || return 99
    shift 3
    case "$*" in
        'get job/fixture -o json')
            if [[ ! -e "$attempt" ]]; then
                touch "$attempt"
                printf 'transient read failure\n' >&2
                return 1
            fi
            printf '{"status":{"conditions":[{"type":"%s","status":"True"}]}}\n' "$terminal"
            ;;
        'get job/fixture -o yaml')
            printf 'fixture Job capture\n'
            ;;
        'get pods -l batch.kubernetes.io/job-name=fixture -o json')
            printf '%s\n' '{"items":[{"metadata":{"name":"fixture-pod"},"status":{"phase":"Failed","containerStatuses":[{"name":"segmentation","state":{"terminated":{"reason":"OOMKilled","exitCode":137}}}]}}]}'
            ;;
        'logs job/fixture -c segmentation')
            printf 'fixture failure logs\n'
            ;;
        *) return 99 ;;
    esac
}
sleep() { :; }
export -f kubectl sleep
export attempt=$2 terminal=$3
bash "$1" fixture 960
"""
        for condition, code in (("Complete", 0), ("Failed", 1)):
            with self.subTest(condition=condition), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run(
                    ["bash", "-c", shell, "test", str(wait), str(Path(directory) / "attempt"), condition],
                    text=True, capture_output=True, timeout=5,
                )
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn("transient read failure", result.stderr)
                for evidence in ("fixture Job capture", "OOMKilled", "fixture failure logs"):
                    self.assertEqual(evidence in result.stderr, condition == "Failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
