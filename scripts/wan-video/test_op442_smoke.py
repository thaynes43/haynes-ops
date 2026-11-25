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
                smoke.smoke(self.directory)
        http.assert_called_once()
        finish.assert_not_called()

    def test_handover_respects_full_render_and_cleanup_budget(self):
        with patch.object(self.trial, "time_left", return_value=1329), \
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


if __name__ == "__main__":
    unittest.main()
