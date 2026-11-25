"""Verify deployment stages with a stub registry and no downloaded model."""

import importlib.util
import json
from hashlib import sha1, sha256
from io import BytesIO, StringIO
from pathlib import Path
import os
import runpy
import sqlite3
import sys
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from copy import deepcopy
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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
        SCRIPT["capacity"](self.root, 0)
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

    def test_byte_budget_and_space_reserve(self):
        constants = PROVISION.__globals__
        capacity = constants["capacity"]
        budget, reserve = 8 * 1024**3, 32 * 1024**3
        self.free.f_frsize = 1
        self.free.f_bavail = reserve
        capacity(self.root, budget)
        for written, available, error in (
            (budget + 1, reserve, "byte_budget_exceeded"),
            (0, reserve + budget - 1, "free_reserve_too_small"),
            (budget, reserve - 1, "free_reserve_too_small"),
        ):
            with self.subTest(written=written, available=available):
                self.free.f_bavail = available
                with self.assertRaisesRegex(SCRIPT["ProvisionError"], error):
                    capacity(self.root, written)

        # The cap includes the notice, manifest and its digest before any writes.
        with patch.dict(constants, BUDGET=len(GRAPH)), self.assertRaisesRegex(
            SCRIPT["ProvisionError"], "byte_budget_exceeded",
        ):
            PROVISION(self.root, ALLOCATION, self.opener, b"{}", NOTICE)
        self.assertFalse((self.root / ALLOCATION).exists())

    def test_response_constraints_and_download_deadline(self):
        constants = PROVISION.__globals__
        for name, status, headers, body, deadline, error, written in (
            ("partial status", 206, {}, GRAPH, 1, "unexpected_download_length", None),
            ("encoded response", 200, {"Content-Encoding": "gzip"}, GRAPH, 1, "unexpected_download_length", None),
            ("wrong declared length", 200, {"Content-Length": "1"}, GRAPH, 1, "unexpected_download_length", None),
            ("oversized body", 200, {}, GRAPH + b"x", 1, "unexpected_download_length", b""),
            ("truncated body", 200, {}, GRAPH[:-1], 1, "unexpected_download_length", GRAPH[:-1]),
            ("expired deadline", 200, {}, GRAPH, 0, "download_deadline", b""),
        ):
            with self.subTest(response=name), patch.dict(constants, monotonic=lambda: 0):
                response = Response(body)
                response.status, response.headers = status, headers
                opener = SimpleNamespace(open=lambda *_args, **_kwargs: response)
                target = self.root / name
                with self.assertRaisesRegex(SCRIPT["ProvisionError"], error):
                    constants["download"](opener, target, deadline)
                if written is None:
                    self.assertFalse(target.exists())
                else:
                    self.assertEqual(target.read_bytes(), written)
        with self.assertRaisesRegex(SCRIPT["ProvisionError"], "unexpected_download_redirect"):
            SCRIPT["PinnedRedirect"]().redirect_request(None, None, 302, "", {}, "https://example.invalid/graph")

    def test_invalid_allocation_and_existing_weights_are_refused(self):
        with self.assertRaisesRegex(SCRIPT["ProvisionError"], "invalid_allocation"):
            PROVISION(self.root, "invalid", self.opener, b"{}", NOTICE)
        self.assertFalse(list(self.root.iterdir()))

        constants = PROVISION.__globals__
        download = constants["download"]
        weights = self.root / ALLOCATION / "weights"
        identities = []

        def download_with_existing_destination(*args):
            download(*args)
            weights.mkdir()
            identities.append(weights.stat().st_ino)

        with patch.dict(constants, download=download_with_existing_destination):
            with self.assertRaisesRegex(SCRIPT["ProvisionError"], "weights_already_exist"):
                PROVISION(self.root, ALLOCATION, self.opener, b"{}", NOTICE)
        self.assertEqual(weights.stat().st_ino, identities[0])
        self.assertTrue((weights.parent / ".quarantine").is_dir())

    def test_provision_cli_checks_writer_identity_and_image_graph(self):
        constants = PROVISION.__globals__
        profile = ModuleType("haynes_studio.upscaling.profile")
        graph = {"graph_bytes": len(GRAPH), "graph_sha256": sha256(GRAPH).hexdigest(), "graph_blob": self.blob}
        profile.UpscaleError = ValueError
        main = SCRIPT["main"]
        for uid, gid, changes, error in (
            (10001, 1000, {}, "wrong_writer_identity"),
            (1000, 10001, {}, "wrong_writer_identity"),
            (1000, 1000, {"graph_bytes": 1}, "image_graph_pin_mismatch"),
            (1000, 1000, {"graph_sha256": "0" * 64}, "image_graph_pin_mismatch"),
            (1000, 1000, {"graph_blob": "0" * 40}, "image_graph_pin_mismatch"),
        ):
            profile.Profile = SimpleNamespace(installed=lambda: SimpleNamespace(**{**graph, **changes}))
            report = StringIO()
            with (
                self.subTest(uid=uid, gid=gid, changes=changes),
                patch.dict(sys.modules, {profile.__name__: profile}),
                patch("os.getuid", return_value=uid), patch("os.getgid", return_value=gid),
                patch.dict(constants, provision=Mock(side_effect=AssertionError("unexpected write"))),
                redirect_stderr(report),
            ):
                self.assertEqual(main(), 1)
                self.assertEqual(json.loads(report.getvalue())["code"], error)

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
            self.assertEqual(main([COMPARE_ALLOCATION, "unexpected"]), 1)
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

    def test_refuses_symlink_swapped_in_after_mkdir(self):
        main = runpy.run_path(str(STAGE / "compare/reserve.py"))["main"]
        root = Path(self.enterContext(TemporaryDirectory()))
        existing = root / "existing"
        existing.mkdir(mode=0o700)
        original_mode = existing.stat().st_mode
        target = root / COMPARE_ALLOCATION
        mkdir = Path.mkdir

        def replace_reserved_directory(path, *args, **kwargs):
            self.assertEqual(path, target)
            mkdir(path, *args, **kwargs)
            path.rmdir()
            path.symlink_to(existing, target_is_directory=True)

        with (patch.dict(main.__globals__, JOBS=root),
              patch.object(Path, "mkdir", replace_reserved_directory),
              patch("sys.stdout", new_callable=StringIO) as report):
            self.assertEqual(main([COMPARE_ALLOCATION]), 1)
            self.assertEqual(json.loads(report.getvalue())["code"], "comparison.output_unavailable")
        self.assertTrue(target.is_symlink())
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


    def test_cli_refuses_invalid_inputs_and_propagates_lookup_failure(self):
        main = runpy.run_path(str(STAGE / "compare/verify_delivery.py"))["main"]
        ids = ModuleType("haynes_studio.canonical.ids")
        ids.is_id = lambda value, prefix: prefix == "dlv" and value == DELIVERY
        db = ModuleType("haynes_studio.db.engine")
        db.connect_engine = Mock(return_value=None)
        db.snapshot = lambda _: nullcontext(self)
        store = ModuleType("haynes_studio.store")
        store.StoreError = ValueError
        store.parse_locator = lambda path: SimpleNamespace(ext=path.rsplit(".", 1)[1])

        modules = {module.__name__: module for module in (ids, db, store)}
        for proxy, master, delivery, lookup in (
            (PROXY.replace(".mp4", ".jpg"), MASTER, DELIVERY, False),
            (PROXY, MASTER.replace(".mp4", ".jpg"), DELIVERY, False),
            (PROXY, MASTER, "invalid", False),
            (PROXY, MASTER, DELIVERY, True),
        ):
            with (
                self.subTest(proxy=proxy, master=master, delivery=delivery),
                patch.dict(sys.modules, modules),
                patch.dict(os.environ, STUDIO_DATABASE_URL="fixture"),
                patch.object(sys, "argv", ["verify_delivery.py", proxy, master, delivery]),
                patch.dict(main.__globals__, verified_delivery=Mock(return_value=None)),
                redirect_stdout(StringIO()) as output,
            ):
                self.assertEqual(main(), 1)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(main.__globals__["verified_delivery"].call_count, int(lookup))


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

    def test_missing_upscaling_stage_requires_no_registry_lookup(self):
        with TemporaryDirectory() as directory, patch.object(self.guard, "published_labels") as fetch:
            self.guard.check_upscaling(Path(directory), [])
            fetch.assert_not_called()

    def test_placeholder_is_refused_without_registry_lookup(self):
        self.configured_image(f"{self.guard.UPSCALING_IMAGE}@sha256:" + "0" * 64)
        with patch.object(self.guard, "published_labels") as fetch:
            with self.assertRaisesRegex(ValueError, "published Studio main digest"):
                self.guard.check_upscaling(self.studio, self.flux)
            fetch.assert_not_called()

    def test_malformed_image_reference_is_refused_before_registry_lookup(self):
        self.configured_image(f"{self.guard.UPSCALING_IMAGE}:latest")
        with patch.object(self.guard, "published_labels") as fetch:
            with self.assertRaisesRegex(ValueError, "published Studio main digest"):
                self.guard.check_upscaling(self.studio, self.flux)
            fetch.assert_not_called()

    def test_flux_policy_path_and_prune_cannot_drift(self):
        for key, value in (("path", f"./{STAGE}/smoke"), ("prune", False)):
            flux = deepcopy(self.flux)
            stage = self.guard.resource(flux, "Kustomization", "studio-upscaling-policy")
            stage["spec"][key] = value
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, "only the upscaling policy stage"):
                self.guard.check_upscaling(self.studio, flux)

    def test_flux_policy_stage_rejects_extra_work_and_changed_egress(self):
        render = self.guard.render
        policies = render(STAGE / "policy")
        for role in (None, "provision", "smoke", "compare"):
            docs = deepcopy(policies)
            if role is None:
                docs.append({"kind": "Job", "metadata": {"name": "unexpected"}})
                error = "only targeted policies and the provisioner script"
            else:
                name = f"studio-upscaling-{role}"
                policy = self.guard.resource(docs, "CiliumNetworkPolicy", name)
                policy["spec"]["egress"] = [{"toEntities": ["world"]}]
                error = f"unexpected traffic or identity labels for {name}"
            with (
                self.subTest(role=role),
                patch.object(
                    self.guard, "render", side_effect=lambda path: docs if path == STAGE / "policy" else render(path),
                ),
                self.assertRaisesRegex(ValueError, error),
            ):
                self.guard.check_upscaling(self.studio, self.flux)

    def test_registry_config_must_identify_the_studio_linux_amd64_image(self):
        labels = {"org.opencontainers.image.source": self.guard.STUDIO_REPO}
        config = {"os": "linux", "architecture": "amd64", "config": {"Labels": labels}}
        for changes in (
            {"os": "windows"},
            {"architecture": "arm64"},
            {"config": {"Labels": {"org.opencontainers.image.source": "other"}}},
        ):
            with (
                self.subTest(changes=changes),
                patch.object(self.guard, "published_config", return_value={**config, **changes}),
                self.assertRaisesRegex(ValueError, "expected a Linux amd64 Studio image"),
            ):
                self.guard.published_labels(self.guard.UPSCALING_IMAGE, DIGEST)

    def assert_job_mutations_refused(self, role, mutations):
        pin = yaml.safe_load((STAGE / "release/release.yaml").read_text())["data"]
        policies = self.guard.render(STAGE / "policy")
        stages = {STAGE / stage: self.guard.render(STAGE / stage) for stage in ("provision", "smoke", "compare")}

        def verify():
            self.guard.check_upscaling_jobs(
                STAGE, pin["image"], pin["allocation"], (pin["memoryRequest"], pin["memoryLimit"]), policies,
            )

        verify()
        for name, keys, value, error in mutations:
            fixtures = deepcopy(stages)
            job = self.guard.resource(fixtures[STAGE / role], "Job")
            parent = job
            for key in keys[:-1]:
                parent = parent[key]
            parent[keys[-1]] = value
            with self.subTest(role=role, mutation=name), patch.object(
                self.guard, "render", side_effect=fixtures.__getitem__,
            ):
                with self.assertRaisesRegex(ValueError, error):
                    verify()

    def test_comparison_job_refuses_unsafe_identities_and_mounts(self):
        pod = ("spec", "template", "spec")
        main = (*pod, "containers", 0)
        init = (*pod, "initContainers", 0)
        self.assert_job_mutations_refused("compare", (
            ("TTL", ("spec", "ttlSecondsAfterFinished"), 86400, "Job envelope"),
            ("label", ("spec", "template", "metadata", "labels"), {"app.kubernetes.io/name": "studio-upscaling-smoke"},
             "Job envelope"),
            ("main UID", (*pod, "securityContext", "runAsUser"), 1000, "Job identity"),
            ("main GID override", (*main, "securityContext", "runAsGroup"), 1000, "UID/GID 10001"),
            ("duplicate mount", (*main, "volumeMounts", slice(0, 0)),
             [{"name": "scratch", "mountPath": "/unexpected"}], "declared volumes and mounts"),
            ("duplicate volume", (*pod, "volumes", slice(0, 0)),
             [{"name": "scratch", "emptyDir": {}}], "declared volumes and mounts"),
            ("supplemental group", (*pod, "securityContext", "supplementalGroups"), [], "supplemental group"),
            ("init UID", (*init, "securityContext", "runAsUser"), 10001, "UID/GID 1000 init"),
            ("init GID", (*init, "securityContext", "runAsGroup"), 10001, "UID/GID 1000 init"),
            ("init command", (*init, "command"), ["studio-upscaling", "compare"], "UID/GID 1000 init"),
            ("missing selector", (*main, "args"), ["--proxy", "proxy-locator-required"], "arguments"),
            ("misnamed selector", (*main, "args", 2), "--frame", "arguments"),
            ("wrong input kind", (*main, "args", 0), "--input", "arguments"),
            ("unexpected volume", (*pod, "volumes", slice(0, 0)),
             [{"name": "extra", "emptyDir": {}}], "declared volumes and mounts"),
            ("writable weights mount", (*main, "volumeMounts", 4, "readOnly"), False, "mount paths"),
            ("writable input mount", (*main, "volumeMounts", 1, "readOnly"), False, "mount paths"),
            ("writable master mount", (*main, "volumeMounts", 2, "readOnly"), False, "mount paths"),
            ("writable weights NFS", (*pod, "volumes", 1, "nfs", "readOnly"), False, "NFS export"),
            ("writable input NFS", (*pod, "volumes", 2, "nfs", "readOnly"), False, "NFS export"),
            ("writable master NFS", (*pod, "volumes", 3, "nfs", "readOnly"), False, "NFS export"),
            ("whole jobs tree", (*main, "volumeMounts", 3, "subPath"), "data/studio/jobs", "mount paths"),
        ))

    def test_comparison_stage_must_contain_only_its_job(self):
        pin = yaml.safe_load((STAGE / "release/release.yaml").read_text())["data"]
        render = self.guard.render
        policies = render(STAGE / "policy")

        def extra_document(path):
            documents = render(path)
            if path == STAGE / "compare":
                documents.append({"kind": "ConfigMap", "metadata": {"name": "unexpected"}})
            return documents

        with (
            patch.object(self.guard, "render", side_effect=extra_document),
            self.assertRaisesRegex(ValueError, "comparison requires one Job"),
        ):
            self.guard.check_upscaling_jobs(
                STAGE, pin["image"], pin["allocation"], (pin["memoryRequest"], pin["memoryLimit"]), policies,
            )

    def test_smoke_job_refuses_unsafe_envelope_and_container(self):
        pod = ("spec", "template", "spec")
        main = (*pod, "containers", 0)
        security = (*main, "securityContext")
        nfs = (*pod, "volumes", 1, "nfs")
        envelope = "Job envelope"
        container = "release image and named CPU command"
        sandbox = "unprivileged with read-only roots"
        limits = "measuring memory and two-CPU ceiling"
        self.assert_job_mutations_refused("smoke", (
            ("image", (*main, "image"), f"{self.guard.UPSCALING_IMAGE}@{DIGEST}", container),
            ("image environment", (*main, "env", 0, "value"), "wrong-pin", container),
            ("CPU command", (*main, "command"), ["studio-upscaling", "serve"], container),
            ("credential environment", (*main, "env", slice(0, 0)),
             [{"name": "CREDENTIAL", "valueFrom": {"secretKeyRef": {"name": "fixture", "key": "value"}}}], container),
            ("imported environment", (*main, "envFrom"), [{"secretRef": {"name": "fixture"}}], container),
            ("port", (*main, "ports"), [{"containerPort": 8080}], container),
            ("device", (*main, "volumeDevices"), [{"name": "weights", "devicePath": "/dev/fixture"}], container),
            ("GPU", (*main, "resources", "limits", "nvidia.com/gpu"), "1", container),
            ("privileged", (*security, "privileged"), True, sandbox),
            ("privilege escalation", (*security, "allowPrivilegeEscalation"), True, sandbox),
            ("writable root", (*security, "readOnlyRootFilesystem"), False, sandbox),
            ("capabilities", (*security, "capabilities"), {"drop": []}, sandbox),
            ("NFS host", (*nfs, "server"), "other.invalid", "NFS export"),
            ("NFS export", (*nfs, "path"), "/other", "NFS export"),
            ("scratch size", (*pod, "volumes", 0, "emptyDir", "sizeLimit"), "1Gi", "256Mi emptyDir"),
            ("unexpected volume", (*pod, "volumes", slice(0, 0)),
             [{"name": "extra", "emptyDir": {}}], "arguments or volumes"),
            ("namespace", ("metadata", "namespace"), "default", envelope),
            ("deadline", ("spec", "activeDeadlineSeconds"), 600, envelope),
            ("backoff", ("spec", "backoffLimit"), 1, envelope),
            ("restart policy", (*pod, "restartPolicy"), "OnFailure", envelope),
            ("service account token", (*pod, "automountServiceAccountToken"), True, envelope),
            ("service links", (*pod, "enableServiceLinks"), True, envelope),
            ("service account", (*pod, "serviceAccountName"), "fixture", envelope),
            ("unexpected init container", (*pod, "initContainers"), [{"name": "unexpected"}], envelope),
            ("host networking", (*pod, "hostNetwork"), True, envelope),
            ("host PID", (*pod, "hostPID"), True, envelope),
            ("host IPC", (*pod, "hostIPC"), True, envelope),
            ("node", (*pod, "nodeSelector", "kubernetes.io/hostname"), "other-node", envelope),
            ("priority", (*pod, "priorityClassName"), "system-cluster-critical", envelope),
            ("root allowed", (*pod, "securityContext", "runAsNonRoot"), False, "Job identity"),
            ("seccomp", (*pod, "securityContext", "seccompProfile", "type"), "Unconfined", "Job identity"),
            ("CPU limit", (*main, "resources", "limits", "cpu"), "3", limits),
            ("memory limit", (*main, "resources", "limits", "memory"), "16Gi", limits),
            ("memory request", (*main, "resources", "requests", "memory"), "1Gi", limits),
        ))

    def test_provision_job_refuses_wrong_script_allocation_and_bounds(self):
        pod = ("spec", "template", "spec")
        main = (*pod, "containers", 0)
        error = "Flux script and the 1Gi write bound"
        self.assert_job_mutations_refused("provision", (
            ("script ConfigMap", (*pod, "volumes", 1, "configMap", "name"), "wrong-script", error),
            ("allocation environment", (*main, "env", 1, "value"), "wrong-allocation", error),
            ("CPU limit", (*main, "resources", "limits", "cpu"), "2", error),
            ("memory limit", (*main, "resources", "limits", "memory"), "2Gi", error),
        ))

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
        template = self.guard.resource(self.guard.render(STAGE / "compare"), "Job")
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
            {"index": "0", "run": "../invalid"},
        ):
            args = {"source": PROXY, "run": "000000000001", **options}
            with self.subTest(options=options), self.assertRaises(ValueError):
                COMPARE_JOB(template, **args)

    def test_measured_image_contract(self):
        self.configured_image(f"{self.guard.UPSCALING_IMAGE}@{DIGEST}")
        resources = {
            "schema": "studio.upscaling-resources/v1",
            "measuring": {"request_gib": 2, "limit_gib": 8},
            "quality_peak_gib": 2,
        }
        resource_label = "io.haynes-studio.upscaling.resources"
        comparison_label = self.guard.UPSCALING_COMPARISON_LABEL
        revision_label = "org.opencontainers.image.revision"
        published = {
            revision_label: "1" * 40,
            comparison_label: "stored-proxy-frame-v2",
            resource_label: json.dumps(resources),
        }
        with patch.object(self.guard, "published_labels", return_value=published) as fetch:
            self.guard.check_upscaling(self.studio, self.flux)
            fetch.assert_called_once_with(self.guard.UPSCALING_IMAGE, DIGEST)
        for changes, error in (
            ({comparison_label: None}, "support the stored-proxy frame comparison"),
            ({comparison_label: "stored-proxy-frame-v1"}, "support the stored-proxy frame comparison and probe"),
            ({revision_label: "invalid"}, "invalid upscaling profile resource contract"),
            *(
                ({resource_label: json.dumps({**resources, **override})}, "resource contract failed")
                for override in (
                    {"schema": "unknown"},
                    {"quality_peak_gib": True},
                    {"quality_peak_gib": 0},
                    {"measuring": {"request_gib": 2, "limit_gib": 9}},
                )
            ),
        ):
            with (
                self.subTest(changes=changes),
                patch.object(self.guard, "published_labels", return_value={**published, **changes}),
                self.assertRaisesRegex(ValueError, error),
            ):
                self.guard.check_upscaling(self.studio, self.flux)

    def test_cilium_rejects_excluded_identity_labels(self):
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
