# Immich ML backlog: the nightly "Missing" run

Immich's own nightly tasks (00:00, America/New_York) queue only missing thumbnails and
face clustering (`queue.service.ts` `handleNightlyJobs`, v3.2.4). **Smart Search** (CLIP),
**Face Detection** and **OCR** are queued only when an asset is uploaded, or when an admin
presses **Missing** under *Administration → Job Queues*. Any asset that arrives while machine
learning is down or overloaded is never processed after that. On 2026-10-05 that came to
20,713 assets without CLIP, 19,778 without face detection and 81,482 without OCR (#3413).

The CronJob `immich-queue-missing` (namespace `photos`) presses Missing on those three queues
every night at **01:30**, through the Immich API, with a key no agent ever sees. It runs them
**one at a time** (Smart Search, then Face Detection, then OCR), because the three together do
not fit on the GPU (*Big backlogs and the shared GPU*, below).

## The pieces

| Piece | Where |
|---|---|
| CronJob `immich-queue-missing` | `kubernetes/main/apps/photos/immich/queue-missing/cronjob.yaml` |
| ExternalSecret `immich-queue-missing` → Secret `immich-queue-missing-secret` | `.../queue-missing/externalsecret.yaml` |
| Flux Kustomization `immich-queue-missing` (ns `photos`) | `kubernetes/main/apps/photos/immich/ks.yaml` |
| The API key | 1Password vault **HaynesKube**, item **`appdaemon`**, field **`IMMICH_API_KEY`**, shared with AppDaemon |

The key is Tom's existing **unrestricted** key on his **admin** user, the same one AppDaemon
uses (Tom chose to share it, 2026-10-06). The ExternalSecret fetches only that one field, not
the whole `appdaemon` item. The job needs only these two permissions, so a dedicated key with
just these two ticked would also work:

| Permission | Route | Why |
|---|---|---|
| `job.create` | `PUT /api/jobs/{name}` with `{"command":"start","force":false}` | Starts the queue in Missing mode. It is the only start route in v3.2.4 (`server/src/controllers/job.controller.ts:45-58`); `/api/queues` can only pause, resume and empty a queue. |
| `queue.read` | `GET /api/queues/{name}` | Reads `isPaused` and the counts (`server/src/controllers/queue.controller.ts:33-42`). The response is `{"name", "isPaused", "statistics": {"active", "completed", "failed", "delayed", "waiting", "paused"}}`, checked against the v3.2.4 server code. (`GET /api/jobs` returns the same numbers for every queue, nested under `jobCounts` and `queueStatus`, but it is deprecated since v2.4.0 and needs `job.read`.) |

Both routes are `admin: true`, so a key made by a non-admin user gets `403 Forbidden`
(`server/src/services/auth.service.ts:219-235`). The queue names are `smartSearch`,
`faceDetection` and `ocr` (`server/src/enum.ts:816,818,828`).

For each queue, in the order Smart Search, Face Detection, OCR, the job:

1. **Gates.** It waits until no queue has work (`active + waiting + delayed == 0`), this one
   included. That covers a manual run, or last night's run still draining. A queue that is
   **paused** in Immich is not waited for (it is not using the GPU). If the queue whose turn it
   is is paused, the job logs `SKIP: the queue is paused` and moves on.
2. Sends `start` with `force: false` (Missing).
3. **Polls** `GET /api/queues/<name>` every 30 s until the queue is drained, which is
   `active + waiting + delayed == 0` on two polls in a row, and logs the counts every 5
   minutes. `isPaused` is checked before the counts: BullMQ moves waiting jobs into a separate
   `paused` count while a queue is paused, so a paused queue reads `waiting=0` without being
   drained. A queue paused while it runs is logged and left alone.

4. **Cools down** for 330 s (not after OCR, the last one). The ML worker keeps a finished
   queue's models and CUDA arena loaded until `MACHINE_LEARNING_MODEL_TTL` (300 s) of idleness,
   then exits and frees its VRAM, so starting the next queue sooner would stack two queues'
   memory on the card (found in review of #3439). It also cools down if the gate had to wait for a busy queue (a manual run, or last night
still draining) before it starts. This adds about 11 minutes a night. The
   cooldown is not counted in the next queue's timeout.

Each queue has its own **timeout**, counted from the start of its turn and covering the gate,
the start and the drain: Smart Search 4 h, Face Detection 4 h, OCR 8 h. The 2026-10-05 backlog
took about 17 minutes, 17 minutes and 3.5 hours. The Job's `activeDeadlineSeconds` is 22 h, a
backstop under the 24 h schedule (the next kickoff is the deadline); the per-queue timeouts
add up to 16 h and fire first. `concurrencyPolicy: Forbid` stops the next night's run from
overlapping one that is still going.

A queue that outlasts its timeout fails the run, and **the later queues are not started**,
since that would put two on the GPU. A status call that fails with a 5xx or a transport error
is retried (10 times in a row, 5 minutes, before it fails the run), so an immich-server roll
mid-run does not kill it; any 4xx fails it at once.

Any non-2xx response, a timeout or a lost status connection fails the Job (the log says
`FAIL` and `ABORT`), and `KubeJobFailed` fires. The key is mounted as a curl header file and
never printed.

> **Never send `force: true`.** That is the **All** button, and it deletes before it starts:
> every CLIP embedding, every ML-detected face along with the people built from them, or all
> OCR text. Then it reprocesses the entire library.

## Run it now

```bash
kubectl create job -n photos --from=cronjob/immich-queue-missing immich-queue-missing-manual-$(date +%s)
kubectl logs -n photos -l app.kubernetes.io/name=immich-queue-missing --tail=50
```

A healthy run ends with `immich-queue-missing done … started=N errors=0`. On a big backlog it
takes hours, since each queue drains before the next one starts, so follow it with
`kubectl logs -n photos -f -l app.kubernetes.io/name=immich-queue-missing`. On a quiet night it
takes about 5 minutes (the gate and the drain each need a poll or two per queue). `started=0`
with `SKIP: the queue is paused` lines is healthy too: someone paused that queue on purpose.
If you start a queue by hand in the UI, the nightly run waits for it before it starts anything.

## Is the backlog shrinking?

Count the assets that still lack each result. This only counts timeline and archive assets
that have a preview and are not deleted, which is what the jobs can process:

```bash
kubectl exec -n database postgres16-pgvecto-1 -c postgres -- psql -d immich -c "select count(*) filter (where ss.\"assetId\" is null) clip_missing, count(*) filter (where js.\"facesRecognizedAt\" is null) faces_missing, count(*) filter (where js.\"ocrAt\" is null) ocr_missing from asset a left join asset_job_status js on js.\"assetId\"=a.id left join smart_search ss on ss.\"assetId\"=a.id where a.\"deletedAt\" is null and a.visibility in ('timeline','archive') and exists (select 1 from asset_file f where f.\"assetId\"=a.id and f.type='preview');"
```

Machine learning is one CUDA replica of `immich-machine-learning` on talosm05's RTX A2000
(12,282 MiB), which it shares with ollama-assist02. `kubectl logs -n photos
deploy/immich-machine-learning | grep -c CUDAExecutionProvider` confirms ML is still on the GPU.
Since 2026-10-06 a VRAM guard keeps ML inside its share of that card under any load
(*The VRAM guard*, below).

## Big backlogs and the shared GPU

A light load costs ML about 1.5 GB of VRAM. A big backlog in all three queues at once costs
far more. Measured on 2026-10-06, the first run against the #3413 backlog behaved like this:

| Phase | talosm05 VRAM (`nvidia_smi_memory_used_bytes`) | Notes |
|---|---|---|
| ML idle, ollama only | 7,027 MiB | ollama-assist02's `qwen3.5:9b`, loaded `Forever` |
| All three queues running | **11,900 of 12,282 MiB** within a minute | ML logged CUDA `Failed to allocate memory` about 150 times in 10 minutes, mostly in facial recognition and OCR. The GPU reached 92 °C. |
| OCR paused; Smart Search and Face Detection draining | 10,488 to 11,636 MiB | ML gives back little arena memory while it is busy. The errors stopped about 7 minutes after the pause. |
| ML idle for 5 minutes | 7,027 MiB | ML logs `Shutting down due to inactivity` and frees all of its VRAM |
| OCR alone (concurrency 1) | 8,762 MiB | about 400 assets a minute |

Smart Search (20,713) and Face Detection (19,778) drained in about 17 minutes. An asset whose
job hit an out-of-memory error stays missing, and the next nightly run queues it again, so
nothing is lost. ollama-assist02 kept working because its model and context are allocated
when it loads. If ollama restarted while ML held the card, though, it might not fit on the
GPU any more.

**That is why the job runs the queues one at a time.** Only one ML workload is on the card
besides ollama (the 330 s cooldown between queues lets ML unload the last one), so the peak is
the highest of the three, not their sum. Since 2026-10-06 nothing manual is needed for a big backlog: the job does what the
2026-10-06 recovery did by hand, which was to run OCR on its own.

One queue at a time turned out not to be enough. In a serial run the same day, Face Detection
alone, for just 49 assets, took the card to 11,448 MiB (ML about 4.4 GB), and OCR alone logged
a CUDA out-of-memory error. Uploads and library imports also run all three queues at once,
whatever the nightly job does. The VRAM guard (next section) is what keeps the card safe now.
The serial order and cooldown stay, because they keep the GPU quieter for voice.

If you start queues by hand, do the same: start one, wait for it to drain, then start the
next. Pausing a queue under *Administration → Job Queues* (or `PUT /api/queues/<name>` with
`{"isPaused": true}`, or `false` to resume) holds it back; the nightly job leaves a paused
queue alone and does not wait for it. That route needs `queue.update`. The shared unrestricted
key has it, but a dedicated key with only `job.create` and `queue.read` would get a 403. An
agent never handles the key, so it makes this call from a one-off Job in `photos` that mounts
`immich-queue-missing-secret` and runs `curl --fail-with-body -sS -X PUT -H
@/secret/api-key-header -H 'Content-Type: application/json' --data '{"isPaused":true}'
http://immich-server.photos.svc.cluster.local:2283/api/queues/ocr`. Copy the CronJob's pod
spec and change only the command. `--fail-with-body` makes a rejected call (a 403, say) fail
the Job instead of completing it, so check that the Job reached `Complete` and that the logged
response shows the `isPaused` value you asked for. A resume that silently failed leaves the
queue paused for good. **A pause that is never resumed quietly stops that queue**, and the job
log shows `SKIP: the queue is paused` every night. Once the backlog is gone, a nightly run
finds only a handful of assets and none of this applies.

A few assets may never clear, for example an asset whose preview cannot be decoded. The job
queues them again every night. That costs little, but if one count stops falling well above
zero, look at that queue's failed count in the job log and at the immich-server log.

## The VRAM guard

**What it bounds.** With the guard, ML's own GPU memory is at most about **4.2 GB** under any
load: the CUDA context and all six models' weights (about 1.1 GB), plus one model run of at most
**3,072 MiB**. Next to ollama-assist02's 7,031 MiB, the card stays under about **11.3 of
12.3 GB**, which keeps at least 1 GB free. The guard also keeps ML's GPU busy at most a quarter
of the time, so voice answers slow down by about 15% during an import instead of 70%.

**Why Immich's own settings cannot do this.** Immich ML v3.2.4 creates every CUDA session with
fixed options, `{"arena_extend_strategy": "kSameAsRequested", "device_id": ...}`
(`machine-learning/immich_ml/sessions/ort.py:134-135`), and no environment variable reaches
them. `MACHINE_LEARNING_MODEL_ARENA` only switches the CPU arena (`ort.py:187`). ONNX Runtime's
CUDA arena never returns memory to the card, so each session grows to its high-water mark and
stays there. OCR's input shape changes with every image, so that mark keeps rising. By default
ML also runs one request per CPU at a time (`request_threads = os.cpu_count()`, `config.py:64`;
20 on talosm05), and each of those holds its own working memory.

**How it works.** Three parts, all in
`kubernetes/main/apps/photos/immich/machine-learning/`:

| Setting | Where | Effect |
|---|---|---|
| `MACHINE_LEARNING_REQUEST_THREADS=1` | `helmrelease.yaml` env (Immich, `config.py:64`, used at `main.py:60-63, 215-219`) | One request at a time. The rest wait in ML's queue. `/ping` is not in that pool. |
| `MACHINE_LEARNING_MAX_BATCH_SIZE__FACIAL_RECOGNITION=8`, `__OCR=6` | `helmrelease.yaml` env (Immich, `config.py:40-42, 74`; `models/facial_recognition/recognition.py:32-33`; `models/ocr/recognition.py:41-42`) | On CUDA, face recognition has no batch limit by default (`recognition.py:84-92`), so a 61-face photo was one 61-face batch. OCR's 6 is the code default, pinned here. |
| VRAM guard `vram-guard/sitecustomize.py` | ConfigMap `immich-ml-vram-guard`, mounted at `/opt/immich-ml-vram-guard`, first on `PYTHONPATH` | Python imports `sitecustomize` at start-up. The guard hooks the first `import onnxruntime` and wraps two public onnxruntime calls. Every CUDA session gets `gpu_mem_limit` = `IMMICH_ML_CUDA_MEM_LIMIT_MB` (3072), the hard cap on that session's arena. Every run on a CUDA session sets the run option `memory.enable_memory_arena_shrinkage=gpu:0`, so ONNX Runtime frees all unused arena memory after each run (onnxruntime 1.26.0 `inference_session.cc:3226-3232, 3347-3349`, `bfc_arena.cc:488`). Runs take one process-wide lock (one model on the GPU at a time). After each run the guard waits `(1/d - 1)` times the run's length, with `d` = `IMMICH_ML_GPU_DUTY_CYCLE` (0.25, so three times as long, at most 30 s). |
| `COLUMNS=200` | `helmrelease.yaml` env | ML's log formatter (Rich) wraps at 80 columns without a terminal, which split `CUDA failure 2: out of memory` across lines. The alert matches whole lines. |

A run that would need more than the 3,072 MiB cap fails inside ONNX Runtime (`Failed to
allocate memory … Available memory of …`). The other processes on the card are never touched.
The largest run measured is OCR detection on a 1:30 scrolling screenshot (1170 × 35,100 px,
which OCR sees as 736 × 22,080), at about 2.8 GB. The library's most elongated image is 8:1.
An image more extreme than about 1:33 would fail OCR every night, and
`ImmichMlCudaOutOfMemory` would say so.

The image's own `PYTHONPATH` is `/usr/src`. An Immich upgrade that changes it, or moves
Python, silently drops the guard, which is why `ImmichMlVramGuardInactive` exists. After an
upgrade that changes onnxruntime, run the guard's checks in the new image (CPU only, safe in the
live pod):

```bash
cd kubernetes/main/apps/photos/immich/machine-learning/vram-guard
kubectl exec -i -n photos deploy/immich-machine-learning -c app -- sh -c 'mkdir -p /tmp/vg && cat > /tmp/vg/sitecustomize.py' < sitecustomize.py
kubectl exec -i -n photos deploy/immich-machine-learning -c app -- sh -c 'cat > /tmp/vg/test.py && cd /tmp && PYTHONPATH=/tmp/vg:/usr/src CUDA_VISIBLE_DEVICES= python /tmp/vg/test.py; rm -rf /tmp/vg' < test_sitecustomize.py
# expect: ALL OK <onnxruntime version>
kubectl logs -n photos deploy/immich-machine-learning | grep immich-ml-vram-guard
# expect, once ML has loaded models: "active (onnxruntime …)" and one "guarding CUDA session …" per model
```

Since #3449, pods log `ERROR: init 250 result=11` once at start, right before the guard's
`active` line (it has no newline of its own). The text comes from the NVIDIA driver's
`libnvidia-sandboxutils.so` (format `%s %d result=%d`, next to its MIG messages; the A2000 has
no MIG). The exact trigger was not found. It is harmless: CUDA sessions load and run normally
after it, and the After load test had 0 errors. The guard's alert matches
`immich-ml-vram-guard ERROR`, not this line.

To switch the guard off for a test, set `IMMICH_ML_VRAM_GUARD: "false"` in the HelmRelease.
Never leave it off: the 2026-10-06 numbers below are what happens.

### Measured (2026-10-06, the load test below)

Same test, same images: 4 minutes at Immich's default job concurrency (Smart Search 2, Face
Detection 2, OCR 1), then 4 minutes at 4/4/4. "Before" and "After" ran against the live
Deployment, before and after #3449. The middle column is a trial server with the guard but no
duty cycle. Card memory comes from `nvidia-smi` every 2 s, and every 200 ms for the spikes.
Voice latency is the median of a probe every 15 s. For ollama it is the server's own
`prompt_eval_duration + eval_duration`, so the test client's time is not counted.

| | Before (live, unguarded) | Trial: guard, no duty cycle | **After (live, as shipped)** |
|---|---|---|---|
| talosm05 card peak | **11,902 of 12,282 MiB** within the first minute | 8,608 MiB | **8,654 MiB**; 10,694 MiB at 200 ms, one OCR spike |
| Failed requests (5xx) | **3,497 of 4,775** (73%), all CUDA out-of-memory | 0 of 1,822 | **0 of 445** |
| ollama-assist02 answer, server time (idle 1.25-1.30 s) | 1.35 / 1.32 s, because ML failed fast and did little GPU work | **2.11 / 2.29 s** (+70-80%) | **1.46 / 1.53 s** (+14-19%) |
| whisper STT on talosm01 (3.8 s command, idle 56-59 ms) | 58 / 54 ms | 56 / 68 ms | 80 / 61 ms (ML is not on that card; the difference is noise) |
| GPU utilisation, max temperature | 0-1%, 82 °C | 56-62%, 92 °C | 15-24%, 90 °C |
| Requests per minute, all three types | n/a, most failed | 267 / 188 | 65 / 46 |

The two figures in each cell are the default phase and the 4/4/4 phase. The cap came from a
trial at 2,048 MiB, where OCR detection of the 1:30 screenshot failed four times ("Available
memory of 387667456 is smaller than requested bytes of 390021120"). At 3,072 MiB it passed,
peaking at 10,024 MiB on the card. Requests per minute fell by about 4x, which is the duty
cycle doing its job. A 2,000-photo iCloud import needs about 6,000 requests, roughly 1.5-2 hours.
A Smart Search text query that arrives during an import waits behind the requests already
queued, so it can take 10-40 s until the import is done.

Even at 15-24% utilisation the card reached 88-90 °C: talosm05's A2000 runs hot. A long
import may raise `GpuHot` (warning) for a while. That is the card's cooling, not the guard.

Voice outliers that are not ML: ollama answers one request at a time, so a voice request waits
behind AppDaemon's camera-vision requests. In a 5-minute window with no ML load at all, the
probe still saw 5-7 s answers. Once, in the recovery phase of the After run, one probe
waited 106 s with ollama logging nothing, and it did not happen again in 95 more probes. Both
are tracked in #3450.

**A second ollama slot is not possible for this model.** `OLLAMA_NUM_PARALLEL` is ignored for
`qwen3.5:9b`: ollama forces one slot for the `qwen35` architecture (and `qwen3next`, `qwen3vl`,
`mllama`, `lfm2`, `nemotron_h`) and logs `model architecture does not currently support
parallel requests` (`server/sched.go:510-515` in v0.35.1, unchanged in v0.40.0 and on `main`
as of 2026-10-06). The model's `model_family` is `qwen35`. Voice therefore waits for whatever
is already running, and the only lever on this card is how much vision work is queued ahead
of it. Measured on 2026-10-06 from a Job in `ai` (ML idle; voice probe = the load test's
~1,020-token Assist prompt, one at a time; vision = AppDaemon's payload shape, a fresh
1920 × 1080 frame plus a ~470-token instruction, 2,667 prompt tokens, about 72 out):

| | Voice answer, client wall time | Vision call, server time |
|---|---|---|
| Idle | 1.4-1.6 s | n/a |
| 5 vision calls sent at once (an unserialized AppDaemon burst) | **37.2 s** for the probe that arrived during the burst | 9.1-9.8 s each, last one answered after 49 s |
| 5 vision calls sent one after another (AppDaemon serialized) | **6.4-7.5 s** (6 probes); bounded by one vision call plus its own 1.5 s | 9.3 → 10.6 s, rising as the card heats |

An image ollama has already seen is much cheaper (2.4 s), because ollama caches image
embeddings, so a load test must use a fresh image for every call. Card memory stayed at
7,031-7,037 MiB through both bursts: llama-server allocates its context and compute buffers
when the model loads.

### Why one replica, on talosm05 only

A second replica on talosm01's A2000 (whisper, speech-to-phrase, kokoro, vexa-whisper) was
tested on 2026-10-06 with a trial server and rejected:

* **It cannot be bounded safely.** talosm01's 14-day peak is 9,590 MiB, which leaves about
  2.1 GB below the 0.5 GB slack line. ML's guarded worst case is about 4.2 GB, and one OCR run
  alone can need 2.8 GB. With a 1,024 MiB cap the trial already failed two extreme OCR
  requests, and the card still reached 10,385 MiB with today's 8,570 MiB baseline.
* **It is not free for speech-to-text.** Whisper (Parakeet, a 3.8 s command) answered in 54 ms
  median before and 61-66 ms during the load (max 104-112 ms against 63 ms), so about +10 ms.
  That is small, but not zero.
* **Throughput is not needed.** Imports come a couple of times a month and nothing waits on
  them.

If it is ever reconsidered: immich-server's `machineLearning.urls` is **failover only**. It
tries the healthy URLs in order and returns the first success
(`server/src/repositories/machine-learning.repository.ts:162-188`), so a second URL takes no
load while the first one works. One Service in front of two pods does spread load, per TCP
connection: immich-server's `fetch` keeps connections alive, ML closes idle ones after 2 s
(`http_keepalive_timeout_s`, `config.py:62`), and Cilium picks a backend at random for each new
connection. That split is random, though. It cannot send the large OCR inputs to the card with
room for them, so both replicas would need the full 3,072 MiB cap.

### Load test

`scripts/immich-ml-loadtest/` holds three scripts. None of them touches Immich's database or
job queues, apart from one read-only query.

* `render-job.sh <name>` renders a Job in `photos` that POSTs straight to ML's `/predict` with
  the exact request bodies immich-server v3.2.4 sends (`machine-learning.repository.ts:190-242`).
  Requests are CLIP visual, face detection + recognition, and OCR. The inputs are real worst-case
  previews, which `picks.sql` finds read-only: the 30 photos with the most faces (up to 61), the 30
  with the most OCR boxes (up to 180) and the 20 most elongated (up to 8:1). It adds synthetic
  extremes: 10:1 text panoramas both ways, one very long text line, a dense text page, a 1:30
  scrolling screenshot, and a 2 × 2 tile of the most face-heavy photo. While the load runs it
  times voice: ollama-assist02 `/api/chat` with a Home-Assistant-sized prompt, and Wyoming STT
  on `whisper` with a 3.8 s command that kokoro synthesizes at start-up. Phases come from
  `PLAN`; the default is idle 120 s, 2/2/1 for 300 s, 4/4/4 for 300 s, idle 120 s.
* `render-ml-trial.sh <name> <node> [KEY=VALUE …]` starts a throwaway ML server as a Job on any
  GPU node, with the guard and the live Deployment's ML settings plus any overrides. Nothing
  routes Immich traffic to it. Use it to try a different cap or card before changing the
  HelmRelease. It copies the models to an emptyDir, so the shared cache PVC is only read.
* `loadtest.py` is the test itself, inlined into the Job.

```bash
declare-activity start "immich-ML load test" --scope photos,ai --ttl 1h
cd scripts/immich-ml-loadtest
PLAN="baseline:120:0:0:0,default:240:2:2:1,heavy:240:4:4:4,recovery:120:0:0:0" \
  ./render-job.sh immich-ml-loadtest-$(date +%m%d%H%M) | kubectl apply -f -
# card memory every 2 s while it runs (Prometheus scrapes too coarsely to see the spikes)
kubectl exec -n observability $(kubectl get pod -n observability -l app.kubernetes.io/name=nvidia-gpu-exporter \
  --field-selector spec.nodeName=talosm05 -o name) -- nvidia-smi --query-gpu=timestamp,memory.used,utilization.gpu,temperature.gpu \
  --format=csv,noheader,nounits -lms 2000
# results: S lines per phase and model type, P lines per voice probe, E lines for the first errors
kubectl logs -n photos -l app.kubernetes.io/name=immich-ml-loadtest | grep -E '^(S|P|E|I,done)'
```

Run it only when no backlog is draining (`immich-queue-missing-*` Jobs and the backlog query
above), since it competes with real jobs for the same ML queue. A healthy guarded run ends with
`I,done,ml_errors=0`, and the card stays below 12,282 − 512 MiB.

## Rotate the key

The key lives in one 1Password field that both AppDaemon and this job read, so one change
updates both once their ExternalSecrets refresh (hourly, or at once with a force-sync).

1. In Immich, signed in as an admin: avatar → **Account Settings** → **API Keys** →
   **New API Key**. If it should stay shared with AppDaemon, keep it unrestricted
   (**Select all**); this job alone needs only `job.create` and `queue.read`.
2. In 1Password (vault HaynesKube, item `appdaemon`), replace the value of `IMMICH_API_KEY`.
3. Pull it into the cluster now instead of waiting for the hourly refresh:
   ```bash
   kubectl annotate externalsecret immich-queue-missing -n photos force-sync=$(date +%s) --overwrite
   kubectl annotate externalsecret appdaemon -n home-automation force-sync=$(date +%s) --overwrite
   ```
   AppDaemon's pod carries `reloader.stakater.com/auto`, so it restarts with the new key.
4. Run the job on demand (above) and check it ends `errors=0`.
5. Delete the old key in Immich (Account Settings → API Keys).

To give this job its own narrower key later, put it in a new field and change the
`remoteRef` in `externalsecret.yaml`. Merge that only after the field exists.

## When it fails

| Log line | Meaning | Fix |
|---|---|---|
| `HTTP 401: {"message":"Invalid API key"}` | The key was deleted in Immich, or 1Password holds a different value. AppDaemon's Immich calls fail the same way, since it uses the same key | Rotate the key (above) |
| `HTTP 403 … Missing required permission: job.create` (or `queue.read`) | The key lacks that permission | Edit the key in Immich and tick the permission, or rotate |
| `HTTP 403: … Forbidden` with no permission named | The key's user is not an admin | Make the key as an admin user |
| `HTTP 404` on `PUT /api/jobs/…` | An Immich upgrade removed the legacy jobs route (deprecated since v2.4.0) | Find the replacement start route in that release's `server/src/controllers/`, then update the script |
| `HTTP 000` | immich-server is unreachable | Check `kubectl get pods -n photos` |
| Pod stuck in `ContainerCreating` (`FailedMount … immich-queue-missing-secret not found`) | The ExternalSecret has not synced, usually because the 1Password field is missing or misnamed | `kubectl get externalsecret -n photos immich-queue-missing`, then check the field name in 1Password |

The machine learning service itself has three warning alerts (none of them pages):

| Alert | Meaning | Fix |
|---|---|---|
| `ImmichMlCudaOutOfMemory` (Loki, `machine-learning/lokirule.yaml`) | ML logged `Failed to allocate memory`, `out of memory` or `ALLOC_FAILED` | `Available memory of … is smaller than requested` alone: one run needed more than the 3,072 MiB cap, an extreme image. Find it in the immich-server log (failed OCR / face job, asset id) and decide whether it is worth raising the cap (the card's headroom allows about 3,500). `CUDA failure 2: out of memory`: the card itself was full. Check that the guard is active, and what else is on talosm05 (`nvidia-smi`). |
| `ImmichMlVramGuardInactive` (Loki) | ML loaded models in the last 30 min without guarding a CUDA session, or the guard logged an ERROR | No guard lines: the mount or `PYTHONPATH` is wrong for this image. ERROR: onnxruntime's API changed; run the guard's checks (*The VRAM guard*). Only `CPUExecutionProvider`: ML lost the GPU. |
| `SharedGpuMemoryNearlyFull` (Prometheus, `observability/nvidia-gpu-exporter/app/prometheusrule.yaml`) | talosm05's or talosm01's card has been over 95% full for 5 min | On talosm05, ML is the usual suspect; `kubectl rollout restart -n photos deploy/immich-machine-learning` frees its memory at once. Then find out why the guard did not hold. |
