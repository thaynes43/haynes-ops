# HelmRelease `chartRef` Migration Plan (app-template)

## Goal

Standardize all `HelmRelease` resources that currently use:

```yaml
  chart:
    spec:
      chart: app-template
      version: 3.7.3
      interval: 30m
      sourceRef:
        kind: HelmRepository
        name: bjw-s
        namespace: flux-system
```

…to instead use:

```yaml
  chartRef:
    kind: OCIRepository
    name: app-template
    namespace: flux-system
```

This change is a **reference mechanism migration** (from `chart.spec` to `chartRef`). It can still trigger workload replacement if the chart version/labels differ from what originally created the workload.

## Why this matters

- **Consistency**: `chartRef` is simpler and aligns with the rest of the repo (example: `ollama-assist01`).
- **Shared source of truth**: `OCIRepository/app-template` in `flux-system` becomes the chart source for all app-template-based releases.
- **Operational reality**: upgrades can fail with `spec.selector ... field is immutable` when the chart changes workload selector labels. That requires a delete/recreate of the controller object.

## Pre-flight checklist

- **Confirm the source exists**:
  - `OCIRepository/app-template` exists in `flux-system` and is healthy.
  - All `HelmRelease` that will reference it include:
    - `spec.chartRef.namespace: flux-system`

- **Identify all targets** (repo-side):

```bash
rg -n --glob "**/helmrelease.yaml" "chart:\n\\s+spec:\n\\s+chart: app-template" kubernetes
```

### Files in this repo that need the change

The following `HelmRelease` files currently use `spec.chart.spec.chart: app-template` (and in practice are pinned to `3.7.3`) and should be migrated to `spec.chartRef`:

- `kubernetes/main/apps/ai/ollama/assist02/app/helmrelease.yaml`
- `kubernetes/main/apps/ai/ollama/prime/app/helmrelease.yaml`
- `kubernetes/main/apps/photos/immich/server/helmrelease.yaml`
- `kubernetes/main/apps/photos/immich/machine-learning/helmrelease.yaml`
- `kubernetes/main/apps/office/paperless-ngx/app/helmrelease.yaml`
- `kubernetes/main/apps/network/cloudflare-ddns/app-www/helmrelease.yaml`
- `kubernetes/main/apps/network/cloudflare-ddns/app/helmrelease.yaml`
- `kubernetes/main/apps/media/plex/app/helmrelease.yaml`
- `kubernetes/main/apps/home-automation/home-assistant/app/helmrelease.yaml`
- `kubernetes/main/apps/home-automation/esphome/app/helmrelease.yaml`
- `kubernetes/main/apps/database/dragonfly/app/helmrelease.yaml`
- `kubernetes/main/apps/downloads/ytdl-sub/youtube/helmrelease.yaml`
- `kubernetes/main/apps/downloads/ytdl-sub/peloton/helmrelease.yaml`
- `kubernetes/main/apps/downloads/ytdl-sub/peloton-config-manager/helmrelease.yaml`
- `kubernetes/main/apps/storage/storage-util/rsync-scans/app/helmrelease.yaml`
- `kubernetes/main/apps/storage/storage-util/rsync-photos/app/helmrelease.yaml`
- `kubernetes/main/apps/storage/storage-util/rsync-music/app/helmrelease.yaml`
- `kubernetes/main/apps/observability/kromgo/app/helmrelease.yaml`
- `kubernetes/main/apps/observability/gatus/app/helmrelease.yaml`
- `kubernetes/main/apps/home-automation/zwave/app/helmrelease.yaml`
- `kubernetes/main/apps/home-automation/go2rtc/app/helmrelease.yaml`
- `kubernetes/main/apps/network/multus/app/helmrelease.yaml`
- `kubernetes/main/apps/ai/wyoming-protocol/whisper/helmrelease.yaml`
- `kubernetes/main/apps/ai/wyoming-protocol/piper/helmrelease.yaml`
- `kubernetes/main/apps/ai/wyoming-protocol/openwakeword/helmrelease.yaml`
- `kubernetes/main/apps/ai/wyoming-protocol/speech-to-phrase/helmrelease.yaml`
- `kubernetes/main/apps/home-automation/zigbee2mqtt/app/helmrelease.yaml`
- `kubernetes/main/apps/external-secrets/onepassword-connect/app/helmrelease.yaml`
- `kubernetes/main/apps/home-automation/music-assistant/app/helmrelease.yaml`
- `kubernetes/main/apps/photos/hass-immich-addon/app/helmrelease.yaml`
- `kubernetes/main/apps/media/overseerr/app/helmrelease.yaml`
- `kubernetes/main/apps/kube-system/intel-device-plugin/exporter/helmrelease.yaml`
- `kubernetes/main/apps/ai/stable-diffusion/comfyui/helmrelease.yaml`

## Repo changes (mechanical)

For each target `HelmRelease`:

1. Remove the entire block:

```yaml
spec:
  chart:
    spec:
      chart: app-template
      version: 3.7.3
      interval: 30m
      sourceRef:
        kind: HelmRepository
        name: bjw-s
        namespace: flux-system
```

2. Add (or replace with) `chartRef`:

```yaml
spec:
  chartRef:
    kind: OCIRepository
    name: app-template
    namespace: flux-system
```

Notes:
- Keep `spec.interval` as-is.
- This does **not** change `metadata.namespace` (the release namespace); it only changes where the chart is sourced from.

## Rollout strategy (recommended)

Do this in batches to reduce blast radius and make it easy to isolate failures:

- **Batch 1 (low risk)**: CronJobs
- **Batch 2**: Deployments
- **Batch 3 (higher operational impact)**: StatefulSets

If a batch fails due to immutable selectors, fix those releases before proceeding to the next batch.

## Cluster commands (per release)

### 1) Force a fresh reconcile (normal path)

```bash
flux reconcile helmrelease <name> -n <namespace> --with-source
```

### 2) Debug “stuck” or repeated rollback

Get status and events:

```bash
flux get helmrelease -n <namespace> <name>
kubectl -n <namespace> describe helmrelease <name>
```

Inspect what Helm thinks it is applying:

```bash
kubectl -n <namespace> get events --sort-by=.lastTimestamp | tail -n 50
```

## Handling immutable selector failures (Deployment/StatefulSet/DaemonSet)

Symptom in helm-controller logs/events:

- `... is invalid: spec.selector ... field is immutable`

This means the controller already exists and Kubernetes will not allow changing `.spec.selector`.

### Fix pattern (GitOps-friendly)

1) Pause reconciliation for the release:

```bash
flux suspend helmrelease <name> -n <namespace>
```

2) Find the workload objects for that release (common labels present in this repo):

```bash
kubectl -n <namespace> get deploy,sts,ds,cronjob -l helm.toolkit.fluxcd.io/name=<name>
```

3) Delete the controller object that is failing.

Deployment:

```bash
kubectl -n <namespace> delete deployment <workload-name> --wait=true
```

StatefulSet:

```bash
kubectl -n <namespace> delete statefulset <workload-name> --wait=true
```

DaemonSet:

```bash
kubectl -n <namespace> delete daemonset <workload-name> --wait=true
```

Notes:
- Deleting the controller **does not delete PVCs** (unless you delete PVCs separately).
- If you are unsure, verify volumes are PVC-backed (`existingClaim`) before deletion.

4) Resume and reconcile:

```bash
flux resume helmrelease <name> -n <namespace>
flux reconcile helmrelease <name> -n <namespace> --with-source
```

5) Verify the new controller is on the intended chart version:

```bash
kubectl -n <namespace> get deploy <workload-name> -o jsonpath='{.metadata.labels.helm\.sh/chart}{"\n"}'
```

## CronJob notes

CronJobs typically do not hit selector immutability issues in the same way as Deployments/StatefulSets. If a CronJob upgrade fails, the simplest recovery is usually:

```bash
flux suspend helmrelease <name> -n <namespace>
kubectl -n <namespace> delete cronjob <workload-name> --wait=true
flux resume helmrelease <name> -n <namespace>
flux reconcile helmrelease <name> -n <namespace> --with-source
```

## Standard verification steps

After each release migration:

```bash
flux get helmrelease -n <namespace> <name>
kubectl -n <namespace> get pods -l app.kubernetes.io/instance=<name>
kubectl -n <namespace> get ingress,svc -l app.kubernetes.io/instance=<name>
```

## Known gotchas in this repo

- **Cross-namespace chart sources**: if `chartRef.namespace` is omitted, you can get confusing failures. Always set:
  - `spec.chartRef.namespace: flux-system`
- **Shared source bump affects many apps**: when `OCIRepository/app-template` tag is changed, multiple apps can upgrade at once. Prefer batching/PRs rather than big-bang.

