# Stack Health & Reliability Audit — 2026-07-07

**Scope:** `main` + `edge` clusters, GitOps repo `haynes-ops`.
**Method:** Read-only cluster introspection (`kubectl get/top/logs`, `flux get`), Grafana/Prometheus MCP, static repo analysis. No cluster mutations.
**Branch:** `audit/stack-health-2026-07-07` (cluster revision + git HEAD both at `55d57969` — no Flux drift).

## TL;DR — the stack is healthy

Clean bill across most dimensions:

| Dimension | Result |
|---|---|
| Flux Kustomizations / HelmReleases / sources | **All Ready**, none suspended/failed, 0 drift |
| Firing Prometheus alerts | **None** (only `Watchdog` deadman's-switch = healthy) |
| Rook/Ceph | **HEALTH_OK**, phase Ready |
| cert-manager | **13/13 certs Ready** |
| Kyverno policy reports | **0 FAIL / 0 WARN / 0 ERROR** across all reports |
| Deprecated k8s APIs (pre-1.36) | **None found** in repo |
| CNPG Postgres backups | Both clusters, scheduled, last **8h ago**, completed |
| VolSync backups | **31/31 sources** last result `Successful` |
| Node resource pressure | Max **36%** memory, <8% CPU — ample headroom |
| PVC fill | Highest **73%** — none over 80% |

No **Critical** or **High** findings. The items below are **Medium/Low** — right-sizing, latent traps, and recurring-but-self-healing reliability issues.

Severity uses: Critical = active outage/data-loss now; High = imminent; Medium = latent risk / waste worth fixing; Low = polish / watch.

---

## What I CHANGED (safe mechanical fixes on this branch)

All four are memory right-sizing in `home-automation`, non-scoped-out, evidence-backed, YAML-validated. QoS class unchanged (all remain Burstable — no CPU limit set).

| App | Change | Evidence |
|---|---|---|
| `zigbee2mqtt` | Add explicit `requests.memory: 512Mi` (was defaulting to limit 4Gi) | Actual working set ~280Mi; k8s silently reserved 4Gi |
| `zwave` | Add explicit `requests.memory: 512Mi` (was defaulting to 4Gi) | Actual ~220Mi |
| `go2rtc` | Add explicit `requests.memory: 256Mi` (was defaulting to 2Gi) | Actual ~15–25Mi |
| `ha-mcp` | Bump `limits.memory` 512Mi → 1Gi | Container **OOMKilled 2026-06-20** at 512Mi |

Net effect of the first three: ~**9.5Gi of phantom memory reservation released** back to the scheduler (see M1).

---

## MEDIUM findings

### M1 — Limit-only-memory "scheduling pin" trap (FIXED for the 3 worst)
**Evidence (live):**
```
zigbee2mqtt  req={cpu:100m, memory:4Gi(defaulted)}  lim={memory:4Gi}   actual ~280Mi
zwave        req={cpu:100m, memory:4Gi(defaulted)}  lim={memory:4Gi}   actual ~220Mi
go2rtc       req={cpu:250m, memory:2Gi(defaulted)}  lim={memory:2Gi}   actual ~25Mi
```
When only `limits.memory` is set, Kubernetes defaults `requests.memory = limits.memory`. These three pods each reserved their full limit (the exact "twins" pin from the plexops history). Nodes have headroom today so it caused no scheduling failure, but it is latent waste and a scheduling landmine.
**Impact:** ~9.5Gi over-reserved; future scheduling pressure could evict/pend workloads that "shouldn't" collide.
**Fix:** CHANGED — explicit right-sized requests added, limits left as-is (headroom preserved).

### M2 — ha-mcp OOMKilled (FIXED)
**Evidence:** `ha-mcp-…` container `lastState.terminated.reason=OOMKilled finishedAt=2026-06-20T15:06:52Z`, limit 512Mi, now running at ~260Mi (50%).
**Impact:** MCP server for Home Assistant dies + restarts under memory spikes.
**Fix:** CHANGED — `limits.memory` 512Mi → 1Gi (request stays 256Mi).

### M3 — kyverno-background-controller at 97% of memory limit (RECOMMENDATION)
**Evidence:** `kyverno-background-controller` req `64Mi` / **lim `128Mi`**, live usage **124Mi = 96.9%** of limit.
**Impact:** OOM would stall background policy reconciliation / policy-report generation (admission path unaffected — separate pod).
**Why not auto-fixed:** Kyverno is a cluster-critical component (per hands-off policy) and the limit is a Helm-chart default. **Recommend** overriding `backgroundController.resources.limits.memory` to `256Mi` via the kyverno HelmRelease values.

### M4 — zigbee2mqtt recurring SIGABRT crashes (RECOMMENDATION)
**Evidence:** 13 restarts; `lastState.terminated exitCode=134 (SIGABRT) finishedAt=2026-07-05T21:40:21Z`. Memory of crashed containers climbed to ~2.3GB before abort (not an OOM — 4Gi limit not reached).
**Impact:** Zigbee mesh drops briefly on each crash/restart; self-heals in seconds. Recurring (~weekly). Note: the M1 request change does **not** affect this — it is a separate application-level crash (likely zigbee-herdsman/adapter assertion or memory-growth). Also recall (CLAUDE.md) EMQX broker restarts can require a z2m restart to re-publish retained MQTT state — verify these aren't broker-coupled.
**Fix:** RECOMMENDATION — investigate z2m logs around crash times; consider z2m version bump / adapter watchdog.

### M5 — comfyui workspace has no backup AND sits on node-local storage (RECOMMENDATION)
**Evidence:** `ai/stable-diffusion/comfyui` PVC `comfyui-workspace` (80Gi) is `storageClassName: openebs-hostpath` with **no VolSync component**. openebs-hostpath is node-local and is **wiped on node re-image** (per re-image-aftermath history).
**Impact:** Custom workflows/inputs lost on node re-image or disk failure. Models are re-downloadable, so partial data-loss.
**Fix:** RECOMMENDATION — either add a VolSync component, or explicitly accept as regenerable (document intent).

---

## LOW findings

### L1 — Remaining limit-only-memory traps (small / cluster-critical)
Same trap as M1 but small absolute waste; several are cluster-critical so not touched:
`cloudflare-tunnel` (256Mi, actual ~40Mi), `onepassword-connect` (256M×2 containers), `kromgo` (64Mi, actual ~21Mi), `nvidia-device-plugin` (512Mi), `dragonfly` (128Mi — DB, cluster-breaker). **Recommend** adding explicit requests opportunistically; low urgency.

### L2 — Fleet-wide CPU limits (throttling anti-pattern)
Nearly every app sets a CPU limit (e.g. media *arr apps 2000m, esphome 16, home-assistant 10). Home-ops norm (onedr0p/bjw-s) is **requests-only, no CPU limits** to avoid CFS throttling. **Recommend** dropping CPU limits fleet-wide (keep requests). Judgment call, out of safe-mechanical scope — not changed.

### L3 — PVCs trending toward capacity (watch, none >80%)
`plex-metadata` 73%, `lidarr` 72%, `esphome` 67%, `radarr` 62%. No action needed yet; watch `plex-metadata`/`lidarr`.

### L4 — Minor VolSync coverage gaps
`soularr` (`soularr-data` 1Gi ceph-block, `prune:disabled` but no backup — small config/state) and `recyclarr` (config regenerable from git). **Recommend** VolSync for soularr if its state (scan markers) is non-trivial to rebuild; recyclarr is fine as regenerable. (Not changed — adding VolSync is non-trivial wiring + restic secret, and soularr is adjacent to the download pipeline another agent owns.)

### L5 — external-dns-unifi occasional restarts
9 historical restarts (`external-dns` container, exitCode 1, last 2026-07-04); **currently healthy** ("All records are already up to date", running since 07-04). Monitor; no action.

### L6 — `task kubernetes:kubeconform` is broken for this repo layout
`scripts/kubeconform.sh` hardcodes `${KUBERNETES_DIR}/flux`, but the repo layout is `kubernetes/main/flux` / `kubernetes/edge/flux`. `task kubernetes:kubeconform` (passes `kubernetes`) fails with `No such file or directory`. Pre-existing tooling bug — CI (flux-local) still validates PRs. **Recommend** fixing the script to iterate `kubernetes/*/flux` (or point the task at `kubernetes/main`).

---

## Scoped-out areas (NOTED only — owned by other agents, no code changed)

- **kometa** (`media/kometa`): 16Gi memory limit with `req 1Gi` (deliberate headroom for its known OOMs); PVC `kometa-config` has no VolSync. Flagged for the kometa owner.
- **gatus** (`observability/gatus`): two limit-only-memory blocks (64Mi, 256Mi) — same M1 trap; left for the gatus owner.
- **qbittorrent / slskd / sabnzbd**: VolSync present and `Successful`; sabnzbd's 32Gi memory limit is deliberate (past par2 OOMs). No issues to report beyond ownership.

---

## Unverified (could not confirm)

- **Edge cluster (`omni-haynes-edge`)**: unreachable — API times out (cluster powered off, per the "two-omnis" history). **All edge-specific audit items unverified.** No edge findings produced.
- **kubeconform schema validation**: `kubeconform`/`kustomize` binaries are absent in this worktree, and the task target is broken (L6). Edited manifests were validated for **YAML well-formedness + resources-block structure** (Python `yaml.safe_load_all`), not full schema. CI `flux-local` will schema-validate on the PR.

---

## Appendix — key commands / queries used

- `flux get kustomizations -A` / `helmreleases -A` / `sources all -A` → all Ready, rev `55d57969`.
- `kubectl get cephcluster -n rook-ceph -o jsonpath` → `phase=Ready health=HEALTH_OK`.
- Prometheus: `100 * kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes` (PVC fill); `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}` (only `ha-mcp`); `container_memory_working_set_bytes / kube_pod_container_resource_limits` (limit ratios); `ALERTS{alertstate="firing"}` (only Watchdog).
- `kubectl get replicationsource -A` + JSON age check → 31 Successful; plex/plexops are weekly-by-design (`0 0 * * 0`), so their 56h age is expected, not stale.
- Static: `grep -rl components/volsync`, resources-block scanner over all 100 helmreleases, deprecated-API grep.
