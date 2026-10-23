-- UNAPPLIED. One isolated, reviewed database-owner bootstrap, never BFF init SQL.
-- CNPG manages both roles, the database (owner postgres), and portal schema.
-- There are no passwords or role creation statements here.
\set ON_ERROR_STOP on
BEGIN;
DO $guard$
BEGIN
  IF current_database() <> 'dev_env_v2_portal' OR current_user <> 'postgres' THEN
    RAISE EXCEPTION 'portal bootstrap target/owner mismatch';
  END IF;
  IF pg_is_in_recovery() OR current_setting('transaction_read_only') <> 'off' THEN
    RAISE EXCEPTION 'portal bootstrap requires current writable primary';
  END IF;
  IF (SELECT datdba FROM pg_database WHERE datname = current_database())
      IS DISTINCT FROM 'postgres'::regrole::oid THEN
    RAISE EXCEPTION 'portal database owner mismatch';
  END IF;
  IF (SELECT nspowner FROM pg_namespace WHERE nspname = 'portal')
      IS DISTINCT FROM 'dev_env_v2_portal_migrator'::regrole::oid THEN
    RAISE EXCEPTION 'portal schema owner mismatch';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname IN
      ('dev_env_v2_portal_runtime', 'dev_env_v2_portal_migrator')
    AND (NOT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolinherit)
  ) OR EXISTS (
    SELECT 1 FROM pg_auth_members WHERE member IN
      ('dev_env_v2_portal_runtime'::regrole, 'dev_env_v2_portal_migrator'::regrole)
  ) THEN
    RAISE EXCEPTION 'portal role authority mismatch';
  END IF;
END
$guard$;
REVOKE ALL ON DATABASE dev_env_v2_portal FROM PUBLIC,
  dev_env_v2_portal_runtime, dev_env_v2_portal_migrator;
GRANT CONNECT ON DATABASE dev_env_v2_portal
  TO dev_env_v2_portal_runtime, dev_env_v2_portal_migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC,
  dev_env_v2_portal_runtime, dev_env_v2_portal_migrator;
REVOKE ALL ON SCHEMA portal FROM PUBLIC, dev_env_v2_portal_runtime;
GRANT USAGE ON SCHEMA portal TO dev_env_v2_portal_runtime;
-- Future tables receive no blanket runtime grant; grant only the two below.
ALTER DEFAULT PRIVILEGES FOR ROLE dev_env_v2_portal_migrator IN SCHEMA portal
  REVOKE ALL ON TABLES FROM PUBLIC, dev_env_v2_portal_runtime;
-- Function EXECUTE defaults are global; a per-schema REVOKE cannot cancel them.
ALTER DEFAULT PRIVILEGES FOR ROLE dev_env_v2_portal_migrator
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER ROLE dev_env_v2_portal_runtime IN DATABASE dev_env_v2_portal
  SET search_path = pg_catalog, portal;
ALTER ROLE dev_env_v2_portal_runtime IN DATABASE dev_env_v2_portal
  SET statement_timeout = '5s';
COMMIT;
