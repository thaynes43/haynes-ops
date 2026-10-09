"""Finite public-only preparation checks; no API, private input or runtime action."""
import json
import re
import os
import subprocess
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent


def needs_source_build(files, event):
    return event == "workflow_dispatch" or any(path.startswith(("scripts/book-native-scan-fixture/", "scripts/ci-cache/")) or path == ".github/workflows/book-native-scan-fixture.yml" for path in files)


def admit_version(release_tag, release_tz, docker_tag, docker_digest, protocol_tag, protocol_digest, protocol_tz, source_build):
    assert (docker_tag, docker_digest) == (protocol_tag, protocol_digest), "frozen fixture internal tag/digest drift"
    current = (release_tag, release_tz) == (protocol_tag, protocol_tz)
    assert current or not source_build, "fixture source build needs reviewed current native successor"
    return "current" if current else "obsolete-no-build-no-publication"


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

    def test_native_version_digest_timezone_and_source_admission(self):
        release = (ROOT.parents[1] / "kubernetes/main/apps/media/kavita/app/helmrelease.yaml").read_text()
        image = re.search(r"repository: docker\.io/jvmilazz0/kavita\s+tag: ([^\s]+)", release)
        self.assertIsNotNone(image)
        tag = image.group(1)
        docker = (ROOT / "Dockerfile").read_text()
        protocol = (ROOT / "FixtureProtocol.cs").read_text()
        docker_tag, digest = re.search(r"kavita:([^\s@]+)@(sha256:[0-9a-f]{64})", docker).groups()
        protocol_tag = re.search(r'NativeTag = "([^"]+)"', protocol).group(1)
        protocol_digest = re.search(r'NativeImageDigest = "([^"]+)"', protocol).group(1)
        protocol_tz = re.search(r'NativeTimeZone = "([^"]+)"', protocol).group(1)
        release_tz = re.search(r"TZ: ([^\s]+)", release).group(1)
        status = admit_version(tag, release_tz, docker_tag, digest, protocol_tag, protocol_digest, protocol_tz, os.environ.get("FIXTURE_SOURCE_BUILD", "true") == "true")
        self.assertIn(f'TZ={protocol_tz}', docker)
        self.assertIn("'scripts/book-native-scan-fixture/Dockerfile'", (ROOT.parents[1] / '.github/renovate.json5').read_text())
        print(f"PASS frozen native source admission: {status}")

    def test_production_only_upgrade_stays_independent_and_never_publishes(self):
        self.assertFalse(needs_source_build(["kubernetes/main/apps/media/kavita/app/helmrelease.yaml"], "pull_request"))
        self.assertFalse(needs_source_build(["kubernetes/main/apps/media/kavita/app/helmrelease.yaml"], "push"))
        self.assertFalse(needs_source_build([".github/renovate.json5"], "push"))
        self.assertTrue(needs_source_build(["scripts/book-native-scan-fixture/Program.cs"], "push"))
        self.assertTrue(needs_source_build(["scripts/ci-cache/verify_dockerhub_cache.py"], "pull_request"))
        args = ("new-production-version", "UTC", "frozen", "digest", "frozen", "digest", "America/New_York")
        self.assertEqual(admit_version(*args, source_build=False), "obsolete-no-build-no-publication")
        with self.assertRaises(AssertionError):
            admit_version(*args, source_build=True)
        with self.assertRaises(AssertionError):
            admit_version("frozen", "America/New_York", "frozen", "changed", "frozen", "digest", "America/New_York", False)


if __name__ == "__main__":
    if sys.argv[1:] == ["--scope"]:
        event = os.environ["GITHUB_EVENT_NAME"]
        if event == "workflow_dispatch":
            build = True
        else:
            base = os.environ["BASE_SHA"]
            assert re.fullmatch(r"[0-9a-f]{40}", base), "unknown diff base"
            subprocess.run(["git", "fetch", "--no-tags", "--depth=1", "origin", base], check=True, capture_output=True, timeout=30)
            files = subprocess.check_output(["git", "diff", "--name-only", base, "HEAD"], text=True, timeout=5).splitlines()
            build = needs_source_build(files, event)
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"build={str(build).lower()}\n")
        print(f"fixture source build/publication={str(build).lower()}")
    else:
        unittest.main()
