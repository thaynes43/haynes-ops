# Studio database on postgres16

[OP431](https://work.haynesnetwork.com/work_packages/431) adds a retained CNPG
`Database` named `studio` on `database/postgres16`. PostgreSQL's `postgres`
role owns the database. `studio_migrator` owns the `studio` schema, and
`studio_app` is the API runtime role. Both logins require TLS and SCRAM from
the IPv4 pod network. Their other TCP connections hit explicit HBA rejects.

## Consumer contract

The `ai/studio-database` Flux Kustomization projects the two CNPG passwords
into separate libpq connection strings for the primary service. Have the
[OP434](https://work.haynesnetwork.com/work_packages/434) deployment depend
on this Kustomization. Wire individual `secretKeyRef` entries as follows.

| Consumer | Secret in `ai` | Key and environment variable |
| --- | --- | --- |
| API | `studio-runtime-db` | `STUDIO_DATABASE_URL` |
| One-shot migration Job | `studio-migrator-db` | `STUDIO_MIGRATION_DATABASE_URL` |

Set `STUDIO_DATABASE_SCHEMA=studio` for both. The web, MCP and render
workers use the API and need no database Secret. Run the two workloads with
their own service accounts. The projection readers are for ESO's TokenRequest
calls only.

ESO generates each CNPG basic-auth Secret once in `database`. The two
namespaced stores each authenticate as a different `ai` service account.
Each account can `get` its own named source Secret. Kubernetes' default
`system:basic-user` binding provides the store permission review. The
projection reads the username and password by property and refreshes every
minute. Both DSNs require TLS with SCRAM channel binding. The project uses
the cluster's Postgres TLS setup without distributing the CNPG CA Secret.
If a source Secret is regenerated, verify CNPG and the projection have
converged and restart its consumers to pick up the new environment value.
[ESO 2.15.0 permission validation](https://github.com/external-secrets/external-secrets/blob/v2.15.0/providers/v1/kubernetes/validate.go)
[PostgreSQL 16 connection options](https://www.postgresql.org/docs/16/libpq-connect.html#LIBPQ-CONNECT-CHANNEL-BINDING)

## Reconciliation and owner bootstrap

The following commands are for the authorized coordinator. On an initial
installation, check for name collisions from the current primary before
reconciling. Use the primary lookup and peer connection below with `-d postgres`
and expect no rows from both queries. Investigate any existing database or role
before proceeding.

```sql
SELECT datname FROM pg_database WHERE datname = 'studio';
SELECT rolname FROM pg_roles WHERE rolname IN ('studio_app', 'studio_migrator');
```

Capture PostgreSQL pod UIDs and restart counts before and after reconciliation.
Require all three instances ready. Wait for the CNPG `Database` generation
to be applied, both roles to be reconciled and all four ExternalSecrets to be
ready. Capture statuses only.

```sh
kubectl -n database get pods -l cnpg.io/cluster=postgres16 \
  -o 'custom-columns=NAME:.metadata.name,UID:.metadata.uid,RESTARTS:.status.containerStatuses[*].restartCount'
kubectl -n database get cluster.postgresql.cnpg.io postgres16 \
  -o 'custom-columns=NAME:.metadata.name,READY:.status.readyInstances,ROLES:.status.managedRolesStatus.byStatus.reconciled'
kubectl -n database get databases.postgresql.cnpg.io studio \
  -o 'custom-columns=NAME:.metadata.name,GEN:.metadata.generation,OBSERVED:.status.observedGeneration,APPLIED:.status.applied'
for namespace in database ai; do
  kubectl -n "$namespace" wait --for=condition=Ready --timeout=2m \
    externalsecret/studio-runtime-db externalsecret/studio-migrator-db
done
```

CNPG creates the database and schema. Its `Database` spec has no ACL fields.
Before OP434 starts the migration or starts using the database, run the
following once through the existing local `postgres` peer path. The guard
requires the expected database, owners and unprivileged roles. The changes are
restricted to the new database and can be retried.

```sh
primary=$(kubectl -n database get cluster.postgresql.cnpg.io postgres16 \
  -o jsonpath='{.status.currentPrimary}')
kubectl -n database exec -i "$primary" -c postgres -- \
  psql -X -w -h /controller/run -U postgres -d studio -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
DO $guard$
BEGIN
  IF current_database() <> 'studio' OR current_user <> 'postgres'
     OR pg_is_in_recovery()
     OR (SELECT datdba FROM pg_database WHERE datname = 'studio')
        IS DISTINCT FROM 'postgres'::regrole::oid
     OR (SELECT nspowner FROM pg_namespace WHERE nspname = 'studio')
        IS DISTINCT FROM 'studio_migrator'::regrole::oid THEN
    RAISE EXCEPTION 'Studio database, owner, or primary mismatch';
  END IF;
  IF (SELECT count(*) FROM pg_roles
      WHERE rolname IN ('studio_app', 'studio_migrator')
        AND rolcanlogin AND NOT (rolsuper OR rolcreatedb OR rolcreaterole
                                OR rolreplication OR rolbypassrls OR rolinherit)) <> 2
     OR EXISTS (SELECT FROM pg_auth_members
                WHERE member IN ('studio_app'::regrole, 'studio_migrator'::regrole)
                   OR roleid IN ('studio_app'::regrole, 'studio_migrator'::regrole)) THEN
    RAISE EXCEPTION 'Studio role privileges mismatch';
  END IF;
END
$guard$;
REVOKE ALL ON DATABASE studio FROM PUBLIC;
GRANT CONNECT ON DATABASE studio TO studio_app, studio_migrator;
REVOKE ALL ON SCHEMA public, studio FROM PUBLIC;
GRANT USAGE ON SCHEMA studio TO studio_app;
COMMIT;
SQL
```

## OP434 migration and grants

Run `studio-api-migrate upgrade head` once in the Job under `studio_migrator`
using the pinned Studio API image. The command holds a database advisory
transaction lock. Run these grants in a transaction using the same Job's
migration connection after the command succeeds. The app's image can use
psycopg for the grant transaction. Release API replicas after both steps
succeed. A retry at the same migration head reapplies the same grants.

```sql
GRANT SELECT, INSERT, UPDATE ON studio.productions, studio.stories,
    studio.boards, studio.selections TO studio_app;
GRANT SELECT, INSERT ON studio.media_types, studio.draft_ids, studio.assets,
    studio.asset_versions, studio.asset_sources, studio.asset_media,
    studio.deliveries, studio.delivery_media TO studio_app;
GRANT SELECT, INSERT, DELETE ON studio.login_attempts, studio.sessions,
    studio.story_asset_refs TO studio_app;
GRANT SELECT ON studio.agent_keys, studio.agent_grants, studio.worker_tokens TO studio_app;
```

Keep additional grants explicit in the migration that requires them. The
runtime role gets no create, temporary table, sequence or Alembic history
privileges. Credential-table provisioning uses a separate owner-managed path.
Check the applied Alembic revision with the migration identity. For the
initial schema in [Studio PR 86](https://github.com/thaynes43/haynes-studio/pull/86)
the output is `0001_studio_metadata`.

```sql
SELECT version_num FROM studio.alembic_version;
```

## Credential-free access evidence

Review the rendered HBA rules on the Postgres instances using the peer path
above. Expect zero syntax errors and the three Studio rules in order on each
instance. Compare the pod UIDs and restart counts with the pre-change capture.

```sql
SELECT count(*) AS hba_errors FROM pg_hba_file_rules WHERE error IS NOT NULL;
SELECT type, database, address, auth_method
FROM pg_hba_file_rules
WHERE user_name @> ARRAY['studio_app', 'studio_migrator']
ORDER BY rule_number;
SELECT pg_postmaster_start_time(), pg_conf_load_time();
```

From a permitted `ai` workload with the chosen key injected via
`secretKeyRef`, run the following with the role argument set to `studio_app`
or `studio_migrator`. Use the same fragment within the migration Job before
it exits. The checks print role and outcome only. Run the runtime checks
after the grants.

```sh
kubectl -n ai exec -i "$verification_pod" -c "$verification_container" -- python - studio_app <<'PY'
import os
import sys

import psycopg

role = sys.argv[1]
key = {
    "studio_app": "STUDIO_DATABASE_URL",
    "studio_migrator": "STUDIO_MIGRATION_DATABASE_URL",
}[role]
dsn = os.environ[key]
with psycopg.connect(dsn) as connection:
    actual = connection.execute(
        "SELECT current_user, current_database(), ssl "
        "FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
    ).fetchone()
    assert actual == (role, "studio", True)
    if role == "studio_app":
        privileges = connection.execute("""
            SELECT has_schema_privilege(current_user, 'studio', 'USAGE'),
                   has_schema_privilege(current_user, 'studio', 'CREATE'),
                   has_database_privilege(current_user, 'studio', 'TEMP'),
                   has_table_privilege(current_user, 'studio.productions', 'UPDATE'),
                   has_table_privilege(current_user, 'studio.agent_keys', 'INSERT')
        """).fetchone()
        assert privileges == (True, False, False, True, False)
    else:
        version = connection.execute(
            "SELECT version_num FROM studio.alembic_version"
        ).fetchone()[0]
        print("Alembic revision:", version)
print(role, "connected to studio over TLS")

for target, override in (
    ("other database", {"dbname": "postgres"}),
    ("plaintext", {"sslmode": "disable", "channel_binding": "disable"}),
):
    try:
        connection = psycopg.connect(dsn, **override)
    except psycopg.OperationalError as error:
        if "pg_hba.conf rejects connection" not in str(error):
            raise SystemExit(f"{role}: expected HBA rejection for {target}") from None
    else:
        connection.close()
        raise SystemExit(f"{role}: unexpectedly allowed {target}")
    print(role, "HBA rejected", target)
PY
```

## Backup and rollback

D47 relies on the existing `postgres16` daily Barman physical base backups
and archived WAL, which include the Studio database and its role catalog.
Check a completed backup after provisioning. The NFS media share is outside
that coverage. [CNPG 1.30 backup scope](https://cloudnative-pg.io/docs/1.30/backup/#main-concepts)

For rollback, stop Studio clients and the Job first. Keep the `Database`
and its schema, and keep both managed roles. In a GitOps commit change the
roles to `login: false` and `disablePassword: true`, remove their
`passwordSecret` references and remove the Studio `hostssl` allow. Keep
the Studio rejects ahead of CNPG's default rule. Verify both logins disabled
and Studio sessions drained before removing the projections, readers and
generators in a follow-up. Preserve the database, schema owners, ACLs,
tables and migration revision. The Flux prune exemption and CNPG reclaim
policy retain the database. Restore via an isolated target or recover forward.
Removing inline role definitions leaves their PostgreSQL roles unmanaged,
including their LOGIN settings. Use the staged rollback above.
[CNPG 1.30 managed roles](https://cloudnative-pg.io/docs/1.30/declarative_role_management/#inline-managed-roles)
