# Self-hosted audio authoring

Haynes Quest uses a private `audio-authoring` workload in namespace `dev` to generate sound-effect candidates from original text descriptions. It runs independently of dev-env and Blender. The game later plays prepared, Tom-reviewed files; it does not depend on this service during play.

## Runtime and storage

The image pins Stability AI's optimized Stable Audio 3 CPU source at `779434a908193105335fd8d833418603625b2859` and uses Small-SFX with LiteRT. It contains FFmpeg/ffprobe for inspecting and preparing outputs. No GPU, PyTorch, hosted subscription, or family data is required for setup.

A separate provisioning job downloads the three text-to-audio files from the official anonymous `stabilityai/stable-audio-3-optimized` distribution at revision `da6edc54ddba10bfd79a077102ded687f80e882b`. It records file sizes and SHA256 hashes in `/models/manifest.json`. The runtime mounts that persistent model volume read-only and has no external egress. Model files retain the publisher's Stability/Gemma terms; source licensing does not replace model terms.

The runtime is a single non-root replica with a read-only root filesystem, writable `/tmp`, and its own persistent `/workspace` for job metadata and WAV artifacts. Cilium allows only dev-env to reach TCP 8000. It has no public ingress, Kubernetes token, or agent credentials. The model and workspace PVCs are protected against Flux pruning; this does not make them backups. Preserve important candidates and approved assets in project artifact storage.

## Agent interface

Connect using streamable HTTP MCP at `http://audio-authoring.dev.svc.cluster.local:8000/mcp`. The service provides `generate_sound`, `get_generation`, `cancel_generation`, and `list_generations`. Submission returns an ID promptly. One inference process runs at a time; the bounded queue protects the worker from concurrent authoring bursts. Job metadata records parameters, pinned source/model identity, terminal status, timing, and artifact details. Interrupted work is recorded when the worker restarts.

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

Initial agent registration is bundled with Blender registration and startup provider rules in held [PR #2833](https://github.com/thaynes43/haynes-ops/pull/2833). Its boot-rendered ConfigMap rolls dev-env, so finish both live services and the combined preflight first, then Tom merges once at a natural break. Native tool discovery is verified from a fresh session after that activation. Do not edit generated Codex configuration manually.

## Required live evidence

Exercise the actual service from dev-env: MCP initialization/tool listing, one real synthetic generation, asynchronous status, cancellation, WAV format/duration/checksum, and artifact confinement. Record the actual node, resource limits, generation elapsed time, and measured peak memory. Save the sample for listening review and state whether it has been auditioned. Replace only the audio pod and confirm history/artifacts persist while dev-env retains its UID and restart count.

Source [PR #2840](https://github.com/thaynes43/haynes-ops/pull/2840) is merged. [Main build, container smoke, publication, and signing](https://github.com/thaynes43/haynes-ops/actions/runs/34551460186) passed; the initial runtime image is `ghcr.io/thaynes43/audio-authoring:0.1.0@sha256:2333f862d4108aca69d41c9eaba21df7710a105e8affb4d1a9787e65b5b647a1`. Anonymous registry inspection verified that digest against manifest bytes. Live provisioning and inference remain pending during deployment preparation. Add the pinned deployed image, PRs, and measured trial result here once verified. Final cue quality, browser decoding/lifecycle, and Tom's asset approval remain separate checks in Haynes Quest DESIGN-008.
