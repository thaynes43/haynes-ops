"""CPU-only tests of the coordinator's stop and cleanup boundaries."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import op442_smoke as smoke


class TrialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.profile = {"model": smoke.MODEL, "unloaded_max_mib": 266}
        self.trial = smoke.Trial(self.directory, self.profile)

    def test_cleanup_reconciles_running_id_waits_for_lease_and_unloads(self):
        self.trial.pending = ("timed-out", "/v1/local-model/requests/original")
        resident = {"sample": {"resident": True}, "journal": {"baseline_memory_mib": 264}}
        idle = {"sample": {"resident": False}, "journal": None}
        unload = {"id": "tracked-unload", "state": "succeeded", "baseline_memory_mib": 264,
                  "released_memory_mib": 266}
        with patch.object(smoke, "http", side_effect=[(200, {"state": "running"}), (200, {"state": "succeeded"})]) as http, \
                patch.object(smoke.time, "sleep"), \
                patch.object(smoke, "lane", side_effect=[{"lease_busy": True}, resident, idle]) as lane, \
                patch.object(self.trial, "operation", return_value=unload) as operation:
            self.trial.finish()
        self.assertIsNone(self.trial.pending)
        self.assertEqual(http.call_count, 2)
        self.assertTrue(all(call.args[1] == "/v1/local-model/requests/original" for call in http.call_args_list))
        self.assertTrue(all("verb" not in call.kwargs for call in http.call_args_list))
        self.assertEqual(operation.call_args.args, ("final-unload", "unload"))
        self.assertEqual(lane.call_args.args, ("prove",))
        self.assertEqual(json.loads((self.directory / "final-unload-drift.json").read_text())["difference_mib"], 2)

    def test_denial_exits_without_cleanup_or_replay(self):
        idle = {"sample": {"resident": False}, "journal": None}
        refusal = "Error from server (Forbidden): cannot create resource pods/exec"
        with patch.object(smoke, "health", return_value=self.profile), \
                patch.object(smoke, "stage"), patch.object(smoke, "lane", return_value=idle), \
                patch.object(smoke, "telemetry", return_value=nullcontext([])), \
                patch.object(smoke, "http", side_effect=smoke.exec_failure("api", refusal.encode())) as http, \
                patch.object(smoke.Trial, "finish") as finish:
            with self.assertRaisesRegex(smoke.Denied, "Forbidden"):
                smoke.run_window(self.directory, 1)
        http.assert_called_once()
        finish.assert_not_called()

    def test_handover_respects_full_render_and_cleanup_budget(self):
        with patch.object(self.trial, "time_left", return_value=smoke.S2V_BUDGET + smoke.S2V_RESERVE - 1), \
                patch.object(smoke, "pod") as pod, patch.object(smoke, "http") as http:
            with self.assertRaisesRegex(smoke.Stop, "Insufficient time"):
                self.trial.handover(266, {})
        pod.assert_not_called()
        http.assert_not_called()

    def test_release_failure_records_a_fresh_post_exit_sample(self):
        unload = {"id": "failed-unload", "state": "failed", "baseline_memory_mib": 260}
        with patch.object(self.trial, "sample", return_value={"sample": {"resident": False, "memory_mib": 265}}):
            with self.assertRaisesRegex(smoke.Stop, "release unverified"):
                self.trial.release("cycle-1", unload, 260)
        evidence = json.loads((self.directory / "cycle-1-drift.json").read_text())
        self.assertEqual(evidence["difference_mib"], 5)
        self.assertEqual(evidence["source"], "post_failure_sample")

    def test_window_two_requires_window_one_before_any_work(self):
        with patch.object(smoke, "health") as health, patch.object(smoke, "stage") as stage:
            with self.assertRaisesRegex(smoke.Stop, "Window 1 must pass"):
                smoke.run_window(self.directory, 2)
        health.assert_not_called()
        stage.assert_not_called()

    def test_windows_gate_on_final_proof_and_keep_work_in_order(self):
        events = []
        idle = {"sample": {"resident": False}, "journal": None}

        def load(trial, label):
            events.append(label)
            return {"baseline_memory_mib": 264}

        def infer(trial, label, budget, **parameters):
            events.append(label)
            if label == "text-plan":
                return {"scene_id": "s01", "shots": [{}, {}]}
            return {"frames": [
                {"face": "visible", "code": "A17", "wardrobe": "match"},
                {"face": "hidden", "code": "B24", "wardrobe": "match"},
                {"face": "visible", "code": "C38", "wardrobe": "mismatch"},
                {"face": "visible", "code": None, "wardrobe": "uncertain"},
            ]}

        def telemetry(directory, phase):
            (directory / f"{phase}-gpu.csv").touch()
            return nullcontext([])

        def finish(trial):
            self.assertFalse((trial.directory / "passed.json").exists())
            events.append(f"{trial.directory.name}-verified-unload")

        hashes = {f"frame-{index}.png": "hash" for index in range(1, 5)}
        with patch.object(smoke, "health", return_value=self.profile), \
                patch.object(smoke, "stage", return_value=hashes) as stage, \
                patch.object(smoke, "lane", return_value=idle), \
                patch.object(smoke, "telemetry", side_effect=telemetry), \
                patch.multiple(smoke.Trial, load=load, infer=infer, finish=finish,
                               check_peak=lambda *a, **kw: None,
                               handover=lambda *a: events.append("s2v"),
                               operation=lambda *a, **kw: {},
                               release=lambda trial, label, *a: events.append(label + "-verified-unload")):
            smoke.run_window(self.directory, 1)
            smoke.run_window(self.directory, 2)

        stage.assert_called_once()
        self.assertEqual(events[:6], [
            "cold-load", "text-plan", "vision", "s2v", "return-load", "window-1-verified-unload",
        ])
        self.assertEqual(events[6:-1], [
            label for index in range(1, 6) for label in (f"cycle-{index}-load", f"cycle-{index}-verified-unload")
        ])
        self.assertEqual(events[-1], "window-2-verified-unload")
        for number in (1, 2):
            self.assertTrue((self.directory / f"window-{number}" / "passed.json").is_file())

    def test_window_cleans_up_after_a_budget_stop_but_preserves_a_release_hold(self):
        idle = {"sample": {"resident": False}, "journal": None}
        for error, cleanup_expected in (
            (smoke.Stop("work budget reached"), True),
            (smoke.ReleaseUnverified("release proof failed"), False),
        ):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as work:
                directory = Path(work)
                with patch.object(smoke, "health", return_value=self.profile), \
                        patch.object(smoke, "stage"), patch.object(smoke, "lane", return_value=idle), \
                        patch.object(smoke, "telemetry", return_value=nullcontext([])), \
                        patch.object(smoke, "handover_window", side_effect=error), \
                        patch.object(smoke.Trial, "finish") as finish:
                    with self.assertRaisesRegex(smoke.Stop, str(error)):
                        smoke.run_window(directory, 1)
                self.assertEqual(finish.called, cleanup_expected)
                self.assertFalse((directory / "window-1" / "passed.json").exists())


if __name__ == "__main__":
    unittest.main()
