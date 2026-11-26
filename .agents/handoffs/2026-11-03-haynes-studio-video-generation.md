# Haynes Studio: video tooling and fresh-session handoff

Prepared 2026-11-03 for Tom and a **fresh Claude Opus 5.5 / high Remote Control session**.
Repository: `thaynes43/haynes-ops`. This is a source/documentation handoff; it starts no session, generation, deployment or publishing job. Preserve the existing interactive conversation, pairings, worktrees and histories.

## Start here

1. Read this repository's `CLAUDE.md`/applicable `AGENTS.md` and this handoff.
2. Use a new story worktree. Do not reuse a finished production's branch or output directory.
3. Read [Haynes Studio saga #362](https://work.haynesnetwork.com/work_packages/362), then discuss the next bounded increment with Tom. No implementation story is currently claimed by this handoff.
4. Inspect existing evidence and recover selected private reference assets as needed. Fresh session volumes do **not** inherit this session's `/home/dev/artifacts` or Python venv.
5. Before any newly authorized generation, check the live services, UUIDs, holds, queues and resources; a historically successful render is not a fresh readiness check. Never submit a render just to prove a plan.

## Project and current state

[Haynes Studio](https://work.haynesnetwork.com/projects/haynes-studio/work_packages), identifier `haynes-studio`, is private, project ID **11**. Saga **#362**, “Repeatable doll-parody Shorts production and winner selection,” has been moved there from Agentic OS (`haynes-engineering`, project ID3). It remains **Ungroomed**.

A read-only check during this handoff confirmed that the project now enables **Saga, Epic and Story**, as well as Task/Milestone/Summary task. The previously reported missing Epic/Story setup is resolved. #362 is still the project's only work package: **no new child epics/stories have been created**. Earlier production stories #339–345 retain their original project/parent, owners, statuses, evidence and holds. Link them as historical prerequisites; do not silently move or reopen them.

Tom's latest direction: develop the successful uncanny fashion-doll angle into a steady stream of shorts, produce a bounded batch, then pick winners. He suggested parody situations inspired by popular media, using his existing subtitle files from **HaynesTower or Bazarr**. No subtitle endpoints, paths, credentials, downloads, parser or index have been discovered/implemented in this conversation. Do not guess them or treat library ownership as publication permission.

Tom chose a dedicated studio project and requested organization before adding epics/stories. Project setup has now landed; **the detailed backlog still needs grooming with Tom**. A planning outline appears below, not an execution queue or approval to generate/upload.

Use the explicitly selected tracker project:

```bash
opwork --project haynes-studio ping
opwork --project haynes-studio show 362
opwork --project haynes-studio search --all --parent 362
```

OpenProject project identifiers and the dev-env operator's `agent-run --project` catalog are separate. Do not assume `haynes-studio` is declared in the operator catalog.

## What actually works

| Capability | Implemented/proven | Limits and evidence |
| --- | --- | --- |
| Dual3090 video | Private asynchronous Wan2.2 TI2V5B through ComfyUI; both3090s participated in one useful render using native MultiGPU CFG Split | Two model copies/parallel CFG passes, **not pooled48GiB**. OP339, [runbook](../runbooks/dual-3090-video.md), [evidence](../evidence/dual-3090-video-2026-10-31/README.md). |
| Multi-shot short | Eight generated shots edited into30s vertical DREAM DEBT with original sound design | Source landscape crops/upscales to1080×1920; not native1080p video generation. OP340/[evidence](../evidence/dual3090-short-2026-10-31/README.md). |
| Toy dialogue | Separate fixed-voice Kokoro speech, generated SFX and a four-shot toy short | Stock voices; speech is separately mixed. OP341/[evidence](../evidence/crunchy-butter-2026-10-31/README.md). |
| Consistent ASMR | Four reference-linked Wan image-to-video portrait takes, clean20s edit, no banner | Approximate continuity, not a general cast-identity system. OP342/[evidence](../evidence/crunchy-butter-asmr-2026-11-01/README.md). |
| Audio revision | Softer timed text-generated Foley with original video packets unchanged | Tom still found the earlier audio harsh/out of sync; accepted delivery state does not imply subjective approval. OP343/[evidence](../evidence/butter-audio-redo-2026-11-01/README.md). |
| Video-conditioned Foley | ControlFoley TV2A, three8s video windows at0/6/12s crossfaded into20s; GPU/process/lease guards | Output was generated through the guarded one-shot runner. HTTP `/free` empty-response compatibility fix subsequently installed and checked; full post-fix HTTP generation was not separately demonstrated. OP344 remains **Needs answer** for listening verdict. [Evidence](../evidence/controlfoley-asmr-2026-11-01/README.md). |
| Fixed-expression doll comedy | Original built-in imagegen cutouts, two Kokoro voices, rigid toy puppetry and expression changes on cuts | Latest doll movie is **CPU-composited cutout animation**, not a Wan-generated talking-doll video. OP345/[evidence and prompts](../evidence/crunchy-butter-dolls-2026-11-01/README.md). |

These are usable, production-specific scripts and preserved proofs of concept. There is no general job-manifest CLI, subtitle-to-parody tool, automated batch/winner UI, release scheduler or publishing integration yet. The built-in imagegen artwork in OP345 was not generated by the pre-existing16GB image node. A future Claude session must discover its available image tools; it must not assume this Codex tool or a paid API credential is available.

### Live metadata read during handoff (no generation)

`wan-video`, `kokoro`, `whisper` and `open-webui-pipelines` each reported one ready Deployment replica. Wan is on `talosw01`; Kokoro and Whisper are on `talosm01`. The Wan PVC is Bound,80Gi. These are metadata observations only, not current audio-quality, end-to-end UI, queue-idleness or GPU-load acceptance.

| Service | Source/runtime |
| --- | --- |
| Wan API/Comfy | `ai/deploy/wan-video`, containers `api`, `app`, `foley`; Comfy `ghcr.io/thaynes43/comfyui:0.39.0-2@sha256:991867f5dafc1eddc9ed70b307d8bf1d2d91c50f53a428a6315e8fd7dbe1a61b` |
| Foley | Same Pod, loopback8189; `audio-authoring:controlfoley-0.1.0-58003addc39ae5ca9e8b56eb4a19c803c121575a-41263355969-1@sha256:cdc31a167095ad7b9af8dc8810ff6e54c28f836ef7963ee7a25eb77f9f2c407a` |
| Kokoro | `ai/deploy/kokoro -c app`, Wyoming loopback10210; Kokoro-82M v1.0, pinned `flight777/kokoro-wyoming-ml` digest46418df6… |
| ASR | `ai/deploy/whisper -c app`, Wyoming loopback10300; image `wyoming-whisper-gpu:3.8.1-3`, configured `nemo-parakeet-tdt-0.6b-v2` |
| Text-to-SFX | Existing Haynes Quest CPU `audio-authoring` in `dev`, Stable Audio3 Small-SFX; [runbook](../runbooks/audio-authoring.md). Separate from the Foley sidecar. |
| Private delivery | `https://video.haynesops.com`, existing Wan API8080 and authenticated OpenWebUI Pipelines integration. Dropdown confirmation in an authenticated OpenWebUI browser remains unverified. Private links can be inaccessible off the authorized local network. |

GPU maintenance identities last verified for the pair: **CardA** `GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a`; **CardC** `GPU-d8a856f1-f955-f683-bc24-654561496774`. Guest PCI addresses observed Oct31 were A02:00.0/C01:00.0, not proof of physical chassis slots. Both had350W limits; no power/clock changes were made. Re-read UUIDs/current limits rather than treating mappings as eternal. CardC previously reached90–91°C with throttling during useful video jobs; do not change hardware/limits or benchmark to prove a new plan.

Wan claims both GPUs. Foley exposes only CardA, stages its models sequentially and shares `/workspace/compute.lock` with the video API; audio/video compute is serialized. Foley accepts0.7–8s windows, waits for cool low-residual idle, has a600s child timeout and88°C sampled stop for its own child only. It rejects unknown work/malformed queues. Text-only SFX/ASMR comfort was a real failure mode: levels and spectral measurements never establish that a sound is pleasant.

## Latest reusable doll cast and finished example

[**Crunch Costs Extra**,20s](https://video.haynesops.com/videos/40999984239c44dab681347602c67cb8/file):1080×1920,24fps,480decodedframes; SHA256 `ceddb95ee01992a7996881cfd182aba2f50d14f8e1e014eee2d9664d302974cd`,10,727,742bytes. Final mixed transcript matched all intended words;−20.03LUFS/−5.62dBTP/zero clipped samples. Tom replied “That's great” and wanted to continue this angle. Do not relabel prior formal listening holds or claim general audience performance from that response.

Private durable bundle: `/workspace/output/productions/op345/` on PVC `ai/wan-video`; editorial ledger `/workspace/jobs/40999984239c44dab681347602c67cb8.json`. Original session copy: `/home/dev/artifacts/crunchy-butter-dolls/v2/` (session-specific).

| Asset/voice | Role |
| --- | --- |
| `a-skeptical.png`, `a-shocked.png` | Ash-brown frizzy hair, pearl earrings, chartreuse brocade; skeptical then startled fixed O-mouth |
| `b-smug.png`, `b-delighted.png` | Deep-brown glossy plastic, huge black/lavender beehive, fuchsia satin/teal sequins; smug then broader grin |
| `set.png` | Blush/lilac miniature kitchen with turquoise foreground craft table |
| `bf_emma` | DollA, British stock Kokoro voice |
| `af_bella` | DollB, American stock Kokoro voice |
| `prompts.json`, `asset-manifest.json` | Full built-in imagegen prompts, reference roles, image/face-region hashes; input photo was not published |
| `speech.json`, `voice-1.wav`…`voice-4.wav` | Text/voice/timing/checksums and original24kHz mono speech |
| `doll-soundtrack.wav`, `dialogue-only.wav` | Final PCM24/48kHz stereo master and separately mixed dialogue |
| `timeline.json`, `shots.json`, `frame-transforms.json` | Four5s shots,480 rigid transforms, timing and immutable source-art identities |
| `validation.json`, `dialogue-transcript.json`, `contact-sheet.jpg` | Decode/levels/transcription/frame and visual-review evidence |

The source photo inspired the material, hair and expression; it is private and need not be copied from another session to reuse the resulting cast. Expressions are immutable within a shot: whole-layer translation/rotation at12 held poses/s inside24fps output. No mouth/eye motion, blinking or facial morphing. Both characters return with different expression art at10/15s. The camera/set is fixed within each shot. This is deliberately stiff puppet movement, not articulated3D character animation.

Original lines: “Can we make this butter squish crunchy?” / “Obviously. Tiny foam beads. Maximum drama.” / “One tiny bead. Why does it sound expensive?” / “That's the sound of luxury. Crunch costs extra.” The third line was clarified after ASR heard “once” in an earlier draft; the first draft remains in the old session's parent artifact directory. A low-level0.85s excerpt of OP344's Foley sounds at10.1s, finishing before the third line at11.35s. No music/banner/captions.

### Recover the existing private cast and audio without running generation

Run from a haynes-ops worktree using an already-authorized Kubernetes identity. This reads only the explicitly recorded production assets, checks SHA256, and refuses existing destinations. It uses the established `kubectl exec` route, not a new ingress/proxy or another identity. If access is denied, report the exact operation; do not seek a workaround.

```bash
python3 - <<'PY'
import hashlib, json, pathlib, subprocess
root = pathlib.Path.home() / 'artifacts/haynes-studio-reference'
remote = ['kubectl','exec','-i','-n','ai','deploy/wan-video','-c','api','--','python3','-c']
read = 'import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())'
evidence = pathlib.Path('.agents/evidence')
bundles = [
    (evidence/'crunchy-butter-dolls-2026-11-01/production.json', 'op345', None),
    (evidence/'controlfoley-asmr-2026-11-01/crunchy-butter-controlfoley-production.json',
     'op344/crunchy-butter-controlfoley', {'crunchy-butter-controlfoley.wav'}),
]
plan = []
for record_path, suffix, selected in bundles:
    record = json.loads(record_path.read_text())
    prefix = '/workspace/output/productions/' + suffix + '/'
    for item in record['assets']:
        path = item['path']; name = path.removeprefix(prefix)
        if not path.startswith(prefix) or not name or pathlib.PurePosixPath(name).name != name:
            raise RuntimeError('Unexpected asset path')
        if selected is not None and name not in selected: continue
        target = root/suffix/name
        if target.exists(): raise FileExistsError(target)
        plan.append((path, target, item['sha256']))
# All existing-target/path checks precede the first write. An interrupted restore
# requires inspection, not deleting history or blindly rerunning the whole script.
for source, target, expected in plan:
    data = subprocess.run(remote+[read,source], capture_output=True, check=True, timeout=60).stdout
    if hashlib.sha256(data).hexdigest() != expected: raise RuntimeError('SHA mismatch: '+source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as handle: handle.write(data)
    print(target)
PY
```

The full bundle includes finished outputs for review. **Do not point a new render at that archive**. Production scripts have create-only guards and intentionally refuse reruns over completed outputs. The archive is an input library, not a resume queue.

## Actual scripts and command sequence

All scripts below already exist under `scripts/wan-video/`; no new generic CLI is claimed. Commands are recipes for separately authorized work, **not instructions to run automatically on reading this handoff**.

### Doll pipeline

Observed working editor environment: Python3.11.2 with `av==18.1.0`, `numpy==2.4.6`, `Pillow==13.0.0`, `imageio-ffmpeg==0.7.0`. The old session has `/home/dev/.cache/short-edit-venv/bin/python`; a fresh pod may not. Restore/install an equivalent user-local venv only within the selected implementation story; no system package manager or credentials are needed for this recipe. Exact package availability on a new platform has not been reproved.

```bash
python3 -m venv "$HOME/.cache/short-edit-venv"
"$HOME/.cache/short-edit-venv/bin/python" -m pip install \
  av==18.1.0 numpy==2.4.6 Pillow==13.0.0 imageio-ffmpeg==0.7.0
```

Historical working sequence (original output directory already contains the results):

```bash
python3 scripts/wan-video/doll_speech.py
OPENBLAS_NUM_THREADS=2 "$HOME/.cache/short-edit-venv/bin/python" scripts/wan-video/edit_doll_short.py audio
OPENBLAS_NUM_THREADS=2 "$HOME/.cache/short-edit-venv/bin/python" scripts/wan-video/edit_doll_short.py render
OPENBLAS_NUM_THREADS=2 "$HOME/.cache/short-edit-venv/bin/python" scripts/wan-video/edit_doll_short.py validate
OPENBLAS_NUM_THREADS=2 "$HOME/.cache/short-edit-venv/bin/python" scripts/wan-video/check_doll_dialogue.py
python3 scripts/wan-video/preserve_doll_short.py
```

For a **new authorized production**, first make job-local copies/changes in its new worktree:

- `doll_speech.py`: change `OUT` from `/home/dev/artifacts/crunchy-butter-dolls/v2` to a fresh job directory, define four `(character, voice, text)` lines, and create the directory. The editor/checker/preserver import `OUT` from here. Restore/copy selected immutable PNG inputs and record their actual hashes.
- `edit_doll_short.py`: change `cue_path` from the old session's absolute OP344 WAV path to the restored reference WAV. Shot durations are currently fixed at4×5s; voice starts are0.5/0.5/1.35/0.5s within each shot and each line must finish0.25s before the cut. Do not truncate speech to fit. New timings or scene counts require a small reviewed source increment.
- `preserve_doll_short.py`: change **all** hardcoded `op345`/`OP345` destination/story references to the new canonical job/story, plus the title. Existing remote production paths are exclusive-create; never republish into OP345. Check source/current naming before execution.
- Keep at most2 CPU/filter/codec workers. The encoder is bounded; a failed/interrupted stage leaves evidence for inspection. No generic transactional resume is implemented.

TTS uses the existing Kokoro Pod's Wyoming listener, not guessed external endpoints: `butter_speech.py` defines the `CODE` helper; `doll_speech.py` sends `SynthesizeVoice(name=...)` via `kubectl exec -i -n ai deploy/kokoro -c app -- python -c ...`. `check_doll_dialogue.py` reuses the existing local ASR helper and transcribes only the produced synthetic movie. Final transcription equality is a useful intelligibility check, not a voice-quality or human-listening approval.

`preserve_doll_short.py` preflights inputs/hashes, writes each asset with `xb`, and atomically links a new edited-production ledger into `/workspace/jobs`. It emits a private `/videos/ID/file` link. The ledger makes an edited CPU movie downloadable through the same API as GPU outputs; it does not claim that Wan generated it.

### Wan video and reference continuity

Source contracts/graphs: `kubernetes/main/apps/ai/wan-video/app/{api.py,helmrelease.yaml,models.yaml}` and the workflow files listed in that directory. See the [runbook](../runbooks/dual-3090-video.md) for actual OpenWebUI/Pipelines integration and failure ownership.

Read-only inspection from an authorized session (no generation):

```bash
kubectl exec -n ai deploy/wan-video -c api -- python3 -c \
 'import urllib.request;print(urllib.request.urlopen("http://127.0.0.1:8080/health").read().decode());print(urllib.request.urlopen("http://127.0.0.1:8080/ready").read().decode());print(urllib.request.urlopen("http://127.0.0.1:8188/queue").read().decode())'
kubectl exec -n ai deploy/wan-video -c api -- nvidia-smi \
 --query-gpu=uuid,name,memory.total,memory.used,utilization.gpu,power.limit,temperature.gpu \
 --format=csv,noheader
```

For a separately authorized, budgeted useful video request on the existing private network:

```bash
curl -H 'Content-Type: application/json' \
 -d '{"prompt":"An original miniature craft scene","preset":"preview","seed":43}' \
 https://video.haynesops.com/videos
# Read the returned ID; do not blindly resubmit after a lost acknowledgement.
curl https://video.haynesops.com/videos/JOB_ID
curl -o NEW_CLIP.mp4 https://video.haynesops.com/videos/JOB_ID/file
```

`preview`:832×480/49frames/24fps/20steps; `720p`:1280×704/81frames/20steps. One active compute lease,409busy,20min video timeout, owned-child cleanup/unknown-work hold. API routes do not accept arbitrary user graphs/paths/URLs. `produce_short.py` is the finite eight-shot OP340 recipe, `produce_butter.py` adapts it for OP341; both contain hardcoded historical output roots and submit useful GPU work when executed. `produce_butter_asmr.py` uses a job-local image-conditioned graph for four portrait continuations (576×1024/121frames) through the existing Comfy engine; inspect its ownership/cooldown logic before adapting. Never turn direct engine access into a way to bypass the shared audio/video lease or unknown-work checks.

### ControlFoley and other audio

Code/runtime documentation: `scripts/controlfoley/{README.md,run.py,service.py,provision.py,Dockerfile,requirements.txt}`. Model source/weights/CLIP/vocoder revisions and SHA pins are in `provision.py` and Dockerfile. About17.4GiB of weights share the Wan PVC. Runtime is offline after provision.

`produce_controlfoley_asmr.py --label UNIQUE --start 0 --duration 8 --seed 3441` submits actual audio to loopback8189 from the API container; it has historical `OUT`, parent video ID `89f6c15276ad4f0b86622563cb8e5547` and an8-attempt cap. `produce_controlfoley_guarded.py --label UNIQUE --start 0 --seed 3441` is the one-shot compatibility producer used for the three proven takes; it executes the corrected service guards without modifying installed files. Neither is a generic queue, cancellation UI or arbitrary-file importer. Do not run either merely as handoff verification.

`edit_controlfoley_asmr.py --labels window-zero window-six window-twelve --name NEW_NAME` joins original0/6/12s windows with2s overlaps, applies gentle mastering and copies the existing20s video stream unchanged; `preserve_controlfoley_asmr.py` saves a new private movie/WAV/provenance. Inspect/change their historical paths before reusing for a new production.

The first HTTP Foley attempt failed because Comfy `/free` returns HTTP200 with an empty body. PR4515 fixed parsing only for that endpoint; PR4533 installed the tested/signed image. Final runtime check observed source SHA match, handled the real empty response successfully, and found the worker ready/idle. Do not describe the earlier failed attempt as successful or silently replay it. Historical first successful take used5636MiB peak allocated CUDA memory and34.848s inference, sampled max65°C; this was one useful take, not a benchmark or a capacity/latency guarantee.

Stable Audio3 Small-SFX is a separate text-to-SFX service. Prompts, job polling, checksums and CPU deployment guidance are in its runbook. Its commercial-license/registration applicability was not established for this PoC. Check actual model/source/platform conditions in a future release story; no monetization guarantee or new account is implied here.

## Existing examples and canonical evidence

| Story | Evidence directory under `.agents/evidence/` | Private result |
| --- | --- | --- |
| #339 | `dual-3090-video-2026-10-31/` | Initial both-GPU proof, see its job/report |
| #340 | `dual3090-short-2026-10-31/` | DREAM DEBT30s; see production record |
| #341 | `crunchy-butter-2026-10-31/` | Toy dialogue16s; see production record |
| #342 | `crunchy-butter-asmr-2026-11-01/` | `/videos/89f6c15276ad4f0b86622563cb8e5547/file` |
| #343 | `butter-audio-redo-2026-11-01/` | `/videos/6ad0e50986c844579c7901978139c26d/file` |
| #344 | `controlfoley-asmr-2026-11-01/` | `/videos/b6d611311a9447edaa21817ea51df5db/file` |
| #345 | `crunchy-butter-dolls-2026-11-01/` | `/videos/40999984239c44dab681347602c67cb8/file` |

Paths above are on `https://video.haynesops.com`. OP345 source/evidence PR4569; ControlFoley PR4513/4514/4515/4516/4533; earlier source/production PR4504–4512. Read their actual evidence rather than extrapolating readiness. Binaries/voices/reference media stay private; public Git stores recipes, prompts, hashes and nonsecret operational evidence. The80Gi PVC had no automated retention/backup policy in the original runbook; recheck capacity and retention before a steady stream. A fresh session cannot rely on local scratch as its durable handoff.

## Next incremental backlog decisions with Tom

These are proposed planning decisions; no epics/stories below have been created.

1. **Agree the hierarchy in Haynes Studio.** Candidate epics: reusable production toolkit/cast; subtitle-inspired parody development; bounded candidate batches and human review; release/feedback. Link OP339–345 as prior evidence while preserving their original tracking/holds. Do not reuse Agentic OS as the new content backlog.
2. **Pick the first usable increment.** Recommended: a manifest-driven version of the successful doll recipe with a new job ID/output root, reusable cast/expressions/voices, one entry command, finite budget, clear status/errors, decode/transcript/levels checks and private delivery. This removes hardcoded OP345 paths before attempting a “factory.”
3. **Choose the first batch size and review rubric.** Proposal:3–5 short candidates, manual winner selection using comedy/recognizability, character consistency, intelligibility, pacing and technical quality. No rate or paid-image budget is yet approved. Avoid broad parallel GPU work; compare creative options rather than benchmark throughput.
4. **Discover subtitle sources read-only.** Ask which service/root only after checking existing authorized stack documentation. Confirm actual paths/formats/languages and scoped access. Keep source references/time ranges private; write original parody situations/dialogue rather than automatically reenacting a verbatim subtitle file. No subtitle acquisition/access setup has occurred here.
5. **Define a small review loop.** A review index/gallery, per-candidate notes, shortlist/reject/winner and reproducible variant IDs are future work. Decide which existing private surface Tom should use, including off-LAN usability. Do not create public exposure to solve convenience.
6. **Separate candidate production from release.** Decide platform/channel, cadence, packaging, current disclosure/rights/model-license checks, and explicit publishing ownership. No upload, scheduler, account, API key, channel integration or audience analytics has been set up by these prototypes.

At pickup, save one bounded Story under an agreed Epic, claim it with the fresh session identity, and name dependencies, allowed files/resources, acceptance evidence, budget/concurrency, artifacts and stop criteria. Split work before it becomes an open-ended production-platform epic in one session. Preserve holds and unknown jobs. No grants, household voice remediation, other session coordination or Haynesnetwork storage maintenance belongs in this handoff's implementation scope.

## Supported fresh remote launcher and relay (documented, not executed)

Locally installed `agent-run v2.45.0`, commit `86cef5bb619bbde29610e3436299a3a4362b7a88`. Syntax checked with `agent-run help run` during this handoff:

```bash
agent-run --repo haynes-ops --agent claude \
 --model claude-opus-5-5 --effort high --interactive --base origin/main
```

`--interactive` is the supported fresh Claude Remote Control session mode. It creates a new session/pod; it is not a fork/resume or reuse of this thread. Help says the Claude Remote Control login is arranged through the supported owner-side flow; this task neither runs login nor modifies credentials. The arranging operator must use its already-supported launch/access route and verify the resulting fresh session/model/effort and phone entry. Command syntax is verified; launch admission/authentication/phone pairing were **not** exercised here. No undocumented host-native relay command is asserted.

Once the operator has the **new** session name, the supported instruction relay is:

```bash
agent-run msg NEW_SESSION_NAME \
 'Read .agents/handoffs/2026-11-03-haynes-studio-video-generation.md from the pushed handoff commit. Report the commit and key limits you read; wait for Tom to choose the first bounded Studio story. Do not generate, publish, change access, reuse another session or start workers merely from this handoff.'
```

The arranging operator supplies the exact pushed commit from the delivery receipt. `agent-run msg` sends to an existing session; it does not itself establish freshness, work authorization or read acknowledgement. The parent/coordinator will arrange the fresh session and verify it actually reads this document. **This handoff task launches and messages no session.**
