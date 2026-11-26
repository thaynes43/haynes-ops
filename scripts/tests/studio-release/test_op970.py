"""Exercise the launcher and the matte activation's policy and log audits."""

import copy
import errno
import importlib.util
import io
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

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
        self.signals = self.enterContext(patch.object(self.launch.signal, "signal"))
        self.enterContext(patch.object(self.launch.sys, "argv", ["matte-start.py", "studio-worker", "--lane", "matte"]))
        self.getaddrinfo = self.enterContext(patch.object(self.launch.socket, "getaddrinfo", side_effect=self.resolve))
        self.client = Mock()
        self.client.connect.side_effect = self.connect
        network = self.enterContext(patch.object(self.launch.socket, "socket"))
        network.return_value.__enter__.return_value = self.client
        packet = Mock()
        packet.read.side_effect = lambda size: self.dns_response.read(size)
        self.client.makefile.return_value.__enter__ = Mock(return_value=packet)
        self.client.makefile.return_value.__exit__ = Mock(return_value=False)
        self.client.sendall.side_effect = self.dns_reply
        self.sleep = self.enterContext(patch.object(self.launch, "sleep"))
        self.up = {53, 8002}
        self.errors = {}
        self.answers = {
            "comfyui-0.comfyui.ai.svc.cluster.local.": [("10.42.1.8", 5)],
            "loki-headless.observability.svc.cluster.local.": [("10.42.2.9", 5)],
        }
        self.visited = []
        self.lookups = []
        self.stale_once = set()
        self.output = self.enterContext(redirect_stdout(io.StringIO()))

    def resolve(self, host, port, *, type):
        self.assertEqual(type, self.launch.socket.SOCK_STREAM)
        return [(self.launch.socket.AF_INET, type, 0, "", ("10.1.2.3", port))]

    def connect(self, address):
        self.visited.append(address)
        port = address[1]
        if port in self.errors:
            raise self.errors[port]
        if port not in self.up:
            raise TimeoutError()

    def dns_reply(self, wire):
        self.assertEqual(int.from_bytes(wire[:2], "big"), len(wire) - 2)
        query_id, flags, questions, answers, authority, extra = struct.unpack("!6H", wire[2:14])
        self.assertEqual((flags, questions, answers, authority, extra), (0x100, 1, 0, 0, 0))
        name = wire[14:-4]
        question = wire[14:]
        self.assertEqual(question[-4:], b"\x00\x01\x00\x01")
        labels = []
        while name[0]:
            size = name[0]
            labels.append(name[1:size + 1].decode("ascii"))
            name = name[size + 1:]
        host = ".".join(labels) + "."
        self.lookups.append(host)
        addresses = self.answers[host]
        flags = 0x8500 if addresses else 0x8503
        reply = struct.pack("!6H", query_id, flags, 1, len(addresses), 0, 0) + question
        for address, ttl in addresses:
            if host in self.stale_once:
                ttl = 0
                self.stale_once.remove(host)
            reply += b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, ttl, 4) + socket.inet_aton(address)
        self.dns_response = io.BytesIO(struct.pack("!H", len(reply)) + reply)

    def stop(self):
        self.signals.call_args_list[0].args[1](signal.SIGTERM, None)

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
        self.assertEqual(self.lookups, list(self.answers))
        self.assertEqual(self.visited, [
            ("10.1.2.3", 8002), ("10.43.0.10", 53), ("10.42.1.8", 8188),
            ("10.43.0.10", 53), ("10.42.2.9", 3100),
        ])
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

    def test_ambiguous_listener_dns_refuses_before_connect(self):
        self.getaddrinfo.side_effect = lambda *args, **kw: self.resolve(*args, **kw) + [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.1.2.4", 8002))]
        self.assertIn("expected one DNS address", self.assert_refuses_before_token())
        self.client.connect.assert_not_called()

    def test_ambiguous_ready_endpoint_refuses_before_trying_its_port(self):
        self.answers[self.launch.FORBIDDEN[0][0]].append(("10.42.1.9", 5))
        self.assertIn("expected one Ready endpoint DNS answer", self.assert_refuses_before_token())
        self.assertEqual([port for _, port in self.visited], [8002, 53])

    def test_stale_dns_does_not_show_a_control_is_live(self):
        self.answers[self.launch.FORBIDDEN[1][0]] = [("10.42.2.9", 0)]
        self.assertIn("fresh Ready pod address", self.assert_refuses_before_token())
        self.assertEqual([port for _, port in self.visited], [8002, 53, 8188, 53])

    def test_expired_cache_answer_can_be_replaced_by_fresh_readiness(self):
        host = self.launch.FORBIDDEN[0][0]
        self.stale_once.add(host)
        self.assertEqual(self.launch.main(), 0)
        self.assertEqual(self.lookups, [host, host, self.launch.FORBIDDEN[1][0]])
        self.assertEqual([port for _, port in self.visited], [8002, 53, 8188, 53, 3100])
        self.sleep.assert_called_once_with(1)

    def test_dns_cluster_ip_does_not_show_a_ready_backend(self):
        self.answers[self.launch.FORBIDDEN[0][0]] = [("10.43.1.8", 5)]
        self.assertIn("Ready pod address", self.assert_refuses_before_token())

    def test_combined_comfyui_backend_outage_and_kubernetes_api_timeout_fail(self):
        self.errors.update({443: TimeoutError(), 8188: ConnectionRefusedError(errno.ECONNREFUSED, "empty Service")})
        self.answers["comfyui-0.comfyui.ai.svc.cluster.local."] = []
        self.assertIn("expected one Ready endpoint", self.assert_refuses_before_token())
        self.assertEqual(self.lookups, ["comfyui-0.comfyui.ai.svc.cluster.local."])
        self.assertEqual([port for _, port in self.visited], [8002, 53])

    def test_dns_socket_timeout_does_not_count_as_a_control_timeout(self):
        self.errors[53] = TimeoutError()
        self.assertIn("TimeoutError", self.assert_refuses_before_token())

    def test_two_second_tcp_timeout(self):
        self.errors[8188] = OSError(errno.ETIMEDOUT, "kernel connect timeout")
        self.assertEqual(self.launch.main(), 0)
        self.assertEqual(self.client.settimeout.call_args_list, [call(2)] * 5)

    def test_connection_refused_fails(self):
        self.errors[8188] = OSError(errno.ECONNREFUSED, "refused")
        self.assertIn("ConnectionRefusedError", self.assert_refuses_before_token())

    def test_connection_reset_fails(self):
        self.errors[8188] = OSError(errno.ECONNRESET, "reset")
        self.assertIn("ConnectionResetError", self.assert_refuses_before_token())

    def test_host_and_network_unreachable_fail(self):
        for number in (errno.EHOSTUNREACH, errno.ENETUNREACH):
            with self.subTest(errno=number):
                self.errors[8188] = OSError(number, "unreachable")
                self.assertIn(f"Errno {number}", self.assert_refuses_before_token())
                self.output.seek(0)
                self.output.truncate(0)

    def test_permissions_errors_fail(self):
        for number in (errno.EACCES, errno.EPERM):
            with self.subTest(errno=number):
                self.errors[8188] = OSError(number, "denied")
                self.assertIn("PermissionError", self.assert_refuses_before_token())
                self.output.seek(0)
                self.output.truncate(0)

    def test_unknown_socket_error_fails(self):
        self.errors[8188] = OSError(errno.EIO, "unexpected error")
        self.assertIn("unexpected error", self.assert_refuses_before_token())

    def test_initial_recheck_deadline_is_one_minute(self):
        self.grant.side_effect = lambda secret, stopping, deadline: deadline
        with patch.object(self.launch, "monotonic", return_value=50):
            self.assertEqual(self.launch.main(), 0)
        self.assertEqual(self.grant.call_args.args[2], 110)
        self.child.wait.assert_called_once_with(timeout=60)

    def test_failed_grant_wait_does_not_spawn(self):
        self.grant.return_value = None
        self.assertEqual(self.launch.main(), 1)
        self.token.read_text.assert_called_once()
        self.spawn.assert_not_called()

    def test_shutdown_during_startup_proof_precedes_token_read(self):
        prove = self.launch.prove_isolation

        def stopping_proof(phase):
            result = prove(phase)
            self.stop()
            return result

        with patch.object(self.launch, "prove_isolation", side_effect=stopping_proof):
            self.assertEqual(self.launch.main(), 0)
        self.token.read_text.assert_not_called()
        self.grant.assert_not_called()
        self.spawn.assert_not_called()

    def test_shutdown_during_successful_grant_wait_precedes_spawn(self):
        def stopping_grant(secret, stopping, deadline):
            self.stop()
            return deadline

        self.grant.side_effect = stopping_grant
        self.assertEqual(self.launch.main(), 0)
        self.token.read_text.assert_called_once()
        self.spawn.assert_not_called()

    def test_lost_isolation_kills_worker_process_group(self):
        def wait(timeout=None):
            if timeout is not None:
                self.up.add(3100)
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


    def test_same_schema_path_with_wrong_code_keeps_waiting(self):
        self.clock.return_value = 0
        self.client.post.return_value = Mock(status_code=422, **{"json.return_value": {
            "error": {"code": "schema.wrong_type", "path": "/registration_probe"},
        }})
        stopping = Mock(side_effect=[False, True])
        self.assertIsNone(self.launch.wait_for_grant("fixture", stopping, 60))
        self.client.post.assert_called_once()


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

    @staticmethod
    def control(namespace, name, label):
        uid = f"{name}-uid"
        return {"items": [
            {"kind": "Pod", "metadata": {"namespace": namespace, "name": name, "uid": uid,
                                          "labels": {"app.kubernetes.io/name": label}},
             "status": {"conditions": [{"type": "Ready", "status": "True"}]}},
            {"kind": "CiliumEndpoint", "metadata": {"namespace": namespace, "name": name,
                                                    "ownerReferences": [{"kind": "Pod", "uid": uid}]},
             "status": {"identity": {"labels": [f"k8s:app.kubernetes.io/name={label}",
                                               f"k8s:io.kubernetes.pod.namespace={namespace}"]}}},
        ]}

    def installed_snapshot(self):
        return [
            {"items": [self.current, self.policy]}, {"items": []},
            {"data": {"enable-policy": "default", "cluster-name": "haynes-ops",
                      "policy-deny-response": "none", "service-no-backend-response": "reject"}},
            self.control("ai", "comfyui-0", "comfyui"), self.control("observability", "loki-0", "loki"),
            {"metadata": {"labels": {"kubernetes.io/metadata.name": "ai"}}},
        ]

    def test_projected_policy_rejects_additional_native_and_cilium_rules(self):
        snapshot = self.installed_snapshot()
        # An egress-only policy on a control does not explain a refused ingress probe.
        snapshot[0]["items"].append({
            "kind": "NetworkPolicy", "metadata": {"name": "loki-egress", "namespace": "observability"},
            "spec": {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "loki"}},
                     "policyTypes": ["Egress"], "egress": []},
        })
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
                        # A third destination can leave both in-pod controls blocked.
                        rogue["spec"] = {"podSelector": {"matchLabels": labels}, "egress": [{
                            "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "wan-video"}}}],
                            "ports": [{"port": 8080, "protocol": "TCP"}],
                        }]}
                    elif kind == "CiliumNetworkPolicy":
                        rogue["specs"] = [{"endpointSelector": {"matchLabels": labels}, "ingressDeny": [{}]}]
                    else:
                        rogue["spec"] = {"endpointSelector": {"matchLabels": labels}, "ingress": [{}]}
                    changed[int(kind == "CiliumClusterwideNetworkPolicy")]["items"].append(rogue)
                    with self.assertRaisesRegex(ValueError, "effective egress|unexpected Cilium policy"):
                        check_installed_policies(changed, stage_repo=Path.cwd())
            for key, changed_value in (("policy-deny-response", "icmp"), ("service-no-backend-response", "drop")):
                with self.subTest(key=key):
                    changed = copy.deepcopy(snapshot)
                    changed[2]["data"][key] = changed_value
                    with self.assertRaisesRegex(ValueError, "must silently deny"):
                        check_installed_policies(changed, stage_repo=Path.cwd())


    def test_ingress_on_either_control_refuses_the_installed_policy_audit(self):
        for kind, namespace, label, name in (
            ("NetworkPolicy", "ai", "comfyui", "comfyui-0"),
            ("NetworkPolicy", "observability", "loki", "loki-0"),
            ("CiliumNetworkPolicy", "observability", "loki", "loki-0"),
            ("CiliumClusterwideNetworkPolicy", None, "loki", "loki-0"),
        ):
            with self.subTest(kind=kind, pod=name), redirect_stdout(io.StringIO()):
                snapshot = self.installed_snapshot()
                pod_labels = {"matchLabels": {"app.kubernetes.io/name": label}}
                if kind == "NetworkPolicy":
                    rules = {"podSelector": pod_labels, "policyTypes": ["Ingress"], "ingress": []}
                else:
                    rules = {"endpointSelector": pod_labels, "ingressDeny": [{"fromEntities": ["all"]}]}
                document = {"kind": kind, "metadata": {"name": "unexpected-control-ingress"}, "spec": rules}
                if namespace:
                    document["metadata"]["namespace"] = namespace
                snapshot[int(namespace is None)]["items"].append(document)
                with self.assertRaisesRegex(ValueError, f"ingress policy .* selects isolation control .+/{name}"):
                    check_installed_policies(snapshot, stage_repo=Path.cwd())

    def test_control_requires_ready_pod_and_cilium_endpoint_for_its_uid(self):
        for damage in ("readiness", "uid"):
            with self.subTest(damage=damage), redirect_stdout(io.StringIO()):
                snapshot = self.installed_snapshot()
                pod, endpoint = snapshot[4]["items"]
                if damage == "readiness":
                    pod["status"]["conditions"][0]["status"] = "False"
                else:
                    endpoint["metadata"]["ownerReferences"][0]["uid"] = "replaced-pod"
                with self.assertRaisesRegex(ValueError, "Ready isolation control pod|missing installed Cilium endpoint"):
                    check_installed_policies(snapshot, stage_repo=Path.cwd())


class ProofLogTest(unittest.TestCase):
    def test_requires_startup_after_container_start_and_fresh_successful_recheck(self):
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
            ([repeat, initial], 1),
            ([initial, line("01:06", "startup"), repeat], 1),
            ([initial, repeat, line("02:20", "recheck", "fail")], 1),
        ):
            with self.subTest(log=lines), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(LOG.audit(start, lines, now), status)


class ReleasePhaseTest(unittest.TestCase):
    STUBS = r"""
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
render_idle() {
  printf 'checked live renderer %s\n' "$1"
  [[ "$DAMAGE" != active-render ]]
}
rollback_candidate() { return 0; }
matte_pods() { return 0; }
matte_removed() { return 0; }
restart() {
  case $IDENTITY_CHANGE in
    uid) jq '.items[0].metadata.uid = "pod-b"' "$MATTE_FIXTURE" ;;
    id) jq '.items[0].status.containerStatuses[0].containerID = "containerd://b"' "$MATTE_FIXTURE" ;;
    start) jq '.items[0].status.containerStatuses[0].state.running.startedAt = "2026-11-26T03:03:04Z"' "$MATTE_FIXTURE" ;;
  esac > "$MATTE_CHANGED"
}
registration() {
  if [[ $DAMAGE == restart-after-proof ]]; then restart; fi
}
worker_started() {
  printf 'matte worker process checked\n'
  if [[ $DAMAGE == restart-during-process ]]; then restart; fi
}
kubectl() {
  case "$*" in
    '-n ai get pods -l app.kubernetes.io/name=studio-matte-worker -o json')
      if [[ -f $MATTE_CHANGED ]]; then cat "$MATTE_CHANGED"; else cat "$MATTE_FIXTURE"; fi ;;
    '-n ai get pods -l app.kubernetes.io/name=studio-render -o json')
      [[ $DAMAGE != render-list-error ]] && printf '%s\n' "$RENDER_FIXTURE" ;;
    "-n ai logs matte-fixture -c worker --timestamps --since-time=$MATTE_STARTED")
      if [[ $DAMAGE == restart-during-logs ]]; then restart; fi
      if [[ $DAMAGE == bad-proof ]]; then return 1; fi
      printf '%s\n' "$MATTE_LOGS" ;;
    '-n ai exec -i matte-fixture -c worker -- python -I -B - matte')
      printf 'matte listener checked\n'
      if [[ $DAMAGE == restart-during-listener ]]; then restart; fi ;;
    *) return 3 ;;
  esac
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
        self.matte_fixture = directory / "matte-a.json"
        self.matte_changed = directory / "matte-b.json"
        now = datetime.now(timezone.utc).replace(microsecond=0)
        started = now - timedelta(seconds=65)
        self.matte_started = started.isoformat().replace("+00:00", "Z")
        self.matte_logs = "\n".join(
            f"{(started + timedelta(seconds=elapsed)).isoformat()} " +
            json.dumps({"event": "matte_isolation", "phase": phase, "result": "pass"})
            for phase, elapsed in (("startup", 1), ("recheck", 61))
        )
        self.matte_fixture.write_text(json.dumps({"items": [{
            "metadata": {"name": "matte-fixture", "uid": "pod-a"},
            "status": {"containerStatuses": [{
                "name": "worker", "containerID": "containerd://a",
                "state": {"running": {"startedAt": self.matte_started}},
            }]},
        }]}))

    def phase(self, *phase, damage="", identity_change="id", render_pods=()):
        self.matte_changed.unlink(missing_ok=True)
        return subprocess.run(
            ["bash", "-c", self.STUBS, "fixture", *phase], text=True, capture_output=True,
            env={**os.environ, "STUDIO_OP970_RECEIPT": str(self.receipt),
                 "STUDIO_OP970_BASELINE": str(self.baseline), "DAMAGE": damage,
                 "MATTE_FIXTURE": str(self.matte_fixture), "MATTE_CHANGED": str(self.matte_changed),
                 "MATTE_STARTED": self.matte_started, "MATTE_LOGS": self.matte_logs,
                 "IDENTITY_CHANGE": identity_change, "RENDER_FIXTURE": json.dumps({"items": render_pods})},
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

    def test_postflight_fails_each_identity_change_between_proof_and_process(self):
        self.baseline.write_text("baseline\n")
        for field in ("uid", "id", "start"):
            with self.subTest(field=field):
                result = self.phase("postflight", damage="restart-after-proof", identity_change=field)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("identity changed during postflight", result.stderr)
                self.assertIn('"phase": "recheck"', result.stdout)
                self.assertNotIn("matte worker process checked", result.stdout)
                self.assertNotIn("matte listener checked", result.stdout)

    def test_postflight_fails_restart_inside_a_log_process_or_listener_read(self):
        self.baseline.write_text("baseline\n")
        for read in ("logs", "process", "listener"):
            with self.subTest(read=read):
                result = self.phase("postflight", damage=f"restart-during-{read}")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("identity changed during postflight", result.stderr)
                self.assertEqual("matte listener checked" in result.stdout, read == "listener")

    def test_postflight_requires_all_identity_fields(self):
        self.baseline.write_text("baseline\n")
        snapshot = json.loads(self.matte_fixture.read_text())
        del snapshot["items"][0]["status"]["containerStatuses"][0]["state"]
        self.matte_fixture.write_text(json.dumps(snapshot))
        result = self.phase("postflight")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("incomplete matte identity", result.stderr)
        self.assertNotIn("matte worker process checked", result.stdout)

    def test_rollback_accepts_verified_absent_pending_or_crashlooped_renderer(self):
        pending = {"status": {"phase": "Pending"}}
        crashloop = {"status": {"phase": "Running", "containerStatuses": [
            {"name": "app", "state": {"waiting": {"reason": "CrashLoopBackOff"}}}]}}
        for pods in ([], [pending], [crashloop]):
            with self.subTest(pods=pods):
                result = self.phase("rollback-preflight", render_pods=pods)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("No running render container", result.stdout)
                self.assertNotIn("checked live renderer", result.stdout)
                self.assertEqual(self.phase("admit-merge", "rollback").returncode, 0)
                self.receipt.unlink()
                self.baseline.unlink()

    def test_rollback_refuses_active_render_and_inconclusive_pod_reads(self):
        live = {"status": {"phase": "Running", "containerStatuses": [
            {"name": "app", "state": {"running": {"startedAt": "2026-11-26T03:03:04Z"}}}]}}
        unknown = {"status": {"phase": "Unknown"}}
        incomplete = {"status": {"phase": "Running"}}
        for damage, pods, checked in (
            ("active-render", [live], True),
            ("render-list-error", [], False),
            ("", [unknown], False),
            ("", [incomplete], False),
        ):
            with self.subTest(damage=damage, pods=pods):
                result = self.phase("rollback-preflight", damage=damage, render_pods=pods)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual("checked live renderer retry" in result.stdout, checked)
                self.assertFalse(self.baseline.exists())
                self.assertEqual(self.phase("admit-merge", "rollback").returncode, 1)
                self.receipt.unlink()

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
