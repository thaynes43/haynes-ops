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
shared libraries, builds the plan and required-tool grammars from the bundled
Qwen3.5-4B Jinja template and accepts one synthetic `record_plan_v1` reply
while rejecting an unexpected function. Production builds its grammars and
parses replies with the Qwen3.5-9B GGUF's embedded template. The first live smoke
proves that template, driver compatibility, GPU allocation, visual tokenization
and model behavior. The Wan CPU suite covers the private protocol, request
limits, recovery and fake inference. CI also renders the staged manifests and
validates the projected profile with the production loader.

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

Create a private evidence directory and reuse it in the smoke window.

```bash
set -euo pipefail
umask 077
evidence=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/op442-smoke.XXXXXXXX")
```

1. Recheck the dormant health gate, OP410 readiness, node headroom (including
   the sidecar's 8 GiB request and 16 GiB limit), PVC capacity and inodes. Keep
   producers quiesced while downloading. Run `wan_exec df -Pk /workspace` and
   `wan_exec df -Pi /workspace`. Budget 7,499,546,400 bytes, the 8 GiB reserve,
   the 1,024-inode reserve, and up to 16 additional inodes.
2. Create the Job's PVC subdirectory as Wan's UID 1000 before referring to it
   as a `subPath`. Install and verify the provisioner's FQDN policy through
   GitOps before introducing the Job. The Job mounts only
   `local-model-weights` and makes one attempt. It starts at the pinned
   `huggingface.co` revision and follows only HTTPS redirects under
   `**.cdn.hf.co`. HEAD checks for both pinned files on 2026-11-25
   returned `us.aws.cdn.hf.co`. The Hub also documents `us.gcp.cdn.hf.co`
   as an edge. TLS uses standard certificate verification and the staged
   files must match the pinned size and SHA-256 before the create-only
   rename. [Hugging Face download hosts](https://huggingface.co/docs/hub/models-downloading)
3. On Job success, read back both weights as shown below. Remove the
   completed Job and policy from desired state after read-back. On failure,
   account for the attempt under its `.quarantine-*` directory before retrying.
   The published revision directory remains in place.
4. Before mounting the profile, evict idle ComfyUI cache under the shared
   compute lease using the commands below, capture several selected-UUID
   samples. Identify the stable readings after asynchronous eviction and
   verify known idle ownership without a model child. Set
   `unloaded_max_mib` to the highest observed drained baseline on Card A,
   subject to the staged design maximum of 512 MiB. Lower the staged value
   when the measurement permits it. Stop before activation if a drained
   baseline cannot be established within the cap. The cold-load and reload
   receipts record their own fresh baselines.
5. In Wan's existing Flux Kustomization set `spec.path` to
   `./kubernetes/main/apps/ai/wan-video/local-model-pending/activation`. Keep
   the same Kustomization identity and PVC prune protection. Render the
   chart and verify the original mounts for Wan and Foley. The preparation
   init creates the socket and ledger directory and staged inputs directory,
   and opens the existing lock without changing its inode. The local sidecar
   receives that lock and staged inputs read-only, the socket and ledger
   directory read-write, and the weights read-only. Reconcile only after
   checking queue and capacity again. Check the new pod, existing
   Wan/Foley/legacy reads, profile discovery, the two-device allocation only
   on `app`, the sidecar's UUID environment and listener-only readiness
   probe. The sidecar starts unloaded.

```bash
# Before introducing the Job.
wan_exec python -B -c '
import os
from pathlib import Path
root = Path("/workspace/local-model-weights")
root.mkdir(mode=0o750, exist_ok=True)
if root.is_symlink() or not os.access(root, os.R_OK | os.W_OK | os.X_OK):
    raise SystemExit("Weights directory must be writable by UID 1000")
'
# After introducing the Job with the FQDN policy in place.
kubectl -n ai wait --for=condition=complete job/wan-local-model-provision-r2 --timeout=30m
```

**Changed: the first attempt failed and was retried under a new name.** On
2026-11-25 at 05:16Z the Job `wan-local-model-provision` (memory limit 256Mi)
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

Run this calibration only with the dormant app, the checks above clear and
other clients quiesced. Keep its output with the rollout evidence.

```bash
wan_exec env PYTHONPATH=/config/video python -B -c '
import json, time
from urllib.request import urlopen
import api

lease = api.acquire_compute()
if lease is None:
    raise SystemExit("Compute is reserved")
with lease:
    with urlopen("http://127.0.0.1:8080/health", timeout=5) as response:
        health = json.load(response)
    if not health.get("accepting_jobs") or health["active_job"] is not None:
        raise SystemExit("Wan is not idle and clear")
    if not api.gpus_idle(api.gpu_sample()):
        raise SystemExit("Rack GPUs are not idle")
    api.require_empty_queue()
    api.engine("/free", {"unload_models": True, "free_memory": True})
    for _ in range(15):
        api.require_empty_queue()
        print(json.dumps(api.gpu_sample()))
        time.sleep(1)
' > "$evidence/calibration.jsonl"
```

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
Reuse the private evidence directory from activation. Start a loopback-only
`kubectl -n ai port-forward svc/wan-video 18080:8080` in a separate terminal.
Keep the producer pause and all admission checks in effect.

```bash
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
      failed) jq -c '{error,reason_code,admission_code,baseline_memory_mib,released_memory_mib}' "$evidence/$label.status.json" >&2; return 1 ;;
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
memory, utilization and temperature, with process ownership samples for
operator diagnosis. The admission code gets the model's child status from the
private sidecar socket. Container PID reports are unnecessary for that decision. Stop the
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
jq -e --argjson max 120 --argjson ceiling "$ceiling" \
  '(.finished_at - .created_at) <= $max and
   (.baseline_memory_mib|type)=="number" and .baseline_memory_mib <= $ceiling' \
  "$evidence/cold.status.json"
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
  --slurpfile load "$evidence/cold.status.json" \
  '.internal and .operation=="unload" and .state=="succeeded" and
    .baseline_memory_mib == $load[0].baseline_memory_mib and
    (.released_memory_mib|type)=="number" and .released_memory_mib <= (.baseline_memory_mib + 4) and
    .released_memory_mib <= $ceiling and .finished_at <= $started' \
  "$evidence/handover.json"
at_wan 8188/queue | tee "$evidence/queue-after-s2v"
```

Every new model load starts by verifying the lease and the engine queue in the
sidecar. It uses the same `/free` request as Foley, rechecks the queue and
records a fresh idle Card A sample before creating the child. Release requires
a fresh idle reading at or below the profile ceiling and no more than 4 MiB
above that baseline, after the supervisor has reaped the child and its process
group. The 4 MiB allowance covers small GPU accounting variations. Record the
observed variation during the first live smoke. The compute
lease covers the proof before ComfyUI submission. Use the UUID samples and the
Wan ledger's prompt ID and timestamps to record which card the engine used.

A normal ComfyUI cache can remain after video. With no journal and no live
sidecar child, cached memory does not prevent later Wan or Foley jobs. Allow
the normal cache to remain after S2V and verify both queue lists are empty
before reload. Reload performs its own cache eviction under the lease. Record
the pre-reload and drained readings and the reload receipt. Stop if ownership
is unclear or the card does not settle within the drained ceiling.

```bash
# Run after observing idle queue and utilization, with at least 150 s remaining.
(( SECONDS - window <= 1650 ))
local_request reload 120 "$(operation load)"
local_request final-unload 30 "$(operation unload)"
jq -e --argjson ceiling "$ceiling" --slurpfile load "$evidence/reload.status.json" \
  '.baseline_memory_mib == $load[0].baseline_memory_mib and
    (.released_memory_mib|type)=="number" and .released_memory_mib <= (.baseline_memory_mib + 4) and
    .released_memory_mib <= $ceiling' "$evidence/final-unload.status.json"
wan_exec test ! -e /workspace/local-model/state.json
at_wan 8080/health
at_wan 8188/queue
(( SECONDS - window <= 1800 ))
```

## Stops and evidence

Stop for active or unknown engine work, a hold, GPU identity or ownership
mismatch, capacity/reserve or hash failure, image or admission denial, lost
telemetry, context or schema failure, exceeded VRAM, host RAM or deadline,
failed unload or insufficient time for handover and cleanup. Read only the
original request ID if an answer is ambiguous. A running job at the deadline
is an incident under the existing Wan recovery procedure. Stop submitting work
and preserve its ID and state.

- Foreign work returns the existing engine code and leaves the model
  reservation intact. Wait for known idle. Malformed queue replies fail closed.
- A live child with a missing journal latches a hold. Preserve the journal and
  pause dispatch for inspection. A sidecar outage returns
  `local_model_unavailable` for attempts requiring residency evidence and adds
  no hold. Restore the listener and recheck residency before using that lane.
- A reaped backend failure, including load or decode failure, timeout or output
  overflow, records its error and both memory readings when release is proved.
  A release failure holds the lane and records the failure reason. Structured
  failures can leave a live child. Use a tracked unload when the lease and
  ownership are clear. Capped text reports `result.truncated: true` and omits
  any partial trailing UTF-8 character.
- Once the sidecar is reachable, no model child or journal remains, both
  queues are empty and the normal rack checks pass, restore Wan/Foley dispatch.
  Ordinary ComfyUI cache is allowed in this state. Keep dispatch paused if
  any hold or unresolved residency remains.

Before rolling back activation, quiesce producers, verify tracked unload and
confirm no child and no journal. Use the verified receipt as proof of release,
or repeat the lease-fenced idle calibration if no load occurred. Revert the
Flux path to the dormant app and repeat its health gate. Preserve the weights,
failed attempts and evidence. Inspect uncertain ownership or an unavailable
sidecar before removing the profile. The fallback requires a new ruling.

Record configured revision and digests, file sizes, available bytes/inodes,
health read-backs, exact request IDs and states, input hashes, complete schema
verdicts and token counts, elapsed cold/reload timings, UUID memory and process
samples, allocation logs, drained baselines, unload receipts and the Wan
engine ledger, final idle proof, refusals and any stopped step. Keep outputs and raw evidence private.
Record source, built image, installed components, usable smoke and human
acceptance as separate facts.
