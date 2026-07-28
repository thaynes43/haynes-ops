# Drill record — Phase A (pod-level failures), 2026-07-28

**Scope:** plan 07 Phase A only — pod deletions, no node operations (those are Phase B,
owner-executed). Operator: dev-env pod (scoped pod-delete SA). Measurement: Gatus
`external_haynesnetwork` (apex WAN check, 1m cadence) + a 5s LAN probe of the staging host
(`haynesnetwork.haynesops.com`, traefik-internal) + kubectl snapshots at 5s.

## A1 — app pod loss: ZERO-DOWNTIME

Deleted `haynesnetwork-main-…-9rpc6` (talosw02) at 16:56:11.9Z. Replacement scheduled the same
second onto talosw02 (the only node with no sibling replica — maxSkew=1 behaving), **Ready at
t+16s**. Every staging probe in the window: HTTP 307 (expected auth redirect); zero failures. The
Gatus tick ~4s after the delete passed, as did the next three (200 at 215/297/259ms). PDB never
dipped below currentHealthy=2.

## A2 — traefik-external pod loss: ZERO-DOWNTIME

Deleted `traefik-external-…-l95tk` (talosw01) at 16:59:16.8Z. Replacement scheduled onto talosw01
and **Ready at t+5s**; the old pod flipped NotReady exactly as the new one went Ready, so ≥2 ready
endpoints existed at every instant. All LAN probes 307; the three post-delete WAN ticks 200
(322/294/247ms). No L2 movement occurs on a pod delete — see limits below.

## Findings for Phase B (and runbook fixes)

1. **Selector drift:** the live traefik instance label is
   `app.kubernetes.io/instance=traefik-external-network`, NOT `…=traefik-external`. The bare form
   silently matches zero pods — a scripted drill would no-op and misread it. Use the corrected
   selector everywhere.
2. **No high-res external vantage from the dev-env pod:** direct probes to 192.168.40.206:443 are
   egress-denied (code 000). Phase B's L2-failover step will be measured at Gatus's 1m resolution
   unless a probe runs from an allowlisted vantage; accept the 1m resolution rather than widening
   egress.
3. **Pod-delete does not exercise Cilium L2 failover** (the lease does not move). Phase A proves
   pod-churn resilience only; the "at-most-blip" L2 hypothesis and kubelet-loss pod replacement
   (~5m NotReady toleration) remain Phase B territory: an owner-executed reboot of a worker
   hosting one app replica (talosw02 suggested — it also carries a Postgres standby but NOT the
   primary) via the Omni UI, with the SLI recording the truth whenever it happens.

**Verdict:** the 3-replica + maxSkew=1 + PDB minAvailable=2 posture on both the app and
traefik-external held exactly as designed under pod-level failure. Phase B remains open.
