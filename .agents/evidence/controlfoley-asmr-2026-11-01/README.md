# OP344 — video-conditioned ASMR audio

Canonical story: OpenProject344, Epic124. Tom authorized ControlFoley using the existing20s crunchy-butter ASMR film after rejecting the preceding text-only sound effects as harsh and out of sync. No original movies, pairings or histories are replaced.

Source/runtime PR4513 merged856a655934d054e15ebdc56d682cb2896344ea7d. Real image tests:14 passed plus offline dependency imports. Published and signed in run41261921965; immutable public audio-authoring variant digest67ca2e97c605d4846b8ea01f991cfe35ab3238a59ffbc7bffdf5380762efe02a.

Deployment PR4514 mergedf517cbc43caf2797827d1378d0e96a77cdfa1033 after required Flux/Diff checks, native review and advisory re-review. Shared flock serializes video and Foley jobs; sidecar sees CardA only. Existing Wan0.39.0-2 image and350W GPU limits retained. All10 model assets totaling18,662,557,852bytes were SHA verified on the existing PVC before the idle-worker rollout.

Production is bounded to8 useful takes, one audio child, CPU2, no paidAPI. Planned windows0–8,6–14,12–20 preserve absolute video timestamps with two-second overlap crossfades; no pitch/time shift. The final20s movie must copy and compare original video packets and decode480frames. Comfort and perceptual synchronization require actual listening; numerical checks alone cannot establish them.

## Delivered candidate

Three useful8s TV2A takes succeeded, seeds3441/3442/3443 at0/6/12s. The first HTTP submission failed before inference because ComfyUI returns an empty200 body from `/free`. PR4515 merged58003addc39ae5ca9e8b56eb4a19c803c121575a fixes that endpoint-specific parsing with regression coverage. The three actual takes used the corrected source through the guarded one-shot producer; no installed code was patched. Shared lease, idle checks, exact GPU identity,88C limit and600s own-child timeout remained in force. Four reservations total, including the failed pre-inference attempt.

Final candidate: https://video.haynesops.com/videos/b6d611311a9447edaa21817ea51df5db/file

Local movie and separate PCM24 WAV: `/home/dev/artifacts/controlfoley-asmr/crunchy-butter-controlfoley.{mp4,wav}`. Create-only checksum-verified copies, raw takes and provenance are under `/workspace/output/productions/op344/crunchy-butter-controlfoley/`; source native Foley jobs retain proxy videos/inference logs under `/workspace/output/foley/`.

Decoded20.0s,480frames; original video packet SHA unchanged (`df91615bc95baf9721846342f0937477205d77e8a871a3dc471e1ddfd6bf2bfb`). MP4 SHA626a3b591a6a40b962785f59260aa3643f365d49e56f3281457335124da5f40a,11,590,164bytes. Master−30.54LUFS,−7.94dBTP,zero clipped decoded samples. Gentle65Hz highpass,−4dB treble shelf,6kHz lowpass; fixed global gain, no pitch/time shift. Numerical checks do not establish pleasantness or perceptual sync.

Preview: https://video.haynesops.com/videos/f20057c27e904088999d32d2b7cf9fdb/file (8s,540×960). Tom was sent a listening preference question; no reply at the time of this record. Subjective acceptance is unverified.

## Remaining runtime gate

Main image run41263355969 is building/testing the source fix. The currently installed long-running HTTP service still has the empty-response bug; guarded one-shot generation works. Do not submit another HTTP audio job until a tested/signed/public replacement image is pinned through GitOps and rolled out while idle. Check canonical OP344 for the latest pipeline and checkpoint state. Hard checkpoint02:10UTC; preserve work and stop then if remaining delivery cannot finish.
