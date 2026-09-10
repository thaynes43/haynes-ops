# Blender authoring in dev-env

## Purpose and activation boundary

Haynes Quest uses image-generated concepts followed by Blender models, rigs, animation, and GLB exports. The authoring image adds Blender 4.5.13 LTS, the commit-pinned Blender MCP bridge, Xvfb/software OpenGL, FFmpeg/ffprobe, glTF Transform, and the Khronos validator. This is developer tooling, not the game's runtime or an automatic character-generation service.

The image build and its smoke test can be merged without rolling dev-env. Keep the live HelmRelease image/digest and the shared MCP registration in a separate **held-draft activation PR**: both are part of a pod restart that ends agent sessions. Tom merges that draft at a natural break under the pod's rules. Do not update generated Codex config or manually install a second MCP entry.

## Local controls after activation

```bash
blender-authoring status
blender-authoring start
# Only stop when the current author has saved and released the shared scene:
blender-authoring stop
```

The MCP configuration invokes `blender-authoring mcp`, which lazily starts Blender under an authenticated Xvfb display and then serves stdio MCP. Diagnostics go to stderr/logs. A client disconnect leaves the shared scene running. Nothing starts on the pod's synchronous boot path, and no runtime package/model download is required.

One instance binds **127.0.0.1:9876**. It exposes no Kubernetes Service or public ingress. Scene commands are local code execution; use it only for trusted authoring tasks. The launcher refuses to adopt an unrelated listener and refuses to silently restart an unhealthy living scene because it may contain unsaved work. Inspect the logs and save/recover before an explicit stop.

All agents share the scene. Assign one active author per work order; parallel code/research work is fine, competing Blender edits are not. Start new Astra development agents with empty conversation context and self-contained work orders in Haynes Quest. Match asset/ability IDs and output paths to that work order.

## Files, privacy, and review

- Workspace: `~/.local/share/blender-authoring/`; candidate files under `candidates/`, Blender settings and caches alongside them on the home PVC.
- Runtime identity/lock/logs: `/tmp/blender-authoring-<uid>/` (`1000` in the pod). These disappear at pod replacement; persisted candidate files do not.
- Source/versions: `/opt/dev-env/blender-mcp/REVISION`, pinned upstream source/lock and local venv; `blender --version`, `ffmpeg -version`, `gltf-transform --version`.

Telemetry, Blender online access, and the bridge's optional external asset/generation services are disabled. Reference images and `.blend`/GLB outputs use the same local filesystem, so viewport screenshot paths need no network transfer. Save named editable files explicitly; an unsaved scene cannot survive a pod replacement. Keep family photos/private artifacts out of public git.

Tom reviews exact final visual/audio candidates before they enter gameplay. A successful smoke test is infrastructure evidence, not approval of its disposable test geometry or sound.

## Validation

The image workflow builds PRs without publishing and runs this command inside a fresh, isolated container as UID 1000, with a read-only rootfs, writable home/tmp, no network, and 64Mi shared memory:

```bash
/opt/dev-env/blender-mcp/.venv/bin/python /opt/dev-env/test-blender-authoring.py
```

It checks MCP initialization/tool listing, repeated-start identity, procedural scene/material creation, viewport PNG, `.blend` save/reopen, GLB export/import and independent validation, stop/restart/reconnect, and FFmpeg/ffprobe output. It refuses to disturb an existing authoring state. On a live pod, save/release any active scene before intentionally running this disposable smoke test; it stops the instance it creates when finished.

After Tom activates the image/config, verify a fresh Codex/Claude session lists the Blender MCP server, run the synthetic smoke with no active authoring session, then verify a saved candidate can be reopened from the PVC. Record the actual image digest and results. CI success does not establish live registration or iPad/iPhone/PC gameplay performance.

## Audio generation

FFmpeg prepares audio; it does not supply a learned text-to-sound model. Haynes Quest DESIGN-008 compares Stable Audio Small-SFX CPU inference with ElevenLabs' free/paid hosted API. Model/account selection, gated Hugging Face terms/access, protected download credentials, exact model CDN egress, and a measured quality/speed trial are separate work. No weights, audio-generation account, or paid subscription is included in this image.
