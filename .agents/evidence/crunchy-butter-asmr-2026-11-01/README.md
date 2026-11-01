# OP342 — Crunchy Butter ASMR

Tom requested a mesmerizing traditional satisfying Short: mostly the same crunchy
butter toy, gentle changes of angle, close crunchy ASMR, and no top title/banner.
Canonical story: OpenProject #342, parent Epic #124; prerequisite OP339/341 Accepted.

## Production recipe

Four finite useful Wan2.2 TI2V-5B reference-conditioned renders, 576×1024,
121 frames at native 24fps, 20 steps, CFG5, seeds34210–34213. First reference is
frame40 of OP341 raw `crunch.mp4`, cropped396×704 and resized576×1024. Each
subsequent reference is the previous take's last frame. These are image-conditioned
continuations with gentle camera prompts, not a reconstructed3D object or guaranteed
360-degree orbit. First120 frames per shot make a20.00s edit, upscaled1080×1920.
No graphics, captions, titles, speech or music are composited.

Audio is one newly generated20s Stable Audio3 Small-SFX cue, seed3421/8steps,
with gentle five-second squeeze envelopes, edge fades,100Hz high-pass/14kHz low-pass,
and loudness mastering targeted at−20LUFS/−3dBTP. This is broad editorial timing,
not physically captured or precisely contact-synchronized Foley. No borrowed recording. Human listening
approval is unverified; objective decode and level measurements are separate.

`produce_butter_asmr.py` supplies a job-local graph to the existing worker helper
in a separate invocation. The running HTTP API, deployed graph, models and config
are unchanged. Runtime input images and graphs are content assets only. Existing
UUID checks, telemetry, owned-job cleanup and20minute watchdog remain in use.
Live HTTP holds are checked before pickup and immediately before submission;
unknown queued/running work stops pickup and is preserved. There remains an
admission race with independent submissions; this does not claim a shared-global
lock. Comfy executes one graph at a time. This dedicated batch had no competing
work observed in the source records.

Exact pair: Card C `GPU-d8a856f1-f955-f683-bc24-654561496774` and Card A
`GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a`, talosw01, two24GiB RTX3090s.
CFG work is split across copies; memory is not pooled. Existing350W limits are
unchanged. Natural idle cooldown between takes. No benchmark, power/clock/cooling
change, hardware action, deployment, restart, credential/grant or public ingress.
Thermal telemetry documents this production only and is not hardware acceptance.

## Evidence and review

Media binaries remain on private worker storage and the session artifact directory;
this public repository contains recipes and nonsecret provenance only. Source jobs,
reference/graph hashes, audio parameters, validation and private production pointer
are recorded alongside this file in this directory. Existing films
and histories are preserved in their original paths.

Native independent static review required a live HTTP hold gate; this was fixed
and rechecked. The known admission race remains documented. Driver reviews contact
frames and source continuity, and validates complete video/audio decoding and levels.

## Completed output

- [Finished20s film — private network](https://video.haynesops.com/videos/89f6c15276ad4f0b86622563cb8e5547/file)
- Local MP4: `/home/dev/artifacts/crunchy-butter-asmr/crunchy-butter-asmr.mp4`
- Separate48kHz stereo WAV: `/home/dev/artifacts/crunchy-butter-asmr/crunchy-butter-asmr.wav`

All four source jobs succeeded, no replacement renders. Both3090s computed
concurrently in every take;52five-second samples had both at≥90% utilization.
Card C/A peaked90/81°C. Configured/enforced limits read back350W each.
This creative output does not establish thermal/hardware acceptance.

Final film:20.00s,480decoded frames,1080×1920 H.264/24fps, AAC48kHz stereo.
Complete video/audio decoding passed. Actual encoded audio measures−22.33LUFS,
−2.45dBTP, zero clipped samples. The quiet master preserves headroom and dynamics.
Driver reviewed first/middle/last contact frames for all four takes: scene/toy
remain recognizable, cuts use adjacent reference poses, no generated/composited
text observed. Angle changes are gentle, with a clearer side view in the latter
half; decoration details soften as the toy deforms. This is not a seamless-loop,
exact3D-identity or precisely contact-synchronized Foley claim. Human listening
approval remains unverified.

28assets were saved with exclusive creation and transferred SHA256 checks under
`/workspace/output/productions/op342/` on the existing Wan PVC. A new atomic
`edited_production` ledger record points to the movie. Existing films and source
histories were not overwritten. Private ingress returned HTTP200/video/mp4,
11,656,833bytes with final SHA256
`927a195531a92dc47753e78b04a9d3bfc313ed0e3471730b721b8eab635fc1a2`.

Runtime ready/idle/accepting jobs; six relevant AI HelmReleases Ready, including
voice/image services. No config, deployment, restart, access, power/clock or
hardware action. Activity `act-001933-315566` ended. Native static review and
required targeted rereview found no remaining blockers; driver reviewed the
small subsequent audio-envelope/metadata/hash-preflight edits. Source syntax
compilation and diff checks passed, plus actual bounded production/edit/preservation.

Recipes are task-specific and intentionally refuse existing render/preservation
records. A new production needs its own authorized story/paths rather than
rerunning this batch. Source generation requires the task venv (`av`, Pillow);
editing also uses numpy/imageio-ffmpeg, at most two CPU workers. Preservation uses
standard-library Python and managed kubectl. Audio authoring uses the existing
MCP tool with parameters in `asmr-main.json`, then a checksum-verified download.
