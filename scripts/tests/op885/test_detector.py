#!/usr/bin/env python3
"""Replay small, retained telemetry excerpts and synthetic controls offline."""

import argparse
import csv
from datetime import datetime, timedelta
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
NODE = "talosm03"
CLUSTER = "main"
LOG_LABELS = {"cluster": CLUSTER, "node": NODE, "source": "talos", "talos_service": "kernel", "level": "info"}
RCU = "rcu: INFO: rcu_preempt self-detected stall on CPU"
SECOND = 1_000_000_000
HTTP = build_opener(ProxyHandler({}))
DISK_METRICS = {
    "in_flight": "node_disk_io_now",
    "reads_completed": "node_disk_reads_completed_total",
    "writes_completed": "node_disk_writes_completed_total",
}


def call_loki(base, path, params=None, payload=None):
    url = base + path
    if params:
        url += "?" + urlencode(params)
    data = json.dumps(payload).encode() if payload else None
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
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


def log_result(base, expression, when):
    body = call_loki(base, "/loki/api/v1/query", {"query": expression, "time": str(when)})
    return json.loads(body)["data"]["result"]


def expect_log(base, expression, when, count=None):
    result = log_result(base, expression, when)
    actual = [(item["metric"], int(item["value"][1])) for item in result]
    expected = [] if count is None else [({"cluster": CLUSTER, "node": NODE}, count)]
    assert actual == expected, (when, actual, expected)


def wait_for_loki(process, check, description, timeout):
    until = time.monotonic() + timeout
    while time.monotonic() < until and process.poll() is None:
        try:
            if check():
                return
        except (URLError, TimeoutError):
            pass
        time.sleep(1)
    raise AssertionError(f"Local Loki did not {description} in {timeout}s")


def loki_replay(base, process):
    expression = json.loads(RULE.read_text())["groups"][0]["rules"][0]["expr"]
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
        groups = json.loads(call_loki(base, "/prometheus/api/v1/rules"))["data"]["groups"]
        for group in groups:
            if group["name"] == "talosm03-kernel-stall" and group["interval"] == 60:
                rule = group["rules"][0]
                if rule["name"] == "TalosKernelRCUStall" and rule["state"] == "firing":
                    return [alert["labels"] for alert in rule["alerts"]] == [
                        {"alertname": "TalosKernelRCUStall", "cluster": CLUSTER, "node": NODE}
                    ]
        return False

    wait_for_loki(process, ruler_is_firing, "fire one node alert", 80)
    print("Loki synthetic controls: isolated report expired, selector ignored unrelated logs, duplicates produced one firing alert", flush=True)


def test_loki(binary, work, env):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    tenant = work / "rules" / "fake"
    tenant.mkdir(parents=True)
    shutil.copyfile(RULE, tenant / RULE.name)
    config = work / "loki.yaml"
    config.write_text(f"""\
auth_enabled: false
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
    with (work / "loki.log").open("w+") as log:
        process = subprocess.Popen(
            [binary, f"-config.file={config}"], cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            wait_for_loki(process, lambda: call_loki(base, "/ready"), "become ready", 40)
            loki_replay(base, process)
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
    yield prom_case("observed only on other node", disk_series(busy, flat, flat, node="talosm02") + [up], stop)
    yield prom_case("stale time with sufficient samples", disk_series("4 4 4 _ _ _", "20 20 20 _ _ _", "20 20 20 _ _ _") + [up],
                    [("2m30s", [])], interval="30s")
    yield prom_case("counter latest sample is from another scrape", disk_series(busy, "20 20 20 20 20 20 _", flat) + [up],
                    stop, interval="30s")
    duplicate = disk_series(busy, flat, flat, target="duplicate") + [prom_series("up", "1x6", target="duplicate")]
    yield prom_case("duplicate exporters share output labels", disk_series(busy, flat, flat) + [up] + duplicate, firing)
    split = disk_series(None, flat, flat, target="duplicate")
    yield prom_case("missing fields cannot be joined across exporters", disk_series(busy, None, None) + [up] + split, stop)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loki", type=Path, required=True)
    parser.add_argument("--promtool", type=Path, required=True)
    args = parser.parse_args()
    env = {"GOMAXPROCS": os.environ.get("DEV_ENV_CPU_LIMIT", "1")}
    with tempfile.TemporaryDirectory(prefix=".op885-test-", dir=Path.cwd()) as directory:
        work = Path(directory)
        cases = [*retained_prom_cases(), *synthetic_prom_cases()]
        fixture = work / "promtool-tests.yml"
        fixture.write_text(json.dumps({"rule_files": [], "evaluation_interval": "30s", "tests": cases}))
        subprocess.run([args.promtool.resolve(), "test", "rules", fixture], cwd=work, env=env, check=True, timeout=30)
        print(f"Promtool: {len(cases)} retained and synthetic cases passed", flush=True)
        test_loki(args.loki.resolve(), work, env)


if __name__ == "__main__":
    main()
