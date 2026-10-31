# Shared dual-3090 video worker

Canonical story: OpenProject #339, authorized by Tom in the dedicated GPU/voice
conversation on 2026-10-31. This worker does not replace the existing image or
voice services.

## Placement and execution

`wan-video` in namespace `ai` is pinned to `talosw01` and claims both
`nvidia.com/gpu` resources. Its runtime also selects the exact verified UUIDs:

| Maintenance identity | UUID | VM PCI address observed 2026-10-31 |
| --- | --- | --- |
| Card C | `GPU-d8a856f1-f955-f683-bc24-654561496774` | `01:00.0` |
| Card A | `GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a` | `02:00.0` |

These guest addresses do not establish physical chassis slots. Both configured
and enforced limits were 350 W. This deployment does not set power or clocks.
The reported GPU connection traverses the PCIe host bridge, without NVLink.

The existing pinned ComfyUI image runs Wan2.2 TI2V 5B with native
`MultiGPU_WorkUnits` (MultiGPU CFG Split). CFG is 5, so both conditional and
unconditional denoising passes run on the pair during one job. Each GPU has its
own model copy; this is parallel computation, not transparent 48 GiB pooling.
VAE decoding uses ComfyUI's normal GPU path, with model offloading managed by
ComfyUI. The first unfinished attempt used a CPU VAE override: denoising took
109 seconds across both GPUs, but CPU decoding then took many minutes. That
override was removed before repeating the unfinished demonstration. The first
preset is a short demonstration, not a maximum-quality or speedup claim.

The graph follows Comfy-Org's `video_wan2_2_5B_ti2v` template, adding native
MultiGPU CFG Split. The upstream reference graph is retained alongside the API
graph. Model URLs pin Hugging Face repository revisions and LFS SHA-256 values
in `models.yaml`. The existing provisioner verifies downloads; no model token
is supplied. Wan2.2 is Apache 2.0; the reused ComfyUI image contains its GPL notice.

## Using the service

Select **Wan Video — Dual RTX 3090** in Open WebUI and describe the clip. The
adapter uses the existing authenticated Pipelines connection and returns a
status link followed by a video link. Title/tag tasks do not submit video jobs.
The pipeline is a ConfigMap-mounted module; controller Reloader annotations
ensure code updates recreate the Pipelines pod. Existing modules and histories
stay on their original volumes.

The API is `http://wan-video.ai.svc.cluster.local:8080`, with internal ingress
at `https://video.haynesops.com` (same private ingress tier as existing ComfyUI).
No new public endpoint, API credential, or grant is provisioned.

```bash
curl -H 'Content-Type: application/json' \
  -d '{"prompt":"A red fox walks through a sunlit forest","preset":"preview","seed":43}' \
  https://video.haynesops.com/videos
curl https://video.haynesops.com/videos/JOB_ID
curl -o clip.mp4 https://video.haynesops.com/videos/JOB_ID/file
```

`GET /openapi.json` describes the contract. `preview` is 832×480, 49 frames at
24 fps, 20 steps; `720p` is 1280×704, 81 frames, 20 steps. Only one request may
be active; busy requests return 409 and are not silently queued or retried.
The API does not accept arbitrary graphs, URLs, filenames, resolution, steps,
or shell commands. Each job has a 20-minute deadline. Its record includes
timestamped UUID, VRAM, utilization, power and temperature samples.
Gatus checks `/ready`, including the local engine, exact GPU pair and recovery
hold state. Kubernetes uses `/health` so failed render readiness does not hide
status/download routes. `GpuMissing` expects two GPUs on talosw01.

## Persistence and failures

The dedicated 80 GiB Ceph PVC holds models, jobs and outputs. It has Flux prune
disabled. Existing image model storage and output retention are untouched.
There is no automatic deletion policy or backup for the new volume yet; inspect
capacity before extending usage. Removing the service must preserve this claim.
Fresh provisioning may exceed ten minutes. The Deployment progress deadline is
30 minutes through a Helm post-renderer (app-template has no native field), and
the Helm action allows 35 minutes. These deadlines do not extend a render's
separate 20-minute limit or change the Pod template on an in-progress download.

After an API restart, persisted jobs are never replayed. Queue ownership is
matched by prompt ID and the recorded client ID, including lost acknowledgments.
Owned work is cancelled and marked failed; unknown work causes a hold requiring
inspection. Status and completed downloads remain available during a hold.

Before deployment or an authorized real render, declare activity scoped to
`ai,talosw01,wan-video,open-webui-pipelines`. No benchmarks, repeated renders or
power/clock adjustments are part of this story. Run CPU-only tests with
`python3 -m unittest discover -s scripts/wan-video -v`; no GPU/model is imported.

Delivery evidence is in
[the 2026-10-31 report](../evidence/dual-3090-video-2026-10-31/README.md): one
completed clip, both GPUs active, and final service readiness verified. Card C
peaked at 90°C with thermal slowdown observed. The adapter loaded in Pipelines;
authenticated Open WebUI dropdown confirmation remains unverified. Source,
tests, installation, generation and UI verification are recorded separately.
