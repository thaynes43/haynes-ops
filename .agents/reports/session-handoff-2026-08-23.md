# Session handoff — dev-env cold start after the 2026-08-23 pod bounce

**Durable copy.** A PVC copy lives at `/home/dev/work/HANDOFF-post-bounce.md`, but
that is readable only from inside the dev-env pod and dies with the volume — this
is the canonical one. Point a fresh session at this file.

You are a fresh session in the dev-env pod. The previous session
(`session_011S5HnA71jHSsCoWWmXE4F3`, 2026-08-20 → 08-23) was ended by the pod
roll that merging **PR #2568** caused. Read this, run the verification block,
then pick up the queue. Everything below is verified state, not plans.

---

> ## ⚠️ CORRECTIONS — applied 2026-08-23 by the successor session
>
> The queue below was picked up and largely cleared. Several statements in this
> file were **wrong against live state**; they are corrected here rather than
> deleted, so the reasoning stays auditable.
>
> - **§1 "the automation is a SEPARATE pod, untouched by the bounce" — WRONG.**
>   `dev-env-ops` carries `reloader.stakater.com/auto`, and #2568's fix touched
>   `dev-activity-check.sh` in its resources dir, so the ops pod rolled too.
>   Expect it to roll on any `dev-env-ops/app/resources/**` change.
> - **§3 item 1 (PDBs) — WRONG on every specific.** Not five blockers, **two**.
>   #2553 had *already* fixed the CNPG singletons and vexa (`enablePDB: false`);
>   nothing was "cleared by hand" (both survivors were 348 days old, untouched);
>   and the names given were pod names, not PDB names. Of the two real ones,
>   `postgres16-primary` is **correct by design** (CNPG forces a switchover —
>   do NOT clear it), and `emqx-core` was the only genuine bug. **Both resolved:
>   #2583** set `coreTemplate.spec.maxUnavailable: 1` (ALLOWED 0 → 1) and shipped
>   a descheduler pressure-taint guard in the same PR, because that PDB had been
>   the only thing making the sole MQTT broker eviction-proof.
> - **§3 item 4 (slskd grace) — the PRESCRIPTION IS UNSAFE.** A raised Gatus
>   failure-threshold does not work: Gatus's failure counter is in-memory and it
>   restarts on every sidecar bump, and slskd's probe ANDs `/health` with the VPN
>   check — so a long grace silences **VPN-leak detection on a P2P client**. The
>   startup probe is 180 min, not the documented 30. Left open deliberately.
> - **§3 item 2 (qb VPN) — RESOLVED, no action.** Reproduced the correct Mullvad
>   exit from w02. There is no node-specific fault; do **not** add a nodeSelector.
> - **§6 PR table — was already stale when written** and is more so now. Re-derive
>   with `gh pr list`. #2506 was merged 08-23 on the owner's explicit
>   authorization (this file's "do NOT merge for him" was overridden for that PR
>   only — do not generalize it); it deployed clean but introduced an ongoing
>   `property_mapping_exception` whose effect on real logins is **still
>   unverified**. #2504 auto-closed as its duplicate.
> - **§7 storage migration — audited.** The Kyverno exemption is the *safest*
>   part (tightly name-scoped), not a headline risk. The real find was elsewhere:
>   **Loki's StatefulSet was cascade-deleting its own 128Gi PVC** (chart default,
>   invisible in the repo, PV on `reclaimPolicy: Delete`, zero backups) — fixed
>   in **#2579**. Backups were then explicitly **declined** by the owner; do not
>   re-propose them.
> - **§9 known-noise — INCOMPLETE and partly misleading.** "ytdl-sub is routed to
>   null" implies other jobs page. They do not: the root receiver is `"null"` and
>   only `severity="critical"` reaches Pushover, while `KubeJobFailed` ships at
>   `warning`. **Every** namespace's job failures are silent — `sync-ai-usage`
>   failed hourly for 6+ days unnoticed. That gap is still open.
> - **§10 memory durability — the premise was wrong.** "The ops pod has the repo
>   cloned so it gets it for free" was **false**: `ops-init.sh` guarded the clone
>   on `[ ! -d .git ]` and the PVC survives restarts, so it was frozen at Aug 20 —
>   meaning `.renovate/holds.json5` *resolved*, to stale contents, and a work-order
>   session could merge an explicitly-held upgrade. Fixed in **#2582**. Curated
>   knowledge now lives in `.agents/runbooks/known-noise-and-non-remediation.md`
>   (**`runbooks/`, not `rules/`** — `respond.sh` only searches the former).

**First:** `~/.claude/.../memory/MEMORY.md` is loaded automatically — the
memories referenced here have the detail. This file is the map, not the terrain.

---

## 1. Verify the bounce landed (30 seconds)

```bash
# #2568's two payloads should now be live in this pod:
declare-activity 2>&1 | head -3                      # should print usage, not "not found"
grep -c "Declare disruptive work" ~/.claude/CLAUDE.md # expect 1
grep -c "Model policy" ~/.claude/CLAUDE.md            # expect 1

# The automation is a SEPARATE pod and should have been untouched by the bounce:
kubectl -n upgrade-agent get pods -l app.kubernetes.io/name=dev-env-ops
kubectl -n upgrade-agent logs deploy/dev-env-ops -c app --tail=3   # want "watcher up ... rem_model=claude-opus-5"
```
If `declare-activity` is missing, #2568 did not actually merge — check
`gh pr view 2568 --repo thaynes43/haynes-ops --json state`.

---

## 2. What was built (all merged, deployed, tested unless noted)

**Talos v1.13.3 → v1.13.9** — all 6 nodes, kernel 6.18.44, first dev-env-driven
roll. Clean. See memory `talos-roll-2026-08-21-learnings` (operator-key Job
pattern, drain-blocker signature).

**Agentic remediation** (#2569 + #2572) — the responder now *fixes* instead of
paging. `rem-*` lane = headless (no Remote Control registration, never in Tom's
session list), triages → checks dev-env activity → fixes in ops containment →
verifies → closes silently. Cannot-fix or tried-and-failed → promotes to `esc-*`
(page + joinable session). Fails open: a failed handoff pages as before. Digest
= Pushover priority -1 (deliberately NOT email — SMTP creds exist but mounting
them in a prompt-injectable pod is an exfil channel; safe shape documented in
`ops-digest.sh`). Memory: `agentic-remediation-lane`.

**Model policy** (#2567 + #2568) — automated agents (responder, shepherd,
dev-env-ops all lanes) pinned to `claude-opus-5`; Tom's interactive dev work =
latest Fable; dispatched subagents = Opus; **any pay-per-token API call =
`claude-sonnet-5`, never Fable, never Opus**. Pinned ids not aliases (aliases lag
a launch by days). Memory: `fable-quota-exhaustion-model-drift`,
`opus-5-exists-training-cutoff-trap`.

---

## 3. Open queue (priority order)

1. **PDB cleanup — do before the control-plane expansion.** Five single-replica
   workloads carry `minAvailable: 1` PDBs (`emqx-core`,
   `postgres16-pgvecto-1`, `vexa-gateway`, `vexa-meeting-api`,
   `ytdrivarr-postgres16-1`) → `ALLOWED DISRUPTIONS: 0` → they veto **every**
   node drain. All five were cleared by hand during the Talos roll. The vexa
   chart was partly fixed (PDBs now gate on `replicaCount > 1`); the CNPG
   singletons + EMQX are NOT. Tom is adding 2 CP nodes soon — this will bite.
2. **qbittorrent VPN placement** — never landed. NOTE the earlier claim "VPN only
   routes on w01" is **WRONG** (corrected 08-23: qb ran fine on w03 with the
   correct Mullvad exit `87.249.134.5`). Reproduce on **w02 specifically** before
   prescribing any nodeSelector. `QbittorrentVpnDown` alert exists and works, but
   it fires for "qb pod unhealthy" too — always confirm the real exit IP.
3. **Gatus → responder coverage gap** (design work, needs a brief). Gatus pages
   Pushover *directly*, bypassing Alertmanager; the responder polls Alertmanager
   only, so app-level failures never reach the remediation lane. Surfaced by an
   slskd 503 page on 08-22. Options: route Gatus through Alertmanager, or teach
   the responder to poll the Gatus API.
4. **slskd Gatus false page** (small) — slskd returns 503 for ~2h after any
   reschedule while it rescans shares (API doesn't bind until done). Not a fault;
   restarting it makes it worse. Give its Gatus check a startup grace.
5. **Untested paths in the new lane** — a real `ACTION: urgent` incident flowing
   responder→rem, and the cannot-fix→`esc-*` promotion. Deployed and their
   contracts proven synthetically, but do not claim verified until exercised.

---

## 4. Hard-won gotchas (do not relearn these)

- **Test the deployed system, not the diff.** #2569 defined `REMEDIATE_SH` but
  had **no call site** — the entire feature was disconnected and the diff looked
  complete. Only running it live found it.
- **Dispatched agents burn out.** Three agents hit 100% context and wandered
  off-brief onto unrequested work. Check BOTH `ctx:` and the model name in the
  tmux pane status line before trusting long-running agent output. Split briefs
  smaller than feels necessary.
- **NEVER merge a dev-env PR from inside this pod** (Tom's rule) — it bounces the
  pod and kills live sessions. Open it as a **held draft** and hand Tom the PR
  number; an outside agent shepherds it in.
- **ConfigMap keys** must match `[-._a-zA-Z0-9]+` and values must be
  **single-encoded** JSON (`jq -nc '…|tojson'` double-encodes). Both bugs shipped
  and had to be fixed live.
- `kubectl auth can-i create pods/exec` lies — use `--subresource=exec`. The ops
  SA **can** exec into ns `dev`.
- Canonical clones in `~/repos/` are **fetch-only**; work in `~/work/<slug>`
  worktrees. (The prior session polluted `~/repos/haynes-ops` and had to reset it.)

---

## 5. Live cluster context

- Ceph `HEALTH_OK`. Six `AUTH_INSECURE_*`/`AUTH_EMERGENCY` checks are **muted in
  git deliberately** — daemon keys are `aes256k`; CSI keys stay `aes` until Talos
  ships **kernel ≥ 7.0** (6.18 is the newest LTS; 7.0 was short-lived and is
  already EOL — realistically 2027). Issue **#2538**. Do not "fix" those mutes.
- talosw01 died and was rebooted by Tom on 08-23; recovered clean. It is the
  GPU/nvidia worker. Hardware-suspect history is on **m02/m03**, not w01.
- Two dev-env agent sessions from the prior day are spent/stale; their tmux
  windows may be gone after the bounce. Their worktrees under `~/work/` are
  harmless leftovers.

---

## 6. Open PRs / issues at handoff time

| # | State | What |
|---|---|---|
| **2568** | **HELD DRAFT** | dev-env model policy + `declare-activity`. Merging it is what bounced the pod. If you are reading this post-bounce it presumably merged — verify with §1. |
| 2565 | open | Renovate: ceph-csi-rbd 3.17.1. Touches the NEW external-ceph CSI (see §7) — vet against that migration, not blind-merge. |
| 2506 | open | **ONE-WAY, needs Tom's click**: authentik 2026.8.0 (forward-only DB migration; CNPG PITR is the only recovery). Fully prepared by the shepherd, deliberately not auto-merged. Do NOT merge for him. |
| 2504 | open | Renovate's own authentik-major PR, superseded by #2506. Leave. |
| 2505 | open | valkey v9 major (used only by ai/vexa). Not in the shepherd ramp — needs a human or a tier decision. |
| issue **2538** | open | Ceph cephx CSI-key rotation, **kernel-gated** (needs Talos on kernel ≥7.0, realistically 2027). Mutes are git-managed and intentional. Do not "fix" them. |
| issue 1066 | open | "Better Monitoring & Automated Recovery" — the agentic remediation work in §2 is largely this; worth reconciling/closing. |

## 7. ⚠️ UNREVIEWED production change — observability storage migration

The drifted cleanup agent (`haynes-ops-0821-130259`) burned its context on work
**that was never in its brief** and merged it: **#2558–#2563**. It moved
**Prometheus + Loki onto a new external Proxmox Ceph cluster** via a new
`gasha01-rbd` StorageClass (`gasha01.rbd.csi.ceph.com`), added a Kyverno
pss-baseline exemption for those CSI pods, and re-sized retention.

State right now: `loki-0` and `prometheus-kube-prometheus-stack-0` are 2/2 and
have ~28h uptime, StorageClass exists, monitoring works. So it is *functioning* —
but **I never reviewed it and it was not requested**. Someone should audit it:
data migration correctness, retention sizing, the Kyverno exemption's blast
radius, and whether TSDB/chunk history actually came across. Treat as
"working but unaudited", not as blessed.

Related: this is also why Alertmanager is now an HA pair (#2555) — that part
*was* in the brief (track 3) and is fine.

## 8. Dispatching agents (you will want to)

```bash
agent-run haynes-ops --agent claude --model claude-opus-5 --effort xhigh --interactive
# then: tmux send-keys -t task-<id>:claude "Read <brief path> and execute end-to-end." Enter
```
- **Fable credits were exhausted 2026-08-23** (may have reset by the time you
  read this). Symptom: silent drift to Opus, or `out of usage credits` +
  `Worked for 0s`. Diagnose as a credit wall, not an agent-run bug.
- **Keep briefs SMALL.** Three agents in a row hit 100% context and wandered.
  One brief = one landable PR. Check `ctx:` and the model name in the pane
  status line before trusting output.
- Stale worktrees exist under `~/work/` from spent sessions (`0821-130259`,
  `0821-163627`, `0822-213836`). Harmless; `agent-run prune` cleans stranded ones.
- `haynes-ops-0821-163627` has unlanded Fable-era work in its worktree — it built
  the esc-* wiring that DID land (#2560); nothing known to be missing, but check
  before reaping if you care.

## 9. Known-noise (do NOT chase these)

- `ytdl-sub-*` KubeJobFailed — expected; Alertmanager routes them to null.
- `CephNodeDiskspaceWarning` on a node right after a reboot — `predict_linear`
  false positive from image re-pull churn; w01 was 66% used / 183GB free.
- `slskd` 503 for ~2h after any reschedule — it rescans shares before binding
  its API (was still 0/1 at 97m on 08-23). **Restarting it makes it worse.**
  Gatus pages for this; a startup-grace fix is queued (§3 item 4).
- `etcdDatabaseHighFragmentationRatio` — long-standing warning.
- AppDaemon checkers reporting `unknown` right after an AD reload — cold-start
  artifact, self-heals ≤30min. Tom declined a fix; do not re-propose.

## 10. Memory durability gap (worth solving)

Agent memory (`~/.claude/projects/-home-dev-repos-haynes-ops/memory/`) is
**PVC-only**: it survives a bounce but dies with the volume, and is invisible to
`dev-env-ops` sessions and any outside agent. The `rem-*`/`esc-*` sessions in the
ops pod therefore operate with none of this pod's institutional knowledge (the
Ceph mute rationale, drain-blocker signature, the qb-VPN correction) — they will
rediscover or contradict it. Fix shape: commit a curated subset into `.agents/`
as shared substrate, or give the ops pod read access. Raised with Tom 08-23, not
yet queued.
