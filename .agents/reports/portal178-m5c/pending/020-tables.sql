-- UNAPPLIED. Separate migrator only; never mounted in BFF.
-- Table definitions match dev-env a28bea4c1701b1100438855ae06f2f70e88accc4
-- web/portal/session/migrations/001_sessions.sql. No UPDATE or future-table grant.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '5s';
DO $guard$
BEGIN
  IF current_database() <> 'dev_env_v2_portal'
      OR current_user <> 'dev_env_v2_portal_migrator' THEN
    RAISE EXCEPTION 'portal migration target/owner mismatch';
  END IF;
  IF pg_is_in_recovery() OR current_setting('transaction_read_only') <> 'off' THEN
    RAISE EXCEPTION 'portal migration requires current writable primary';
  END IF;
  IF (SELECT nspowner FROM pg_namespace WHERE nspname='portal')
      IS DISTINCT FROM current_user::regrole::oid THEN
    RAISE EXCEPTION 'portal migration schema owner mismatch';
  END IF;
END
$guard$;
CREATE TABLE portal.pending_logins (
    handle bytea PRIMARY KEY CHECK (octet_length(handle) = 32),
    generation text NOT NULL CHECK (length(generation) BETWEEN 32 AND 128),
    version smallint NOT NULL CHECK (version = 1),
    state_digest bytea NOT NULL CHECK (octet_length(state_digest) = 32),
    expires_at timestamptz NOT NULL,
    payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 28 AND 131072)
);
CREATE TABLE portal.sessions (
    handle bytea PRIMARY KEY CHECK (octet_length(handle) = 32),
    generation text NOT NULL CHECK (length(generation) BETWEEN 32 AND 128),
    version smallint NOT NULL CHECK (version = 1),
    expires_at timestamptz NOT NULL,
    payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 28 AND 131072)
);
REVOKE ALL ON portal.pending_logins, portal.sessions FROM PUBLIC, dev_env_v2_portal_runtime;
GRANT SELECT, INSERT, DELETE ON portal.pending_logins, portal.sessions
  TO dev_env_v2_portal_runtime;
COMMIT;
