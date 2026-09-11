# Self-hosted audio authoring

Haynes Quest uses a private `audio-authoring` workload in namespace `dev` to generate sound-effect candidates from original text descriptions. It runs independently of dev-env and Blender. The game later plays prepared, Tom-reviewed files; it does not depend on this service during play.

## Runtime and storage

The image pins Stability AI's optimized Stable Audio 3 CPU source at `779434a908193105335fd8d833418603625b2859` and uses Small-SFX with LiteRT. It contains FFmpeg/ffprobe for inspecting and preparing outputs. No GPU, PyTorch, hosted subscription, or family data is required for setup.

A separate provisioning job downloads the three text-to-audio files from the official anonymous `stabilityai/stable-audio-3-optimized` distribution at revision `da6edc54ddba10bfd79a077102ded687f80e882b`. It records file sizes and SHA256 hashes in `/models/manifest.json`. The runtime mounts that persistent model volume read-only and has no external egress. Model files retain the publisher's Stability/Gemma terms; source licensing does not replace model terms.

The runtime is a single non-root replica with a read-only root filesystem, writable `/tmp` (including `/tmp/audio-xnnpack` weight caches), and its own persistent `/workspace` for job metadata and WAV artifacts. Cilium allows only dev-env to reach TCP 8000. It has no public ingress, Kubernetes token, or agent credentials. The model and workspace PVCs are protected against Flux pruning; this does not make them backups. Preserve important candidates and approved assets in project artifact storage.

## Agent interface

Connect using streamable HTTP MCP at `http://audio-authoring.dev.svc.cluster.local:8000/mcp`. The service provides `generate_sound`, `get_generation`, `cancel_generation`, and `list_generations`. Submission returns an ID promptly. One inference process runs at a time; the bounded queue protects the worker from concurrent authoring bursts. Job metadata records parameters, pinned source/model identity, terminal status, timing, and artifact details. From service version 0.1.1, new jobs also record their originating service version; upgrades retain historical provenance. Interrupted work is recorded when the worker restarts.

Generated paths belong to the remote workspace. Retrieve completed files through the confined `/artifacts/<job-id>/output.wav` route and compare their checksums. Do not assume dev-env paths are shared, and do not expose this command-capable authoring endpoint through public ingress.

Use descriptive original prompts, a reproducible seed, and a small number of candidates per cue ID. The initial service fixes Small-SFX/SAME-S, FP32 diffusion, quantized decoder, four CPU threads, and eight steps by default. Short events may benefit from generating longer context and trimming the desired sound. Keep masters and processing recipes; do not label an infrastructure test cue as an approved game asset.

## Provisioning and upgrades

All persistent changes go through checked GitOps PRs and Flux. Build and smoke-test a new image, publish/sign it, then pin its immutable digest in the deployment and provisioning template. The CI fixture tests the asynchronous control plane without weights or networking; a passing fixture test is not evidence of real inference.

The provision CronJob is suspended and exists as a manual Job template. On first setup, reconcile the GitOps resources and launch it once:

```bash
flux reconcile kustomization audio-authoring -n dev --with-source
kubectl create job -n dev --from=cronjob/audio-authoring-provision \
  audio-authoring-provision-$(date +%s)
```

Check the Job's completion and model manifest before claiming readiness. The service notices atomic manifest publication, verifies the files, and becomes ready without a provisioning restart. Before a later model revision changes this volume, quiesce inference and plan the corresponding runtime/image update; do not overwrite model files beneath active jobs. Runtime `/healthz` reports service liveness; `/readyz` establishes that the pinned model bundle is available. Missing weights should prevent generation without sending the runtime online.

Before replacing an active worker, allow useful work to finish or cancel it explicitly. Declare scoped activity for disruptive tests, replace only the audio pod, then verify history and completed artifacts persist. Never restart dev-env to upgrade this independent service.

Initial Blender/audio registrations and provider-specific startup rules are already staged through [PR #2844](https://github.com/thaynes43/haynes-ops/pull/2844). [PR #2843](https://github.com/thaynes43/haynes-ops/pull/2843) temporarily excluded the two startup ConfigMaps from Reloader first; both changes were verified with an unchanged dev-env pod template, Deployment revision 50, and zero container restarts. Live maps and mounted files match Git. Codex uses native GPT-5.6 Sol subagents at `xhigh`; Claude Code uses Opus.

Held [PR #2833](https://github.com/thaynes43/haynes-ops/pull/2833) removes that temporary exclusion and adds one explicit pod-template marker. Tom merges it at a natural break after preflight; it is the single planned dev-env restart, with no image bump. Native tool discovery is verified from a fresh session afterward. Do not edit generated Codex configuration manually. Changing two ConfigMaps in one unguarded PR would not guarantee one rollout: Reloader v1.4.22 handles their events separately.

## Live validation

Exercise the actual service from dev-env: MCP initialization/tool listing, one real synthetic generation, asynchronous status, cancellation, WAV format/duration/checksum, and artifact confinement. Record the actual node, resource limits, generation elapsed time, and measured peak memory. Save the sample for listening review and state whether it has been auditioned. Replace only the audio pod and confirm history/artifacts persist while dev-env retains its UID and restart count.

Source [PR #2840](https://github.com/thaynes43/haynes-ops/pull/2840), deployment [#2841](https://github.com/thaynes43/haynes-ops/pull/2841), and the verified download-CDN policy [#2842](https://github.com/thaynes43/haynes-ops/pull/2842) are merged. [Main build, container smoke, publication, and signing](https://github.com/thaynes43/haynes-ops/actions/runs/34551460186) passed; the initial runtime image is `ghcr.io/thaynes43/audio-authoring:0.1.0@sha256:2333f862d4108aca69d41c9eaba21df7710a105e8affb4d1a9787e65b5b647a1`. Anonymous registry inspection verified that digest against manifest bytes. Provisioning Job `audio-authoring-provision-1789091504` completed anonymously on 2026-09-11, downloading 2,491,906,768 bytes across the three pinned files and publishing their checksum manifest. The provisioner needs only `huggingface.co` and the verified redirect host `us.aws.cdn.hf.co`; runtime has no external egress. Final cue quality, browser decoding/lifecycle, and Tom's asset approval remain separate checks in Haynes Quest DESIGN-008.

The first real test on `talosw01` used a synthetic magical-chime prompt, 10 seconds, eight steps, and seed 43. Job `9417e7b462c547c0901061a51c212339` completed in 7.329 seconds with 4,151,418,880 bytes peak subprocess RSS. The pod requests 1 CPU/4Gi memory and limits inference to 4 CPUs/16Gi, without a GPU. Its PCM16 WAV is stereo, 44.1kHz, exactly 10 seconds, and 1,764,044 bytes; SHA256 is `b36dd6e28ed14bae0dc67445608e05a80d6f2398ad34cb78ce8f080680ac8096`. Measured peak is -17.28 dBFS with zero full-scale samples. It has not been auditioned or approved for gameplay.

MCP initialization and all four tools, prompt submission/status, active and queued cancellation, WAV download/checksum, and rejected artifact escapes passed against the live service. Completed output is under `/workspace/jobs/<job-id>/output.wav`; temporary dev-env evidence is in `/tmp/quest-audio-live/`. Keep the service/PVC copy when dev-env restarts.

Cache fix [#2845](https://github.com/thaynes43/haynes-ops/pull/2845) passed [container smoke, publication, and signing](https://github.com/thaynes43/haynes-ops/actions/runs/34553801735). Deployment [#2846](https://github.com/thaynes43/haynes-ops/pull/2846) pins both runtime and provisioning template to `ghcr.io/thaynes43/audio-authoring:0.1.1@sha256:370be35723f8a66a4dbfe5f4ce4b4b9e1ff73c74e42d90bb28e942b2712f57bb`, independently verified against registry manifest bytes. Flux applied `a76e2cfae9f1028d7759eb336342c7bdf6e63bd9`. Replacing the audio pod changed UID from `a0f5e3a4-b1b7-4925-964d-80f9dc368f6e` to `f825079a-88b1-44fd-b12a-b414d3fa87cf`; the old completed WAV retained the exact checksum, cancelled history persisted, and historical provenance was unchanged.

A fresh 0.1.1 job, `b9cc5590a151442ea1a16e18b8096643`, repeated the 10-second/seed-43 test in 7.046 seconds with 4,151,181,312 bytes peak subprocess RSS and the identical WAV checksum. The decoder created a 55,196,912-byte cache under `/tmp/audio-xnnpack`; the generation log had no read-only/cache-open errors. The pinned model manifest is unchanged and mounted read-only. Dev-env still has its original UID `5cedae51-83ee-4e29-b3ef-ec96886bf938`, byte-identical pod template, Deployment revision 50, and zero container restarts. A subsequent five-second generation reused the existing cache successfully (job `7ae24d96bfe847ed888f7be2b81c27fc`, 5.796 seconds), without cache-open errors. Both authoring services are ready; only initial native registration activation remains held.
