"""Exercise the deployed OP967 retry probe against the actual Studio migrations."""

import importlib.util
import os
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path


DSN = os.getenv("STUDIO_OP967_TEST_DATABASE_URL")


@unittest.skipUnless(DSN, "requires the disposable OP967 PostgreSQL database")
class RetrySchemaTest(unittest.TestCase):
    def test_retry_probe_distinguishes_0011_0012_and_0013(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict

        from haynes_studio.db.engine import connect_engine
        from haynes_studio.db.migrate import migrate

        info = conninfo_to_dict(DSN)
        self.assertEqual(info["dbname"], "studio_op967_test")
        self.assertIn(info["host"], {"127.0.0.1", "localhost"})

        path = Path(__file__).resolve().parents[2] / "studio-release/op967-data.py"
        spec = importlib.util.spec_from_file_location("op967_data", path)
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)

        role = "studio_op967_probe"
        with psycopg.connect(DSN, autocommit=True) as admin:
            with admin.transaction():
                admin.execute("CREATE ROLE studio_op967_probe")
                admin.execute("CREATE SCHEMA studio")
            # Clean up the schema and grants before dropping the role.
            try:
                admin.execute("GRANT USAGE ON SCHEMA studio TO studio_op967_probe")
                engine = connect_engine(DSN)
                try:
                    for revision, can_retry in (
                        ("0011_worker_lane_grants", False),
                        ("0012_release_constraints", True),
                        ("0013_matte_jobs", False),
                    ):
                        with self.subTest(revision=revision):
                            migrate(engine, target=revision, runtime_role=role)
                            with psycopg.connect(DSN) as runtime, redirect_stderr(StringIO()):
                                runtime.read_only = True
                                runtime.execute("SET LOCAL ROLE studio_op967_probe")
                                results = {}
                                probe.database_snapshot(runtime, "retry-preflight", results)
                            self.assertIs(
                                results["0012 release constraints present and 0013 release column absent"],
                                can_retry,
                            )
                            if revision == "0011_worker_lane_grants":
                                self.assertTrue(results["required lanes idle (image, voice)"])
                finally:
                    engine.dispose()
            finally:
                admin.execute("DROP SCHEMA studio CASCADE")
                admin.execute("DROP ROLE studio_op967_probe")


if __name__ == "__main__":
    unittest.main()
