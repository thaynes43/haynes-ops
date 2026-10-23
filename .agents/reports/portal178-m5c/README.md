# Story178 M5C: reviewed client corrections and pending DB/auth packet

Canonical [Story178](https://work.haynesnetwork.com/work_packages/178), Epic120 /
Saga85. One infrastructure implementer `haynes-ops-1023-202021`, no children.
Root retains outcome custody and assigns independent native review; infrastructure
corrections remain with this owner. PR528 UI and the PR529/PR531 Go source owners are disjoint.
No V1, active-agent, shared-login, replication or backup changes.

## States and gates

PR4246 corrected client head is `a5c129e649b7229c22fa32b69f8a380c92bb7d0a`.
Both required aggregates and the isolated hash-locked rendered-content guard PASS.
Advisory review reports no findings. Fresh independent native review of exacta5
is PASS-with-minors, zero blockers, Story178 journal1184; N1–N5 were fixed together.
Earlier d82/d463 reviews are historical and are not relabeled. Optional a5 items
concern documented cross-blueprint semantics, filter breadth and historical
reproducer labels; actual mounted content passed both CI and native checks.
Root owns the fresh selected authenticated preflight and post-install readback.
See [client release packet](../portal178-m5a/RELEASE.md). No installed-client,
login or Gate A claim. Do not deliver Tom's OIDC-copy step before installation and
exact selected metadata/binding/signer/JWKS readback succeed.

Everything in [pending/](pending/) is **unregistered and unapplied**. No production
role, Database, grants, Secret, mapping, consumer or route is created by this packet.
The approved boundary is database `dev_env_v2_portal`, schema `portal`, only
`pending_logins` and `sessions`; runtime CONNECT/USAGE/SELECT/INSERT/DELETE.
Migrator owns only the portal schema/tables. Its credentials never enter the BFF.
Actual installation, privilege/TLS/HBA negatives and runtime Gate A are distinct
acceptance states. Astra's exact admission record and recovery producer are specified and separately
approved; static RBAC/bootstrap/actual runtime and safety acceptance remain pending.

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

Database setup Jobs can project only key `ca.crt` from existing
`postgres16-ca` through a Secret volume with explicit `items: [{key: ca.crt,
path: ca.crt}]`, read-only, automountServiceAccountToken=false. Never `envFrom`,
whole-secret volume, ca.key or Secret API permission. Kubelet rotates the public
file with CNPG; the pool factory must reload it safely. Astra now proposes the BFF identity in `dev-agents`; this same-namespace
volume is a Job-only proposal and cannot deliver its BFF CA. Review a cross-namespace
key-only rotation mechanism before runtime assembly; no new projector/RBAC or
static CA workaround is granted here. TLS requires verify-full and the exact hostname above.

## Provisioning and secret isolation

1. Preflight actual catalog role/database names and ownership; stop on an existing
   name or ambiguity instead of silently adopting it. Review role flags/HBA source
   and live ordering before any role can log in.
   Register role-scoped denies first, then field-scoped role Secrets and managed
   roles, then Database/schema through existing CNPG. Verify actual controller
   reconciliation/status and role memberships; check before duplicate attempts.
2. Apply [010-bootstrap.sql](pending/010-bootstrap.sql) once through the existing
   trusted CNPG **peer psql** route, after source/config review and required CI.
   [The fixed-target helper](../../../../scripts/tests/portal178-m5c/peer_bootstrap.py)
   defaults to catalog-only preflight. Its apply mode requires the reviewed SQL's
   exact SHA256 and one execution, without password retrieval/distribution,
   impersonation, identity flags, retries or credential fallbacks. Root reviews
   the supported command/readback before use. It checks actual primary ownership;
   the SQL checks exact DB/current role, writable primary, database/schema owner
   and non-admin roles/memberships, then changes only new logical-DB ACL/defaults.
   No Job mounts the shared postgres superuser password. The earlier owner Job
   candidate was removed following root's explicit direction, never deployed.
   Actual read-only preflight succeeded via current pod identity: postgres user,
   postgres catalog DB, non-recovery primary, target roles0 and database0. `get
   pods/exec` is allowed (the current websocket route); `create pods/exec` is not.
   Any actual denial stops the action; no identity/token workaround.
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
`utils.py`; root owns tracing/redaction-canary proof before live UserInfo. A
disposable installed-image harness and minimal three-call token-field removal
proposal passed the installed-image disposable harness, with no shared image/logging rollout. No
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

Historical executable packet [run40022341761](https://github.com/thaynes43/haynes-ops/actions/runs/40022341761)
PASS at `843313449389762d586b724b761c4db061425ed8`: **4 packet tests + 4 actual
PostgreSQL groups, zero skips**. Includes executor isolation assertions and
default-authority same-transaction incarnation reads with no added function grant.
That older receipt does not cover later peer-bootstrap/witness/canary additions.
The remaining migration Job was parsed/guarded, not installed or
executed as a Kubernetes Job; actual admission/effective network policy remain
installation gates. Current database namespace has no NetworkPolicy/CiliumNetworkPolicy;
cluster-wide/additive policy behavior must also be verified before claiming isolation.

## Exact additional witness grant and bootstrap boundary

The additional named-object grant is approved in root's exact human receipt,
Sentinel_7123e803ae848191a76e3531619e59e2. [Static RBAC](pending/witness-rbac.yaml)
is prepared but unregistered: exact BFF SA get one ConfigMap, existing recovery
SA get/update one ConfigMap, no other new verb/resource/name. Existing recovery
cluster-wide get/list/watch remains disclosed; the narrow role does not reduce it.
The [CLOSED-only seed](pending/witness-closed-bootstrap.yaml) stays **unregistered**.
Root explicitly rejected a Flux IfNotPresent seed for this portal contract.
Dynamic state/generation/incarnation must remain out of GitOps rollback. After
review/CI, the existing authorized operator route may CREATE it once; name
existence/ownership/outcome check first, no apply/update overwriting or blind
retry, no permanent create grant. Record actual UID/RV privately and leave CLOSED.
CLOSED data intentionally cannot parse as an OPEN admission; no placeholder
generation/incarnation or opening is included. Align with accepted Go source
before deployment (current public PR531 parseAdmission requires8 keys for OPEN).

This V2 pod has no cross-identity SubjectAccessReview create permission, and
performs no --as/alternate-identity workaround. Root's authorized real SAR path
must verify the effective grants after GitOps install: BFF get named object only,
no other CM/Secret reads or mutations/list/watch; recovery update named object
only, no create/patch/delete or update another dev-agents object. Existing recovery
read breadth must remain visible in the evidence. Both witness and BFF SA were
verified absent locally before preparation. No actual RBAC/CM action occurred.
API readback: Service10.43.0.1:443, current endpoints192.168.40.10/.59/.6/.79/.93:6443.
Any future BFF policy selects only its own pods and must be checked against actual
endpoint/Cilium semantics; no shared namespace policy or active agent is changed.

Current executable packet [run40025572973](https://github.com/thaynes43/haynes-ops/actions/runs/40025572973)
PASS at `c3fbe2ab7d0f69e9e38635e3946ee33d2f2880bf`: **5 packet tests + 5 actual
PostgreSQL groups + 12 installed-image canary cases**, zero skips. Includes exact
witness scopes/CLOSED template, migration-only credential/CA guards, SQLSTATE
precondition refusals, actual SQL/TLS/HBA negatives, default-authority same-SQL-
connection incarnation reads, and baseline-versus-minimal-patch logging proof.
The source remains unregistered. Native config acceptance and actual provisioning
are distinct; no runtime/route or usable portal is claimed.
