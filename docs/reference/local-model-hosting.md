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
The Bash verification blocks run in their own shell. Each gate reports its
result and a failed check stops the block, after collecting failure evidence
where the block calls for it. Export the `OP442_*` inputs before running a block.

```bash
bash -s <<'BASH'
set -euo pipefail
source scripts/wan-video/op442_gate.sh
wan_exec() { kubectl -n ai exec deploy/wan-video -c api -- "$@"; }
at_wan() {
  wan_exec python -c 'import sys, urllib.request
with urllib.request.urlopen("http://127.0.0.1:" + sys.argv[1], timeout=15) as response:
    if response.status != 200:
        raise SystemExit("Expected HTTP 200")
    print(response.read().decode())' "$1"
}

gate='Wan health and idle state'
at_wan 8080/health | jq -e '.accepting_jobs == true and has("active_job") and .active_job == null'
gate_passed
gate='Wan product readiness and idle state'
at_wan 8080/v1/ready | jq -e '.ready == true and has("active_video_job") and has("active_foley_job") and
  .active_video_job == null and .active_foley_job == null'
gate_passed
gate='Foley readiness and idle state'
at_wan 8189/v1/ready | jq -e 'has("active_job") and .active_job == null'
gate_passed
gate='Empty ComfyUI queue'
at_wan 8188/queue | jq -e '.queue_running == [] and .queue_pending == []'
gate_passed
gate='Read rack GPU samples for operator review'
wan_exec nvidia-smi --query-gpu=uuid,memory.used,utilization.gpu,temperature.gpu --format=csv
wan_exec nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory --format=csv
gate_passed
gate='Read mounted code hashes'
wan_exec sha256sum /config/video/api.py /config/video/product_local.py \
  /config/video/local_model_sidecar.py /config/video/openapi.json
gate_passed
BASH
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

**Expected for the startup repair.** Its merge updates the same dormant
ConfigMap and Reloader recreates Wan. Run the prechecks and post-rollout gate
above, including one Ready Service endpoint on the new pod and the unchanged
PVC UID. Keep Studio and direct producers paused across that window. The main
workflow publishes a new signed digest. Leave the activation path staged.

## Part 2b activation

**Expected.** Merge A, B and C in order, after each preceding health gate. A
installs the download policy. B starts a single CPU-only provisioning Job.
Both leave the running Wan pod and its PVC unchanged. C retires the two
temporary Flux Kustomizations and recreates the Wan pod with the sidecar and
calibrated profile. Flux can pick up a merge on its interval. Do each merge
with its prechecks complete and the lane quiesced, and reconcile to capture the
exact revision.

A and B completed during the first attempt and their Flux references are retired.
Keep their weights and read-back evidence. For the next C, start from current
main and verify both temporary references stay absent from
`kubernetes/main/apps/ai/kustomization.yaml`. Require checks on the exact
activation and calibration head.

### Prepare evidence and verify the image

Run from the PR C checkout throughout. Export `OP442_EVIDENCE` with the printed
directory for the commands below. Export `OP442_LEGACY_ID` with a known
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
manifest and the provenance descriptor. In the activation patch, replace
`pending-op442-startup-proof` with the `tag@sha256:...` from the signed main
build. That image line also supplies the diagnostic Job and deployment health
gate. The check below requires the latest image source revision on main and
refuses the first activation's digest. Stop if any check fails.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  umask 077
  evidence=${OP442_EVIDENCE:?Set OP442_EVIDENCE to the private rollout directory}
  rm -f "$evidence/verified-image"
  base=kubernetes/main/apps/ai/wan-video
  gate='Image pin matches the current startup source'
  git fetch origin main
  image_inputs=(scripts/studio-local-model "$base/app"
    "$base/local-model-pending/weights/weights.json" .github/workflows/studio-local-model-build.yml)
  revision=$(git log -1 --format=%H FETCH_HEAD -- "${image_inputs[@]}")
  git grep -Fq 'STARTUP_LOG = "local-model-listening"' "$revision" -- "$base/app/local_model_sidecar.py"
  git diff --quiet "$revision" -- "${image_inputs[@]}"
  pin=$(awk '$1 == "tag:" {print $2}' "$base/local-model-pending/activation/sidecar.yaml")
  [[ $pin =~ ^b12679-([0-9a-f]{40})-[0-9]+-[0-9]+@sha256:[0-9a-f]{64}$ ]]
  [[ ${BASH_REMATCH[1]} == "$revision" ]]
  tag=${pin%@*}
  digest=${pin#*@}
  [[ $digest != sha256:e2e9b5e9b71aee89d0b8077ae47be113587b61054957b10f694cb4ab92b93f03 ]]
  gate_passed
  gate='Cosign available'
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
  gate_passed
  export DOCKER_CONFIG="$evidence/registry-anon"
  mkdir -p "$DOCKER_CONFIG"
  export GOMAXPROCS=2

  repo=thaynes43/studio-local-model
  image="ghcr.io/$repo@$digest"
  gate='Image signature and workflow identity'
  "$cosign_bin" verify \
    --certificate-identity https://github.com/thaynes43/haynes-ops/.github/workflows/studio-local-model-build.yml@refs/heads/main \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com "$image" > "$evidence/signature.json"
  jq -e --arg digest "$digest" 'any(.[]; .critical.image["docker-manifest-digest"] == $digest)' "$evidence/signature.json"
  gate_passed
  gate='Tag, signed digest and platform descriptor'
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
  gate_passed
  gate='Attestation digest, image provenance and source revision'
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
  gate_passed
  printf '%s\n' "ghcr.io/$repo:$pin" > "$evidence/verified-image"
BASH
```

### A. Install only the download policy

Before merging A, run the dormant gate and save the Wan pod and PVC UIDs. Check
both idle GPU UUIDs, utilization and process ownership, the bound PVC and node
Ready. Record a known legacy read with the completed job's ID. After merging A
reconcile the parent and policy. Require the Cilium policy to match the
provisioner's label and its DNS and HTTPS allowlists, require no Job or Job
Kustomization, and check the same Wan pod is still Ready.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  umask 077
  evidence=${OP442_EVIDENCE:?}
  gate='Dormant Wan, Foley and engine health'
  python3 -B scripts/wan-video/op442_smoke.py dormant > "$evidence/before-a.health.json"
  gate_passed
  gate='Record Wan pod UID'
  kubectl -n ai get pods -l app.kubernetes.io/name=wan-video -o json |
    jq -er '.items | select(length == 1) | .[0].metadata.uid' > "$evidence/dormant.uid"
  gate_passed
  gate='Bound PVC and UID'
  kubectl -n ai get pvc wan-video -o json |
    jq -er 'select(.status.phase == "Bound") | .metadata.uid' > "$evidence/pvc.uid"
  gate_passed
  gate='Render node Ready on amd64'
  kubectl get node talosw01 -o json |
    jq -e '.status.nodeInfo.architecture == "amd64" and any(.status.conditions[]; .type == "Ready" and .status == "True")'
  gate_passed
  gate='Read rack GPU samples for operator review'
  kubectl -n ai exec deploy/wan-video -c api -- nvidia-smi --query-gpu=uuid,memory.used,utilization.gpu,temperature.gpu --format=csv
  kubectl -n ai exec deploy/wan-video -c api -- nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory --format=csv
  gate_passed
  gate='Known completed legacy video read'
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
  gate_passed
BASH
```

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  umask 077
  evidence=${OP442_EVIDENCE:?}
  gate='Reconcile download policy manifests'
  flux reconcile kustomization cluster-apps -n flux-system --with-source
  flux reconcile kustomization wan-local-model-download-policy -n ai
  flux get kustomizations -n ai wan-local-model-download-policy | tee "$evidence/a-flux.txt"
  gate_passed
  gate='Provisioner policy label and allowed DNS and HTTPS'
  kubectl -n ai get ciliumnetworkpolicy wan-local-model-provision -o json |
    jq -e '
      .spec.endpointSelector.matchLabels["app.kubernetes.io/name"] == "wan-local-model-provision" and
      (.spec.egress[0].toPorts[0].rules.dns ==
        [{"matchName":"huggingface.co"},{"matchPattern":"**.cdn.hf.co"}]) and
      (.spec.egress[1].toFQDNs ==
        [{"matchName":"huggingface.co"},{"matchPattern":"**.cdn.hf.co"}]) and
      (.spec.egress[1].toPorts[0].ports == [{"port":"443","protocol":"TCP"}])
    '
  gate_passed
  gate='Provisioning Job and inventory are absent'
  job=$(kubectl -n ai get job wan-local-model-provision-r2 --ignore-not-found -o name)
  job_inventory=$(kubectl -n ai get kustomization wan-local-model-weights --ignore-not-found -o name)
  [[ -z "$job" && -z "$job_inventory" ]]
  gate_passed
  gate='Same Ready Wan pod UID'
  old=$(cat "$evidence/dormant.uid")
  uid=$(kubectl -n ai get pods -l app.kubernetes.io/name=wan-video -o json |
    jq -er '.items | select(length == 1) | .[0] |
      select(any(.status.conditions[]; .type == "Ready" and .status == "True")) | .metadata.uid')
  [[ -n "$old" && "$uid" == "$old" ]]
  gate_passed
  gate='Dormant health and discovery'
  python3 -B scripts/wan-video/op442_smoke.py dormant
  gate_passed
BASH
```

**Rollback A.** Before B, merge a revert of A and reconcile `cluster-apps`.
Confirm the policy Kustomization and the Cilium policy disappear through Flux
inventory pruning, the Job remains absent, and the dormant gate and both UIDs
still pass. If B was already applied, retire B first. If the dormant gate finds
a retained local-model journal from a later activation, use the
[hold-clearing step](#clear-a-local-model-hold) before declaring recovery.

### B. Provision and read back the weights

Before merging B, confirm A Ready at the correct revision and repeat the
dormant and storage checks. Reserve 7,499,546,400 bytes for the files, 1 MiB for
writes and metadata, and 8 GiB plus 1,024 inodes free. Budget 16 additional
inodes for staging. Create the PVC subdirectory as the serving UID. If the
pinned revision already exists, stop and review its provenance.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  gate='Weights capacity, directory permissions and unused revision'
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
  gate_passed
BASH
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

**Changed: the first attempt failed and was retried under a new name.** On
2026-11-25 at 05:16Z the original provisioning Job (memory limit 256Mi)
was killed for memory five seconds after it started (`OOMKilled`, exit 137). It
had logged the redirect to `us.aws.cdn.hf.co` and written 570,458,112 bytes of
the first file to
`/workspace/local-model-weights/qwen3.5-9b-q5_k_m/.quarantine-rgimzg5p/`.
Nothing was published and the Wan pod was not restarted. The downloader wrote
the file with a single `fsync` at the end, so its written pages stayed in the
container's memory account. The retry, `wan-local-model-provision-r2`, flushes
and drops the file's cached pages every 64 MiB and has a 1Gi limit. The
quarantined partial is accounted for as described below and stays until the
retry has published.

A failed or interrupted Job can leave one or more quarantined directories.
After the Job's pod has stopped, list the matching entries under
`/workspace/local-model-weights/qwen3.5-9b-q5_k_m/`. Record the Job ID,
failure code, exact attempt path, allocated bytes (`du -s --block-size=1`) and
entry count (`find -xdev -printf . | wc -c`). Recheck the byte and inode
reserves before any retry. Keep the attempt while it is needed as evidence.
For operator cleanup, select one exact `.quarantine-*` directory, resolve
and verify its parent and name, and confirm it is a directory and has no
active Job using it. As the owning UID, run `chmod u+w -- "$attempt"` if it
was sealed, then `rm -r -- "$attempt"` using the verified path. Check that
the path is gone and record the freed space. Leave the revision directory
and other attempts in place.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  umask 077
  evidence=${OP442_EVIDENCE:?}
  gate='Reconcile provisioning manifests'
  flux reconcile kustomization cluster-apps -n flux-system --with-source
  if ! flux --timeout=34m reconcile kustomization wan-local-model-weights -n ai; then
    kubectl -n ai logs job/wan-local-model-provision-r2 > "$evidence/b-provision.log" || true
    exit 1
  fi
  gate_passed
  gate='Provisioning Job complete and logs saved'
  kubectl -n ai wait --for=condition=complete job/wan-local-model-provision-r2 --timeout=30s
  kubectl -n ai logs job/wan-local-model-provision-r2 > "$evidence/b-provision.log"
  gate_passed
  gate='Successful pod ran the pinned provisioner'
  provisioner=docker.io/library/python:3.12.16-slim-bookworm@sha256:1475c4f5b6bcb7d35e57661d6f5d7429ec4041275633c15f2b49350ac898276d
  kubectl -n ai get pods -l batch.kubernetes.io/job-name=wan-local-model-provision-r2 -o json |
    jq -e --arg provisioner "$provisioner" '.items | select(length == 1) | .[0] |
      select(.status.phase == "Succeeded" and .spec.containers[0].image == $provisioner) |
      {uid: .metadata.uid, image: .spec.containers[0].image, imageID: .status.containerStatuses[0].imageID}' \
      > "$evidence/b-image.json"
  gate_passed
  gate='Provisioned manifest matches approved pins'
  weights=/workspace/local-model-weights/qwen3.5-9b-q5_k_m/3885219b6810b007914f3a7950a8d1b469d598a5
  pins=kubernetes/main/apps/ai/wan-video/local-model-pending/weights/weights.json
  wan() { kubectl -n ai exec deploy/wan-video -c api -- "$@"; }
  wan cat "$weights/manifest.json" > "$evidence/b-manifest.json"
  jq -S . "$pins" > "$evidence/b-pins.sorted.json"
  jq -S . "$evidence/b-manifest.json" > "$evidence/b-manifest.sorted.json"
  diff -u "$evidence/b-pins.sorted.json" "$evidence/b-manifest.sorted.json"
  files=$(jq -er '.files | select(length > 0) | .[] | [.name,.bytes,.sha256] | @tsv' "$pins")
  gate_passed
  while IFS=$'\t' read -r name bytes hash; do
    gate="Weight size and SHA-256 for $name"
    [[ $(wan stat -c '%s' "$weights/$name") == "$bytes" ]]
    checksum=$(wan nice -n 19 sha256sum "$weights/$name")
    read -r actual _ <<< "$checksum"
    [[ $actual == "$hash" ]]
    printf '%s %s %s\n' "$name" "$bytes" "$actual"
    gate_passed
  done <<< "$files" > "$evidence/b-weights.txt"
  gate='PVC free byte reserve'
  wan df -Pk /workspace | tee "$evidence/b-bytes.txt" |
    awk 'END { exit !(NR >= 2 && $4 >= 8 * 1024 * 1024) }'
  gate_passed
  gate='PVC free inode reserve'
  wan df -Pi /workspace | tee "$evidence/b-inodes.txt" |
    awk 'END { exit !(NR >= 2 && $4 >= 1024) }'
  gate_passed
  gate='Same Wan pod and PVC UIDs'
  old=$(cat "$evidence/dormant.uid")
  uid=$(kubectl -n ai get pods -l app.kubernetes.io/name=wan-video -o json |
    jq -er '.items | select(length == 1) | .[0].metadata.uid')
  [[ -n "$old" && "$uid" == "$old" ]]
  pvc_uid_matches "$evidence/pvc.uid"
  gate_passed
  gate='Dormant health and discovery'
  python3 -B scripts/wan-video/op442_smoke.py dormant > "$evidence/b-health.json"
  gate_passed
BASH
```

**Rollback B.** Keep producers paused. Capture the
`wan-local-model-provision-r2` Job UID, state and logs. Merge a revert that
removes the weights Kustomization from the `ai` inventory, then
reconcile `cluster-apps`. Flux prunes the Job and hashed ConfigMap. Check that
the Job and its pods are gone, the policy is still present, the PVC UID is the
same and the dormant health gate passes. Use the [hold-clearing step](#clear-a-local-model-hold)
for a retained local-model journal if it blocks that gate. Preserve any published revision and
account for the exact bytes and inodes of every `.quarantine-*` attempt.
Require fresh capacity evidence and a reviewed new Job attempt before retry.
On success leave the completed Job in desired state through the read-back and
calibration. C will prune the completed Job, ConfigMap and download policy.

### C. Calibrate, activate and check ready

**Changed: the first activation was reverted.** On 2026-11-25 the calibration
ran in the dormant pod (15 samples on the model's card, all 266 MiB; ceiling
270) and PR 5050 was merged at 06:15Z. The Wan pod was recreated with the
sidecar at 06:17Z. Its three existing containers became Ready; the
`local-model` container never did. It exited with status 1 immediately on
every observed start. The process raised
`Local-model sidecar must be pinned to its configured GPU UUID`, so it never
bound the private socket. The pod had no Ready Service endpoint. The
coordinator proved the queue and workers idle under the lease and found no
residency journal. PR 5056 (`0a521b75`) restored the dormant path. Helm waited
until the Deployment reached its 30-minute progress deadline. The Flux
Kustomization health check failed two seconds later on the failed HelmRelease
and the revert could reconcile. The weights and activation configuration were
retained.

**Proven on CPU.** Anonymous GHCR extraction of the failed digest selected
the Linux amd64 manifest `sha256:34336c097119df5ebecad0f5d5ba58a1bd0062a07a0c1ecdd89edba217706f1b`.
The three Python modules matched `79fa01b5` byte for byte. Its CPython
3.12.16 ran the real `serve` module with the staged profile. The weights
fixture used the pinned manifest and sparse regular files of the pinned sizes.
Startup checks that manifest, file type and size. The provisioning Job verifies
the actual file hashes. Neither the fixture nor the test loads a model.
With both process variables set to the profile UUID, `ready` passed and
residency reported no child. Setting only `NVIDIA_VISIBLE_DEVICES=all` or only
`CUDA_VISIBLE_DEVICES=0` reproduced the original Python traceback. The
archived ReplicaSet records the correct named pins, and the failed process
did not log its effective values. The specific live mismatch remains
unverified. The Talos NVIDIA runtime has been observed rewriting
`NVIDIA_VISIBLE_DEVICES` to `void` after mounting a pinned card. That could
explain the failure. The diagnostic below checks the process values and
driver-visible cards on talosw01. Foley starts without a startup equality
check. Its leased worker verifies Card A
through NVML and supplies the exact CUDA pin to its inference subprocess.
[Talos NVIDIA runtime observation](../../.agents/runbooks/egpu-test-node.md)
[ControlFoley worker source](../../scripts/controlfoley/service.py)

The NVIDIA toolkit warning `ERROR: init 250 result=11` did not occur in the
extracted image runtime. The archived logs show it in the same pod's healthy
ComfyUI container, immediately before normal startup output. The
talosw01 extension version `580.178.04-v1.19.1` contains
`libnvidia-sandboxutils.so.580.178.04`. Its `Management::init` failure
branch passes `init`, line 250 and the driver-initialization result to the
log formatter. NVIDIA defines result 11 as a missing file path. The exact
path was absent from the logs. The Python pin guard ran before loading a
model library or querying the GPU.
[NVIDIA status definitions](https://github.com/NVIDIA/nvidia-container-toolkit/blob/v1.19.1/internal/nvsandboxutils/nvsandboxutils.h)
[Talos extensions issue 940](https://github.com/siderolabs/extensions/issues/940)

**Proven on the node (2026-11-25, 08:40Z).** After PR 5060 the published
image `b12679-1840480c…-45576973328-1@sha256:33e76e0a…48e6` passed the
verification above, and the one-shot diagnostic
`wan-local-model-start-check-bcs5t` ran on talosw01 with Wan dormant and both
cards idle. Its log shows what the container sees: `NVIDIA_VISIBLE_DEVICES`
is `void` (the pod spec gives it the card's UUID), `CUDA_VISIBLE_DEVICES` is
the card's UUID, the driver shows exactly one card and it is the profile's,
the start-up check's verdict is `ready` with no reason code, the listener is
ready with no child, and the CUDA library loads. `ERROR: init 250 result=11`
is printed on this successful run too. **That establishes the cause of the
first failure:** the old guard compared `NVIDIA_VISIBLE_DEVICES` with the
card's UUID, and on this node the NVIDIA runtime replaces that variable with
`void` once it has mounted the card. One limit: the Job had no sibling
container holding a device-plugin allocation, as the sidecar has in the Wan
pod. If that changes what the sidecar sees, the result is a served refusal
with a reason, not an outage. A fresh calibration in the current dormant pod
at 08:41Z gave 15 readings of 266 MiB and the same ceiling, 270.
[OP442, comment 10007](https://work.haynesnetwork.com/work_packages/442)

**Expected: the second activation.** This revision moves the Wan
Kustomization to the activation overlay again. Neither the Kustomization nor
the overlay's HelmRelease waits on roll-out health, so the gates in
[Activate and verify the Service](#activate-and-verify-the-service) are the
proof, and a revert of this one line applies at once.

**Expected after the repair.** The sidecar binds its private socket after a
profile, pin or weights check fails. `ready` checks the listener and
`residency` reports that this instance never started a child. Local discovery
and new local requests return HTTP 503 `local_model_unavailable` with a
`reason_code`. The CUDA pin must be the profile UUID and the NVIDIA pin must be
that UUID or `void`. The same bounded NVML read used for card status queries
all visible device UUIDs under the existing compute lease. Exactly one must
match the profile. If the lease is occupied at startup, the listener reports
`gpu_pin_unproven` immediately. The first local request or operator probe
that passes the already-held lease completes the pin check. Discovery only
reads the current verdict. A terminal refusal requires a restart after
correcting the cause. With no older reservation and the lease and queue
guards satisfied, Wan and Foley can proceed. A retained reservation still
requires its normal release proof.
Use the diagnostic below before changing the Wan pod.

Check the Job succeeded, the read-back and disk/inode reserves pass and Wan
is still dormant. Check node headroom for an 8 GiB request and 16 GiB limit,
including other workloads.
Run the [Wan-pod change check](#wan-pod-change-check) before every subsequent
Wan-pod change. With producers paused, confirm known card ownership, drain
ordinary ComfyUI cache under the existing lease, and collect Card A readings
and its engine process baseline. Round the highest drained memory reading up
to the next integer if needed, add the 4 MiB release allowance, and cap the
ceiling at 512 MiB. Set the marked `unloaded_max_mib` line in
[the profile](../../kubernetes/main/apps/ai/wan-video/local-model-pending/activation/profile.yaml)
to the value printed below before merging PR C. Keep Wan dormant if the
readings are unstable or their rounded maximum exceeds 512 MiB. The cap can
leave less than 4 MiB of headroom. Save the unedited calibration JSON with
the approved profile for rollback.

Review every Card A PID and process name in `calibration.json` against the
engine ownership checks and record its attribution privately. Refuse rows
with unresolvable process names. The API container's
`nvidia-smi --query-compute-apps` can miss processes in other containers.
An empty process list proves nothing by itself. The release guard is the
idle card's memory against both the load baseline plus 4 MiB and the
calibrated ceiling.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  umask 077
  evidence=${OP442_EVIDENCE:?}
  gate='Leased idle proof, calibration and memory ceiling'
  kubectl -n ai exec -i deploy/wan-video -c api -- env PYTHONPATH=/config/video python -B - \
    > "$evidence/calibration.json" <<'PY'
import json, math, subprocess, time
from pathlib import Path
from urllib.request import urlopen
import api

card = "GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a"

def compute_apps():
    return subprocess.check_output(
        ["nvidia-smi", "--id=" + card, "--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory",
         "--format=csv,noheader,nounits"], text=True, timeout=10,
    )

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
    if api.local.load_profile(api.CONFIG) is not None:
        raise SystemExit("Expected dormant Wan")
    try:
        api.local.state_path(api.ROOT).lstat()
    except FileNotFoundError:
        pass
    else:
        raise SystemExit("Expected no local-model residency journal")
    if not api.gpus_idle(api.gpu_sample()):
        raise SystemExit("Rack GPUs are not idle")
    api.require_empty_queue()
    api.engine("/free", {"unload_models": True, "free_memory": True})
    processes = compute_apps()
    samples = []
    for _ in range(15):
        api.require_empty_queue()
        sample = api.gpu_sample()
        if not api.gpus_idle(sample):
            raise SystemExit("Unexpected GPU activity during calibration")
        sample_a = next(gpu for gpu in sample["gpus"] if gpu["uuid"] == card)
        sample_a["gpu_uuid"] = sample_a.pop("uuid")
        samples.append({"time": sample["time"], **sample_a})
        time.sleep(1)
    api.require_empty_queue()
    if compute_apps() != processes:
        raise SystemExit("Card A compute processes changed during calibration")
    rounded = math.ceil(max(sample["memory_mib"] for sample in samples))
    if rounded > api.local.MAX_UNLOADED_MIB:
        raise SystemExit("Rounded drained memory exceeds the 512 MiB cap")
    ceiling = min(api.local.MAX_UNLOADED_MIB, rounded + api.local.RELEASE_DRIFT_MIB)
    print(json.dumps({"gpu_uuid": card, "samples": samples, "compute_apps_csv": processes,
                      "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                      "unloaded_max_mib": ceiling}))
PY
  jq -r '.samples[].memory_mib' "$evidence/calibration.json"
  jq -r '.compute_apps_csv' "$evidence/calibration.json"
  jq -r '"Set unloaded_max_mib to \(.unloaded_max_mib) MiB"' "$evidence/calibration.json"
  gate_passed
BASH
```

C keeps the `ai/wan-video` Kustomization identity. Its rendered PVC stays the
same and retains `kustomize.toolkit.fluxcd.io/prune: disabled`. The two retired
Kustomizations own only the policy, Job and Job ConfigMap. Misplacing the
PVC in a retiring inventory could delete the claim and leave Wan Pending.
The activation initializer preserves the compute-lock inode. The sidecar
mounts both weights read-only and has no second device allocation.

Save the profile from the calibrated, checked PR head before merging. The
hold-clearing command needs that exact profile if the mount has been removed.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  umask 077
  evidence=${OP442_EVIDENCE:?}
  gate='Save the approved profile JSON'
  profile=kubernetes/main/apps/ai/wan-video/local-model-pending/activation/profile.yaml
  awk 'found { sub(/^    /, ""); print } /^  local-model.json: \|$/ { found=1 }' "$profile" |
    jq -e . > "$evidence/c-approved-profile.json"
  gate_passed
  # The dormant pod may have been recreated since A saved its UID. The
  # activation gates compare the new pod with this one.
  gate='Save the current dormant pod UID'
  kubectl -n ai get pods -l app.kubernetes.io/name=wan-video -o json |
    jq -er '.items | select(length == 1) | .[0] |
      select(([.status.containerStatuses[].name] | sort) == ["api","app","foley"]) | .metadata.uid' \
    > "$evidence/dormant.uid"
  gate_passed
BASH
```

#### Run the one-shot startup diagnostic

**Expected.** The coordinator decides whether to run the staged Job. Before
creating it, run the dormant Wan-pod change check with producers paused.
Confirm the queue and workers are empty, both GPU owners are known and idle,
and the existing compute-lock file is present. Keep dispatch paused through
the check. The Job uses the same container image, GPU environment, runtime
class and security context as the sidecar. The NVIDIA runtime can expose Card A
by UUID before the process acquires the lease, even without a device-plugin
allocation. That gives the exact image a compute-capable device. Under this
reviewed command, the wrapper writes the approved profile from a Job argument
to its private `emptyDir` and calls the same startup function as the
sidecar. Startup takes the original lease by inode for the read-only NVML
enumeration. The wrapper checks that the CUDA library can be mapped under
the same lease. It calls no CUDA initialization or model operation. The
private workspace has no dispatch lease path, and the Wan PVC is read-only.
Keep the overlay dormant if idle ownership or the nonblocking flock cannot
be proved.
An authorized equivalent is an isolated maintenance test on talosw01 with
Wan stopped and proven idle, using the same container spec. The CPU smoke
covers the real PID 1 listener and the `void` case off the NVIDIA runtime.
The CPU runner bind-mounts a stand-in for the UUID query and gives only its
disposable container a fixture PATH. The published image has no stand-in or
test switch.

Keep the Job and profile outside Flux. Use only the verified image from the
step above. One Job create carries the approved profile and gets a unique
attempt suffix. The 10-minute Job deadline includes scheduling and the
image pull. Poll for either terminal condition and save the Job, pod
termination state and logs for each attempt. Kubernetes cleans up the
finished Job after 24 hours. Repeat the dormant health gate after collecting
evidence. A failure keeps C on hold.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  umask 077
  evidence=${OP442_EVIDENCE:?}
  attempt=$(mktemp -d "$evidence/start-check.XXXXXXXX")
  base=kubernetes/main/apps/ai/wan-video/local-model-pending
  gate='Verified image and approved profile'
  pin=$(awk '$1 == "tag:" {print $2}' "$base/activation/sidecar.yaml")
  image=$(cat "$evidence/verified-image")
  [[ $image == "ghcr.io/thaynes43/studio-local-model:$pin" ]]
  profile=$(jq -ce . "$evidence/c-approved-profile.json")
  card=$(jq -er .gpu_uuid <<< "$profile")
  gate_passed
  gate='Existing Wan compute lock'
  kubectl -n ai exec deploy/wan-video -c api -- test -f /workspace/compute.lock
  gate_passed
  gate='Create diagnostic Job'
  yq -o=json '.' "$base/diagnostic/job.yaml" |
    jq -e --arg image "$image" --arg profile "$profile" '
      .spec.template.spec.containers[0] |= (.image = $image | .args[1] = $profile)
    ' > "$attempt/spec.json"
  kubectl -n ai create -f "$attempt/spec.json" -o json > "$attempt/created.json"
  name=$(jq -er '.metadata.name | select(test("^wan-local-model-start-check-[a-z0-9]+$"))' "$attempt/created.json")
  printf 'Diagnostic evidence directory %s, Job %s\n' "$attempt" "$name"
  gate_passed
  gate='Read diagnostic Job condition'
  deadline=$((SECONDS + 660))
  condition=''
  while :; do
    # One failed read must not end the block before the logs are saved: a
    # re-run would create a second Job. Keep the last good read and poll on.
    if kubectl -n ai get "job/$name" -o json --request-timeout=10s > "$attempt/job.json.next"; then
      mv "$attempt/job.json.next" "$attempt/job.json"
      condition=$(jq -r '[.status.conditions[]? | select(.status == "True") | .type] |
        if index("Failed") then "Failed" elif index("Complete") then "Complete" else "" end
      ' "$attempt/job.json")
    fi
    if [[ -n $condition ]] || (( SECONDS >= deadline )); then
      break
    fi
    sleep 2
  done
  gate_passed
  gate='Capture pod termination state'
  kubectl -n ai get pods -l "batch.kubernetes.io/job-name=$name" -o json |
    jq '{pods: [.items[] | {name: .metadata.name, status: .status}]}' > "$attempt/pods.json"
  gate_passed
  result=0
  gate='Diagnostic Job completed'
  if [[ $condition == Complete ]]; then
    gate_passed
  else
    printf '%s failed (%s)\n' "$gate" "${condition:-deadline}" >&2
    result=1
  fi
  gate='Capture diagnostic logs'
  kubectl -n ai logs "job/$name" --pod-running-timeout=5s --request-timeout=15s \
    > "$attempt/start-check.log" 2> "$attempt/logs.stderr"
  gate_passed
  gate='Diagnostic startup verdict and listener'
  if sed -n 's/^[^{]*\({".*}\)$/\1/p' "$attempt/start-check.log" |
    jq -se --arg card "$card" '
      any(.[]; .event == "local-model-listening" and .verdict == "ready" and .reason_code == null and
        .gpu_uuid == $card and (.pins.NVIDIA_VISIBLE_DEVICES == $card or .pins.NVIDIA_VISIBLE_DEVICES == "void") and
        .pins.CUDA_VISIBLE_DEVICES == $card and .visible_gpu_count == 1 and .visible_gpu_uuids == [$card]) and
      any(.[]; .ready == true and .residency.resident == false and .residency.receipt == null and
        (.residency | has("startup_error") | not)) and
      any(.[]; .cuda_library_loaded == true)
    '; then
    gate_passed
  else
    printf '%s failed\n' "$gate" >&2
    result=1
  fi
  gate=''
  exit "$result"
BASH
```

#### Activate and verify the Service

After the diagnostic and calibrated PR checks pass, declare activity with
`agent-run declare-activity start "Wan local model activation" --scope ai,talosw01,wan-video`.
In the Wan Flux Kustomization, change the path to the reviewed activation
overlay. Keep `wait: false`, the 5-minute apply timeout, and the absence of
`healthChecks`. The activation overlay sets HelmRelease
`upgrade.disableWait: true` so its upgrade can finish applying resources
while an image pull, init container or listener is still unhealthy. This
allows the reviewed revert to reconcile without waiting for the Deployment
progress deadline. Use the Deployment, EndpointSlice and discovery gates
below as the workload proof. The dormant HelmRelease keeps its existing
upgrade settings.

Merge C and reconcile the parent and existing Wan Kustomization. Require four
Ready containers on the new pod, completed init containers, a Ready Service
endpoint, and healthy local discovery. The private probe remains Ready in the
served failure state. Any complete listener outage can briefly remove Wan
from the Service. While the listener is unavailable, an otherwise admissible
legacy Wan, typed I2V, S2V or Foley request fails before engine work. Keep
producers paused until the applicable gate passes. Use the activity ID to end
the declaration once settled.

The leased startup proof below resolves `gpu_pin_unproven` before discovery
if the sidecar started while the API held the lease. The probe cannot
dispatch model work.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  umask 077
  evidence=${OP442_EVIDENCE:?}
  gate='Reconcile activation manifests'
  flux reconcile kustomization cluster-apps -n flux-system --with-source
  flux reconcile kustomization wan-video -n ai
  gate_passed
  # The Kustomization does not wait: Flux returns once the HelmRelease object is
  # applied and Helm patches the Deployment a few seconds later. Reading the
  # roll-out in that gap reads the old, already complete Deployment.
  gate='Deployment template has the local-model container'
  deadline=$((SECONDS + 300))
  until kubectl -n ai get deploy wan-video -o json |
      jq -e 'any(.spec.template.spec.containers[]; .name == "local-model")' >/dev/null; do
    (( SECONDS < deadline ))
    sleep 3
  done
  gate_passed
  gate='Deployment rollout'
  kubectl -n ai rollout status deploy/wan-video --timeout=35m
  gate_passed
  gate='New Ready pod, init containers and image'
  image=$(cat "$evidence/verified-image")
  old=$(cat "$evidence/dormant.uid")
  [[ -n $old && -n $image ]]
  kubectl -n ai get pods -l app.kubernetes.io/name=wan-video -o json |
    jq -e --arg old "$old" --arg image "$image" '
      .items | select(length == 1) | .[0] |
      select(.metadata.uid != $old and .spec.nodeName == "talosw01") |
      select(any(.spec.containers[]; .name == "local-model" and .image == $image)) |
      select(any(.status.initContainerStatuses[]; .name == "prepare-local-model")) |
      select(([.status.containerStatuses[].name] | sort) == ["api","app","foley","local-model"]) |
      select(all(.status.containerStatuses[]; .ready and .restartCount == 0)) |
      select(all(.status.initContainerStatuses[]; .state.terminated.exitCode == 0)) |
      {uid:.metadata.uid, containers:[.status.containerStatuses[] | {name,image,imageID,ready}]}
    ' > "$evidence/c-pod.json"
  gate_passed
  gate='Same nonempty PVC UID'
  pvc_uid_matches "$evidence/pvc.uid"
  gate_passed
  gate='New pod is a Ready Service endpoint'
  uid=$(jq -er .uid "$evidence/c-pod.json")
  kubectl -n ai get endpointslices -l kubernetes.io/service-name=wan-video -o json |
    jq -e --arg uid "$uid" '
      any(.items[].endpoints[]; .targetRef.uid == $uid and .conditions.ready == true)'
  gate_passed
  for name in wan-local-model-weights wan-local-model-download-policy; do
    gate="Retired inventory $name is absent"
    retired=$(kubectl -n ai get "kustomization/$name" --ignore-not-found -o name)
    [[ -z "$retired" ]]
    gate_passed
  done
  gate='Retired provisioning Job, policy and ConfigMap are absent'
  job=$(kubectl -n ai get job wan-local-model-provision-r2 --ignore-not-found -o name)
  policy=$(kubectl -n ai get ciliumnetworkpolicy wan-local-model-provision --ignore-not-found -o name)
  [[ -z "$job" && -z "$policy" ]]
  kubectl -n ai get configmaps -o name |
    awk '/^configmap\/wan-local-model-provision-/ { found=1 } END { exit found }'
  gate_passed
  gate='Local-model startup proof under the compute lease'
  kubectl -n ai exec -i deploy/wan-video -c api -- env PYTHONPATH=/config/video python -B - <<'PY'
import api

lease = api.acquire_compute()
if lease is None:
    raise SystemExit("Compute is reserved")
with lease:
    lane = api.local.enabled_lane(api.ROOT, api.CONFIG)
    if lane is None:
        raise SystemExit("Expected an active local-model lane")
    api.local.require_started(lane.status(residency_only=True, allow_unbound=True, lease=lease))
PY
  gate_passed
  gate='Wan, Foley, queue and local discovery health'
  python3 -B scripts/wan-video/op442_smoke.py active > "$evidence/c-health.json"
  gate_passed
  gate='Discovery matches the approved ceiling'
  ceiling=$(jq -er .unloaded_max_mib "$evidence/c-approved-profile.json")
  jq -e --argjson ceiling "$ceiling" '.discovery.unloaded_max_mib == $ceiling' "$evidence/c-health.json"
  gate_passed
  gate='Sidecar log proves the GPU pin'
  kubectl -n ai logs deploy/wan-video -c local-model > "$evidence/c-local-model.log"
  card=$(jq -er .gpu_uuid "$evidence/c-approved-profile.json")
  sed -n 's/^[^{]*\({".*}\)$/\1/p' "$evidence/c-local-model.log" |
    jq -se --arg card "$card" '
      any(.[]; (.event == "local-model-listening" or .event == "local-model-pin-proof") and
        .reason_code == null and .gpu_uuid == $card and
        (.pins.NVIDIA_VISIBLE_DEVICES == $card or .pins.NVIDIA_VISIBLE_DEVICES == "void") and
        .pins.CUDA_VISIBLE_DEVICES == $card and .visible_gpu_count == 1 and .visible_gpu_uuids == [$card])
    '
  gate_passed
  gate='GPU plugin allocation remains on ComfyUI'
  kubectl -n ai get deployment wan-video -o json |
    jq -e '[.spec.template.spec.containers[] | select(.resources.limits["nvidia.com/gpu"] != null) |
      {name, limit:.resources.limits["nvidia.com/gpu"]}] == [{"name":"app","limit":"2"}]'
  gate_passed
BASH
```

#### Wan-pod change check

Before every later Wan-pod change, pause the Studio worker and direct
producers. Confirm there is one Ready Wan pod, four Ready containers if
active, and a Ready Service endpoint for that pod. Save the pod UID, image,
restart counts and listener status. Run the active health command below and
review the empty queue, cleared residency and idle ownership before
proceeding. Set `OP442_WAN_MODE=dormant` to check a dormant deployment.
Allow for a Service outage in the change window and repeat these checks
after the replacement pod starts.

**After a recreate of an active pod, prove the pin first.** The API holds
the compute lease while it recovers, so the sidecar normally starts with
`gpu_pin_unproven` and serves a refusal. Local discovery then answers 503 and
the active health command below fails closed, although nothing is wrong. Run
the gate "Local-model startup proof under the compute lease" from
[Activate and verify the Service](#activate-and-verify-the-service) on the
replacement pod before this check. A submit proves the pin as well; discovery
only reads it. (Found in the delta review of PR 5060.)

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  mode=${OP442_WAN_MODE:-active}
  case "$mode" in
    active) containers='["api","app","foley","local-model"]' ;;
    dormant) containers='["api","app","foley"]' ;;
    *) printf 'Expected OP442_WAN_MODE=active or dormant\n' >&2; exit 1 ;;
  esac
  gate='One Ready Wan pod with the expected containers'
  uid=$(kubectl -n ai get pods -l app.kubernetes.io/name=wan-video -o json |
    jq -er --argjson containers "$containers" '.items | select(length == 1) | .[0] |
      select(any(.status.conditions[]; .type == "Ready" and .status == "True")) |
      select(([.status.containerStatuses[].name] | sort) == $containers) |
      select(all(.status.containerStatuses[]; .ready)) | .metadata.uid')
  printf 'Wan pod UID %s\n' "$uid"
  gate_passed
  gate='Wan pod is a Ready Service endpoint'
  kubectl -n ai get endpointslices -l kubernetes.io/service-name=wan-video -o json |
    jq -e --arg uid "$uid" 'any(.items[].endpoints[]; .targetRef.uid == $uid and .conditions.ready == true)'
  gate_passed
  gate='Read container status and image IDs'
  kubectl -n ai get pods -l app.kubernetes.io/name=wan-video -o json |
    jq '.items[].status.containerStatuses[] | {name, imageID, ready, restartCount}'
  gate_passed
  gate='Wan, Foley, queue and model health'
  python3 -B scripts/wan-video/op442_smoke.py "$mode"
  gate_passed
  gate='Read rack process ownership for operator review'
  kubectl -n ai exec deploy/wan-video -c api -- nvidia-smi \
    --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory --format=csv
  gate_passed
BASH
```

#### Clear a local-model hold

Keep all producers quiesced. Run the reviewed helper in the API container
when a release latched `local_model_held`, or when a retained journal blocks
the dormant path. The guard is three idle Card A memory readings at or below
both the calibrated ceiling and the recorded memory baseline plus 4 MiB. The
journal's load baseline takes precedence. For a hold that predates a load
baseline, use the highest recorded dormant calibration sample.

With the profile mounted, the helper requires the matching sidecar to report
no child. With the profile removed, it checks `sidecar.sock` under the compute
lease. A live listener, a non-socket path or an inconclusive probe is a
refusal. If the socket refuses a connection, the helper moves it in the same
directory to `sidecar.stale.<UTC timestamp>-<id>.sock`. It records the name in
the receipt and requires the original path to stay absent through the rest
of the proof. Keep the moved socket as evidence even if a later check fails.

The dormant path checks three compute-process lists against the engine PIDs
and names reviewed in the calibration for that boot, within their recorded
memory. The API container may miss processes in other containers. An empty
list proves nothing by itself. A row with an unresolvable process name causes
refusal. The helper also refuses an occupied lease, a nonempty or unreadable
queue, an active Wan or Foley job, or a separate render hold. For a Wan render
hold, use the existing Wan recovery procedure first.

Use the original reviewed calibration and save the exact approved C profile.
The helper accepts ceilings from the rounded-up drained maximum through that
maximum plus 4 MiB, within 1–512 MiB. If the original ceiling was too low,
calculate the corrected ceiling from the original samples using the same
formula and review it through GitOps. Keep both profile artifacts. For a mounted profile the helper reads the
corrected mount and requires the sidecar to match. For dormant recovery, point
`OP442_RECOVERY_PROFILE` to the private copy of the reviewed corrected JSON.
The helper reconstructs the earlier digest from the corrected profile and
accepts the journal only when a lower ceiling in the same range reproduces
that digest exactly. It records both digests and the ceiling change. The
journal's load baseline still applies.

Keep the receipt and archived journal on the original PVC. Link the receipt
ID and state hash in the private operator note with the operator, pod UID and
reason for recovery. The helper writes its proof before renaming `state.json`
in place to `state.<UTC timestamp>-<id>.json`. On refusal, preserve the files
and resolve the stated proof failure. On interruption, check the receipt,
archived journal and any moved socket before another attempt. Keep the local
request receipts and avoid reusing an unresolved request ID. Start new work
only after the health gate for the resulting active or dormant state.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  umask 077
  evidence=${OP442_EVIDENCE:?}
  receipt=$(mktemp "$evidence/hold-clear.XXXXXXXX")
  profile=${OP442_RECOVERY_PROFILE:-"$evidence/c-approved-profile.json"}
  gate='Leased hold-clear proof and receipt'
  jq -en --slurpfile calibration "$evidence/calibration.json" \
    --slurpfile profile "$profile" \
    '{calibration: $calibration[0], profile: $profile[0]}' |
    kubectl -n ai exec -i deploy/wan-video -c api -- python -B /config/video/clear_local_model_hold.py |
    tee "$receipt"
  gate_passed
BASH
```

#### Rollback C after a served startup refusal

A Ready listener and HTTP 503 with a startup reason trigger this gate.
Keep producers paused. Under the existing lease, confirm the queue and workers
are idle, the journal path is absent, and the listener attests to a startup
refusal with no child. Save that evidence and proceed to the GitOps revert.
An existing journal or uncertain evidence requires the tracked rollback or
hold-clearing path and the original release proof.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  umask 077
  evidence=${OP442_EVIDENCE:?}
  gate='Leased idle proof, no journal and served refusal with no child'
  kubectl -n ai exec -i deploy/wan-video -c api -- env PYTHONPATH=/config/video python -B - \
    > "$evidence/start-refusal-rollback.json" <<'PYTHON'
import json
import api
from clear_local_model_hold import check_idle, path_present

lease = api.acquire_compute()
if lease is None:
    raise SystemExit("Compute is reserved")
with lease:
    check_idle()
    if path_present(api.local.state_path(api.ROOT)):
        raise SystemExit("Residency journal exists. Use tracked rollback or hold clearing.")
    lane = api.local.enabled_lane(api.ROOT, api.CONFIG)
    if lane is None:
        raise SystemExit("Expected an active local-model lane")
    status = lane.status(residency_only=True, allow_unbound=True)
    if not status.get("startup_error") or status["resident"]:
        raise SystemExit("The listener did not report a startup refusal with no model child")
    print(json.dumps(status))
PYTHON
  gate_passed
BASH
```

#### Rollback C after the sidecar served

Quiesce producers and check Wan, Foley, queue and sidecar listener health.
Restore listener readiness if needed for release inspection. If the lane
has a local-model hold, run [hold clearing](#clear-a-local-model-hold) first.
If it has ordinary residency, the rollback helper submits the tracked unload,
validates its receipt and proves no child, reservation or pending operation
remains under the lease. It drains ordinary ComfyUI cache only after proving
residency is clear. If release latches a hold, preserve the journal and
return to hold clearing before continuing to the GitOps revert. If the
ceiling lacks the calibrated headroom, preserve the original profile and
review the ceiling-only correction against the original calibration. If the
listener is unavailable, a stale socket becomes eligible to move only after
the profile has been removed through the reviewed dormant revert. Follow
the dormant recovery gate for any retained journal before resuming dispatch.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  umask 077
  gate='Tracked rollback and residency release'
  python3 -B scripts/wan-video/op442_smoke.py rollback --evidence "${OP442_EVIDENCE:?}"
  gate_passed
BASH
```

#### Rollback C when the sidecar never became Ready

An image that cannot run or a listener it cannot bind can leave the Service
without a Ready endpoint. For a served startup refusal, use its leased no-child gate above. If rollout observation from pod creation
shows the sidecar never became Ready, and the API container confirms
`state.json` is absent, proceed directly to the GitOps revert. Every model load
writes state before it starts a child. This path works without a sidecar
listener or a local-model request. Current `ready: false` alone cannot establish
that readiness was never reached. Cross-check the observed rollout with the
pod's container states, events, current and previous sidecar logs, and
EndpointSlices. A previous successful listener, a state file, or uncertain
history requires the tracked rollback and, if held, the hold-clearing step.
A stale socket can survive a crash. Use rollout history and journal absence
for the direct revert and leave the socket in place. If a retained journal
is found, keep dispatch paused and use the applicable hold-clearing path with
the original calibration and approved profile or reviewed ceiling correction.

Keep dispatch paused. Copy the exact failed pod name from the first command
into `OP442_FAILED_POD`. A container that never started has no logs. When
Kubernetes reports an exit, read the previous container logs. Keep these
records private. Require the inside-container proof to succeed on the same pod.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  gate='Read current Wan pod names'
  kubectl -n ai get pods -l app.kubernetes.io/name=wan-video -o wide
  gate_passed
  gate='Read failed sidecar status and pod events'
  pod=${OP442_FAILED_POD:?Set OP442_FAILED_POD to the failed Wan pod name}
  kubectl -n ai get pod "$pod" -o json |
    jq -e '.status.containerStatuses[] | select(.name == "local-model" and .ready == false) |
      {ready, started, restartCount, state, lastState}'
  kubectl -n ai describe pod "$pod"
  gate_passed
  gate='No Ready Service endpoint'
  kubectl -n ai get endpointslices -l kubernetes.io/service-name=wan-video -o json |
    jq -e 'all(.items[].endpoints[]; .conditions.ready != true)'
  gate_passed
  gate='Read status and applicable sidecar logs'
  status=$(kubectl -n ai get pod "$pod" -o json |
    jq -ce '.status.containerStatuses[] | select(.name == "local-model")')
  if jq -e '.state.running or .state.terminated' <<< "$status" > /dev/null; then
    kubectl -n ai logs "$pod" -c local-model --tail=80
  fi
  if jq -e '.lastState.terminated' <<< "$status" > /dev/null; then
    kubectl -n ai logs "$pod" -c local-model --previous --tail=80
  fi
  gate_passed
BASH
```

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  pod=${OP442_FAILED_POD:?}
  gate='Leased idle proof, activated profile and no residency journal'
  kubectl -n ai exec -i "$pod" -c api -- env PYTHONPATH=/config/video python -B - <<'PY'
import api
from clear_local_model_hold import check_idle, path_present

lease = api.acquire_compute()
if lease is None:
    raise SystemExit("Compute is reserved")
with lease:
    check_idle()
    if api.local.load_profile(api.CONFIG) is None:
        raise SystemExit("Expected the activated profile on the failed pod")
    if path_present(api.local.state_path(api.ROOT)):
        raise SystemExit("Residency journal exists. Use the tracked rollback or hold clearing.")
PY
  gate_passed
BASH
```

#### GitOps revert and dormant gate

After the applicable rollback proof, merge a reviewed revert of C's activation settings in
`wan-video/ks.yaml`. Restore the dormant `./kubernetes/main/apps/ai/wan-video/app`
path. Keep the 5-minute timeout and operator readiness gate. Retain the
Wan app ConfigMap helper and keep both completed provisioning inventories
retired. Reconcile the parent and existing Wan Kustomization. Set
`OP442_ACTIVE_POD_UID` to the UID of the pod being replaced. Require a new
three-container Ready pod with a Ready Service endpoint, the same PVC UID,
empty queues, idle workers, and `local_model_not_installed`. Repeat the private
legacy read from the dormant gate and verify rack ownership before resuming
dispatch. If an already-dormant pod still has a journal, run
[hold clearing](#clear-a-local-model-hold) before resuming. Supply the
original calibration and approved profile or reviewed ceiling correction.
The dormant helper moves only a refused socket under the lease, then requires
the memory proof and the remaining gates. End the activity declaration once
settled.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  umask 077
  evidence=${OP442_EVIDENCE:?}
  old=${OP442_ACTIVE_POD_UID:?Set OP442_ACTIVE_POD_UID to the pod being replaced}
  gate='Reconcile dormant manifests'
  flux reconcile kustomization cluster-apps -n flux-system --with-source
  flux reconcile kustomization wan-video -n ai
  gate_passed
  # As in the activation block: wait for Helm to patch the Deployment before
  # reading the roll-out, or the gate reads the Deployment being replaced.
  gate='Deployment template no longer has the local-model container'
  deadline=$((SECONDS + 300))
  until kubectl -n ai get deploy wan-video -o json |
      jq -e 'all(.spec.template.spec.containers[]; .name != "local-model")' >/dev/null; do
    (( SECONDS < deadline ))
    sleep 3
  done
  gate_passed
  gate='Dormant Deployment rollout'
  kubectl -n ai rollout status deploy/wan-video --timeout=35m
  flux get kustomizations -n ai wan-video > "$evidence/rollback-flux.txt"
  gate_passed
  gate='New Ready dormant pod and successful init containers'
  uid=$(kubectl -n ai get pods -l app.kubernetes.io/name=wan-video -o json |
    jq -er --arg old "$old" '.items | select(length == 1) | .[0] |
      select(.metadata.uid != $old) |
      select(([.status.containerStatuses[].name] | sort) == ["api","app","foley"]) |
      select(all(.status.containerStatuses[]; .ready)) |
      select(all(.status.initContainerStatuses[]; .state.terminated.exitCode == 0)) | .metadata.uid')
  gate_passed
  gate='Same nonempty PVC UID'
  pvc_uid_matches "$evidence/pvc.uid"
  gate_passed
  gate='New pod is a Ready Service endpoint'
  kubectl -n ai get endpointslices -l kubernetes.io/service-name=wan-video -o json |
    jq -e --arg uid "$uid" 'any(.items[].endpoints[]; .targetRef.uid == $uid and .conditions.ready == true)'
  gate_passed
  gate='Local-model profile mount is absent'
  kubectl -n ai exec deploy/wan-video -c api -- test ! -e /config/video/local-model.json
  gate_passed
  gate='Dormant health and discovery'
  python3 -B scripts/wan-video/op442_smoke.py dormant > "$evidence/rollback-health.json"
  gate_passed
BASH
```

## Two private smoke windows

**Expected.** Run both windows with the active health gate, known idle
ownership, the same private evidence directory and all other producers paused.
The driver stages only synthetic inputs generated by
[smoke-inputs.py](../../scripts/studio-local-model/smoke-inputs.py) before the
first window. Each admitted operation uses the Wan compute lease. Each
30-minute clock starts immediately before its first leased load. Keep the
terminal open through cleanup and hold dispatch paused between windows.

```bash
bash -s <<'BASH'
  set -euo pipefail
  source scripts/wan-video/op442_gate.sh
  umask 077
  evidence=${OP442_EVIDENCE:?}
  gate='Window 1 cold load, text, vision, handover and final unload'
  python3 -B scripts/wan-video/op442_smoke.py window-1 --evidence "$evidence"
  gate_passed
  gate='Window 2 paired memory drift and final unload'
  python3 -B scripts/wan-video/op442_smoke.py window-2 --evidence "$evidence"
  gate_passed
BASH
```

### Window 1. Cold load, text, vision and S2V

| Step | Budget and evidence |
| --- | --- |
| Cold load | Up to 120 s with a 60 s target. Record the load ID, baseline and wall time. The PVC cache may already be warm. |
| Text and vision | 30 s for one `plan_v1` answer at 256 tokens and 55 s for one `frame_check_v1` answer at 512 tokens. Keep the token counts, schema results and all four ordered synthetic frame hashes. Allow 10 s for the model peak check. |
| Handover precheck and S2V | Allow 30 s for the leased readiness and ledger precheck. Submit by 4m 15s elapsed with 1,280 s for the render and admission, and keep 265 s for read-back, reload and final cleanup. |
| S2V evidence read-back | Allow 40 s to verify the internal unload receipt predates engine dispatch, read the empty queue, and save the prompt ID and receipt. Use the per-UUID process samples to confirm S2V ownership. |
| Return load | Up to 120 s with a 60 s target. Record the warm load ID, baseline and wall time. |
| Final tracked unload and idle proof | Reserve 100 s. Verify the unload receipt against its paired baseline, then prove the queue, workers and card idle under the lease. Record the final receipt and idle proof. |

### Window 2. Five paired drift cycles

Start only after window 1 writes `passed.json`. The driver checks the profile
and unloaded host again. Budget 120 s per load and 25 s for each unload and
its receipt, for 725 s total. Keep the 100 s final cleanup reserve throughout.
Each cycle records the load baseline, verified post-exit sample and signed
difference. Require post-exit memory at or below the calibrated ceiling and
at most 4 MiB above the corresponding baseline. The fifth tracked unload
supplies the final release receipt. The driver then verifies idle ownership
under the lease. A stopped window with a safely unloadable child still uses
its final cleanup reserve. A failure preserves the receipts and evidence.

Rely on the 4 MiB allowance for unattended use only after window 2 passes.
A release exceeding that limit already latches a hold during the pilot.
Review the evidence before changing any allowance. The absolute profile
ceiling also applies.

Keep both windows' GPU and process samples, observed local-model VRAM peaks
against 12,288 MiB, sidecar cgroup memory peaks against 16 GiB, request IDs
and timings private. A sampled peak is an observation at the sampling
interval. The driver retries the exact original PUT at most once, after a
transport failure and a definitive absent response for that ID. At a polling
deadline, follow the same original ID through cleanup. If work settles with
a live child and a known owner, submit the tracked unload within the cleanup
reserve. A failed or unverified release, unknown owner, access denial or
unresolved original ID stops further requests. Keep any hold and use the
recovery route below. A skipped, cut-off or unverified step leaves the
window incomplete.

## Stops and evidence

| Stop | Hold state and next action |
| --- | --- |
| Release exceeds the load baseline plus 4 MiB or the calibrated ceiling and fails the API's memory proof, or the API reports `local_model_held` | Save the receipt, journal and memory readings. Use [hold clearing](#clear-a-local-model-hold). If the ceiling was set too low, preserve the original profile and review a ceiling-only correction within the original calibration range. |
| Dormant recovery finds a socket, an unresolvable process name or an unavailable process query | The helper archives a socket only after a refused connection under the lease. A live listener, non-socket, ambiguous probe or process proof failure stops recovery. Keep dispatch paused and preserve any moved socket. An empty process list still requires all three memory readings to pass. |
| Release receipt missing, failed or inconsistent without a confirmed hold | Hold status unknown. Preserve the request and journal, and inspect under the lease. Use [hold clearing](#clear-a-local-model-hold) if held, or resume tracked cleanup only after ownership and residency are known. |
| Unknown ownership, malformed queue, unsettled request or failed lease proof | Foreign work or lease contention alone does not latch the local-model hold. A malformed queue may latch a separate render hold or a local-model hold after dispatch. Resolve ownership and any Wan render hold first. Use [hold clearing](#clear-a-local-model-hold) for a latched local-model hold. |
| Startup refusal, reserve failure or unavailable listener | The startup refusal creates no hold. With a listening but unavailable process, verify prior residency under the lease before reverting. Use the [direct rollback](#rollback-c-when-the-sidecar-never-became-ready) only with evidence of no Ready transition and no state file. With state present, use the tracked rollback and [hold clearing](#clear-a-local-model-hold) if held. |
| Access denial | The denial does not itself latch a hold. The state of any earlier request may be unknown. Stop without retrying through another route. An authorized operator can inspect and follow [hold clearing](#clear-a-local-model-hold) if held. |
| Output check, cap or budget failure | No hold from the stop itself. Finish verified cleanup within the reserve if safe. If the API latches a hold during release, use [hold clearing](#clear-a-local-model-hold). |

Keep dispatch paused through verified cleanup or recovery. Resume once the
applicable Service, health, queue, rack and residency gates pass. Preserve
failed attempts, weights and journals. Publish only IDs, states, hashes,
measured times and memory samples to the tracker. Keep service logs, full
model outputs, prompts, staged media and engine ledgers in the private
evidence directory. Record source, built image, tested checks, installation,
usable smoke and Tom's acceptance separately. The E10 model quality verdict
comes from its own benchmark.
