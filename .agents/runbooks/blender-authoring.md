# Blender authoring service

## Architecture

Haynes Quest uses image-generated concepts followed by Blender models, rigs, animation, and GLB exports. Run Blender and its MCP adapter together in a dedicated `blender-authoring` workload in namespace `dev`. Their separate image, Flux Kustomization, and artifact PVC let authoring upgrades and restarts proceed without restarting the dev-env agent pod.

The agent connects to `http://blender-authoring.dev.svc.cluster.local:8000/mcp` using native streamable HTTP. The addon's raw command socket stays on `127.0.0.1:9876` inside the authoring pod. MCP, preview bytes, and artifact downloads cross the service boundary; filesystem paths refer to the authoring pod, not the agent pod. No public ingress is provided. Cilium permits port 8000 only from dev-env, and the authoring pod has no external egress, Kubernetes token, or agent credentials.

The standalone image contains Blender 4.5.13 LTS, commit-pinned Blender MCP source/addon, Xvfb/software OpenGL, FFmpeg/ffprobe, glTF Transform, and the Khronos validator. The first deployment requests no GPU. This is offline asset authoring, separate from the game's browser runtime and from future audio-model inference.

The earlier dev-env image build in [PR #2831](https://github.com/thaynes43/haynes-ops/pull/2831) validated the local toolchain but was superseded by Tom's request for independent workloads. Do not activate that image to obtain Blender. [PR #2833](https://github.com/thaynes43/haynes-ops/pull/2833) combines initial Blender/audio MCP registrations and provider-specific agent startup guidance. It remains a held draft: the current boot-rendered MCP ConfigMap triggers a dev-env restart once, so Tom merges it at a natural break. Later Blender image/config upgrades do not touch dev-env resources.

## Authoring and files

Run one active author per scene/work order. Native HTTP requests are serialized, but that does not assign a scene to a particular agent or isolate concurrent authors' intentions. Fresh Sol development agents under Astra receive an empty conversation context and a self-contained Haynes Quest work order with asset IDs, inputs, and acceptance evidence.

Save named `.blend` sources and candidate exports explicitly beneath `/workspace`. The CephFS `blender-authoring-workspace` PVC persists saved files across authoring pod replacement and allows later render Jobs to read explicit saved scenes. An unsaved scene is not durable. The PVC is protected against Flux inventory pruning; it is not itself a backup. Copy important editable sources and approved exports to the project's artifact storage.

Retrieve a saved file using its workspace-relative path, for example:

```bash
curl --fail --output candidate.glb \
  http://blender-authoring.dev.svc.cluster.local:8000/artifacts/candidates/example/candidate.glb
```

The artifact route serves regular files under the workspace and rejects traversal, symlinks, and hidden state. MCP screenshots return image bytes directly. To provide an input reference from dev-env, read its bytes and write them to a named `/workspace/candidates/<work-order>/` file through an explicit Blender Python command; keep the transfer bounded. Never assume a dev-env path exists remotely.

Telemetry, Blender online access, and optional third-party asset/generation integrations are disabled. Use synthetic references for setup validation. Keep family photos and private artifacts out of public git, logs, and published image layers. Tom reviews exact final visual/audio candidate versions before gameplay use; the smoke test's disposable geometry and tones are not approved game assets.

## Operation and validation

`GET /healthz` checks service/desktop process liveness. `GET /readyz` checks Blender responsiveness while idle and avoids interrupting active authoring operations. The workload uses Recreate with a single editor. Save and release the scene before any deliberate upgrade or restart. Declare scoped disruptive authoring work with `declare-activity` when it could trigger monitoring; the agent pod remains running.

All persistent changes go through GitOps. After merging a deployment change:

```bash
flux reconcile kustomization blender-authoring -n dev --with-source
kubectl rollout status deployment/blender-authoring -n dev
curl --fail http://blender-authoring.dev.svc.cluster.local:8000/readyz
```

The image CI builds PRs without publishing and runs its integration smoke in a non-root, read-only container with no external network, a writable `/workspace` and `/tmp`, and 64Mi shared memory. It verifies native HTTP MCP initialization/tool listing, scene/material creation, screenshot bytes, `.blend` save/reopen, GLB roundtrip and Khronos validation, artifact transfers and rejected file escapes, service restart with saved-file recovery, and FFmpeg output. A passing image smoke establishes container behavior, not live cluster connectivity or browser gameplay performance.

After first deployment, exercise the actual ClusterIP MCP endpoint from dev-env with a synthetic work-order directory, fetch its screenshot/GLB artifacts, validate the exported GLB, restart only the new authoring pod after saving, and reopen its saved file from the PVC. Record the running image digest and evidence before declaring service readiness. After initial MCP registration is merged, verify a fresh agent session discovers it. Do not change generated Codex configuration manually.

## GPU and audio follow-up

The main cluster currently advertises one RTX 3090 on `talosw01`, an A2000 on `talosm01`, and an RTX 2000 Ada on `talosm03`. Each has existing consumers; some select a GPU without declaring `nvidia.com/gpu` requests, so scheduler availability is not evidence of idle hardware. Inventory on 2026-09-11 found ComfyUI and ollama-prime on the 3090, Vexa/Ollama/speech workloads on the A2000, and Ollama/Whisper/Immich ML on the Ada. Tom also has a server capable of two 3090s; its intended availability needs establishing before placement.

Use the CPU editor for the initial setup smoke. Add separately scheduled GPU render Jobs when measured previews/bakes justify them, with explicit device assignment and a coordinated usage window or dedicated capacity. Share immutable saved inputs with each Job and write outputs to its own directory. Do not send competing workers to the live editor scene.

FFmpeg prepares audio; learned sound effects come from the separate [audio-authoring service](audio-authoring.md). Haynes Quest DESIGN-008 selects the official optimized Stable Audio Small-SFX CPU route. Its model cache, runtime dependencies, and lifecycle remain independent of Blender and dev-env. No GPU reservation is required for that baseline. The combined initial registration remains held until audio passes its live trial, following Tom's one-restart instruction.
