"""Read the approved proxy/master pair inside the Studio API container."""

import json
import os
import sys


def check(label, passed):
    print(f"{'PASS' if passed else 'FAIL'} {label}", file=sys.stderr)
    return passed


def verified_delivery(conn, proxy, master, delivery_id):
    conn.exec_driver_sql("SET TRANSACTION READ ONLY")
    if not check("delivery transaction is read-only",
                 conn.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"):
        return None
    rows = conn.exec_driver_sql(
        """
        SELECT id, document->'proxy'->>'locator', document->'master'->>'locator'
        FROM deliveries
        WHERE document->'proxy'->>'sha256' = %s
          AND document->'master'->>'sha256' = %s
        LIMIT 2
        """,
        (proxy.sha256, master.sha256),
    ).all()
    if not check("exactly one delivery records the requested proxy and master hashes", len(rows) == 1):
        return None
    row_id, proxy_path, master_path = rows[0]
    if not check("delivery ID and role-specific locators match the approved inputs",
                 (row_id, proxy_path, master_path) == (delivery_id, proxy.text, master.text)):
        return None
    return {"id": row_id, "proxy": proxy_path, "master": master_path}


def main():
    from haynes_studio.canonical.ids import is_id
    from haynes_studio.db.engine import connect_engine, snapshot
    from haynes_studio.store import StoreError, parse_locator

    try:
        proxy_path, master_path, delivery_id = sys.argv[1:]
        proxy, master = parse_locator(proxy_path), parse_locator(master_path)
    except (ValueError, StoreError):
        check("canonical MP4 locators and delivery ID", False)
        return 1
    if not check("canonical MP4 locators and delivery ID",
                 proxy.ext == master.ext == "mp4" and is_id(delivery_id, "dlv")):
        return 1
    try:
        engine = connect_engine(os.environ["STUDIO_DATABASE_URL"], os.getenv("STUDIO_DATABASE_SCHEMA", "studio"))
        with snapshot(engine) as conn:
            delivery = verified_delivery(conn, proxy, master, delivery_id)
    except Exception:
        # Connection and query errors can contain private values.
        check("read-only delivery lookup completed", False)
        return 1
    if delivery is None:
        return 1
    # Captured by the runbook. Every field was checked against the caller's inputs.
    print(json.dumps(delivery, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
