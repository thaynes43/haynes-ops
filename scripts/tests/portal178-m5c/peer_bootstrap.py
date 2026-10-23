#!/usr/bin/env python3
"""Fixed portal SQL through the existing CNPG peer route, no password/identity swap.

Default --preflight reads catalog counts only. --apply-reviewed-sha256 is one
attempt after independent config review/required CI and role/DB provisioning.
No retries: inspect actual outcomes before repeating after an error.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
SQL_FILE = ROOT / ".agents/reports/portal178-m5c/pending/010-bootstrap.sql"
NAMESPACE = "database"
CLUSTER = "postgres16"


def invoke(args, **kwargs):
    # Inherit only this session's normal kubectl identity; no --as, kubeconfig,
    # token or credential flag. A real denial/failure propagates without fallback.
    return subprocess.run(["kubectl", *args], check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--apply-reviewed-sha256")
    args = parser.parse_args()
    cluster = json.loads(invoke(["get", "cluster.postgresql.cnpg.io", CLUSTER,
                                "-n", NAMESPACE, "-o", "json"], capture_output=True).stdout)
    primary = cluster.get("status", {}).get("currentPrimary", "")
    if not re.fullmatch(r"postgres16-[0-9]+", primary):
        raise RuntimeError("unknown primary; stop")
    pod = json.loads(invoke(["get", "pod", primary, "-n", NAMESPACE, "-o", "json"],
                           capture_output=True).stdout)
    if pod["metadata"].get("labels", {}).get("cnpg.io/cluster") != CLUSTER:
        raise RuntimeError("primary ownership mismatch; stop")
    command = ["exec", "-i", "-n", NAMESPACE, primary, "-c", "postgres", "--",
               "psql", "-X", "--no-password", "--set=ON_ERROR_STOP=1",
               "--host=/controller/run", "--username=postgres"]
    if args.preflight:
        source = """SELECT json_build_object('currentUser',current_user,
        'database',current_database(),'isRecovery',pg_is_in_recovery(),
        'targetRoleCount',(SELECT count(*) FROM pg_roles WHERE rolname IN
          ('dev_env_v2_portal_runtime','dev_env_v2_portal_migrator')),
        'targetDatabaseCount',(SELECT count(*) FROM pg_database WHERE datname='dev_env_v2_portal'));"""
        invoke([*command, "--dbname=postgres", "-A", "-t", "--file=-"], input=source)
        return
    expected = args.apply_reviewed_sha256
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RuntimeError("exact reviewed SHA256 required; stop")
    source = SQL_FILE.read_bytes()
    if hashlib.sha256(source).hexdigest() != expected:
        raise RuntimeError("SQL differs from reviewed hash; stop")
    # SQL itself checks exact DB/current role, current writable primary, database
    # and schema ownership and both runtime/migrator non-admin memberships.
    invoke([*command, "--dbname=dev_env_v2_portal", "--file=-"], input=source.decode())


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        # SQL contains no credentials. Do not include environment, command output
        # or exception arguments in the wrapper's own diagnostics.
        print(f"STOP: peer bootstrap {type(error).__name__}; no retry or alternate identity", file=sys.stderr)
        raise SystemExit(1)
