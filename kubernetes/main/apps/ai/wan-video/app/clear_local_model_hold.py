"""Operator-only recovery of a local-model residency journal in the Wan API container."""
import csv
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from urllib.request import urlopen
from uuid import uuid4

import api
import product
import product_local as local


BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
COMPUTE_QUERY = "--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_idle():
    try:
        api.require_empty_queue()
    except (OSError, HTTPException) as exc:
        raise ValueError("ComfyUI queue is unavailable") from exc
    try:
        with urlopen("http://127.0.0.1:8080/health", timeout=5) as response:
            wan = json.load(response)
    except (OSError, HTTPException, ValueError) as exc:
        raise ValueError("Wan health is unavailable") from exc
    require(isinstance(wan, dict) and wan.get("api") == "ok" and "active_job" in wan, "Wan activity is unknown")
    require(wan["active_job"] is None, "A Wan job is active")
    require(wan.get("accepting_jobs") is True, "Wan render hold requires separate recovery")
    try:
        status, foley = api.foley("/v1/ready")
    except (OSError, HTTPException, ValueError) as exc:
        raise ValueError("Foley readiness is unavailable") from exc
    require(status == 200 and isinstance(foley, dict)
            and foley.get("api_version") == product.VERSION and "active_job" in foley,
            "Foley activity is unknown")
    require(foley["active_job"] is None, "A Foley job is active")


def compute_apps(raw, card):
    """Index checked NVML process rows by the exact PID and name."""
    apps = {}
    for row in csv.reader(raw.splitlines(), skipinitialspace=True, strict=True):
        require(len(row) == 4, "Malformed GPU compute-process row")
        uuid, pid, name, memory = (field.strip() for field in row)
        require(uuid == card and pid.isascii() and pid.isdecimal() and int(pid) > 0
                and name and name not in {"N/A", "[N/A]", "[Not Found]", "[Insufficient Permissions]"}
                and memory.isascii() and memory.isdecimal() and int(memory) <= 32768
                and (pid, name) not in apps, "Unverifiable GPU compute-process row")
        apps[pid, name] = int(memory)
    return apps


def calibration_baseline(calibration, profile):
    card, ceiling = profile["gpu_uuid"], profile["unloaded_max_mib"]
    require(calibration.get("gpu_uuid") == card, "Calibration is for a different GPU")
    samples = calibration["samples"]
    require(isinstance(samples, list) and len(samples) >= 3
            and all(local.valid_card_sample(sample, card) and sample["utilization_percent"] <= 5
                    for sample in samples), "Invalid calibration samples")
    baseline = max(sample["memory_mib"] for sample in samples)
    require(max(1, math.ceil(baseline)) == ceiling, "Profile ceiling does not match the recorded calibration")
    return baseline


def path_present(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def require_no_socket():
    # Stale sockets and symlinks still occupy the path. An unreadable path
    # propagates the error because it cannot establish absence.
    require(not path_present(local.socket_path(api.ROOT)), "Sidecar socket path still exists")


def dormant_process_proof(calibration, card):
    require_no_socket()
    recorded = compute_apps(calibration["compute_apps_csv"], card)
    try:
        raw = subprocess.run(
            ["nvidia-smi", "--id=" + card, COMPUTE_QUERY, "--format=csv,noheader,nounits"],
            capture_output=True, check=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("GPU compute-process query failed") from exc
    current = compute_apps(raw, card)
    if current:
        require(BOOT_ID.read_text().strip() == calibration["boot_id"], "Engine PID baseline is from another boot")
    require(all(identity in recorded and memory <= recorded[identity] for identity, memory in current.items()),
            "Model card has a compute process outside the reviewed engine baseline")
    return {"socket_present": False, "compute_apps_csv": raw, "baseline_compute_apps_csv": calibration["compute_apps_csv"]}


def clear(evidence):
    lock_path = api.ROOT / "compute.lock"
    require(lock_path.is_file() and not lock_path.is_symlink(), "Expected the existing Wan compute lock file")
    lease = api.acquire_compute()
    require(lease is not None, "Wan compute lease is held")
    with lease:
        check_idle()
        path = local.state_path(api.ROOT)
        try:
            require(stat.S_ISREG(path.lstat().st_mode), "Residency journal is not a regular file")
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise ValueError("No residency journal to archive") from exc
        state = json.loads(raw)
        require(type(state) is dict and type(state.get("resident")) is bool
                and local.valid_id(state.get("instance"))
                and ("held" not in state or state["held"] is True)
                and ("pending" not in state or local.valid_id(state["pending"])), "Invalid residency journal")
        mounted = local.load_profile(api.CONFIG)
        if mounted is not None:
            require(state.get("held") is True, "Mounted profile has no latched hold. Use the tracked unload procedure.")
        profile = mounted if mounted is not None else evidence["profile"]
        require(profile.get("gpu_uuid") in local.APPROVED_CARDS
                and type(profile.get("unloaded_max_mib")) is int
                and 1 <= profile["unloaded_max_mib"] <= local.MAX_UNLOADED_MIB
                and product.digest(profile) == state.get("profile_sha256"),
                "Profile does not match the residency journal")
        lane = local.Lane(api.ROOT, profile)
        card = profile["gpu_uuid"]
        calibration = evidence.get("calibration")
        baseline = state.get("baseline_memory_mib")
        baseline_source = "residency_journal"
        if mounted is None or "baseline_memory_mib" not in state:
            require(isinstance(calibration, dict), "Reviewed dormant calibration is required")
            calibrated = calibration_baseline(calibration, profile)
            if "baseline_memory_mib" not in state:
                baseline, baseline_source = calibrated, "dormant_calibration"
        require(local.gpu_range(baseline, 0, profile["unloaded_max_mib"]), "Invalid recorded memory baseline")

        samples = []
        residency = []
        for index in range(3):
            if index:
                time.sleep(1)
            if mounted is not None:
                answer = lane.status(residency_only=True)
                require(not answer["resident"], "Sidecar reports a live model child")
                residency.append({key: answer[key] for key in ("gpu_uuid", "profile_sha256", "instance", "resident")})
            else:
                residency.append(dormant_process_proof(calibration, card))
            try:
                sample = local.sample_card(card)
            except (OSError, ValueError, csv.Error, subprocess.SubprocessError) as exc:
                raise ValueError("GPU memory sample is unavailable") from exc
            require(local.valid_card_sample(sample, card), "Invalid GPU memory sample")
            require(lane.released(sample, baseline), "Card is busy or memory exceeds the ceiling or baseline plus 4 MiB")
            samples.append({"time": time.time(), **sample})

        check_idle()
        if mounted is None:
            require_no_socket()
        require(path.read_bytes() == raw, "Residency journal changed during proof")
        now = datetime.now(timezone.utc)
        tag = now.strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex
        archive = path.with_name(f"state.{tag}.json")
        receipt = path.with_name(f"hold-clear.{tag}.json")
        lock = os.fstat(lease.fileno())
        record = {
            "time": now.isoformat(), "lease": {"path": "compute.lock", "device": lock.st_dev, "inode": lock.st_ino},
            "state_sha256": hashlib.sha256(raw).hexdigest(), "archive": archive.name, "receipt": receipt.name,
            "profile_mounted": mounted is not None, "baseline_memory_mib": baseline,
            "baseline_source": baseline_source, "unloaded_max_mib": profile["unloaded_max_mib"],
            "samples": samples, "residency": residency,
        }
        if mounted is None or baseline_source == "dormant_calibration":
            record["calibration_sha256"] = product.digest(calibration)
        local.write_record(receipt, record)
        path.rename(archive)
        local.sync_directory(path.parent)
        return record


def main():
    try:
        evidence = json.load(sys.stdin)
        require(isinstance(evidence, dict), "Expected a recovery evidence object")
        print(json.dumps(clear(evidence), indent=2))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, csv.Error, HTTPException) as exc:
        print(f"REFUSED: {exc}. Check the journal and preserve recovery evidence.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
