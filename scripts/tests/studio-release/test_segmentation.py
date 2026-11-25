"""Offline tests for the staged release guard and create-only two-model provisioner."""

import copy
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
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
                self.assertEqual(weights.stat().st_mode & 0o777, 0o444)
            with self.assertRaisesRegex(provision.ProvisionError, "allocation_already_exists"):
                provision.provision(path, ALLOCATION, FakeHub(), profile)

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
            "os": "linux", "architecture": "amd64", "config": {"Labels": {
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
            self.pin["servingLimit"] = "16Gi"
            with self.assertRaisesRegex(ValueError, "resources must match"):
                guard.check_segmentation(STUDIO, self.flux)

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


if __name__ == "__main__":
    unittest.main()
