# Kometa image upgrades

## Current state (since 2026-11-10)

All three jobs (operations, overlays, collections) run upstream Kometa unmodified
through `config/serial-run.py`, which only serializes them behind the shared lock.
Operations sets `HNET_IMDB_RATINGS_SOURCE: upstream` and passes `--timings`.

**Owner ruling (Tom, 2026-11-10): "Do what Kometa recommends."** The custom daily
IMDb dataset patch that operations used on v2.5.2 and v2.5.3 is retired, so
upgrades stay simple. Story:
[OP513](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/513).
History and evidence: [the Oct 9 report](../reports/kometa-runtime-2026-10-09.md)
and its 2026-11-10 addendum.

How IMDb ratings work from v2.5.4 on:

- Each job, when it loads a library, sends every IMDb ID with no cached score, or
  a score older than `settings.cache_expiration`, to
  `POST https://utilities.kometa.wiki/imdb-service/ratings`, up to 1,000 IDs per
  request. It does the same for genres (`/imdb-service/genre`). Results go into
  `config.cache` on the PVC.
- `cache_expiration` is 60 days in the git seed of `config.yml`. The live copy is
  PVC-seeded and was not re-read for this change. **An IMDb score in Plex can be
  up to 60 days old.** New cache rows get random ages within the window, so
  refreshes spread out: roughly 1/60 of the library (about 100 titles) per night.
- If the service fails, Kometa falls back to IMDb's official TSV datasets for the
  titles still unresolved. For ratings that is a small download. For genres it is
  `title.basics`, a much larger one.
- **What leaves the cluster:** the library's IMDb IDs go to Kometa-Team's
  utilities host. The first v2.5.4 run sends the whole library (about 6,200 IDs)
  to both endpoints, and later nights send only the expired ones. MDBList already
  received the library's IDs before this change.

The dataset-patch code is still in `serial-run.py` but nothing uses it (see the
note at `APPROVED_IMDB_SHA256`). Only `HNET_IMDB_RATINGS_SOURCE=dataset` on
operations can reach it. On v2.5.4 or any later image, the hash guard refuses the
image before the lock, any Kometa import or any Plex write: the pod exits 1, the
Job fails and `MediaAutomationJobFailed` pages. The patch's anchor does not exist
in v2.5.4, so never update that hash. Remove the dead path in a follow-up once
v2.5.4 runs are verified.

## Upgrade gate

Renovate opens Kometa bump PRs. They stay manual-only (`.renovate/autoMerge.json5`
and the PR disposition labeler), outside the upgrade-shepherd ramp, for two
reasons:

- Kometa exits 0 even when it aborts at startup. On v2.5.3, `--timings` crashed
  every operations run from 2026-10-23 to 2026-11-09 while the Jobs showed
  Completed (Kometa-Team/Kometa#3718).
- An upgrade can reset caches. v2.5.4 renamed the MDBList cache table
  (`mdb_data5` to `mdb_data6`) and drops the old one at startup, so its first run
  refetches MDBList for the whole library.

For every proposed tag:

1. Read the release notes and the upstream changes to `kometa.py` (CLI parser,
   `_process_pool_context`), `modules/request.py` (timeouts, retries),
   `modules/cache.py` (the dropped-table list: a renamed table means a cold cache
   on the first run) and the Dockerfile entrypoint. Pin the tag with its index
   digest (`tag: vX.Y.Z@sha256:...`) so the probed image is the deployed one.
2. Run the local regressions and render check:

   ```bash
   nice -n 19 python3 scripts/kometa-serial-run-test.py
   nice -n 19 node scripts/pr-disposition-test.cjs
   python3 -c 'import ast,pathlib; ast.parse(pathlib.Path("scripts/kometa-image-probe.py").read_text())'
   kubectl kustomize kubernetes/main/apps/media/kometa/app > /tmp/kometa-rendered.yaml
   git diff --check
   ```

3. Probe the exact image. This creates a Job, so it is a cluster write that needs
   authorization. Run it from the PR branch in an idle window: more than 30
   minutes away from the 03:00, 04:00 and 06:30 America/New_York starts, with
   no Kometa Job active.

   ```bash
   IMAGE='docker.io/kometateam/kometa:vX.Y.Z@sha256:<index digest>'
   NODE=talosw02   # pick from the worker list below: TAINTS <none>, READY True
   TZ=America/New_York date
   kubectl get cronjob -n media kometa-operations kometa-overlays kometa-collections
   kubectl get jobs -n media | grep kometa
   kubectl get pods -n media --no-headers | grep -i kometa | grep -vE 'Completed|Error'   # expect nothing
   kubectl get nodes -l '!node-role.kubernetes.io/control-plane' -o 'custom-columns=NAME:.metadata.name,TAINTS:.spec.taints[*].key,READY:.status.conditions[?(@.type=="Ready")].status'
   kubectl get job -n media kometa-image-probe   # expect NotFound
   python3 scripts/kometa-image-probe.py --image "$IMAGE" --node "$NODE" > /tmp/kometa-image-probe.json
   kubectl create -f /tmp/kometa-image-probe.json
   kubectl wait -n media --for=condition=complete job/kometa-image-probe --timeout=200s
   kubectl logs -n media job/kometa-image-probe
   kubectl delete job -n media kometa-image-probe --wait=true
   ```

   The Job has a 180-second deadline (including image pull), 500m CPU and 1Gi
   memory limits, no application PVC or credentials, and only temporary emptyDir
   writes. A wait timeout is not a pass. Read
   `kubectl describe job -n media kometa-image-probe` and the logs, then delete
   the Job anyway. Do not run probes in parallel. A pass prints:

   - `image_imdb_sha256 <hash>`: record it in the PR.
   - `retired_dataset_patch_fails_closed: PASS`: a stray `dataset` setting still
     refuses this image.
   - `actual_image_cli_flags [...] PASS`: the image's own parser accepts all
     three production argument sets, read from the HelmRelease.
   - `actual_image_worker_lock_and_tini_group_termination: PASS`: a forked
     worker holds the shared lock until tini's group SIGTERM releases it.
   - `kometa_image_probe: PASS`.

   The probe never starts Kometa, loads config or contacts Plex or any provider,
   so it cannot catch a startup abort like #3718. Only a real run proves that.
4. Read the required checks and any advisory review, then merge and reconcile
   Flux (`flux reconcile kustomization kometa -n media --with-source`).
5. Verify the first run of each job, as described below. A separately authorized
   manual operations run may use the normal CronJob template, shared lock and a
   shortened 10 to 15 minute deadline while all three jobs are idle. Declare it
   with `declare-activity start ... --scope media,kometa`, then end the
   declaration.

## Verify a run

- **`Finished Run`** in the job's log, followed by its `Start Time / Finished /
  Run Time` line. A Completed Job without it means Kometa aborted:
  `{namespace="media", pod=~"kometa-.*"} |~ "Finished Run|Run Time: |Version:"`.
- **Runtime.** On v2.5.2, operations took 1:41 on 2026-10-18. v2.5.4 with a warm
  cache is expected to take about as long; the addendum to the Oct 9 report
  estimates the first, cold-cache run. `KometaOperationsRunTooLong` pages when an
  operations Job is still active after 1 hour (for 5 minutes). Overlays and
  collections page after 2 hours.
- **IMDb.** `Caching N IMDb ratings` lines show each batch request: N is close to
  the library size on a cold cache and about 100 a night afterwards.
  `IMDb Service unavailable, falling back to TSV dataset` means the service
  failed and that run used the official datasets.
- **Timings export** (operations, `--timings`):
  `/config/logs/timings-*-summary.log`, `.json` and `.csv` on the PVC. These are
  files, not Loki lines, so reading them needs a separately authorized read-only
  PVC Job. `other:utilities.kometa.wiki` should show a handful of calls (one per
  batch), not thousands.
- **MDBList.** The next job's banner prints `API Requests Used Today`; compare it
  with the 1,000 a day free quota.

## Revert and operating limits

To roll back, revert the upgrade PR in git and reconcile Flux. Reverting the
v2.5.4 PR returns v2.5.3, the dataset patch on operations and operations without
`--timings`, which v2.5.3 cannot run. v2.5.3 does not use v2.5.4's `mdb_data6`
table and recreates `mdb_data5` empty, so a rollback also starts with a cold
MDBList cache. Do not change only `HNET_IMDB_RATINGS_SOURCE`: on v2.5.4,
`dataset` fails the Job. Do not raise an alert threshold to hide a slow run.
Neither direction needs the PVC-seeded config or cache to be touched.

All three production launchers mount the same RWO Ceph block PVC at `/config` and
hold `/config/.run.lock`. Waiting and acquisition are logged separately. If an
overlapping Job schedules on another node, RWO volume attachment can hold its pod
in `ContainerCreating` with `Multi-Attach` events before the launcher starts; no
lock-wait line exists yet. Inspect `kubectl describe pod -n media <pod>` and its
events in that case. Same-node processes coordinate with `flock`; cross-node
mounts additionally wait for attachment. Job elapsed time/alerting includes both
waits. Preserve tini `-g -s`, inheritable lock
descriptor, and the three-hour production deadline across changes. This deadline
bounds waiting plus execution; an exceeded budget fails the Job and releases the
lock. The next daily run can retry idempotent ratings/overlay work. Do not enlarge
the cap to accommodate a slow provider or a non-native collection sort. The Oct 9 probe
verified cross-process `flock` exclusion and release on the actual PVC backend.
