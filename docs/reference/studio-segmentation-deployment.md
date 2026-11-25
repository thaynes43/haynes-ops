# Stage Studio segmentation

CR-34 selects Grounding DINO Tiny followed by SAM 2.1 Hiera Tiny on CPU.
One immutable allocation contains both safetensors files, their allowlisted
processor and tokenizer files, and the image-bound profile. Flux installs
the two policies and the provisioner script. The coordinator creates the
provisioning and measuring Jobs individually. [OP460](https://work.haynesnetwork.com/work_packages/460)
· [Profile and fixture thresholds](https://github.com/thaynes43/haynes-studio/blob/main/docs/reference/segmentation-backend-profile.md)

## Pin and reconcile the policy

Merge the Studio source PR first. In its **Studio images** run on main, take
the published `ghcr.io/thaynes43/haynes-studio-segmentation@sha256:<digest>`
for that revision. Record the image byte size and the run's
`studio-segmentation-sbom-<revision>` artifact. Set only `data.image` in
[release.yaml](../../kubernetes/main/apps/ai/studio/segmentation-pending/release/release.yaml)
before merging this measuring release. The release guard fetches the exact
digest's OCI manifest and config and checks its resource contract. The
`16Gi` request and limit are allowances for the first measurement.
Run the blocks below from the merged Ops revision.

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

The Job runs at 1000:1000 on talosw01 with a 1 GiB limit. The downloader
syncs to NFS and drops cached pages every 64 MiB. It rehashes the staged
bytes, syncs the files and publishes the manifest and both verified
directories together. The only writable mount is the existing Studio
jobs subtree. A failed allocation remains private and needs a fresh suffix
for the next attempt. Use the 2 GiB and 64-entry budget with 8 GiB and
1,024 entries in reserve. CR-34 accepts the bounded entry budget when
NFS reports the inode count as unavailable.

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
    df -Pk /studio/jobs | awk "NR == 2 { print; ok = (\$4 >= 10485760) } END { exit !ok }"
    df -Pi /studio/jobs | awk "NR == 2 { print; ok = (\$4 == \"-\" || \$4 >= 1088) } END { exit !ok }"
  ' || status=1
  if (( status )); then exit "$status"; fi
  check 'Provision Job created' kubectl -n ai create -f - <<< "$provision"
  check 'Provision Job completed' kubectl -n ai wait --for=condition=complete "job/$job" --timeout=11m || status=1
  report=$(kubectl -n ai logs "job/$job" | tail -n 1) || report=
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
  if (( status )); then
    kubectl -n ai get "job/$job" -o wide
    exit "$status"
  fi
  profile=$(jq -er '.profile_sha256' <<< "$report")
  kubectl -n ai exec -i deploy/studio-render -c app -- python -B - "$allocation" "$profile" <<'PY'
from hashlib import sha256
from pathlib import Path
import json
import os
import sys

os.nice(10)
root = Path("/studio/jobs") / sys.argv[1] / "weights"
manifest = (root / "profile.json").read_bytes()
pin = sha256(manifest).hexdigest()
checks = {"profile digest": pin == sys.argv[2] == (root / "profile.sha256").read_text().strip()}
for model in json.loads(manifest)["models"]:
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

## Measure the first offline smoke

Reserve an exclusive heavy-job window on talosw01. The idle gate requires
current node CPU at most 50%, node memory at most 70%, the render app
at most one CPU, Wan with no active job and a clear shared ComfyUI queue.
Record the readings and hold competing heavy render, Wan, Foley and local
model work for this window. The smoke requests 16 GiB for scheduling and
limits CPU to two and memory to 16 GiB for measurement. The Job has a
900 s total deadline to fit both bounded passes, with no retries. It runs
memory-mapped first and buffered second, in separate child processes.
It compares PNG bytes and canonical receipts with the experimental
load field removed. Startup and each request keep the 60 s bounds.

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
  provision="studio-segmentation-provision-${allocation##*-}"
  status=0
  check 'Provision Job complete' kubectl -n ai wait --for=condition=complete "job/$provision" --timeout=5s || status=1
  profile=$(kubectl -n ai logs "job/$provision" | tail -n 1 | jq -r '.profile_sha256 // ""') || profile=
  check 'Provision profile digest available' test "${#profile}" -eq 64 || status=1
  node=$(kubectl top node talosw01 --no-headers) || node=
  render=$(kubectl -n ai top pod --containers -l app.kubernetes.io/name=studio-render --no-headers) || render=
  printf '%s\n%s\n' "$node" "$render"
  check 'Node load within measuring window' awk '
    $1 == "talosw01" && $3 ~ /^[0-9]+%$/ && $5 ~ /^[0-9]+%$/ && $3+0 <= 50 && $5+0 <= 70 { ok = 1 }
    END { exit !ok }
  ' <<< "$node" || status=1
  check 'Offline render app idle' awk '
    $2 == "app" { count++; ok = ($3 ~ /^[0-9]+m$/ && $3+0 <= 1000) }
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
  check 'Smoke Job completed' kubectl -n ai wait --for=condition=complete "job/$job" --timeout=16m || status=1
  report=$(kubectl -n ai logs "job/$job" | tail -n 1) || report=
  printf '%s\n' "$report"
  check 'Exact image, profile and two tested settings' jq -e --arg image "${image##*@}" --arg profile "$profile" '
    .image_digest == $image and .profile_sha256 == $profile and [.runs[].weight_load] == ["mmap", "buffered"]
  ' <<< "$report" || status=1
  check 'Memory by phase within the 16 GiB measuring allowance' jq -e '
    .cgroup_limit_bytes == 17179869184 and .verdicts.cgroup_allowance == true and
    ([.runs[].verdicts.phase_memory] == [true, true])
  ' <<< "$report" || status=1
  check 'Byte parity across load strategies' jq -e '.verdicts.byte_parity == true' <<< "$report" || status=1
  check 'Synthetic fixtures, read-back and readiness' jq -e '.passed == true' <<< "$report" || status=1
  check 'Offline render ready after smoke' kubectl -n ai rollout status deploy/studio-render --timeout=30s || status=1
  exit "$status"
)
```

Keep the final JSON report with the story. Record both cold starts, per-case
wall times, per-phase worker peak and resident RSS, and container cgroup
peak bytes. The visible-face and hidden-prop fractions exercise the fixed
synthetic arrangement. Story [452](https://work.haynesnetwork.com/work_packages/452)
owns real fixture quality and the adapter's build-time layout decision.

## Set serving memory and activate in a follow-up

Use the measured memory and byte comparison to choose the runtime setting
and size the serving request and limit. Publish that contract in a new
Studio image and pin its digest and measured fields in the Ops release.
Give it a fresh allocation and run a bounded smoke on that exact pin.
The measuring image refuses `serve`. The staged Helm overlay reserves two
of the 20 offline render CPUs and subtracts the measured limit from the
32 GiB offline render ceiling. Cap manual render workers at 16 in this
split. If another companion already draws from that budget, account for
both before changing the render Flux path. Keep the four-CPU queue lane
separate.

Roll out from Git, then verify both containers, the exact profile hash via
Unix readiness and existing render health. For rollback, stop dispatch and
revert the render Flux path to its last known-good revision. Keep the
verified immutable allocations and smoke reports for later comparison.
