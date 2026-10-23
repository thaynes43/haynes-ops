#!/usr/bin/env python3
"""Hosted disposable fixture only. No live cluster credentials or endpoints."""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import time
import unittest

import psycopg
from psycopg import sql
import yaml

ROOT = Path(__file__).resolve().parents[3]
PACKET = ROOT / ".agents/reports/portal178-m5c/pending"
RUNTIME = "dev_env_v2_portal_runtime"
MIGRATOR = "dev_env_v2_portal_migrator"
DB = "dev_env_v2_portal"
HOST = "postgres16-rw.database.svc.cluster.local"
IMAGE = "postgres:16@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97"
# Public, disposable fixture values. Never source these from runtime/1Password.
PASSWORDS = {"postgres": "synthetic-ci-admin-only", RUNTIME: "synthetic-runtime-only",
             MIGRATOR: "synthetic-migration-only"}
CONTAINER = os.environ.get("PORTAL_TEST_CONTAINER", "")
TLS = Path(os.environ.get("PORTAL_TEST_TLS", "/nonexistent"))


def connect(role, **overrides):
    config = dict(host=HOST, hostaddr="127.0.0.1", port=5432, dbname=DB, user=role,
                  password=PASSWORDS[role], sslmode="verify-full",
                  sslrootcert=str(TLS / "server.crt"), connect_timeout=5,
                  autocommit=True)
    config.update(overrides)
    return psycopg.connect(**config)


def script(connection, name):
    # psql's ON_ERROR_STOP directive is redundant with psycopg exceptions.
    source = "\n".join(line for line in (PACKET / name).read_text().splitlines()
                       if not line.startswith("\\"))
    connection.execute(source)


class Postgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("GITHUB_ACTIONS") != "true" or not re.fullmatch(r"[0-9a-f]{64}", CONTAINER):
            raise RuntimeError("disposable GitHub service fixture required; no live mode")
        inspect = json.loads(subprocess.check_output(["docker", "inspect", CONTAINER]))[0]
        if inspect["Config"]["Image"] != IMAGE:
            raise RuntimeError("unexpected fixture image")
        deadline = time.monotonic() + 10
        while True:
            try:
                cls.admin = connect("postgres")
                break
            except psycopg.OperationalError as exc:
                if "server does not support SSL" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        cls.source = cls.admin.execute("SELECT host(inet_client_addr())").fetchone()[0]
        cls.fixture_cidr = str(ipaddress.ip_network(cls.source + "/32"))
        for role in (RUNTIME, MIGRATOR):
            cls.admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                                     "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(
                                         sql.Identifier(role), sql.Literal(PASSWORDS[role])))
        cls.admin.execute("CREATE SCHEMA portal AUTHORIZATION dev_env_v2_portal_migrator")
        script(cls.admin, "010-bootstrap.sql")
        with connect(MIGRATOR) as migration:
            script(migration, "020-tables.sql")
            migration.execute("CREATE TABLE portal.future_table (id integer)")
            migration.execute("CREATE FUNCTION portal.future_function() RETURNS integer "
                              "LANGUAGE sql AS 'SELECT 1'")
        cls.admin.execute("CREATE SCHEMA private_fixture")
        cls.admin.execute("CREATE TABLE private_fixture.data (id integer)")
        cls.rules = yaml.safe_load((PACKET / "cluster-portal.patch.yaml").read_text())["spec"]["postgresql"]["pg_hba"]
        cls.install_hba(cls.fixture_cidr)

    @classmethod
    def install_hba(cls, allowed_cidr):
        # Same ordering as live CNPG; only source CIDR differs for runner bridge.
        fixed = ["local all cnpg_metrics_exporter peer map=cnpg_metrics_exporter",
                 "local all all peer map=local",
                 "hostssl postgres streaming_replica all cert map=cnpg_streaming_replica",
                 "hostssl replication streaming_replica all cert map=cnpg_streaming_replica",
                 "hostssl all cnpg_pooler_pgbouncer all cert map=cnpg_pooler_pgbouncer"]
        rules = [line.replace("10.42.0.0/16", allowed_cidr) for line in cls.rules]
        hba = "\n".join(fixed + rules + ["host all all all scram-sha-256", ""])
        subprocess.run(["docker", "exec", "-i", "--user", "postgres", CONTAINER, "sh", "-eu", "-c",
                        'cat > "$PGDATA/pg_hba.conf"'], input=hba, text=True, check=True)
        subprocess.run(["docker", "exec", "-i", "--user", "postgres", CONTAINER, "sh", "-eu", "-c",
                        'cat > "$PGDATA/pg_ident.conf"'], input="local postgres postgres\n", text=True, check=True)
        before = cls.admin.execute("SELECT pg_conf_load_time()").fetchone()
        assert cls.admin.execute("SELECT pg_reload_conf()").fetchone() == (True,)
        # A reload signal is asynchronous. Wait only for this configuration
        # change, without repeating a test suite or generating concurrent load.
        deadline = time.monotonic() + 10
        while cls.admin.execute("SELECT pg_conf_load_time()").fetchone() == before:
            if time.monotonic() >= deadline:
                raise AssertionError("fixture configuration reload not observed")
            time.sleep(0.1)
        # Parse the actual file, not merely the desired rules.
        errors = cls.admin.execute("SELECT error FROM pg_hba_file_rules WHERE error IS NOT NULL").fetchall()
        assert errors == [], errors

    def denied_sql(self, statement, role=RUNTIME):
        with connect(role) as connection:
            with self.assertRaises(psycopg.Error) as failure:
                connection.execute(statement)
            self.assertEqual(failure.exception.sqlstate, "42501", statement)

    def test_01_exact_grants_and_sql_negatives(self):
        with connect(RUNTIME) as runtime:
            self.assertEqual(runtime.execute("SHOW search_path").fetchone(), ("pg_catalog, portal",))
            self.assertEqual(runtime.execute("SHOW statement_timeout").fetchone(), ("5s",))
            for table in ("sessions", "pending_logins"):
                self.assertEqual(runtime.execute(sql.SQL("SELECT count(*) FROM portal.{}").format(sql.Identifier(table))).fetchone(), (0,))
                columns = "handle,generation,version,expires_at,payload"
                values = "decode(repeat('01',32),'hex'),repeat('g',32),1,clock_timestamp()+interval '1 minute',decode(repeat('02',28),'hex')"
                if table == "pending_logins":
                    columns += ",state_digest"
                    values += ",decode(repeat('03',32),'hex')"
                runtime.execute(f"INSERT INTO portal.{table} ({columns}) VALUES ({values})")
                self.assertEqual(runtime.execute(f"DELETE FROM portal.{table} RETURNING version").fetchall(), [(1,)])
        for statement in [
            "UPDATE portal.sessions SET version=1", "UPDATE portal.pending_logins SET version=1",
            "TRUNCATE portal.sessions", "CREATE TABLE portal.denied (id integer)",
            "CREATE TABLE public.denied (id integer)", "CREATE SCHEMA denied",
            "CREATE TEMP TABLE denied (id integer)", "CREATE DATABASE denied",
            "CREATE ROLE denied", "SET ROLE dev_env_v2_portal_migrator", "SET ROLE postgres",
            "ALTER TABLE portal.sessions OWNER TO dev_env_v2_portal_runtime",
            "SELECT * FROM private_fixture.data", "SELECT * FROM portal.future_table",
            "SELECT portal.future_function()", "SELECT * FROM pg_authid",
        ]:
            with self.subTest(sql=statement):
                self.denied_sql(statement)
        for statement in ["CREATE SCHEMA denied", "CREATE TEMP TABLE denied (id integer)",
                          "CREATE TABLE public.denied (id integer)", "SET ROLE postgres",
                          "SELECT * FROM private_fixture.data"]:
            with self.subTest(migrator=statement):
                self.denied_sql(statement, role=MIGRATOR)
        with connect("postgres") as admin:
            self.assertEqual(admin.execute("SELECT has_database_privilege(%s,'postgres','CONNECT')",
                                           (RUNTIME,)).fetchone(), (True,))
            self.assertEqual(admin.execute("SELECT rolname FROM pg_roles WHERE rolname IN (%s,%s) AND "
                             "(rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolinherit)",
                             (RUNTIME, MIGRATOR)).fetchall(), [])
            self.assertEqual(admin.execute("SELECT count(*) FROM pg_auth_members WHERE member IN "
                             "(%s::regrole,%s::regrole)", (RUNTIME, MIGRATOR)).fetchone(), (0,))

    def test_02_transport_and_hba_negatives(self):
        # libpq connect errors do not reliably expose SQLSTATE. Check the exact
        # HBA/x509 reason, never merely 'some connection error'. SQL above uses 42501.
        for role in (RUNTIME, MIGRATOR):
            with self.subTest(role=role, case="plaintext"):
                with self.assertRaises(psycopg.OperationalError) as failure:
                    connect(role, sslmode="disable")
                self.assertIn("pg_hba.conf rejects connection", str(failure.exception))
                self.assertIn("no encryption", str(failure.exception))
            with self.subTest(role=role, case="other-db-public-connect"):
                # postgres retains PUBLIC CONNECT; role-scoped HBA must stop it.
                with self.assertRaises(psycopg.OperationalError) as failure:
                    connect(role, dbname="postgres")
                self.assertIn("pg_hba.conf rejects connection", str(failure.exception))
                self.assertIn('database "postgres"', str(failure.exception))
        with self.assertRaises(psycopg.OperationalError) as failure:
            connect(RUNTIME, host="wrong-host.example.invalid")
        self.assertIn("does not match host name", str(failure.exception))
        with self.assertRaises(psycopg.OperationalError) as failure:
            connect(RUNTIME, sslrootcert=str(TLS / "wrong-ca.crt"))
        self.assertIn("certificate verify failed", str(failure.exception))
        with self.assertRaises(psycopg.OperationalError) as failure:
            connect(RUNTIME, password="synthetic-wrong-password")
        self.assertIn("password authentication failed", str(failure.exception))
        for role in (RUNTIME, MIGRATOR):
            result = subprocess.run(["docker", "exec", "--user", "postgres", CONTAINER,
                                     "psql", "-X", "-U", role, "-d", DB, "-c", "SELECT 1"],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Peer authentication failed", result.stderr)

    def test_03_source_address_and_unrelated_role(self):
        try:
            self.install_hba("203.0.113.254/32")
            with self.assertRaises(psycopg.OperationalError) as failure:
                connect(RUNTIME)
            self.assertIn("pg_hba.conf rejects connection", str(failure.exception))
            with connect("postgres") as unrelated:
                self.assertEqual(unrelated.execute("SELECT 1").fetchone(), (1,))
        finally:
            self.install_hba(self.fixture_cidr)

    def test_04_default_incarnation_read_authority(self):
        # Resolve the remaining proposal's function-authority uncertainty on an
        # actual two-table runtime role. Add no function grant or pg_monitor role.
        statement = """SELECT system_identifier::text,
            left(pg_walfile_name(pg_current_wal_lsn()),8),
            pg_postmaster_start_time(), host(inet_server_addr()), pg_backend_pid()
            FROM pg_control_system()"""
        with connect(RUNTIME) as runtime:
            with runtime.transaction():
                first = runtime.execute(statement).fetchone()
                second = runtime.execute(statement).fetchone()
                self.assertEqual(first, second)
                self.assertRegex(first[0], r"^[0-9]+$")
                self.assertRegex(first[1], r"^[0-9A-F]{8}$")
                self.assertTrue(all(value is not None for value in first))
                self.assertEqual(runtime.execute("SELECT count(*) FROM portal.sessions").fetchone(), (0,))

    def test_05_bootstrap_target_preconditions(self):
        for role, database, reason in [
            ("postgres", "postgres", "portal bootstrap target/owner mismatch"),
            (MIGRATOR, DB, "portal bootstrap target/owner mismatch"),
        ]:
            with self.subTest(role=role, database=database):
                with connect(role, dbname=database) as connection:
                    with self.assertRaises(psycopg.Error) as failure:
                        script(connection, "010-bootstrap.sql")
                    self.assertEqual(failure.exception.sqlstate, "P0001")
                    self.assertIn(reason, str(failure.exception))
                    connection.execute("ROLLBACK")
        # A permission mismatch must not be silently repaired or widen a role.
        self.admin.execute("ALTER ROLE dev_env_v2_portal_runtime INHERIT")
        try:
            with self.assertRaises(psycopg.Error) as failure:
                script(self.admin, "010-bootstrap.sql")
            self.assertEqual(failure.exception.sqlstate, "P0001")
            self.assertIn("portal role authority mismatch", str(failure.exception))
            self.admin.execute("ROLLBACK")
        finally:
            self.admin.execute("ALTER ROLE dev_env_v2_portal_runtime NOINHERIT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
