# Known noise & non-remediation

**Audience: autonomous agents** — the `rem-*` remediation lane, the alert-responder,
and any dispatched session that might "fix" something. Humans welcome too.

This file exists because the agents that act on alerts run in a **different pod**
from the sessions that learned these lessons, and they have no shared memory. Every
entry below is a case where the *obvious* corrective action is wrong — several make
things actively worse.

It lives in `.agents/runbooks/` deliberately: `respond.sh` instructs the responder to
check `.agents/runbooks/` and `docs/`, and it re-clones this repo on every run. A file
placed in `.agents/rules/` would never be read.

---

## How to use this file (read before acting on a match)

1. **A match means "do not remediate". It does NOT mean "close silently."**
   Marking an order done without a note is indistinguishable from a real outage that
   nobody handled. Always record *which* entry matched and why, so a wrong match is
   auditable after the fact.
2. **Match on the described signature, not on the alert name alone.** Most entries
   below share an alert name with genuine faults. The signature is what distinguishes
   them.
3. **If the observation contradicts the entry, believe the cluster.** These notes go
   stale. Every one carries the check that re-verifies it. An entry that fails its own
   check is a bug in this file — fix it, don't work around it.
4. **When in doubt, escalate.** A needless escalation costs a human two minutes. A
   wrongly-suppressed incident costs far more.

---

## Do not act — benign signatures

### slskd returns 503 for up to ~3h after any reschedule
**Restarting it makes this worse, and restarting is the instinctive response.**

slskd rescans its share database before binding its API, so it is `0/1` and serving
503 the entire time. Its startup probe allows **180 minutes** (1080 × 10s) — note that
is 180, not the "30 min" some older comments claim. A restart discards the partial
scan and begins the whole wait again.

- **Signature:** slskd `Running` but `0/1`, HTTP 503, recently rescheduled, and the
  logs show a share scan in progress.
- **Do:** wait. Optionally confirm scan progress in the logs.
- **Do NOT:** restart the pod, delete it, or "unstick" it.
- **Check:** `kubectl -n downloads logs deploy/slskd | grep -i scan`

### `ytdl-sub-*` KubeJobFailed
Expected; these jobs fail routinely by design and are explicitly null-routed in
Alertmanager.

**Important correction, because the config invites the wrong inference:** the
Alertmanager route for `ytdl-sub-*` is effectively *decorative*. The root receiver is
`"null"` and the only path to a page is `severity="critical"`; `KubeJobFailed` ships
at `severity: warning`. So **every** namespace's job failures are silent, not just
these. Do not read that route as evidence that other jobs page — they do not.

### `cigar-journal-crawl-*-fleet` Job failed with ONE vendor red
The fleet CLI exits 1 when any vendor fails, by design (the `FAILURE VISIBILITY`
note in the cigar-journal HelmRelease). A single vendor at `status=failed pages=0
error: fetch failed` is that vendor's transport — DNS, TLS, connection reset —
refusing the very first request (robots.txt), not the image. Since
cigar-journal #313 (merged 2026-09-07, first tag after v0.44.1) the line carries
the cause chain — `fetch failed (CERT_HAS_EXPIRED: certificate has expired)` —
so the log names the transport fault itself; on an older tag it says only
`fetch failed` and the probe below is how you learn the cause.

- **Signature:** the fleet block at the end of the Job log (`fleet enrich
  vendors=9 succeeded=8 failed=1`) names one vendor with `fetch failed` and every
  other vendor succeeded in the same pod, so the cluster's egress is fine. Both
  `backoffLimit` attempts read identically. The triage lane calls it
  upgrade-attributable whenever a cigar-journal tag bump merged in its 3h
  lookback — path coincidence, not mechanism. 2026-09-07 (`esc-shepherd-637a8804`):
  2 Guys Cigars' certificate expired at 23:59:59 UTC, two hours before the 02:00
  enrich run, on an evening with four tag bumps.
- **Check:** read the pod log (`kubectl -n frontend logs <pod>` — the remediate
  lane cannot; an `esc-*` session can). If the line already names the cause, stop
  there. Then probe from inside `frontend` with the vendor's own image: a Job
  cloned from the CronJob's `jobTemplate` with initContainers/volumes/envFrom
  dropped, pod label `app.kubernetes.io/controller` changed so the gate cannot
  attribute the probe to the workload, and `node -e` doing `tls.connect` +
  `fetch` against the adapter's `url` plus a healthy vendor as control, printing
  `error.cause.code`. Delete the probe Job afterwards.
- **Do not** re-pin the tag on temporal correlation. A vendor outage keeps the
  nightly Job red until the vendor recovers; the owner can set that vendor row's
  `crawl_enabled=false` (the row wins over the adapter). Once diagnosed, delete
  the Failed Job's `Error` corpse pods so the gate stops re-paging a verified
  external outage — the Job object still records the failure and the log is in
  the escalation report.

### `CephNodeDiskspaceWarning` immediately after a node reboot
`predict_linear` extrapolates from image re-pull churn and forecasts a full disk that
never arrives. Self-clears once the pull settles.

- **Signature:** fires within roughly an hour of a node boot, with plenty of real
  headroom.
- **Check the actual free space before dismissing** — this one shares an alert name
  with a genuine disk-fill, which has bitten this cluster for real.

### `etcdDatabaseHighFragmentationRatio`
Long-standing, accepted warning. Not actionable.

### AppDaemon checkers reporting `unknown` right after an AppDaemon reload
Cold-start artifact of the AppDaemon↔Home-Assistant websocket reconnecting; self-heals
within ~30 minutes. **The owner considered this and declined a fix — do not propose
one again.**

### Gatus metric gaps whenever Gatus itself reschedules
Gatus is a single-replica StatefulSet that moves on every sidecar bump, and its metric
series has multi-minute holes across each move — long enough to reset a `for:` timer
and to make many endpoints read 0 simultaneously.

- **Signature:** a *synchronised* dip across many unrelated endpoints, coincident with
  a Gatus restart. Genuine outages do not politely align.
- Any alert rule written over Gatus metrics needs `max_over_time` smoothing or
  `keep_firing_for`; without it the rule is measuring Gatus, not the services.

---

## Do not "fix" — deliberate configuration that looks broken

### `postgres16-primary` PodDisruptionBudget at `ALLOWED DISRUPTIONS: 0`
This is CloudNativePG working correctly, not a drain blocker to clear. It forces a
graceful switchover ahead of a drain instead of a hard primary eviction. Removing it
re-opens a diverged-standby failure mode that has already cost this cluster an
incident.

**Do not** set `enablePDB: false` on that cluster or delete the PDB. For a drain,
switch over first (`kubectl cnpg promote`), then drain.

Sibling PDBs are a different story and were fixed properly: the CNPG singletons and
the vexa components gate their PDBs on replica count, and `emqx-core` now sets
`maxUnavailable` explicitly. If a *new* singleton shows `allowed=0`, that one is worth
investigating.

### The muted Ceph `AUTH_INSECURE_*` / `AUTH_EMERGENCY` health checks
Muted **on purpose**, in git, with a written rationale. The daemon keys are already
rotated; the CSI keys cannot rotate until Talos ships a new enough kernel, which is
realistically years out. Tracked in the repo's open issue for cephx rotation.

**Do not un-mute them, and do not file the mute as a finding.** It has been reviewed.

### qbittorrent's VPN — there is no node-specific problem
An earlier theory that the VPN "only routes on one worker" was **wrong** and has been
disproven twice, most recently by reproducing the correct Mullvad exit from a second
worker. Do **not** add a `nodeSelector` to pin it; that reduces scheduling freedom and
made a previous node roll worse.

- Transient failures are fixed by deleting the pod — **but only pod-side ones.** See
  the discriminator below before you delete anything.
- **The `QbittorrentVpnDown` alert also fires when the pod is merely unhealthy.** An
  alert firing is not evidence of a routing fault — confirm the actual exit IP before
  concluding anything about the VPN.

**Discriminator: pod-side vs. gateway-side (do this first — it is two commands).**
The readiness probe is a Mullvad-egress check through the VLAN-30 gateway
`192.168.30.1` (the UDM, which policy-routes VPNLan out Mullvad). That gateway is
shared, so a tunnel drop there takes out every VPNLan pod at once while the pods
themselves are perfectly healthy. Deleting a pod cannot fix that, and the replacement
fails the very same probe.

- **The control group is the point — but read the node column, do not assume it.**
  VLAN 30 carries exactly two workloads, `downloads/qbittorrent` and `downloads/slskd`.
  They are **not** pinned apart: both carry only `nodeSelector:
  network.haynesops.com/vpn=true` and neither has anti-affinity or a topology spread,
  so the scheduler may co-locate them and often does (both were on `talosw01` on
  2026-09-13). The other macvlan pods (`home-automation`: esphome, home-assistant,
  zigbee2mqtt, zwave) sit on the IoT/Sonos VLANs behind the *same* UDM, on the
  control-plane nodes. List them all together, **with their nodes**:
  `kubectl get pods -A -o json | jq -r '.items[] | select(.metadata.annotations["k8s.v1.cni.cncf.io/networks"]) | "\(.metadata.namespace)/\(.metadata.name) \(.spec.nodeName)"'`
  - **One** pod down, its VLAN-30 peer fine ⇒ genuinely pod-side; the delete applies.
  - **Both** down on **different nodes**, other VLANs Ready ⇒ gateway/tunnel. Not the
    pod, not the node, not multus. Do **not** delete pods.
  - **Both** down on the **same node** ⇒ **undetermined — the node is not yet ruled
    out**, because the pair shares a failure domain here. Do not stop at "both are
    down". Check the node (`kubectl get node <n>`, and whether its non-macvlan pods
    are healthy), then settle it with the in-pod gateway probe below, which
    distinguishes the two directly and does not depend on placement at all.
- **Confirm at the gateway**, from inside the pod: `nc -z -w4 192.168.30.1 443` is
  open (the UDM is alive), yet `nc -z -w3 1.1.1.1 443` and every other public IP is
  dead, and DNS via `192.168.30.1` times out. That split — gateway reachable, forwards
  nothing — *is* the fail-closed kill-switch doing its job. **No leak is occurring**,
  which is why this is not an emergency.
- **Do not use ICMP to judge the UDM.** `ping 192.168.30.1` is rate-limited and drops
  whole 2-packet probes while egress is demonstrably fine (observed 02:05:01Z: 100%
  loss to the gateway, `nc` to 1.1.1.1:443 open in the same second). A run of lost
  pings is not a gateway reboot and not a tunnel drop. Judge the gateway by TCP 443
  and the fault by whether public egress works.
- **Gateway-side outages can self-heal** and the lane's job is then to confirm, not to
  act. 2026-09-13 (`rem-responder-7f98616b`): VPNLan egress dropped ~01:35Z and was
  back by 02:02:16Z, returning on the *same* exit IP `87.249.134.5` and the same server
  `us-chi-wg-201` — the tunnel re-established rather than reconnecting elsewhere.
  qBittorrent was `3/3` and the alert clear by 02:04:48Z with no action taken. Total
  outage ~27 min against a 10-minute `for:`, so this alert will fire on a tunnel flap
  that needs nobody. What happened *on* the UDM is not observable from inside the
  cluster — the recovery was not attributed to any action, and none was taken.
- **Re-query before escalating.** The window between the page and a triage that is
  actually finished is comparable to the outage itself. Verify the exit IP
  (`wget -qO- https://am.i.mullvad.net/json` → `mullvad_exit_ip: true`) at the end,
  not just at the start.
- **slskd restarting itself during a VPN outage is by design, not a symptom.** Its
  liveness probe asserts the Soulseek session is logged-in/connecting precisely because
  `/health` stays green while slskd sits in "Disconnecting" forever after a VPN flap.
  Let it restart; later boots reuse the cached share db. This does **not** contradict
  the "never restart slskd" entry above, which is about *you* restarting it.
- **Escalate only if the gateway does not come back** (say, 30+ min of no forwarding
  with the UDM still answering on 443). The UDM is not a Kubernetes object and is
  outside the lane's egress — the fix is a human on the UniFi console re-establishing
  the Mullvad WireGuard tunnel for VPNLan.

---

## Escalate — do not attempt these autonomously

### A wedged node (kubelet unreachable while its containers are still alive)
Signature: the kubelet stops reporting and the node goes `NotReady`, but workloads on
it are demonstrably still running — Ceph OSDs still showing `up` is the giveaway. This
has happened several times across different nodes.

**Do NOT force-delete pods stuck in `Terminating` on that node.** The pods are still
running. Force-deleting removes the API object while the process keeps its volume, so
the replacement pod mounts the same RWO volume from a second node — **two writers on
one volume corrupts data.** The mass `Multi-Attach` errors are a *symptom* of the
wedge, not the problem to solve.

The actual fix is a power-cycle, which needs hypervisor or physical access that
autonomous agents do not have. **Escalate to a human.** This is the single most
damaging wrong action available in this cluster.

### Anything requiring a decision that was deferred to the owner
Several open items are explicitly waiting on a human decision rather than blocked on
work. If a fix looks obvious but the file or PR says it is awaiting a call, it is
awaiting a call.

---

## Not noise — remediated autonomously instead

### `CephDaemonCrash`
**Not noise, and not to be suppressed.** The daemon has usually recovered on its own,
but the alert *cannot* self-clear: Ceph keeps `RECENT_CRASH` raised until someone runs
`ceph crash archive <id>`, so a 21-second OSD abort re-pages every 12h for two weeks.
Silencing it, null-routing it, or answering `ACTION: none` because "the pod is Running"
all hide the next real crash.

- **Signature:** `ceph_health_detail{name="RECENT_CRASH"} == 1` with the crashed pod
  back `Running`/Ready and PGs active+clean.
- **Do:** follow [`ceph-daemon-crash.md`](ceph-daemon-crash.md) — the responder answers
  `ACTION: urgent` (the handoff verb), and the `rem-*` lane inspects each `ceph crash
  ls-new` id, verifies recovery, and archives **per id**.
- **Do NOT:** silence it, route it away, mark the order `done` without archiving, or
  `ceph crash archive-all` (that swallows records nobody looked at — it is the human's
  break-glass, not the lane's tool). A repeat signature, a daemon that stays down, or
  more than three new records at once **escalates** instead of being archived.
- **Check:** `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph crash ls-new`

---

## Deliberately NOT in this file

**Ceph mgr memory.** An earlier note claimed the active mgr leaked ~3.3 GiB/day and
OOMKilled daily, and that a mgr OOM could therefore be waved off. **That is no longer
true** — at the current Ceph version both mgr pods show zero restarts over a day and a
half, and the active one peaks well under half its limit. Growth is roughly an order
of magnitude slower than the old figure.

It is called out here precisely so nobody re-adds it: **a Ceph mgr OOM is now a novel
event that deserves investigation.** Inlining the old claim would pre-authorise
dismissing a real fault. Verify with the working-set metric before asserting either
way.

That is the general test for anything added below — *if this entry were wrong, what
would it cause an agent to ignore?* If the answer is "a real outage", it does not
belong here.
