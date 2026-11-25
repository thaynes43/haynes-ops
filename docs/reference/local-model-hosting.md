# Studio local-model hosting

[Story 442](https://work.haynesnetwork.com/work_packages/442) and
[profile CR-20](https://github.com/thaynes43/haynes-studio/blob/fa52968/docs/reference/local-model-profile.md)
select Qwen3.5-9B Q5_K_M and its BF16 projector on Card A. The image uses the
pinned llama.cpp b12679 CUDA 12 libraries and CPython 3.12.16. The loader reads
both local GGUFs in the child under the Wan lease. It unloads before every Wan
or Foley handover. The E10 benchmark and Tom's acceptance have separate gates.

## Part 2a dormant rollout

**Expected.** On merge the main-only image workflow builds, runs the CPU smoke,
publishes and signs the observed digest, with BuildKit provenance. Flux updates
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
```

Declare disruptive work with
`declare-activity start "Wan dormant admission update" --scope ai,talosw01,wan-video`.
After independent review and while the gates still pass, reconcile the approved
merge with `flux reconcile kustomization wan-video -n ai --with-source`. Wait
for the new pod UID and `kubectl -n ai rollout status deploy/wan-video --timeout=35m`.
Confirm the watched revision and new ConfigMap and mounted code hashes. Repeat
the prechecks plus `at_wan 8080/ready`, `at_wan 8080/v1/profiles` (I2V, S2V,
Foley), a read of a known legacy `/videos/<id>` and
`at_wan 8080/v1/local-model/profile` (503 and `local_model_not_installed`).
Check for a single healthy pod and release the activity declaration once settled.
On failure keep dispatch quiesced, inspect the job/queue/hold and restore the
previous dormant source through a GitOps revert and the same targeted
reconciliation. Repeat the full health gate. Keep the PVC and existing ledgers.

The PR image smoke uses UID 1000 and a read-only filesystem with Docker network
disabled. It imports the Python module, resolves the C ABI against the exact
shared libraries, builds the plan and required-tool grammars from the packaged
upstream Qwen Jinja fixture and accepts one synthetic `record_plan_v1` reply
while rejecting an unexpected function. The Wan CPU suite covers the private
protocol, request limits, recovery and fake inference. The image smoke uses no
GGUFs, CUDA device or network. Driver compatibility, the embedded 9B template,
GPU allocation, visual tokenization and model behavior require the live smoke.

## Part 2b activation

**Expected.** Use a separate GitOps PR or serial changes under coordinator
ownership. Verify the observed main build and signature against
`https://github.com/thaynes43/haynes-ops/.github/workflows/studio-local-model-build.yml@refs/heads/main`
with the GitHub Actions OIDC issuer `https://token.actions.githubusercontent.com`.
Record build provenance and the digest from that run. Replace the pending image
tag with the published tag and digest.

```bash
cosign verify --certificate-identity \
  https://github.com/thaynes43/haynes-ops/.github/workflows/studio-local-model-build.yml@refs/heads/main \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com "$image_at_digest"
docker buildx imagetools inspect "$image_at_digest" --format '{{ json .Provenance.SLSA }}'
```

1. Recheck the dormant health gate, OP410 readiness, node headroom (including
   the sidecar's 8 GiB request and 16 GiB limit), PVC capacity and inodes. Keep
   producers quiesced while downloading. Run `wan_exec df -Pk /workspace` and
   `wan_exec df -Pi /workspace`. Budget 7,499,546,400 bytes, the 8 GiB reserve,
   the 1,024-inode reserve, and up to 16 additional inodes.
2. Reference the `local-model-pending/weights/` resources from GitOps. Install
   and verify the provisioner's FQDN policy before introducing the Job. Its
   one attempt uses the same claim on talosw01, with HTTPS to the pinned Hub
   revision and CDN. Check `kubectl -n ai wait --for=condition=complete job/wan-local-model-provision --timeout=30m`.
   On success, run the commands below. On failure preserve the quarantined
   path and account for its bytes. Remove the completed Job and policy from
   the desired state once read-back completes. Keep the revision directory.
3. Record several idle memory readings by UUID from the already quiescent pod,
   with no model child and known CUDA ownership. Calibrate `unloaded_max_mib`
   from the highest observed drained baseline on Card A, at most 512 MiB.
   Stop if the baseline cannot be proved. Replace the profile's `null`
   placeholder with that integer. The cold-load handover will confirm it with
   a fresh measurement after the child exits. The rollout may lower the
   ceiling on new evidence. It may never raise the source budget to make a
   release pass.
4. In Wan's existing Flux Kustomization set `spec.path` to
   `./kubernetes/main/apps/ai/wan-video/local-model-pending/activation`. Keep
   the same Kustomization identity and PVC prune protection. Review the Helm
   render, verify the exact read-only weights mount, and reconcile only after
   rechecking queue and capacity gates. Check the new pod and profile
   discovery, the existing Wan/Foley/legacy reads, the sole device allocation
   of two GPUs on `app`, the sidecar's UUID environment and its non-inference
   readiness probe. The sidecar starts unloaded.

```bash
weights=/workspace/local-model-weights/qwen3.5-9b-q5_k_m/3885219b6810b007914f3a7950a8d1b469d598a5
wan_exec cat "$weights/manifest.json"
wan_exec sha256sum "$weights/Qwen3.5-9B-Q5_K_M.gguf" "$weights/mmproj-BF16.gguf"
wan_exec df -Pk /workspace
wan_exec df -Pi /workspace
at_wan 8080/v1/local-model/profile
kubectl -n ai exec deploy/wan-video -c local-model -- python3 -m studio_local_backend ready --workspace /workspace
```

Compare both hashes and sizes with
[the checked-in pins](../../kubernetes/main/apps/ai/wan-video/local-model-pending/weights/weights.json).
The 30-minute GPU window starts with the first load submission. Complete
image/weight verification and input staging before that point.

## One private smoke window

**Expected.** Run from a shell with `curl`, `jq`, Python 3 and the checkout.
Use a new private evidence directory. Start a loopback-only
`kubectl -n ai port-forward svc/wan-video 18080:8080` in a separate terminal.
Keep the producer pause and all admission checks in effect.

```bash
set -euo pipefail
umask 077
evidence=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/op442-smoke.XXXXXXXX")
base=http://127.0.0.1:18080
python3 scripts/studio-local-model/smoke-inputs.py "$evidence/inputs"
profile=$(curl -fsS "$base/v1/local-model/profile" | tee "$evidence/profile.json" | jq -er .profile.model)
card=$(jq -er .profile.gpu_uuid "$evidence/profile.json")
ceiling=$(jq -er '.profile.unloaded_max_mib | select(type == "number" and . <= 512)' "$evidence/profile.json")
for file in "$evidence"/inputs/*.png "$evidence"/inputs/*.wav; do
  name=${file##*/}
  sha=$(jq -er --arg name "$name" '.[$name]' "$evidence/inputs/hashes.json")
  curl -fsS --max-time 30 -X PUT --data-binary "@$file" \
    "$base/v1/inputs/$sha.${name##*.}" > "$evidence/$name.upload.json"
done

new_id() { python3 -c 'from uuid import uuid4; print(uuid4().hex)'; }
submit() {
  local label=$1 route=$2 id=$3 body=$4
  printf '%s\n' "$body" > "$evidence/$label.request.json"
  printf '%s\n' "$id" > "$evidence/$label.id"
  curl --fail-with-body --silent --show-error --max-time 15 -X PUT -H 'Content-Type: application/json' --data-binary "$body" \
    "$base/$route/$id" > "$evidence/$label.put.json" ||
    printf 'PUT reply uncertain for %s. Read the original ID before any repeat.\n' "$label" >&2
}
await_result() {
  local label=$1 route=$2 id=$3 limit=$4 started=$SECONDS
  while (( SECONDS - started < limit && SECONDS - window < 1800 )); do
    curl --fail-with-body --silent --show-error --max-time 5 \
      "$base/$route/$id" > "$evidence/$label.status.json" || return 1
    case $(jq -er .state "$evidence/$label.status.json") in
      succeeded) return 0 ;;
      failed) jq -c '{error,reason_code,admission_code,released_memory_mib}' "$evidence/$label.status.json" >&2; return 1 ;;
    esac
    sleep 2
  done
  printf 'Stop. Deadline reached for %s. Review original ID %s.\n' "$label" "$id" >&2
  return 1
}
local_request() {
  local label=$1 limit=$2 body=$3 id
  id=$(new_id)
  submit "$label" v1/local-model/requests "$id" "$body"
  await_result "$label" v1/local-model/requests "$id" "$limit"
}
operation() {
  jq -nc --arg p "$profile" --arg op "$1" '{profile:$p,operation:$op}'
}
inference() {
  jq -nc --arg p "$profile" --arg mode "$1" --arg text "$2" --argjson cap "$3" \
    '{profile:$p,operation:"infer",mode:$mode,prompt:$text,max_output_tokens:$cap}'
}
```

Start timestamped per-UUID measurements just before load, including resident
memory, utilization and temperature, with process ownership samples. Stop the
two samplers and port-forward when done. Keep their output private. Record the
cold-load and reload wall times, checkpoint readings and possibility of a warm
page cache. Capture loader logs and host memory peaks under the sidecar's
16 GiB limit. Watch the measures throughout.

```bash
timeout 30m kubectl -n ai exec deploy/wan-video -c api -- nvidia-smi \
  --query-gpu=timestamp,uuid,memory.used,utilization.gpu,temperature.gpu --format=csv -l 1 > "$evidence/gpu.csv" &
gpu_sample_pid=$!
timeout 30m kubectl -n ai exec deploy/wan-video -c api -- nvidia-smi \
  --query-compute-apps=timestamp,gpu_uuid,pid,process_name,used_gpu_memory --format=csv -l 1 > "$evidence/process.csv" &
process_sample_pid=$!
trap 'kill "$gpu_sample_pid" "$process_sample_pid" 2>/dev/null || true; wait "$gpu_sample_pid" "$process_sample_pid" 2>/dev/null || true' EXIT

window=$SECONDS
cold_id=$(new_id)
submit cold v1/local-model/requests "$cold_id" "$(operation load)"
await_result cold v1/local-model/requests "$cold_id" "$((120 - (SECONDS - window)))"
jq -e --argjson max 120 '(.finished_at - .created_at) <= $max' "$evidence/cold.status.json"
# Compare (.finished_at - .created_at) with the 60 s target. Stop at 120 s.

local_request text 180 "$(inference plan_tool_v1 \
  'For scene s01 use record_plan_v1 once. Propose two shots and hold the camera at eye level.' 256)"
jq -e '.result.text|fromjson|.name == "record_plan_v1" and .arguments.scene_id == "s01" and .arguments.shot_count == 2' \
  "$evidence/text.status.json"
local_request plan 180 "$(inference plan_v1 \
  'Make a short two-shot plan for scene s01. Hold an eye-level wide shot, then cut to a close view.' 256)"
jq -e '.result.text|fromjson|.scene_id == "s01" and (.shots|length) == 2' "$evidence/plan.status.json"

images=$(jq -c '[.["frame-1.png"],.["frame-2.png"],.["frame-3.png"],.["frame-4.png"]]' "$evidence/inputs/hashes.json")
vision=$(inference frame_check_v1 \
  'Report each image in order. The wardrobe record says green. Read the printed code when visible. Return null for unreadable code and uncertain when the garment is covered.' 512 |
  jq -c --argjson images "$images" '. + {image_sha256s:$images}')
local_request vision 180 "$vision"
jq -e '.result.text|fromjson|.frames == [
  {"face":"visible","code":"A17","wardrobe":"match"},
  {"face":"hidden","code":"B24","wardrobe":"match"},
  {"face":"visible","code":"C38","wardrobe":"mismatch"},
  {"face":"visible","code":null,"wardrobe":"uncertain"}]' "$evidence/vision.status.json"
```

The worker enforces the full server-owned schemas. Keep all three validated
responses and token counts. If any expected text/frame evidence differs, stop
the trial and use the tracked unload when ownership and the lease are clear.

For the single S2V trial keep at least 1,500 seconds available before
submission so the 1,200-second render deadline leaves time to verify, reload
and unload. Inspect `gpu.csv` and `process.csv` first. The local-model cap is
12,288 MiB on Card A. Preserve the delayed request as its original ID and use
GET after an ambiguous PUT. If that read returns `request_not_found`, repeat
the exact saved PUT once with its original ID. There is no automatic replay
under a new ID.

```bash
(( SECONDS - window <= 300 ))
wan_exec ls /workspace/local-model/requests | sort > "$evidence/requests.before"
s2v=$(new_id)
body=$(jq -nc --slurpfile h "$evidence/inputs/hashes.json" '{
  profile:"wan2.2-s2v-14b-lightning",
  story:"https://work.haynesnetwork.com/work_packages/442", seed:442,
  prompt:"A simple illustrated figure makes a brief speaking motion; locked camera.",
  image_sha256:$h[0]["s2v.png"],audio_sha256:$h[0]["s2v.wav"],board_frames:48}')
submit s2v v1/jobs/s2v "$s2v" "$body"
await_result s2v v1/jobs/s2v "$s2v" 1280
wan_exec ls /workspace/local-model/requests | sort > "$evidence/requests.after"
handover=$(comm -13 "$evidence/requests.before" "$evidence/requests.after")
[[ "$handover" =~ ^[0-9a-f]{32}\.json$ ]]
wan_exec cat "/workspace/local-model/requests/$handover" > "$evidence/handover.json"
started=$(jq -er .started_at "$evidence/s2v.status.json")
jq -e --argjson ceiling "$ceiling" --argjson started "$started" \
  '.internal and .operation=="unload" and .state=="succeeded" and
    (.released_memory_mib|type)=="number" and .released_memory_mib <= $ceiling and .finished_at <= $started' \
  "$evidence/handover.json"
at_wan 8188/queue | tee "$evidence/queue-after-s2v"
```

The receipt's measurement is taken after the local child is reaped. The same
lease encloses that verification and only then allows ComfyUI submission. Use
the UUID/process samples and the Wan ledger's prompt ID and timestamps to record
which card the engine used. Prove both queue lists empty before reload, and
account for any remaining model-card memory above the ceiling. A normal ComfyUI
cache can remain after video. Stop for inspection if the owner is unclear.
If ownership is proven to be the idle ComfyUI cache, the coordinator can
evict it under the compute lock with the commands below. Record the owner,
the eviction and the fresh reading before submitting reload. A missing
residency record with unexplained memory above the ceiling now latches a hold.

```bash
# Optional. Use only after independently identifying the idle ComfyUI owner.
wan_exec env PYTHONPATH=/config/video python -B -c '
import json
from urllib.request import urlopen
import api
import product_local as local
lease = api.acquire_compute()
if lease is None:
    raise SystemExit("Compute is reserved")
with lease:
    with urlopen("http://127.0.0.1:8080/health", timeout=5) as response:
        health = json.load(response)
    if not health.get("accepting_jobs") or health["active_job"] is not None:
        raise SystemExit("Wan is not idle and clear")
    lane = local.enabled_lane(api.ROOT, api.CONFIG)
    if lane and (lane.read_state() is not None or lane.status()["resident"]):
        raise SystemExit("Local-model residency requires inspection")
    api.require_empty_queue()
    api.engine("/free", {"unload_models": True, "free_memory": True})
    api.require_empty_queue()
    print(json.dumps(api.gpu_sample()))
' > "$evidence/cache-eviction.json"
```

```bash
# Run after observing idle and a clear card, with at least 150 s remaining.
(( SECONDS - window <= 1650 ))
local_request reload 120 "$(operation load)"
local_request final-unload 30 "$(operation unload)"
jq -e --argjson ceiling "$ceiling" \
  '(.released_memory_mib|type)=="number" and .released_memory_mib <= $ceiling' "$evidence/final-unload.status.json"
wan_exec test ! -e /workspace/local-model/state.json
at_wan 8080/health
at_wan 8188/queue
(( SECONDS - window <= 1800 ))
```

## Stops and evidence

Stop for any active or unknown engine work, hold, GPU identity/ownership
mismatch, capacity/reserve or hash failure, image or admission denial, lost
telemetry, context or schema failure, exceeded VRAM/host RAM/deadline,
failed unload or no time left for the handover and cleanup. Reconcile only
the original ID if an answer is ambiguous. Preserve any hold. A running job at
the deadline is an incident for the coordinator under the existing Wan recovery
procedure. Stop submitting work and preserve its ID and state.

Before a rollback from activation, quiesce, verify unload under the lease and
prove the selected card at or below its ceiling with no residency record.
Then revert the Flux path to the dormant app and repeat the dormant health
gate. Preserve the weights, failed attempts and evidence. If unload or
ownership is uncertain, keep dispatch paused for operator inspection before
changing the profile. The fallback requires a new ruling.

Record configured revision and digests, file sizes, available bytes/inodes,
health read-backs, exact request IDs and states, input hashes, complete schema
verdicts and token counts, elapsed cold/reload timings, UUID memory and process
samples, allocation logs, the unload receipt and the Wan engine ledger, final
idle proof, refusals and any stopped step. Keep outputs and raw evidence private.
Record source, built image, installed components, usable smoke and human
acceptance as separate facts.
