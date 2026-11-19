#!/usr/bin/env python3
"""Replay small, retained telemetry excerpts and synthetic controls offline."""

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener


HERE = Path(__file__).resolve().parent
RULE = HERE / "rcu-rule.json"
RBD_QUERY = HERE / "rbd-stall.promql"
VECTOR_CONFIG = HERE.parents[2] / "kubernetes/main/apps/observability/vector/app/resources/vector.yaml"
NODE = "talosm03"
CLUSTER = "main"
LOG_LABELS = {
    "cluster": CLUSTER, "node": NODE, "source": "talos", "log_type": "service",
    "talos_service": "kernel", "level": "info",
}
GROUP = "talosm03-kernel-stall"
ALERT = "TalosKernelRCUStall"
RCU = "rcu: INFO: rcu_preempt self-detected stall on CPU"
SECOND = 1_000_000_000
CADENCE = 60 * SECOND
WINDOW = 300 * SECOND
ARRIVALS = (
    # Tenant, time held (s), time from delivery to the next ruler tick (s), firing.
    ("delay-30s-loss", 30, 12, True),
    ("delay-30s-after-tick", 30, 48, True),
    ("delay-2m-before-tick", 120, 12, True),
    ("delay-2m-after-tick", 120, 48, True),
    ("delay-4m30s-before-tick", 270, 12, True),
    ("delay-4m30s-after-tick", 270, 48, False),
    ("delay-over-5m-before-tick", 310, 12, False),
    ("delay-over-5m-after-tick", 310, 48, False),
)
HTTP = build_opener(ProxyHandler({}))
DISK_METRICS = {
    "in_flight": "node_disk_io_now",
    "reads_completed": "node_disk_reads_completed_total",
    "writes_completed": "node_disk_writes_completed_total",
}


def call_loki(base, path, params=None, payload=None, *, tenant="replay"):
    url = base + path
    if params:
        url += "?" + urlencode(params)
    data = json.dumps(payload).encode() if payload else None
    request = Request(url, data=data, headers={"Content-Type": "application/json", "X-Scope-OrgID": tenant})
    with HTTP.open(request, timeout=3) as response:
        return response.read()


def push_logs(base, entries):
    streams = {}
    for when, message, extra_labels in entries:
        labels = LOG_LABELS | extra_labels
        values = streams.setdefault(tuple(labels.items()), [])
        values.append([str(when), json.dumps({"msg": message})])
    payload = {"streams": [{"stream": dict(labels), "values": values} for labels, values in streams.items()]}
    call_loki(base, "/loki/api/v1/push", payload=payload)


def log_result(base, expression, when, *, tenant):
    body = call_loki(base, "/loki/api/v1/query", {"query": expression, "time": str(when)}, tenant=tenant)
    return json.loads(body)["data"]["result"]


def expect_log(base, expression, when, count=None, *, tenant="replay"):
    result = log_result(base, expression, when, tenant=tenant)
    actual = [(item["metric"], int(item["value"][1])) for item in result]
    expected = [] if count is None else [({"cluster": CLUSTER, "node": NODE}, count)]
    assert actual == expected, (tenant, when, actual, expected)


def wait_for_loki(process, check, description, timeout, poll=1):
    until = time.monotonic() + timeout
    while time.monotonic() < until and process.poll() is None:
        try:
            result = check()
            if result:
                return result
        except (URLError, TimeoutError):
            pass
        time.sleep(poll)
    raise AssertionError(f"Local Loki did not {description} in {timeout}s")


def ruler_status(base, tenant):
    groups = json.loads(call_loki(base, "/prometheus/api/v1/rules", tenant=tenant))["data"]["groups"]
    if not groups:
        return None
    assert len(groups) == 1, (tenant, groups)
    group = groups[0]
    assert (group["name"], group["interval"], len(group["rules"])) == (GROUP, 60, 1), (tenant, group)
    rule = group["rules"][0]
    assert rule["name"] == ALERT, (tenant, rule)
    evaluated = datetime.fromisoformat(group["lastEvaluation"])
    return int(evaluated.timestamp() * SECOND), rule


def expect_state(tenant, rule, firing):
    assert rule["health"] == "ok", (tenant, rule)
    expected = "firing" if firing else "inactive"
    assert rule["state"] == expected, (tenant, expected, rule)
    labels = [{"alertname": ALERT, "cluster": CLUSTER, "node": NODE}] if firing else []
    assert [alert["labels"] for alert in (rule["alerts"] or [])] == labels, (tenant, rule)


def loki_replay(base, process, expression):
    history = json.loads((HERE / "nov17-kernel.json").read_text())
    # Preserve historical spacing on the ephemeral instance's current ingest horizon.
    first_report = time.time_ns() - 150 * 60 * SECOND
    shift = first_report - int(history[2]["loki_timestamp_ns"])
    push_logs(base, [(int(entry["loki_timestamp_ns"]) + shift, entry["excerpt"], {}) for entry in history])
    last_report = int(history[-1]["loki_timestamp_ns"]) + shift
    expect_log(base, expression, first_report - SECOND)
    expect_log(base, expression, first_report + SECOND, 1)
    expect_log(base, expression, first_report + 5 * SECOND, 2)
    expect_log(base, expression, last_report + 299 * SECOND, 1)
    expect_log(base, expression, last_report + 301 * SECOND)
    print("Loki retained excerpts: file-reference warning quiet, first RCU report matched, window expired", flush=True)

    now = time.time_ns()
    transient = now - 10 * 60 * SECOND
    unrelated = now - 30 * SECOND
    duplicate = now - 10 * SECOND
    plain_warning = "file_ref_inc() on a released file reference"
    push_logs(base, [
        (transient, "rcu: INFO: rcu_preempt detected stalls on CPUs/tasks:", {}),
        (unrelated, plain_warning, {}),
        (unrelated, RCU, {"node": "talosm02"}),
        (unrelated, RCU, {"source": "pods"}),
        (unrelated, RCU, {"talos_service": "kubelet"}),
        (unrelated + SECOND, "rcu: INFO: rcu_preempt callback activity", {}),
        (duplicate, RCU, {}),
        (duplicate + SECOND, RCU, {}),
        (duplicate, RCU, {"level": "warn"}),
    ])
    expect_log(base, expression, transient - SECOND)
    expect_log(base, expression, transient + SECOND, 1)
    expect_log(base, expression, transient + 301 * SECOND)
    expect_log(base, expression, unrelated + 2 * SECOND)
    expect_log(base, expression, now, 3)

    def ruler_is_firing():
        status = ruler_status(base, "replay")
        return status[1] if status and status[1]["state"] == "firing" else None

    expect_state("replay", wait_for_loki(process, ruler_is_firing, "fire one node alert", 80), True)
    print("Loki synthetic controls: isolated report expired, selector ignored unrelated logs, duplicates produced one firing alert", flush=True)


def loopback_port(kind=socket.SOCK_STREAM):
    with socket.socket(type=kind) as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def iso_time(when):
    seconds, nanos = divmod(when, SECOND)
    return f"{datetime.fromtimestamp(seconds, timezone.utc):%Y-%m-%dT%H:%M:%S}.{nanos:09d}Z"


def talos_payload(tenant, when, message):
    return {
        "op885_tenant": tenant,
        "hostname": NODE,
        "cluster": CLUSTER,
        "talos-service": "kernel",
        "talos-level": "info",
        "talos-time": iso_time(when),
        # Talos kmsg storage embeds a separate kernel timestamp in the service log.
        "msg": f"kern: warning: [{iso_time(when - 15 * SECOND)}]: {message}",
    }


def send_datagram(sender, payload):
    sender.send(json.dumps(payload).encode() + b"\n")


def vector_logs(base, tenant, start):
    body = call_loki(base, "/loki/api/v1/query_range", {
        "query": '{source="talos"}', "start": str(start), "end": str(time.time_ns()),
    }, tenant=tenant)
    # Loki can add its own discovery labels on ingestion, independent of Vector templates.
    return [
        ({key: value for key, value in stream["stream"].items() if key not in {"detected_level", "service_name"}},
         int(when), json.loads(line))
        for stream in json.loads(body)["data"]["result"]
        for when, line in stream["values"]
    ]


def vector_controls(base, process, sender, expression):
    tenant = "vector-controls"
    # These are Talos-shaped synthetic datagrams, sent through the repository's VRL and sink.
    cases = (
        ("complete", None, LOG_LABELS),
        ("no-hostname", "hostname", LOG_LABELS | {"node": "127.0.0.1"}),
        ("no-service", "talos-service", {key: value for key, value in LOG_LABELS.items() if key != "talos_service"}),
        ("no-level", "talos-level", {key: value for key, value in LOG_LABELS.items() if key != "level"}),
        ("no-cluster-tag", "cluster", LOG_LABELS),
    )
    start = time.time_ns() - 20 * SECOND
    expected = {}
    for index, (name, omitted, labels) in enumerate(cases):
        when = start + (index + 1) * SECOND
        payload = talos_payload(tenant, when, f"{RCU} 8: {name}")
        if omitted:
            del payload[omitted]
        expected[payload["msg"]] = (labels, when)
        send_datagram(sender, payload)

    def read_controls():
        records = vector_logs(base, tenant, start)
        return records if len(records) >= len(cases) else None

    records = wait_for_loki(process, read_controls, "receive the Vector label controls", 15, poll=0.25)
    assert len(records) == len(cases), records
    assert {line["msg"]: (labels, when) for labels, when, line in records} == expected, records
    expect_log(base, expression, time.time_ns(), 3, tenant=tenant)
    print("Vector UDP/VRL/sink: intact event timestamps; missing hostname or service excluded; missing level or cluster tag matched", flush=True)


def tick_at_or_after(last_tick, threshold):
    return threshold + (last_tick - threshold) % CADENCE


def expect_ruler_tick(base, process, expression, tenant, tick, firing):
    def evaluated():
        status = ruler_status(base, tenant)
        return status if status and status[0] >= tick - 2 * SECOND else None

    actual_tick, rule = wait_for_loki(process, evaluated, f"evaluate {tenant} at its expected tick", 8, poll=0.25)
    assert actual_tick <= tick + 8 * SECOND, (tenant, iso_time(tick), iso_time(actual_tick), rule)
    expect_state(tenant, rule, firing)
    expect_log(base, expression, actual_tick, 1 if firing else None, tenant=tenant)
    return actual_tick


def staged_arrivals(base, process, sender, expression):
    def read_phases():
        states = {tenant: ruler_status(base, tenant) for tenant, _, _, _ in ARRIVALS}
        return states if all(state and state[1]["health"] == "ok" for state in states.values()) else None

    phases = wait_for_loki(process, read_phases, "evaluate all empty staged-arrival tenants", 80)
    steps = []

    def check_before_delivery(tenant, event):
        expect_log(base, expression, event + SECOND, tenant=tenant)
        _, rule = ruler_status(base, tenant)
        expect_state(tenant, rule, False)

    def deliver(tenant, event, delay, payload):
        sent = time.time_ns()
        assert 0 <= sent - event - delay * SECOND <= 3 * SECOND, (tenant, sent, event, delay)
        send_datagram(sender, payload)

    def query_arrival(tenant, event, tick, payload):
        records = wait_for_loki(process, lambda: vector_logs(base, tenant, event - SECOND), f"receive {tenant}", 6, poll=0.2)
        observed = time.time_ns()
        assert observed < tick - 2 * SECOND, (tenant, iso_time(observed), iso_time(tick))
        assert [(labels, when, line["msg"]) for labels, when, line in records] == [(LOG_LABELS, event, payload["msg"])], records
        expect_log(base, expression, event + SECOND, 1, tenant=tenant)
        print(f"{tenant}: Vector kept {iso_time(event)}, queryable by +{(observed - event) / SECOND:.1f}s", flush=True)

    def check_firing(tenant, event, tick, firing):
        actual = expect_ruler_tick(base, process, expression, tenant, tick, firing)
        print(f"{tenant}: first post-delivery ruler tick at +{(actual - event) / SECOND:.1f}s, {'firing' if firing else 'inactive'}", flush=True)

    def loss_witness(tenant):
        # Deliver the next report only to a control tenant to model target telemetry loss.
        # An unrelated host still gets through to the monitored tenant.
        when = time.time_ns()
        witness = "loss-witness"
        continued = talos_payload(witness, when, f"{RCU} 8: synthetic continuing stall")
        unrelated = talos_payload(tenant, when, "OP885 synthetic unrelated host traffic") | {"hostname": "talosm02"}
        send_datagram(sender, continued)
        send_datagram(sender, unrelated)
        for name, labels, payload in ((witness, LOG_LABELS, continued),
                                      (tenant, LOG_LABELS | {"node": "talosm02"}, unrelated)):
            records = wait_for_loki(process, lambda: vector_logs(base, name, when - SECOND), f"receive {name}", 6, poll=0.2)
            assert [(actual, stamp, line["msg"]) for actual, stamp, line in records] == [(labels, when, payload["msg"])], records

    def check_expiry(tenant, event, tick):
        actual = expect_ruler_tick(base, process, expression, tenant, tick, False)
        expect_log(base, expression, actual, 1, tenant="loss-witness")
        expect_log(base, expression, event + WINDOW - SECOND, 1, tenant=tenant)
        expect_log(base, expression, event + WINDOW + SECOND, tenant=tenant)
        print(f"{tenant}: inactive at +{(actual - event) / SECOND:.1f}s after event-time expiry; node state unknown", flush=True)

    earliest_event = time.time_ns() + 5 * SECOND
    for tenant, delay, until_tick, firing in ARRIVALS:
        last_tick, rule = phases[tenant]
        expect_state(tenant, rule, False)
        tick = tick_at_or_after(last_tick, earliest_event + (delay + until_tick) * SECOND)
        delivery = tick - until_tick * SECOND
        event = delivery - delay * SECOND
        payload = talos_payload(tenant, event, f"{RCU} 8: synthetic delayed report")
        steps.extend([
            (delivery - 4 * SECOND, check_before_delivery, (tenant, event)),
            (delivery, deliver, (tenant, event, delay, payload)),
            (delivery + 3 * SECOND, query_arrival, (tenant, event, tick, payload)),
            (tick + 2 * SECOND, check_firing, (tenant, event, tick, firing)),
        ])
        if tenant == "delay-30s-loss":
            expiry_tick = tick_at_or_after(tick, event + WINDOW)
            last_firing_tick = expiry_tick - CADENCE
            steps.extend([
                (tick + 20 * SECOND, loss_witness, (tenant,)),
                (last_firing_tick + 2 * SECOND, expect_ruler_tick,
                 (base, process, expression, tenant, last_firing_tick, True)),
                (expiry_tick + 2 * SECOND, check_expiry, (tenant, event, expiry_tick)),
            ])

    for when, action, args in sorted(steps, key=lambda step: step[0]):
        while (remaining := (when - time.time_ns()) / SECOND) > 0:
            assert process.poll() is None, "Local Loki exited during staged replay"
            time.sleep(min(remaining, 5))
        action(*args)


@contextmanager
def local_process(command, work, env, log_name):
    with (work / log_name).open("w+") as log:
        process = subprocess.Popen(command, cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            yield process
        except (AssertionError, URLError, TimeoutError):
            log.seek(0)
            for line in log.readlines()[-6:]:
                print(line.rstrip(), flush=True)
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_loki(binary, vector, work, env):
    port = loopback_port()
    base = f"http://127.0.0.1:{port}"
    expression = json.loads(RULE.read_text())["groups"][0]["rules"][0]["expr"]
    # Isolate cases while evaluating the same rule and preserving its production label selector.
    for tenant in ("replay", *(name for name, _, _, _ in ARRIVALS)):
        rules = work / "rules" / tenant
        rules.mkdir(parents=True)
        shutil.copyfile(RULE, rules / RULE.name)
    config = work / "loki.yaml"
    config.write_text(f"""\
auth_enabled: true
analytics:
  reporting_enabled: false
server:
  http_listen_address: 127.0.0.1
  http_listen_port: {port}
  grpc_listen_address: 127.0.0.1
  grpc_listen_port: 0
  log_level: error
common:
  instance_addr: 127.0.0.1
  path_prefix: state
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
  storage:
    filesystem:
      chunks_directory: chunks
      rules_directory: rules
schema_config:
  configs:
    - from: 2020-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
limits_config:
  reject_old_samples: false
ingester:
  max_chunk_age: 6h
querier:
  query_ingesters_within: 0s
ruler:
  enable_api: true
  evaluation_interval: 1m
  poll_interval: 5s
  rule_path: scratch-rules
  storage:
    type: local
    local:
      directory: rules
""")
    yq = shutil.which("yq")
    assert yq, "The Vector fixture requires Mike Farah's yq on PATH"
    vector_config = json.loads(subprocess.check_output([yq, "-o=json", VECTOR_CONFIG], env=env, timeout=5))
    udp_port = loopback_port(socket.SOCK_DGRAM)
    vector_data = work / "vector-data"
    vector_data.mkdir()
    vector_config["data_dir"] = str(vector_data)
    vector_config["api"]["enabled"] = False
    vector_config["sources"]["talos_service"]["address"] = f"127.0.0.1:{udp_port}"
    vector_config["sinks"]["loki"].update(endpoint=base, tenant_id="{{ op885_tenant }}")
    vector_path = work / "vector.json"
    vector_path.write_text(json.dumps(vector_config))

    with local_process([binary, f"-config.file={config}"], work, {"GOMAXPROCS": "1"}, "loki.log") as process:
        wait_for_loki(process, lambda: call_loki(base, "/ready"), "become ready", 40)
        loki_replay(base, process, expression)
        with local_process([vector, "--threads", "1", "--config-json", vector_path], work, env, "vector.log") as vector_process:
            with socket.socket(type=socket.SOCK_DGRAM) as sender:
                sender.connect(("127.0.0.1", udp_port))

                def vector_listening():
                    return "Listening." in (work / "vector.log").read_text()

                wait_for_loki(vector_process, vector_listening, "start Vector's UDP listener", 10)
                vector_controls(base, process, sender, expression)
                staged_arrivals(base, process, sender, expression)


def prom_series(metric, values, *, device=None, node=NODE, target="primary"):
    labels = {"job": "node-exporter", "kubernetes_node": node, "instance": f"{target}:9100", "pod": target}
    if device:
        labels["device"] = device
    selector = ",".join(f'{name}="{value}"' for name, value in labels.items())
    return {"series": f"{metric}{{{selector}}}", "values": values}


def disk_series(flight, reads, writes, *, device="rbd0", node=NODE, target="primary"):
    return [
        prom_series(metric, values, device=device, node=node, target=target)
        for metric, values in zip(DISK_METRICS.values(), (flight, reads, writes))
        if values is not None
    ]


def prom_case(name, series, checks, interval="1m"):
    expression = RBD_QUERY.read_text().strip()
    return {
        "name": name,
        "interval": interval,
        "input_series": series,
        "promql_expr_test": [
            {
                "expr": expression,
                "eval_time": when,
                "exp_samples": [
                    {"labels": f'{{device="{device}",kubernetes_node="{NODE}"}}', "value": value}
                    for device, value in devices
                ],
            }
            for when, devices in checks
        ],
    }


def retained_prom_cases():
    with (HERE / "nov17-rbd.csv").open(newline="") as fixture:
        rows = list(csv.DictReader(fixture))
    checks = {
        "healthy": [("4m", [])],
        "onset": [("10m", []), ("17m30s", []), ("18m", [("rbd0", 8)]), ("23m", []),
                  ("28m30s", [("rbd0", 9)]), ("29m", [("rbd0", 9), ("rbd1", 2)])],
        "stall": [("4m", [("rbd0", 11), ("rbd1", 2)])],
        "backfill": [("4m", [])],
    }
    for phase, expected in checks.items():
        series = [prom_series("up", "1x35")]
        for device in ("rbd0", "rbd1"):
            samples = [row for row in rows if row["phase"] == phase and row["device"] == device]
            times = [datetime.fromisoformat(row["timestamp"]) for row in samples]
            assert times == [times[0] + timedelta(minutes=i) for i in range(len(times))], (phase, device)
            series.extend(disk_series(*(" ".join(row[key] for row in samples) for key in DISK_METRICS), device=device))
        yield prom_case(f"retained {phase}", series, expected)


def synthetic_prom_cases():
    stop = [("3m", [])]
    firing = [("3m", [("rbd0", 4)])]
    up = prom_series("up", "1x6")
    flat = "20x6"
    busy = "4x6"
    ready = {"series": 'kube_node_status_condition{node="talosm03",condition="Ready",status="true"}', "values": "1x6"}
    yield prom_case("Ready stays true", disk_series(busy, flat, flat) + [up, ready], firing)
    yield prom_case("brief queue drains", disk_series("4 4 0 4 4", flat, flat) + [up], stop)
    yield prom_case("idle and no completions", disk_series("0x6", flat, flat) + [up], stop)
    yield prom_case("slow reads continue", disk_series(busy, "20 20 21 21 22", flat) + [up], stop)
    yield prom_case("slow writes continue", disk_series(busy, flat, "20 20 21 21 22") + [up], stop)
    yield prom_case("counter reset", disk_series(busy, "20 20 1 1 1", flat) + [up], stop)
    yield prom_case("missing completion counter", disk_series(busy, flat, None) + [up], stop)
    yield prom_case("missing sample in window", disk_series("4 4 _ 4 4", flat, flat) + [up], stop)
    yield prom_case("explicit stale sample", disk_series("4 4 4 stale 4", flat, flat) + [up], stop)
    yield prom_case("exporter scrape failed", disk_series(busy, flat, flat) + [prom_series("up", "1 1 1 0")], stop)
    yield prom_case("old successful up cannot validate newer disk stats",
                    disk_series(busy, flat, flat) + [prom_series("up", "1 1 1 _")], [("2m", [("rbd0", 4)]), ("3m", [])])
    yield prom_case("device detached during a successful scrape",
                    disk_series("4 4 4 stale", "20 20 20 stale", "20 20 20 stale") + [up],
                    [("2m", [("rbd0", 4)]), ("3m", [])])
    yield prom_case("observed only on other node", disk_series(busy, flat, flat, node="talosm02") + [up], stop)
    yield prom_case("stale time with sufficient samples", disk_series("4 4 4 _ _ _", "20 20 20 _ _ _", "20 20 20 _ _ _") + [up],
                    [("2m30s", [])], interval="30s")
    yield prom_case("counter latest sample is from another scrape", disk_series(busy, "20 20 20 20 20 20 _", flat) + [up],
                    stop, interval="30s")
    duplicate = disk_series(busy, flat, flat, target="duplicate") + [prom_series("up", "1x6", target="duplicate")]
    yield prom_case("duplicate exporters share output labels", disk_series(busy, flat, flat) + [up] + duplicate, firing)
    split = disk_series(None, flat, flat, target="duplicate")
    yield prom_case("missing fields cannot be joined across exporters", disk_series(busy, None, None) + [up] + split, stop)

    scrape_seconds = {0, 58, 120, 181, 239}

    def jittered(value):
        return " ".join(value if when in scrape_seconds else "_" for when in range(240))

    yield prom_case("scrape jitter briefly leaves only two points",
                    disk_series(jittered("4"), jittered("20"), jittered("20")) + [prom_series("up", jittered("1"))],
                    [("2m", [("rbd0", 4)]), ("3m", []), ("3m1s", [("rbd0", 4)]), ("4m", [("rbd0", 4)])],
                    interval="1s")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loki", type=Path, required=True)
    parser.add_argument("--promtool", type=Path, required=True)
    parser.add_argument("--vector", type=Path, required=True)
    args = parser.parse_args()
    env = {"GOMAXPROCS": os.environ.get("DEV_ENV_CPU_LIMIT", "1")}
    with tempfile.TemporaryDirectory(prefix=".op885-test-", dir=Path.cwd()) as directory:
        work = Path(directory)
        cases = [*retained_prom_cases(), *synthetic_prom_cases()]
        fixture = work / "promtool-tests.yml"
        fixture.write_text(json.dumps({"rule_files": [], "evaluation_interval": "30s", "tests": cases}))
        subprocess.run([args.promtool.resolve(), "test", "rules", fixture], cwd=work, env=env, check=True, timeout=30)
        print(f"Promtool: {len(cases)} retained and synthetic cases passed", flush=True)
        test_loki(args.loki.resolve(), args.vector.resolve(), work, env)


if __name__ == "__main__":
    main()
