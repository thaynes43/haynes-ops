#!/usr/bin/env python3
"""Fixed portal SQL through the existing CNPG peer route, no password/identity swap.

An explicit mode is required. There is no default.
--preflight reads catalog counts only.
--verify-reviewed-sha256 checks bounded effective ACLs. The SHA256 pin and
review of those exact bytes are the control. A best-effort denylist runs
before psql; it is not an SQL sandbox. The session is then opened read-only.
--apply-reviewed-sha256 bootstraps ACLs; --migrate-reviewed-sha256 executes the
reviewed tables once with SET ROLE migrator. Both require config review/CI and
actual role/DB provisioning; neither distributes a password or enables LOGIN.
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
VERIFY_PREFIX = "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;\nBEGIN READ ONLY;\n"
ALLOWED_META = "\\set ON_ERROR_STOP on"
# Best-effort denylist. The hash pin and review of the exact file are the control.
VERIFY_DENY = re.compile(
    r"(?i)(transaction_read_only|\bread\s+write\b|\breset\b|\bdiscard\b|"
    r"\bset\s+transaction\b|\bstart\s+transaction\b|"
    r"\bset\s+session\s+characteristics\b|default_transaction_read_only)")
STOP_ERRORS = (RuntimeError, subprocess.CalledProcessError, ValueError, KeyError)


def assert_verify_readonly(sql_text):
    """Refuse known read-write and psql escapes before psql. Not an SQL sandbox."""
    for raw in sql_text.splitlines():
        line = raw.strip()
        if line.startswith("\\") and line != ALLOWED_META:
            raise RuntimeError("verification SQL can leave the read-only transaction; stop")
    if VERIFY_DENY.search(sql_text):
        raise RuntimeError("verification SQL can leave the read-only transaction; stop")


def stop(error):
    # Type name only. Do not print the exception text or psql stdout.
    print(f"STOP: peer bootstrap {type(error).__name__}; no retry or alternate identity", file=sys.stderr)
    raise SystemExit(1)


def invoke(args, **kwargs):
    # Inherit only this session's normal kubectl identity; no --as, kubeconfig,
    # token or credential flag. A real denial/failure propagates without fallback.
    return subprocess.run(["kubectl", *args], check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--apply-reviewed-sha256")
    mode.add_argument("--migrate-reviewed-sha256")
    mode.add_argument("--verify-reviewed-sha256")
    args = parser.parse_args()
    cluster = json.loads(invoke(["get", "cluster.postgresql.cnpg.io", CLUSTER,
                                "-n", NAMESPACE, "-o", "json"], capture_output=True).stdout)
    primary = cluster.get("status", {}).get("currentPrimary", "")
    if not re.fullmatch(r"postgres16-[0-9]+", primary):
        raise RuntimeError("unknown primary; stop")
    pod = json.loads(invoke(["get", "pod", primary, "-n", NAMESPACE, "-o", "json"],
                           capture_output=True).stdout)
    metadata = pod["metadata"]
    owners = metadata.get("ownerReferences", [])
    if (metadata.get("labels", {}).get("cnpg.io/cluster") != CLUSTER
        or metadata.get("deletionTimestamp")
        or not cluster.get("metadata", {}).get("uid")
        or not any(owner.get("kind") == "Cluster" and owner.get("name") == CLUSTER
                   and owner.get("uid") == cluster["metadata"]["uid"]
                   and owner.get("controller") is True for owner in owners)):
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
    if args.verify_reviewed_sha256:
        if not re.fullmatch(r"[0-9a-f]{64}", args.verify_reviewed_sha256):
            raise RuntimeError("exact reviewed SHA256 required; stop")
        source = SQL_FILE.with_name("030-verify.sql").read_bytes()
        if hashlib.sha256(source).hexdigest() != args.verify_reviewed_sha256:
            raise RuntimeError("SQL differs from reviewed hash; stop")
        text = source.decode()
        # The digest is the control. The denylist is best-effort and runs before psql.
        assert_verify_readonly(text)
        result = invoke([*command, "--dbname=dev_env_v2_portal", "-q", "-A", "-t", "--file=-"],
                        input=VERIFY_PREFIX + text,
                        capture_output=True)
        proof = json.loads(result.stdout)
        checks = proof.get("checks", {})
        if set(checks) != {"target", "roles", "database_acl", "schema_acl", "tables_acl",
                           "default_acl", "role_settings", "portal_objects",
                           "cluster_role_settings", "portal_backends"}:
            raise RuntimeError("verification shape mismatch; stop")
        print(json.dumps(proof, sort_keys=True))
        if any(value is not True for value in checks.values()):
            raise RuntimeError("effective authority differs from reviewed contract; stop")
        return
    expected = args.apply_reviewed_sha256 or args.migrate_reviewed_sha256
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RuntimeError("exact reviewed SHA256 required; stop")
    selected = SQL_FILE.with_name("020-tables.sql") if args.migrate_reviewed_sha256 else SQL_FILE
    source = selected.read_bytes()
    if hashlib.sha256(source).hexdigest() != expected:
        raise RuntimeError("SQL differs from reviewed hash; stop")
    # SQL itself checks exact DB/current role, current writable primary, database
    # and schema ownership and both runtime/migrator non-admin memberships.
    # This is a one-shot peer session. SET ROLE limits the migration's effective
    # authority to its schema owner; the BFF never receives this route/credential.
    # No Job is registered; failure/uncertain commit requires outcome inspection.
    prefix = "SET ROLE dev_env_v2_portal_migrator;\n" if args.migrate_reviewed_sha256 else ""
    invoke([*command, "--dbname=dev_env_v2_portal", "--file=-"], input=prefix + source.decode())


if __name__ == "__main__":
    try:
        main()
    except STOP_ERRORS as error:
        stop(error)
