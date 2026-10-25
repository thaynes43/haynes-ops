# Story178 M5D: staged registration source, activation still closed

Canonical [Story178](https://work.haynesnetwork.com/work_packages/178), Epic120 /
Saga85. M5D source is this Grok provisioning worktree. The global UX owner is
unchanged and disjoint. No child implementers. Prior M5C Sol source remains the
merged base. Root retains independent review, required CI, merge, and
provisioning custody. Grok's PR528 UI and GoM6's PR531 are disjoint. No
active-agent, V1, shared-login, replication or backup changes.

## States and gates

Client PR4246 was independently reviewed at `a5c129e6`, required checks PASS,
merged by root as `ad1c86b8`. Root reports installation and all21 selected checks
PASS at **2026-10-23 21:30:45 UTC**: exact metadata, enabled0/10/20 bindings,
non-null expected RSA signer, RS256 JWKS and blueprint hash/status, with actual
staged deny for Tom. This is installed-client evidence, not login/Gate A. Root
owns delivery of the minimal secure Authentik Admin UI → HaynesKube OIDC-copy
step. See [client release packet](../portal178-m5a/RELEASE.md).

Registered in this source, and applied only after root review and required CI:
reject-only portal HBA on `postgres16`, the portal-local mapping inside
blueprint 97, and the static witness RBAC. Still unregistered: managed roles,
Database, ExternalSecrets, the runtime hostssl allow, the migrator allow, the
CLOSED admission ConfigMap, BFF, and any route. Runtime remains 503.
See [review corrections](REVIEW-CORRECTIONS.md) and
[recovery proposal](RECOVERY-PROPOSAL.md).

## Actual infrastructure and bounded DB authority

Read-only2026-10-23: existing CNPG1.30.1 (`2a35abb4628f209d149825ef3c38011e0701ff2f`),
postgres16 healthy3/3, min/max sync replicas0 unchanged. No portal managed roles,
Database or custom HBA. The live CRDs support roles/Database/schema ownership,
not SQL ACL provisioning on an existing database. Use that trusted controller;
no new PostgreSQL/Redis platform or controller.

Approved logical DB `dev_env_v2_portal`, ownerpostgres; schema `portal`,
ownermigrator; exactly `pending_logins` and `sessions`. Runtime
`dev_env_v2_portal_runtime` has CONNECT/USAGE/SELECT/INSERT/DELETE only, no
UPDATE/TRUNCATE/CREATE/TEMP, ownership/admin/role/replication/BYPASSRLS or grant
option. Migrator `dev_env_v2_portal_migrator` owns only this schema/tables; never
BFF-mounted. No role-membership edge in either direction is allowed.

The unregistered [role-only preproof stage](pending/roles-stage-preproof.patch.yaml)
sets runtime connectionLimit 8 and migrator 1 without a `postgresql`/HBA stanza.
It is **INERT source only**, not actual ACL, controller, admission, dynamic witness,
database provisioning, or runtime proof. The original unregistered
[combined post-proof patch](pending/cluster-portal.patch.yaml) remains the later
role-plus-HBA composition. Public GoM6 `c03cda77493c37d7bfe55747254ebbcf4dbeac98`
[postgres.go](https://github.com/thaynes43/dev-env/blob/c03cda77493c37d7bfe55747254ebbcf4dbeac98/web/portal/session/postgres.go#L92)
sets MaxConns2/MinConns0. Proposed deployment bound: at most2 replicas plus1
rollout surge, hence6 pool slots plus2 explicit verification/headroom slots =8.
There is no installed BFF/replica count or production portal concurrency to
measure yet; enforce these replica/surge/pool bounds during runtime assembly.
A read-only live snapshot measured51 client backends (1 active, including the
probe),59 total processes, max_connections400. That is contextual headroom, not
capacity/load proof. The serial hosted fixture measures8 idle runtime connections
and rejects the ninth; it does not generate load or change shared capacity.

Migrator is **NOLOGIN at steady state**, including bootstrap and the peer
migration route. No unbounded login window or permanently mounted migration
credential. If a later explicit credential test requires LOGIN, use a reviewed
GitOps migration window and verify NOLOGIN restoration; CNPG remains its source
of truth. Disposable CI enables LOGIN only to prove the missing migrator HBA
allow still rejects the connection, then restores NOLOGIN. Schema-owner SQL
uses `SET ROLE`.

Live generated HBA matches [pinned CNPG](https://github.com/cloudnative-pg/cloudnative-pg/blob/2a35abb4628f209d149825ef3c38011e0701ff2f/pkg/postgres/configuration.go):
fixed local peer/certificate rules, user rules, default `host all all all
scram-sha-256`. Actual local map onlypostgres→postgres; a later user local reject
would be shadowed and is not a control. Stage 1, registered on `cluster16`, explicitly rejects replication, plaintext,
IPv6 and every other database for both portal roles. It contains no `hostssl`
allow. The single runtime `hostssl`/`scram-sha-256` rule in
[hba-stage-allow.yaml](pending/hba-stage-allow.yaml) is inserted immediately
before the first `host all` catch-all only after `010`, `020`, and `030` are
all true. Appending it after that catch-all would never match. There is no
migrator allow, including while the role stays NOLOGIN. The candidate runtime
allow is TLS/SCRAM to the new DB from 10.42.0.0/16. Actual node pod CIDRs10.42.0/24 through
10.42.8/24 fit that bound. HBA contains other DBs' PUBLIC CONNECT at connection
admission; role-local REVOKE cannot subtract PUBLIC. Never revoke PUBLIC on
shared databases. Workload isolation and actual source/order negatives remain
gates; no database-wide NetworkPolicy affecting consumers is proposed.

## One-shot provisioning and effective readback

1. Fresh ownership/absence/catalog/HBA checks, independent config review and
   required CI precede each registration. The staged order is: six registered
   rejects → [roles-only preproof](pending/roles-stage-preproof.patch.yaml) and
   retained Database/schema → [010](pending/010-bootstrap.sql) bootstrap →
   [020](pending/020-tables.sql) tables → [030](pending/030-verify.sql) ACL proof
   → **LAST** runtime-only [hostssl allow](pending/hba-stage-allow.yaml), inserted
   before the `host all` catch-all. The combined
   [cluster-portal.patch.yaml](pending/cluster-portal.patch.yaml) is post-proof
   only, never the first registration. This order is an inert source plan, not
   evidence of ACL/controller/admission proof, dynamic witness creation, or DB
   provisioning. Stop on existing names/ambiguous outcomes; no adoption or replay.
2. The [fixed-target helper](../../../scripts/tests/portal178-m5c/peer_bootstrap.py)
   requires an explicit mode and has no default. `--preflight` and
   `--verify-reviewed-sha256` are read-only. Existing CNPG peer psql is root's
   accepted route for [010-bootstrap.sql](pending/010-bootstrap.sql); apply requires
   `--apply-reviewed-sha256` with the independently reviewed exact SQL hash.
   No password retrieval/distribution, identity flag, `--as`, fallback or retry.
   It checks the current primary and pod ownership label; SQL checks exact
   DB/user, writable primary, DB/schema owners, non-admin flags, migratorNOLOGIN
   and both membership directions. Changes affect only the approved logical DB.
3. Run [020-tables.sql](pending/020-tables.sql) with the same helper's
   **`--migrate-reviewed-sha256`**, which verifies those bytes then opens one peer
   session and `SET ROLE dev_env_v2_portal_migrator`. Effective SQL authority is
   the schema owner; current_user guard enforces it. NOLOGIN stays in effect.
   No rerunnable/Flux Job, SQL ConfigMap or admin password distribution. No image
   is required for this existing psql route. Atomic first creation fails42P07 on
   a collision; inspect the outcome, never reset or blindly retry.
4. **`--verify-reviewed-sha256`** runs [030-verify.sql](pending/030-verify.sql)
   only when the digest matches. The SHA256 pin and review of those exact bytes
   are the control. Before psql, a best-effort denylist refuses
   `transaction_read_only` assignments, `RESET`, `DISCARD`, read-write
   transaction syntax, and any psql backslash line except the exact
   `\set ON_ERROR_STOP on`. That denylist is not an SQL sandbox. Accepted bytes
   are then prefixed with one read-only transaction.
   Bare `--verify` is not a mode. It emits bounded datacl/nspacl/relacl/default-ACL/role-setting
   evidence plus exact effective-rights booleans, including no portal routines or
   extra composite types, no cluster-wide portal role settings, and no runtime or
   migrator backend left on the database. Every check must be literally true. Verify no
   unexpected object/grantee, memberships, table grant option, CREATE/TEMP or
   runtime owner; migrator NOLOGIN, null password and both limits are mandatory.
   Password-null is computed server-side as a boolean; no verifier/password
   value or session row is returned. Repeat actual SQL/HBA/TLS/hostname
   positives/negatives before mounting runtime, preserving other consumers.

Disclosure: existing agent `get pods/exec` into CNPG already permits broad peer
PostgreSQL superuser operation. That breadth predates this task; root explicitly
accepted the fixed reviewed portal-only route, not a new runtime grant or
credential distribution. The BFF receives none of it. Read-only preflight already
succeeded with current V2 identity (postgres/catalogpostgres/nonrecovery,
targetroles0/database0). GET websocket exec is allowed, CREATE exec is not.
An actual denial stops that exact action with no alternate identity/access path.

## Secret, CA and rotation boundaries

[Runtime DB projection](pending/role-externalsecrets.yaml) gives CNPG basic-auth
username/password only, labeled `cnpg.io/reload: "true"`. The NOLOGIN migrator has
`disablePassword: true`, no passwordSecret, ExternalSecret or mounted credential.
The approved migration item/fields stay reserved for a future independently
reviewed explicit LOGIN window, if one is ever needed; the peer SET ROLE route
needs no migration password. Do not populate an unused migration credential.
[Runtime projection](pending/runtime-externalsecret.yaml) has only the approved
four runtime fields, no dataFrom, cross-item, migration or admin value. Existing
unconditioned onepassword-connect ClusterSecretStore breadth predates this PR;
field isolation here does not reduce its existing controller-wide authority.
Only Tom populates values; nothing is generated or retrieved by the worker.

Deleting an ESO/item does not revoke PostgreSQL's stored password or terminate
existing sessions. After reviewed installation, test actual rotation/revocation
propagation and pool containment through an explicit approved procedure. Source
reload labels are not rotation proof. Missing fields/ESO errors block release.

**Supersession, 2026-10-24.** The canonical 1Password item is the existing `HaynesKube/dev-env`. The item names `dev-env-v2-portal` and `dev-env-v2-portal-runtime` in the historical paragraph below are the prior instruction. They are not created. Current ExternalSecret `remoteRef.key` values are `dev-env`. Kubernetes Secret names are unchanged. Unrelated fields already on `dev-env` stay. Runtime property population is held. The next paragraph is retained as that prior instruction.

Historical instruction, superseded for the item name. Owner population instructions are **withheld until reviewed consumers exist**:
HaynesKube `dev-env-v2-portal-runtime`: top-level DB_USERNAME=
`dev_env_v2_portal_runtime`, DB_PASSWORD, SESSION_AEAD_KEY (canonical base64 of32
cryptographically random bytes), SESSION_GENERATION (32–128 characters, aligned
with the external fence). No migration-item population step now: its approved
DB_USERNAME/DB_PASSWORD contract is deferred with its unused consumer. Generate/save directly in supported
secure UI; never chat/commands/logs/Git/errors/tracker. Rotate independent
generation and AEAD key together; static generation is not a recovery fence.

Existing primary public CA/server certificate chain and hostname verified for
`postgres16-rw.database.svc.cluster.local`. Public CA SHA256
`53b084dc3e8fe33fb9e6e1ffd49092cd806a396ef480324a2af0c21720e99f21`,
expires **2026-11-05 03:55 UTC**. No private key/Secret value was read. Runtime
must verify CA + exact hostname and reload rotated publicca.crt safely. BFF in
dev-agents still needs a reviewed public-key-only cross-namespace rotation route.
No new projector/RBAC/Secret permission or static-CA workaround is inferred.
This remains an explicit runtime gate; migration peer psql does not solve it.

## Provider-local mapping and token endpoint contract

Blueprint `97-dev-env-v2-portal-oidc.yaml` is the only owner of the provider
`property_mappings` relation. It keeps the staged deny and adds the portal-local
mapping to that list. The old separate `98` provider entry is not registered
and is not in the tree, so a later edit of 97 cannot be overwritten by it.
The mapping uses root's verified SHA256 of the existing Admins Group.pk:
lowercase hyphenated 36-character UUID, UTF-8/no newline. No raw directory UUID
appears. It freshly re-reads token.user and **direct** memberships, returning
only two literal booleans. It neither reparents Actors nor borrows a parent's
groups/active flag. Empty/missing pin or exceptions cannot authorize (BFF denies
missing/wrong types).

The relation is **exactly 3 mappings**: managed openid, managed profile, and
this portal-local profile. No fourth mapping or ambiguous same-scope override
is permitted. The installed-client guard rejects both a 2-mapping set and a
4th mapping, and it rejects removal of the staged deny. Shared mapping
definitions/flows are untouched. Callback/issuer remain exact; scopes remain
openid/profile. No admin API token.

Actor MIRROR/COPY may satisfy Authentik's effective-policy-user gate. This mapping
reports the actual Actor/service-account's own current row/direct groups: even
an active directly-admin Actor may returntrue/true. Actor expiry is not inferred
from is_active. **Claims alone never admit it**: the BFF must require exact
approved issuer AND Tom sub, plus current booleans. Synthetic cases cover
MIRROR/COPY/expired/service/internal-service accounts with and without direct
membership; actual disposable Authentik expression/UserInfo/Actor/sub tests remain
unrun. No real Tom/group/account mutations. Pin analysis uses
[2026.8.5 source](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/core/models.py),
not assumptions about newer Authentik.

**Binding BFF contract (root-selected F3): portal never calls `/revoke` or
`/introspect`.** Use exact-session current UserInfo plus durable local logout and
short-lived encrypted token storage; no refresh or broader OAuth scope. Root
coordinates the separate GoM6 contract/regression. The additional shared raw-token
DEBUG paths remain disclosed in [logging evidence](LOGGING-EVIDENCE.md). The
three-call UserInfo change is a logging-argument bug fix, not a global fix:
introspection and revocation stay outside it. The candidate image, synthetic
base/patched proof, and rollback packet are in
[the release packet](AUTHENTIK-CANDIDATE-RELEASE.md). This source does not change
the HelmRelease pin. Ordinary GitOps rollout waits until root accepts the
immutable artifact. A synthetic canary is not proof that a real user token was
logged. No production token or live revoked/expired test was used.

## Evidence, with exact coverage

Before this correction, exact main-based ready head `1fdd22ef` passed required
Flux Local/Diff Scope and hosted [run40026958200](https://github.com/thaynes43/haynes-ops/actions/runs/40026958200):
5 packet groups,5 PostgreSQL groups,12 installed-image canary cases, zero skips.
It **does not cover this correction**. Historical frozen2e canary run40023963984
FAILED;714e/c3 and later runs corrected the harness and passed. None is relabeled
as proof for a different head. Current corrected-head receipts belong in the PR
and durable handoff after execution; native exact-head recheck remains required.

Local corrected-source serial nice19:7 packet tests PASS, immutable CNPG and
actual live CRD structural validation PASS, proposed-selector comparison against
all119 observed dev-agents pods ×2 shared tiers =238 unchanged non-BFF matches.
No policy applied; no CEL/admission/controller/effective-network claim. Hosted CI
now downloads SHA256-verified immutable CNPG CRDs and structurally validates them.
The SQL fixture uses the one workflow PostgreSQL digest as its source of truth,
synthetic credentials/certificates and actual SQL, with only the runner source
CIDR substituted. It includes grant-option, both membership-direction, repeated
migration atomicity, IPv6, replication, NOLOGIN/limits and effective-ACL negatives.
New hosted results must pass before these are counted as executed evidence.

Mapping tests mock ORM/execution, not Authentik's sandbox. Installed-image canary
runs actual decorated UserInfo/log processors/Event serialization, with token
lookup and Model.save mocked and DB/network disabled:12 synthetic cases, baseline
log6/6 versus patched0/6. It does not prove event persistence/signals/notifications,
live revoked/expired paths, shared rollout, Go middleware/proxy or runtime Gate A.
