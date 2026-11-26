"""Exercise the launcher and the matte activation's policy and log audits."""

import copy
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import yaml

from check import (
    MATTE_ACTIVATION,
    MATTE_POLICY,
    STUDIO_PATH,
    check_additive_matte_ingress,
    check_installed_policies,
    render,
    resource,
)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LAUNCHER_PATH = STUDIO_PATH / MATTE_ACTIVATION / "worker/matte-start.py"
ACTIVE = LAUNCHER_PATH.is_file()
LOG = load(Path("scripts/studio-release/op970-log.py"), "matte_log")


@unittest.skipUnless(ACTIVE, "the reviewed revert removes the matte launcher")
class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.launch = load(LAUNCHER_PATH, "matte_launcher")
        directory = Path(self.enterContext(TemporaryDirectory(prefix="op970-progress-")))
        self.progress = directory / "worker-progress"
        self.progress.touch()
        self.enterContext(patch.dict(os.environ, {"STUDIO_WORKER_PROGRESS_FILE": str(self.progress)}))
        self.token = Mock()
        self.token.read_text.return_value = "fixture only"
        self.child = Mock(pid=123)
        self.child.wait.return_value = 0
        self.spawn = self.enterContext(patch.object(self.launch.subprocess, "Popen", return_value=self.child))
        self.grant = self.enterContext(patch.object(self.launch, "wait_for_grant", return_value=100))
        self.enterContext(patch.object(self.launch, "TOKEN", self.token))
        self.enterContext(patch.object(self.launch.signal, "signal"))
        self.enterContext(patch.object(self.launch.sys, "argv", ["matte-start.py", "studio-worker", "--lane", "matte"]))
        self.getaddrinfo = self.enterContext(patch.object(self.launch.socket, "getaddrinfo", side_effect=self.resolve))
        self.client = Mock()
        self.client.connect.side_effect = self.connect
        network = self.enterContext(patch.object(self.launch.socket, "socket"))
        network.return_value.__enter__.return_value = self.client
        self.up = {self.launch.LISTENER[1]}
        self.visited = []
        self.output = self.enterContext(redirect_stdout(io.StringIO()))

    def resolve(self, host, port, *, type):
        self.assertEqual(type, self.launch.socket.SOCK_STREAM)
        return [(self.launch.socket.AF_INET, type, 0, "", ("10.1.2.3", port))]

    def connect(self, address):
        port = address[1]
        self.visited.append(port)
        if port not in self.up:
            raise TimeoutError()

    def events(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]

    def assert_refuses_before_token(self):
        self.assertEqual(self.launch.main(), 1)
        self.token.read_text.assert_not_called()
        self.grant.assert_not_called()
        self.spawn.assert_not_called()
        self.assertFalse(self.progress.exists())
        event, = self.events()
        self.assertEqual((event["phase"], event["result"]), ("startup", "fail"))
        return event["reason"]

    def test_each_open_forbidden_socket_refuses_before_token(self):
        for host, port in self.launch.FORBIDDEN:
            with self.subTest(port=port):
                self.up.add(port)
                self.assertIn(f"{host}:{port}", self.assert_refuses_before_token())
                self.up.remove(port)
                self.output.seek(0)
                self.output.truncate(0)

    def test_dns_and_listener_up_and_both_forbidden_blocked_starts_worker(self):
        self.assertEqual(self.launch.main(), 0)
        self.assertEqual(self.visited, [8002, 443, 8188])
        self.token.read_text.assert_called_once_with()
        self.grant.assert_called_once()
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0], ["studio-worker", "--lane", "matte"])
        self.assertIs(self.spawn.call_args.kwargs["start_new_session"], True)
        self.assertEqual(self.events(), [{"event": "matte_isolation", "phase": "startup",
                                         "result": "pass", "reason": ""}])

    def test_positives_down_is_not_isolation(self):
        self.up.clear()
        self.assertIn("TCP 8002", self.assert_refuses_before_token())
        self.output.seek(0)
        self.output.truncate(0)
        self.getaddrinfo.side_effect = self.launch.socket.gaierror()
        self.assertIn("gaierror", self.assert_refuses_before_token())

    def test_lost_isolation_kills_worker_process_group(self):
        def wait(timeout=None):
            if timeout is not None:
                self.up.add(8188)
                raise subprocess.TimeoutExpired("studio-worker", timeout)
            return -signal.SIGKILL

        self.child.wait.side_effect = wait
        with patch.object(self.launch.os, "killpg") as kill:
            self.assertEqual(self.launch.main(), 1)
            kill.assert_called_once_with(self.child.pid, signal.SIGKILL)
        self.assertEqual([(event["phase"], event["result"]) for event in self.events()],
                         [("startup", "pass"), ("recheck", "fail")])


@unittest.skipUnless(ACTIVE, "the reviewed revert removes the matte launcher")
class GrantWaitTest(unittest.TestCase):
    def setUp(self):
        self.launch = load(LAUNCHER_PATH, "matte_grant_wait")
        self.client = MagicMock()
        self.client.__enter__.return_value = self.client
        self.enterContext(patch.dict(sys.modules, {
            "httpx": SimpleNamespace(Client=Mock(return_value=self.client), RequestError=ValueError),
            "haynes_studio.queue.progress": SimpleNamespace(ProgressFile=Mock()),
            "haynes_studio.queue.worker_tokens": SimpleNamespace(worker_bearer=lambda _: "fixture-bearer"),
        }))
        self.enterContext(patch.dict(os.environ, {
            "STUDIO_WORKER_PROGRESS_FILE": "/unused", "STUDIO_API_URL": "http://fixture",
        }))
        self.clock = self.enterContext(patch.object(self.launch, "monotonic"))
        self.enterContext(patch.object(self.launch, "sleep"))

    def test_recheck_failure_during_grant_wait_stops_before_another_request(self):
        self.client.post.return_value.status_code = 401
        self.clock.side_effect = [0, 65]
        with patch.object(self.launch, "prove_isolation", return_value=False) as proof:
            self.assertIsNone(self.launch.wait_for_grant("fixture", lambda: False, 60))
            proof.assert_called_once_with("recheck")
        self.client.post.assert_called_once()

    def test_waits_for_the_exact_validation_error(self):
        self.clock.return_value = 0
        self.client.post.side_effect = [Mock(status_code=422, **{"json.return_value": response}) for response in (
            [], {"error": None}, {"error": {"code": "schema.unknown_field", "path": "/other"}},
            {"error": {"code": "schema.unknown_field", "path": "/registration_probe"}},
        )]
        self.assertEqual(self.launch.wait_for_grant("fixture", lambda: False, 60), 60)
        self.assertEqual(self.client.post.call_count, 4)
        self.assertTrue(all(call.kwargs == {"json": {"lane": "matte", "registration_probe": True}}
                            for call in self.client.post.call_args_list))


@unittest.skipUnless(ACTIVE, "requires the rendered activation")
class ProjectedPolicyTest(unittest.TestCase):
    def setUp(self):
        self.app = render(STUDIO_PATH / "app")
        self.stage = render(STUDIO_PATH / MATTE_ACTIVATION)
        self.policy = resource(render(STUDIO_PATH / "matting-pending/worker-policy"),
                               "NetworkPolicy", MATTE_POLICY)
        self.current = copy.deepcopy(resource(self.app, "NetworkPolicy", "studio-api-ingress"))
        for docs in (self.app, self.stage, [self.policy, self.current]):
            for document in docs:
                document["metadata"].setdefault("namespace", "ai")
        self.candidate = copy.deepcopy(self.current["spec"])
        self.current["spec"]["ingress"][1]["from"][0]["podSelector"]["matchExpressions"][0]["values"].remove(MATTE_POLICY)

    def test_rendered_api_ingress_diff_is_additive_only(self):
        check_additive_matte_ingress(self.current["spec"], self.candidate)
        for key in ("ports", "from"):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.candidate)
                changed["ingress"][0][key].pop()
                with self.assertRaisesRegex(ValueError, "only add the matte peer"):
                    check_additive_matte_ingress(self.current["spec"], changed)

    def test_projected_policy_rejects_additional_native_and_cilium_rules(self):
        snapshot = [
            {"items": [self.current, self.policy]},
            {"items": []},
            {"data": {"enable-policy": "default", "cluster-name": "haynes-ops"}},
            {"metadata": {"labels": {"kubernetes.io/metadata.name": "ai"}}},
        ]
        with (
            patch("check.studio_inventory", return_value=(None, None, None, {"studio": "api", MATTE_POLICY: "matte"})),
            patch("check.flux_build", side_effect=lambda _, stage: self.app if stage == "api" else self.stage),
            redirect_stdout(io.StringIO()),
        ):
            check_installed_policies(snapshot, stage_repo=Path.cwd())
            for kind, workload in (
                ("NetworkPolicy", MATTE_POLICY),
                ("CiliumNetworkPolicy", "studio-api"),
                ("CiliumClusterwideNetworkPolicy", MATTE_POLICY),
            ):
                with self.subTest(kind=kind):
                    changed = copy.deepcopy(snapshot)
                    labels = {"app.kubernetes.io/name": workload}
                    rogue = {"kind": kind, "metadata": {"name": "unexpected", "namespace": "ai"}}
                    if kind == "NetworkPolicy":
                        rogue["spec"] = {"podSelector": {"matchLabels": labels}, "egress": [{}]}
                    elif kind == "CiliumNetworkPolicy":
                        rogue["specs"] = [{"endpointSelector": {"matchLabels": labels}, "ingressDeny": [{}]}]
                    else:
                        rogue["spec"] = {"endpointSelector": {"matchLabels": labels}, "ingress": [{}]}
                    changed[int(kind == "CiliumClusterwideNetworkPolicy")]["items"].append(rogue)
                    with self.assertRaisesRegex(ValueError, "effective egress|unexpected Cilium policy"):
                        check_installed_policies(changed, stage_repo=Path.cwd())


class ProofLogTest(unittest.TestCase):
    def test_requires_startup_after_pod_and_fresh_successful_recheck(self):
        def line(time, phase, result="pass"):
            return f"2026-11-26T00:{time}Z " + json.dumps(
                {"event": "matte_isolation", "phase": phase, "result": result})

        start = datetime.fromisoformat("2026-11-26T00:01:00Z")
        now = datetime.fromisoformat("2026-11-26T00:02:25Z")
        initial, repeat = line("01:05", "startup"), line("02:10", "recheck")
        for lines, status in (
            ([initial, repeat], 0),
            ([initial], 2),
            ([line("01:00", "startup"), repeat], 1),
            ([initial, line("01:04", "recheck")], 1),
            ([initial, repeat, line("02:20", "recheck", "fail")], 1),
        ):
            with self.subTest(log=lines), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(LOG.audit(start, lines, now), status)


class ReleasePhaseTest(unittest.TestCase):
    STUBS = """
source scripts/studio-release/op970.sh || exit 3
candidate_checkout() {
  head=$(printf 'a%.0s' {1..40})
  tree=$(printf 'b%.0s' {1..40})
}
selected_checkout() { candidate_checkout; }
flux_state() { return 0; }
policy_snapshot() { printf 'policy snapshot %s\n' "$1"; }
installed_policies() { [[ "$1" != "$DAMAGE" ]]; }
allocation_present() { return 0; }
control_connections() { return 0; }
api_endpoints() { return 0; }
render_pin() { printf 'render CPU %s\n' "$1"; }
wan_idle() { return 0; }
images_idle() { return 0; }
migration() { return 0; }
render_idle() { return 0; }
rollback_candidate() { return 0; }
matte_pods() { return 0; }
matte_removed() { return 0; }
proof_logs() { [[ "$DAMAGE" != bad-proof ]]; }
registration() { return 0; }
worker_started() { return 0; }
kubectl() {
  [[ $* == '-n ai exec -i deploy/studio-matte-worker -c worker -- python -I -B - matte' ]] || return 3
  printf 'matte listener checked\n'
}
data_probe() {
  printf 'snapshot %s\n' "$1"
  case $1 in
    matte-before|matte-retry|matte-rollback) printf 'baseline\n' > "$baseline" ;;
    *) [[ $(< "$baseline") == baseline && "$DAMAGE" != bad-data ]] ;;
  esac
}
run_activation_check "$@"
"""

    def setUp(self):
        directory = Path(self.enterContext(TemporaryDirectory(prefix="op970-receipts-")))
        self.receipt, self.baseline = (directory / file for file in ("receipt", "baseline"))

    def phase(self, *phase, damage=""):
        return subprocess.run(
            ["bash", "-c", self.STUBS, "fixture", *phase], text=True, capture_output=True,
            env={**os.environ, "STUDIO_OP970_RECEIPT": str(self.receipt),
                 "STUDIO_OP970_BASELINE": str(self.baseline), "DAMAGE": damage},
        )

    def test_fresh_preflight_receipt_required_for_each_merge_mode(self):
        for phase, mode, snapshot in (
            ("preflight", "release", "matte-before"),
            ("retry-preflight", "retry", "matte-retry"),
            ("rollback-preflight", "rollback", "matte-rollback"),
        ):
            with self.subTest(mode=mode):
                result = self.phase(phase)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("snapshot " + snapshot, result.stdout)
                receipt = json.loads(self.receipt.read_text())
                self.assertEqual(receipt["jobs_sha256"], "")
                result = self.phase("admit-merge", mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.baseline.write_text("tampered")
                result = self.phase("admit-merge", mode)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.baseline.unlink()
                self.receipt.unlink()

    def test_policy_projection_failure_blocks_baseline_and_admission(self):
        preflight = self.phase("preflight", damage="preflight")
        self.assertEqual(preflight.returncode, 1, preflight.stderr)
        self.assertFalse(self.baseline.exists())
        admission = self.phase("admit-merge")
        self.assertEqual(admission.returncode, 1, admission.stderr)

    @unittest.skipUnless(ACTIVE, "requires the active matting pin")
    def test_current_ready_matte_pod_can_have_restarted_during_isolation_convergence(self):
        api = yaml.safe_load((STUDIO_PATH / "release.yaml").read_text())["apiDigest"]
        pin = yaml.safe_load((STUDIO_PATH / "matting-pending/release/release.yaml").read_text())["data"]
        containers = {"worker": f"ghcr.io/thaynes43/haynes-studio@{api}", "matting": pin["image"]}
        snapshot = {"items": [{
            "metadata": {"name": "matte-fixture"},
            "spec": {"nodeName": "talosw01", "containers": [
                {"name": name, "image": image} for name, image in containers.items()]},
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "containerStatuses": [
                    {"name": name, "imageID": image, "ready": True, "restartCount": 1}
                    for name, image in containers.items()],
            },
        }]}
        result = subprocess.run(["bash", "-c", """
source scripts/studio-release/op970.sh || exit 3
kubectl() { printf '%s\\n' "$POD_FIXTURE"; }
matte_pods
"""], env={**os.environ, "POD_FIXTURE": json.dumps(snapshot)}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("matte-fixture", result.stdout)

    def test_postflight_refuses_listener_probes_until_current_proof_and_matching_data(self):
        self.baseline.write_text("baseline\n")
        for damage in ("", "bad-proof", "bad-data"):
            with self.subTest(damage=damage):
                result = self.phase("postflight", damage=damage)
                self.assertEqual(result.returncode, int(bool(damage)), result.stderr)
                self.assertEqual("matte listener checked" in result.stdout, not damage)
                self.assertIn("render CPU 17", result.stdout)

    def test_rollback_compares_retained_rows_and_render_limit_after_pod_removal(self):
        result = self.phase("rollback-preflight")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.phase("compare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("policy snapshot empty", result.stdout)
        self.assertIn("snapshot matte-removed", result.stdout)
        self.assertIn("render CPU 20", result.stdout)


if __name__ == "__main__":
    unittest.main()
