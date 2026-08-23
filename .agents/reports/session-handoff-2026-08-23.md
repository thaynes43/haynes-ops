# Session handoff — dev-env cold start after the 2026-08-23 pod bounce

**Durable copy.** A PVC copy lives at `/home/dev/work/HANDOFF-post-bounce.md`, but
that is readable only from inside the dev-env pod and dies with the volume — this
is the canonical one. Point a fresh session at this file.

You are a fresh session in the dev-env pod. The previous session
(`session_011S5HnA71jHSsCoWWmXE4F3`, 2026-08-20 → 08-23) was ended by the pod
roll that merging **PR #2568** caused. Read this, run the verification block,
then pick up the queue. Everything below is verified state, not plans.

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
