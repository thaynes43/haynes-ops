# Stage Studio segmentation

CR-34 selects Grounding DINO Tiny followed by SAM 2.1 Hiera Tiny on CPU.
One immutable allocation contains both safetensors files, their allowlisted
processor and tokenizer files, and the image-bound profile. Flux installs
the two policies and the provisioner script. The coordinator creates the
provisioning and serving smoke Jobs individually. [OP460](https://work.haynesnetwork.com/work_packages/460)
· [Profile and fixture thresholds](https://github.com/thaynes43/haynes-studio/blob/main/docs/reference/segmentation-backend-profile.md)

## Pin and reconcile the policy

Merge the Studio source PR first. In its **Studio images** run on main, take
the published `ghcr.io/thaynes43/haynes-studio-segmentation@sha256:<digest>`
for that revision. Record the image byte size and the run's
`studio-segmentation-sbom-<revision>` artifact. Set only `data.image` in
[release.yaml](../../kubernetes/main/apps/ai/studio/segmentation-pending/release/release.yaml)
before merging the serving release for allocation
`segmentation-op460-dino-sam-v1-03`. The release guard refuses the old
measuring digest and verifies the published OCI config for the new digest.
The serving contract pins memory-mapped loads, a 4 GiB request and a
5 GiB limit. Run the blocks below from the merged Ops revision.

```bash
(
  set -euo pipefail
  check() {
    local name=$1
    shift
    if "$@"; then printf 'PASS %s\n' "$name"; else printf 'FAIL %s\n' "$name" >&2; return 1; fi
  }
  check 'Cluster source reconciled' flux reconcile kustomization cluster-apps -n flux-system --with-source
  check 'Segmentation policy reconciled' flux reconcile kustomization studio-segmentation-policy -n ai
  check 'Segmentation policy ready' kubectl -n ai wait --for=condition=ready \
    kustomization/studio-segmentation-policy --timeout=2m
  status=0
  check 'Provision and smoke policy present' kubectl -n ai get ciliumnetworkpolicy \
    studio-segmentation-provision studio-segmentation-smoke || status=1
  check 'Offline render ready' kubectl -n ai rollout status deploy/studio-render --timeout=30s || status=1
  exit "$status"
)
```

Only the download Job has DNS and HTTPS to the two observed publisher
hosts, `huggingface.co` and `us.aws.cdn.hf.co`. Both Job pod selectors and
the CoreDNS peer use `app.kubernetes.io/name`. The smoke Job denies all
ingress and egress. The serve process uses the local Unix socket and the
image's offline runtime.

## Provision one new allocation

**Changed (2026-11-25): the first provisioning Job refused the share.**
`studio-segmentation-provision-01` exited in
one second with `free_file_reserve_too_small` before creating anything. The
Studio share reports a file total but `-1` for free files; the provisioner
treated only "no file total" as an unreported count and compared `-1` with the
reserve. The runbook's own `df` gate had passed, because `df` shows `-` there.
The provisioner now treats a negative or all-ones free count as unreported
(CR-34's accepted case) and still enforces a count that is reported. No
allocation directory was created by the failed Job; a Job's name cannot be
reused, so the measuring retry used suffix `02`. The serving image gets
its own allocation and profile hash.

The Job runs at 1000:1000 on talosw01 with a 1 GiB limit. The downloader
syncs to NFS and drops cached pages every 64 MiB. It rehashes the staged
bytes, syncs the files and publishes the manifest and both verified
directories together. The only writable mount is the existing Studio
jobs subtree. A failed allocation remains private and needs a fresh suffix
for the next attempt. Use the 2 GiB and 64-entry budget with 32 GiB and
1,024 entries in reserve. CR-34 accepts the bounded entry budget when
NFS reports the inode count as unavailable. Published files are mode `0444`
and directories are `0555`, readable by the smoke and sidecar at 10001:10001.

Segmentation takes its pins from the image's two-model profile and publishes
both safetensors sets, their allowlisted auxiliary files and the profile in a
single rename. Matting stages one ONNX graph and its bundled licence. The
larger payload accounts for the 2 GiB budget and 540 s download deadline.
The entry budget covers the two model directories and their auxiliary files.
Segmentation allows exact same-host cache redirects for auxiliary files and
releases read-back pages every 64 MiB to bound the two-model rehash. The
provisioners each ship in a stage-local hashed ConfigMap. The release tests
check that their directory sync and write/cache flush functions match.

```bash
(
  set -euo pipefail
  check() {
    local name=$1
    shift
    if "$@"; then printf 'PASS %s\n' "$name"; else printf 'FAIL %s\n' "$name" >&2; return 1; fi
  }
  stage=kubernetes/main/apps/ai/studio/segmentation-pending
  provision=$(kustomize build "$stage/provision" | yq 'select(.kind == "Job")')
  job=$(yq '.metadata.name' <<< "$provision")
  config=$(yq '.spec.template.spec.volumes[] | select(.name == "config") | .configMap.name' <<< "$provision")
  allocation=$(yq '.data.allocation' "$stage/release/release.yaml")
  image=$(yq '.data.image' "$stage/release/release.yaml")
  status=0
  check 'Flux provision script present' kubectl -n ai get "configmap/$config" -o name || status=1
  check 'Studio byte and reported inode reserve' kubectl -n ai exec deploy/studio-render -c app -- sh -ec '
    df -Pk /studio/jobs | awk "NR == 2 { print; ok = (\$4 >= 35651584) } END { exit !ok }"
    df -Pi /studio/jobs | awk "NR == 2 { print; ok = (\$4 == \"-\" || \$4 >= 1088) } END { exit !ok }"
  ' || status=1
  if (( status )); then exit "$status"; fi
  check 'Provision Job created' kubectl -n ai create -f - <<< "$provision"
  check 'Provision Job completed' bash scripts/studio-segmentation/wait-job.sh "$job" 660 || status=1
  report=$(kubectl -n ai --request-timeout=10s logs "job/$job" -c segmentation | grep '^{' | tail -n 1) || report=
  printf '%s\n' "$report"
  check 'Provision report matches both model pins and release' jq -e --arg allocation "$allocation" \
    --arg image "${image##*@}" '
    .allocation == $allocation and .image_digest == $image and .download_bytes == 846220082 and
    (.profile_sha256 | test("^[a-f0-9]{64}$")) and
    [.models[].files[0].sha256] == [
      "1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3",
      "48c14467e5cf9e51870511feb72c89688e82dd74523142c0538b663e193ac2a7"
    ]
  ' <<< "$report" || status=1
  if (( status )); then exit "$status"; fi
  profile=$(jq -er '.profile_sha256' <<< "$report")
  kubectl -n ai exec -i deploy/studio-render -c app -- python -B - "$allocation" "$profile" "${image##*@}" <<'PY'
from hashlib import sha256
from pathlib import Path
import json
import os
import sys

os.nice(10)
root = Path("/studio/jobs") / sys.argv[1] / "weights"
manifest = (root / "profile.json").read_bytes()
pin = sha256(manifest).hexdigest()
document = json.loads(manifest)
checks = {
    "profile digest": pin == sys.argv[2] == (root / "profile.sha256").read_text().strip(),
    "serving profile identity": document["profile"] == "grounding-dino-tiny-sam2.1-tiny-cpu-v2"
        and document["image_digest"] == sys.argv[3],
    "serving load and memory": document["parameters"]["weight_load"] == "mmap"
        and document["resources"]["serving"] == {"weight_load": "mmap", "request_gib": 4, "limit_gib": 5},
}
for model in document["models"]:
    for entry in model["files"]:
        path = root / model["directory"] / entry["name"]
        digest = sha256()
        try:
            with path.open("rb", buffering=0) as source:
                while block := source.read(1024**2):
                    digest.update(block)
                    if source.tell() % (64 * 1024**2) == 0:
                        os.posix_fadvise(source.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                os.posix_fadvise(source.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
            good = path.stat().st_size == entry["size_bytes"] and digest.hexdigest() == entry["sha256"]
        except OSError:
            good = False
        checks[f"{model['directory']}/{entry['name']}"] = good
for label, good in checks.items():
    print(f"{'PASS' if good else 'FAIL'} {label}")
raise SystemExit(not all(checks.values()))
PY
)
```

**Proven (2026-11-25).** `studio-segmentation-provision-02` ran 11:41:39Z to
11:42:19Z on talosw01 and published allocation
`segmentation-op460-dino-sam-v1-02`: 846,220,082 bytes, profile
`7d39bb26ab9166ad79f7b0ab4f9cd2e7e068de6efbb5048c7b8839346efebf44`. The
read-back for that revision passed for the profile and all 12 files of both models.
[Story 460, comment 10083](https://work.haynesnetwork.com/work_packages/460).

## Run the serving smoke

Reserve an exclusive heavy-job window on talosw01. The idle gate requires
current node CPU at most 10%, node memory at most 25%, the render app
at most 250m CPU, Wan with no active job and a clear shared ComfyUI queue.
Record the readings and hold competing heavy render, Wan, Foley and local
model work for this window.

**Expected.** `studio-segmentation-smoke-03` requests and limits two CPUs,
requests 4 GiB and has a 5 GiB limit. It runs at 10001:10001 with read-only
weights. Plain `studio-segmentation smoke` loads the serving profile once
and sends two of each of the six fixture requests. Startup and each
request keep the 60 s bounds, leaving room within the Job's 900 s deadline.
The Job has zero retries.

```bash
(
  set -euo pipefail
  check() {
    local name=$1
    shift
    if "$@"; then printf 'PASS %s\n' "$name"; else printf 'FAIL %s\n' "$name" >&2; return 1; fi
  }
  endpoint() {
    kubectl -n ai exec "$1" -c "$2" -- python -B -c '
import sys
from urllib.request import urlopen
with urlopen(sys.argv[1], timeout=5) as response:
    sys.stdout.buffer.write(response.read())
' "$3"
  }
  stage=kubernetes/main/apps/ai/studio/segmentation-pending
  allocation=$(yq '.data.allocation' "$stage/release/release.yaml")
  image=$(yq '.data.image' "$stage/release/release.yaml")
  limit_bytes=5368709120
  provision="studio-segmentation-provision-${allocation##*-}"
  status=0
  check 'Provision Job complete' bash scripts/studio-segmentation/wait-job.sh "$provision" 15 || status=1
  provision_report=$(kubectl -n ai --request-timeout=10s logs "job/$provision" -c segmentation |
    grep '^{' | tail -n 1) || provision_report=
  profile=$(jq -r '.profile_sha256 // ""' <<< "$provision_report") || profile=
  check 'Provision profile digest matches this allocation and image' jq -e --arg allocation "$allocation" \
    --arg image "${image##*@}" '
    .allocation == $allocation and .image_digest == $image and
    (.profile_sha256 | test("^[a-f0-9]{64}$"))
  ' <<< "$provision_report" || status=1
  node=$(kubectl top node talosw01 --no-headers) || node=
  render=$(kubectl -n ai top pod --containers -l app.kubernetes.io/name=studio-render --no-headers) || render=
  printf '%s\n%s\n' "$node" "$render"
  check 'Node load within smoke window' awk '
    $1 == "talosw01" && $3 ~ /^[0-9]+%$/ && $5 ~ /^[0-9]+%$/ && $3+0 <= 10 && $5+0 <= 25 { ok = 1 }
    END { exit !ok }
  ' <<< "$node" || status=1
  check 'Offline render app idle' awk '
    $2 == "app" { count++; ok = ($3 ~ /^[0-9]+m$/ && $3+0 <= 250) }
    END { exit !(count == 1 && ok) }
  ' <<< "$render" || status=1
  wan=$(endpoint deploy/wan-video api http://127.0.0.1:8080/health) || wan=
  queue=$(endpoint comfyui-0 app http://127.0.0.1:8188/queue) || queue=
  check 'Wan idle' jq -es 'length == 1 and (.[0] | has("active_job") and .active_job == null)' <<< "$wan" || status=1
  check 'Shared ComfyUI queue empty' jq -es '
    length == 1 and (.[0] | .queue_running == [] and .queue_pending == [])
  ' <<< "$queue" || status=1
  if (( status )); then exit "$status"; fi
  smoke=$(kustomize build "$stage/smoke")
  job=$(yq '.metadata.name' <<< "$smoke")
  check 'Smoke Job created' kubectl -n ai create -f - <<< "$smoke"
  check 'Smoke Job completed' bash scripts/studio-segmentation/wait-job.sh "$job" 960 || status=1
  report=$(kubectl -n ai --request-timeout=10s logs "job/$job" -c segmentation | grep '^{' | tail -n 1) || report=
  printf '%s\n' "$report"
  check 'Exact image, profile and serving load setting' jq -e --arg image "${image##*@}" --arg profile "$profile" '
    .schema == "studio.segmentation-smoke/v1" and .mode == "serving" and
    .image_digest == $image and .profile_sha256 == $profile and
    .profile_id == "grounding-dino-tiny-sam2.1-tiny-cpu-v2" and [.runs[].weight_load] == ["mmap"]
  ' <<< "$report" || status=1
  check '5 GiB cgroup limit and peak' jq -e --argjson limit "$limit_bytes" '
    .cgroup_limit_bytes == $limit and .verdicts.cgroup_allowance == true and
    (.cgroup_peak_bytes | type == "number" and . > 0 and . <= $limit)
  ' <<< "$report" || status=1
  check 'Cold start and memory phases for all twelve calls' jq -e --argjson limit "$limit_bytes" '
    .runs[0] |
    (.cold_load_ms | type == "number" and . >= 0 and . <= 60000) and .verdicts.phase_memory == true and
    (.memory.startup | keys == ["load_dino", "load_sam", "runtime_import", "verify_profile_and_files"]) and
    (.memory.requests | length == 12 and all(.[]; has("source_validation"))) and
    ([.memory.startup, .memory.requests[]] |
      all(.[]; all(.[]; .phase_rss_peak_reset == true and
        ([.peak_rss_bytes, .resident_rss_bytes, .cgroup_resident_bytes, .cgroup_peak_since_start_bytes] |
          all(.[]; type == "number" and . > 0 and . <= $limit)))))
  ' <<< "$report" || status=1
  check 'Resident memory growth is at most 64 MiB' jq -e --argjson limit "$limit_bytes" '
    def stable($first; $second):
      ([$first, $second] | all(.[]; type == "number" and . > 0 and . <= $limit)) and
      ($second <= $first + 67108864);
    .runs[0] |
    .memory.requests as $requests | .memory.passes as $passes |
    $passes.cgroup_resident_bytes as $cgroup |
    .verdicts.memory_stable == true and $passes.tolerance_bytes == 67108864 and
    ($cgroup | type == "array" and length == 2) and stable($cgroup[0]; $cgroup[1]) and
    all(range(6); . as $case |
      $requests[$case] as $first | $requests[$case + 6] as $second |
      ($first | keys) == ($second | keys) and all($first | keys[]; . as $phase |
        stable($first[$phase].resident_rss_bytes; $second[$phase].resident_rss_bytes) and
        stable($first[$phase].cgroup_resident_bytes; $second[$phase].cgroup_resident_bytes)))
  ' <<< "$report" || status=1
  check 'Exact readiness before and after both passes' jq -e --arg profile "$profile" '
    {profile_id: "grounding-dino-tiny-sam2.1-tiny-cpu-v1", profile_sha256: $profile, device: "cpu"} as $ready |
    .runs[0] | .readiness == {initial: $ready, after: $ready} and
    .verdicts.ready_initial == true and .verdicts.ready_after == true
  ' <<< "$report" || status=1
  check 'Both fixture passes and create-only replay' jq -e '
    .passed == true and (.runs[0] | (.cases | length == 12) and
      .verdicts.create_only_replay == true and
      .verdicts.visibility == true and .verdicts.visibility_repeat == true)
  ' <<< "$report" || status=1
  check 'Offline render ready after smoke' kubectl -n ai rollout status deploy/studio-render --timeout=30s || status=1
  exit "$status"
)
```

Keep the final JSON report with the story. Record the cold start, per-case
wall times, per-phase worker peak and resident RSS, and container cgroup
peak, limit and resident bytes. Save both passes' phase samples and the
cgroup resident samples at the end of each pass. The 64 MiB tolerance
allows allocator and page-cache variation. The Job waits stop on `Complete`
or `Failed`.
A failed poll can recover at the next read. On failure or timeout, the
wait prints the Job, pod termination state when available and container logs.
The visible-face and hidden-prop fractions exercise the fixed
synthetic arrangement. Story [452](https://work.haynesnetwork.com/work_packages/452)
owns real fixture quality and the adapter's build-time layout decision.

## Measuring evidence

**Proven (2026-11-25).**
`studio-segmentation-smoke-02` ran 11:43:12Z to 11:44:47Z on talosw01 and
exited 0. The idle gate before it read node CPU 4%, node memory 12%, the
render app at 3m CPU, Wan with no active job and an empty shared ComfyUI
queue.

| Measured | Memory-mapped (first) | Buffered (second) |
|---|---|---|
| Cold load | 23,638 ms, from a cold share | 7,941 ms, page cache warm |
| Job cgroup peak by the end of the run | 2,970,984,448 bytes | 3,707,191,296 bytes |
| Worker RSS peak | 2,850,689,024 bytes | 2,865,315,840 bytes |
| Text-grounded detection and mask | 8,749 and 11,068 ms | 8,905 and 11,334 ms |
| Point, foreground, face, prop masks | 2,386 to 2,539 ms | 2,408 to 2,540 ms |

The cgroup limit was 17,179,869,184 bytes. Every verdict was true, including
byte parity between the two settings and create-only replay. The first cold
load also read the weights from the share. The second started with a warm
page cache, which prevents a comparison of cold-load times.

**Changed.** The first runbook block read the last log line. Python's
multiprocessing resource tracker printed a shutdown warning after the smoke's
report, so the last line was not JSON and the four report checks printed FAIL
although the Job had succeeded. The same four expressions pass on the report
line itself. Each report read now takes the last line that starts with `{`.
[Story 460, comment 10083](https://work.haynesnetwork.com/work_packages/460).

For a later measurement, use `smoke --compare-load-strategy buffered` in
a separate approved Job with at least the 16 GiB measuring limit and a
fresh immutable Job name. It starts a separate inference worker for each
setting and compares PNG bytes and canonical receipts after removing the
recorded experimental load setting.

## Activate in a follow-up

**Expected.** Activate matting first in the fourth deployment pin
([story 967](https://work.haynesnetwork.com/work_packages/967)). Follow the
[matting runbook](studio-matting-deployment.md#activation-retries-and-rollback)
to activate the `studio-render/matting-pending` render split and the matting
sidecar on `studio-worker`. After matting is ready and the segmentation smoke
passes, point the render Flux Kustomization at `studio/segmentation-pending/activation`.
That overlay builds on `studio-render/matting-pending`. It adds the segmentation
sidecar while the queue stays on its matting overlay. The release guard checks
both combinations below. Cap manual render at `--workers 16` with matting and
`--workers 14` with both. Segmentation runs at 10001:10001 with read-only
weights and shares scratch with the render app via fsGroup 1000.

| Container on talosw01 | Matting alone CPU / memory | Matting and segmentation CPU / memory |
|---|---:|---:|
| Offline render app | 18 / 32 GiB | 16 / 32 GiB |
| Queue worker | 4 / 8 GiB | 4 / 8 GiB |
| Matting sidecar | 2 / 10 GiB | 2 / 10 GiB |
| Segmentation sidecar | — | 2 / 5 GiB |
| Both Studio pods | 24 / 50 GiB | 24 / 55 GiB |

The 2026-11-25 read of `talosw01` showed 39 CPUs and 111,129,616 KiB
(105.98 GiB) allocatable. Existing memory requests totalled 55,482 MiB,
and limits totalled 239,424 MiB (233.81 GiB). With both staged companions,
requests would total 67,770 MiB and limits 254,784 MiB (248.81 GiB) at
that baseline. The node cannot sustain all configured peaks simultaneously.
Keep heavy work serialized and check current headroom before activation.

The segmentation overlay gives the pod 150 s on termination so the service
can keep its 60 s request deadline and finish bounded worker recovery and
cleanup. An expired request returns a failed receipt. The client uses a mask
only after a matching successful reply and file read-back.

After GitOps rollout, set `STUDIO_SEGMENTATION_PROFILE_SHA256` to the
manifest hash from the verified provisioning and serving reports. Check the
render rollout and the exact Unix readiness response. The v1 selector names
the model pair. The hash pins the v2 serving manifest.

```bash
(
  set -euo pipefail
  profile=${STUDIO_SEGMENTATION_PROFILE_SHA256:?Set the verified manifest SHA-256}
  [[ $profile =~ ^[a-f0-9]{64}$ ]]
  kubectl -n ai rollout status deploy/studio-render --timeout=30s
  kubectl -n ai exec -i deploy/studio-render -c app -- python -B - "$profile" <<'PY'
import http.client
import json
import socket
import sys

with socket.socket(socket.AF_UNIX) as connection:
    connection.settimeout(3)
    connection.connect("/segmentation/segment.sock")
    connection.sendall(b"GET /v1/ready HTTP/1.0\r\n\r\n")
    with http.client.HTTPResponse(connection) as response:
        response.begin()
        if response.status != 200 or json.loads(response.read(8193)) != {
            "profile_id": "grounding-dino-tiny-sam2.1-tiny-cpu-v1",
            "profile_sha256": sys.argv[1],
            "device": "cpu",
        }:
            raise SystemExit("segmentation readiness pin mismatch")
PY
)
```

For rollback, stop dispatch and revert the render Flux path to its last
known-good revision. Keep the verified immutable allocations and smoke reports
for later comparison.
