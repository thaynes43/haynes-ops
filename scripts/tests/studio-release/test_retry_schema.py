"""Exercise the deployed OP967 catalogue read against the actual Studio migrations."""

import os
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

        def catalogue(*, read_only=True):
            with psycopg.connect(DSN) as conn:
                conn.read_only = read_only
                return conn.execute(query).fetchone()[0]

        def check_partial(admin, break_schema, restore_schema):
            admin.execute(break_schema)
            try:
                self.assertEqual(catalogue(), "unverified")
            finally:
                admin.execute(restore_schema)

        role = "studio_op967_probe"
        with psycopg.connect(DSN, autocommit=True) as admin:
            with admin.transaction():
                admin.execute("CREATE ROLE studio_op967_probe")
                admin.execute("CREATE SCHEMA studio")
            engine = connect_engine(DSN)
            try:
                admin.execute("GRANT USAGE ON SCHEMA studio TO studio_op967_probe")
                migrate(engine, target="0011_worker_lane_grants", runtime_role=role)
                self.assertEqual(catalogue(), "unverified")

                migrate(engine, target="0012_release_constraints", runtime_role=role)
                self.assertEqual(catalogue(), "0012_release_constraints")
                self.assertEqual(catalogue(read_only=False), "unverified")
                add_column = "ALTER TABLE studio.media_jobs ADD COLUMN matte_releases smallint DEFAULT 0 NOT NULL"
                drop_column = "ALTER TABLE studio.media_jobs DROP COLUMN matte_releases"
                check_partial(admin, add_column, drop_column)
                check_partial(admin, """
                    ALTER TABLE studio.media_job_lanes DROP CONSTRAINT media_job_lanes_lane_check,
                        ADD CONSTRAINT media_job_lanes_lane_check CHECK (lane IN ('image', 'voice', 'matte'))
                """, """
                    ALTER TABLE studio.media_job_lanes DROP CONSTRAINT media_job_lanes_lane_check,
                        ADD CONSTRAINT media_job_lanes_lane_check CHECK (lane IN ('image', 'voice'))
                """)

                migrate(engine, runtime_role=role)
                self.assertEqual(catalogue(), "0013_matte_jobs")
                migrate(engine, runtime_role=role)  # Replacement Job upgrades an already committed head.
                self.assertEqual(catalogue(), "0013_matte_jobs")
                check_partial(admin, drop_column, add_column)
                check_partial(admin, "ALTER TABLE studio.media_jobs ALTER COLUMN matte_releases SET DEFAULT 1",
                              "ALTER TABLE studio.media_jobs ALTER COLUMN matte_releases SET DEFAULT 0")
                check_partial(admin, "DELETE FROM studio.media_job_lanes WHERE lane = 'matte'",
                              "INSERT INTO studio.media_job_lanes (lane, next_position) VALUES ('matte', 1)")
                check_partial(admin, "INSERT INTO studio.alembic_version VALUES ('0012_release_constraints')",
                              "DELETE FROM studio.alembic_version WHERE version_num = '0012_release_constraints'")

                for table, constraint in (
                    ("publications", "publications_package_run_target_fkey"),
                    ("archive_records", "archive_records_root_version_fkey"),
                ):
                    with self.subTest(missing=constraint):
                        definition, = admin.execute("""
                            SELECT pg_get_constraintdef(oid) FROM pg_constraint
                            WHERE conrelid = %s::regclass AND conname = %s
                        """, (f"studio.{table}", constraint)).fetchone()
                        name = sql.Identifier("studio", table)
                        key = sql.Identifier(constraint)
                        check_partial(admin, sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(name, key),
                                      sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} {}").format(
                                          name, key, sql.SQL(definition)))
            finally:
                engine.dispose()
                admin.execute("DROP SCHEMA studio CASCADE")
                admin.execute("DROP ROLE studio_op967_probe")


if __name__ == "__main__":
    unittest.main()
