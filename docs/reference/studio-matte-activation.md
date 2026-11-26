# Studio matte worker policy

[Story 970, part A](https://work.haynesnetwork.com/work_packages/970) adds the
independent Flux stage `ai/studio-matte-policy`. It owns
`NetworkPolicy/ai/studio-matte-worker`, which selects
`app.kubernetes.io/name=studio-matte-worker`. It denies ingress and permits
only DNS to CoreDNS in `kube-system` on TCP and UDP 53 and TCP 8002 to
`studio-api` pods in `ai`. The
[parked Deployment template](https://github.com/thaynes43/haynes-ops/blob/e1286607191dcddf9d33bce7f1ebb6f85979b95a/kubernetes/main/apps/ai/studio/matting-pending/activation/worker/deployment.yaml#L10-L15)
uses that label.

The installed Flux kustomize-controller v1.10.2 checks the live object's
metadata during inventory pruning. The policy's
`kustomize.toolkit.fluxcd.io/prune: disabled` annotation excludes it from
deletion when it drops out of the stage's render. [Flux prune call](https://github.com/fluxcd/kustomize-controller/blob/v1.10.2/internal/controller/kustomization_controller.go#L1185-L1205) ·
[Deletion exclusions](https://github.com/fluxcd/kustomize-controller/blob/v1.10.2/internal/controller/kustomization_controller.go#L1505-L1520) ·
[Live object check](https://github.com/fluxcd/pkg/blob/ssa/v0.83.0/ssa/manager_delete.go#L63-L94) ·
[Annotation matching](https://github.com/fluxcd/pkg/blob/ssa/v0.83.0/ssa/utils/meta.go#L59-L69)

If the parent inventory prunes the stage, `deletionPolicy: Orphan` skips
the stage's resource deletion at finalization. The existing `ai`
namespace has its own prune exclusion. [Orphan value](https://github.com/fluxcd/kustomize-controller/blob/v1.10.2/api/v1/kustomization_types.go#L37-L41) ·
[Finalizer decision](https://github.com/fluxcd/kustomize-controller/blob/v1.10.2/internal/controller/kustomization_controller.go#L1245-L1279) ·
[Namespace manifest](../../kubernetes/main/apps/ai/namespace.yaml)

## Part A installation read-back (Proven)

Flux installed haynes-ops `ed212ad0` on 2026-11-25 at 23:55Z. The read-back
passed at that revision. The policy stage was Ready, the policy matched the
reviewed spec, both retention protections were present, and its selector matched
zero pods, including terminating pods. Cilium logged that it received the policy.
The `studio`, `studio-worker` and `studio-media-workers` stages were mid-reconcile
at the first read and were Ready about 25 seconds later. All ten existing Studio
pods were Running with zero restarts.
[Read-back](https://work.haynesnetwork.com/work_packages/970/activity#comment-10332) ·
[Stage correction](https://work.haynesnetwork.com/work_packages/970/activity#comment-10333)

Run from a clean checkout of the merged Ops commit after Flux pickup and
before the worker activation PR. Set `MERGED_OPS_REVISION` to that commit's
full SHA. The block uses `kubectl`, `jq`, Flux CLI and Python with PyYAML.
Capture the revision, audit output and Cilium watcher line if present.

```bash
(
  set -euo pipefail
  stage=$(kubectl -n ai get kustomization studio-matte-policy -o json)
  policy=$(kubectl -n ai get networkpolicy studio-matte-worker -o json)
  pods=$(kubectl get pods -A -l app.kubernetes.io/name=studio-matte-worker -o json)
  printf '%s\n' "$stage" "$policy" "$pods" | jq -s . |
    python3 -B scripts/tests/studio-release/check.py --audit-matte-policy "$MERGED_OPS_REVISION"

  namespaces=$(kubectl get networkpolicy -A --field-selector metadata.name=studio-matte-worker -o json |
    jq -c '[.items[].metadata.namespace]')
  agent=$(kubectl -n kube-system get pods -l k8s-app=cilium --field-selector spec.nodeName=talosw01 -o json |
    jq -er '.items | if length == 1 then .[0].metadata.name else error("expected one Cilium pod on talosw01") end')
  created=$(jq -er '.metadata.creationTimestamp' <<< "$policy")
  watcher=$(kubectl -n kube-system logs "$agent" -c cilium-agent --since-time="$created" --timestamps |
    awk '/NetworkPolicy successfully added/ && /(^| )k8sNetworkPolicyName=studio-matte-worker( |$)/')
  if [[ $namespaces == '["ai"]' && -n $watcher ]]; then
    printf '%s\n' "$watcher"
  else
    printf '%s\n' 'No unambiguous Cilium watcher receipt in the available logs.'
  fi
)
```

The audit compares every field in the policy spec, treats Kubernetes' omitted
empty ingress list as empty, checks Flux ownership and both retention controls,
and verifies the stage's current generation is Ready at the merge SHA with
only this policy in inventory. The pod list includes terminating pods.

On Cilium v1.20.5, the watcher log confirms the agent parsed and queued
the policy for import. The policy repository logs its completed upsert at
debug level, which is disabled in the installed configuration. Kubernetes
NetworkPolicy exposes no policy status, and there is no matte CiliumEndpoint
to inspect before activation. Record the watcher receipt separately from
enforcement. Part B must prove the expected denial from inside the matte pod
before requesting its grant and keep checking it. [Watcher](https://github.com/cilium/cilium/blob/v1.20.5/pkg/policy/k8s/network_policy.go#L18-L58) ·
[Importer queue](https://github.com/cilium/cilium/blob/v1.20.5/pkg/policy/cell/policy_importer.go#L100-L109) ·
[Repository upsert](https://github.com/cilium/cilium/blob/v1.20.5/pkg/policy/cell/policy_importer.go#L268-L290)

## Deliberate removal (Expected)

1. Ensure the matte workload can no longer create pods. Read the all-namespace
   selector above and record zero pods, including terminating pods. In the
   first PR keep the policy and stage declared, remove the prune annotation,
   set the stage's deletion policy to `MirrorPrune` with `prune: true`,
   and update the guard expectations for that transition. After Flux applies
   it, read back the absence of the annotation, the stage's deletion policy,
   and zero pods again.
2. In a second PR remove the stage and policy source and retire the guard for
   the policy. Confirm Flux deleted both the stage and NetworkPolicy.

## Part B rollout (Expected, held)

PR 5096 remains on hold pending Tom's scoped decision and review of the revised
proof. The steps below apply only after the hold is lifted.
[PR 5096](https://github.com/thaynes43/haynes-ops/pull/5096) ·
[Story 970 hold](https://work.haynesnetwork.com/work_packages/970/activity#comment-10371)

After installation, the matte worker claims cutouts submitted in Studio and
runs them through its local matting sidecar. Before installation, cutouts wait
in the matte queue. If **Generate asset** opens from a waiting cutout and its
submit button is disabled, **Close job view** clears that cutout from the form.
Keep Studio submissions closed and the offline renderer idle from preflight
through postflight. Tom can submit a cutout through the app at review after
these checks and judge the result separately.

The worker uses the installed fourth-pin Studio API image with
`studio-worker --lane matte` and the OP459 matting image and profile. The offline
render limit moves from 20 to 17 CPUs, requiring one more idle render turnover.
The run worker keeps its four CPUs. The matte worker and sidecar take the other
three CPUs. The matte stage waits for the API, the worker policy stage, the
render Helm health gate and the token projection stage.

Under provisional CR-59 and CR-66, the two containers share the pod network
namespace. Only the worker container mounts its token and the Studio store.
The sidecar receives the read-only allocation and the shared Unix socket
directory. Each startup or recurring check requires a single DNS address for
the Studio worker listener and a successful TCP connection to it on port 8002.
The launcher then queries CoreDNS over the allowed TCP 53 path for each
control. It requires one A record in the pod subnet with a positive TTL, then
connects directly to that pod IP. CoreDNS uses Ready EndpointSlice entries for
these names. Its cache can serve an expired record with TTL zero. The launcher
allows one refresh attempt on the same DNS connection and requires a positive
TTL before probing the port.
[CoreDNS settings](../../kubernetes/main/apps/kube-system/coredns/app/helm-values.yaml) ·
[Pod subnet](../../kubernetes/main/bootstrap/omni/cluster-template.yaml#L36-L43) ·
[Endpoint DNS](https://github.com/coredns/coredns/blob/v1.13.1/plugin/kubernetes/kubernetes.go#L487-L516) ·
[Ready filtering](https://github.com/coredns/coredns/blob/v1.13.1/plugin/kubernetes/object/endpoint.go#L85-L98) ·
[Stale cache TTL](https://github.com/coredns/coredns/blob/v1.13.1/plugin/cache/README.md#L59-L67)

| Control | Evidence available inside the matte pod |
| --- | --- |
| ComfyUI TCP 8188 | The app-template 5.3.0 render gives the `comfyui` StatefulSet a governing Service named `comfyui` with TCP 8188. The `comfyui-0.comfyui.ai.svc.cluster.local.` name selects that Ready pod endpoint. The Service VIP can resolve during a backend outage. The StatefulSet uses Kubernetes' default container readiness. An unrestricted render-pod connect checks the listener separately. |
| Loki TCP 3100 | The Loki 7.7.0 single-binary render gives `loki-headless.observability.svc.cluster.local.` a headless Service without `publishNotReadyAddresses`. Its sole backend uses an HTTP `/ready` readiness probe on TCP 3100. |

The manifests give neither destination a matching ingress policy. The
unrestricted render pod in `ai` verifies that it can connect to both pod
endpoints. The matte policy allows only CoreDNS on TCP/UDP 53 and the Studio
worker listener on TCP 8002, so it drops both control connections. Studio
API TCP 8000 cannot serve as a control because its own ingress policy admits
the matte label only on TCP 8002. The Kubernetes API Service's DNS name
gives a VIP without a Ready backend witness and was removed from the proof.
[ComfyUI values](../../kubernetes/main/apps/ai/stable-diffusion/comfyui/helmrelease.yaml) ·
[Loki values](../../kubernetes/main/apps/observability/loki/app/helmrelease.yaml) ·
[Loki headless Service template](https://github.com/grafana/loki/blob/helm-loki-7.7.0/production/helm/loki/templates/single-binary/service-headless.yaml#L25-L34) ·
[Kubernetes pod DNS](https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/#pods) ·
[Matte policy](../../kubernetes/main/apps/ai/studio/matting-pending/worker-policy/networkpolicy.yaml#L12-L30) ·
[API ingress](../../kubernetes/main/apps/ai/studio/app/networkpolicy.yaml#L10-L38)

Only a two-second TCP connect timeout on a witnessed control counts as a
silent drop. A success, refusal, reset, unreachable or other socket error
fails the proof, as does missing or ambiguous DNS. The repository pins
Cilium v1.20.5 with kube-proxy replacement enabled and inherits Helm's
`policy-deny-response: none` and `service-no-backend-response: reject`.
The installed-policy check verifies both settings. The deny path drops
without a reply by default. The IPv4 Service no-backend rejection sends an
ICMP port-unreachable response.
[Cilium pin](../../kubernetes/main/apps/kube-system/cilium/app/ocirepository.yaml#L9-L14) ·
[Ops Helm values](../../kubernetes/main/apps/kube-system/cilium/app/helm-values.yaml#L28-L40) ·
[Helm defaults](https://github.com/cilium/cilium/blob/v1.20.5/install/kubernetes/cilium/values.yaml#L3179-L3188) ·
[ConfigMap mapping](https://github.com/cilium/cilium/blob/v1.20.5/install/kubernetes/cilium/templates/cilium-configmap.yaml#L535-L541) ·
[IPv4 policy-deny path](https://github.com/cilium/cilium/blob/v1.20.5/bpf/bpf_lxc.c#L1528-L1540) ·
[IPv4 no-backend path](https://github.com/cilium/cilium/blob/v1.20.5/bpf/bpf_lxc.c#L228-L243) ·
[ICMP response](https://github.com/cilium/cilium/blob/v1.20.5/bpf/lib/lb.h#L2456-L2480)

After the startup proof passes, the launcher reads the token and waits for
the lane grant using a claim payload that fails validation. The same proof
runs about once a minute, including during the grant wait. A failed recheck
stops the worker and its child processes. The cluster read of installed
policies is authoritative for the contents of the allowlist, including whether
it admits a third destination. The inside proof is authoritative for
enforcement at the sampled endpoints. A Ready DNS answer reflects Kubernetes
readiness as observed by CoreDNS, with the normal DNS and readiness update
delay. The render pod's independent positive checks bracket activation.

### Preflight and merge admission

Use Bash with GNU coreutils, Git, jq, yq, Python 3.12 with PyYAML, kubectl and
Flux CLI 2.10. Each block launches its own process and works from a session pod
without a LAN route. Use a clean checkout of the reviewed PR head incorporating
current Ops main. Set `STUDIO_OP970_RECEIPT` and `STUDIO_OP970_BASELINE` to new
paths in a private directory outside the checkout.

```bash
bash scripts/studio-release/op970.sh preflight &&
  bash scripts/studio-release/op970.sh admit-merge
```

Preflight requires the fourth pin and part A at the same reviewed inputs,
including the part A policy's Ready, owner, inventory and retention read-back
with zero matching pods. The policy projection applies the candidate API
ingress change to the installed Kubernetes and Cilium policy inputs and checks
both the prospective matte pod and API pod. The existing API peers and ports
must match. The retained allocation must have its pinned profile hash and
model file, the renderer and queues must be idle, and neither a matte grant
nor a matte pod may exist for the first activation. Baseline hashes cover
archived runs, deliveries and media indexes, media job rows, and the store's
file contents, sizes and modification times.

Under provisional CR-63, merging the reviewed PR is the only action that
changes the cluster. The admission verifies the checkout, current main,
receipt checks and baseline digest. Merge within 60 minutes of starting
preflight. Take fresh evidence when the PR head changes or the receipt expires.

### Wait for Flux and audit the rollout

Check out the exact Ops merge and set `MERGED_OPS_REVISION` to its full SHA.
Keep the same private baseline path. Flux picks up Git automatically. The wait
checks applied revisions, live stage specs and Ready generations against the
selected merge. A descendant is allowed if its Studio inputs still match.

```bash
bash scripts/studio-release/op970.sh wait
```

```bash
bash scripts/studio-release/op970.sh postflight
```

Postflight checks both API endpoints, the pinned Ready matte pod and profile
readiness, the completed registration Job and dedicated matte grant, and the
running matte lane worker. It reads the installed policy and actual pod and
Cilium endpoint identities for the worker and the API. It requires a successful
startup isolation line newer than the worker container's running start time
and a fresh successful recheck from that container. For each log read and each
subsequent check, postflight compares the same pod UID, worker container ID
and running start time on both sides of the read. A restart during the pass
fails it. It targets the selected pod by name for worker and listener reads.
It reads the forbidden destinations from the unrestricted pod again and checks
the render limit at 17 CPUs. It compares the idle matte queue, archive and store
snapshot with the baseline. The final listener probes send uncredentialed,
wrong-lane or invalid requests and require refusals before claim or release SQL.
Record the selected merge, preflight and audit output, pod UID, worker
container ID and running start time, both launcher log lines and any retries.
Keep the hashes and logs private.

### Reviewed rollback and retry

Keep Studio submissions and the offline renderer idle. A rollback preflight
requires idle work. It accepts a successful render pod list that verifies no
container is running, including an absent, Pending or crash-looped renderer
after a failed turnover. It applies the normal idle check when a render
container is running and fails on an inconclusive pod read. If there are
queued matte jobs, the snapshot permits them only when the current matte
pause is recorded. Running or held work fails the
gate. Retain the pause and the queued rows through the rollback audit.

Open a reviewed revert PR that restores the following Git paths to their part A
state at `ed212ad0052722abdd1a9590f8f064420c7aa8b7`:

- `kubernetes/main/apps/ai/studio/ks.yaml` removes the matte worker and matte
  registration stages.
- `kubernetes/main/apps/ai/studio/matte-worker-registration/`,
  `matting-pending/activation/`, and `matting-pending/release/` (under the Studio
  directory) remove the matte Job and worker and restore the dormant overlay and
  its existing pin.
- `kubernetes/main/apps/ai/studio/media-worker-registration/kustomization.yaml`
  and `matte-credential.yaml` (in the same directory) remove the matte
  credential declarations. Their prune exclusions preserve the generated
  token for a retry, and the database grant stays with it.
- `kubernetes/main/apps/ai/studio/app/{kustomization.yaml,networkpolicy.yaml,media-worker-routes.conf,media-worker-routes-op964.yaml}`
  restores the five-route listener and its original peers. The extended
  ConfigMap's live prune protection keeps it available to outgoing pods.
- `kubernetes/main/apps/ai/studio-render/app/helmrelease.yaml` restores 20 CPUs.
  Restore `studio-render/matting-pending/kustomization.yaml` and
  `studio/segmentation-pending/activation/render/sidecar.yaml` under
  `kubernetes/main/apps/ai/` to restore the dormant CPU projections.

The rollback retains the ops scripts, tests and this runbook. Keep the
`studio-matte-policy` stage, its prune-protected policy, migration
`0013_matte_jobs`, and every matte job row and pause event. This revert applies
no migration or credential rotation. Keep the renderer idle through the
turnover and until all matte pods are gone.

Use a new private receipt and baseline for the reviewed revert head:

```bash
bash scripts/studio-release/op970.sh rollback-preflight &&
  bash scripts/studio-release/op970.sh admit-merge rollback
```

After the reviewed revert merges, set `MERGED_OPS_REVISION` to its full SHA and
check out that merge. With the rollback baseline, wait for Flux and audit policy
retention, no remaining matte pods or registration Job, the 20-CPU renderer,
and preserved schema, rows, archives and store.

```bash
bash scripts/studio-release/op970.sh wait
```

```bash
bash scripts/studio-release/op970.sh compare
```

To retry after that audit, open a reviewed PR reapplying the five path groups
above from the activation merge, with the reviewed fix. Reuse the credential,
allocation and profile. The recreated registration Job accepts registration or
reuse of the same grant. Run the retry preflight on their clean candidate head
with fresh private evidence paths, with idle queues, then use the same wait and
postflight after merging the reviewed retry.

```bash
bash scripts/studio-release/op970.sh retry-preflight &&
  bash scripts/studio-release/op970.sh admit-merge retry
```

### Retire listener ConfigMap protection (Expected)

The generator's prune exclusion protects the current listener ConfigMap when
rollback replaces it. It also applies to future hashes while the option remains
in Git. After postflight and once the coordinator closes this rollback window,
remove the generator's `options.annotations` entry from
`studio/app/kustomization.yaml` in a reviewed PR before changing the listener
routes again. In the same PR remove the prune annotation from
`studio/app/media-worker-routes-op964.yaml` while keeping that file and its
resource entry. Update the release guard's two listener retention expectations
and keep its route and mount checks. Wait for Flux to clear both live
annotations.

When no API pod mounts the five-route ConfigMap, remove
`studio/app/media-worker-routes-op964.yaml` and its resource entry in a second
reviewed PR. Flux can then prune that ConfigMap and resume normal pruning of
future generated listener hashes. Both changes use reviewed GitOps PRs.
