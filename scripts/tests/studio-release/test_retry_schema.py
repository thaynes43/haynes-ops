"""Exercise the deployed OP967 catalogue read against the actual Studio migrations."""

import os
import runpy
import unittest
from pathlib import Path


DSN = os.getenv("STUDIO_OP967_TEST_DATABASE_URL")
CATALOGUE = Path(__file__).resolve().parents[2] / "studio-release/op967-catalogue.sql"


@unittest.skipUnless(DSN, "requires the disposable OP967 PostgreSQL database")
class RetrySchemaTest(unittest.TestCase):
    def test_retry_catalogue_accepts_only_complete_0012_and_0013(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict

        from haynes_studio.db.engine import connect_engine
        from haynes_studio.db.migrate import migrate

        info = conninfo_to_dict(DSN)
        self.assertEqual(info["dbname"], "studio_op967_test")
        self.assertIn(info["host"], {"127.0.0.1", "localhost"})
        query = CATALOGUE.read_text()
        old, new = "0012_release_constraints", "0013_matte_jobs"

        def catalogue(*, read_only=True, search_path="pg_catalog"):
            with psycopg.connect(DSN, options=f"-csearch_path={search_path}") as conn:
                conn.read_only = read_only
                return conn.execute(query).fetchone()[0]

        def check_partial(admin, break_schema, restore_schema):
            admin.execute(break_schema)
            try:
                self.assertEqual(catalogue(), "unverified")
            finally:
                admin.execute(restore_schema)

        def replace_constraint(admin, table, constraint, replacement=None):
            definition, = admin.execute("""
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = %s::regclass AND conname = %s
            """, (f"studio.{table}", constraint)).fetchone()
            name = sql.Identifier("studio", table)
            key = sql.Identifier(constraint)
            drop = sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(name, key)
            add = sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} {}")
            change = drop if replacement is None else drop + sql.SQL("; ") + add.format(
                name, key, sql.SQL(replacement(definition)))
            restore = add.format(name, key, sql.SQL(definition))
            if replacement is not None:
                restore = drop + sql.SQL("; ") + restore
            check_partial(admin, change, restore)

        role = "studio_app"
        with psycopg.connect(DSN, autocommit=True) as admin:
            self.assertEqual(admin.info.server_version // 10000, 16)
            with admin.transaction():
                admin.execute("CREATE ROLE studio_app")
                admin.execute("CREATE SCHEMA studio")
                admin.execute("CREATE SCHEMA op967_standby")
            engine = connect_engine(DSN)
            try:
                migrate(engine, target="0011_worker_lane_grants", runtime_role=role)
                self.assertEqual(catalogue(), "unverified")

                add_column = "ALTER TABLE studio.media_jobs ADD COLUMN matte_releases smallint DEFAULT 0 NOT NULL"
                drop_column = "ALTER TABLE studio.media_jobs DROP COLUMN matte_releases"
                grant = "GRANT UPDATE (matte_releases) ON studio.media_jobs TO studio_app"
                lane_check = "media_job_lanes", "media_job_lanes_lane_check"
                for revision in (old, new):
                    migrate(engine, target=revision, runtime_role=role)
                    self.assertEqual(catalogue(), revision)
                    with self.subTest(revision=revision, missing_check=lane_check[1]):
                        replace_constraint(admin, *lane_check)
                    if revision == old:
                        check_partial(admin, add_column, drop_column)
                        replace_constraint(admin, *lane_check, lambda d: d.replace("'voice'::text", "'matte'::text, 'voice'::text"))

                migrate(engine, runtime_role=role)  # Replacement Job upgrades an already committed head.
                self.assertEqual(catalogue(), new)
                self.assertEqual(catalogue(read_only=False), "unverified")
                # Shadow only the recovery bit. The same search path accepts the real primary first.
                standby_path = "op967_standby,pg_catalog"
                self.assertEqual(catalogue(search_path=standby_path), new)
                admin.execute("CREATE FUNCTION op967_standby.pg_is_in_recovery() RETURNS boolean "
                              "LANGUAGE sql AS 'SELECT true'")
                self.assertEqual(catalogue(search_path=standby_path), "unverified")

                for table, constraint in (
                    ("worker_lane_grants", "worker_lane_grants_lane_check"),
                    ("queue_lane_events", "queue_lane_events_lane_check"),
                    ("media_jobs", "media_jobs_kind_check"),
                    ("media_jobs", "media_jobs_check1"),
                    ("media_jobs", "media_jobs_check2"),
                ):
                    with self.subTest(catalogue_constraint=constraint):
                        replace_constraint(admin, table, constraint)
                replace_constraint(admin, *lane_check, lambda d: d.replace("'matte'::text", "'matte'::text, 'extra'::text"))
                replace_constraint(admin, *lane_check, lambda d: d + " NOT VALID")
                check_partial(admin, "REVOKE UPDATE (matte_releases) ON studio.media_jobs FROM studio_app", grant)
                check_partial(admin, drop_column, f"{add_column}; {grant}")
                for broken, restored in (
                    ("SET DEFAULT 1", "SET DEFAULT 0"),
                    ("DROP NOT NULL", "SET NOT NULL"),
                    ("TYPE integer", "TYPE smallint"),
                ):
                    with self.subTest(column=broken):
                        alter = "ALTER TABLE studio.media_jobs ALTER COLUMN matte_releases "
                        check_partial(admin, alter + broken, alter + restored)
                check_partial(admin, "DELETE FROM studio.media_job_lanes WHERE lane = 'matte'",
                              "INSERT INTO studio.media_job_lanes (lane, next_position) VALUES ('matte', 1)")
                check_partial(admin, "UPDATE studio.media_job_lanes SET next_position = 0 WHERE lane = 'matte'",
                              "UPDATE studio.media_job_lanes SET next_position = 1 WHERE lane = 'matte'")
                check_partial(admin, "INSERT INTO studio.alembic_version VALUES ('0012_release_constraints')",
                              "DELETE FROM studio.alembic_version WHERE version_num = '0012_release_constraints'")

                archive_key = "archive_records", "archive_records_root_version_fkey"
                replace_constraint(admin, "publications", "publications_package_run_target_fkey")
                replace_constraint(admin, *archive_key)
                admin.execute("CREATE TABLE studio.other_versions (record_id text, number bigint, PRIMARY KEY (record_id, number))")
                for pattern, value in (
                    ("studio.archive_versions", "studio.other_versions"),
                    (" DEFERRABLE INITIALLY DEFERRED", ""),
                    ("INITIALLY DEFERRED", "INITIALLY IMMEDIATE"),
                ):
                    with self.subTest(foreign_key=pattern):
                        replace_constraint(admin, *archive_key, lambda d: d.replace(pattern, value))
                self.assertEqual(catalogue(), new)

                snapshot = runpy.run_path(str(CATALOGUE.with_name("op967-data.py")))["database_snapshot"]

                def postflight_results():
                    with psycopg.connect(DSN) as reader:
                        reader.read_only = True
                        results = {}
                        snapshot(reader, "postflight", results)
                        return results

                empty_matte = "matte queue empty (queued, running or held)"
                absent_grant = "no registered matte worker grant"
                self.assertTrue(postflight_results()[empty_matte])
                self.assertTrue(postflight_results()[absent_grant])
                admin.execute("""
                    INSERT INTO studio.media_jobs (
                        id, lane, position, kind, idempotency_target, idempotency_actor, idempotency_key,
                        request_signature, requester, inputs, document
                    ) VALUES (
                        'fixture', 'matte', 1, 'matte', 'fixture', 'fixture', 'fixture', 'fixture',
                        '{}', '{}', '{"status":"queued"}'
                    )
                """)
                self.assertFalse(postflight_results()[empty_matte])
                admin.execute("INSERT INTO studio.worker_lane_grants VALUES ('fixture-matte-worker', 'matte')")
                self.assertFalse(postflight_results()[absent_grant])
            finally:
                engine.dispose()
                admin.execute("DROP SCHEMA studio CASCADE")
                admin.execute("DROP SCHEMA op967_standby CASCADE")
                admin.execute("DROP ROLE studio_app")


if __name__ == "__main__":
    unittest.main()
