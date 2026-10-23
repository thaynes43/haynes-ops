# Astra final recovery admission design — exact grant approved, installation pending

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

Additional exact authorization **is approved** in root's Sentinel_7123e803ae848191a76e3531619e59e2 receipt; source review/CI/install are pending: proposed BFF get one named
ConfigMap; recovery identity update one named ConfigMap (it already has get).
No list/watch/create/patch/delete in this proposed delta. Root used actual SARs,
not impersonation, to verify existing `dev/dev-env` get/list/watch breadth through
its operator ClusterRoleBinding, and no witness write authority. A narrow new Role
would not reduce those existing broad reads; disclose them in the access proposal.
Root forwarded this distinct additional approval. It does not grant any broader
object or runtime action; review/CI and initial bootstrap gates remain mandatory.

Static RBAC belongs in GitOps. Dynamic CLOSED/open/generation/incarnation data
must stay **outside GitOps rollback** so reverting source cannot reopen an old
record. Initial creation is a distinct controlled one-time CREATE from an unregistered
CLOSED-only template, not a runtime/recovery create privilege. Root explicitly
requires the seed to stay outside registered GitOps, including IfNotPresent seeds;
check existence and actual outcomes before creation and never overwrite an existing record. No arbitrary Kubernetes or service credentials are inferred.

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

Read-only correction: dev-agents has **zero Kubernetes NetworkPolicies but14
CiliumNetworkPolicies**. Namespace-wide CCNPs `dev-env-session-platform` and
`dev-env-session-web` select ordinary dev-agents pods, including a prospective
BFF, and add MCP/operator/API and arbitrary publicTCP80/443 access. A narrow CNP
alone cannot subtract those inherited permissions. This corrects root's earlier
unqualified "no NetworkPolicies" statement; existing policies are untouched.

[Proposed exclusions](pending/bff-network-exclusions.json) add only the unique
BFF app-name to both existing NotIn sets; [dedicated policy](pending/bff-networkpolicy.yaml)
then permits only named DNS, exact postgres16 pods:5432, API entity443/6443 and
IdP FQDN443, with ingress only from network/name=traefik AND
instance=traefik-internal-network to proposed backendTLS8443. Neither proposal
is registered. No shared-agent behavior change or route activation is included.
Fresh snapshot confirmed no existing pod has the BFF name; all119 non-BFF
pod selectors remain unchanged across both proposed exclusions (238 comparisons).
The14 namespaced CNP selectors do not select the proposed BFF. Actual Cilium
admission/enforcement, all inherited policies and DNS/FQDN/API SNAT identities
must be rechecked at runtime assembly, including denied public destinations,
MCP/operator/other-DB endpoints and unchanged existing-agent behavior. Recheck
policy and pod-label drift immediately before any separately reviewed registration.
A policy-model comparison is not live enforcement proof. No cross-namespace or
namespace-wide rule affecting sessions is changed here.

API readback: Service10.43.0.1:443; endpoints192.168.40.10/.59/.6/.79/.93:6443.
The BFF also needs a reviewed public-ca.crt-only cross-namespace delivery/rotation
route (current publicCA expires2026-11-05 03:55UTC); peer migration does not solve
it. No new projector/RBAC/secret-read grant or staticCA workaround is invented.
The unconditioned onepassword-connect ClusterSecretStore is existing breadth,
not new permission or proof of item-level RBAC.

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
