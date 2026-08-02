# Handoff: DNS-canary readout — name the hop dropping A-records (or clear DNS)

**For**: the dev-env in-cluster agent (you have an in-cluster SA — no Omni OIDC
browser expiry — and you can set watch timers; that's why this is yours).
**From**: WSL session 2026-08-01 (Tom + Claude), which kept losing kubectl to
OIDC expiry mid-investigation.
**Done =** verdict comment posted on PR #2332 + Tom told + this file removed
(convention: completed handoffs are deleted via PR, see #2301 for an example).

## Mission

A `dns-canary` Deployment went live 2026-08-02T03:52Z (PR #2332). Let it soak,
then correlate its anomalies against the flux-system egress errors and deliver
ONE of the verdicts below with evidence. Set yourself watch timers — suggested
checks at **+6h (early signal), +24h (main readout), +48h (final if quiet)**
from canary start. Do not sit idle-polling; schedule and yield.

First actions: `kubectl auth can-i get pods/log -n observability && kubectl
auth can-i get pods/log -n flux-system` — if either fails, stop and tell Tom.

## Background you should NOT re-derive (evidence already collected)

- **Symptom**: flux source-controller/kustomize-controller intermittently fail
  outbound fetches with `dial tcp [2606:...]:443: connect: network is
  unreachable` — always an IPv6 address, failures complete in <1s. The cluster
  is v4-only, so instant v6-ENETUNREACH with no v4 attempt in the error means
  **the dial list contained only AAAA answers at that moment** (a v4 timeout
  would have taken seconds and been the reported error).
- **Timeline**: sparse blips ≤07-29 (a handful total in 14 days) → dense
  episode 07-30 05:59–09:42 ET (paged via the dragonfly ks, since fixed) →
  **chronic since: 2–14/hour, ~130/day, every hour**, fully absorbed by
  source-controller artifact caches (nothing user-visible, nothing pages).
- **Cleared suspects** (all checked 08-01): WAN/ISP — unpoller shows zero
  drops/interface errors/downtime, ISP dashboard clean. CoreDNS — ~zero
  SERVFAIL over 24h. Spot probes — 3×120 A-queries via cluster DNS, UDM
  (192.168.0.1), and 1.1.1.1 all perfect. WAN-level Site Manager metrics are
  unavailable (cloud API key not provisioned) and not needed.
- **Remaining hypotheses**:
  - **H1**: some hop occasionally returns **NOERROR with an empty A answer
    (NODATA)** — invisible in rcode metrics and error logs; exactly produces
    the AAAA-only dial list. The canary exists to catch this in the act.
  - **H2**: CoreDNS chart **1.46.2 → 1.47.0** rolled 07-30 ~04:30 ET (#2305) —
    ~90 min before chronic onset. Blips predate it (so it's not the sole
    cause) but the RATE change correlates. Prime suspect *if* the canary pins
    the cluster path.
  - Unrelated aside, don't chase: CoreDNS fields ~33 NXDOMAIN/s (ndots search
    expansion noise) — a someday-cleanup, not this bug.

## The canary (what's deployed)

`kubernetes/main/apps/observability/dns-canary/` — netshoot pod on talosw02
(same node as the flux controllers), every 15s queries **A** records for
`charts.goauthentik.io`, `helm.openwebui.com`, `raw.githubusercontent.com` via
(a) cluster DNS (pod resolv.conf, the path flux uses) and (b) `@192.168.0.1`
(UDM direct). Logs ONLY anomaly lines + a 15-min heartbeat; promtail ships to
Loki. Anomaly line format:
`<ts> ANOMALY resolver=cluster|udm host=<h> kind=empty-A-NODATA|<rcode>|timeout aaaa=<same-instant AAAA or none>`

## Readout queries

```
# canary anomalies (Loki; or: kubectl logs -n observability deploy/dns-canary | grep ANOMALY)
{namespace="observability", pod=~"dns-canary.*"} |= "ANOMALY"
# heartbeats prove liveness (expect one per 15m; queries=120 per beat)
{namespace="observability", pod=~"dns-canary.*"} |= "heartbeat"
# the flux-side errors to correlate against (timestamps ±2min)
{namespace="flux-system"} |= "network is unreachable"
```

Expected catch rate if DNS is guilty: ~5,700 queries/day/resolver vs ~0.1%
event rate → a handful of anomalies per day. Zero canary anomalies while flux
logged 50+ errors in the same window is itself a verdict (see below).

## Verdicts → actions

1. **cluster-path anomalies, UDM clean** → CoreDNS/cache layer. Action
   (pre-approved by Tom's standing revertible-change policy — coredns is
   stateless, shepherd-ramp, clean revert): open + merge a PR reverting the
   coredns chart to 1.46.2, reconcile, then measure the flux error rate for
   12–24h. Rate collapses → 1.47.0 is causal: re-hold it, file upstream, tell
   Tom. Rate unchanged → re-apply 1.47.0 (roll forward), keep digging at the
   CoreDNS config layer (cache/serve_stale/prefetch interplay).
2. **anomalies on BOTH paths** → the UDM's resolver/upstream is dropping A
   answers. No cluster fix exists; report to Tom with the evidence (he owns
   the UDM; likely UDM DNS settings/upgrade or switching CoreDNS forward to
   1.1.1.1 — the latter is a cluster change, propose it, don't do it).
3. **canary clean ×48h while flux errors continue** → NOT DNS. The dial-layer
   is next (conntrack on the UDM, Cilium egress path). Report + propose next
   probe; don't freelance a new theory into the cluster without checking in.
4. **flux errors stopped on their own** → note when, correlate with anything
   that changed, report, and proceed to cleanup.

Anomaly lines where `aaaa=<addr>` while the A answer was empty are the smoking
gun — quote them in the verdict.

## Cleanup (when the verdict is delivered and acted on)

- Comment the verdict + evidence on PR #2332.
- Remove the canary: revert #2332 (delete
  `kubernetes/main/apps/observability/dns-canary/` + its line in
  `kubernetes/main/apps/observability/kustomization.yaml`) once it has no
  further diagnostic value. Note `prune: true` — deleting the ks removes the
  Deployment cleanly; nothing stateful.
- Delete this handoff file in the same PR.

## Context pointers

- PRs: #2311/#2313 (dragonfly fix — why nothing pages anymore), #2332 (canary),
  #2305 (coredns 1.47.0), #2312 (shepherd — unrelated).
- The 07-30 incident triage lives in this repo's PR bodies (#2311, #2313) and
  the WSL session's memory; do not re-triage it.
