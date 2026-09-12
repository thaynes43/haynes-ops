# CephDaemonCrash — autonomous acknowledgement of self-recovered Ceph crashes

**Audience:** the alert-responder (read-only diagnosis), the `rem-*` remediation
lane (exec-capable, silent), and any `esc-*` or human session that inherits one.
Lane mechanics: [`agentic-remediation.md`](agentic-remediation.md).

## What the alert is, and why it never clears on its own

`CephDaemonCrash` (shipped by the rook-ceph chart's PrometheusRule,
`severity: critical`, `for: 1m`) is `ceph_health_detail{name="RECENT_CRASH"} == 1`:
the mgr `crash` module holds at least one crash record from the last 14 days
(`mgr/crash/warn_recent_interval`) that nobody has acknowledged. Ceph keeps it
raised on purpose until someone runs `ceph crash archive <id>` — the daemon
restarting and rejoining does **not** clear it.

Left alone it costs Tom a page every 12h for two weeks: the Pushover route
inherits Alertmanager's root `repeat_interval: 12h`, and the responder claims the
incident once, then skips it as "already handled". 2026-09-11/12: one osd.3
abort that self-recovered in 21 seconds paged three times before a session
archived it (`ceph crash info 2026-09-11T16:01:31.739897Z_dd7e79a1-…`).

Acknowledging a crash whose daemon is verifiably healthy is bounded, reversible,
and needs no human judgment — so it belongs to the rem lane. What **does** need a
human is a daemon that is not back, or a signature that repeats: those escalate.

## Responder: what to output (read-only)

You cannot `exec`, so you can neither list nor archive crash records. Establish
two things and hand off.

1. **Which daemon crashed.** `kubectl -n rook-ceph get pods -o wide`, then for
   any pod with restarts in the alert window:
   `kubectl -n rook-ceph get pod <pod> -o jsonpath='{.status.containerStatuses[*].lastState}'`
   — `exitCode 134` + `reason Error` is an abort (a crash record); `OOMKilled`
   is not (no record, different alert). Loki confirms the signature:
   `/opt/responder/loki-query.sh '{namespace="rook-ceph"} |~ "FAILED ceph_assert|Caught signal"' 1440`.
2. **Is it back.** PromQL via `/opt/responder/prom-query.sh '<expr>'` (the bare
   script name is not on your tool allowlist; the full path is):
   `ceph_osd_up` / `ceph_osd_in` (OSDs), `ceph_mon_quorum_status` (mons),
   `ceph_mgr_status` (mgr), `ceph_pg_degraded`, `ceph_pg_undersized`,
   `ceph_pg_active` vs `ceph_pg_total`, `ceph_health_status`, and
   `ceph_health_detail{name!="RECENT_CRASH"} == 1` for any other raised check.
3. **Output `ACTION: urgent`** with `RUNBOOK: .agents/runbooks/ceph-daemon-crash.md`.
   `urgent` is the *handoff* verb: a still-firing urgent is filed to the silent
   rem lane, which archives after its own verification and pages only if it
   cannot. Do **not** answer `investigate` (that pages Tom to run one command the
   lane can run) and do not answer `none` (this alert cannot self-heal; it will
   re-page every 12h). The only `none` is when `ALERTS{alertname="CephDaemonCrash",alertstate="firing"}`
   is already empty. If the daemon is **not** back (CrashLoopBackOff, OSD down,
   mon out of quorum, PGs inactive) still answer `urgent` and say so in `FIX`:
   the lane verifies and escalates with a joinable session, which is a better
   page than a read-only summary.

## Rem lane: the procedure

Everything below runs through the toolbox:
`kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph <cmd>`. Your ClusterRole
grants `pods/exec` create cluster-wide. If you want to prove it, the probe is
`kubectl auth can-i create pods --subresource=exec -n rook-ceph` — the intuitive
`can-i create pods/exec` answers **no** because it parses `pods/exec` as a pod name.

1. **Confirm the condition live.** `ceph health detail` names `RECENT_CRASH` and
   `ceph crash ls-new` lists the unacknowledged ids. Empty list → someone got
   there first: `done` with that note.
2. **Inspect every new id.** `ceph crash info <id>` gives `entity_name`,
   `utsname_hostname`, `timestamp`, `assert_condition` / `assert_file` /
   `assert_line` (or the signal for a segfault), `stack_sig`, `ceph_version`.
   Then read the daemon's last minutes before the abort — this is the context
   your digest note must carry:
   `kubectl -n rook-ceph logs <pod> -c <osd|mon|mgr|mds|rgw> --previous --tail=300`
   (look for slow ops, heartbeat failures, `I/O error`, OOM, a compaction in
   flight). No `--previous` container means the pod was rescheduled; use Loki.
3. **Verify recovery — all of these must hold before you archive anything:**
   - the pod is `Running`, Ready, and its restart count has been stable for
     ≥10 min; the crash `timestamp` is ≥15 min old;
   - the entity is serving: OSD → `ceph osd tree` shows it `up` and `in`;
     mon → `ceph quorum_status` lists it in `quorum`; mgr → `ceph mgr stat`
     shows an active mgr; MDS → `ceph fs status` shows active + hot standby;
     RGW → its pods are Ready;
   - `ceph -s`: no PG `inactive` / `incomplete` / `down` / `stale`;
     `degraded` / `recovering` / `backfilling` only if the count is shrinking on
     a second read; no `OSD_DOWN` / `MON_DOWN` / `MDS_*`; nothing raised besides
     `RECENT_CRASH` and the muted `AUTH_*` checks (issue #2538);
   - `bash /opt/dev-env-ops/dev-activity-check.sh rook-ceph <hostname>` — a
     declared node roll, key rotation, or Ceph maintenance explains a crash
     record (OSDs restart in host pairs on a cephx rotation). Still archive;
     name the declaration in the note.
4. **Pattern gate — escalate instead of archiving when any of these is true:**
   - the same entity has ≥2 crash records in the last 7 days (`ceph crash ls`
     — archived records count, they are still listed);
   - the same `stack_sig`, or the same `assert_file:assert_line`, appears in any
     other record from the last 30 days (`ceph crash ls`, then `ceph crash info`
     on the ids in that window — it is cheap);
   - a mon or mgr crashed **and** quorum or the active mgr was lost at any
     point (`ceph log last 2000 info cluster` around the timestamp);
   - the daemon fails step 3;
   - more than three new records at once — that is a loop (the 2026-08-20
     mgr `node_proxy_fullreport` storm minted 786 records in 3.5h; that one
     raises `RECENT_MGR_MODULE_CRASH`, a different code, but the reflex is the
     same: find the loop, do not archive it into the void).

   `order-status.sh <key> escalate "CephDaemonCrash: <entity>@<host> <N> crashes in <window> / sig <sig8> repeats <ids>; daemon <up|down>; <assert one-liner>; upstream <ref|unknown>. Human: decide bump/hold per tier4-component-playbooks.md (rook-ceph) — do not archive until decided."`
5. **Known signatures** (archive; name the match in the note):
   - mon.i tcmalloc abort around 02:30 ET, and the osd.2 perf-counter abort —
     the recurring night crashes on the health gate's benign-warn list
     ([`upgrade-health-gate.md`](upgrade-health-gate.md) §5);
   - 2026-09-11 osd.3 (talosm03, ceph 20.2.4):
     `BlueFS::_flush_and_sync_log_LD` `ceph_assert(want_seq == 0 || want_seq <= dirty.seq_live)`
     (`BlueFS.cc:3715`, thread `bstore_kv_sync`, stack_sig `8f081d83…`). No
     upstream report of this exact assert as of 2026-09-12; the nearest is
     ceph/ceph#70892 (tracker #79068, a BlueFS log-sequence race in this same
     function, merged to main 2026-08-25, tentacle backport #71593 still open).
     One hit is a one-off — archive. A second hit of this sig trips the pattern
     gate: escalate with both ids and that PR link.
6. **Archive per id:** `ceph crash archive <id>` for each id you inspected.
   Not `archive-all` — you looked at specific records, and `archive-all` would
   also swallow one that arrived while you worked. (`archive-all` stays the
   health gate's break-glass shortcut for a human.)
7. **Verify.** `ceph crash ls-new` is empty. Then poll `ceph health detail`
   every 30s for up to 12 min until `RECENT_CRASH` disappears — the mgr crash
   module re-evaluates on its own timer (`warn_recent_interval/100`, clamped to
   60s–600s, so 10 min at the default 14 days),
   **not** when you archive. Do not restart the mgr and do not re-archive.
   Finally confirm the alert dropped:
   `curl -sG http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090/api/v1/query --data-urlencode 'query=ALERTS{alertname="CephDaemonCrash",alertstate="firing"}'`
   returns an empty result (one scrape after the health flip).
8. **Close out:**
   `order-status.sh <key> done "CephDaemonCrash: <entity>@<host> <YYYY-MM-DDTHH:MMZ> <assert or signal one-liner> sig=<sig8> — back <N>s after abort, PGs clean, archived <id>; upstream <ref|none>"`.
   That line is the digest entry — the **only** record a human sees — so keep
   the signature greppable.

## Gotchas

- **Toolbox lockout** after a cephx key rotation (`handle_auth_bad_method` on
  every `ceph` command): `kubectl -n rook-ceph rollout restart deploy/rook-ceph-tools`
  (in your RBAC) remounts the rotated admin keyring; retry after it is Ready.
- **Talos `dmesg` clock skew.** On 2026-09-11 talosm03's kernel timestamps ran
  2 min behind Ceph/k8s time, so `libceph: osd3 down/up` appeared to *precede*
  the abort. Cross-check `kubectl logs --previous` or `ceph log last … cluster`
  before believing a dmesg lead about an earlier event.
- **Archiving hides nothing.** `ceph crash ls` and `ceph crash info` still return
  archived records; only `ls-new` and the health check change. Records persist
  for `mgr/crash/retain_interval` (1 year); `ceph crash prune <days>` is
  housekeeping, not part of resolving an alert.
- **OOMKilled is not a crash record.** A mgr OOM cycle raises no `RECENT_CRASH`
  (see `RESPONDER_IGNORE_PAIRS` / `OOMKilled@rook-ceph`); a segfault does.
- The `for: 1m` and severity come from the rook-ceph chart's rules, not this
  repo — they are not the lever.

## Related

- [`agentic-remediation.md`](agentic-remediation.md) — the lane and its decision table.
- [`known-noise-and-non-remediation.md`](known-noise-and-non-remediation.md) — what not to "fix".
- [`upgrade-health-gate.md`](upgrade-health-gate.md) §5 and
  [`tier4-component-playbooks.md`](tier4-component-playbooks.md) (rook-ceph) —
  the gate's read-only view of the same health check and the break-glass archive.
