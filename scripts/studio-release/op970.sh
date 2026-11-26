#!/usr/bin/env bash
# Read-only cluster checks from the reviewed or merged Ops checkout.
source scripts/studio-release/op967.sh --helpers || exit 1
operation=OP970
unset ROLLBACK_OPS_REVISION
receipt=${STUDIO_OP970_RECEIPT:-}
baseline=${STUDIO_OP970_BASELINE:-}
policy_merge=ed212ad0052722abdd1a9590f8f064420c7aa8b7
matting_pin=$root/matting-pending/release/release.yaml
selector=app.kubernetes.io/name=studio-matte-worker

policy_snapshot() {
  local stage policy pods
  stage=$(kubectl -n ai get kustomization studio-matte-policy -o json) || return 1
  policy=$(kubectl -n ai get networkpolicy studio-matte-worker -o json) || return 1
  pods=$(kubectl get pods -A -l "$selector" -o json) || return 1
  printf '%s\n' "$stage" "$policy" "$pods" | jq -s . |
    python3 -B scripts/tests/studio-release/check.py --audit-matte-policy-snapshot "$1"
}

installed_policies() {
  local namespaced clusterwide cilium namespace endpoints api_pods matte_pods
  namespaced=$(kubectl get networkpolicies.networking.k8s.io,ciliumnetworkpolicies.cilium.io -A -o json) || return 1
  clusterwide=$(kubectl get ciliumclusterwidenetworkpolicies.cilium.io -o json) || return 1
  cilium=$(kubectl -n kube-system get configmap cilium-config -o json) || return 1
  if [[ $1 == preflight ]]; then
    namespace=$(kubectl get namespace ai -o json) || return 1
    printf '%s\n' "$namespaced" "$clusterwide" "$cilium" "$namespace"
  else
    endpoints=$(kubectl -n ai get ciliumendpoints.cilium.io -o json) || return 1
    api_pods=$(kubectl -n ai get pods -l app.kubernetes.io/name=studio-api -o json) || return 1
    matte_pods=$(kubectl -n ai get pods -l "$selector" -o json) || return 1
    printf '%s\n' "$namespaced" "$clusterwide" "$cilium" "$endpoints" "$api_pods" "$matte_pods"
  fi | jq -s . | python3 -B scripts/tests/studio-release/check.py --installed-policies "$1"
}

allocation_present() {
  local allocation profile image
  allocation=$(yq -er '.data.allocation' "$matting_pin") || return 1
  profile=$(yq -er '.data.profileSha256' "$matting_pin") || return 1
  image=$(yq -er '.data.image' "$matting_pin") || return 1
  [[ $allocation == matting-op459-birefnet-lite-v1-03 && $profile =~ ^[0-9a-f]{64}$ ]] || return 1
  kubectl -n ai exec deploy/studio-render -c app -- python -I -B -c '
from hashlib import sha256
from pathlib import Path
import json
import sys

allocation, profile, image = sys.argv[1:]
weights = Path("/studio/jobs") / allocation / "weights"
manifest = (weights / "profile.json").read_bytes()
assert sha256(manifest).hexdigest() == profile == (weights / "profile.sha256").read_text().strip()
assert json.loads(manifest)["image_digest"] == image.split("@", 1)[1]
assert (weights / "model.onnx").stat().st_size == 224005088
print(allocation, profile)
' "$allocation" "$profile" "$image"
}

control_connections() {
  kubectl -n ai exec deploy/studio-render -c app -- python -I -B -c '
import socket
for target in (("comfyui-0.comfyui.ai.svc.cluster.local.", 8188),
               ("loki-headless.observability.svc.cluster.local.", 3100)):
    with socket.create_connection(target, timeout=2):
        print(target[0], target[1], "reachable from unrestricted render pod")
'
}

matte_identity() {
  local pods
  pods=$(kubectl -n ai get pods -l "$selector" -o json) || return 1
  jq -er '
    def present: if type == "string" and length > 0 then . else error("incomplete matte identity") end;
    .items | select(length == 1) | .[0] | select(.metadata.deletionTimestamp == null) |
    [.status.containerStatuses[] | select(.name == "worker")] as $workers |
    select(($workers | length) == 1) |
    [(.metadata.name | present), (.metadata.uid | present),
     ($workers[0].containerID | present), ($workers[0].state.running.startedAt | present)] | @tsv
  ' <<< "$pods"
}

select_matte_container() {
  matte_binding=$(matte_identity) || return 1
  IFS=$'\t' read -r matte_pod matte_uid matte_container matte_started <<< "$matte_binding" || return 1
  printf 'Matte pod %s UID %s, worker container %s started %s\n' \
    "$matte_pod" "$matte_uid" "$matte_container" "$matte_started"
}

same_matte_container() {
  local current
  if ! current=$(matte_identity) || [[ $current != "$matte_binding" ]]; then
    printf 'FAIL matte pod or worker container identity changed during postflight\n' >&2
    return 1
  fi
}

bound_matte_check() {
  same_matte_container && "$@" && same_matte_container
}

proof_logs() {
  local started=$matte_started logs deadline status
  deadline=$((SECONDS + 90))
  while true; do
    same_matte_container || return 1
    logs=$(kubectl -n ai logs "$matte_pod" -c worker --timestamps --since-time="$started") || return 1
    same_matte_container || return 1
    status=0
    python3 -B scripts/studio-release/op970-log.py "$started" <<< "$logs" || status=$?
    if ((status != 2)); then return "$status"; fi
    if ((SECONDS >= deadline)); then
      printf 'FAIL no matte isolation recheck within 90 seconds\n' >&2
      return 1
    fi
    sleep 5 || return 1
  done
}

matte_pods() {
  local image containers
  image=$(yq -er '.data.image' "$matting_pin") || return 1
  containers=$(jq -cn --arg worker "ghcr.io/thaynes43/haynes-studio@$api" --arg matting "$image" \
    '{worker:$worker, matting:$matting}') || return 1
  # The current container's fresh isolation receipts cover a successful restart.
  pods matte-worker "$containers" 1 talosw01 true
}

worker_started() {
  kubectl -n ai exec "$matte_pod" -c worker -- python -I -B -c '
from pathlib import Path
from haynes_studio.queue.progress import ProgressFile

children = Path("/proc/1/task/1/children").read_text().split()
assert len(children) == 1 and children[0].isdecimal()
args = Path("/proc", children[0], "cmdline").read_bytes().strip(b"\0").split(b"\0")
assert b"studio-worker" in [part.rsplit(b"/", 1)[-1] for part in args]
assert args[-2:] == [b"--lane", b"matte"]
assert ProgressFile("/run/studio/worker-progress").fresh(300)
'
}

matte_removed() {
  local jobs deployment
  deployment=$(kubectl -n ai get deploy/studio-matte-worker --ignore-not-found -o name) || return 1
  [[ -z $deployment ]] || return 1
  jobs=$(kubectl -n ai get jobs -l app.kubernetes.io/name=studio-media-worker-registration -o json) || return 1
  jq -e 'all(.items[]; .metadata.name | startswith("studio-register-matte-worker-") | not)' \
    <<< "$jobs" >/dev/null
}

rollback_render_idle() {
  local pods state
  pods=$(kubectl -n ai get pods -l app.kubernetes.io/name=studio-render -o json) || return 1
  state=$(jq -er '
    def render_state:
      .status as $status |
      [$status.containerStatuses[]? | select(.name == "app")] as $app |
      if ($status.phase | IN("Pending", "Running", "Succeeded", "Failed") | not) then
        error("unknown render pod phase")
      elif any(($status.containerStatuses, $status.initContainerStatuses, $status.ephemeralContainerStatuses)[]?;
          .state.running != null) then
        "running"
      elif $status.phase == "Running" and
          ($app | length != 1 or all(.[]; .state.waiting == null and .state.terminated == null)) then
        error("running render pod has no conclusive app container state")
      else
        "stopped"
      end;
    if (.items | type) != "array" then
      error("invalid render pod list")
    else
      [.items[] | render_state] | if any(.[]; . == "running") then "running" else "stopped" end
    end
  ' <<< "$pods") || return 1
  if [[ $state == running ]]; then
    render_idle retry
  else
    printf 'No running render container in the render pod list\n'
  fi
}

# The labels written by preflight and required by admission come from the same call.
receipt_check() {
  local label=$1
  shift
  if [[ $phase == admit-merge ]]; then
    required+=("$label")
  else
    check "$label" "$@"
  fi
}

collect_preflight() {
  receipt_check 'reviewed checkout based on current Ops main' candidate_checkout
  if ((failed)); then return; fi
  if [[ $mode == matte-rollback ]]; then
    receipt_check 'rollback restores the part A Studio manifests' rollback_candidate
    receipt_check 'matte policy still installed' policy_snapshot active
  else
    receipt_check 'fourth pin and part A installed' flux_state "$policy_merge"
    receipt_check 'part A policy retained and no matte pods' policy_snapshot empty
    receipt_check 'API and worker policies projected from all installed inputs' installed_policies preflight
    receipt_check 'installed API endpoints on the fourth pin' api_endpoints
    receipt_check 'offline renderer at 20 CPUs' render_pin 20
    receipt_check 'retained matting allocation and profile present' allocation_present
    receipt_check 'both forbidden destinations reachable from the unrestricted pod' control_connections
    receipt_check 'Wan and Foley idle' wan_idle
    receipt_check 'ComfyUI idle' images_idle
  fi
  receipt_check 'fourth pin migration at 0013' migration
  if [[ $mode == matte-rollback ]]; then
    receipt_check 'offline render worker idle or verified stopped' rollback_render_idle
  else
    receipt_check 'offline render worker idle' render_idle retry
  fi
  if (( ! failed )); then
    receipt_check 'idle queues and baseline for archives, jobs and store' data_probe "$mode" "$revision" "$api"
  fi
}

candidate_checkout() {
  fetch_main && checkout && git merge-base --is-ancestor refs/remotes/origin/main "$head"
}

rollback_candidate() {
  git diff --quiet "$policy_merge" HEAD -- "$root/ks.yaml" "$root/matte-worker-registration" \
    "$root/matting-pending/activation" "$root/matting-pending/release" \
    "$root/media-worker-registration/kustomization.yaml" "$root/media-worker-registration/matte-credential.yaml" \
    "$root/app/kustomization.yaml" "$root/app/networkpolicy.yaml" \
    "$root/app/media-worker-routes.conf" "$root/app/media-worker-routes-op964.yaml" \
    "${root}-render/app/helmrelease.yaml" "${root}-render/matting-pending/kustomization.yaml" \
    "$root/segmentation-pending/activation/render/sidecar.yaml"
}

run_activation_check() {
  phase=${1:-}
  local mode=matte-before
  local required=() matte_binding matte_pod matte_uid matte_container matte_started
  case $phase in
    retry-preflight) mode=matte-retry ;;
    rollback-preflight) mode=matte-rollback ;;
    admit-merge)
      case ${2:-release} in
        release) ;;
        retry) mode=matte-retry ;;
        rollback) mode=matte-rollback ;;
        *) printf 'FAIL unknown merge mode\n' >&2; exit 2 ;;
      esac ;;
  esac

  case $phase in
    preflight|retry-preflight|rollback-preflight)
      check 'new private receipt and baseline paths supplied' receipt_paths
      if ((failed)); then finish; fi
      started=$(date -u +%s) || exit 1
      collect_preflight
      write_receipt "$mode" || exit 1
      printf 'Preflight receipt %s\n' "$receipt"
      ;;
    admit-merge)
      candidate_checkout && [[ -f $receipt && -n $baseline ]] || exit 1
      baseline=$(realpath -m -- "$baseline") || exit 1
      now=$(date -u +%s) || exit 1
      required=('new private receipt and baseline paths supplied')
      collect_preflight
      check 'passing receipt at this head within the merge window' verify_receipt "$mode" "$now" "${required[@]}"
      ;;
    wait|postflight|compare)
      check 'clean checkout of the selected Ops merge' selected_checkout
      if ((failed)); then finish; fi
      case $phase in
        wait)
          check 'automatic Flux pickup and Studio stages Ready at the selected merge' wait_for_flux
          ;;
        postflight|compare)
          check 'installed Studio stages match the selected merge' flux_state "$head"
          if ((failed)); then finish; fi
          check 'API endpoints Ready' api_endpoints
          check 'fourth pin migration at 0013' migration
          if [[ $phase == postflight ]]; then
            check 'single running matte worker container selected' select_matte_container
            if ((failed)); then finish; fi
            check 'retained policy selects the current worker' bound_matte_check policy_snapshot active
            check 'installed API and matte policy coverage' bound_matte_check installed_policies postflight
            check 'matte worker and matting sidecar each Ready on the pinned images' bound_matte_check matte_pods
            check 'both forbidden destinations still reachable from the unrestricted pod' bound_matte_check control_connections
            if (( ! failed )); then
              check 'fresh startup and recurring isolation proof' bound_matte_check proof_logs
            fi
            check 'matte registration Job completed' bound_matte_check registration matte-worker '[201,200]'
            check 'registered matte worker polling with a fresh progress marker' bound_matte_check worker_started
            check 'offline renderer at 17 CPUs' bound_matte_check render_pin 17
            check 'dedicated matte grant, idle queues, archives and store unchanged' bound_matte_check data_probe matte-after "$revision" "$api"
            if (( ! failed )); then
              check 'matte listener refuses invalid claims and releases' bound_matte_check kubectl -n ai exec -i "$matte_pod" \
                -c worker -- python -I -B - matte < "$script_dir/op967-routes.py"
            fi
          else
            check 'retained policy and no matching pods' policy_snapshot empty
            check 'matte Deployment and registration Job removed' matte_removed
            check 'offline renderer at 20 CPUs' render_pin 20
            check '0013, matte rows, archives and store unchanged' data_probe matte-removed "$revision" "$api"
          fi ;;
      esac ;;
    *)
      printf 'Usage: bash %s {preflight|retry-preflight|rollback-preflight|admit-merge [release|retry|rollback]|wait|postflight|compare}\n' "$0" >&2
      exit 2 ;;
  esac
  finish
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  run_activation_check "$@"
fi
