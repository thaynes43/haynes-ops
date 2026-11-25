#!/usr/bin/env python3
"""Coordinator-only smoke and rollback checks for the CR-20 host."""
import argparse
from contextlib import contextmanager
import csv
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
EXEC = ("kubectl", "-n", "ai", "exec")
LOCAL = "/v1/local-model/requests"
STORY = "https://work.haynesnetwork.com/work_packages/442"
CARD = "GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a"
CARDS = {CARD, "GPU-d8a856f1-f955-f683-bc24-654561496774"}
MODEL = "qwen3.5-9b-q5_k_m"
WINDOW = 1800
S2V_BUDGET = 1280
CLEANUP_BUDGET = 100
LOAD_LIMIT = 120
S2V_READBACK = 40
RETURN_RESERVE = LOAD_LIMIT + CLEANUP_BUDGET + 5
S2V_RESERVE = S2V_READBACK + RETURN_RESERVE
GPU_LIMIT = 12288
RAM_LIMIT = 16 * 1024**3

# Each HTTP exchange is local to the Wan pod. The operator needs pods/exec.
HTTP = """
import json, sys
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

port, method, path, content_type = sys.argv[1:]
data = None if method == "GET" else sys.stdin.buffer.read()
request = Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                  headers={"Content-Type": content_type})
try:
    try:
        response = urlopen(request, timeout=8)
    except HTTPError as error:
        response = error
    with response:
        print(json.dumps([response.status, json.load(response)]))
except (URLError, OSError, HTTPException):
    print("Loopback transport failed", file=sys.stderr)
    raise SystemExit(75)
"""

# Read the active lane under the real lease. Only an admitted unload can clear
# residency. When requested, evict ordinary ComfyUI cache after proving the
# child and residency journal have both gone.
LANE = """
import json, sys, time
from urllib.request import urlopen
import api
import product_local as model

action = sys.argv[1]
lease = api.acquire_compute()
if lease is None:
    print(json.dumps({"lease_busy": True}))
    raise SystemExit()
with lease:
    with urlopen("http://127.0.0.1:8080/health", timeout=5) as response:
        health = json.load(response)
    if not health["accepting_jobs"] or health["active_job"] is not None:
        raise SystemExit("Wan is held or busy")
    status, foley = api.foley("/v1/ready")
    if status != 200 or foley["active_job"] is not None:
        raise SystemExit("Foley is unavailable or busy")
    api.require_empty_queue()
    rack = api.gpu_sample()
    if not api.gpus_idle(rack):
        raise SystemExit("Rack GPUs are not idle")
    if action == "dormant":
        if model.load_profile(api.CONFIG) is not None:
            raise SystemExit("Dormant gate requires no profile")
        try:
            model.state_path(api.ROOT).lstat()
        except FileNotFoundError:
            print(json.dumps({"rack": rack, "journal": None}))
            raise SystemExit()
        raise SystemExit("Dormant gate requires no residency journal")
    lane = model.enabled_lane(api.ROOT, api.CONFIG)
    if lane is None:
        raise SystemExit("Expected the active profile")
    sample = lane.status()
    state = None if action == "sample" else lane.read_state()
    if action != "sample" and (bool(state) != sample["resident"] or (state and state.get("pending"))):
        raise SystemExit("Residency and journal disagree or work is unresolved")
    if action == "prove" and state is not None:
        raise SystemExit("Residency must be cleared through the tracked unload")
    if action == "prove" and not lane.unloaded(sample):
        api.engine("/free", {"unload_models": True, "free_memory": True})
        for _ in range(5):
            time.sleep(1)
            api.require_empty_queue()
            sample = lane.status()
            if sample["resident"] or lane.read_state() is not None:
                raise SystemExit("Residency changed while draining ComfyUI")
            if lane.unloaded(sample):
                break
        if not lane.unloaded(sample):
            raise SystemExit("Unloaded memory is above the ceiling")
    print(json.dumps({"sample": sample, "journal": state, "rack": rack}))
"""


class Stop(RuntimeError):
    pass


class Denied(Stop):
    pass


class Uncertain(Stop):
    pass


class ReleaseUnverified(Stop):
    pass


def require(condition, message):
    if not condition:
        raise Stop(message)


def pod_command(container, *args, stdin=False):
    return (*EXEC, *(("-i",) if stdin else ()), "deploy/wan-video", "-c", container, "--", *args)


def exec_failure(container, stderr):
    detail = stderr.decode(errors="replace").strip()
    if re.search(r"forbidden|unauthori[sz]ed|access denied|permission denied|admission.*(?:denied|rejected)", detail, re.I):
        return Denied(detail)
    if re.search(r"loopback transport failed|unexpected EOF|connection reset|i/o timeout|stream error", detail, re.I):
        return Uncertain(f"{container} exec transport failed")
    return Stop(f"{container} exec failed: {detail or 'no error text'}")


def pod(container, *args, data=None, timeout=15):
    require(timeout > 0, "Operation deadline reached")
    try:
        return subprocess.run(
            pod_command(container, *args, stdin=data is not None),
            input=data, capture_output=True, check=True, timeout=timeout,
        ).stdout
    except subprocess.CalledProcessError as error:
        raise exec_failure(container, error.stderr) from error
    except subprocess.TimeoutExpired as error:
        raise Uncertain(f"{container} exec timed out") from error
    except OSError as error:
        raise Stop(f"Could not start {container} exec") from error


def http(port, path, *, verb="GET", data=None, timeout=12):
    content_type = "application/json" if path.startswith(("/v1/jobs/", LOCAL)) else "application/octet-stream"
    status, body = json.loads(pod("api", "python", "-B", "-c", HTTP, str(port), verb, path, content_type,
                                  data=data, timeout=timeout))
    if status in (401, 403):
        raise Denied(f"{verb} {path} denied ({status}, {body.get('code')})")
    return status, body


def read(port, path):
    status, body = http(port, path)
    require(status == 200, f"GET {path} returned {status}")
    return body


def health(active):
    legacy_health = read(8080, "/health")
    require(legacy_health["accepting_jobs"] and legacy_health["active_job"] is None, "Wan is held or busy")
    require(read(8080, "/ready")["ready"], "Wan legacy readiness is false")
    ready = read(8080, "/v1/ready")
    require(ready["ready"] and ready["active_video_job"] is None and ready["active_foley_job"] is None,
            "Wan product readiness or idle check failed")
    require(read(8189, "/v1/ready")["active_job"] is None, "Foley is busy")
    queue = read(8188, "/queue")
    require(queue["queue_running"] == queue["queue_pending"] == [], "Engine queue must be empty")
    profiles = read(8080, "/v1/profiles")["profiles"]
    require({"s2v", "i2v", "foley"} <= profiles.keys(), "Wan/Foley profile discovery failed")
    status, body = http(8080, "/v1/local-model/profile")
    if not active:
        require(status == 503 and body.get("code") == "local_model_not_installed", "Expected dormant model discovery")
        return body
    require(status == 200, "Active profile discovery failed")
    profile = body["profile"]
    ceiling = profile["unloaded_max_mib"]
    require(profile["model"] == MODEL and profile["gpu_uuid"] == CARD and "s2v_gpu_uuid" not in profile
            and type(ceiling) is int and 1 <= ceiling <= 512, "Unexpected active model profile")
    pod("local-model", "python3", "-m", "studio_local_backend", "ready", "--workspace", "/workspace")
    return profile


def lane(action, timeout=20):
    return json.loads(pod("api", "env", "PYTHONPATH=/config/video", "python", "-B", "-c", LANE, action,
                          timeout=timeout))


def store(path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n")


def check_samplers(samplers):
    for process, error_log in samplers:
        if process.poll() is not None:
            raise exec_failure("GPU telemetry", error_log.read_bytes())


@contextmanager
def telemetry(directory, phase):
    commands = {
        "gpu": "--query-gpu=timestamp,uuid,memory.used,utilization.gpu,temperature.gpu",
        "process": "--query-compute-apps=timestamp,gpu_uuid,pid,process_name,used_gpu_memory",
    }
    samplers = []
    try:
        for name, query in commands.items():
            errors = directory / f"{phase}-{name}-errors.txt"
            with (directory / f"{phase}-{name}.csv").open("xb") as output, errors.open("xb") as stderr:
                process = subprocess.Popen(pod_command(
                    "api", "timeout", "1800", "stdbuf", "-oL", "nvidia-smi", query, "--format=csv,noheader,nounits", "-l", "1",
                ), stdout=output, stderr=stderr)
                samplers.append((process, errors))
        gpu_log = directory / f"{phase}-gpu.csv"
        limit = time.monotonic() + 8
        while gpu_log.stat().st_size == 0 and time.monotonic() < limit:
            check_samplers(samplers)
            time.sleep(0.2)
        check_samplers(samplers)
        require(gpu_log.stat().st_size > 0, f"{phase}: GPU telemetry did not start")
        yield samplers
    finally:
        for proc, _ in samplers:
            if proc.poll() is None:
                proc.terminate()
        for proc, _ in samplers:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


class Trial:
    def __init__(self, directory, profile):
        self.directory = directory
        self.profile = profile
        self.deadline = time.monotonic() + WINDOW
        self.pending = None

    def time_left(self):
        return self.deadline - time.monotonic()

    def note(self, name, body):
        store(self.directory / f"{name}.json", body)

    def sample(self, name, action="inspect", *, wait_for_lease=0):
        # A terminal receipt can be visible just before its worker releases
        # the lock. Retry only the explicit busy sentinel within a short bound.
        limit = min(time.monotonic() + wait_for_lease, self.deadline)
        while True:
            result = lane(action, timeout=min(20, self.time_left()))
            if not result.get("lease_busy"):
                self.note(name, result)
                return result
            require(time.monotonic() < limit, "Compute lease remains busy")
            time.sleep(1)

    def request(self, label, route, body, *, budget, reserve=CLEANUP_BUDGET):
        require(self.pending is None, "Original request is unresolved")
        deadline = min(time.monotonic() + budget, self.deadline - reserve)
        require(time.monotonic() < deadline, f"{label}: no time for submission")
        request_id = uuid4().hex
        path = f"{route}/{request_id}"
        data = json.dumps(body).encode()
        self.note(f"{label}-request", {"id": request_id, "path": path, "body": body})
        self.pending = (label, path)

        def call(method):
            return http(8080, path, verb=method, data=data if method == "PUT" else None,
                        timeout=min(12, deadline - time.monotonic()))

        ambiguous = False
        try:
            status, record = call("PUT")
        except Uncertain:
            ambiguous = True
        else:
            self.note(f"{label}-put", {"status": status, "record": record})
            require(status in (200, 202), f"{label}: PUT refused ({status}, {record.get('code')})")
        while time.monotonic() < deadline:
            status, record = call("GET")
            self.note(f"{label}-status", record)
            if ambiguous and status == 404 and record.get("code") in ("request_not_found", "job_not_found", "not_found"):
                # Reuse the exact ID and bytes once, only after a definitive 404.
                ambiguous = False
                response, reply = call("PUT")
                self.note(f"{label}-replay", {"status": response, "record": reply})
                require(response in (200, 202), f"{label}: replay refused ({response}, {reply.get('code')})")
                continue
            ambiguous = False
            require(status == 200, f"{label}: GET refused ({status}, {record.get('code')})")
            if record["state"] in ("succeeded", "failed"):
                self.pending = None
                return record
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        raise Stop(f"{label}: deadline reached, reconcile original ID {request_id}")

    def operation(self, label, operation, *, budget, reserve=CLEANUP_BUDGET, **parameters):
        body = {"profile": self.profile["model"], "operation": operation, **parameters}
        return self.request(label, LOCAL, body, budget=budget, reserve=reserve)

    def load(self, label):
        require(self.time_left() > LOAD_LIMIT + CLEANUP_BUDGET, f"{label}: insufficient load/unload time")
        record = self.operation(label, "load", budget=LOAD_LIMIT)
        baseline = record.get("baseline_memory_mib")
        require(record["state"] == "succeeded" and type(baseline) in (int, float)
                and baseline <= self.profile["unloaded_max_mib"]
                and record["finished_at"] - record["created_at"] <= LOAD_LIMIT, f"{label}: load or baseline failed")
        self.note(f"{label}-timing", {
            "elapsed_seconds": record["finished_at"] - record["created_at"], "target_seconds": 60,
            "potentially_warm_pvc_cache": True,
        })
        return record

    def release(self, label, unload, baseline):
        post_exit = unload.get("released_memory_mib")
        source = "release_receipt"
        diagnostic_error = None
        if post_exit is None and unload["state"] == "failed":
            # A held receipt can omit its failed sample. Preserve a fresh
            # post-exit reading without touching the journal or the hold.
            source = "post_failure_sample"
            try:
                observed = self.sample(f"{label}-diagnostic", "sample")["sample"]
            except Denied:
                raise
            except Stop as error:
                diagnostic_error = str(error)
            else:
                if not observed["resident"]:
                    post_exit = observed["memory_mib"]
        drift = None if post_exit is None else post_exit - baseline
        self.note(f"{label}-drift", {
            "id": unload["id"], "baseline_memory_mib": baseline,
            "post_exit_mib": post_exit, "difference_mib": drift,
            "source": source, "diagnostic_error": diagnostic_error,
        })
        if (unload["state"] != "succeeded" or unload.get("baseline_memory_mib") != baseline
                or drift is None or drift > 4 or post_exit > self.profile["unloaded_max_mib"]):
            raise ReleaseUnverified(f"{label}: release unverified, preserve the lane and review evidence")
        return unload

    def check_peak(self, phase, samplers, *, offset=0):
        check_samplers(samplers)
        with (self.directory / "window-gpu.csv").open(newline="") as source:
            source.seek(offset)
            rows = [row for row in csv.reader(source, skipinitialspace=True) if len(row) == 5]
        require({row[1] for row in rows} == CARDS and all(row[2].isdecimal() for row in rows),
                f"{phase}: missing valid rack GPU telemetry")
        peak = max(int(row[2]) for row in rows if row[1] == CARD)
        host_peak = int(pod("local-model", "cat", "/sys/fs/cgroup/memory.peak",
                            timeout=min(10, self.time_left())))
        self.note(f"{phase}-peak", {"card": CARD, "vram_mib": peak, "cgroup_peak_bytes": host_peak})
        require(peak <= GPU_LIMIT and host_peak <= RAM_LIMIT, f"{phase}: VRAM or host RAM exceeds profile bound")

    def infer(self, label, budget, **parameters):
        record = self.operation(label, "infer", budget=budget, **parameters)
        require(record["state"] == "succeeded", f"{label}: did not succeed, inspect its private receipt")
        result = record["result"]
        require(0 <= result["output_tokens"] <= parameters["max_output_tokens"]
                and 0 <= result["input_tokens"] + parameters["max_output_tokens"] <= self.profile["context_tokens"],
                f"{label}: token bounds failed")
        return json.loads(result["text"])

    def handover(self, baseline, hashes):
        require(self.time_left() >= S2V_BUDGET + S2V_RESERVE, "Insufficient time for S2V, reload and verified cleanup")
        sample = self.sample("before-s2v")
        require(sample["sample"]["resident"] and all(gpu["temperature_c"] < 62 for gpu in sample["rack"]["gpus"]),
                "Model residency or rack temperature does not permit S2V")
        names = ("ls", "-1", "/workspace/local-model/requests")
        before = set(pod("api", *names, timeout=min(10, self.time_left())).decode().splitlines())
        require(self.time_left() >= S2V_BUDGET + S2V_RESERVE, "S2V deadline reserve was consumed")
        record = self.request("s2v", "/v1/jobs/s2v", {
            "profile": "wan2.2-s2v-14b-lightning", "story": STORY, "seed": 442,
            "prompt": "An illustrated figure makes a brief speaking motion. Locked camera.",
            "image_sha256": hashes["s2v.png"], "audio_sha256": hashes["s2v.wav"], "board_frames": 48,
        }, budget=S2V_BUDGET, reserve=S2V_RESERVE)
        after = set(pod("api", *names, timeout=min(10, self.time_left())).decode().splitlines())
        new = after - before
        require(len(new) == 1 and re.fullmatch(r"[0-9a-f]{32}\.json", next(iter(new))), "Ambiguous handover ledger")
        receipt = json.loads(pod("api", "cat", f"/workspace/local-model/requests/{new.pop()}",
                                 timeout=min(10, self.time_left())))
        self.note("handover", receipt)
        self.release("handover", receipt, baseline)
        require(record["state"] == "succeeded" and record.get("prompt_id") and receipt.get("internal") is True
                and receipt["operation"] == "unload" and receipt["finished_at"] <= record["started_at"],
                "S2V success and pre-dispatch unload were not both proved")
        self.sample("after-s2v")

    def settle_pending(self):
        if self.pending is None:
            return
        label, path = self.pending
        # Cleanup never resubmits an operation. Preserve enough time for a
        # tracked unload after the original ID settles.
        cutoff = self.deadline - 75
        while time.monotonic() < cutoff:
            status, record = http(8080, path, timeout=min(10, cutoff - time.monotonic()))
            self.note(f"{label}-cleanup-read", {"status": status, "record": record})
            if status == 404 and record.get("code") in ("request_not_found", "job_not_found", "not_found"):
                self.pending = None
                return
            require(status == 200, f"{label}: original ID read refused ({status}, {record.get('code')})")
            if record["state"] in ("succeeded", "failed"):
                self.pending = None
                return
            time.sleep(min(2, max(0, cutoff - time.monotonic())))
        raise Stop(f"{label}: original request unresolved at cleanup cutoff ({path})")

    def finish(self):
        self.settle_pending()
        state = self.sample("before-final-unload", wait_for_lease=5)
        if state["sample"]["resident"]:
            require(self.time_left() >= 55, "Insufficient time for a safe unload; keep dispatch paused")
            baseline = state["journal"]["baseline_memory_mib"]
            final = self.operation("final-unload", "unload", budget=30, reserve=25)
            self.release("final-unload", final, baseline)
        proof = self.sample("final-idle", "prove", wait_for_lease=3)
        require(not proof["sample"]["resident"] and proof["journal"] is None, "Residency was not cleared")


def stage(directory):
    inputs = directory / "inputs"
    subprocess.run((sys.executable, str(ROOT / "scripts/studio-local-model/smoke-inputs.py"), str(inputs)),
                   check=True, timeout=30)
    hashes = json.loads((inputs / "hashes.json").read_text())
    for name, digest in hashes.items():
        path = inputs / name
        status, body = http(8080, f"/v1/inputs/{digest}{path.suffix}", verb="PUT", data=path.read_bytes())
        store(directory / f"{name}-upload.json", body)
        require(status in (200, 201) and body.get("sha256") == digest and body.get("bytes") == path.stat().st_size,
                f"Upload or checksum read-back failed for {name}")
    return hashes


def handover_window(trial, samplers, hashes):
    load = trial.load("cold-load")
    plan = trial.infer("text-plan", 30, mode="plan_v1", max_output_tokens=256,
                       prompt="Plan two shots for scene s01: an eye-level wide shot, then a close view.")
    require(plan["scene_id"] == "s01" and len(plan["shots"]) == 2, "Unexpected structured text result")
    vision = trial.infer(
        "vision", 55, mode="frame_check_v1", max_output_tokens=512,
        prompt="Report the frames in order. Expected garment green. Return null for unreadable code.",
        image_sha256s=[hashes[f"frame-{index}.png"] for index in range(1, 5)],
    )
    require(vision["frames"] == [
        {"face": "visible", "code": "A17", "wardrobe": "match"},
        {"face": "hidden", "code": "B24", "wardrobe": "match"},
        {"face": "visible", "code": "C38", "wardrobe": "mismatch"},
        {"face": "visible", "code": None, "wardrobe": "uncertain"},
    ], "Unexpected structured vision result")
    trial.check_peak("cold", samplers)
    trial.handover(load["baseline_memory_mib"], hashes)
    offset = (trial.directory / "window-gpu.csv").stat().st_size
    trial.load("return-load")
    return offset


def drift_window(trial):
    for index in range(1, 6):
        load = trial.load(f"cycle-{index}-load")
        unload = trial.operation(f"cycle-{index}-unload", "unload", budget=25)
        trial.release(f"cycle-{index}", unload, load["baseline_memory_mib"])


def run_window(evidence, number):
    previous = evidence / "window-1" / "passed.json"
    if number == 2:
        require(previous.is_file(), "Window 1 must pass before window 2")
    profile = health(True)
    if number == 2:
        require(json.loads(previous.read_text())["profile"] == profile, "Active profile changed since window 1")
    directory = evidence / f"window-{number}"
    directory.mkdir(mode=0o700)
    store(directory / "profile.json", profile)
    hashes = stage(directory) if number == 1 else None
    initial = lane("inspect")
    require(not initial.get("lease_busy") and not initial["sample"]["resident"] and initial["journal"] is None,
            "Start with the host unloaded and the lease clear")
    store(directory / "initial-idle.json", initial)

    try:
        with telemetry(directory, "window") as samplers:
            trial = Trial(directory, profile)
            try:
                if number == 1:
                    offset = handover_window(trial, samplers, hashes)
                else:
                    drift_window(trial)
                    offset = 0
            except (Denied, ReleaseUnverified):
                # A denial is terminal. A failed release can leave a latched
                # hold, which only the operator recovery proof can clear.
                raise
            except BaseException:
                try:
                    trial.finish()
                except BaseException as cleanup_error:
                    trial.note("cleanup-error", {"reason": str(cleanup_error) or type(cleanup_error).__name__})
                raise
            else:
                trial.finish()
            trial.check_peak("return" if number == 1 else "drift", samplers, offset=offset)
        store(directory / "passed.json", {"profile": profile, "finished_at": time.time()})
    except BaseException as error:
        store(directory / "stopped.json", {"reason": str(error) or type(error).__name__})
        raise
    print(f"Window {number} passed with verified final unload and idle proof.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("dormant", "active", "window-1", "window-2", "rollback"))
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.action in ("window-1", "window-2", "rollback"):
            require(args.evidence is not None and args.evidence.is_dir(), "Provide a private evidence directory")
        if args.action in ("window-1", "window-2"):
            run_window(args.evidence, int(args.action[-1]))
        elif args.action == "rollback":
            directory = args.evidence / f"rollback-{uuid4().hex}"
            directory.mkdir(mode=0o700)
            Trial(directory, health(True)).finish()
        else:
            active = args.action == "active"
            profile = health(active)
            evidence = {"discovery": profile}
            if active:
                idle = lane("inspect")
                require(not idle.get("lease_busy") and not idle["sample"]["resident"] and idle["journal"] is None,
                        "Expected a fresh unloaded host and clear lease")
                evidence["idle"] = idle
            else:
                idle = lane("dormant")
                require(not idle.get("lease_busy"), "Expected a clear dormant lease")
                evidence["idle"] = idle
            print(json.dumps(evidence, indent=2))
    except (Stop, KeyError, ValueError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(f"STOP: {error}. Keep dispatch quiesced and preserve evidence.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
