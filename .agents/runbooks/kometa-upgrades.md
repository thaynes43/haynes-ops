# Kometa image upgrades and daily IMDb compatibility

Operations uses `HNET_IMDB_RATINGS_SOURCE: dataset` in its HelmRelease. The shared
launcher changes only `IMDb.get_rating` to select the image's existing official
ratings dataset route. All other IMDb methods, Rotten Tomatoes providers and
cron schedules retain their normal behavior. See the incident evidence in
[the Oct 9 report](../reports/kometa-runtime-2026-10-09.md).

## Upgrade gate

Renovate opens Kometa bump PRs but does not auto-merge them. The launcher checks
the full `/modules/imdb.py` SHA-256 and exact `get_rating` anchor before any
Kometa imports or Plex writes. An incompatible image fails explicitly; it never
silently returns to thousands of external title lookups. Kometa is outside the
upgrade-shepherd ramp. The disposition labeler mirrors this manual-only policy.

For every proposed tag or digest:

1. Read that version's upstream `modules/imdb.py` and `kometa.py`. Compare
   `get_rating`, `ratings`, `_interface`, the HTTP downloader, CLI parser and
   `_process_pool_context` to the reviewed v2.5.2 implementation. Review the
   image entrypoint paths as well. If these contracts changed, adapt or remove
   the compatibility patch; do not update the hash without reviewing the code.
2. Set `APPROVED_IMDB_SHA256` to the reviewed image file's actual SHA-256 and
   update the method anchor/version descriptions when needed in the same PR.
   Obtain source/hash from a bounded Job in the proposed image, or verify that
   a fetched upstream file is byte-identical to the image file in that Job.
3. Run the local regressions and render check:

   ```bash
   nice -n 19 python3 scripts/kometa-serial-run-test.py
   nice -n 19 node scripts/pr-disposition-test.cjs
   kubectl kustomize kubernetes/main/apps/media/kometa/app > /tmp/kometa-rendered.yaml
   git diff --check
   ```

4. Verify operations, overlays and collections are idle with
   `kubectl get cronjob -n media kometa-operations kometa-overlays kometa-collections`.
   Render the secret-free image probe with the proposed image and an idle worker
   (example node below; verify it is a worker before using it):

   ```bash
   python3 scripts/kometa-image-probe.py --image docker.io/kometateam/kometa:v2.5.2 \
     --node talosw01 > /tmp/kometa-image-probe.json
   kubectl create -f /tmp/kometa-image-probe.json
   kubectl wait -n media --for=condition=complete job/kometa-image-probe --timeout=60s
   kubectl logs -n media job/kometa-image-probe
   kubectl delete job -n media kometa-image-probe --wait=true
   ```

   The Job has a 180-second deadline, 500m CPU/1Gi memory limits, no application
   PVC or credentials, and only temporary emptyDir writes. A wait timeout is not
   a pass: inspect status/logs, then clean up the Job. Avoid parallel probe runs.
   Require source guard, real-image CLI flags, one official dataset download,
   shared movie/show lookups, null/unknown ID handling, temporary file cleanup,
   runpy worker pickling/patch inheritance, worker-held lock, and tini group
   termination/lock release to pass. The probe deliberately never runs Kometa
   against Plex. Do not run the full nightly workload merely to test an image.
5. Read advisory review and required checks, then merge and reconcile Flux.
   Verify the next operations run's source and lock messages plus
   `/config/logs/timings-*-summary.log`. A separately authorized manual operations
   verification may use the normal CronJob template, shared lock and a shortened
   10–15 minute Job deadline while all three jobs are idle. Declare it with
   `declare-activity start ... --scope media,kometa`, then end the declaration.

## Revert and operating limits

To return to upstream per-title IMDb ratings, change only the operations
`HNET_IMDB_RATINGS_SOURCE` to `upstream` in git and reconcile Flux. This retains
the shared lock and `--timings`; expect the longer serial lookup cost to return.
Do not alter the alert threshold to conceal it. Restoring this route does not
require touching the PVC-seeded config or persistent cache.

The official IMDb dataset is refreshed daily and is available for personal,
noncommercial use under [IMDb's terms](https://data.imdb.com/non-commercial-datasets/).
The existing image downloads/parses it once per process and removes temporary
files. It is an in-memory snapshot reused by both libraries, not a persistent
120-day cache. A dataset outage can fail operations; preserve existing ratings
and investigate egress/source errors before choosing an explicit rollback.

All three production launchers mount the same Ceph block PVC at `/config` and
hold `/config/.run.lock`. Waiting and acquisition are logged separately; Job
elapsed time/alerting includes the wait. Preserve tini `-g -s`, inheritable lock
descriptor, and the three-hour production deadline across changes. This deadline
bounds waiting plus execution; an exceeded budget fails the Job and releases the
lock. The next daily run can retry idempotent ratings/overlay work. Do not enlarge
the cap to accommodate a slow provider or a non-native collection sort. The Oct 9 probe
verified cross-process `flock` exclusion and release on the actual PVC backend.
