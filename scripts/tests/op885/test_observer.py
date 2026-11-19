"""Serial observer tests with scripted Loki replies and real curl on loopback."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import observe as observer


S = observer.SECOND
EPOCH = 1_800_000_000 * S
EXPR = json.loads(observer.RULE.read_text())["groups"][0]["rules"][0]["expr"]


def reply(kind, entries, **fields):
    return json.dumps({
        "status": "success", "data": {"resultType": kind, "result": entries}, **fields,
    }).encode()


def entry(ns, message="private synthetic line"):
    return [str(ns), message]


def stream(*entries, **labels):
    return {"stream": observer.LABELS | labels, "values": list(entries)}


def empty(path, _params, _index):
    return reply("streams" if path == "query_range" else "vector", [])


class FakeRuntime:
    def __init__(self):
        self.elapsed = 0
        self.offset = 0
        self.next_wait_extra = 0
        self.stopped = False

    def now(self):
        return self.elapsed

    def wall(self):
        return EPOCH + self.elapsed + self.offset

    def wait(self, ns):
        self.elapsed += ns + self.next_wait_extra
        self.next_wait_extra = 0


class FakeLoki:
    def __init__(self, runtime, handler):
        self.runtime = runtime
        self.handler = handler
        self.calls = []
        self.requests = 0
        self.received = 0

    def get(self, path, params, deadline):
        self.calls.append((self.runtime.now(), deadline, path, params))
        index = self.requests
        self.requests += 1
        self.runtime.elapsed += 2 * S
        body = self.handler(path, params, index)
        self.received += len(body)
        return body


class ObservationTests(unittest.TestCase):
    def simulate(self, handler=empty, *, seconds=120, approval=None, runtime=None):
        runtime = runtime or FakeRuntime()
        loki = FakeLoki(runtime, handler)
        output = io.BytesIO()
        with patch.object(observer, "DURATION", seconds * S):
            reason = observer.observe(loki, observer.Ledger(output), EXPR, runtime, approval)
        return reason, [json.loads(line) for line in output.getvalue().splitlines()], loki.calls

    def test_overlapping_poll_brackets_and_dedup(self):
        old = entry(EPOCH - 30 * S)
        late = entry(EPOCH - 10 * S, "late synthetic line")
        fresh = entry(EPOCH + 8 * S + 117)
        another = entry(EPOCH + 8 * S + 117, "another synthetic line")

        def answer(path, params, index):
            if path == "query":
                self.assertEqual(params["query"], EXPR)
                return empty(path, params, index)
            self.assertIn(params["query"], EXPR)
            self.assertEqual(params["limit"], 128)
            self.assertEqual(params["end"] - params["start"], 633 * S)
            if index == 0:
                return reply("streams", [stream(old)])
            return reply("streams", [
                stream(old, late, fresh, fresh, another), stream(fresh, level="warning"),
            ])

        reason, records, calls = self.simulate(answer)
        self.assertEqual(reason, "expired")
        self.assertEqual([call[0] // S for call in calls], [0, 15, 30, 45, 60, 75, 90, 105])
        instant_times = [call[3]["time"] for call in calls if call[2] == "query"]
        self.assertEqual(instant_times, [EPOCH + 45 * S, EPOCH + 105 * S])
        observations = [record for record in records if record["type"] == "first_visibility"]
        self.assertEqual(len(observations), 5)
        self.assertEqual(len({record["event_id"] for record in observations}), 5)
        self.assertIsNone(observations[0]["lower_observed_ns"])
        self.assertFalse(observations[0]["fresh"])
        for event in observations[1:]:
            self.assertEqual(event["lower_observed_ns"], EPOCH)
            self.assertEqual(event["upper_observed_ns"], EPOCH + 17 * S)
            self.assertEqual(event["coverage"], "bracketed")
        self.assertEqual(observations[2]["upper_delay_ns"], 9 * S - 117)
        ranges = [record for record in records if record["type"] == "range"]
        self.assertEqual([record["duplicates"] for record in ranges], [0, 2, 6, 6, 6, 6])
        self.assertNotIn("synthetic line", repr(records))

    def test_old_event_outside_previous_query_cannot_claim_a_lower_bound(self):
        output = io.BytesIO()
        state = observer.Observation(observer.Ledger(output), EPOCH)
        record = {
            "type": "range", "slot": 0, "scheduled_ns": EPOCH,
            "request_started_ns": EPOCH, "response_completed_ns": EPOCH + S,
        }
        state.range(record, reply("streams", []))
        # Simulate a discontinuity at the exact exclusive end of the prior window.
        later = EPOCH + observer.RANGE_END_ALLOWANCE
        state.range(record | {
            "slot": 3, "scheduled_ns": EPOCH + 45 * S,
            "request_started_ns": EPOCH + 45 * S, "response_completed_ns": EPOCH + 46 * S,
        }, reply("streams", [stream(entry(later))]))
        event = json.loads(output.getvalue().splitlines()[-1])
        self.assertIsNone(event["lower_observed_ns"])
        self.assertEqual(event["missing_lower_bound"], "outside_previous_window")

    def test_truncation_and_partial_response_break_coverage(self):
        delayed = entry(EPOCH + 5 * S)

        def answer(path, params, index):
            if index == 1:
                return reply("streams", [stream(*[delayed] * observer.ENTRY_LIMIT)])
            if index == 2:
                return reply("streams", [stream(delayed)])
            if index == 3:
                return reply("vector", [], warnings=["shard data unavailable"])
            if index == 4:
                return reply("streams", [stream(delayed, entry(EPOCH + 7 * S))])
            return empty(path, params, index)

        _, records, _ = self.simulate(answer)
        gaps = [record for record in records if record["type"] == "gap"]
        self.assertEqual([gap["reason"] for gap in gaps], [
            "possible_entry_truncation", "invalid_or_partial_loki_response",
        ])
        events = [record for record in records if record["type"] == "first_visibility"]
        self.assertEqual([event["slot"] for event in events], [2, 4])
        self.assertEqual([event["missing_lower_bound"] for event in events], ["prior_gap"] * 2)
        self.assertTrue(all(event["coverage"] == "inconclusive" for event in events))
        self.assertEqual(records[-1]["fresh_unbracketed_events"], 2)
        self.assertEqual(records[-1]["coverage"], "inconclusive")

    def test_match_aging_out_and_quiet_stream_leave_health_unknown(self):
        old_event = EPOCH + 40 * S
        header = "rcu: INFO: rcu_preempt detected expedited stalls on CPUs/tasks"

        def answer(path, params, index):
            if path == "query":
                tick = params["time"]
                if tick - old_event < 300 * S:
                    return reply("vector", [{"metric": observer.TARGET, "value": [tick // S, "1"]}])
            elif index >= 4:
                return reply("streams", [stream(entry(old_event, header))])
            return empty(path, params, index)

        _, records, _ = self.simulate(answer, seconds=360)
        evaluations = [record for record in records if record["type"] == "evaluation"]
        self.assertEqual([row["result"] for row in evaluations], ["recent_match"] * 5 + ["no_recent_match"])
        self.assertTrue(evaluations[-1]["previous_match_now_absent"])
        self.assertEqual(records[-3]["returned"], 1)
        self.assertIn(f'|~ "{observer.RCU_HEADER.pattern}" [5m]', EXPR)
        self.assertTrue(next(row for row in records if row["type"] == "first_visibility")["rcu_header"])
        self.assertNotIn(header, repr(records))
        self.assertTrue(all(row["node_health"] == "unknown" for row in evaluations))
        self.assertEqual(records[-1]["node_health"], "unknown")
        self.assertEqual(records[-1]["coverage"], "inconclusive")

    def test_very_late_delivery_remains_visible_to_range_poll(self):
        old_event = EPOCH + S

        def answer(path, params, index):
            if path == "query_range" and index * 15 >= 330 and params["start"] <= old_event < params["end"]:
                return reply("streams", [stream(entry(old_event))])
            return empty(path, params, index)

        _, records, _ = self.simulate(answer, seconds=630)
        events = [row for row in records if row["type"] == "first_visibility"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["lower_observed_ns"], EPOCH + 315 * S)
        self.assertEqual(events[0]["upper_observed_ns"], EPOCH + 332 * S)
        self.assertEqual(events[0]["upper_delay_ns"], 331 * S)
        self.assertTrue(all(row["result"] == "no_recent_match" for row in records if row["type"] == "evaluation"))

    def test_three_consecutive_failures_stop_and_success_resets(self):
        def answer(path, params, index):
            if index != 2:
                raise observer.CycleError("request_timeout")
            return empty(path, params, index)

        reason, records, calls = self.simulate(answer)
        self.assertEqual(reason, "three_failed_cycles")
        self.assertEqual(len(calls), 6)
        self.assertEqual(records[-1]["consecutive_failed_cycles"], 3)
        self.assertEqual(records[-1]["gap_slots"], 5)
        self.assertEqual(records[-1]["coverage"], "inconclusive")

    def test_schedule_gap_skips_slots_without_catch_up(self):
        runtime = FakeRuntime()
        runtime.next_wait_extra = 22 * S
        reason, records, calls = self.simulate(runtime=runtime)
        self.assertEqual(reason, "expired")
        self.assertEqual([call[0] // S for call in calls], [0, 45, 60, 75, 90, 105])
        self.assertEqual([row["reason"] for row in records if row["type"] == "gap"], [
            "missed_schedule", "late_start",
        ])
        self.assertEqual(records[-1]["coverage"], "inconclusive")

    def test_many_missed_slots_stop_before_another_request(self):
        runtime = FakeRuntime()
        runtime.next_wait_extra = 63 * S
        reason, records, calls = self.simulate(runtime=runtime)
        self.assertEqual(reason, "three_failed_cycles")
        self.assertEqual(len(calls), 1)
        self.assertEqual(records[-2]["slots"], 4)

    def test_clock_step_stops_and_future_dated_event_is_inconclusive(self):
        runtime = FakeRuntime()

        def answer(path, params, index):
            if index == 0:
                return reply("streams", [stream(entry(EPOCH + 10 * S))])
            runtime.offset += 5 * S
            return empty(path, params, index)

        reason, records, calls = self.simulate(answer, runtime=runtime)
        self.assertEqual(reason, "clock_step")
        self.assertEqual(len(calls), 2)
        self.assertEqual(records[2]["missing_lower_bound"], "event_clock_ahead")
        self.assertIsNone(records[2]["upper_delay_ns"])
        self.assertEqual(records[-1]["coverage"], "inconclusive")

    def test_48_hour_hard_expiry_and_request_budget(self):
        reason, records, calls = self.simulate(seconds=48 * 3600)
        self.assertEqual(reason, "expired")
        self.assertEqual(len(calls), observer.MAX_REQUESTS)
        deadline = 48 * 3600 * S
        self.assertTrue(all(start < deadline == cap for start, cap, _, _ in calls))
        self.assertEqual(records[-1]["ended_ns"], EPOCH + deadline)
        self.assertEqual(records[-1]["complete_cycles"], observer.MAX_REQUESTS)
        self.assertEqual(records[-1]["coverage"], "inconclusive")

    def test_complete_schedule_and_enough_fresh_brackets_report_sampled_windows(self):
        def answer(path, params, index):
            if index == 1:
                return reply("streams", [stream(*(entry(EPOCH + i * S // 2) for i in range(1, 21)))])
            return empty(path, params, index)

        reason, records, _ = self.simulate(answer)
        self.assertEqual(reason, "expired")
        self.assertEqual(records[-1]["fresh_bracketed_events"], 20)
        self.assertEqual(records[-1]["coverage"], "sampled_query_windows")
        self.assertEqual(records[-1]["node_health"], "unknown")

    def test_approval_cutoff_can_shorten_observation(self):
        reason, records, calls = self.simulate(approval=EPOCH + 18 * S)
        self.assertEqual(reason, "expired")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[1] == 18 * S for call in calls))
        self.assertEqual(records[-1]["ended_ns"], EPOCH + 18 * S)
        self.assertEqual(records[-1]["coverage"], "inconclusive")

    def test_output_budget_reserves_terminal_record(self):
        with patch.object(observer, "MAX_OUTPUT_BYTES", 1300):
            reason, records, calls = self.simulate()
        self.assertEqual(reason, "output_budget")
        self.assertEqual(len(calls), 1)
        self.assertEqual([record["type"] for record in records], ["start", "end"])
        self.assertEqual(records[-1]["coverage"], "inconclusive")

    def test_range_rejects_wrong_target_and_accepts_structured_metadata(self):
        with self.assertRaisesRegex(observer.CycleError, "invalid_range_entry"):
            observer.parse_range(reply("streams", [stream(entry(EPOCH), node="other")]), EPOCH, EPOCH + 1)
        log = entry(EPOCH) + [{"facility": "kern"}]
        count, entries = observer.parse_range(reply("streams", [stream(log, log)]), EPOCH, EPOCH + 1)
        self.assertEqual((count, list(entries.values())), (2, [(EPOCH, False)]))


@contextmanager
def loopback(respond):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            try:
                respond(self)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join()


def send(handler, body, status=200, length=None, headers=None):
    handler.send_response(status)
    handler.send_header("Content-Length", str(len(body) if length is None else length))
    handler.send_header("Connection", "close")
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


class TransportTests(unittest.TestCase):
    def get(self, client):
        return client.get("query_range", {"query": observer.SELECTOR}, client.runtime.now() + 10 * S)

    def test_real_curl_is_bounded_by_rolling_and_total_request_limits(self):
        body = reply("streams", [])
        with loopback(lambda handler: send(handler, body)) as (url, requests):
            client = observer.Loki(url, observer.Runtime())
            for _ in range(5):
                self.assertEqual(self.get(client), body)
            with self.assertRaisesRegex(observer.CycleError, "rate_guard"):
                self.get(client)
            with patch.object(observer, "MAX_REQUESTS", 5):
                with self.assertRaisesRegex(observer.StopRun, "request_budget"):
                    self.get(client)
        self.assertEqual(len(requests), 5)
        path, headers = requests[0]
        self.assertEqual(urlsplit(path).path, "/loki/api/v1/query_range")
        self.assertEqual(parse_qs(urlsplit(path).query), {"query": [observer.SELECTOR]})
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("X-Scope-OrgID", headers)

    def test_http_denial_and_redirects_stop_immediately(self):
        expected = [(403, "http_403"), (429, "http_429"), (302, "http_302")]
        for status, reason in expected:
            with self.subTest(status=status):
                def answer(handler):
                    send(handler, b"", status, headers={"Location": "/elsewhere"})

                with loopback(answer) as (url, requests):
                    client = observer.Loki(url, observer.Runtime())
                    with self.assertRaisesRegex(observer.StopRun, reason):
                        self.get(client)
                    self.assertEqual(len(requests), 1)

    def test_content_length_and_truncated_responses(self):
        expected = [(1_000_000, "response_limit"), (512, "incomplete_http_response")]
        for length, reason in expected:
            with self.subTest(length=length), loopback(lambda h: send(h, b"{}", length=length)) as (url, _):
                client = observer.Loki(url, observer.Runtime())
                with self.assertRaisesRegex(observer.CycleError, reason):
                    self.get(client)

    def test_chunked_response_cannot_exceed_byte_caps(self):
        def chunked(handler):
            handler.send_response(200)
            handler.send_header("Transfer-Encoding", "chunked")
            handler.send_header("Connection", "close")
            handler.end_headers()
            for _ in range(6):
                handler.wfile.write(b"4000\r\n" + b"x" * 16384 + b"\r\n")
            handler.wfile.write(b"0\r\n\r\n")

        with loopback(chunked) as (url, _):
            client = observer.Loki(url, observer.Runtime())
            with self.assertRaisesRegex(observer.CycleError, "response_limit"):
                self.get(client)
            self.assertEqual(client.received, observer.MAX_RESPONSE_BYTES)
            with patch.object(observer, "MAX_RECEIVED_BYTES", client.received + 512):
                with self.assertRaisesRegex(observer.StopRun, "response_budget"):
                    self.get(client)
                self.assertEqual(client.received, observer.MAX_RECEIVED_BYTES)

    def test_total_timeout_even_when_peer_keeps_sending_data(self):
        def trickle(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", "100")
            handler.end_headers()
            for _ in range(25):
                handler.wfile.write(b"x")
                time.sleep(0.05)

        with loopback(trickle) as (url, _), patch.object(observer, "REQUEST_TIME", S // 4):
            runtime = observer.Runtime()
            client = observer.Loki(url, runtime)
            started = runtime.now()
            with self.assertRaisesRegex(observer.CycleError, "request_timeout"):
                self.get(client)
            self.assertLess(runtime.now() - started, S)

    def test_cli_loopback_cancellation_and_exclusive_local_output(self):
        script = Path(observer.__file__)
        with loopback(lambda h: send(h, reply("streams", []))) as (url, requests):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "trial.jsonl"
                command = [sys.executable, "-I", "-B", str(script), "--loopback-url", url, "--output", str(output)]
                with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
                    try:
                        until = time.monotonic() + 5
                        while time.monotonic() < until and child.poll() is None:
                            if output.exists() and b'"type":"range"' in output.read_bytes():
                                break
                            time.sleep(0.02)
                        child.send_signal(signal.SIGTERM)
                        stdout, stderr = child.communicate(timeout=3)
                        self.assertEqual((child.returncode, stdout, stderr), (130, b"", b""))
                    finally:
                        if child.poll() is None:
                            child.kill()
                rows = [json.loads(line) for line in output.read_text().splitlines()]
                self.assertEqual([row["type"] for row in rows], ["start", "range", "end"])
                self.assertEqual(rows[-1]["reason"], "cancelled")
                self.assertEqual(rows[-1]["node_health"], "unknown")
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                refused = subprocess.run(command, capture_output=True, timeout=3)
                self.assertEqual(refused.returncode, 1)
        self.assertEqual(len(requests), 1)

    def test_cli_refuses_non_loopback_url_and_unscoped_approval(self):
        for arguments in (
            ["--loopback-url", observer.LOKI],
            ["--loopback-url", "http://127.0.0.1:8000/redirect"],
            ["--approved-loki"],
            ["--approved-loki", "--expires-at", "2020-01-01T00:00:00Z"],
        ):
            with self.subTest(arguments=arguments), patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as raised:
                    observer.main(["--output", "unused.jsonl", *arguments])
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
