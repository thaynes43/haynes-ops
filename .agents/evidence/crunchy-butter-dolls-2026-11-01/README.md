# OP345 — Crunch Costs Extra

Tom requested original Barbie-style plastic dolls inspired by his attached frizzy-haired doll photo, sophisticated outfits, two different voices, movement like an unseen person playing, and facial expressions that change only between shots.

## Production

Four five-second shots alternate two original adult fashion dolls in a miniature craft kitchen. The ash-brown-haired doll wears chartreuse brocade; her skeptical expression changes to a startled O-mouth on her return. The other doll wears fuchsia puff-shouldered satin and teal sequins, with an enormous black/lavender beehive; she returns with a wider smug grin.

Built-in `image_gen.imagegen` generated five raster assets: four transparent expression cutouts and one kitchen plate. The original user photo was used for visual inspiration and remains private. Full prompts, mode, image hashes and input-reference hash are retained in `prompts.json` and `asset-manifest.json`. New artwork is private on the existing video PVC rather than committed as public binaries.

Each shot uses one immutable RGBA doll layer. Whole-layer rigid translation/rotation at12 held poses/second makes the toy bob and lean; the camera and kitchen stay fixed. Facial pixels are never animated independently. Expressions switch on hard cuts at5/10/15seconds. These are deliberately rigid cutout puppets; the technique guarantees the requested frozen facial expressions.

Existing Kokoro-82M v1.0 supplies two stock voices verified through the live Wyoming Describe response: `bf_emma` and `af_bella`. Four serial lines, one replacement of the third line for clarity. No voice cloning. Dialogue is not lip-synced because the requested mouths remain static.

1. A: “Can we make this butter squish crunchy?”
2. B: “Obviously. Tiny foam beads. Maximum drama.”
3. A: “One tiny bead. Why does it sound expensive?”
4. B: “That's the sound of luxury. Crunch costs extra.”

A restrained excerpt of the preserved OP344 ControlFoley master sounds before the third line. No background music, title banner or added captions. The first draft's recognizer heard “once” in place of “one”; that draft is preserved locally. The final line is made explicit as “One tiny bead,” with the crunch completed before speech. The second render also uses stronger character-specific rigid puppet motion.

## Source and artifacts

Canonical queue item: OpenProject345 underEpic124, originating from344. Branch `agent/crunchy-butter-dolls`; worktree `/home/dev/work/crunchy-butter-dolls`.

Final local asset root: `/home/dev/artifacts/crunchy-butter-dolls/v2/`. Generated artwork: `a-skeptical.png`, `a-shocked.png`, `b-smug.png`, `b-delighted.png`, `set.png`. Prompt set: `prompts.json`. Movie: `crunchy-butter-dolls.mp4`. Separate audio: `doll-soundtrack.wav`. Individual speech takes, dialogue-only WAV, speech/timing records,480 frame transforms, contact sheet and validation are retained there. The initial draft stays in the parent artifact directory.

Scripts under `scripts/wan-video/`: `doll_speech.py`, `edit_doll_short.py`, `check_doll_dialogue.py`, `preserve_doll_short.py`. Run the editor/transcriber with the existing task-local `/home/dev/.cache/short-edit-venv/bin/python` and `OPENBLAS_NUM_THREADS=2`; codec/filter workers are bounded to2. Source art must be restored from the private preserved production assets before rebuilding; output guards refuse existing stage files.

No deployment, configuration, credentials, GPU power/clock, hardware or service restart changes. No benchmarks, workload interruption or unrelated coordination. One agent; five built-in image calls and five short serial TTS lines. Human viewing/listening approval is not fabricated.

Final validation, delivery URL and preservation evidence follow in the JSON records beside this file.

## Verified delivery

[Private20s film](https://video.haynesops.com/videos/40999984239c44dab681347602c67cb8/file). The existing private ingress returned200 video/mp4,10,727,742bytes; body SHA256matched `ceddb95ee01992a7996881cfd182aba2f50d14f8e1e014eee2d9664d302974cd`.

20.0seconds,1080×1920,24fps,480decodedframes. Final mixed dialogue transcription matches all intended words after punctuation normalization. Audio−20.03LUFS,−5.62dBTP,zero clipped decoded samples. Source RGBA art is invariant within each120-frame shot; both return shots use different fixed expression assets. The driving session inspected the contact sheet. No claim of Tom's listening or viewing approval.

Create-only checksum-verified copies of final movie, PCM24 soundtrack, dialogue-only WAV, four voice takes, five original artwork files, prompt set, source hashes, timeline, frame transforms, contact sheet and validation are on the existing Wan PVC at `/workspace/output/productions/op345/`. Atomic new edited-production ledger40999984239c44dab681347602c67cb8 preserves all earlier histories and assets. The initial draft stays local under the parent artifact directory.
