# Studio local-model hosting

[Story 442](https://work.haynesnetwork.com/work_packages/442) and
[profile CR-20](https://github.com/thaynes43/haynes-studio/blob/fa52968/docs/reference/local-model-profile.md)
select Qwen3.5-9B Q5_K_M and its BF16 projector on Card A. The image uses the
pinned llama.cpp b12679 CUDA 12 libraries and CPython 3.12.16. The loader reads
both local GGUFs in the child under the Wan lease. It unloads before every Wan
or Foley handover. The E10 benchmark and Tom's acceptance have separate gates.

## Part 2a dormant rollout

**Proven for part 2a.** The [main build](https://github.com/thaynes43/haynes-ops/actions/runs/45545041495)
passed its CPU smoke, published and signed the image, and attached BuildKit
provenance. The dormant rollout at `79fa01b5` recreated the Wan pod once.
See comment 9924 on [story 442](https://work.haynesnetwork.com/work_packages/442)
for the mounted code hashes and health reads. Flux updates
`wan-video-config`. The existing Reloader annotation recreates the single Wan
pod (Deployment strategy `Recreate`), including the app, Foley and API
containers and existing provisioning init containers. Those init containers
recheck their current pinned files and can fetch existing artifacts if they
are missing. The new local-model Job, Cilium policy, profile and container are
all under `local-model-pending/`, outside the applied Flux path. Local-model
discovery continues to return HTTP 503 `local_model_not_installed`.

Run these gates as the coordinator, one Wan change in flight. Quiesce Studio
queue dispatch and other direct clients. Check for no Wan or Foley job and no
render hold. Check both Comfy queue lists empty and confirm the expected UUIDs
and known idle ownership. Confirm the PVC is bound and the current init
containers are healthy. Collect the previous revision, pod UID and code hashes.

```bash
(
set -euo pipefail
wan_exec() { kubectl -n ai exec deploy/wan-video -c api -- "$@"; }
at_wan() {
  wan_exec python -c 'import sys, urllib.request
from urllib.error import HTTPError
try:
    response = urllib.request.urlopen("http://127.0.0.1:" + sys.argv[1], timeout=15)
except HTTPError as error:
    response = error
with response:
    print(response.status)
    print(response.read().decode())' "$1"
}

at_wan 8080/health    # 200, accepting_jobs=true, active_job=null
at_wan 8080/v1/ready # 200, active_video_job=null, active_foley_job=null
at_wan 8189/v1/ready # 200, active_job=null
at_wan 8188/queue    # queue_running=[], queue_pending=[]
wan_exec nvidia-smi --query-gpu=uuid,memory.used,utilization.gpu,temperature.gpu --format=csv
wan_exec nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory --format=csv
wan_exec sha256sum /config/video/api.py /config/video/product_local.py \
  /config/video/local_model_sidecar.py /config/video/openapi.json
)
```

Declare disruptive work with
`declare-activity start "Wan dormant admission update" --scope ai,talosw01,wan-video`.
After independent review and while the gates still pass, reconcile the approved
merge with `flux reconcile kustomization wan-video -n ai --with-source`. Wait
for the new pod UID and `kubectl -n ai rollout status deploy/wan-video --timeout=35m`.
Confirm the watched revision and new ConfigMap and mounted code hashes. Repeat
the prechecks plus `GET /ready`, `GET /v1/profiles` (I2V, S2V, Foley), a read
of a known legacy `/videos/<id>` and `GET /v1/local-model/profile` (503 and
`local_model_not_installed`).
Check for a single healthy pod and release the activity declaration once settled.
On failure keep dispatch quiesced, inspect the job/queue/hold and restore the
previous dormant source through a GitOps revert and the same targeted
reconciliation. Repeat the full health gate. Keep the PVC and existing ledgers.

The PR image smoke uses UID 1000 and a read-only filesystem with Docker network
disabled. It imports the Python module, resolves the C ABI against the exact
shared libraries, builds the plan and required-tool grammars from the bundled
Qwen3.5-4B Jinja template and accepts one synthetic `record_plan_v1` reply
while rejecting an unexpected function. Production builds its grammars and
parses replies with the Qwen3.5-9B GGUF's embedded template. The first live smoke
proves that template, driver compatibility, GPU allocation, visual tokenization
and model behavior. The Wan CPU suite covers the private protocol, request
limits, recovery and fake inference. CI also renders the staged manifests and
validates the projected profile with the production loader.

## Part 2b activation

**Expected.** Merge A, B and C in order, after each preceding health gate. A
installs the download policy. B starts a single CPU-only provisioning Job.
Both leave the running Wan pod and its PVC unchanged. C retires the two
temporary Flux Kustomizations and recreates the Wan pod with the sidecar and
calibrated profile. Flux can pick up a merge on its interval. Do each merge
with its prechecks complete and the lane quiesced, and reconcile to capture the
exact revision.

The PRs form a stack reviewed against `main`. After each squash merge rebase
the next PR so its diff contains only its own step. Save the original A and
B heads before updating branches. Use the original predecessor as the
`git rebase --onto origin/main <original-predecessor-head>` boundary on the
original next branch. Push with `--force-with-lease` to that branch and require
its checks at the rebased head. Repeat for C after B. At C verify the diff
removes both temporary references from `kubernetes/main/apps/ai/kustomization.yaml`.
A pre-B merge base would hide that retirement from the diff.

### Prepare evidence and verify the image

Run from the PR C checkout throughout, including before merging A and B. Use one shell and set `OP442_EVIDENCE` to the
printed directory for the commands below. Set `OP442_LEGACY_ID` to a known
completed legacy video ID and keep its read private. Keep Studio dispatch and
other direct producers quiesced from preflight through the smoke or rollback.
Confirm OP410 and the queue worker are ready and no Studio dispatch is active.

```bash
(
  set -euo pipefail
  umask 077
  mkdir -p "$HOME/.local/state/op442"
  mktemp -d "$HOME/.local/state/op442/rollout.XXXXXXXX"
)
```

Use installed cosign or the pinned Linux amd64 tool below. The registry checks
use an anonymous public pull token. They fetch the index, platform descriptors
and attestation only. The signature binds the index, which binds the platform
manifest and the provenance descriptor. Stop if any check fails.

```bash
(
  set -euo pipefail
  umask 077
  evidence=${OP442_EVIDENCE:?Set OP442_EVIDENCE to the private rollout directory}
  cosign_bin=$(command -v cosign || true)
  if [[ -z "$cosign_bin" ]]; then
    [[ $(uname -m) == x86_64 ]]
    mkdir -p "$HOME/.local/bin"
    cosign_bin="$HOME/.local/bin/cosign-op442-v3.1.4"
    checksum=424e2c97d0b45fd7a6b608a87f090f35633f4c23ece5f092d48bba11aaf4bbfa
    if [[ ! -x "$cosign_bin" ]]; then
      binary=$(mktemp "$HOME/.local/bin/.cosign-op442.XXXXXXXX")
      curl -fsSL -o "$binary" \
        https://github.com/sigstore/cosign/releases/download/v3.1.4/cosign-linux-amd64
      printf '%s  %s\n' "$checksum" "$binary" | sha256sum -c
      chmod 0700 "$binary"
      mv -n "$binary" "$cosign_bin"
    fi
    printf '%s  %s\n' "$checksum" "$cosign_bin" | sha256sum -c
  fi
  export DOCKER_CONFIG="$evidence/registry-anon"
  mkdir -p "$DOCKER_CONFIG"
  export GOMAXPROCS=2

  repo=thaynes43/studio-local-model
  revision=79fa01b5ff27b2b7aeacc78b079be7d99864d252
  tag="b12679-$revision-45545041495-1"
  digest=sha256:e2e9b5e9b71aee89d0b8077ae47be113587b61054957b10f694cb4ab92b93f03
  image="ghcr.io/$repo@$digest"
  "$cosign_bin" verify \
    --certificate-identity https://github.com/thaynes43/haynes-ops/.github/workflows/studio-local-model-build.yml@refs/heads/main \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com "$image" > "$evidence/signature.json"
  jq -e --arg digest "$digest" 'any(.[]; .critical.image["docker-manifest-digest"] == $digest)' "$evidence/signature.json"
  token=$(curl -fsS "https://ghcr.io/token?scope=repository:$repo:pull&service=ghcr.io" | jq -er .token)
  manifest() {
    curl -fsS -H "Authorization: Bearer $token" -H "Accept: $1" "https://ghcr.io/v2/$repo/manifests/$2"
  }
  check_digest() {
    printf '%s  %s\n' "${1#sha256:}" "$2" | sha256sum -c
  }
  for ref in "$digest" "$tag"; do
    manifest application/vnd.oci.image.index.v1+json "$ref" > "$evidence/index.json"
    check_digest "$digest" "$evidence/index.json"
  done
  platform=$(jq -er '.manifests[] | select(.platform.os == "linux" and .platform.architecture == "amd64") | .digest' "$evidence/index.json")
  attestation=$(jq -er --arg platform "$platform" '.manifests[] |
    select(.annotations["vnd.docker.reference.type"] == "attestation-manifest" and
           .annotations["vnd.docker.reference.digest"] == $platform) | .digest' "$evidence/index.json")
  manifest application/vnd.oci.image.manifest.v1+json "$attestation" > "$evidence/attestation.json"
  check_digest "$attestation" "$evidence/attestation.json"
  layer=$(jq -er '.layers[] | select(.annotations["in-toto.io/predicate-type"] == "https://slsa.dev/provenance/v0.2") | .digest' "$evidence/attestation.json")
  curl -fsSL -H "Authorization: Bearer $token" "https://ghcr.io/v2/$repo/blobs/$layer" > "$evidence/provenance.json"
  check_digest "$layer" "$evidence/provenance.json"
  jq -e --arg platform "${platform#sha256:}" --arg revision "$revision" '
    .predicateType == "https://slsa.dev/provenance/v0.2" and
    any(.subject[]; .digest.sha256 == $platform) and
    (.predicate.metadata["https://mobyproject.org/buildkit@v1#metadata"].vcs |
      .source == "https://github.com/thaynes43/haynes-ops" and .revision == $revision)
  ' "$evidence/provenance.json"
)
```

### A. Install only the download policy

Before merging A, run the dormant gate and save the Wan pod and PVC UIDs. Check
both idle GPU UUIDs, utilization and process ownership, the bound PVC and node
Ready. Record a known legacy read with the completed job's ID. After merging A
reconcile the parent and policy. Require the Cilium policy to match the
provisioner's label and its DNS and HTTPS allowlists, require no Job or Job
Kustomization, and check the same Wan pod is still Ready.

```bash
(
  set -euo pipefail
  umask 077
  evidence=${OP442_EVIDENCE:?}
  python3 -B scripts/wan-video/op442_smoke.py dormant > "$evidence/before-a.health.json"
  kubectl -n ai get pods -l app.kubernetes.io/name=wan-video -o json |
    jq -er '.items | select(length == 1) | .[0].metadata.uid' > "$evidence/dormant.uid"
  kubectl -n ai get pvc wan-video -o json |
    jq -er 'select(.status.phase == "Bound") | .metadata.uid' > "$evidence/pvc.uid"
  kubectl get node talosw01 -o json |
    jq -e '.status.nodeInfo.architecture == "amd64" and any(.status.conditions[]; .type == "Ready" and .status == "True")'
  kubectl -n ai exec deploy/wan-video -c api -- nvidia-smi --query-gpu=uuid,memory.used,utilization.gpu,temperature.gpu --format=csv
  kubectl -n ai exec deploy/wan-video -c api -- nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory --format=csv
  id=${OP442_LEGACY_ID:?}
  [[ $id =~ ^[0-9a-f]{32}$ ]]
  kubectl -n ai exec deploy/wan-video -c api -- python -B -c '
import json, sys
from urllib.request import urlopen
with urlopen("http://127.0.0.1:8080/videos/" + sys.argv[1], timeout=5) as response:
    job = json.load(response)
if job["id"] != sys.argv[1] or job["state"] != "succeeded":
    raise SystemExit("Expected a completed legacy video")
print(json.dumps({"id": job["id"], "state": job["state"]}))
' "$id" > "$evidence/legacy.json"
)
```

```bash
(
  set -euo pipefail
  umask 077
  evidence=${OP442_EVIDENCE:?}
  flux reconcile kustomization cluster-apps -n flux-system --with-source
  flux reconcile kustomization wan-local-model-download-policy -n ai
  flux get kustomizations -n ai wan-local-model-download-policy | tee "$evidence/a-flux.txt"
  kubectl -n ai get ciliumnetworkpolicy wan-local-model-provision -o json |
    jq -e '
      .spec.endpointSelector.matchLabels["app.kubernetes.io/name"] == "wan-local-model-provision" and
      (.spec.egress[0].toPorts[0].rules.dns ==
        [{"matchName":"huggingface.co"},{"matchPattern":"**.cdn.hf.co"}]) and
      (.spec.egress[1].toFQDNs ==
        [{"matchName":"huggingface.co"},{"matchPattern":"**.cdn.hf.co"}]) and
      (.spec.egress[1].toPorts[0].ports == [{"port":"443","protocol":"TCP"}])
    '
  job=$(kubectl -n ai get job wan-local-model-provision --ignore-not-found -o name)
  job_inventory=$(kubectl -n ai get kustomization wan-local-model-weights --ignore-not-found -o name)
  [[ -z "$job" && -z "$job_inventory" ]]
  uid=$(kubectl -n ai get pods -l app.kubernetes.io/name=wan-video -o json |
    jq -er '.items | select(length == 1) | .[0].metadata.uid')
  [[ $uid == "$(cat "$evidence/dormant.uid")" ]]
  python3 -B scripts/wan-video/op442_smoke.py dormant
)
```

**Rollback A.** Before B, merge a revert of A and reconcile `cluster-apps`.
Confirm the policy Kustomization and the Cilium policy disappear through Flux
inventory pruning, the Job remains absent, and the dormant gate and both UIDs
still pass. If B was already applied, retire B first.

### B. Provision and read back the weights

Before merging B, confirm A Ready at the correct revision and repeat the
dormant and storage checks. Reserve 7,499,546,400 bytes for the files, 1 MiB for
writes and metadata, and 8 GiB plus 1,024 inodes free. Budget 16 additional
inodes for staging. Create the PVC subdirectory as the serving UID. If the
pinned revision already exists, stop and review its provenance.

```bash
(
  set -euo pipefail
  kubectl -n ai exec deploy/wan-video -c api -- python -B -c '
import os
from pathlib import Path

if os.geteuid() != 1000:
    raise SystemExit("Expected Wan UID 1000")
workspace = Path("/workspace")
space = os.statvfs(workspace)
if space.f_bavail * space.f_frsize < 7499546400 + 1024**2 + 8 * 1024**3 or space.f_favail < 1040:
    raise SystemExit("Weights capacity gate failed")
root = workspace / "local-model-weights"
root.mkdir(mode=0o750, exist_ok=True)
if root.is_symlink() or not os.access(root, os.R_OK | os.W_OK | os.X_OK):
    raise SystemExit("Weights directory must be writable by UID 1000")
target = root / "qwen3.5-9b-q5_k_m" / "3885219b6810b007914f3a7950a8d1b469d598a5"
if os.path.lexists(target):
    raise SystemExit("Pinned revision already exists")
print({"free_bytes": space.f_bavail * space.f_frsize, "free_inodes": space.f_favail})
'
)
```

B gives Flux a Job health check. The Job runs on talosw01 with the published
Linux amd64 Python manifest, one attempt, a 30-minute CPU-only deadline and a
mount limited to `local-model-weights`. It starts at the pinned Hub revision
and records each redirect hostname. It follows only HTTPS redirects under
`**.cdn.hf.co`. The first host observed in the preflight metadata was
`us.aws.cdn.hf.co`. Byte caps, private staging, hashes and a create-only
rename govern publication. On `unexpected_download_redirect` preserve the
refused hostname and stop. A reviewed GitOps change must add only the
necessary verified hostname to both the Job's HTTPS redirect allowlist and
the Cilium DNS and FQDN rules, with the same file SHA-256s and caps. Require a
new attempt after reviewing space and the refused Job. Keep path, query and
signature values out of the evidence.

```bash
(
  set -euo pipefail
  umask 077
  evidence=${OP442_EVIDENCE:?}
  flux reconcile kustomization cluster-apps -n flux-system --with-source
  if ! flux --timeout=34m reconcile kustomization wan-local-model-weights -n ai; then
    kubectl -n ai logs job/wan-local-model-provision > "$evidence/b-provision.log" || true
    exit 1
  fi
  kubectl -n ai wait --for=condition=complete job/wan-local-model-provision --timeout=30s
  kubectl -n ai logs job/wan-local-model-provision > "$evidence/b-provision.log"
  provisioner=docker.io/library/python:3.12.16-slim-bookworm@sha256:1475c4f5b6bcb7d35e57661d6f5d7429ec4041275633c15f2b49350ac898276d
  kubectl -n ai get pods -l app.kubernetes.io/name=wan-local-model-provision -o json |
    jq -e --arg provisioner "$provisioner" '.items | select(length == 1) | .[0] |
      select(.status.phase == "Succeeded" and .spec.containers[0].image == $provisioner) |
      {uid: .metadata.uid, image: .spec.containers[0].image, imageID: .status.containerStatuses[0].imageID}' \
      > "$evidence/b-image.json"
  weights=/workspace/local-model-weights/qwen3.5-9b-q5_k_m/3885219b6810b007914f3a7950a8d1b469d598a5
  pins=kubernetes/main/apps/ai/wan-video/local-model-pending/weights/weights.json
  wan() { kubectl -n ai exec deploy/wan-video -c api -- "$@"; }
  wan cat "$weights/manifest.json" > "$evidence/b-manifest.json"
  diff -u <(jq -S . "$pins") <(jq -S . "$evidence/b-manifest.json")
  while IFS=$'\t' read -r name bytes hash; do
    [[ $(wan stat -c '%s' "$weights/$name") == "$bytes" ]]
    read -r actual _ < <(wan nice -n 19 sha256sum "$weights/$name")
    [[ $actual == "$hash" ]]
    printf '%s %s %s\n' "$name" "$bytes" "$actual"
  done < <(jq -r '.files[] | [.name,.bytes,.sha256] | @tsv' "$pins") | tee "$evidence/b-weights.txt"
  wan df -Pk /workspace | tee "$evidence/b-bytes.txt" |
    awk 'END { exit !(NR >= 2 && $4 >= 8 * 1024 * 1024) }'
  wan df -Pi /workspace | tee "$evidence/b-inodes.txt" |
    awk 'END { exit !(NR >= 2 && $4 >= 1024) }'
  uid=$(kubectl -n ai get pods -l app.kubernetes.io/name=wan-video -o json |
    jq -er '.items | select(length == 1) | .[0].metadata.uid')
  [[ $uid == "$(cat "$evidence/dormant.uid")" ]]
  python3 -B scripts/wan-video/op442_smoke.py dormant > "$evidence/b-health.json"
)
```

**Rollback B.** Keep producers paused. Capture the Job UID, terminal state and
logs. If the Job is still running, capture its current state and merge a
revert that removes the weights Kustomization from the `ai` inventory, then
reconcile `cluster-apps`. Flux prunes the Job and hashed ConfigMap. Check that
the Job and its pods are gone, the policy is still present, the PVC UID is the
same and the dormant health gate passes. Preserve any published revision and
account for the exact bytes and inodes of every `.quarantine-*` attempt.
Require fresh capacity evidence and a reviewed new Job attempt before retry.
On success leave the completed Job in desired state through the read-back and
calibration. C will prune the completed Job, ConfigMap and download policy.

### C. Calibrate, activate and check ready

Check the Job succeeded, the read-back and disk/inode reserves pass and Wan
is still dormant. Check node headroom for an 8 GiB request and 16 GiB limit,
including other workloads. Confirm known card ownership. With producers
paused, drain ordinary ComfyUI cache under the existing lease, then collect
Card A readings. Choose the highest stable drained reading rounded up as an
integer in 1–512 MiB. Edit the marked `unloaded_max_mib` line in
[the profile](../../kubernetes/main/apps/ai/wan-video/local-model-pending/activation/profile.yaml)
in PR C before merging. If no stable readings fit, keep Wan dormant.

```bash
(
  set -euo pipefail
  umask 077
  evidence=${OP442_EVIDENCE:?}
  kubectl -n ai exec -i deploy/wan-video -c api -- env PYTHONPATH=/config/video python -B - \
    > "$evidence/calibration.jsonl" <<'PY'
import json, time
from urllib.request import urlopen
import api

lease = api.acquire_compute()
if lease is None:
    raise SystemExit("Compute is reserved")
with lease:
    with urlopen("http://127.0.0.1:8080/health", timeout=5) as response:
        health = json.load(response)
    status, foley = api.foley("/v1/ready")
    if (not health["accepting_jobs"] or health["active_job"] is not None
            or status != 200 or foley["active_job"] is not None):
        raise SystemExit("Wan or Foley is busy/unavailable")
    if api.local.load_profile(api.CONFIG) is not None or api.local.state_path(api.ROOT).exists():
        raise SystemExit("Expected dormant Wan with no residency")
    if not api.gpus_idle(api.gpu_sample()):
        raise SystemExit("Rack GPUs are not idle")
    api.require_empty_queue()
    api.engine("/free", {"unload_models": True, "free_memory": True})
    for _ in range(15):
        api.require_empty_queue()
        sample = api.gpu_sample()
        if not api.gpus_idle(sample):
            raise SystemExit("Unexpected GPU activity during calibration")
        print(json.dumps(sample), flush=True)
        time.sleep(1)
PY
  jq -r '.gpus[] | select(.uuid == "GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a") | .memory_mib' "$evidence/calibration.jsonl"
)
```

C keeps the `ai/wan-video` Kustomization identity. Its rendered PVC stays the
same and retains `kustomize.toolkit.fluxcd.io/prune: disabled`. The two retired
Kustomizations own only the policy, Job and Job ConfigMap. Misplacing the
PVC in a retiring inventory could delete the claim and leave Wan Pending.
The activation initializer preserves the compute-lock inode. The sidecar
mounts both weights read-only and has no second device allocation.

After the calibrated PR checks pass, declare activity with
`agent-run declare-activity start "Wan local model activation" --scope ai,talosw01,wan-video`.
Merge C and reconcile the parent and existing Wan Kustomization. Each of the
four containers must be Ready on the new pod, and init containers must have
completed. The sidecar must answer its private listener probe before the Wan
gate passes. Mounted profile discovery and the legacy `/health` can respond
while the sidecar is starting. An otherwise admissible legacy Wan, typed I2V,
S2V or Foley request then reaches the ledger as failed with
`local_model_unavailable` before engine work. Check the CPU outage test
`test_mounted_outage_refuses_without_latching_even_with_engine_cache` in the PR
results and the live Ready and listener gates below. Keep producers paused
for any unavailable sidecar. Use the activity ID to end the declaration once
settled.

```bash
(
  set -euo pipefail
  umask 077
  evidence=${OP442_EVIDENCE:?}
  flux reconcile kustomization cluster-apps -n flux-system --with-source
  flux --timeout=36m reconcile kustomization wan-video -n ai
  kubectl -n ai rollout status deploy/wan-video --timeout=35m
  image=ghcr.io/thaynes43/studio-local-model:b12679-79fa01b5ff27b2b7aeacc78b079be7d99864d252-45545041495-1@sha256:e2e9b5e9b71aee89d0b8077ae47be113587b61054957b10f694cb4ab92b93f03
  kubectl -n ai get pods -l app.kubernetes.io/name=wan-video -o json |
    jq -e --arg old "$(cat "$evidence/dormant.uid")" --arg image "$image" '
      .items | select(length == 1) | .[0] |
      select(.metadata.uid != $old and .spec.nodeName == "talosw01") |
      select(any(.spec.containers[]; .name == "local-model" and .image == $image)) |
      select(any(.status.initContainerStatuses[]; .name == "prepare-local-model")) |
      select(([.status.containerStatuses[].name] | sort) == ["api","app","foley","local-model"]) |
      select(all(.status.containerStatuses[]; .ready and .restartCount == 0)) |
      select(all(.status.initContainerStatuses[]; .state.terminated.exitCode == 0)) |
      {uid:.metadata.uid, containers:[.status.containerStatuses[] | {name,image,imageID,ready}]}
    ' > "$evidence/c-pod.json"
  [[ $(kubectl -n ai get pvc wan-video -o jsonpath='{.metadata.uid}') == "$(cat "$evidence/pvc.uid")" ]]
  for name in wan-local-model-weights wan-local-model-download-policy; do
    kubectl -n ai wait --for=delete "kustomization/$name" --timeout=120s
  done
  job=$(kubectl -n ai get job wan-local-model-provision --ignore-not-found -o name)
  policy=$(kubectl -n ai get ciliumnetworkpolicy wan-local-model-provision --ignore-not-found -o name)
  [[ -z "$job" && -z "$policy" ]]
  kubectl -n ai get configmaps -o name |
    awk '/^configmap\/wan-local-model-provision-/ { found=1 } END { exit found }'
  python3 -B scripts/wan-video/op442_smoke.py active > "$evidence/c-health.json"
  profile=kubernetes/main/apps/ai/wan-video/local-model-pending/activation/profile.yaml
  ceiling=$(awk -F '[: ,]+' '/"unloaded_max_mib"/ {print $3}' "$profile")
  [[ $ceiling =~ ^[0-9]+$ ]]
  jq -e --argjson ceiling "$ceiling" '.discovery.unloaded_max_mib == $ceiling' "$evidence/c-health.json"
  kubectl -n ai exec deploy/wan-video -c local-model -- printenv NVIDIA_VISIBLE_DEVICES CUDA_VISIBLE_DEVICES
  kubectl -n ai get deployment wan-video -o json |
    jq -e '[.spec.template.spec.containers[] | select(.resources.limits["nvidia.com/gpu"] != null) |
      {name, limit:.resources.limits["nvidia.com/gpu"]}] == [{"name":"app","limit":"2"}]'
)
```

**Rollback C.** Quiesce producers and check the Wan, Foley, queue and
sidecar listener gates. Restore sidecar readiness before attempting release
proof if it is unavailable. An unavailable sidecar still blocks Wan and
Foley admission while the profile is mounted. Preserve any hold and resolve
unknown work with the Wan recovery procedure. Once ownership is clear the
rollback command below submits a tracked unload if the child is resident,
validates its release receipt and proves no child, reservation or pending
operation remains under the lease. It drains cache only after proving
residency is clear. Stop on a failed proof and preserve the journal.

```bash
(
  set -euo pipefail
  umask 077
  python3 -B scripts/wan-video/op442_smoke.py rollback --evidence "${OP442_EVIDENCE:?}"
)
```

After proof, merge a GitOps change restoring `wan-video/ks.yaml` to the dormant
app path and the original 5-minute timeout without the activation health
checks. Keep the finished provisioning inventories retired. Reconcile
`cluster-apps` and `wan-video`, check one new three-container pod, the same
PVC UID, the dormant health gate, no model container or profile and
`local_model_not_installed`. The persistent weights and evidence remain
available. End the activity declaration after the dormant lane is settled.

## One private smoke window

**Expected.** Use the active health gate, known idle ownership and the same
producer pause and evidence directory. The smoke script stages only synthetic
inputs generated by [smoke-inputs.py](../../scripts/studio-local-model/smoke-inputs.py).
It uses loopback via pods/exec in `ai`. The 30-minute clock starts with the
first leased load after input staging. Each admitted operation uses the same
compute lease. Watch GPU ownership, utilization and temperature, and keep
the terminal open through cleanup.

```bash
(
  set -euo pipefail
  umask 077
  evidence=${OP442_EVIDENCE:?}
  python3 -B scripts/wan-video/op442_smoke.py smoke --evidence "$evidence"
)
```

| Step | Wall-time target and gate |
| --- | --- |
| Five release-drift cycles | Plan 5 × (60 s load + ~8 s unload/receipt). The first load stops at 120 s and may hit a warm PVC cache. Every unload must return a verified release, with memory at or below the ceiling and a signed post-exit-minus-baseline difference of at most 4 MiB. |
| Load for text, vision and handover | Plan 60 s. One `plan_v1` text answer proves both bounded text and schema output (256 tokens, target ~20 s). One `frame_check_v1` has four ordered synthetic frames with known face, code and wardrobe differences (512 tokens, target ~35 s). |
| Handover | Start only by 7m 50s elapsed with at least 1,280 s for S2V (1,200 s render plus admission and read-back) and 50 s reserved for idle proof and cleanup. Verify the internal unload receipt before the engine's start time and an empty engine queue at completion. |
| Return load and final verification | If at least 185 s remain, time the return load with the same 60 s target and verify tracked unload. Reserve 50 s for cleanup. If short on time after S2V, skip the return load first and preserve S2V's verified unload plus final idle proof. If the S2V cutoff is missed, skip S2V and unload the current child. A skipped step leaves the smoke incomplete. |

Every drift record saves the load baseline, the post-exit sample and the
difference. A failed receipt may omit its sample. The script records a
fresh diagnostic sample if the child exited and the lane can be read under
the lease. A failed cycle, a difference greater than 4 MiB, or a reading above
the calibrated ceiling stops handover. Leave the lane as the code left it,
preserve its journal or hold and record the request IDs and samples. Raise
the drift allowance only by a reviewed change with evidence. The proof still
uses the absolute profile ceiling.

Keep sampler files and receipts private. Check that both GPUs' samples cover
the full smoke, record Card A VRAM peak against 12,288 MiB, sidecar cgroup memory
peak against 16 GiB, and S2V ownership using the per-UUID process data and
ledger prompt ID. Record cold and warm wall times. A sample is an observation
of peak usage at its sampling interval. Keep the sidecar's logs privately for
allocation diagnosis. The script verifies schema outputs and token bounds,
keeps the source result IDs, and stops on ambiguity. After a transport failure
it reads the original ID and repeats the exact saved PUT at most once, after
a definitive absent response. If a polling deadline arrives with work still
running, cleanup reads the same ID until it settles or the cleanup cutoff.
It then verifies release or submits the tracked unload if needed. An unresolved
original ID at the cutoff takes the Wan recovery path. Avoid new work while queue or ownership is
unclear.

## Stops and evidence

Stop on an access denial, hold, unknown ownership, malformed queue, bad image
or weight digest, reserve failure, absent listener, failed release, caps or
deadline. Keep dispatch quiesced through verified cleanup or recovery. Restore
dispatch once the sidecar is Ready, the model and journal are cleared, the
engine queue is empty and the rack checks pass. Keep failed attempts and
published weights under their original paths. A fallback needs a new ruling
and window.

Publish only IDs, states, hashes, measured times and memory samples to the
tracker. Keep service logs, full model outputs, prompts, staged media and
engine ledgers in the private evidence directory. Record source, built image,
tested checks, installation, usable smoke and Tom's acceptance as separate
facts. The E10 model quality verdict comes from its own benchmark.
