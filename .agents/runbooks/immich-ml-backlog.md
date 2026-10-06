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
   memory on the card (found in review of #3439). It adds about 11 minutes a night. The
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
the highest of the three (OCR, about 9.1 GB in total), not their sum. Since 2026-10-06 nothing manual is needed for a big backlog: the job does what the
2026-10-06 recovery did by hand, which was to run OCR on its own.

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
