"""Verify deployment stages with a stub registry and no downloaded model."""

import importlib.util
import json
from hashlib import sha1, sha256
from io import BytesIO, StringIO
from pathlib import Path
import os
import runpy
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

STAGE = Path("kubernetes/main/apps/ai/studio/upscaling-pending")
SCRIPT = runpy.run_path(str(STAGE / "policy/provision.py"))
PROVISION = SCRIPT["provision"]
DIGEST = "sha256:" + "a" * 64
GRAPH = b"a small synthetic graph"
ALLOCATION = "upscaling-op461-fsrcnn-x2-v1-02"
NOTICE = b"Synthetic model licence notice"
COMPARE_ALLOCATION = "upscaling-op461-compare-000000000001"
PROXY = "library/sha256/aa/" + "a" * 64 + ".mp4"
PRODUCTION = "prd_0" + "A" * 25
DELIVERY = "dlv_0" + "B" * 25
OTHER_DELIVERY = "dlv_0" + "C" * 25
MASTER = f"productions/{PRODUCTION}/sha256/bb/" + "b" * 64 + ".mp4"
COMPARE_JOB = runpy.run_path(str(STAGE / "compare/render.py"))["comparison_job"]
VERIFY_DELIVERY = runpy.run_path(str(STAGE / "compare/verify_delivery.py"))["verified_delivery"]


class Response(BytesIO):
    status = 200
    headers = {}


class ProvisionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory(prefix="upscale-provision-")))
        constants = PROVISION.__globals__
        self.blob = sha1(b"blob " + str(len(GRAPH)).encode() + b"\0" + GRAPH, usedforsecurity=False).hexdigest()
        self.enterContext(patch.dict(
            constants, GRAPH_BYTES=len(GRAPH), GRAPH_SHA256=sha256(GRAPH).hexdigest(), GRAPH_BLOB=self.blob,
        ))
        self.free = SimpleNamespace(f_files=100_000, f_favail=100_000, f_bavail=128 * 1024**2, f_frsize=1024)
        self.enterContext(patch("os.statvfs", return_value=self.free))
        self.opener = SimpleNamespace(open=lambda *_args, **_kwargs: Response(GRAPH))

    def test_flushed_and_create_only(self):
        constants = PROVISION.__globals__
        real_settle = constants["settle"]
        flushes = []

        def settle(fd):
            flushes.append(fd)
            real_settle(fd)

        with patch.dict(constants, CHUNK=4, FLUSH_EVERY=8, settle=settle):
            proof = PROVISION(self.root, ALLOCATION, self.opener, b"{}", NOTICE)
        run = self.root / ALLOCATION
        weights = run / "weights"
        try:
            self.assertEqual(proof["graph_git_blob"], self.blob)
            self.assertEqual(proof["graph_sha256"], sha256(GRAPH).hexdigest())
            self.assertEqual(len(flushes), len(GRAPH) // 8 + 1)
            self.assertEqual((weights / "FSRCNN_x2.pb").read_bytes(), GRAPH)
            self.assertEqual((weights / "FSRCNN-NOTICE").read_bytes(), NOTICE)
            self.assertEqual((weights / "FSRCNN-NOTICE").stat().st_mode & 0o777, 0o444)
            self.assertEqual(proof["notice_sha256"], sha256(NOTICE).hexdigest())
            with self.assertRaisesRegex(SCRIPT["ProvisionError"], "allocation_already_exists"):
                PROVISION(self.root, ALLOCATION, self.opener, b"{}", NOTICE)
        finally:
            run.chmod(0o700)
            weights.chmod(0o700)

    def test_bad_git_blob_stays_quarantined(self):
        with patch.dict(PROVISION.__globals__, GRAPH_BLOB="0" * 40):
            with self.assertRaisesRegex(SCRIPT["ProvisionError"], "download_hash_mismatch"):
                PROVISION(self.root, ALLOCATION, self.opener, b"{}", NOTICE)
        self.assertFalse((self.root / ALLOCATION / "weights").exists())

    def test_inode_reserve_refuses_before_write(self):
        self.free.f_favail = 2047
        with self.assertRaisesRegex(SCRIPT["ProvisionError"], "free_inode_reserve_too_small"):
            PROVISION(self.root, ALLOCATION, self.opener, b"{}", NOTICE)
        self.assertFalse((self.root / ALLOCATION).exists())

    def test_unreported_free_file_count_is_accepted_and_a_reported_one_enforced(self):
        capacity = PROVISION.__globals__["capacity"]
        need = PROVISION.__globals__["INODE_BUDGET_AND_RESERVE"]
        # The Studio share: a file total, and the free count as all ones (-1 in Python).
        for files, free in ((1794936, -1), (1794936, 2**64 - 1), (0, 0)):
            self.free.f_files, self.free.f_favail = files, free
            capacity(self.root, 0, check_inodes=True)
        self.free.f_files, self.free.f_favail = 1794936, need
        capacity(self.root, 0, check_inodes=True)
        self.free.f_favail = need - 1
        with self.assertRaisesRegex(SCRIPT["ProvisionError"], "free_inode_reserve_too_small"):
            capacity(self.root, 0, check_inodes=True)

    def test_the_studio_shares_counts_do_not_stop_provisioning(self):
        self.free.f_files, self.free.f_favail = 1794936, -1
        PROVISION(self.root, ALLOCATION, self.opener, b"{}", NOTICE)
        self.assertTrue((self.root / ALLOCATION).is_dir())


class OutputReservationTest(unittest.TestCase):
    def test_reserves_only_a_new_group_writable_directory(self):
        script = runpy.run_path(str(STAGE / "compare/reserve.py"))
        main = script["main"]
        root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.dict(main.__globals__, JOBS=root))
        root.chmod(0o700)
        target = root / COMPARE_ALLOCATION
        with patch("sys.stdout"):
            self.assertEqual(main(["../other"]), 1)
            self.assertFalse(list(root.iterdir()))
            self.assertEqual(main([COMPARE_ALLOCATION]), 0)
            self.assertEqual(target.stat().st_mode & 0o7777, 0o2770)
            self.assertEqual(target.stat().st_gid, os.getgid())
            self.assertFalse(list(target.iterdir()))
            self.assertEqual(main([COMPARE_ALLOCATION]), 1)
            self.assertEqual(list(root.iterdir()), [target])

    def test_refuses_a_symlink_at_the_output_name(self):
        script = runpy.run_path(str(STAGE / "compare/reserve.py"))
        main = script["main"]
        root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.dict(main.__globals__, JOBS=root))
        existing = root / "existing"
        existing.mkdir(mode=0o700)
        original_mode = existing.stat().st_mode
        (root / COMPARE_ALLOCATION).symlink_to(existing, target_is_directory=True)
        with patch("sys.stdout"):
            self.assertEqual(main([COMPARE_ALLOCATION]), 1)
        self.assertEqual(existing.stat().st_mode, original_mode)


class DeliveryGateTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE deliveries (id TEXT PRIMARY KEY, document TEXT)")
        self.proxy = SimpleNamespace(text=PROXY, sha256="a" * 64)
        self.master = SimpleNamespace(text=MASTER, sha256="b" * 64)
        self.read_only = None
        self.transaction_is_read_only = True
        self.verdicts = self.enterContext(patch("sys.stderr", new_callable=StringIO))

    def exec_driver_sql(self, statement, parameters=()):
        if statement == "SET TRANSACTION READ ONLY":
            self.read_only = "on" if self.transaction_is_read_only else "off"
            return None
        self.assertIsNotNone(self.read_only, "transaction mode must be set before any read")
        if statement == "SHOW transaction_read_only":
            return SimpleNamespace(scalar_one=lambda: self.read_only)
        self.assertEqual(self.read_only, "on")
        # SQLite exercises the actual JSON role predicates and row limit.
        result = self.db.execute(statement.replace("%s", "?"), parameters)
        return SimpleNamespace(all=result.fetchall)

    def add_delivery(self, delivery=DELIVERY, proxy=None, master=None):
        proxy, master = proxy or self.proxy, master or self.master
        self.db.execute("INSERT INTO deliveries VALUES (?, ?)", (delivery, json.dumps({
            "proxy": {"locator": proxy.text, "sha256": proxy.sha256},
            "master": {"locator": master.text, "sha256": master.sha256},
        })))

    def test_exactly_one_record_supplies_both_locators_and_id(self):
        self.add_delivery()
        self.add_delivery(OTHER_DELIVERY, master=self.proxy)
        self.assertEqual(VERIFY_DELIVERY(self, self.proxy, self.master, DELIVERY),
                         {"id": DELIVERY, "proxy": PROXY, "master": MASTER})
        self.assertEqual(self.verdicts.getvalue().count("PASS "), 3)
        self.assertNotIn(PROXY, self.verdicts.getvalue())
        self.assertNotIn(MASTER, self.verdicts.getvalue())

    def test_crossed_roles_unpaired_hashes_and_duplicate_rows_are_refused(self):
        self.add_delivery(proxy=self.master, master=self.proxy)
        self.assertIsNone(VERIFY_DELIVERY(self, self.proxy, self.master, DELIVERY))
        self.db.execute("DELETE FROM deliveries")
        self.add_delivery(master=self.proxy)
        self.add_delivery(OTHER_DELIVERY, proxy=self.master)
        self.assertIsNone(VERIFY_DELIVERY(self, self.proxy, self.master, DELIVERY))
        self.db.execute("DELETE FROM deliveries")
        self.add_delivery()
        self.add_delivery(OTHER_DELIVERY)
        self.assertIsNone(VERIFY_DELIVERY(self, self.proxy, self.master, DELIVERY))
        self.assertEqual(self.verdicts.getvalue().count("FAIL exactly one delivery"), 3)

    def test_wrong_delivery_id_or_locator_is_refused(self):
        self.add_delivery()
        self.assertIsNone(VERIFY_DELIVERY(self, self.proxy, self.master, OTHER_DELIVERY))
        self.master.text = self.master.text.replace(f"productions/{PRODUCTION}", "library")
        self.assertIsNone(VERIFY_DELIVERY(self, self.proxy, self.master, DELIVERY))
        self.assertEqual(self.verdicts.getvalue().count("FAIL delivery ID and role-specific locators"), 2)

    def test_unconfirmed_read_only_mode_refuses_before_the_select(self):
        self.transaction_is_read_only = False
        self.assertIsNone(VERIFY_DELIVERY(self, self.proxy, self.master, DELIVERY))
        self.assertEqual(self.verdicts.getvalue(), "FAIL delivery transaction is read-only\n")


class ReleaseGuardTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("studio_release_check", Path(__file__).with_name("check.py"))
        self.guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.guard)
        self.studio = STAGE.parent
        self.flux = list(yaml.safe_load_all((self.studio / "ks.yaml").read_text()))

    def configured_image(self, image):
        real_load, real_render = yaml.safe_load, self.guard.render

        def pin(value):
            parsed = real_load(value)
            parsed["data"]["image"] = image
            return parsed

        def render(path):
            docs = real_render(path)
            for doc in docs:
                if doc["kind"] == "Job":
                    pod = doc["spec"]["template"]["spec"]
                    for init in pod.get("initContainers", []):
                        if init["name"] == "reserve-output":
                            init["image"] = image
                    for c in pod["containers"]:
                        if c["name"] == "upscaling":
                            c["image"] = image
                            for entry in c["env"]:
                                if entry["name"] == "STUDIO_UPSCALING_IMAGE":
                                    entry["value"] = image
            return docs

        self.enterContext(patch.object(yaml, "safe_load", side_effect=pin))
        self.enterContext(patch.object(self.guard, "render", side_effect=render))

    def test_placeholder_is_refused_without_registry_lookup(self):
        self.configured_image(f"{self.guard.UPSCALING_IMAGE}@sha256:" + "0" * 64)
        with patch.object(self.guard, "published_labels") as fetch:
            with self.assertRaisesRegex(ValueError, "published Studio main digest"):
                self.guard.check_upscaling(self.studio, self.flux)
            fetch.assert_not_called()

    def test_comparison_job_refuses_unsafe_identities_and_mounts(self):
        pin = yaml.safe_load((STAGE / "release/release.yaml").read_text())["data"]
        policies = self.guard.render(STAGE / "policy")
        actually_render = self.guard.render

        def verify():
            self.guard.check_upscaling_jobs(
                STAGE, pin["image"], pin["allocation"], (pin["memoryRequest"], pin["memoryLimit"]), policies,
            )

        def rewritten_comparison(path, keys, value):
            docs = actually_render(path)
            if path == STAGE / "compare":
                parent = docs[0]["spec"]
                for key in keys[:-1]:
                    parent = parent[key]
                parent[keys[-1]] = value
            return docs

        verify()
        pod = ("template", "spec")
        main = (*pod, "containers", 0)
        init = (*pod, "initContainers", 0)
        mutations = (
            ("TTL", ("ttlSecondsAfterFinished",), 86400, "Job envelope"),
            ("label", ("template", "metadata", "labels"), {"app.kubernetes.io/name": "studio-upscaling-smoke"},
             "Job envelope"),
            ("main UID", (*pod, "securityContext", "runAsUser"), 1000, "Job identity"),
            ("main GID override", (*main, "securityContext", "runAsGroup"), 1000, "UID/GID 10001"),
            ("supplemental group", (*pod, "securityContext", "supplementalGroups"), [], "supplemental group"),
            ("init UID", (*init, "securityContext", "runAsUser"), 10001, "UID/GID 1000 init"),
            ("init GID", (*init, "securityContext", "runAsGroup"), 10001, "UID/GID 1000 init"),
            ("init command", (*init, "command"), ["studio-upscaling", "compare"], "UID/GID 1000 init"),
            ("missing selector", (*main, "args"), ["--proxy", "proxy-locator-required"], "arguments"),
            ("misnamed selector", (*main, "args", 2), "--frame", "arguments"),
            ("wrong input kind", (*main, "args", 0), "--input", "arguments"),
            ("writable weights mount", (*main, "volumeMounts", 4, "readOnly"), False, "mount paths"),
            ("writable input mount", (*main, "volumeMounts", 1, "readOnly"), False, "mount paths"),
            ("writable master mount", (*main, "volumeMounts", 2, "readOnly"), False, "mount paths"),
            ("writable weights NFS", (*pod, "volumes", 1, "nfs", "readOnly"), False, "NFS export"),
            ("writable input NFS", (*pod, "volumes", 2, "nfs", "readOnly"), False, "NFS export"),
            ("writable master NFS", (*pod, "volumes", 3, "nfs", "readOnly"), False, "NFS export"),
            ("whole jobs tree", (*main, "volumeMounts", 3, "subPath"), "data/studio/jobs", "mount paths"),
        )
        for name, keys, value, error in mutations:
            with self.subTest(name=name), patch.object(
                self.guard, "render", side_effect=lambda path: rewritten_comparison(path, keys, value),
            ):
                with self.assertRaisesRegex(ValueError, error):
                    verify()

    def test_comparison_renders_each_input_form_and_mounts_only_used_shards(self):
        template = self.guard.resource(self.guard.render(STAGE / "compare"), "Job")
        source_shard = PROXY.rsplit("/", 1)[0]
        master_shard = MASTER.rsplit("/", 1)[0]
        still = PROXY.removesuffix(".mp4") + ".jpg"
        same_shard = f"{source_shard}/aa" + "b" * 62 + ".mp4"
        for source, options, expected, other_shard in (
            (still, {"plate": True}, ["--input", "a" * 64 + ".jpg", "--input-sha256", "a" * 64, "--plate"], None),
            (PROXY, {"index": "1"}, ["--proxy", PROXY, "--frame-index", "1"], None),
            (PROXY, {"timestamp": "0.125", "master": MASTER, "delivery_id": DELIVERY},
             ["--proxy", PROXY, "--timestamp", "0.125", "--master", MASTER, "--delivery-id", DELIVERY],
             master_shard),
            (PROXY, {"index": "1", "master": same_shard, "delivery_id": DELIVERY},
             ["--proxy", PROXY, "--frame-index", "1", "--master", same_shard, "--delivery-id", DELIVERY], None),
        ):
            with self.subTest(expected=expected):
                job = COMPARE_JOB(template, source=source, run="000000000001", **options)
                self.assertEqual(job["metadata"]["name"], "studio-upscaling-compare-000000000001")
                self.assertEqual(job["spec"]["ttlSecondsAfterFinished"], 3600)
                pod = job["spec"]["template"]["spec"]
                self.assertEqual(pod["initContainers"][0]["args"][-1], COMPARE_ALLOCATION)
                self.assertEqual(self.guard.container(job)["args"], [*expected, "--orientation", "portrait"])
                mounts = {mount["name"]: mount for mount in self.guard.container(job)["volumeMounts"]}
                self.assertEqual(mounts["jobs"]["subPath"], f"data/studio/jobs/{COMPARE_ALLOCATION}")
                self.assertEqual(mounts["source"]["mountPath"], "/input" if source == still else f"/store/{source_shard}")
                self.assertEqual(mounts["source"]["subPath"], f"data/studio/{source_shard}")
                self.assertTrue(mounts["source"]["readOnly"])
                self.assertEqual({v["name"] for v in pod["volumes"]}, set(mounts))
                if other_shard:
                    self.assertEqual(mounts["master"], {"name": "master", "mountPath": f"/store/{other_shard}",
                                                        "subPath": f"data/studio/{other_shard}", "readOnly": True})
                else:
                    self.assertNotIn("master", mounts)

    def test_probe_uses_comparison_inputs_and_only_read_only_shards(self):
        template = self.guard.resource(self.guard.render(STAGE / "compare"), "Job")
        options = {"source": PROXY, "run": "000000000001", "timestamp": "0.125",
                   "master": MASTER, "delivery_id": DELIVERY}
        compare = COMPARE_JOB(template, **options)
        probe = COMPARE_JOB(template, **options, probe=True)
        self.assertEqual(probe["metadata"]["name"], "studio-upscaling-probe-000000000001")
        self.assertEqual(probe["spec"]["activeDeadlineSeconds"], 90)
        self.assertEqual(probe["spec"]["backoffLimit"], 0)
        self.assertEqual(probe["spec"]["ttlSecondsAfterFinished"], 3600)
        self.assertEqual(probe["spec"]["template"]["metadata"], compare["spec"]["template"]["metadata"])
        pod = probe["spec"]["template"]["spec"]
        self.assertNotIn("initContainers", pod)
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["securityContext"], compare["spec"]["template"]["spec"]["securityContext"])
        container = self.guard.container(probe)
        comparison = self.guard.container(compare)
        self.assertEqual(container["command"], comparison["command"])
        self.assertEqual(container["image"], comparison["image"])
        self.assertEqual(container["args"], [*comparison["args"], "--probe"])
        self.assertEqual(container["resources"]["limits"], {"cpu": "1", "memory": "256Mi"})
        self.assertEqual(container["volumeMounts"], [
            mount for mount in comparison["volumeMounts"] if mount["name"] in {"source", "master"}
        ])
        self.assertEqual({v["name"] for v in pod["volumes"]}, {"source", "master"})
        self.assertTrue(all(mount["readOnly"] for mount in container["volumeMounts"]))
        self.assertTrue(all(v["nfs"]["readOnly"] for v in pod["volumes"]))

    def test_comparison_renderer_refuses_bad_arguments(self):
        for options in (
            {}, {"index": "-1"}, {"timestamp": "NaN"}, {"timestamp": "0", "index": "0"},
            {"index": "0", "plate": True}, {"index": "0", "master": "../private.mp4", "delivery_id": DELIVERY},
            {"index": "0", "master": MASTER.replace(".mp4", ".jpg"), "delivery_id": DELIVERY},
            {"index": "0", "master": MASTER},
            {"index": "0", "master": MASTER, "delivery_id": "not-a-delivery-id"},
            {"index": "0", "delivery_id": DELIVERY},
            {"index": "0", "source": PROXY.replace("/aa/", "/bb/")},
            {"index": "0", "source": PROXY.replace(".mp4", ".jpg")},
            {"probe": True, "source": PROXY.replace(".mp4", ".jpg")},
        ):
            args = {"source": PROXY, "run": "000000000001", **options}
            with self.subTest(options=options), self.assertRaises(ValueError):
                COMPARE_JOB({}, **args)

    def test_measured_contract_and_cilium_labels(self):
        self.configured_image(f"{self.guard.UPSCALING_IMAGE}@{DIGEST}")
        published = {
            "org.opencontainers.image.revision": "1" * 40,
            self.guard.UPSCALING_COMPARISON_LABEL: "stored-proxy-frame-v2",
            "io.haynes-studio.upscaling.resources": json.dumps({
                "schema": "studio.upscaling-resources/v1",
                "measuring": {"request_gib": 2, "limit_gib": 8},
                "quality_peak_gib": 2,
            }),
        }
        with patch.object(self.guard, "published_labels", return_value=published) as fetch:
            self.guard.check_upscaling(self.studio, self.flux)
            fetch.assert_called_once_with(self.guard.UPSCALING_IMAGE, DIGEST)
            del published[self.guard.UPSCALING_COMPARISON_LABEL]
            with self.assertRaisesRegex(ValueError, "support the stored-proxy frame comparison"):
                self.guard.check_upscaling(self.studio, self.flux)
            published[self.guard.UPSCALING_COMPARISON_LABEL] = "stored-proxy-frame-v1"
            with self.assertRaisesRegex(ValueError, "support the stored-proxy frame comparison and probe"):
                self.guard.check_upscaling(self.studio, self.flux)
            published[self.guard.UPSCALING_COMPARISON_LABEL] = "stored-proxy-frame-v2"
            published["io.haynes-studio.upscaling.resources"] = "{}"
            with self.assertRaisesRegex(ValueError, "resource contract failed"):
                self.guard.check_upscaling(self.studio, self.flux)

        document = {
            "kind": "CiliumNetworkPolicy", "metadata": {"name": "test"},
            "spec": {"endpointSelector": {}, "egress": [
                {"toEndpoints": [{"matchLabels": {"k8s:statefulset.kubernetes.io/pod-name": "comfyui-0"}}]},
            ]},
        }
        with self.assertRaisesRegex(ValueError, "leaves out of pod identities"):
            self.guard.check_policy_selectors([document])


if __name__ == "__main__":
    unittest.main()
