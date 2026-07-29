# HANDOFF: fix wedged postgres16-10 after the dev-env pod roll

Written 2026-07-29 by the dev-env session that prepared PR #2276's CNPG kit.
That PR's merge bounces the dev-env pod and kills the authoring session — you
are the successor. **Tom has authorized this end-to-end: execute without
re-asking.** Stop only for a genuinely new decision. A copy of this mission
also sits in agent memory (`handoff-cnpg-fix-after-devenv-roll`); this repo
file is authoritative — if they disagree, trust this one.

## Context in 60 seconds

- 2026-07-29 03:20 ET: the UDM Pro SE rebooted (auto-update window) → ~90s
  partition of the control-plane nodes → the CNPG operator, with the default
  `failoverDelay: 0`, failed `database/postgres16` over (timeline 84→85,
  `postgres16-7` promoted; old primary `-11` rewound and rejoined cleanly).
- Standby `postgres16-10` had replayed timeline-84 WAL **past the fork point**
  (`2C4/73000000`). CNPG only rewinds demoted primaries, never standbys, so it
  wedged permanently: loops asking S3 for a tl-84 segment that will never
  exist, replication lag growing unbounded (13h+ at handoff time), yet the pod
  stays 1/1 Ready — **serving stale reads via `postgres16-ro`** — and the
  Cluster CR reports "healthy". Only `CNPGReplicaNotStreaming` (critical) told
  the truth. The alert-responder's 03:42 diagnosis was correct.
- The recurring fix is destroy + re-clone. PR #2276 (the PR this file rode in
  on) added what that needs: **PVC delete scoped to `database`** (Role
  `dev-env-cnpg-remediation`, in the cloudnative-pg app, applies on merge — no
  pod bounce required for it) and **kubectl-cnpg 1.30.0** (dev-init.sh block;
  also pre-installed on the PVC at `~/.local/bin/kubectl-cnpg`).
- PR #2300 (draft) is the durable prevention: `failoverDelay: 120` +
  `probes.readiness {type: streaming, maximumLag: 64Mi}` on `cluster16.yaml`.
  Land it **last** (its probe change rolls the instances).

## Steps

**0. Still broken?** Tom may have fixed it by hand before the roll.
`kubectl get pods -n database -l cnpg.io/cluster=postgres16` — if
`postgres16-10` is gone / a replacement instance is streaming, skip to step 3.

**1. Verify the new powers landed:**
- `kubectl auth can-i delete pvc -n database` → `yes`. If `no`:
  `flux reconcile kustomization cloudnative-pg -n database --with-source`,
  re-check.
- `kubectl-cnpg version` → 1.30.0 (on the PVC; dev-init reinstalls if absent).

**2. The fix** (deletes the instance's PVC + pod; operator re-clones fresh):

```
kubectl cnpg destroy postgres16 10 -n database
```

Fallback without the plugin: `kubectl delete pvc postgres16-10 -n database`
then `kubectl delete pod postgres16-10 -n database`.

**3. Verify recovery** (Grafana MCP `query_prometheus`, datasource `prometheus`):
- New instance appears, clones from the primary, reaches Running.
- `cnpg_pg_replication_streaming_replicas` on the primary pod → **2**.
- `cnpg_pg_replication_lag` ≈ 0 for both standbys (the wedged one read 48000+).
- `CNPGReplicaNotStreaming` resolves (~15m after streaming=2; check `ALERTS`).
- The every-30s "cannot advance replication slot" log loop on `postgres16-11`
  stops once the stale `-10` slot is gone from the primary.

**4. Land the durable fix — PR #2300** (branch `agent/cnpg-failover-hardening`),
only once the cluster is 3/3 streaming. Confirm Tom approves (draft → ready →
merge), reconcile, and watch the rolling probe update complete
(`primaryUpdateStrategy: unsupervised` — the primary will switchover once).

## Parked follow-ups (propose when relevant; not authorized to self-start)

- Alert-responder escalation: re-page at priority 1 if an `ACTION: urgent`
  incident is still firing hours later (today's 03:42 priority-0 page was
  missed until the afternoon).
- Auto-unwedger: the wedge signature is crisp (replica not streaming + lag
  growing + primary healthy + sibling streaming) and RBAC now permits the fix —
  a guarded automated destroy is feasible; needs Tom's sign-off on guardrails.
- Stray future-timeline history files in `s3://cnpg-haynesops/`
  (`00000056.history`, timeline 86 > current 85, likely from a prior cluster
  incarnation): will collide with archiving when the cluster reaches tl 86.
  Tom holds the S3 creds.

## Cleanup

When steps 0–4 are done: delete this file (branch + PR is fine), remove the
`handoff-cnpg-fix-after-devenv-roll` memory + its MEMORY.md line, and fold
anything newly learned into the `postgres16-diverged-standby-wedge` memory.
