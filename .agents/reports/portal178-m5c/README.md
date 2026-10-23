# Story178 M5C: reviewed client corrections and pending DB/auth packet

Canonical [Story178](https://work.haynesnetwork.com/work_packages/178), Epic120 /
Saga85. One infrastructure implementer `haynes-ops-1023-202021`, no children.
Root retains outcome custody and assigns independent native review; infrastructure
corrections remain with this owner. PR528 UI and PR529 Go source are disjoint.
No V1, active-agent, shared-login, replication or backup changes.

## States and gates

PR4246 corrected client head is `d4632c1ea145756a99a862dddd998b98649b71e1`.
Both required aggregates and path-scoped guard PASS. Advisory review read and
valid CI-scope finding fixed; final advisory reports no findings. Historical
native `d82f6fbc` PASS-with-minors does not cover this corrected head. Fresh native
recheck and selected authenticated preflight remain required before source merge.
See [client release packet](../portal178-m5a/RELEASE.md). No installed-client,
login or Gate A claim. Do not deliver Tom's OIDC-copy step before installation and
exact selected metadata/binding/signer/JWKS readback succeed.

Everything in [pending/](pending/) is **unregistered and unapplied**. No production
role, Database, grants, Secret, mapping, consumer or route is created by this packet.
The approved boundary is database `dev_env_v2_portal`, schema `portal`, only
`pending_logins` and `sessions`; runtime CONNECT/USAGE/SELECT/INSERT/DELETE.
Migrator owns only the portal schema/tables. Its credentials never enter the BFF.
Actual installation, privilege/TLS/HBA negatives and runtime Gate A are distinct
acceptance states. The external recovery producer/adapter is unresolved.

## Actual read-only infrastructure evidence

2026-10-23: live CNPG operator image `1.30.1`; postgres16 healthy 3/3, min/max sync
replicas both zero. No managed roles, Database CRs or user-defined HBA rules.
The live CRDs support managed roles with explicit non-admin flags and
`passwordSecret`, and Database/schema owner management. They provide no ACL SQL
field for an already-running database. Retain existing trusted CNPG provisioning
for roles/database/schema; do not add a second database controller.

The live pg_hba.conf matches
[pinned CNPG configuration](https://github.com/cloudnative-pg/cloudnative-pg/blob/2a35abb4628f209d149825ef3c38011e0701ff2f/pkg/postgres/configuration.go):
fixed local peer rules, fixed CNPG certificate rules, user-defined rules, then
`host all all all scram-sha-256`. The live local map contains only
`postgres -> postgres`; neither portal role maps. A later user `local reject`
would be shadowed and is not claimed as a security control. Verify actual socket
denial as well as the generated network rules during provisioning.

Actual node pod CIDRs are `10.42.0.0/24` through `10.42.8.0/24`.
[Candidate role/HBA patch](pending/cluster-portal.patch.yaml) proposes the pod
range `10.42.0.0/16` for TLS/SCRAM target-DB connections and rejects both roles
everywhere else, including plaintext, IPv6 and other databases. This contains
PUBLIC CONNECT defaults on other databases **at connection admission**; a
role-local SQL REVOKE alone cannot override PUBLIC. Do not revoke PUBLIC on
shared databases. Final placement/source ranges and label-specific network policy
must be ratified with the runtime, and actual negative tests remain mandatory.
No database-wide network policy affecting existing consumers is proposed.

Read only `/controller/certificates/server-ca.crt` and `server.crt` from the
existing primary. OpenSSL chain + hostname verification PASS for
`postgres16-rw.database.svc.cluster.local`. Public CA SHA256 fingerprint:
`53b084dc3e8fe33fb9e6e1ffd49092cd806a396ef480324a2af0c21720e99f21`.
CA expiry: 2026-11-05 03:55 UTC. No ca.key/server key/Secret values read.

Preferred CA proposal for root/Astra review: place the isolated runtime and
migrator in `database` and project only key `ca.crt` from existing
`postgres16-ca` through a Secret volume with explicit `items: [{key: ca.crt,
path: ca.crt}]`, read-only, automountServiceAccountToken=false. Never `envFrom`,
whole-secret volume, ca.key or Secret API permission. Kubelet rotates the public
file with CNPG; the pool factory must reload it safely. Placement is a proposal,
not a decision or live mount. If another namespace is selected, review a
key-only rotation mechanism first; no new projector/RBAC or static CA workaround
is granted here. TLS requires verify-full and the exact hostname above.

## Provisioning and secret isolation

1. Review role flags/HBA source and live ordering before any role can log in.
   Register role-scoped denies first, then field-scoped role Secrets and managed
   roles, then Database/schema through existing CNPG. Verify actual controller
   reconciliation/status and role memberships; check before duplicate attempts.
2. Apply [010-bootstrap.sql](pending/010-bootstrap.sql) through an isolated,
   reviewed **database-owner bootstrap** component. It only changes ACL/defaults
   and settings inside the new logical database. It does not create credentials.
   It checks target, schema ownership and role authority; PUBLIC rights in the
   new DB and public schema are revoked, CONNECT/USAGE added exactly, no future
   table grant. No runtime or migration pod may receive `POSTGRES_SUPER_PASS`.
   The current CRD cannot perform these ACLs; the exact isolated bootstrap
   execution/credential path requires root/Astra ratification before application.
   No privileged Job, service account or extra grant is invented by this packet.
3. Run [020-tables.sql](pending/020-tables.sql) with migrator only. Definitions
   match reviewed dev-env `a28bea4` migration. Atomic first creation fails on
   collisions; no silent adoption, destructive reset or duplicate retry. Runtime
   grants are exactly SELECT/INSERT/DELETE on the two named tables, no grant
   option. Future objects receive no runtime grant. Runtime owns no objects and
   has no membership/SET ROLE/UPDATE/TRUNCATE/CREATE/TEMP/replication/BYPASSRLS.
4. Run disposable source tests, then actual positive/negative role, HBA, CA and
   hostname tests before mounting any runtime. Inspect effective/default rights,
   not merely SQL text. Verify other DB access denied, including a DB retaining
   PUBLIC CONNECT; preserve existing consumers. No failover/restore on the shared
   cluster is authorized for this verification.

The [role projections](pending/role-externalsecrets.yaml) give CNPG only username
and password as basic-auth Secrets. The [pending runtime projection](pending/runtime-externalsecret.yaml)
contains only the four approved runtime fields, from its dedicated item. No
dataFrom, cross-item reference, migrator/admin field or broad existing item.
Only Tom populates 1P; no production values are generated or retrieved here.
ESO Delete/missing-item behavior does not revoke a password already stored in
PostgreSQL or terminate an existing pool. Missing fields/ESO errors block release;
actual credential revocation and existing-connection containment need an explicit
verified procedure alongside the application fence and local session revocation.

Owner population step, **deliver only after the reviewed consumers exist**:
in HaynesKube create `dev-env-v2-portal-runtime` with top-level DB_USERNAME
`dev_env_v2_portal_runtime`, DB_PASSWORD, SESSION_AEAD_KEY (canonical base64 of
32 cryptographically random bytes) and SESSION_GENERATION (32–128 characters,
coordinated with the eventual external fence). Create `dev-env-v2-portal-migration`
with top-level DB_USERNAME `dev_env_v2_portal_migrator` and DB_PASSWORD. Generate
and save values directly in the supported secure UI; never chat, commands, logs,
Git, errors or tracker. This packet requests no values and delivers no premature
population instruction. Rotate AEAD key and independent generation together;
static generation alone cannot make asynchronous recovery safe.

## Portal-local mapping proposal

[Pending mapping](pending/98-portal-live-profile.yaml) is unattached and denies
admin while its immutable pin is empty. Root must supply the verified SHA256
fingerprint of the existing Admins group UUID (lowercase/hyphenated UTF-8, no
newline); never substitute a mutable name or publish raw UUID. It re-reads the
actual account and **direct** group memberships, returning literal booleans only.
Exceptions omit claims in Authentik, and the BFF must deny missing/wrong types.

Pinned [2026.8.5 UserInfo](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/providers/oauth2/views/userinfo.py)
evaluates provider scope mappings on every request with the exact token's user.
[Mapping context](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/core/expression/evaluator.py)
supplies `user`; the ORM lookup avoids relying on a stale hydrated active flag.
Attach this **additional profile mapping only to this provider**, preserving both
managed openid and profile mappings and their sub/profile output. Do not alter
shared mappings/flows or request broader scopes. Update the exact guard
deliberately with that later reviewed change. Require disposable active/inactive,
direct/inherited-only/nonmember, renamed-same-UUID/replaced-same-name, missing
group/exception and wrong-sub tests before installation/activation. No real
Tom/group edits. Pinned source analysis is not executed IdP integration.

Backend native review also identified a revoked-token warning in Authentik
`utils.py`; root owns tracing/redaction-canary proof before live UserInfo. No
blanket logging change is proposed here. Existing RS256/signer gates remain.

See [recovery proposal](RECOVERY-PROPOSAL.md) for the unresolved primary-bound
witness, with no new privilege or platform claimed.

## Verification limits

Local serial `nice -n 19` packet tests PASS: field/role isolation, retained DB
ownership, exact ordered HBA and synthetic fresh-account/direct immutable-group
mapping semantics. Merged candidate Cluster and Database validate against the
actual live CRD structural schemas; CEL/admission/controller execution is unrun.
The mapping test uses mocked ORM objects, not Authentik's expression sandbox.

The scoped hosted workflow uses digest-pinned disposable PostgreSQL16, synthetic
credentials and ephemeral certificates, at two CPUs with one serial suite. It
applies the actual pending SQL. It substitutes only the runner bridge /32 for
the production pod CIDR and simulates the verified fixed CNPG HBA/ident prefix.
It checks SQLSTATE 42501, precise TLS/HBA/password/peer error reasons, normal
two-table SELECT/INSERT/DELETE, no UPDATE/TRUNCATE/DDL/TEMP/SET ROLE/other schema,
default grants on future objects and PUBLIC CONNECT other-DB containment.
No arbitrary connection error counts as a negative PASS. Hosted execution is
separate evidence; actual production HBA/CA/controller/privilege tests still need
reviewed provisioning. No local PostgreSQL server/container runtime is available.

Hosted [run40021185964](https://github.com/thaynes43/haynes-ops/actions/runs/40021185964)
PASS at exact packet head `e30862a49e6a90b0e08add8240e2d97de6e272b8`: all three
packet tests and all three actual PostgreSQL test groups, zero skips. The initial
run40021038841 failed before SQL setup because a fixture client address carried a
CIDR suffix; `host(inet_client_addr())` corrected the harness. That failure was
not counted as negative security evidence. This is disposable SQL/HBA proof, not
actual CNPG provisioning or recovery-fence/IdP/proxy acceptance.

[Logging evidence](LOGGING-EVIDENCE.md) now includes one safe live synthetic
nonexistent-token canary: denied401 but present in the deployed DEBUG log's
access_token field. Root/Astra owns the resulting UserInfo release hold and
narrow remediation review; no actual credential or directory mutation was used.
