#!/usr/bin/env python3
"""Record bounded Loki query observations for main/talosm03 in a local JSONL file."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time
from urllib.parse import urlencode


SECOND = 1_000_000_000
SLOT = 15 * SECOND
START_SLACK = 3 * SECOND
LOOKBACK = 600 * SECOND
# The previous poll can test absence even for timestamps between poll starts.
RANGE_END_ALLOWANCE = 2 * SLOT + START_SLACK
DURATION = 48 * 3600 * SECOND
REQUEST_TIME = 5 * SECOND
CLOCK_TOLERANCE = SECOND
MIN_SAMPLES = 20
ENTRY_LIMIT = 128
MAX_URL_BYTES = 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_RECEIVED_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
FOOTER_BYTES = 1024
MAX_REQUESTS = 11_520
MAX_REQUESTS_PER_MINUTE = 5
LOKI = "http://loki.observability.svc.cluster.local:3100"
TARGET = {"cluster": "main", "node": "talosm03"}
LABELS = TARGET | {"source": "talos", "talos_service": "kernel"}
SELECTOR = "{" + ",".join(f'{key}="{value}"' for key, value in LABELS.items()) + "}"
RCU_HEADER = re.compile(
    r"rcu: INFO: rcu_preempt (self-detected stall on CPU|detected (expedited )?stalls on CPUs/tasks)"
)
RULE = Path(__file__).with_name("rcu-rule.json")


class CycleError(Exception):
    """The slot did not yield a complete observation."""


class StopRun(Exception):
    """Stop the one-shot run and record the reason."""


class Runtime:
    def __init__(self):
        self.stopped = False

    @staticmethod
    def now():
        return time.clock_gettime_ns(time.CLOCK_BOOTTIME)

    @staticmethod
    def wall():
        return time.time_ns()

    def stop(self, *_):
        self.stopped = True

    def wait(self, ns):
        time.sleep(min(ns / SECOND, 1))


class Loki:
    def __init__(self, base, runtime):
        self.base = base
        self.runtime = runtime
        self.requests = 0
        self.received = 0
        self.recent_requests = []

    def get(self, path, params, hard_deadline):
        url = f"{self.base}/loki/api/v1/{path}?{urlencode(params)}"
        if len(url.encode("ascii")) > MAX_URL_BYTES:
            raise StopRun("request_size")
        started = self.runtime.now()
        if hard_deadline - started < 1_000_000:
            raise StopRun("expired")
        if self.runtime.stopped:
            raise StopRun("cancelled")
        if self.requests >= MAX_REQUESTS:
            raise StopRun("request_budget")
        cap = min(MAX_RESPONSE_BYTES, MAX_RECEIVED_BYTES - self.received)
        if cap <= 0:
            raise StopRun("response_budget")
        self.recent_requests = [ts for ts in self.recent_requests if started - ts < 60 * SECOND]
        if len(self.recent_requests) >= MAX_REQUESTS_PER_MINUTE:
            raise CycleError("rate_guard")

        deadline = min(started + REQUEST_TIME, hard_deadline)
        command = [
            "curl", "--disable", "--noproxy", "*", "--proto", "=http", "--http1.1",
            "--globoff", "--silent", "--include", "--connect-timeout", "2",
            "--max-time", f"{(deadline - started) / SECOND:.6f}",
            "--max-filesize", str(cap), "--user-agent", "op885-observer/1",
            "--header", "Accept-Encoding: identity", "--url", url,
        ]
        self.requests += 1
        self.recent_requests.append(started)
        try:
            child = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env={"PATH": os.defpath, "LC_ALL": "C"},
            )
        except OSError as exc:
            raise StopRun("curl_unavailable") from exc
        try:
            return self.read_response(child, cap, deadline)
        except OSError as exc:
            raise CycleError("transport_io") from exc
        finally:
            if child.poll() is None:
                child.kill()
            child.stdout.close()
            try:
                child.wait(timeout=0.25)
            except subprocess.TimeoutExpired as exc:
                raise StopRun("transport_cleanup") from exc

    def read_response(self, child, cap, deadline):
        response = bytearray()
        body_at = None
        status_checked = False
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - self.runtime.now()
                if remaining <= 0:
                    raise CycleError("request_timeout")
                if self.runtime.stopped:
                    raise StopRun("cancelled")
                if not selector.select(min(remaining / SECOND, 0.25)):
                    continue
                if self.runtime.now() >= deadline:
                    raise CycleError("request_timeout")
                chunk = os.read(child.stdout.fileno(), min(8192, cap - len(response)))
                if not chunk:
                    break
                response.extend(chunk)
                self.received += len(chunk)
                if not status_checked:
                    line_end = response.find(b"\r\n")
                    if line_end >= 0:
                        check_status(response[:line_end])
                        status_checked = True
                if body_at is None:
                    delimiter = response.find(b"\r\n\r\n")
                    if delimiter >= 0:
                        body_at = delimiter + 4
                if self.received >= MAX_RECEIVED_BYTES:
                    raise StopRun("response_budget")
                if len(response) >= cap:
                    raise CycleError("response_limit")
        try:
            code = child.wait(timeout=max(0, (deadline - self.runtime.now()) / SECOND))
        except subprocess.TimeoutExpired as exc:
            raise CycleError("request_timeout") from exc
        if self.runtime.now() >= deadline:
            raise CycleError("request_timeout")
        if code == 28:
            raise CycleError("request_timeout")
        if code == 63:
            raise CycleError("response_limit")
        if code != 0 or body_at is None:
            raise CycleError("incomplete_http_response")
        return response[body_at:]


def check_status(line):
    match = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})(?: .*)?", line)
    if match is None:
        raise CycleError("invalid_http_status")
    status = int(match[1])
    if 300 <= status < 500 and status != 408:
        # Redirects, denials and rate limits end the run without a repeat or fallback.
        raise StopRun(f"http_{status}")
    if status != 200:
        raise CycleError(f"http_{status}")


def results(body, kind):
    try:
        reply = json.loads(body)
        data = reply["data"]
        entries = data["result"]
        if (
            reply["status"] == "success" and data["resultType"] == kind
            and isinstance(entries, list) and not reply.get("warnings") and not data.get("warnings")
        ):
            return entries
    except (KeyError, TypeError, ValueError, RecursionError):
        pass
    raise CycleError("invalid_or_partial_loki_response")


def parse_range(body, start, end):
    seen = {}
    count = 0
    for stream in results(body, "streams"):
        try:
            labels = stream["stream"]
            values = stream["values"]
            if not isinstance(labels, dict) or not isinstance(values, list):
                raise ValueError
            if any(labels.get(key) != value for key, value in LABELS.items()):
                raise ValueError
            if not all(isinstance(value, str) for value in labels.values()):
                raise ValueError
            for entry in values:
                count += 1
                if count >= ENTRY_LIMIT:
                    raise CycleError("possible_entry_truncation")
                if not isinstance(entry, list) or len(entry) not in (2, 3):
                    raise ValueError
                timestamp, line = entry[:2]
                if not re.fullmatch(r"[0-9]{1,19}", timestamp) or not isinstance(line, str):
                    raise ValueError
                if len(entry) == 3 and not isinstance(entry[2], dict):
                    raise ValueError
                event_ns = int(timestamp)
                if not start <= event_ns < end:
                    raise ValueError
                identity = json.dumps([labels, event_ns, *entry[1:]], sort_keys=True).encode()
                seen[hashlib.sha256(identity).hexdigest()] = (event_ns, bool(RCU_HEADER.search(line)))
        except (KeyError, TypeError, ValueError, RecursionError) as exc:
            raise CycleError("invalid_range_entry") from exc
    return count, seen


def parse_evaluation(body):
    entries = results(body, "vector")
    if not entries:
        return False
    try:
        (match,) = entries
        _, value = match["value"]
        count = float(value)
        if match["metric"] == TARGET and math.isfinite(count) and count > 0:
            return True
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    raise CycleError("invalid_evaluation")


class Ledger:
    def __init__(self, stream):
        self.stream = stream
        self.written = 0

    def write(self, *records, final=False):
        data = "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records).encode()
        cap = MAX_OUTPUT_BYTES if final else MAX_OUTPUT_BYTES - FOOTER_BYTES
        if self.written + len(data) > cap:
            raise StopRun("output_budget")
        if self.stream.write(data) != len(data):
            raise OSError("short ledger write")
        self.written += len(data)


@dataclass
class Poll:
    start: int
    end: int
    request_start: int


class Observation:
    def __init__(self, ledger, started):
        self.ledger = ledger
        self.started = started
        self.seen = {}
        self.previous = None
        self.prior_gap = False
        self.previous_match = False
        self.gaps = 0
        self.failed = 0
        self.cycles = 0
        self.fresh_bracketed = 0
        self.fresh_unbracketed = 0
        self.matches = 0

    def gap(self, slot, kind, reason, scheduled, count=1):
        self.previous = None
        self.prior_gap = True
        self.previous_match = False
        self.gaps += count
        self.failed += count
        self.ledger.write({
            "type": "gap", "slot": slot, "kind": kind, "reason": reason, "slots": count,
            "scheduled_ns": scheduled, "coverage": "inconclusive", "node_health": "unknown",
        })
        if self.failed >= 3:
            raise StopRun("three_failed_cycles")

    def range(self, record, body):
        start = record["scheduled_ns"] - LOOKBACK
        end = record["scheduled_ns"] + RANGE_END_ALLOWANCE
        count, entries = parse_range(body, start, end)
        self.seen = {key: ts for key, ts in self.seen.items() if ts >= start}
        new = []
        for key, (ts, rcu_header) in entries.items():
            if key in self.seen:
                continue
            upper = record["response_completed_ns"]
            fresh = ts >= self.started
            lower, missing = self.lower_bound(ts, upper)
            new.append({
                "type": "first_visibility", "slot": record["slot"], "event_id": key,
                "loki_event_ns": ts, "fresh": fresh, "lower_observed_ns": lower,
                "upper_observed_ns": upper, "upper_delay_ns": upper - ts if ts <= upper else None,
                "coverage": "bracketed" if lower is not None else "inconclusive",
                "missing_lower_bound": missing, "rcu_header": rcu_header,
            })
        self.ledger.write(record | {
            "start_ns": start, "end_ns": end, "returned": count,
            "duplicates": count - len(new), "first_seen": len(new),
        }, *new)
        for event in new:
            if event["fresh"]:
                if event["coverage"] == "bracketed":
                    self.fresh_bracketed += 1
                else:
                    self.fresh_unbracketed += 1
        self.seen.update((key, ts) for key, (ts, _) in entries.items())
        self.previous = Poll(start, end, record["request_started_ns"])
        self.prior_gap = False

    def lower_bound(self, event, completed):
        if event > completed:
            return None, "event_clock_ahead"
        if self.prior_gap:
            return None, "prior_gap"
        if self.previous is None:
            return None, "warmup"
        if not self.previous.start <= event < self.previous.end:
            return None, "outside_previous_window"
        return self.previous.request_start, None

    def evaluation(self, record, body):
        match = parse_evaluation(body)
        self.ledger.write(record | {
            "result": "recent_match" if match else "no_recent_match",
            "previous_match_now_absent": self.previous_match and not match,
            "node_health": "unknown",
        })
        self.previous_match = match
        self.matches += int(match)


def observe(loki, ledger, expr, runtime, approval_expiry_ns=None):
    origin = runtime.now()
    started = runtime.wall()
    deadline = origin + DURATION
    if approval_expiry_ns is not None:
        deadline = min(deadline, origin + approval_expiry_ns - started)
    state = Observation(ledger, started)
    ledger.write({
        "type": "start", "pid": os.getpid(), "started_ns": started,
        "expires_ns": started + deadline - origin, "target": TARGET,
    })
    slot = 0
    try:
        while True:
            now = runtime.now()
            actual = started + now - origin
            if now >= deadline or (approval_expiry_ns is not None and runtime.wall() >= approval_expiry_ns):
                raise StopRun("expired")
            if runtime.stopped:
                raise StopRun("cancelled")
            if abs(runtime.wall() - actual) > CLOCK_TOLERANCE:
                raise StopRun("clock_step")
            scheduled = started + slot * SLOT
            if actual < scheduled:
                runtime.wait(min(scheduled - actual, deadline - now))
                continue
            current_slot = (actual - started) // SLOT
            if current_slot > slot:
                state.gap(slot, "schedule", "missed_schedule", scheduled, current_slot - slot)
                slot = current_slot
                continue
            kind = "evaluation" if slot % 4 == 3 else "range"
            try:
                if actual - scheduled > START_SLACK:
                    raise CycleError("late_start")
                if kind == "range":
                    body = loki.get("query_range", {
                        "query": SELECTOR, "start": scheduled - LOOKBACK,
                        "end": scheduled + RANGE_END_ALLOWANCE, "direction": "forward",
                        "limit": ENTRY_LIMIT,
                    }, deadline)
                else:
                    body = loki.get("query", {"query": expr, "time": scheduled}, deadline)
                finished = started + runtime.now() - origin
                if finished >= started + deadline - origin:
                    raise StopRun("expired")
                if runtime.stopped:
                    raise StopRun("cancelled")
                if abs(runtime.wall() - finished) > CLOCK_TOLERANCE:
                    raise StopRun("clock_step")
                record = {
                    "type": kind, "slot": slot, "scheduled_ns": scheduled,
                    "request_started_ns": actual, "response_completed_ns": finished,
                }
                if kind == "range":
                    state.range(record, body)
                else:
                    state.evaluation(record, body)
                state.failed = 0
                state.cycles += 1
            except CycleError as exc:
                state.gap(slot, kind, str(exc), scheduled)
            slot += 1
    except StopRun as exc:
        reason = str(exc)
    full_schedule = state.cycles == (DURATION + SLOT - 1) // SLOT
    sampled = state.fresh_bracketed >= MIN_SAMPLES and state.fresh_unbracketed == 0
    covered = reason == "expired" and full_schedule and sampled and state.gaps == 0
    ledger.write({
        "type": "end", "reason": reason, "ended_ns": runtime.wall(),
        "coverage": "sampled_query_windows" if covered else "inconclusive",
        "complete_cycles": state.cycles, "gap_slots": state.gaps,
        "consecutive_failed_cycles": state.failed, "requests": loki.requests,
        "received_bytes": loki.received, "fresh_bracketed_events": state.fresh_bracketed,
        "fresh_unbracketed_events": state.fresh_unbracketed, "matching_evaluations": state.matches,
        "node_health": "unknown",
    }, final=True)
    return reason


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--loopback-url", metavar="http://127.0.0.1:PORT")
    mode.add_argument("--approved-loki", action="store_true", help="requires separate OP885 trial approval")
    parser.add_argument("--expires-at", metavar="YYYY-MM-DDTHH:MM:SSZ", help="approved UTC cutoff")
    parser.add_argument("--output", required=True, type=Path, help="new local JSONL file")
    args = parser.parse_args(argv)
    expiry = None
    if args.approved_loki:
        if not args.expires_at:
            parser.error("--approved-loki requires --expires-at")
        try:
            cutoff = datetime.strptime(args.expires_at, "%Y-%m-%dT%H:%M:%SZ")
            expiry = int(cutoff.replace(tzinfo=timezone.utc).timestamp()) * SECOND
        except ValueError:
            parser.error("--expires-at must be a UTC timestamp such as 2026-12-01T08:00:00Z")
        if expiry <= time.time_ns():
            parser.error("--expires-at has passed")
        base = LOKI
    else:
        local = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})", args.loopback_url)
        if local is None or not 1 <= int(local[1]) <= 65535 or args.expires_at:
            parser.error("loopback mode requires a numeric 127.0.0.1 port and no approval cutoff")
        base = args.loopback_url

    runtime = Runtime()
    signal.signal(signal.SIGTERM, runtime.stop)
    signal.signal(signal.SIGINT, runtime.stop)
    try:
        expr = json.loads(RULE.read_text())["groups"][0]["rules"][0]["expr"]
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb", buffering=0) as output:
            reason = observe(Loki(base, runtime), Ledger(output), expr, runtime, expiry)
    except OSError:
        parser.exit(1, "observer could not open, read or write a required local file\n")
    if reason == "expired":
        return 0
    return 130 if reason == "cancelled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
