"""Offline tests for the staged release guard and create-only two-model provisioner."""

import copy
import hashlib
import importlib.util
import inspect
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from stat import S_IMODE
from types import SimpleNamespace
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
        self.pin, self.digest = guard.check_segmentation_stage(STUDIO, self.flux)

    def test_published_measurement_contract(self):
        memory = {"schema": "studio.segmentation-resources/v1",
                  "measurement": {"request_gib": 16, "limit_gib": 16}, "serving": None}
        config = {
            "os": "linux", "architecture": "amd64", "config": {"User": "10001:10001", "Labels": {
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
            self.pin["measurementLimit"] = "20Gi"
            guard.check_segmentation(STUDIO, self.flux)
            self.pin["measurementLimit"] = "15Gi"
            with self.assertRaisesRegex(ValueError, "minimums"):
                guard.check_segmentation(STUDIO, self.flux)
            self.pin["measurementLimit"] = "20Gi"
            config["config"]["User"] = "1000:1000"
            with self.assertRaisesRegex(ValueError, "default to 10001:10001"):
                guard.check_segmentation(STUDIO, self.flux)
            config["config"]["User"] = "10001:10001"
            self.pin["servingLimit"] = "16Gi"
            with self.assertRaisesRegex(ValueError, "resources must match"):
                guard.check_segmentation(STUDIO, self.flux)
            memory["serving"] = {"request_gib": 8, "limit_gib": 10}
            config["config"]["Labels"]["io.haynes-studio.segmentation.resources"] = json.dumps(memory)
            self.pin.update(servingRequest="8Gi", servingLimit="10Gi", renderMemoryLimit="22Gi")
            guard.check_segmentation(STUDIO, self.flux)
            self.pin.update(servingLimit="11Gi", renderMemoryLimit="21Gi")
            with self.assertRaisesRegex(ValueError, "resources must match"):
                guard.check_segmentation(STUDIO, self.flux)

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

    def test_zero_digest_never_accepted(self):
        with patch.object(guard, "check_segmentation_stage", return_value=(self.pin, "sha256:" + "0" * 64)), (
            patch.object(guard, "published_config")
        ) as fetch:
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
