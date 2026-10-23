-- Read-only, bounded effective-authority proof. No credential/session row reads.
\set ON_ERROR_STOP on
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SET LOCAL lock_timeout = '1s';
WITH roles AS (
  SELECT * FROM pg_roles WHERE rolname IN
    ('dev_env_v2_portal_runtime','dev_env_v2_portal_migrator')
), db AS (
  SELECT * FROM pg_database WHERE datname=current_database()
), namespaces AS (
  SELECT * FROM pg_namespace WHERE nspname IN ('public','portal')
), tables AS (
  SELECT c.* FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='portal' AND c.relkind IN ('r','p','v','m','f','S') ORDER BY c.oid LIMIT 3
), defaults AS (
  SELECT * FROM pg_default_acl WHERE defaclrole='dev_env_v2_portal_migrator'::regrole ORDER BY oid LIMIT 17
), settings AS (
  SELECT * FROM pg_db_role_setting
  WHERE setdatabase=(SELECT oid FROM db) AND setrole='dev_env_v2_portal_runtime'::regrole
)
SELECT json_build_object('checks',json_build_object(
  'target', current_database()='dev_env_v2_portal' AND current_user='postgres'
    AND NOT pg_is_in_recovery(),
  'roles', (SELECT count(*)=2 AND bool_and(NOT (rolsuper OR rolcreatedb OR rolcreaterole
    OR rolreplication OR rolbypassrls OR rolinherit)) FROM roles)
    AND (SELECT rolcanlogin AND rolconnlimit=8 FROM roles WHERE rolname='dev_env_v2_portal_runtime')
    AND (SELECT NOT rolcanlogin AND rolconnlimit=1 FROM roles WHERE rolname='dev_env_v2_portal_migrator')
    -- Server-side boolean only: never return/hash/print a password verifier.
    AND (SELECT rolpassword IS NULL FROM pg_authid WHERE rolname='dev_env_v2_portal_migrator')
    AND NOT EXISTS(SELECT FROM pg_auth_members WHERE member IN (SELECT oid FROM roles)
      OR roleid IN (SELECT oid FROM roles)),
  'database_acl', (SELECT datdba='postgres'::regrole AND NOT EXISTS(
      SELECT FROM aclexplode(coalesce(datacl,acldefault('d',datdba))) a
      WHERE a.grantee<>'postgres'::regrole AND NOT
        (a.grantee IN (SELECT oid FROM roles) AND a.privilege_type='CONNECT' AND NOT a.is_grantable)) FROM db)
    AND (SELECT bool_and(has_database_privilege(oid,current_database(),'CONNECT')
      AND NOT has_database_privilege(oid,current_database(),'CREATE')
      AND NOT has_database_privilege(oid,current_database(),'TEMP')) FROM roles),
  'schema_acl', (SELECT nspowner='dev_env_v2_portal_migrator'::regrole
      AND has_schema_privilege('dev_env_v2_portal_runtime',oid,'USAGE')
      AND NOT has_schema_privilege('dev_env_v2_portal_runtime',oid,'CREATE')
      AND NOT EXISTS(SELECT FROM aclexplode(coalesce(nspacl,acldefault('n',nspowner))) a
        WHERE a.grantee<>nspowner AND NOT (a.grantee='dev_env_v2_portal_runtime'::regrole
          AND a.privilege_type='USAGE' AND NOT a.is_grantable))
      FROM namespaces WHERE nspname='portal')
    AND (SELECT bool_and(NOT has_schema_privilege(r.oid,n.oid,'USAGE')
      AND NOT has_schema_privilege(r.oid,n.oid,'CREATE')) FROM roles r CROSS JOIN namespaces n
      WHERE n.nspname='public'),
  'tables_acl', (SELECT count(*)=2 AND bool_and(relkind='r'
      AND relname IN ('sessions','pending_logins')
      AND relowner='dev_env_v2_portal_migrator'::regrole
      AND has_table_privilege('dev_env_v2_portal_runtime',oid,'SELECT')
      AND has_table_privilege('dev_env_v2_portal_runtime',oid,'INSERT')
      AND has_table_privilege('dev_env_v2_portal_runtime',oid,'DELETE')
      AND NOT has_table_privilege('dev_env_v2_portal_runtime',oid,'UPDATE,TRUNCATE,REFERENCES,TRIGGER')
      AND NOT has_table_privilege('dev_env_v2_portal_runtime',oid,'SELECT WITH GRANT OPTION,INSERT WITH GRANT OPTION,DELETE WITH GRANT OPTION')
      AND NOT EXISTS(SELECT FROM aclexplode(coalesce(relacl,acldefault('r',relowner))) a
        WHERE a.grantee<>relowner AND NOT (a.grantee='dev_env_v2_portal_runtime'::regrole
          AND a.privilege_type IN ('SELECT','INSERT','DELETE') AND NOT a.is_grantable))) FROM tables),
  'default_acl', (SELECT count(*)<17 FROM defaults) AND NOT EXISTS(SELECT FROM defaults d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
      WHERE a.grantee<>d.defaclrole)
    AND EXISTS(SELECT FROM defaults WHERE defaclnamespace=0 AND defaclobjtype='f'
      AND NOT EXISTS(SELECT FROM aclexplode(defaclacl) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE')),
  'role_settings', (SELECT count(*)=1 AND bool_and(cardinality(setconfig)=2
      AND setconfig @> ARRAY['search_path=pg_catalog, portal','statement_timeout=5s']) FROM settings)
),'catalogs',json_build_object(
  'datacl',(SELECT datacl::text FROM db),
  'nspacl',(SELECT json_object_agg(nspname,nspacl::text) FROM namespaces),
  'relacl',(SELECT json_object_agg(relname,relacl::text) FROM tables),
  'pg_default_acl',(SELECT coalesce(json_agg(json_build_object('namespace',defaclnamespace,
    'type',defaclobjtype,'acl',defaclacl::text)),'[]'::json) FROM defaults),
  'pg_db_role_setting',(SELECT json_agg(setconfig) FROM settings)
));
COMMIT;
