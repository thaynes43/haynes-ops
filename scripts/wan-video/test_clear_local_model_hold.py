"""CPU-only refusal and archive checks for the operator recovery command."""
from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_local_model as fixtures

import clear_local_model_hold as recovery

api = recovery.api
local = api.local
CARD, PROFILE = fixtures.CARD, fixtures.PROFILE
SAMPLE = {"gpu_uuid": CARD, "memory_mib": 64, "utilization_percent": 0, "temperature_c": 50}
ENGINE = f"{CARD}, 11, /opt/ComfyUI/python, 30\n"
INSTANCE = "ab" * 16


class HoldRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.lock_path = self.root / "compute.lock"
        self.lock_path.touch()
        config = self.root / "config"
        config.mkdir()
        self.profile = config / "local-model.json"
        self.profile.write_text(json.dumps(PROFILE))
        self.path = local.state_path(self.root)
        self.lane = local.Lane(self.root, PROFILE)
        self.lane.write_state(INSTANCE, True, pending=fixtures.LOAD, baseline=64, hold=True)
        self.boot = self.root / "boot_id"
        self.boot.write_text("this-boot")
        self.evidence = {"profile": PROFILE, "calibration": {
            "gpu_uuid": CARD, "samples": [dict(SAMPLE, memory_mib=128)] * 3,
            "compute_apps_csv": ENGINE, "boot_id": "this-boot",
        }}
        self.wan = {"api": "ok", "active_job": None, "accepting_jobs": True}
        self.audio = {"api_version": fixtures.product.VERSION, "active_job": None}
        self.foley = self.fake(api, "foley", return_value=(200, self.audio))
        self.queue = self.fake(api, "engine", return_value=fixtures.EMPTY)
        self.fake(api, "ROOT", self.root)
        self.fake(api, "CONFIG", config)
        self.fake(recovery, "urlopen", side_effect=lambda *a, **kw: nullcontext(io.StringIO(json.dumps(self.wan))))
        self.fake(recovery, "BOOT_ID", self.boot)
        self.fake(recovery.time, "sleep")
        sidecar = fixtures.sidecar.Sidecar(self.root, PROFILE, None, check_idle=None, evict_cache=None)
        self.resident = self.fake(sidecar.model, "alive", return_value=False)
        self.exchange = self.fake(local, "exchange", side_effect=lambda *a: sidecar.status(residency_only=True))
        self.sample = self.fake(local, "sample_card", return_value=SAMPLE)
        self.nvidia = self.fake(recovery.subprocess, "run",
                                return_value=subprocess.CompletedProcess([], 0, stdout=ENGINE))

    def fake(self, target, attribute, *args, **kwargs):
        mock = patch.object(target, attribute, *args, **kwargs)
        self.addCleanup(mock.stop)
        return mock.start()

    def assert_refused(self, reason):
        original = self.path.read_bytes() if self.path.is_file() else None
        with self.assertRaisesRegex((ValueError, RuntimeError, OSError), reason):
            recovery.clear(self.evidence)
        if original is not None:
            self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("state.*.json")), [])
        self.assertEqual(list(self.path.parent.glob("hold-clear.*.json")), [])

    def test_missing_compute_lock_is_not_created(self):
        self.lock_path.unlink()
        self.assert_refused("Expected the existing Wan compute lock")
        self.assertFalse(self.lock_path.exists())

    def test_busy_lease_refuses_before_inspection(self):
        with api.acquire_compute():
            self.assert_refused("lease is held")
        self.queue.assert_not_called()

    def test_queue_busy_refused(self):
        for key in ("queue_pending", "queue_running"):
            with self.subTest(key=key):
                self.queue.return_value = {**fixtures.EMPTY, key: [[0, "foreign"]]}
                self.assert_refused("already has a job")

    def test_queue_unknown_refused(self):
        self.queue.return_value = {}
        self.assert_refused("queue is malformed")
        self.queue.side_effect = TimeoutError
        self.assert_refused("queue is unavailable")

    def test_wan_activity_refused(self):
        self.wan["active_job"] = fixtures.LOAD
        self.assert_refused("Wan job is active")

    def test_wan_health_unknown_refused(self):
        self.wan.pop("active_job")
        self.assert_refused("Wan activity is unknown")
        recovery.urlopen.side_effect = TimeoutError
        self.assert_refused("Wan health is unavailable")

    def test_separate_wan_render_hold_refused(self):
        self.wan["accepting_jobs"] = False
        self.assert_refused("render hold")

    def test_foley_activity_refused(self):
        self.audio["active_job"] = fixtures.LOAD
        self.assert_refused("Foley job is active")

    def test_foley_health_unknown_refused(self):
        self.foley.return_value = (503, self.audio)
        self.assert_refused("Foley activity is unknown")
        self.foley.side_effect = TimeoutError
        self.assert_refused("Foley readiness is unavailable")

    def test_missing_or_nonregular_state_refused(self):
        self.path.unlink()
        self.assert_refused("No residency journal")
        self.path.symlink_to(self.profile)
        self.assert_refused("not a regular file")

    def test_invalid_or_unheld_mounted_state_refused(self):
        self.path.write_text(json.dumps({"resident": "bad"}))
        self.assert_refused("Invalid residency journal")
        self.lane.write_state(INSTANCE, True, baseline=64)
        self.assert_refused("no latched hold")

    def test_different_profile_refused(self):
        self.profile.write_text(json.dumps({**PROFILE, "max_output_tokens": 13}))
        self.assert_refused("Profile does not match")

    def test_ceiling_raise_matches_earlier_digest_and_preserves_load_baseline(self):
        corrected = {**PROFILE, "unloaded_max_mib": 132}
        self.evidence["profile"] = corrected
        for mounted in (True, False):
            with self.subTest(mounted=mounted):
                self.lane.write_state(INSTANCE, False, baseline=126, hold=True)
                if mounted:
                    self.profile.write_text(json.dumps(corrected))
                    reply = self.exchange.side_effect()
                    self.exchange.side_effect = None
                    self.exchange.return_value = {**reply, "profile_sha256": fixtures.product.digest(corrected)}
                else:
                    self.profile.unlink()
                self.sample.return_value = dict(SAMPLE, memory_mib=130)
                result = self.assert_archived(self.evidence)
                self.assertEqual(result["profile_sha256"], fixtures.product.digest(corrected))
                self.assertEqual(result["journal_profile_sha256"], self.lane.profile_hash)
                self.assertEqual(result["ceiling_correction"], {
                    "previous_unloaded_max_mib": 128, "unloaded_max_mib": 132,
                })
                self.assertEqual(result["baseline_memory_mib"], 126)
                self.assertEqual(result["calibration_ceiling_range_mib"], [128, 132])

    def test_ceiling_raise_refuses_non_ceiling_change_or_out_of_range_ceiling(self):
        self.profile.unlink()
        corrected = {**PROFILE, "unloaded_max_mib": 132}
        for previous, current, reason in (
            (PROFILE, {**corrected, "max_output_tokens": 13}, "does not match"),
            ({**PROFILE, "unloaded_max_mib": 127}, corrected, "does not match"),
            ({**PROFILE, "unloaded_max_mib": 131}, {**corrected, "unloaded_max_mib": 130}, "does not match"),
            (PROFILE, {**corrected, "unloaded_max_mib": 133}, "outside the recorded calibration range"),
        ):
            with self.subTest(previous=previous, current=current):
                local.Lane(self.root, previous).write_state(INSTANCE, False, baseline=64, hold=True)
                self.evidence["profile"] = current
                self.assert_refused(reason)

    def test_ceiling_raise_requires_original_calibration(self):
        self.profile.write_text(json.dumps({**PROFILE, "unloaded_max_mib": 132}))
        del self.evidence["calibration"]
        self.assert_refused("no calibration was supplied")

    def test_live_child_refused(self):
        self.resident.return_value = True
        self.assert_refused("live model child")

    def test_unavailable_or_wrong_sidecar_refused_with_profile(self):
        good = self.exchange.side_effect
        self.exchange.side_effect = lambda *a: {**good(*a), "instance": "bad"}
        self.assert_refused("sidecar state is unavailable")
        self.exchange.side_effect = ConnectionRefusedError
        self.assert_refused("sidecar state is unavailable")
        self.nvidia.assert_not_called()

    def test_no_baseline_without_reviewed_calibration_refused(self):
        self.lane.write_state(INSTANCE, False, hold=True)
        self.evidence.pop("calibration")
        self.assert_refused("calibration is required")

    def test_invalid_state_baseline_refused(self):
        self.lane.write_state(INSTANCE, False, baseline=True, hold=True)
        self.assert_refused("Invalid recorded memory baseline")

    def test_wrong_calibration_card_samples_or_ceiling_refused(self):
        self.profile.unlink()
        original = self.evidence["calibration"]
        for changes, reason in (
            ({"gpu_uuid": fixtures.OTHER}, "different GPU"),
            ({"samples": [SAMPLE]}, "Invalid calibration samples"),
            ({"samples": [dict(SAMPLE, memory_mib=128.1)] * 3}, "outside the recorded calibration range"),
            ({"samples": [dict(SAMPLE, memory_mib=123)] * 3}, "outside the recorded calibration range"),
        ):
            with self.subTest(reason=reason):
                self.evidence["calibration"] = {**original, **changes}
                self.assert_refused(reason)

    def test_calibration_range_rounds_up_and_caps_the_allowance(self):
        for memory, minimum, maximum in ((0, 1, 4), (123.2, 124, 128), (510.1, 511, 512)):
            calibration = {**self.evidence["calibration"], "samples": [dict(SAMPLE, memory_mib=memory)] * 3}
            for ceiling in range(minimum, maximum + 1):
                with self.subTest(memory=memory, ceiling=ceiling):
                    self.assertEqual(
                        recovery.calibration_baseline(calibration, {**PROFILE, "unloaded_max_mib": ceiling}),
                        (memory, minimum, maximum),
                    )

    def test_dormant_live_socket_refused(self):
        self.profile.unlink()
        path = local.socket_path(self.root)
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
            listener.bind(str(path))
            listener.listen()
            self.assert_refused("live listener")
        self.assertTrue(path.exists())
        self.assertEqual(list(path.parent.glob("sidecar.stale.*.sock")), [])
        self.nvidia.assert_not_called()

    def test_dormant_stale_socket_is_archived_under_lease_before_remaining_proof(self):
        self.profile.unlink()
        path = local.socket_path(self.root)
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
            connection.bind(str(path))
        entry = path.stat()
        original = self.nvidia.return_value

        def check_lease(*args, **kwargs):
            self.assertIsNone(api.acquire_compute())
            self.assertFalse(path.exists())
            return original

        self.nvidia.side_effect = check_lease
        result = self.assert_archived(self.evidence)
        archived = path.with_name(result["stale_socket_archive"])
        self.assertRegex(archived.name, r"^sidecar\.stale\.\d{8}T\d{6}\.\d{6}Z-[a-f0-9]{32}\.sock$")
        self.assertTrue(archived.is_socket())
        self.assertEqual(archived.stat().st_ino, entry.st_ino)

    def test_dormant_inconclusive_socket_probe_refused(self):
        self.profile.unlink()
        path = local.socket_path(self.root)
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
            connection.bind(str(path))
        with patch.object(socket.socket, "connect", side_effect=TimeoutError("Socket probe timed out")):
            self.assert_refused("Socket probe timed out")
        self.assertTrue(path.is_socket())
        self.assertEqual(list(path.parent.glob("sidecar.stale.*.sock")), [])
        self.nvidia.assert_not_called()

    def test_dormant_stale_socket_preserved_if_memory_proof_fails(self):
        self.profile.unlink()
        path = local.socket_path(self.root)
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
            connection.bind(str(path))
        self.sample.return_value = dict(SAMPLE, memory_mib=69)
        self.assert_refused("Card is busy or memory exceeds")
        self.assertFalse(path.exists())
        archived, = list(path.parent.glob("sidecar.stale.*.sock"))
        self.assertTrue(archived.is_socket())

    def test_dormant_socket_replaced_during_probe_refused(self):
        self.profile.unlink()
        path = local.socket_path(self.root)
        replacement = path.with_name("replacement.sock")
        for entry in (path, replacement):
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
                connection.bind(str(entry))
        replacement_inode = replacement.stat().st_ino

        def refuse(*args):
            replacement.rename(path)
            raise ConnectionRefusedError

        with patch.object(socket.socket, "connect", side_effect=refuse):
            self.assert_refused("socket changed during the probe")
        self.assertEqual(path.stat().st_ino, replacement_inode)
        self.nvidia.assert_not_called()

    def test_dormant_non_socket_or_dangling_link_refused(self):
        self.profile.unlink()
        path = local.socket_path(self.root)
        path.touch()
        self.assert_refused("is not a socket")
        self.assertTrue(path.is_file())
        path.unlink()
        path.symlink_to(self.root / "missing")
        self.assert_refused("is not a socket")
        self.assertTrue(path.is_symlink())
        self.nvidia.assert_not_called()

    def test_dormant_unreadable_socket_cannot_prove_absence(self):
        self.profile.unlink()
        socket_path = local.socket_path(self.root)
        lstat = Path.lstat

        def unreadable(path, *args, **kwargs):
            if path == socket_path:
                raise PermissionError("Sidecar socket path is unreadable")
            return lstat(path, *args, **kwargs)

        with patch.object(Path, "lstat", new=unreadable):
            self.assert_refused("Sidecar socket path is unreadable")
        self.nvidia.assert_not_called()

    def test_dormant_compute_query_failure_or_malformed_row_refused(self):
        self.profile.unlink()
        self.nvidia.return_value.stdout = "bad row"
        self.assert_refused("Malformed GPU compute-process")
        self.nvidia.side_effect = subprocess.CalledProcessError(1, "nvidia-smi")
        self.assert_refused("process query failed")

    def test_dormant_unresolvable_process_name_refused_even_if_baseline_matches(self):
        self.profile.unlink()
        for name in ("", "[Not Found]", "[Insufficient Permissions]", "N/A", "[Unknown]"):
            with self.subTest(name=name):
                # Even a matching row in the original calibration is no proof.
                raw = f"{CARD}, 11, {name}, 30"
                self.evidence["calibration"]["compute_apps_csv"] = raw
                self.nvidia.return_value.stdout = raw
                self.assert_refused("Unverifiable GPU compute-process")

    def test_dormant_empty_process_list_still_requires_memory_proof(self):
        self.profile.unlink()
        self.nvidia.return_value.stdout = ""
        self.sample.return_value = dict(SAMPLE, memory_mib=69)
        self.assert_refused("Card is busy or memory exceeds")
        self.sample.return_value = dict(SAMPLE, memory_mib=68)
        result = self.assert_archived(self.evidence)
        self.assertIn("empty process list proves nothing", result["process_visibility"])
        self.assertIn("unresolvable process names", result["process_visibility"])
        self.assertEqual(result["memory_guard"]["release_allowance_mib"], 4)

    def test_dormant_unattributed_process_refused(self):
        self.profile.unlink()
        for claim in (ENGINE.replace("11,", "12,"), ENGINE.replace("ComfyUI", "model"), ENGINE.replace("30", "31")):
            with self.subTest(claim=claim):
                self.nvidia.return_value.stdout = claim
                self.assert_refused("outside the reviewed engine baseline")

    def test_dormant_pid_from_previous_boot_refused(self):
        self.profile.unlink()
        self.boot.write_text("other-boot")
        self.assert_refused("another boot")

    def test_card_sample_unavailable_or_wrong_card_refused(self):
        self.sample.return_value = dict(SAMPLE, gpu_uuid=fixtures.OTHER)
        self.assert_refused("Invalid GPU memory sample")
        self.sample.side_effect = OSError
        self.assert_refused("memory sample is unavailable")

    def test_drift_ceiling_and_utilization_refused_even_on_last_sample(self):
        for baseline, sample in (
            (64, dict(SAMPLE, memory_mib=69)),
            (128, dict(SAMPLE, memory_mib=129)),
            (64, dict(SAMPLE, utilization_percent=6)),
        ):
            with self.subTest(sample=sample):
                self.lane.write_state(INSTANCE, False, baseline=baseline, hold=True)
                self.sample.side_effect = [SAMPLE, SAMPLE, sample]
                self.assert_refused("Card is busy or memory exceeds")

    def test_release_at_two_and_four_mib_above_load_baseline_clears(self):
        self.evidence["calibration"]["samples"] = [dict(SAMPLE, memory_mib=123.5)] * 3
        for mounted in (True, False):
            if not mounted:
                self.profile.unlink()
            for drift in (2, 4):
                with self.subTest(mounted=mounted, drift=drift):
                    self.lane.write_state(INSTANCE, False, baseline=124, hold=True)
                    self.sample.return_value = dict(SAMPLE, memory_mib=124 + drift)
                    result = self.assert_archived(self.evidence)
                    self.assertEqual(result["unloaded_max_mib"], 128)
                    self.assertEqual(result["calibration_ceiling_range_mib"], [124, 128])
                    self.assertNotIn("ceiling_correction", result)

    def test_release_at_five_mib_above_load_baseline_holds_at_calibrated_ceiling(self):
        self.evidence["calibration"]["samples"] = [dict(SAMPLE, memory_mib=123.5)] * 3
        self.lane.write_state(INSTANCE, False, baseline=124, hold=True)
        self.sample.return_value = dict(SAMPLE, memory_mib=129)
        self.assert_refused("Card is busy or memory exceeds")
        self.profile.unlink()
        self.assert_refused("Card is busy or memory exceeds")

    def test_queue_rechecked_before_evidence(self):
        self.queue.side_effect = [fixtures.EMPTY, {**fixtures.EMPTY, "queue_running": [[0, "new"]]}]
        self.assert_refused("already has a job")
        self.assertEqual(self.sample.call_count, 3)

    def test_dormant_socket_rechecked_before_evidence(self):
        self.profile.unlink()

        def sample(_):
            if self.sample.call_count == 3:
                local.socket_path(self.root).touch()
            return SAMPLE

        self.sample.side_effect = sample
        self.assert_refused("socket path appeared during proof")

    def test_changed_state_rechecked_before_evidence(self):
        def sample(_):
            if self.sample.call_count == 3:
                self.lane.write_state(INSTANCE, False, baseline=65, hold=True)
            return SAMPLE

        self.sample.side_effect = sample
        with self.assertRaisesRegex(ValueError, "journal changed during proof"):
            recovery.clear(self.evidence)
        self.assertEqual(json.loads(self.path.read_bytes())["baseline_memory_mib"], 65)
        self.assertEqual(list(self.path.parent.glob("hold-clear.*.json")), [])
        self.assertEqual(list(self.path.parent.glob("state.*.json")), [])

    def test_evidence_write_failure_does_not_archive_state(self):
        with patch.object(local, "write_record", side_effect=OSError("record write failed")):
            self.assert_refused("record write failed")

    def assert_archived(self, evidence):
        original = self.path.read_bytes()
        write = local.write_record

        def record(path, body):
            self.assertIsNone(api.acquire_compute())
            self.assertEqual(self.path.read_bytes(), original)
            write(path, body)

        with patch.object(local, "write_record", side_effect=record):
            result = recovery.clear(evidence)
        self.assertFalse(self.path.exists())
        archive = self.path.with_name(result["archive"])
        self.assertRegex(archive.name, r"^state\.\d{8}T\d{6}\.\d{6}Z-[a-f0-9]{32}\.json$")
        self.assertEqual(archive.read_bytes(), original)
        self.assertEqual(result["state_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(result["lease"]["inode"], (self.root / "compute.lock").stat().st_ino)
        self.assertEqual(len(result["samples"]), 3)
        self.assertEqual(result, json.loads(self.path.with_name(result["receipt"]).read_text()))
        return result

    def test_archives_after_mounted_residency_and_sample_proof(self):
        result = self.assert_archived({})
        self.assertEqual(result["baseline_source"], "residency_journal")
        self.assertEqual(result["baseline_memory_mib"], 64)
        self.assertEqual([answer["resident"] for answer in result["residency"]], [False] * 3)
        self.exchange.assert_called_with(self.root, {"action": "residency"})
        self.nvidia.assert_not_called()

    def test_archives_after_dormant_socket_and_attributed_process_proof(self):
        self.profile.unlink()
        result = self.assert_archived(self.evidence)
        self.assertEqual(result["baseline_memory_mib"], 64)
        self.assertEqual([answer["compute_apps_csv"] for answer in result["residency"]], [ENGINE] * 3)
        self.assertEqual(self.nvidia.call_count, 3)
        self.assertIn("--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory", self.nvidia.call_args.args[0])
        self.exchange.assert_not_called()

    def test_archives_hold_before_first_load_against_calibration(self):
        self.lane.write_state(INSTANCE, False, hold=True)
        result = self.assert_archived(self.evidence)
        self.assertEqual(result["baseline_source"], "dormant_calibration")
        self.assertEqual(result["baseline_memory_mib"], 128)

    def test_cli_refuses_nonobject_evidence_before_inspection(self):
        with patch.object(recovery.sys, "stdin", io.StringIO("[]")), \
                patch.object(recovery.sys, "stderr", io.StringIO()) as stderr:
            self.assertEqual(recovery.main(), 1)
        self.assertIn("REFUSED: Expected a recovery evidence object", stderr.getvalue())
        self.queue.assert_not_called()
