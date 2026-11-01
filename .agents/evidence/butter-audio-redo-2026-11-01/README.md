# OP343 — Softer, individually timed crunchy-butter audio

Tom rejected OP342's soundtrack as pitchy/squeaky, hard on the ears and poorly
aligned with the video. This revision replaces its audio and copies the original
20-second picture stream unchanged. Prior versions and source histories remain
intact. Canonical story #343, Epic #124; prerequisite/parent production OP342.
Budget: 30-minute checkpoint, at most three short serial SFX generations, CPU
editing limited to two workers, one native reviewer.

## Changes

Two new five-second Small-SFX cues were generated and checksum-verified. The sand
cue (seed 3432) was selected over cork/sand (seed 3431) because it had less
concentrated spectral peaks and a broader granular texture. This is an objective
selection aid, not listening approval. The rejected rubber/cellophane cue is unused.

The selected cue is downpitched by a 0.75 ratio (about five semitones), highpassed
at 100 Hz and lowpassed twice at 1600 Hz. Ten short pieces have gentle contact
envelopes and individual placements at visible compressions, pinches and releases.
Release friction is much lower than the main press. `spots.json` records starts,
envelope peaks, ends and source offsets. Timing comes from manual review of
half-second video frames, with editorial estimates between them. This is not
physical Foley capture or a sample-accurate visual-sync claim. The old repeating
five-second envelope is removed. Long holds are silent.

The master targets −27 LUFS / −8 dBTP. Actual encoded output is −29.80 LUFS,
−7.93 dBTP, with zero clipping. Energy above 3 kHz is 0.923% of measured stereo
spectral energy, compared with 79.089% in the rejected mix. Native review found
that the initial metric folded to mono; it was corrected to sum per-channel FFT
powers and rechecked. These measurements establish a lower level and a much
smaller bright band. They do not prove comfort or satisfaction. Human listening
approval remains unverified; Tom judges this new candidate.

## Validation and delivery

- [New film — private network](https://video.haynesops.com/videos/6ad0e50986c844579c7901978139c26d/file)
- Local film: `/home/dev/artifacts/butter-audio-redo/crunchy-butter-soft-audio.mp4`
- Separate WAV: `/home/dev/artifacts/butter-audio-redo/soft-crunch.wav`

All 480 frames and the complete audio stream decode successfully; duration is
20.00 seconds. Demuxed video-packet SHA256 is identical before and after:
`df91615bc95baf9721846342f0937477205d77e8a871a3dc471e1ddfd6bf2bfb`.
Decoded audio during the 4.5–6.0 second hold has zero RMS. Final measurements and
whole-file hash are in `validation.json`. Private ingress returned HTTP 200,
video/mp4, 11,393,968 bytes and the expected SHA256, recorded in `delivery.json`.

Eleven assets were exclusively created and checksum-verified under
`output/productions/op343/` on the existing Wan PVC. A new editorial ledger record
was atomically created. Interrupted preservation requires inspection before retry;
existing assets are never overwritten. Binaries remain private; this directory
contains prompts, seeds, pinned identities, checksums and processing evidence.

Recipes: `scripts/wan-video/redo_butter_audio.py` and
`preserve_butter_audio_redo.py`. Editing uses the existing av/numpy/FFmpeg task venv
and at most two codec/filter workers. Preservation uses standard-library Python
and managed kubectl. Source compilation/diff checks and actual bounded execution
passed. Independent native review and targeted recheck found no remaining
blockers; driver reviewed the final source and evidence. Required CI and advisory
review results are recorded on the PR and canonical story.

No video generation, GPU work, benchmark, configuration edit, deployment, restart,
new access/grant/credential, power/clock/cooling or hardware action. Stop after
this revision's delivery; subjective listening approval is separate.
