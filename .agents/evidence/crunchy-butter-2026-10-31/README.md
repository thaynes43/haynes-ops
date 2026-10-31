# The Butter Lab — family toy-film PoC

Canonical story: OpenProject **#341**, following the installed dual-3090 worker
and the original Short production in #339/#340. Tom requested a playful film
for his children about squishy butter toys becoming crunchy, and asked whether
the existing audio stack can produce dialogue.

## Capability and treatment

Stable Audio 3 Small SFX supplies squishes, squeaks, rattles and crackles. It is
not used as a dialogue model. Live Wyoming `describe` confirmed that the existing
**Kokoro-82M v1.0** service offers fixed voices, including `af_heart` and `am_puck`.
Four normal synthesis requests produced these separate dialogue assets:

| Voice | Line | WAV duration |
| --- | --- | --- |
| af_heart | Can you make butter crunchy? | 1.536 s |
| am_puck | Only in the butter laboratory! | 1.643 s |
| af_heart | That's not butter. That's a tiny rave! | 2.325 s |
| am_puck | Behold! Toastless toast. | 1.835 s |

The voices are separate off-screen narration, with pauses for toy sounds.
Natural expressive acting and lip synchronization are not demonstrated. No
voice cloning, service configuration, model changes or new credentials are
involved. Existing voice requests, pairings and histories are preserved.

An adult-sized lavender glove handles a smiling yellow silicone squishy on a
turquoise craft mat. Four original shots progress from soft squish to colorful
foam-star additions, crunchy squeeze, and a party-hat reveal. A persistent
“SQUISHY TOYS • NOT FOOD” banner identifies the material. This is fictional toy
play, with no eating or children depicted. The original joke and audio mix are
the creative contribution; it is not a publishing or monetization task.

## Recipe and bounds

Source: Wan2.2 TI2V 5B, unchanged existing fixed `720p` API preset and exact
Card C/A UUID pair on talosw01. Four serial 1280×704 sources each contain 81
frames. The edit crops 396×704 to portrait and upscales to 1080×1920, retiming
each clip to 96 frames at 24 fps: **16 seconds total**. This is an upscale,
not native 1080p generation.

`produce_butter.py` uses the existing finite serial runner, source evidence
preflight, cool-idle check and no automatic retry/resume. The runner's original
3200-second checkpoint is unchanged by default; the new wrapper sets 1200
seconds for future reproduction. The currently authorized finite four-shot
process began before that small parameterization; its elapsed time is supervised
within this story's 45-minute checkpoint. No benchmark, power/clock/cooling
change, deployment, restart or access expansion is included.

`butter_speech.py` uses the actual service's local Wyoming protocol and captures
24 kHz mono WAV output. The 16-second dialogue-only track was transcribed once
by the existing `whisper` service, whose actual configured recognizer is
Parakeet TDT. It recovered all four lines, with punctuation differences. Only
our synthetic asset was sent/read; no household conversations or logs were read.
This is an asset intelligibility check, not a latency/load benchmark.

`edit_butter.py` mixes two independently generated ten-second Small-SFX cues,
four speech clips, and a short original synthesized chime. SFX are smoothly
ducked under speech with attack/release ramps. CPU editing/decoding uses at most
two workers. Mastering leaves additional headroom for AAC, and validation
checks the encoded audio rather than relying only on WAV measurements.
The `sfx-*.json` evidence records contain the exact existing MCP `generate_sound`
prompts, seconds, seeds and steps, with downloadable artifact paths and hashes.
Those two cues are authored through the existing tool rather than a second
HTTP client. `check_butter_dialogue.py` retains the bounded transcription recipe;
`--mixed` checks the final movie, and existing transcript records block reruns.

`preserve_butter.py` uses exclusive file creation, transferred SHA256 checks,
and atomic creation of a new explicitly marked `edited_production` record.
Its directory is `/workspace/output/productions/op341/` on the existing Wan
PVC. Prior films, assets and inference records are never overwritten. Media
stays private; source recipes and nonsecret evidence are retained in Git.

Run order for an explicitly authorized new production: serial video recipe,
speech recipe, MCP SFX authoring/download from recorded parameters, edit recipe,
dialogue checks (pure and `--mixed`), then preservation. Editing/checking uses the
existing task venv with `av`, `pillow`, `numpy` and `imageio-ffmpeg`; renderer,
speech and preservation need only Python's standard library and managed kubectl.
Existing artifacts deliberately block automatic render/speech/check reruns.

## Validation and delivery

Production in progress. Final media checks, download pointer, runtime readback
and review status will be recorded here before completing the story. Human
listening approval remains unverified, even when speech recognition matches.
