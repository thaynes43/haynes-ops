# DREAM DEBT — original Short proof of concept

Canonical work: OpenProject **#340**, following the installed worker in #339.
Tom authorized creative control for one edited Short with connected original
cuts and separately generated audio. No YouTube upload or access change is
included. This directory records the production and preserves its recipe;
media masters remain outside public Git.

## Creative treatment

A city sells perfect dreams, quietly charging its inhabitants their tomorrows.
When its lights finally go out, a tiny flower and an actual sunrise offer a way
out. The edge comes from atmosphere and the predatory transaction metaphor.
There is no graphic violence, sexual content, profanity, real-person likeness,
brand imitation, or depiction of an actual sensitive event.

Eight distinct generated shots tell one story: alley, dream machine, golden
dream, meter, bottled sunrise, blackout, flower beside a power socket, dawn.
Original captions carry the narrative; this version has sound design rather
than spoken narration. Hard cuts move the story forward. Each 81-frame source
is retimed by 10/9 to 90 frames: eight 3.75-second cuts make **30 seconds** at
24 fps, without looping a short clip to fill time.

Delivery is portrait 1080×1920 H.264/AAC. The installed API's native source is
1280×704 landscape. Prompts favor centered subjects; the edit crops 396×704
and uses Lanczos upscaling. Shot seven shifts its crop right to keep the flower
inside the frame. This is explicitly an upscale, not native 1080p portrait
generation. Text stays clear of the bottom controls region. The sixth take
moves up the building but does not reliably extinguish its windows; a short
editorial fade supplies the blackout while its caption remains readable.

## Length and monetization research

Checked 2026-10-31 against these sources:

| Source | Finding and production decision |
| --- | --- |
| [YouTube: three-minute Shorts](https://support.google.com/youtube/answer/15424877) | Square or vertical uploads up to three minutes can be Shorts. Thirty seconds is an editorial choice, not a required minimum or an official optimum. |
| [Mark Studios length study](https://www.markstudios.com/research/youtube-shorts-length-study) | Its October 29 sample of 574 recent Shorts from 97 large channels had median 40 s, mean 43.9 s, middle half 24.75–58 s, with 82% at most one minute. This is a large-channel sample, not a representative platform average or causal evidence of performance. A 30-second complete story fits that observed range. |
| [YouTube: channel monetization](https://support.google.com/youtube/answer/1311392) | Original and authentic work matters; generic, repetitive, mass-produced content can be ineligible. Eight shots, a distinct plot, written captions, purposeful pacing and custom sound design are the creative contribution here. Merely joining AI clips does not certify compliance. Review also considers the channel. |
| [YouTube: advertiser-friendly guidelines](https://support.google.com/youtube/answer/6162278) | Suitability applies to the video and its packaging. This treatment uses fictional dystopian suspense with a hopeful finish, avoiding the listed graphic, sexual, hateful and dangerous-act concerns. This is our suitability assessment, not a YouTube approval. |
| [YouTube: GenAI disclosure](https://support.google.com/youtube/answer/14328491) | Realistic generated scenes require disclosure; disclosure alone does not remove earning eligibility. Recommended upload setting: **AI use = Yes**, with a plain description that the film is fictional and AI-assisted. |
| [YouTube: Shorts monetization](https://support.google.com/youtube/answer/12504220) | The channel must qualify for the Partner Program, accept the Shorts module, and comply with the applicable policies. This PoC does not establish those channel conditions or promise revenue. |

Suggested title: **DREAM DEBT — The City Sold Us Better Dreams**.
Suggested description: “An original fictional sci-fi micro-story about a city
that sells perfect dreams and charges in tomorrows. AI-assisted visuals and
sound effects, original story, captions, editing and sound design. No real
events or people are depicted.” This is a handoff draft, not a publication.

## Audio and rights provenance

The existing Haynes Quest audio-authoring service generates three original SFX
cues: 30 seconds of city ambience, 10 seconds of machinery, and 10 seconds of
dawn shimmer. Only the needed portions are mixed. Service metadata records
prompts, seeds, pinned source/weights, duration and SHA256. The sound design
also contains an original synthesized 48/96 Hz pulse, defined in the edit
recipe. No commercial song, stock recording or creator imitation was requested.

Visual model: Wan2.2 TI2V 5B, Apache 2.0, using the unchanged OP339 pinned graph,
model files and ComfyUI image. Audio model: Stable Audio 3 Small SFX, pinned
weights revision `da6edc54ddba10bfd79a077102ded687f80e882b`, service 0.2.0.
The [pinned audio license](https://huggingface.co/stabilityai/stable-audio-3-optimized/blob/da6edc54ddba10bfd79a077102ded687f80e882b/LICENSE.md)
allows evaluation/testing and includes separate commercial-use registration
and revenue conditions. Their applicability/readiness for Tom's eventual
monetized production has **not** been verified. No account, credential, or
commercial registration is created by this PoC. Model-output ownership terms
are not a guarantee against third-party claims.

The WAV master targets −18 LUFS and −2.5 dBTP using two-pass loudness processing.
The final AAC is independently decoded and measured again; final values belong
in `validation.json`. Objective audio checks do not substitute for human
listening approval, which remains unverified.

## Reproduction and preservation

Recipes are `scripts/wan-video/produce_short.py`, `edit_short.py`, and
`preserve_short.py`. The render recipe has a finite eight-shot list, one active
request, natural cooldown below 62°C between jobs, a story time checkpoint,
and guards against automatic rerun after interrupted submissions. It never
changes power, clocks, cooling, deployment configuration or existing jobs.
CPU editing/decoding uses at most two workers. The existing audio service uses
its already-configured four-thread inference limit, one cue at a time.

The local artifact directory is `/home/dev/artifacts/dual3090-short`. The final
delivery is copied with checksums and exclusive writes into the existing Wan
PVC under `/workspace/output/productions/op340/`. A new record explicitly
marked `kind=edited_production` serves the completed MP4 through the existing
private download API. It records source job IDs and hashes, and does not claim
that a model generated the entire edited film in one inference. Original
render records, media and histories are preserved.

No benchmark, unrelated workload change, public endpoint, or YouTube upload is
part of this production. Hardware/airflow acceptance remains separate from
successful creative output.

## Final evidence

- [Finished film — private network](https://video.haynesops.com/videos/ae3d7abb2d2b4cca90f7bc985252ce0c/file)
- [Production record — private network](https://video.haynesops.com/videos/ae3d7abb2d2b4cca90f7bc985252ce0c)
- Local movie: `/home/dev/artifacts/dual3090-short/dream-debt-short.mp4`.
- Separate WAV master: `/home/dev/artifacts/dual3090-short/dream-debt-soundtrack.wav`.
- Durable service copy: `/workspace/output/productions/op340/`, 30 checked assets.
- [Source PR #4509](https://github.com/thaynes43/haynes-ops/pull/4509).

The final movie is **30.00 seconds**, 1080×1920, H.264 at 24 fps, with stereo
AAC at 48 kHz. All **720 video frames** and the complete audio stream decode.
It is 17,020,552 bytes, SHA256
`d059d699dc40b934160a8fd2e6a8ca0813f634f036a6da79367518b54654c238`.
The existing Pipelines Pod downloaded the final private URL: HTTP 200,
`video/mp4`, matching bytes and SHA256. Private access still requires the
existing local-network path.

The final AAC measures **−18.07 LUFS**, **−1.44 dBTP**, and zero full-scale
samples. The first mux was rejected by our check at −0.66 dBTP after AAC
overshoot. Lowering the WAV master ceiling from −1.5 to −2.5 dBTP fixed the
encoded result; only audio was remixed/remuxed, with no additional GPU render.
Selected portrait frames and the complete eight-panel storyboard were visually
inspected. Human listening approval remains unverified.

All **eight** source jobs succeeded, with **zero replacement renders** and no
benchmark. Their total API running time was 1,045.33 seconds, with additional
natural cooldown between jobs. Every job recorded both GPUs at least 90%
utilization together; across the sequence there were 143 such five-second
samples. Both GPUs individually peaked at 100%.

| Card / UUID suffix | Peak GPU temperature | Peak used VRAM | Peak sampled power |
| --- | --- | --- | --- |
| Card C / `654561496774` | 91°C | 22,124 MiB | 349.41 W |
| Card A / `00aa1924c55a` | 81°C | 11,854 MiB | 349.71 W |

Configured and enforced limits were verified at **350 W on each card before
and after**. No setting changed. These are observations from useful production,
not sustained hardware acceptance, a speedup comparison, or pooled-VRAM proof.
No thermal-flag snapshot was taken in this production; the earlier #339 thermal
finding remains separate history.

Final runtime readback: API ready, accepting jobs, active job null, GPUs idle.
The Wan Pod remains on talosw01 with zero restarts. Wan, Open WebUI, existing
ComfyUI and llama-server HelmReleases are Ready. Existing image/voice workloads,
pairings and histories were not changed by this work.

Independent native review findings were fixed: interrupted-run provenance,
checkpoint placement, and final media assertions. Advisory Claude review found
a PyAV metadata lifetime issue, also fixed and answered. Required source checks
and final merge are recorded on the PR and canonical story. `validation.json`,
`render-report.json`, source `jobs/`, audio metadata and `production.json` contain
the detailed evidence; binary media remains private.
