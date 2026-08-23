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

- Transient failures are fixed by deleting the pod.
- **The `QbittorrentVpnDown` alert also fires when the pod is merely unhealthy.** An
  alert firing is not evidence of a routing fault — confirm the actual exit IP before
  concluding anything about the VPN.

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
