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
from itertools import cycle
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
        self.signals = self.enterContext(patch.object(self.launch.signal, "signal"))
        self.enterContext(patch.object(self.launch.sys, "argv", ["matte-start.py", "studio-worker", "--lane", "matte"]))
        self.getaddrinfo = self.enterContext(patch.object(self.launch.socket, "getaddrinfo", side_effect=self.resolve))
        self.clients = []
        self.trace = []
        self.backend_choices = cycle(("10.42.1.8", "10.42.2.9"))
        self.dns_sockets = {}
        self.tcp_table = Mock(open=Mock(side_effect=self.socket_table))
        self.enterContext(patch.object(self.launch, "TCP_TABLE", self.tcp_table))
        self.enterContext(patch.object(self.launch.os, "fstat", side_effect=lambda fd: SimpleNamespace(st_ino=fd + 1000)))
        self.enterContext(patch.object(self.launch.socket, "socket", side_effect=self.make_socket))
        self.up = {53, 8002}
        self.errors = {}
        self.query_errors = {}
        self.cached_records = {}
        self.output = self.enterContext(redirect_stdout(io.StringIO()))

    def resolve(self, host, port, *, type):
        self.assertEqual(type, socket.SOCK_STREAM)
        return [(socket.AF_INET, type, 0, "", ("10.1.2.3", port))]

    def make_socket(self, family, kind):
        self.assertEqual((family, kind), (socket.AF_INET, socket.SOCK_STREAM))
        fd = len(self.clients) + 10
        client = MagicMock()
        client.fileno.return_value = fd
        client.getpeername.return_value = self.launch.COREDNS
        client.__enter__.return_value = client
        client.__exit__.side_effect = lambda *_: self.dns_sockets.pop(fd, None) and False
        client.connect.side_effect = lambda address: self.connect(fd, address)
        client.sendall.side_effect = lambda wire: self.dns_reply(client, wire)
        client.makefile.return_value.__enter__.return_value.read.side_effect = (
            lambda size: client.dns_response.read(size))
        client.query_count = 0
        self.clients.append(client)
        return client

    def connect(self, fd, address):
        self.trace.append(("connect", address, fd))
        port = address[1]
        if port in self.errors:
            raise self.errors[port]
        if port not in self.up:
            raise TimeoutError()
        if address == self.launch.COREDNS:
            self.dns_sockets[fd] = next(self.backend_choices)

    @staticmethod
    def table_row(address, inode, *, state="01", port=53):
        raw = int.from_bytes(socket.inet_aton(address), sys.byteorder)
        return f"0: 0100007F:FEED {raw:08X}:{port:04X} {state} 0:0 0:0 0 1000 0 {inode}\n"

    def socket_table(self):
        # A different open socket can have the same local or remote port.
        return io.StringIO("header\n" + self.table_row("10.42.50.50", 999) + "".join(
            self.table_row(backend, fd + 1000) for fd, backend in self.dns_sockets.items()))

    def dns_reply(self, client, wire):
        self.assertEqual(int.from_bytes(wire[:2], "big"), len(wire) - 2)
        query_id, flags, questions, answers, authority, extra = struct.unpack("!6H", wire[2:14])
        self.assertEqual((flags, questions, answers, authority, extra), (0x100, 1, 0, 0, 0))
        question = wire[14:]
        name = question[:-4]
        labels = []
        while name[0]:
            size = name[0]
            labels.append(name[1:size + 1].decode("ascii"))
            name = name[size + 1:]
        host = ".".join(labels) + "."
        fd = client.fileno()
        backend = self.dns_sockets[fd]
        self.trace.append(("query", host, fd))
        failure = self.query_errors.get((backend, client.query_count))
        client.query_count += 1
        if failure:
            raise failure
        address, ttl = self.cached_records.get(host, (self.launch.COREDNS[0], 5))
        reply = struct.pack("!6H", query_id, 0x8500, 1, 1, 0, 0) + question
        reply += b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, ttl, 4) + socket.inet_aton(address)
        client.dns_response = io.BytesIO(struct.pack("!H", len(reply)) + reply)

    def stop(self):
        self.signals.call_args_list[0].args[1](signal.SIGTERM, None)

    def events(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]

    def clear_output(self):
        self.output.seek(0)
        self.output.truncate(0)

    def assert_refuses_before_token(self):
        self.assertEqual(self.launch.main(), 1)
        self.token.read_text.assert_not_called()
        self.grant.assert_not_called()
        self.spawn.assert_not_called()
        self.assertFalse(self.progress.exists())
        event, = self.events()
        self.assertEqual((event["phase"], event["result"]), ("startup", "fail"))
        return event["reason"]

    def test_success_brackets_each_timeout_on_its_own_established_dns_socket(self):
        self.backend_choices = iter(("10.42.1.8", "10.42.1.8", "10.42.2.9"))
        self.assertEqual(self.launch.main(), 0)
        host = "kube-dns.kube-system.svc.cluster.local."
        self.assertEqual(self.trace, [
            ("connect", ("10.1.2.3", 8002), 10),
            ("connect", ("10.43.0.10", 53), 11),
            ("query", host, 11),
            ("connect", ("10.42.1.8", 9153), 12),
            ("query", host, 11),
            ("connect", ("10.43.0.10", 53), 13),
            ("connect", ("10.43.0.10", 53), 14),
            ("query", host, 14),
            ("connect", ("10.42.2.9", 9153), 15),
            ("query", host, 14),
        ])
        for client in self.clients:
            client.getpeername.assert_not_called()
        self.token.read_text.assert_called_once_with()
        self.grant.assert_called_once()
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0], ["studio-worker", "--lane", "matte"])
        self.assertIs(self.spawn.call_args.kwargs["start_new_session"], True)
        event, = self.events()
        self.assertRegex(event["instance"], "^[0-9a-f]{32}$")
        self.assertEqual((event["phase"], event["result"], event["backends"]),
                         ("startup", "pass", ["10.42.1.8", "10.42.2.9"]))

    def test_retired_ready_addresses_never_supply_the_forbidden_targets(self):
        # These unexpired records were sufficient for the old launcher. They
        # still exist, and any attempt to reach the retired pods would time out.
        self.cached_records = {
            "comfyui-0.comfyui.ai.svc.cluster.local.": ("10.42.8.8", 3),
            "loki-headless.observability.svc.cluster.local.": ("10.42.9.9", 4),
        }
        self.up.add(9153)  # Actual live DNS metrics are open: isolation is absent.
        self.assertIn("forbidden TCP connection to 10.42.1.8:9153 succeeded",
                      self.assert_refuses_before_token())
        self.assertEqual([target for action, target, _ in self.trace if action == "query"],
                         ["kube-dns.kube-system.svc.cluster.local."])
        self.assertEqual([target for action, target, _ in self.trace if action == "connect"],
                         [("10.1.2.3", 8002), ("10.43.0.10", 53), ("10.42.1.8", 9153)])

    def test_unidentifiable_backend_refuses_without_probing(self):
        for address, state, port, rows in (
            ("10.42.1.8", "01", 53, 0), ("10.42.1.8", "01", 53, 2),
            ("10.42.1.8", "08", 53, 1), ("10.42.1.8", "01", 9153, 1),
            ("10.43.0.10", "01", 53, 1), ("192.0.2.1", "01", 53, 1),
        ):
            with self.subTest(address=address, state=state, port=port, rows=rows):
                self.clients.clear()
                self.trace.clear()
                self.clear_output()
                self.tcp_table.open.side_effect = lambda: io.StringIO(
                    "header\n" + self.table_row("10.42.50.50", 999)
                    + self.table_row(address, 1011, state=state, port=port) * rows)
                self.assertIn("TCP table", self.assert_refuses_before_token())
                self.assertEqual(self.trace, [("connect", ("10.1.2.3", 8002), 10),
                                              ("connect", ("10.43.0.10", 53), 11)])

    def test_unreadable_socket_table_refuses_without_probing(self):
        self.tcp_table.open.side_effect = PermissionError("TCP table unavailable")
        self.assertIn("TCP table unavailable", self.assert_refuses_before_token())
        self.assertEqual([target for action, target, _ in self.trace if action == "connect"],
                         [("10.1.2.3", 8002), ("10.43.0.10", 53)])

    def test_single_backend_exhausts_a_bounded_number_of_attempts(self):
        self.backend_choices = cycle(("10.42.1.8",))
        self.assertIn("two distinct DNS backends", self.assert_refuses_before_token())
        connections = [target for action, target, _ in self.trace if action == "connect"]
        self.assertEqual(connections.count(self.launch.COREDNS), 12)
        self.assertEqual(connections.count(("10.42.1.8", 9153)), 1)

    def test_each_bracket_must_answer(self):
        for bracket, attempts in ((0, 0), (1, 1)):
            with self.subTest(bracket=bracket):
                self.clear_output()
                self.trace.clear()
                self.backend_choices = cycle(("10.42.1.8", "10.42.2.9"))
                self.query_errors = {("10.42.1.8", bracket): TimeoutError("DNS witness stopped replying")}
                self.assertIn("DNS witness stopped replying", self.assert_refuses_before_token())
                self.assertEqual([target for action, target, _ in self.trace if action == "connect"].count(
                    ("10.42.1.8", 9153)), attempts)

    def test_positives_down_is_not_isolation(self):
        self.up.clear()
        self.assertIn("TCP 8002", self.assert_refuses_before_token())
        self.clear_output()
        self.getaddrinfo.side_effect = socket.gaierror()
        self.assertIn("gaierror", self.assert_refuses_before_token())

    def test_ambiguous_listener_dns_refuses_before_connect(self):
        self.getaddrinfo.side_effect = lambda *args, **kw: self.resolve(*args, **kw) + [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.1.2.4", 8002))]
        self.assertIn("expected one DNS address", self.assert_refuses_before_token())
        self.assertFalse(self.clients)

    def test_dns_socket_timeout_does_not_count_as_a_control_timeout(self):
        self.errors[53] = TimeoutError()
        self.assertIn("TimeoutError", self.assert_refuses_before_token())

    def test_two_second_tcp_timeout(self):
        self.errors[9153] = OSError(errno.ETIMEDOUT, "kernel connect timeout")
        self.assertEqual(self.launch.main(), 0)
        self.assertEqual(len(self.clients), 5)
        for client in self.clients:
            client.settimeout.assert_called_once_with(2)

    def test_forbidden_socket_errors_besides_timeout_fail(self):
        for number in (errno.ECONNREFUSED, errno.ECONNRESET, errno.EHOSTUNREACH,
                       errno.ENETUNREACH, errno.EACCES, errno.EPERM, errno.EIO):
            with self.subTest(errno=number):
                self.clear_output()
                self.errors[9153] = OSError(number, "forbidden probe error")
                self.assertIn(f"[Errno {number}] forbidden probe error", self.assert_refuses_before_token())

    def test_initial_recheck_deadline_is_one_minute(self):
        self.grant.side_effect = lambda secret, stopping, deadline, instance: deadline
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

        def stopping_proof(phase, instance):
            result = prove(phase, instance)
            self.stop()
            return result

        with patch.object(self.launch, "prove_isolation", side_effect=stopping_proof):
            self.assertEqual(self.launch.main(), 0)
        self.token.read_text.assert_not_called()
        self.grant.assert_not_called()
        self.spawn.assert_not_called()

    def test_shutdown_during_successful_grant_wait_precedes_spawn(self):
        def stopping_grant(secret, stopping, deadline, instance):
            self.stop()
            return deadline

        self.grant.side_effect = stopping_grant
        self.assertEqual(self.launch.main(), 0)
        self.token.read_text.assert_called_once()
        self.spawn.assert_not_called()

    def test_lost_isolation_kills_worker_process_group(self):
        def wait(timeout=None):
            if timeout is not None:
                self.up.add(9153)
                raise subprocess.TimeoutExpired("studio-worker", timeout)
            return -signal.SIGKILL

        self.child.wait.side_effect = wait
        with patch.object(self.launch.os, "killpg") as kill:
            self.assertEqual(self.launch.main(), 1)
            kill.assert_called_once_with(self.child.pid, signal.SIGKILL)
        initial, repeat = self.events()
        self.assertEqual([(evt["phase"], evt["result"]) for evt in (initial, repeat)],
                         [("startup", "pass"), ("recheck", "fail")])
        self.assertEqual(initial["instance"], repeat["instance"])

    def test_instance_is_readable_from_the_live_launcher_fd_only(self):
        own_fds = Path("/proc/self/fd")
        with self.assertRaisesRegex(ValueError, "live matte launcher instance"):
            self.launch.launcher_instance(own_fds)
        captured = []

        def run(instance):
            self.assertEqual(self.launch.launcher_instance(own_fds), instance)
            captured.append(instance)
            return 0

        with patch.object(self.launch, "run_worker", side_effect=run):
            self.assertEqual(self.launch.main(), 0)
            self.assertEqual(self.launch.main(), 0)
        self.assertNotEqual(*captured)
        with self.assertRaisesRegex(ValueError, "live matte launcher instance"):
            self.launch.launcher_instance(own_fds)


@unittest.skipUnless(ACTIVE, "requires the launcher DNS reply reader")
class DNSReplyTest(unittest.TestCase):
    def setUp(self):
        self.launch = load(LAUNCHER_PATH, "matte_dns")
        self.query_id = 617
        self.question = self.launch.DNS_QUESTION
        self.record = struct.pack("!HHIH", 1, 1, 0, 4) + socket.inet_aton("10.43.0.10")

    def response(self, *, query_id=617, flags=0x8500, counts=(1, 1, 0, 0), question=None,
                 owner=b"\xc0\x0c", record=None):
        return (struct.pack("!6H", query_id, flags, *counts)
                + (self.question if question is None else question)
                + owner + (self.record if record is None else record))

    def read(self, response, declared=None):
        prefix = struct.pack("!H", len(response) if declared is None else declared)
        self.launch.read_dns_reply(io.BytesIO(prefix + response), self.query_id)

    def test_successful_reply_with_either_owner_encoding_can_have_zero_ttl(self):
        self.read(self.response())
        self.read(self.response(owner=self.question[:-4]))

    def test_wrong_size_and_incomplete_reads(self):
        response = self.response()
        with self.assertRaisesRegex(ValueError, "invalid DNS response size"):
            self.launch.read_dns_reply(io.BytesIO(b"\x30"), self.query_id)
        for length in (1, 5, 513):
            with self.subTest(length=length), self.assertRaisesRegex(ValueError, "invalid DNS response size"):
                self.read(response, declared=length)
        with self.assertRaisesRegex(ValueError, "incomplete DNS response"):
            self.read(response, declared=len(response) + 1)

    def test_wrong_transaction_flags_counts_and_echoed_question(self):
        for change in (
            {"query_id": 618}, {"flags": 0x0500}, {"flags": 0x8700}, {"flags": 0x8D00},
            {"flags": 0x8503}, {"counts": (1, 2, 0, 0)}, {"question": b"\x08wrongdns" + self.question[9:]},
        ):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "invalid DNS response header or question"):
                self.read(self.response(**change))

    def test_wrong_answer_owner_or_size(self):
        for response in (self.response(owner=b"\xc0\x0d"), self.response() + b"\0"):
            with self.subTest(response=response), self.assertRaisesRegex(ValueError, "DNS answer owner or size"):
                self.read(response)

    def test_wrong_record_type_class_length_or_address(self):
        for kind, dns_class, length, address in (
            (28, 1, 4, "10.43.0.10"), (1, 3, 4, "10.43.0.10"), (1, 1, 5, "10.43.0.10"), (1, 1, 4, "10.42.8.8"),
        ):
            record = struct.pack("!HHIH", kind, dns_class, 5, length) + socket.inet_aton(address)
            with self.subTest(record=record), self.assertRaisesRegex(ValueError, "DNS Service A record"):
                self.read(self.response(record=record))


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
            self.assertIsNone(self.launch.wait_for_grant("fixture", lambda: False, 60, "fixture-instance"))
            proof.assert_called_once_with("recheck", "fixture-instance")
        self.client.post.assert_called_once()

    def test_waits_for_the_exact_validation_error(self):
        self.clock.return_value = 0
        self.client.post.side_effect = [Mock(status_code=422, **{"json.return_value": response}) for response in (
            [], {"error": None}, {"error": {"code": "schema.unknown_field", "path": "/other"}},
            {"error": {"code": "schema.unknown_field", "path": "/registration_probe"}},
        )]
        self.assertEqual(self.launch.wait_for_grant("fixture", lambda: False, 60, "fixture-instance"), 60)
        self.assertEqual(self.client.post.call_count, 4)
        self.assertTrue(all(call.kwargs == {"json": {"lane": "matte", "registration_probe": True}}
                            for call in self.client.post.call_args_list))


    def test_same_schema_path_with_wrong_code_keeps_waiting(self):
        self.clock.return_value = 0
        self.client.post.return_value = Mock(status_code=422, **{"json.return_value": {
            "error": {"code": "schema.wrong_type", "path": "/registration_probe"},
        }})
        stopping = Mock(side_effect=[False, True])
        self.assertIsNone(self.launch.wait_for_grant("fixture", stopping, 60, "fixture-instance"))
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

    def installed_snapshot(self):
        pods, endpoints = zip(*(dns_pod(index) for index in (1, 2)))
        return [
            {"items": [self.current, self.policy]}, {"items": []},
            cilium_config(), {"items": list(pods)}, {"items": list(endpoints)},
            {"metadata": {"labels": {"kubernetes.io/metadata.name": "ai"}}},
        ]

    def test_projected_policy_rejects_additional_native_and_cilium_rules(self):
        snapshot = self.installed_snapshot()
        # Egress-only rules on DNS and namespaced ingress elsewhere do not select DNS ingress.
        snapshot[0]["items"].extend([
            dns_ingress_policy("NetworkPolicy", "kube-system", policyTypes=["Egress"]),
            dns_ingress_policy("CiliumNetworkPolicy", "ai", ingressDeny=[{}]),
        ])
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
            for key, changed_value, message in (
                ("policy-deny-response", "icmp", "must silently deny"),
                ("service-no-backend-response", "drop", "must silently deny"),
                ("kube-proxy-replacement", "false", "socket load balancing"),
                ("bpf-lb-sock-hostns-only", "true", "socket load balancing"),
            ):
                with self.subTest(key=key):
                    changed = copy.deepcopy(snapshot)
                    changed[2]["data"][key] = changed_value
                    with self.assertRaisesRegex(ValueError, message):
                        check_installed_policies(changed, stage_repo=Path.cwd())


    def test_installed_ingress_rules_on_dns_controls_fail_closed(self):
        for kind, namespace, rules in (
            ("NetworkPolicy", "kube-system", {}),
            ("NetworkPolicy", "kube-system", {"policyTypes": []}),
            ("NetworkPolicy", "kube-system", {"policyTypes": ["Ingress"]}),
            ("CiliumNetworkPolicy", "kube-system", {"ingress": [{}]}),
            ("CiliumNetworkPolicy", "kube-system", {"ingressDeny": [{"fromEntities": ["all"]}]}),
            ("CiliumClusterwideNetworkPolicy", None, {"enableDefaultDeny": {"ingress": True}}),
        ):
            with self.subTest(kind=kind, rules=rules), redirect_stdout(io.StringIO()):
                snapshot = self.installed_snapshot()
                document = dns_ingress_policy(kind, namespace, **rules)
                # Select only the second replica using its actual Cilium identity.
                selector = document["spec"].get("endpointSelector")
                if selector:
                    selector["matchLabels"]["k8s:fixture-id"] = "coredns-2"
                name = "coredns-2" if selector else "coredns-1"
                snapshot[int(namespace is None)]["items"].append(document)
                with self.assertRaisesRegex(ValueError, f"ingress policy .* selects DNS control kube-system/{name}"):
                    check_installed_policies(snapshot, stage_repo=Path.cwd())

    def test_control_requires_ready_pod_and_owned_cilium_identity_in_dns_namespace(self):
        for obj_index, section, key, value, reason in (
            (3, "status", "conditions", [], "Ready DNS pod"),
            (3, "metadata", "namespace", "ai", "Ready DNS pod"),
            (3, "metadata", "deletionTimestamp", "2026-11-26T03:01:00Z", "Ready DNS pod"),
            (4, "metadata", "namespace", "ai", "Ready DNS pod"),
            (4, "metadata", "deletionTimestamp", "2026-11-26T03:01:00Z", "Ready DNS pod"),
            (4, "metadata", "ownerReferences", [{"kind": "Pod", "uid": "other"}], "missing installed Cilium endpoint"),
            (4, "status", "identity", {"labels": []}, "missing installed Cilium identity labels"),
            (3, "status", "podIP", "10.43.0.10", "distinct DNS pod address"),
            (3, "status", "podIP", "10.42.1.2", "distinct DNS pod address"),
        ):
            with self.subTest(obj=obj_index, key=key), redirect_stdout(io.StringIO()):
                snapshot = self.installed_snapshot()
                snapshot[obj_index]["items"][0][section][key] = value
                with self.assertRaisesRegex(ValueError, reason):
                    check_installed_policies(snapshot, stage_repo=Path.cwd())

    def test_requires_multiple_ready_dns_pods(self):
        snapshot = self.installed_snapshot()
        snapshot[3]["items"].pop()
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "at least two distinct"):
            check_installed_policies(snapshot, stage_repo=Path.cwd())


def dns_pod(index):
    name = f"coredns-{index}"
    uid = f"{name}-uid"
    pod = {"kind": "Pod", "metadata": {"namespace": "kube-system", "name": name, "uid": uid,
                                       "labels": {"k8s-app": "kube-dns"}},
           "status": {"podIP": f"10.42.1.{index}", "conditions": [{"type": "Ready", "status": "True"}]}}
    endpoint = {"kind": "CiliumEndpoint", "metadata": {"namespace": "kube-system", "name": name,
                                                      "ownerReferences": [{"kind": "Pod", "uid": uid}]},
                "status": {"identity": {"labels": [
                    "k8s:k8s-app=kube-dns", "k8s:io.kubernetes.pod.namespace=kube-system", f"k8s:fixture-id={name}",
                ]}}}
    return pod, endpoint


def dns_ingress_policy(kind, namespace, **rules):
    selector = "podSelector" if kind == "NetworkPolicy" else "endpointSelector"
    metadata = {"name": "dns-control-policy"}
    if namespace:
        metadata["namespace"] = namespace
    return {"kind": kind, "metadata": metadata,
            "spec": {selector: {"matchLabels": {"k8s-app": "kube-dns"}}, **rules}}


def cilium_config():
    return {"data": {"enable-policy": "default", "cluster-name": "haynes-ops", "kube-proxy-replacement": "true",
                     "policy-deny-response": "none", "service-no-backend-response": "reject"}}


INSTANCE = "a" * 32
REPLACEMENT_INSTANCE = "b" * 32


class ProofLogTest(unittest.TestCase):
    def test_requires_startup_after_container_start_and_fresh_successful_recheck(self):
        def line(time, phase, result="pass", instance=INSTANCE):
            return f"2026-11-26T00:{time}Z " + json.dumps(
                {"event": "matte_isolation", "phase": phase, "result": result, "instance": instance})

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
            ([line("01:05", "startup", instance=None), repeat], 1),
            ([initial, line("02:10", "recheck", instance=REPLACEMENT_INSTANCE)], 1),
            ([initial, repeat, line("02:20", "recheck", instance=REPLACEMENT_INSTANCE)], 1),
        ):
            with self.subTest(log=lines), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(LOG.audit(start, lines, now), status)

    def test_recheck_before_startup_fails_at_the_order_guard(self):
        start = datetime.fromisoformat("2026-11-26T00:01:00Z")
        payload = json.dumps({"event": "matte_isolation", "phase": "recheck", "result": "pass", "instance": INSTANCE})
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error:
            self.assertEqual(LOG.audit(start, [f"2026-11-26T00:01:05Z {payload}"], start), 1)
        self.assertIn("logs out of order", error.getvalue())


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
if [[ -z $POLICY_FIXTURE ]]; then
  installed_policies() { [[ "$1" != "$DAMAGE" ]]; }
fi
allocation_present() { return 0; }
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
sleep() {
  [[ $DAMAGE == restart-between-log-reads ]] || return 1
  restart
}
kubectl() {
  local check runtime_instance
  case "$*" in
    'get networkpolicies.networking.k8s.io,ciliumnetworkpolicies.cilium.io -A -o json')
      jq '.[0]' <<< "$POLICY_FIXTURE"; return ;;
    'get ciliumclusterwidenetworkpolicies.cilium.io -o json')
      jq '.[1]' <<< "$POLICY_FIXTURE"; return ;;
    '-n kube-system get configmap cilium-config -o json')
      jq '.[2]' <<< "$POLICY_FIXTURE"; return ;;
    '-n kube-system get pods -l k8s-app=kube-dns -o json')
      jq '.[3]' <<< "$POLICY_FIXTURE"; return ;;
    '-n kube-system get ciliumendpoints.cilium.io -o json')
      jq '.[4]' <<< "$POLICY_FIXTURE"; return ;;
    '-n ai get ciliumendpoints.cilium.io -o json'|'-n ai get pods -l app.kubernetes.io/name=studio-api -o json')
      # Reject the DNS policy before examining the Studio workloads.
      printf '{"items":[]}\n'; return ;;
    '-n ai exec deploy/studio-render -c app -- python -I -B -c '*)
      printf 'unrestricted metrics checked\n'; return ;;
  esac
  case "$*" in
    '-n ai get pods -l app.kubernetes.io/name=studio-matte-worker -o json')
      if [[ -f $MATTE_CHANGED ]]; then cat "$MATTE_CHANGED"; else cat "$MATTE_FIXTURE"; fi ;;
    '-n ai get pods -l app.kubernetes.io/name=studio-render -o json')
      [[ $DAMAGE != render-list-error ]] && printf '%s\n' "$RENDER_FIXTURE" ;;
    "-n ai logs matte-fixture -c worker --timestamps --since-time=$MATTE_STARTED")
      printf 'matte log read\n' >&2
      if [[ $DAMAGE == restart-during-logs ]]; then restart; fi
      if [[ $DAMAGE == bad-proof ]]; then return 1; fi
      if [[ $DAMAGE == restart-between-log-reads ]]; then
        printf '%s\n' "${MATTE_LOGS%%$'\n'*}"
      else
        printf '%s\n' "$MATTE_LOGS"
      fi ;;
    '-n ai exec -i matte-fixture -c worker -- python -I -B -c '*)
      shift 12
      check=process
      if [[ ${3:-} == matte ]]; then check=listener; fi
      if [[ $DAMAGE == restart-during-$check ]]; then restart; fi
      runtime_instance=$INSTANCE
      if [[ $DAMAGE == unpublished-restart-$check ]]; then runtime_instance=$REPLACEMENT_INSTANCE; fi
      # Execute the real exec wrapper, replacing only the remote procfs lookup
      # and the body that would inspect the worker or send listener requests.
      RUNTIME_INSTANCE=$runtime_instance python3 -B -c '
import os
import runpy
import sys

wrapper, *args = sys.argv[1:]
runpy.run_path = lambda _: {"launcher_instance": lambda: os.environ["RUNTIME_INSTANCE"]}
sys.argv = ["-c", *args]
exec(wrapper, {"__name__": "__main__"})
' "$@" <<< "print('matte worker $check checked')" ;;
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
            json.dumps({"event": "matte_isolation", "phase": phase, "result": "pass", "instance": INSTANCE})
            for phase, elapsed in (("startup", 1), ("recheck", 61))
        )
        self.matte_fixture.write_text(json.dumps({"items": [{
            "metadata": {"name": "matte-fixture", "uid": "pod-a"},
            "status": {"containerStatuses": [{
                "name": "worker", "containerID": "containerd://a",
                "state": {"running": {"startedAt": self.matte_started}},
            }]},
        }]}))

    def phase(self, *phase, damage="", identity_change="id", render_pods=(), policy_fixture=None):
        self.matte_changed.unlink(missing_ok=True)
        return subprocess.run(
            ["bash", "-c", self.STUBS, "fixture", *phase], text=True, capture_output=True,
            env={**os.environ, "STUDIO_OP970_RECEIPT": str(self.receipt),
                 "STUDIO_OP970_BASELINE": str(self.baseline), "DAMAGE": damage,
                 "MATTE_FIXTURE": str(self.matte_fixture), "MATTE_CHANGED": str(self.matte_changed),
                 "MATTE_STARTED": self.matte_started, "MATTE_LOGS": self.matte_logs,
                 "IDENTITY_CHANGE": identity_change, "RENDER_FIXTURE": json.dumps({"items": render_pods}),
                 "INSTANCE": INSTANCE, "REPLACEMENT_INSTANCE": REPLACEMENT_INSTANCE,
                 "POLICY_FIXTURE": json.dumps(policy_fixture) if policy_fixture is not None else "",
                 "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}"},
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
                self.assertEqual("matte worker listener checked" in result.stdout, not damage)
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
                self.assertNotIn("matte worker listener checked", result.stdout)

    def test_postflight_fails_restart_inside_a_log_process_or_listener_read(self):
        self.baseline.write_text("baseline\n")
        for read in ("logs", "process", "listener"):
            with self.subTest(read=read):
                result = self.phase("postflight", damage=f"restart-during-{read}")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("identity changed during postflight", result.stderr)
                self.assertEqual("matte worker listener checked" in result.stdout, read == "listener")

    @unittest.skipUnless(ACTIVE, "requires the installed-policy parser")
    def test_postflight_dns_ingress_blocks_process_and_listener_evidence(self):
        self.baseline.write_text("baseline\n")
        pods, endpoints = zip(*(dns_pod(index) for index in (1, 2)))
        deny = dns_ingress_policy("CiliumNetworkPolicy", "kube-system", ingressDeny=[{
            "fromEntities": ["all"], "toPorts": [{"ports": [{"port": "9153", "protocol": "TCP"}]}],
        }])
        fixture = [
            {"items": [deny]}, {"items": []}, cilium_config(),
            {"items": pods}, {"items": endpoints},
        ]
        result = self.phase("postflight", policy_fixture=fixture)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("ingress policy dns-control-policy selects DNS control", result.stderr)
        for evidence in ("unrestricted metrics checked", "matte log read",
                         "matte worker process checked", "matte worker listener checked"):
            self.assertNotIn(evidence, result.stdout + result.stderr)

    def test_postflight_detects_unpublished_restarts_in_both_execs(self):
        self.baseline.write_text("baseline\n")
        for check in ("process", "listener"):
            with self.subTest(check=check):
                result = self.phase("postflight", damage=f"unpublished-restart-{check}")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("exec reached a different matte launcher instance", result.stderr)
                self.assertIn('"phase": "recheck"', result.stdout)
                self.assertEqual("matte worker process checked" in result.stdout, check == "listener")
                self.assertNotIn("matte worker listener checked", result.stdout)
                self.assertFalse(self.matte_changed.exists())

    def test_checks_identity_on_both_sides_of_each_log_read(self):
        self.baseline.write_text("baseline\n")
        for damage in ("restart-between-log-reads", "restart-during-logs"):
            with self.subTest(damage=damage):
                result = self.phase("postflight", damage=damage)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("identity changed during postflight", result.stderr)
                self.assertEqual(result.stderr.count("matte log read"), 1)
                self.assertNotIn('"phase": "recheck"', result.stdout)

    def test_requires_one_nonterminating_pod_with_exactly_one_worker_status(self):
        self.baseline.write_text("baseline\n")
        snapshot = json.loads(self.matte_fixture.read_text())
        pod, = snapshot["items"]
        worker, = pod["status"]["containerStatuses"]
        matting = {**worker, "name": "matting", "containerID": "containerd://sidecar"}
        without_worker = {**pod, "status": {"containerStatuses": [matting]}}
        duplicate_worker = {**pod, "status": {"containerStatuses": [worker, worker]}}
        deleting_pod = {**pod, "metadata": {**pod["metadata"], "deletionTimestamp": self.matte_started}}
        for items in ([], [pod, pod], [deleting_pod], [without_worker], [duplicate_worker]):
            with self.subTest(items=items):
                self.matte_fixture.write_text(json.dumps({"items": items}))
                result = self.phase("postflight")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertNotIn("matte log read", result.stderr)
        pod["status"]["containerStatuses"].insert(0, matting)
        self.matte_fixture.write_text(json.dumps({"items": [pod]}))
        result = self.phase("postflight")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("worker container containerd://a", result.stdout)

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
            ("", {}, False),
            ("", None, False),
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
