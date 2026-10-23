# Existing CNPG primary authority: proposal for root/Astra, not activation

Actual producer found: CNPG 1.30.1's instance-manager primary Lease, currently
`database/postgres16`, holder `postgres16-1`, transitions 8. Existing controller
creates/adopts it and instance managers acquire/renew it. No new witness service,
Kubernetes reader grant or replication/backup change has been created.

Pinned sources:
[producer](https://github.com/cloudnative-pg/cloudnative-pg/blob/2a35abb4628f209d149825ef3c38011e0701ff2f/internal/cmd/manager/instance/run/lease/runnable.go),
[controller](https://github.com/cloudnative-pg/cloudnative-pg/blob/2a35abb4628f209d149825ef3c38011e0701ff2f/internal/controller/primary_lease.go).
The holder identity is a **pod name**, reused after process/pod replacement.
In-place manager upgrades retain/re-adopt the same holder. The controller may
adopt a Lease left from an old Cluster incarnation. Consequently holder name or
leaseTransitions alone cannot identify every database connection incarnation or
prove acknowledged portal DELETE survival. A polling watcher cannot close the
interval before it observes a promotion. Merely observing the existing Lease is
not a sufficient adapter for `RecoveryFence.Check(ctx, generation)`.

Concrete candidate for existing Astra to ratify:

1. Use the existing CNPG instance-manager/primary-election path as the
   authoritative **primary admission producer**, rather than a delayed BFF
   watch. Before a primary admits portal authorization, it must publish a
   compare-and-swap independent tuple containing cluster incarnation, Lease
   incarnation/transition, admitted primary/pod incarnation, database system ID,
   WAL timeline and postmaster/connection incarnation, plus external generation.
   A restore or uncertain commit lineage requires a new generation before
   admission. Fail closed if the tuple cannot be published or verified. This
   describes a required producer contract; no verified portal pre-admission hook
   exists in the reviewed source today, so it cannot be assumed deployable.
2. Widen the source fence contract to take an incarnation read **inside the same
   SQL transaction/connection** as the session read/consume/revoke. Compare it to
   that authoritative tuple before commit and before response release. A fixed
   `-rw` hostname or pg_is_in_recovery=false cannot bind the actual server.
   New connections must remain quarantined until their tuple is admitted; no
   old-primary cached allow or healthy second connection can cover a mismatch.
3. Verify actual production function defaults rather than proposing an unnecessary
   grant. Hosted run40022033678 at `1fb8c489aab06e8c71d16d129a397ebc1cfa96bb`
   proves the constrained runtime role can read `pg_control_system()` system ID,
   current WAL filename's timeline, postmaster start time, server address and its
   own backend PID in the **same transaction/connection**, with no additional
   function grant, pg_monitor membership or wrapper. This is default PostgreSQL16
   fixture evidence, not a read using a live provisioned portal role. Verify live
   defaults during reviewed provisioning; deny on permission/missing values and
   stop to review any genuine authority delta. PostgreSQL16
   [control data](https://www.postgresql.org/docs/16/functions-info.html#FUNCTIONS-CONTROLDATA)
   and [WAL functions](https://www.postgresql.org/docs/16/functions-admin.html#FUNCTIONS-ADMIN-BACKUP)
   document these fields. No SECURITY DEFINER function or additional
   Kubernetes/service permission is introduced here.
4. Prove fail-closed behavior for new timeline before watcher observation,
   same-name pod/process reuse, orphan Lease adoption, restore with old DB rows,
   unavailable/stale witness, lost commit ACK and isolated old-primary reads.
   Current async CNPG policy stays unchanged. An admission observation followed
   by a pool reconnect must never carry the old connection's allow forward.

This proposal identifies a real authoritative election producer and the missing
connection/admission binding. It does **not** prove the producer can deliver the
portal contract without source work or authority beyond this task. Existing
Astra/root must choose a complete realizable producer/interface (or keep runtime
503). Static generation, process flags and post-event manual rotation remain
insufficient. Source and approved logical-DB setup can progress independently;
no internal route or login acceptance follows from them.
