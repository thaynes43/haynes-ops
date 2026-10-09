"""Finite public-only preparation checks; no API, private input or runtime action."""
import json
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent


class PreparedFixtureTests(unittest.TestCase):
    def setUp(self):
        self.packet = json.loads((ROOT / "prepared-runtime.json").read_text())
        self.job = self.packet["job"]
        self.pod = self.job["spec"]["template"]["spec"]
        self.container, = self.pod["containers"]

    def test_no_apply_authority_or_bound_runtime(self):
        self.assertTrue(self.packet["preparedOnly"])
        self.assertFalse(self.packet["runtimeApproval"])
        self.assertNotIn("apiVersion", self.packet)
        self.assertIn("REVIEWED_", self.container["image"])

    def test_single_original_clock_and_worker_budget(self):
        spec = self.job["spec"]
        self.assertEqual((spec["activeDeadlineSeconds"], spec["backoffLimit"], spec["parallelism"], spec["completions"]), (180, 0, 1, 1))
        self.assertEqual(self.pod["nodeName"], "talosw01")
        self.assertEqual(self.container["resources"]["limits"]["cpu"], "500m")
        self.assertEqual(self.container["command"][:3], ["nice", "-n", "19"])

    def test_private_tmpfs_only_no_production_or_secrets(self):
        self.assertFalse(self.pod["automountServiceAccountToken"])
        self.assertNotIn("serviceAccountName", self.pod)
        self.assertNotIn("envFrom", self.container)
        self.assertEqual({entry["name"] for entry in self.container["env"]}, {"POD_UID", "NODE_NAME", "TZ"})
        self.assertEqual(next(e["value"] for e in self.container["env"] if e["name"] == "TZ"), "America/New_York")
        self.assertEqual(self.job["spec"]["template"]["metadata"]["annotations"], {"k8tz.io/inject": "false"})
        for volume in self.pod["volumes"]:
            self.assertEqual(set(volume), {"name", "emptyDir"})
            self.assertEqual(volume["emptyDir"]["medium"], "Memory")
        self.assertEqual({entry["mountPath"] for entry in self.container["volumeMounts"]}, {"/fixture-input", "/kavita/config", "/data/cephfs-hdd/data/media/books/EBooks", "/tmp"})
        self.assertTrue(self.container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(self.container["securityContext"]["capabilities"]["drop"], ["ALL"])

    def test_explicit_deny_overrides_additive_allow(self):
        deny = self.packet["networkDeny"]["spec"]
        self.assertEqual(deny["endpointSelector"]["matchLabels"], {"app.kubernetes.io/name": self.job["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/name"]})
        self.assertEqual(deny["ingressDeny"], [{"fromEntities": ["all"]}])
        self.assertEqual(deny["egressDeny"], [{"toEntities": ["all"]}])
        self.assertNotIn("egress", deny)

    def test_image_build_context_has_only_generic_sources(self):
        allow = (ROOT / "Dockerfile.dockerignore").read_text().splitlines()
        self.assertEqual(allow[0], "**")
        allowed_files = [line[1:] for line in allow if line.startswith("!") and not line.endswith("/")]
        self.assertEqual(set(allowed_files), {"scripts/book-native-scan-fixture/" + name for name in ("Dockerfile", "NativeScannerFixture.csproj", "FixtureProtocol.cs", "Program.cs")})

    def test_native_version_and_timezone_match_gitops_source(self):
        release = (ROOT.parents[1] / "kubernetes/main/apps/media/kavita/app/helmrelease.yaml").read_text()
        image = re.search(r"repository: docker\.io/jvmilazz0/kavita\s+tag: ([^\s]+)", release)
        self.assertIsNotNone(image)
        tag = image.group(1)
        docker = (ROOT / "Dockerfile").read_text()
        protocol = (ROOT / "FixtureProtocol.cs").read_text()
        self.assertIn(f"kavita:{tag}@sha256:", docker)
        self.assertIn(f'NativeTag = "{tag}"', protocol)
        self.assertIn("TZ: America/New_York", release)
        self.assertIn('NativeTimeZone = "America/New_York"', protocol)


if __name__ == "__main__":
    unittest.main()
