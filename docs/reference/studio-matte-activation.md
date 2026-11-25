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

## Read back the installation (Expected)

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
