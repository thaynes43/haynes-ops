"""CPU-only refusal and archive checks for the operator recovery command."""
from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
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
        self.profile.write_text(json.dumps({**PROFILE, "unloaded_max_mib": 127}))
        self.assert_refused("Profile does not match")

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
            ({"samples": [dict(SAMPLE, memory_mib=127)] * 3}, "ceiling does not match"),
        ):
            with self.subTest(reason=reason):
                self.evidence["calibration"] = {**original, **changes}
                self.assert_refused(reason)

    def test_dormant_socket_even_dangling_refused(self):
        self.profile.unlink()
        local.socket_path(self.root).symlink_to(self.root / "missing")
        self.assert_refused("socket path still exists")
        self.nvidia.assert_not_called()

    def test_dormant_compute_query_failure_or_malformed_row_refused(self):
        self.profile.unlink()
        self.nvidia.return_value.stdout = "bad row"
        self.assert_refused("Malformed GPU compute-process")
        self.nvidia.return_value.stdout = f"{CARD}, 11, [Not Found], 30"
        self.assert_refused("Unverifiable GPU compute-process")
        self.nvidia.side_effect = subprocess.CalledProcessError(1, "nvidia-smi")
        self.assert_refused("process query failed")

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
        self.assert_refused("socket path still exists")

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
