# Astra final recovery admission design — source only, extra grant pending

Story178; root delivery `PORTAL178-ASTRA-FINAL-FENCE-20261023-01` supersedes
this packet's preliminary CNPG pre-admission-hook proposal. No witness, extra
RBAC, function grant, controller or new platform is installed. Runtime stays503.

## Authoritative record and producer

Proposed one nonsecret ConfigMap `dev-agents/dev-env-v2-portal-admission`;
root verified it absent at20:44UTC. Proposed BFF ServiceAccount
`dev-agents/dev-env-v2-portal` also absent. The **existing recovery owner**
`dev/dev-env` closes admission and, after measuring the actual recovered primary
and obtaining a fresh independent generation through the approved secret path,
CAS-opens the exact incarnation. No generation reuse or automatic watcher opening.
A logical restore on the same server or a control-plane restore requires CLOSED
admission. No destructive shared-cluster test or replication/backup change.

Additional authorization is **not yet approved**: proposed BFF get one named
ConfigMap; recovery identity update one named ConfigMap (it already has get).
No list/watch/create/patch/delete in this proposed delta. Root used actual SARs,
not impersonation, to verify existing `dev/dev-env` get/list/watch breadth through
its operator ClusterRoleBinding, and no witness write authority. A narrow new Role
would not reduce those existing broad reads; disclose them in the access proposal.
Root must forward the additional approval and settle initial record bootstrap
before any live object/RBAC action. Existing DB/auth approval does not grant it.

Static RBAC belongs in GitOps. Dynamic CLOSED/open/generation/incarnation data
must stay **outside GitOps rollback** so reverting source cannot reopen an old
record. Initial creation is a distinct controlled bootstrap, not a runtime/recovery
create privilege. No arbitrary Kubernetes or service credentials are inferred.

## Same-connection binding and response fence

The record carries schema, CLOSED/open, generation, expected PostgreSQL system ID,
active WAL timeline, postmaster start time, server address and port. A snapshot
also carries Kubernetes UID/resourceVersion.

The BFF must directly GET the exact object with resourceVersion **unset** for
fresh current API semantics: no informer, application cache, mounted ConfigMap or
rv0. Missing/closed/error/malformed/recreated record or mismatch denies. Before
any session SQL on the same acquired transaction/connection, measure system ID,
postmaster start, server address/port, active WAL timeline, writable/non-recovery.
Compare to the admitted snapshot. Re-read before and after COMMIT and require the
identical snapshot. Discard uncertain outcomes without retry. A new primary or
restart mismatches immediately, before any watcher notices; writable is not admitted.
Old-primary connections and two BFF replicas need explicit tests of fresh-record
checks. This source contract belongs to the disjoint Go owner, not this worktree.

Hosted run40022033678 at `1fb8c489aab06e8c71d16d129a397ebc1cfa96bb`
proved the constrained runtime role can read `pg_control_system()` system ID,
current WAL filename timeline, postmaster start, server address and backend PID
in one transaction without a new function grant/pg_monitor/SD wrapper. Root's
read-only live catalog separately confirmed PUBLIC EXECUTE on all seven needed
identity functions, including inet_server_port and pg_is_in_recovery. The actual
production portal role is absent; verify its effective behavior after reviewed
provisioning. Do not add a hypothetical function privilege.
[PostgreSQL16 control data](https://www.postgresql.org/docs/16/functions-info.html#FUNCTIONS-CONTROLDATA),
[WAL functions](https://www.postgresql.org/docs/16/functions-admin.html#FUNCTIONS-ADMIN-BACKUP).

## Infrastructure and negative gates

Root reports no current dev-agents NetworkPolicies. Future API egress belongs in
one dedicated BFF policy, never a change to shared agents. Discover exact API
Service/endpoints before proposing selectors/CIDRs. The proposed BFF namespace
also needs a reviewed public-CA-only rotation mechanism; the database Job's
same-namespace ca.crt volume does not solve cross-namespace BFF CA delivery.

Require real **disposable** async failover losing an acknowledged DELETE with
unchanged witness; same-connection/pool binding, two replicas, postmaster restart,
partition, malformed/recreated/missing records, all transaction boundaries,
uncertain commit, old cookies/callbacks/old-primary connections. Runtime must
neither mutate the witness nor read other ConfigMaps. Measure API request count
and latency per protected request and choose bounds from actual evidence.
Current fixture identity reads are not these recovery tests.

## Historical diagnostic evidence

Live CNPG1.30.1 already maintains `database/postgres16` primary Lease (observed
holderpostgres16-1/transitions8). Its holder is a reusable pod name; in-place
manager upgrades retain it, and a leftover Lease can be adopted by a new Cluster
incarnation. It remains diagnostic evidence, not the portal witness or admission
producer. The final record-plus-measured-connection design avoids depending on a
nonexistent CNPG portal hook or a delayed Lease watcher.
[Producer source](https://github.com/cloudnative-pg/cloudnative-pg/blob/2a35abb4628f209d149825ef3c38011e0701ff2f/internal/cmd/manager/instance/run/lease/runnable.go),
[controller](https://github.com/cloudnative-pg/cloudnative-pg/blob/2a35abb4628f209d149825ef3c38011e0701ff2f/internal/controller/primary_lease.go).
