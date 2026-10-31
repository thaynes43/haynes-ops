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
and uses Lanczos upscaling. This is explicitly an upscale, not native 1080p
portrait generation. Text stays clear of the bottom controls region.

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

The WAV master targets −18 LUFS and −1.5 dBTP using two-pass loudness processing.
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

Production in progress; final media checks, artifact pointer, GPU summary and
delivery state will be recorded here before story completion.
