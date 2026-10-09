#!/usr/bin/env python3
"""Prepare private, hash-bound inputs for review; never call an API or launch a Job."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def artifact(path):
    raw = path.read_bytes()
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}


def write_private(path, value):
    raw = (json.dumps(value, indent=2) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--selected-scope", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--packet-dir", type=Path, required=True)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    args = parser.parse_args()
    if not all(path.is_absolute() for path in (args.selected_scope, args.output_dir, args.packet_dir, args.kubeconfig)):
        raise ValueError("review paths must be absolute")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise ValueError("output directory must be unused")
    # The kubeconfig is a reference only. Never read it or put its contents in a packet.
    spec = importlib.util.spec_from_file_location("prepared_host", HERE / "run-live-byte-baseline.py")
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    frozen = json.loads((HERE / "frozen-dependencies.json").read_bytes())
    dependencies = frozen["dependencies"]
    manifest_entry = artifact(HERE / "live-baseline-closed-manifest.json")
    manifest = json.loads(Path(manifest_entry["path"]).read_bytes())
    phase = manifest["metadata"]["labels"][host.LABEL]
    if manifest["metadata"]["name"] != host.NAME or manifest["spec"]["template"]["metadata"]["labels"][host.LABEL] != phase:
        raise ValueError("reviewed manifest identity differs")
    contract = {"schema": 1, "output_dir": str(args.output_dir), "phase_token": phase}
    for key in host.PINS:
        if key == "closed_manifest":
            entry = manifest_entry
        elif key == "selected_scope":
            entry = artifact(args.selected_scope)
        else:
            entry = artifact(HERE / dependencies[key]["path"])
        if entry["sha256"] != host.PINS[key]:
            raise ValueError("reviewed dependency pin differs: " + key)
        contract[key] = entry
    args.packet_dir.mkdir(mode=0o700, exist_ok=False)
    contract_path = args.packet_dir / "live-launch-contract.json"
    write_private(contract_path, contract)
    files = {str(path.resolve()): artifact(path) for path in HERE.rglob("*") if path.is_file() and "__pycache__" not in path.parts}
    files[str(contract_path)] = artifact(contract_path)
    packet = {
        "schema": 1, "prepared_only": True, "runtime_authorization": False,
        "production_operations": 0, "files": files,
        "private_selected_scope": contract["selected_scope"],
        "successor_of_v2_sha256": frozen["v2_packet_sha256"],
        "launch_argv": ["env", "KUBECONFIG=" + str(args.kubeconfig), "nice", "-n", "19", "python3", "-B", str(HERE / "run-live-byte-baseline.py"), "--contract", str(contract_path), "--root-authorization", host.GO],
        "unchanged_bounds": {"collection_seconds": 180, "total_cleanup_seconds": 200, "artifact_bytes": 33554432, "input_seconds": 12, "ack_seconds": 5, "retries": 0},
        "unbound": ["actual name absence and server admission", "actual Job/Pod UIDs and native binding", "current corpus and original capture clocks", "ACK, successful completion, and foreground absence proof"],
    }
    packet_path = args.packet_dir / "immutable-review-packet.json"
    write_private(packet_path, packet)
    print(json.dumps({"prepared_only": True, "packet": artifact(packet_path), "phase_token": phase, "job_name": host.NAME, "production_operations": 0}))


if __name__ == "__main__":
    main()
