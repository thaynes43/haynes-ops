## Health signals with `wait: false` (reference repo patterns)

This documents the patterns used in the reference repo (`example-ops/onedr0p-home-ops`) to get **strong health signals** even when many Flux `Kustomization`s are set to `wait: false`.

The goal is not to copy blindly, but to capture the “signal sources” we may want to adopt in `haynes-ops` later.

---

### The core idea

`wait: false` prevents a Flux `Kustomization` from blocking on health checks for everything it applies. That reduces “deadlocks” (one broken app shouldn’t stall the whole cluster-apps tree), but it means you need **other** mechanisms to surface failures quickly and reliably.

The reference repo gets those signals from:

- Flux Notifications (events → Alertmanager / GitHub status)
- Prometheus scraping + alert rules for Flux itself
- HelmRelease remediation defaults (retries/rollback behavior is consistent)
- Targeted `healthChecks` / `healthCheckExprs` for a small set of “critical infra” Kustomizations

---

## Pattern 1: Flux Notifications (errors → Alertmanager)

Reference repo uses Flux notifications to send **error events** for most Flux objects to Alertmanager.

Files:

- `example-ops/onedr0p-home-ops/kubernetes/components/alerts/alertmanager/provider.yaml`
- `example-ops/onedr0p-home-ops/kubernetes/components/alerts/alertmanager/alert.yaml`

Notable behaviors:

- **Broad coverage**: the `Alert` watches errors from `FluxInstance`, `GitRepository`, `Kustomization`, `HelmRelease`, `OCIRepository`, etc.
- **Noise control**: an `exclusionList` drops known-flaky patterns (e.g., transient GitHub DNS/timeout lookups).

Why it matters with `wait: false`:

- Even if a `Kustomization` “applies clean”, a failing `HelmRelease` still emits events and flips `READY=False`. Notifications catch that without relying on KS health gating.

---

## Pattern 2: Flux Notifications (Kustomization → GitHub status)

Reference repo also drives GitHub status from Kustomizations.

Files:

- `example-ops/onedr0p-home-ops/kubernetes/components/alerts/github-status/provider.yaml`
- `example-ops/onedr0p-home-ops/kubernetes/components/alerts/github-status/alert.yaml`

Why it matters:

- This is a “developer feedback loop” signal. It’s not cluster correctness on its own, but it makes breakage visible even when `wait: false` is used widely.

---

## Pattern 3: Flux Instance monitoring (PodMonitor + PrometheusRule)

The reference repo monitors Flux controllers and alerts if Flux itself isn’t healthy.

Files:

- `example-ops/onedr0p-home-ops/kubernetes/apps/flux-system/flux-instance/app/podmonitor.yaml`
- `example-ops/onedr0p-home-ops/kubernetes/apps/flux-system/flux-instance/app/prometheusrule.yaml`
- `example-ops/onedr0p-home-ops/kubernetes/apps/flux-system/flux-instance/app/grafanadashboard.yaml`

What it does:

- **PodMonitor** scrapes metrics from:
  - `source-controller`
  - `kustomize-controller`
  - `helm-controller`
  - `notification-controller`
- **PrometheusRule** alerts on:
  - **FluxInstanceAbsent** (no metrics)
  - **FluxInstanceNotReady** (ready != True for 5m)
- **GrafanaDashboard** imports Flux dashboards from upstream URLs (Flux Operator + flux2 monitoring example).

Why it matters with `wait: false`:

- If Flux controllers are degraded, `wait` settings on downstream Kustomizations are irrelevant. This gives a first-line signal that “GitOps is broken”.

---

## Pattern 4: “Fast apply” + “strong remediation” defaults

Reference repo uses a root `cluster-apps` Kustomization with `wait: false`, but patches all child Kustomizations to inject **HelmRelease remediation defaults**.

File:

- `example-ops/onedr0p-home-ops/kubernetes/flux/cluster/ks.yaml`

What it injects (high level):

- Install/upgrade strategies (`RetryOnFailure`, `RemediateOnFailure`)
- CRD install/upgrade behavior (`CreateReplace`)
- Rollback cleanup + recreate
- Upgrade retries/remediation behavior

Why it matters with `wait: false`:

- You get consistent “self-healing” without relying on Kustomization health gating.
- Failures become visible primarily in `HelmRelease` status/events, and are forwarded via notifications.

---

## Pattern 5: Targeted `healthChecks` / `healthCheckExprs` (selected infra)

The reference repo adds explicit `healthChecks` and `healthCheckExprs` on some infrastructure Kustomizations (examples include `cert-manager`, `onepassword`, and `cloudflare-dns`).

Examples:

- `example-ops/onedr0p-home-ops/kubernetes/apps/cert-manager/cert-manager/ks.yaml`
  - health check a `HelmRelease` plus a `ClusterIssuer`
  - expression-based readiness for `ClusterIssuer` conditions
- `example-ops/onedr0p-home-ops/kubernetes/apps/external-secrets/onepassword/ks.yaml`
  - expression-based readiness for `ClusterSecretStore` conditions
- `example-ops/onedr0p-home-ops/kubernetes/apps/network/cloudflare-dns/ks.yaml`
  - health check a `HelmRelease` plus a CRD

Important nuance:

- `healthChecks` are only useful if Flux is actually performing health evaluation for that Kustomization. In `haynes-ops`, if we adopt this pattern, we should be explicit about which Kustomizations are “gated” (set `wait: true` where we truly want readiness to reflect downstream health) vs “fast apply” (keep `wait: false`).

---

## Receiver pattern: GitHub webhook triggers immediate reconcile

The reference repo creates a `Receiver` that listens for GitHub push events and triggers reconciliation of the `GitRepository` and root `Kustomization`.

File:

- `example-ops/onedr0p-home-ops/kubernetes/apps/flux-system/flux-instance/app/receiver.yaml`

Why it matters:

- Reduces time-to-detect for broken commits without requiring very tight intervals.

---

## What we could adopt in `haynes-ops` (later)

If we want “strong health signals” while keeping `wait: false` broadly:

- **Flux → Alertmanager notifications**: likely the highest value and lowest risk.
- **Flux controller metrics scraping + rules**: also high value; requires Prometheus stack integration.
- **HelmRelease remediation defaults via root patches**: medium risk (behavior change), but powerful.
- **Selective `wait: true` + healthChecks on critical infra**: use sparingly to avoid deadlocks.
- **GitHub status provider/alert**: optional; most useful if you care about PR/commit status feedback.

