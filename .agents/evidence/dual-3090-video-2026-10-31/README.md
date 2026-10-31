# Dual-3090 video delivery evidence — 2026-10-31

Canonical work: OpenProject #339. Configuration delivered through PRs
[#4504](https://github.com/thaynes43/haynes-ops/pull/4504),
[#4505](https://github.com/thaynes43/haynes-ops/pull/4505),
[#4506](https://github.com/thaynes43/haynes-ops/pull/4506) and
[#4507](https://github.com/thaynes43/haynes-ops/pull/4507). Installed configuration
commit: `2edd85ad1f4aa5013b3a57fee6d23e47e07026cf`.

## Verified result

- The isolated worker on talosw01 generated one valid 1280×704 H.264 MP4:
  81 decoded frames, 24 fps, 3.375 seconds, 3,124,436 bytes.
- Engine time was 158.70 seconds; API elapsed time was 161.47 seconds, including
  status polling. Denoising was approximately 112 seconds.
- Both exact GPU UUIDs simultaneously reached 100% utilization. Fourteen of the
  33 telemetry samples showed both at least 90% utilized. The worker log also
  confirmed the model clone on cuda:1.
- Card C peaked at 24,106 MiB VRAM and 90°C; Card A at 11,868 MiB and 80°C.
  A live readback during denoising showed Card C's software thermal slowdown
  active at 88°C, 1,515 MHz and 272.23 W. Both limits remained 350 W. The exact
  timestamp of that individual flags readback was not recorded; the full
  utilization/VRAM/power/temperature series has timestamps in `job.json`.
- Both cards reported PCIe Gen4 ×16 in the guest. Physical chassis slots were
  not inferred from guest PCI addresses. The topology reports PHB, without NVLink.
- The download URL returned HTTP200/video-mp4 from the Pipelines pod, matching
  the local copy's SHA-256. All frames decoded, and the middle frame was visually
  inspected: a fox on a sunlit woodland path.

[Play/download the video](https://video.haynesops.com/videos/8c1d4c6eed93491d86b010d4d4c16c77/file)
· [job status and telemetry](https://video.haynesops.com/videos/8c1d4c6eed93491d86b010d4d4c16c77)
· [activity chart](gpu-activity.svg) · [machine-readable report](report.json).

The video remains on the dedicated, prune-protected PVC, rather than in this
public repository. SHA-256:
`25977d3991fbc72511646b6f060abfc6b4f30109a1cb41b7c272e8a580f8e521`.

## Installation and validation

All three model downloads passed the provisioner's pinned SHA-256 checks.
The corrected rollout reused them without another download. Required Flux Local
and Diff Scope checks passed on each delivery PR. Thirteen CPU-only protocol
tests passed. Independent native review findings were fixed and rereviewed;
the final advisory Claude reviews reported no findings.

The first unfinished attempt used CPU VAE decoding, completed dual-GPU
denoising, then spent many minutes decoding on two CPU cores. Its owned job
was cancelled through ComfyUI's per-job API and retained as failed:
`d5bfa3018ad24ae7bc8f958601676ed3`. Removing the CPU override yielded the one
completed clip above. No single-GPU comparison or benchmark was run.

Final checks: the video API was ready, accepting requests, with no active job;
both GPUs were idle and still configured/enforced at 350 W. The wan-video,
Open WebUI, existing ComfyUI and existing llama-server HelmReleases were Ready.
The existing image and voice Pod identities/restart counts were unchanged.

The Open WebUI adapter's ConfigMap mount and controller Reloader annotation
were verified; Pipelines logged `Loaded module: wan_video`. Private video
ingress was verified from Pipelines. The authenticated model dropdown was not
verified: Tom received a phone question, with no answer at this checkpoint.
No credentials were read to bypass that verification gap.

The reused digest-pinned image emitted a Kyverno signature trust-root **audit**
warning; admission allowed it. No image-verification policy or exception changed.

## Boundary and handoff

The activity declaration `act-211028-809625` was ended after runtime validation.
No further renders are scheduled. This demonstrates one usable video workflow;
it does not close the separate hardware/airflow acceptance in OP#221. Retain the
observed Card C thermal slowdown for Tom's next cooling or allocation decision.
No power, clock, hardware, UPS/PDU, credential or grant changes were made.
