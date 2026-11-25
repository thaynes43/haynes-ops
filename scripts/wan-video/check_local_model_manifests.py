"""Check the dormant and staged manifests without contacting the cluster."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

WAN = Path(__file__).resolve().parents[2] / "kubernetes/main/apps/ai/wan-video"
PENDING = WAN / "local-model-pending"
sys.path.insert(0, str(WAN / "app"))
import product
import product_local as local


def documents(*command):
    return [doc for doc in yaml.safe_load_all(subprocess.check_output(command, text=True)) if doc]


def resource(rendered, kind, name):
    found, = [doc for doc in rendered if doc["kind"] == kind and doc["metadata"]["name"] == name]
    return found


def pod_spec(release):
    with tempfile.TemporaryDirectory() as tmp:
        values = Path(tmp) / "values.yaml"
        values.write_text(yaml.safe_dump(release["spec"]["values"]))
        rendered = documents("helm", "template", "wan-video", os.environ["WAN_HELM_CHART"],
                             "--namespace", "ai", "--values", str(values))
    return resource(rendered, "Deployment", "wan-video")["spec"]["template"]["spec"]


def workspace_mounts(container):
    return {(mount["mountPath"], mount.get("subPath"), mount.get("readOnly", False))
            for mount in container["volumeMounts"] if mount["name"] == "workspace"}


class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.activation = documents("kustomize", "build", PENDING / "activation")
        cls.policy = documents("kustomize", "build", PENDING / "download-policy")
        cls.weights = documents("kustomize", "build", PENDING / "weights")
        cls.ai = documents("kustomize", "build", WAN.parent)
        cls.provisioning_flux = [
            yaml.safe_load((PENDING / f"{name}-ks.yaml").read_text()) for name in ("download-policy", "weights")
        ]
        cls.dormant = documents("kustomize", "build", WAN / "app")
        cls.active_pod = pod_spec(resource(cls.activation, "HelmRelease", "wan-video"))
        cls.dormant_pod = pod_spec(resource(cls.dormant, "HelmRelease", "wan-video"))

    def test_rendered_profile_accepts_only_integer_ceilings_from_1_to_512(self):
        config = resource(self.activation, "ConfigMap", "wan-local-model-profile")
        config_volume, = [v for v in self.active_pod["volumes"] if v["name"] == "config"]
        self.assertIn({"configMap": {"name": config["metadata"]["name"]}},
                      config_volume["projected"]["sources"])
        for container in self.active_pod["containers"]:
            self.assertIn({"name": "config", "mountPath": "/config/video", "readOnly": True},
                          container["volumeMounts"])
        content = config["data"]["local-model.json"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / "local-model.json"
            path.write_text(content)
            profile = local.load_profile(directory)
            self.assertIs(type(profile["unloaded_max_mib"]), int)
            self.assertIn(profile["unloaded_max_mib"], range(1, 513))
            for value in (1, 266, 512):
                with self.subTest(valid=value):
                    path.write_text(json.dumps({**profile, "unloaded_max_mib": value}))
                    self.assertEqual(local.load_profile(directory)["unloaded_max_mib"], value)
            for value in (-1, 0, 513, 266.0, True, None, "266"):
                with self.subTest(invalid=value):
                    path.write_text(json.dumps({**profile, "unloaded_max_mib": value}))
                    with self.assertRaisesRegex(product.RequestError, "Invalid local-model profile"):
                        local.load_profile(directory)

    def test_wan_inventory_is_dormant_and_the_activation_overlay_stays_staged(self):
        # Reverted on 2026-11-25: the sidecar never became Ready on activation (see
        # "Changed" in docs/reference/local-model-hosting.md). The overlay is kept,
        # unreferenced, for the next attempt; the checks on it below still run.
        flux = resource(self.ai, "Kustomization", "wan-video")["spec"]
        self.assertEqual(flux["path"], "./kubernetes/main/apps/ai/wan-video/app")
        self.assertEqual(flux["timeout"], "5m")
        self.assertNotIn("healthChecks", flux)
        names = {doc["metadata"]["name"] for doc in self.ai}
        self.assertFalse(names & {"wan-local-model-download-policy", "wan-local-model-weights"})
        self.assertFalse({doc["kind"] for doc in self.activation} & {"Job", "CiliumNetworkPolicy"})
        dormant_pvc = resource(self.dormant, "PersistentVolumeClaim", "wan-video")
        active_pvc = resource(self.activation, "PersistentVolumeClaim", "wan-video")
        self.assertEqual(active_pvc, dormant_pvc)
        self.assertEqual(active_pvc["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"], "disabled")

    def test_recovery_helper_is_in_the_api_code_config_for_both_paths(self):
        for rendered in (self.dormant, self.activation):
            data = resource(rendered, "ConfigMap", "wan-video-config")["data"]
            self.assertEqual(data["clear_local_model_hold.py"],
                             (WAN / "app" / "clear_local_model_hold.py").read_text())

    def test_dormant_release_has_no_local_model_resources_or_container(self):
        self.assertFalse({doc["kind"] for doc in self.dormant} & {"Job", "CiliumNetworkPolicy"})
        self.assertNotIn("wan-local-model-profile", {doc["metadata"]["name"] for doc in self.dormant})
        self.assertEqual({c["name"] for c in self.dormant_pod["containers"]}, {"app", "api", "foley"})

    def test_only_required_workspace_paths_are_mounted_in_local_model(self):
        active = {c["name"]: c for c in self.active_pod["containers"] + self.active_pod["initContainers"]}
        for container in self.dormant_pod["containers"] + self.dormant_pod["initContainers"]:
            with self.subTest(container=container["name"]):
                self.assertEqual(workspace_mounts(container), workspace_mounts(active[container["name"]]))
        self.assertEqual(workspace_mounts(active["local-model"]), {
            ("/workspace/compute.lock", "compute.lock", True),
            ("/workspace/local-model", "local-model", False),
            ("/workspace/input/studio-v1", "input/studio-v1", True),
            ("/workspace/local-model-weights", "local-model-weights", True),
        })
        self.assertEqual(active["app"]["resources"]["limits"]["nvidia.com/gpu"], 2)
        self.assertNotIn("nvidia.com/gpu", active["local-model"]["resources"]["limits"])

    def test_activation_initializer_creates_subpaths_and_preserves_lease_inode(self):
        init, = [c for c in self.active_pod["initContainers"] if c["name"] == "prepare-local-model"]
        self.assertEqual(workspace_mounts(init), {("/workspace", None, False)})
        self.assertIn({"name": "NVIDIA_VISIBLE_DEVICES", "value": "void"}, init["env"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_code = init["command"][-1].replace('"/workspace"', repr(tmp))
            command = (sys.executable, "-B", "-c", init_code)
            subprocess.run(command, check=True)
            lease = root / "compute.lock"
            self.assertTrue(lease.is_file())
            inode = lease.stat().st_ino
            self.assertTrue((root / "local-model").is_dir())
            self.assertTrue((root / "input/studio-v1").is_dir())
            subprocess.run(command, check=True)
            self.assertEqual(lease.stat().st_ino, inode)

    def test_sidecar_probes_only_check_the_listener_with_time_to_start(self):
        container, = [c for c in self.active_pod["containers"] if c["name"] == "local-model"]
        self.assertEqual(container["image"],
                         "ghcr.io/thaynes43/studio-local-model:"
                         "b12679-79fa01b5ff27b2b7aeacc78b079be7d99864d252-45545041495-1"
                         "@sha256:e2e9b5e9b71aee89d0b8077ae47be113587b61054957b10f694cb4ab92b93f03")
        for name in ("startupProbe", "readinessProbe"):
            with self.subTest(probe=name):
                probe = container[name]
                self.assertGreaterEqual(probe["timeoutSeconds"], 10)
                self.assertEqual(probe["exec"]["command"], [
                    "python3", "-m", "studio_local_backend", "ready", "--workspace", "/workspace",
                ])

    def test_provisioner_can_only_write_its_weights_directory(self):
        flux = resource(self.provisioning_flux, "Kustomization",
                        "wan-local-model-weights")["spec"]
        self.assertEqual(flux["dependsOn"], [{"name": "wan-local-model-download-policy"}, {"name": "wan-video"}])
        self.assertEqual(flux["healthChecks"], [{
            "apiVersion": "batch/v1", "kind": "Job", "name": "wan-local-model-provision-r2", "namespace": "ai",
        }])
        self.assertTrue(flux["prune"])
        job = resource(self.weights, "Job", "wan-local-model-provision-r2")
        self.assertEqual(job["spec"]["activeDeadlineSeconds"], 1800)
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertEqual(job["spec"]["podReplacementPolicy"], "Failed")
        spec = job["spec"]["template"]["spec"]
        self.assertEqual(spec["restartPolicy"], "Never")
        provision, = spec["containers"]
        self.assertEqual(provision["image"], "docker.io/library/python:3.12.16-slim-bookworm@"
                                             "sha256:1475c4f5b6bcb7d35e57661d6f5d7429ec4041275633c15f2b49350ac898276d")
        self.assertIn({"name": "config", "mountPath": "/config", "readOnly": True},
                      provision["volumeMounts"])
        config, = [doc for doc in self.weights if doc["kind"] == "ConfigMap"]
        self.assertIn({"name": "config", "configMap": {"name": config["metadata"]["name"]}}, spec["volumes"])
        pins = json.loads(config["data"]["weights.json"])
        self.assertEqual(pins["byte_cap"], 7499546400)
        self.assertEqual(sum(file["bytes"] for file in pins["files"]), pins["byte_cap"])
        self.assertFalse(spec.get("initContainers"))
        self.assertEqual(workspace_mounts(provision), {
            ("/workspace/local-model-weights", "local-model-weights", False),
        })

    def test_provisioner_dns_and_https_allow_hub_and_cdn_subdomains(self):
        flux = resource(self.provisioning_flux, "Kustomization",
                        "wan-local-model-download-policy")
        self.assertEqual(flux["spec"]["path"], f"./{(PENDING / 'download-policy').relative_to(WAN.parents[4])}")
        self.assertTrue(flux["spec"]["prune"])
        self.assertEqual({doc["kind"] for doc in self.policy}, {"CiliumNetworkPolicy"})
        policy = resource(self.policy, "CiliumNetworkPolicy", "wan-local-model-provision")
        self.assertEqual(policy["spec"]["endpointSelector"], {
            "matchLabels": {"app.kubernetes.io/name": "wan-local-model-provision"},
        })
        dns, https = policy["spec"]["egress"]
        allowed = [{"matchName": "huggingface.co"}, {"matchPattern": "**.cdn.hf.co"}]
        self.assertEqual(dns["toPorts"][0]["rules"]["dns"], allowed)
        self.assertEqual(https["toFQDNs"], allowed)
        self.assertEqual(https["toPorts"][0]["ports"], [{"port": "443", "protocol": "TCP"}])


if __name__ == "__main__":
    unittest.main()
