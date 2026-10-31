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
VAE decoding runs on CPU to reserve GPU headroom. The first preset is a short
demonstration, not a claim about maximum quality or two-card speedup.

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

## Persistence and failures

The dedicated 80 GiB Ceph PVC holds models, jobs and outputs. It has Flux prune
disabled. Existing image model storage and output retention are untouched.
There is no automatic deletion policy or backup for the new volume yet; inspect
capacity before extending usage. Removing the service must preserve this claim.

After an API restart, persisted jobs are never replayed. Queue ownership is
matched by prompt ID and the recorded client ID, including lost acknowledgments.
Owned work is cancelled and marked failed; unknown work causes a hold requiring
inspection. Status and completed downloads remain available during a hold.

Before deployment or an authorized real render, declare activity scoped to
`ai,talosw01,wan-video,open-webui-pipelines`. No benchmarks, repeated renders or
power/clock adjustments are part of this story. Run CPU-only tests with
`python3 -m unittest discover -s scripts/wan-video -v`; no GPU/model is imported.

Delivery evidence is added after rollout and the one authorized clip. Source,
tests, installation, generation and Open WebUI discovery are separate checks.
