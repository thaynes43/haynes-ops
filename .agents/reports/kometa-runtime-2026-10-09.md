# Kometa operations runtime — 2026-10-09

Investigated the Oct 8–9 overnight run in America/New_York. The operations Job
completed successfully; it was slow rather than wedged. Preserve daily ratings,
the three cron schedules and the existing paging thresholds.

## Evidence

Times below are EDT; Kubernetes timestamps and Prometheus queries used UTC.
Job `kometa-operations-29858820` started at 07:00:00Z and completed at 08:17:47Z
with one successful pod and no restarts. Kometa itself logged 03:00:11–04:17:45,
1:17:33. The one-hour rule was pending around 04:01 and firing around 04:06;
the last observed firing sample was 04:18 before the completion scrape cleared it.

| Run | Version | Movies / shows | Movie operations | Show operations | Kometa total |
| --- | --- | --- | --- | --- | --- |
| Sep 21 | 2.4.8 | 5,258 / 950 | 2:29 | 0:26 | 3:25 |
| Sep 22 | 2.5.0 | 5,264 / 951 | 8:30 | 1:11 | 10:11 |
| Sep 23 | 2.5.0 | 5,272 / 952 | 16:07 | 1:19 | 17:57 |
| Oct 5 | 2.5.1 | 5,242 / 966 | 42:26 | 2:56 | 45:54 |
| Oct 6 | 2.5.1 | 5,246 / 966 | 48:08 | 2:45 | 51:28 |
| Oct 7 | 2.5.1 | 5,246 / 967 | 39:11 | 3:11 | 42:51 |
| Oct 8 | 2.5.2 | 5,250 / 970 | 46:56 | 3:56 | 51:24 |
| Oct 9 | 2.5.2 | 5,252 / 970 | 59:07 | 17:55 | 77:33 |

Oct 9 item-to-item log gaps had movie median 0.644s/p90 1.040s and show median
1.142s/p90 1.406s. Only six gaps exceeded two seconds and none exceeded ten.
6,191 of 6,222 items logged no edits. There were eleven MDBList 404s, matching
yesterday's count, and no MDBList 429s, service fallback warnings, timeout or
retry messages. Library growth cannot explain the time change: only two movies
and no shows were added since Oct 8, and total size is close to Sep 21.

A bounded read-only PVC inspection confirmed `cache: true`, global expiration
60 days, MDBList expiration 120 days and the intended three ratings operations
on both libraries. The persistent database had MDBList data and IMDb identity
maps, keywords and parental-guide tables, but no IMDb ratings cache. The three
retained operations log files had timing instrumentation disabled and no HTTP
request records. Inspection Jobs were removed; the config, cache and Plex
metadata were not changed.

Queries:

- Runtime: `kube_job_status_completion_time{namespace="media",job_name=~"kometa-operations-.*"} - kube_job_status_start_time{namespace="media",job_name=~"kometa-operations-.*"}`.
- Timeline: `ALERTS{alertname="KometaOperationsRunTooLong"}`.
- Logs: `{namespace="media",pod=~"kometa-operations-.*"} |~ "Start Time:|Operations Run Time:|Version:|Processed [0-9]"`.

## Cause and limits

[Upstream PR #3374](https://github.com/Kometa-Team/Kometa/pull/3374) replaced
IMDb's bulk datasets with individual title lookups, caching only within a run.
It shipped in v2.5.0, deployed here Sep 21 after that morning's short v2.4.8 run.
Both libraries still use `mass_user_rating_update: imdb`; every unique IMDb
identity now causes an external `GET /title/{id}` during nightly maintenance.
The warm MDBList cache covers the Rotten Tomatoes fields, not these IMDb calls.

[v2.5.2 IMDb source](https://github.com/Kometa-Team/Kometa/blob/v2.5.2/modules/imdb.py#L1202)
retains the same lookup path as v2.5.1. Oct 8 already ran v2.5.2, so last night's
additional 26 minutes cannot be attributed solely to that upgrade. The larger
baseline and steady per-item delays are consistent with the serial external
lookup path. Historic logs cannot split network time precisely between IMDb
and Plex; direct per-source timing is required to establish the external
service's share of last night's extra latency.

[The published service source](https://github.com/Kometa-Team/Kometa-Utilities/blob/4121ca030bf88d4b92d7c9e8a18886bc366f33ae/imdb-service/main.py#L4331)
serves one full title record per call, including crew/principals and a TV episode
count. It exposes no title batch route in that revision. This is source evidence,
not verification of the service's current deployment or a claim about its
internal database performance.

## Run coordination and diagnostics

Operations ran until 04:17:47 while overlays started at 04:00, overlapping for
17m47s. Separate CronJob `concurrencyPolicy: Replace` settings coordinate only
successive runs of the same controller. They do not coordinate operations,
overlays and collections with each other. All three share `config.yml` and
`config.cache` on the same PVC, and operations flush rating edits at the end of
each library pass. Movie ratings were written before overlays started, but the
show flush happened at 04:17:45. A show overlay can therefore read the prior
rating. This is an ordering hazard; no evidence shows the overlap caused the
preceding 59-minute movie pass.

Serialize all three launchers with a shared advisory lock at
`/config/.run.lock`. Keep the descriptor across exec into the existing Kometa
interpreter, and let tini forward termination to the process group. Log both
waiting and acquisition. Tighten each Job's deadline from 23 hours to three hours,
bounding waiting plus execution; keep all schedules, providers and thresholds.
The Ceph block PVC is RWO: a cross-node overlapping pod can instead wait for
volume attachment in `ContainerCreating` with `Multi-Attach` events before the
launcher starts, so it has no lock-wait log yet. Check pod events as well as the
launcher logs; elapsed alerts and hard deadlines include either wait.

A separate completed-run Loki query for Sep 10–Oct 9 found collections maximum
50:18 across 30 completions and overlays maximum 45:52 across 29 completions.
The missing Sep 13 overlay was the previously documented nine-hour wedged run.
The older [full-badge overlay QA](kometa-qa-2026-07-07.md#task-4--overlay-coverage-qa-kometa-overlays-drain-kometa-244)
completed in 72:13, and the documented July 19 cold/quota operations catch-up
took about 2h15m. With the one-hour gap before overlays, those two older costs
combine into about 2h27m of overlay Job elapsed, within the three-hour cap.
The July 25 8h19m collection run was an already-fixed non-native sort defect,
not a legitimate mode to preserve. No legitimate run over three hours was found.
An unusually slow combination can exhaust its budget while waiting or processing;
the failed Job releases the lock and the next daily run retries normal work.
This prevents all-day starvation without hiding the earlier alert thresholds.

Completed-run query:
`{namespace="media",pod=~"kometa-(overlays|collections)-.*"} |= "Start Time:" |= "Finished:"`.

Enable `--timings` only for operations. The
[upstream instrumentation](https://github.com/Kometa-Team/Kometa/blob/v2.5.2/modules/timings.py#L260)
records source names, call counts, durations and cache aggregates in
`/config/logs/timings-*.json`, `.csv` and `-summary.log`; it records no request URLs
or credentials. The utilities host appears as `other:utilities.kometa.wiki`.
The summary is file-backed, not printed into Loki. After deployment, operations
validation must inspect these files alongside the lock messages.

Correct the obsolete five-minute operations claim and the Plex upgrade
preflight's obsolete weekly 01:00 schedule. Threshold expressions stay intact.

## Daily-freshness remedy

The current image still contains `IMDb.ratings` and `_interface("ratings")`:
one download of `title.ratings.tsv.gz`, parsing into an in-memory IMDb ID→rating
map reused by both libraries, then deleting temporary dataset files. The service
path selects this fallback only after a service failure; there is no supported
configuration toggle for choosing it directly. A failure does not invalidate
Plex's existing ratings. [IMDb documents daily dataset refreshes and personal,
noncommercial availability](https://data.imdb.com/non-commercial-datasets/).

A narrow maintained compatibility patch selects this existing dataset
path for `get_rating` on operations only, preserving direct IMDb daily freshness
without downgrading Kometa or changing charts, genres, episode lookups, Rotten
Tomatoes scores or overlays. The upstream motivation was avoiding repeated
large downloads across ratings, basics and episode datasets; this deployment
uses only the ratings dataset. There is no existing local Kometa patch pattern.
The shared launcher checks the full v2.5.2 `modules/imdb.py` SHA-256 and exact
method anchor before any Kometa imports/writes. Its import hook replaces only
that method, leaving the image's downloader/parser and unrelated methods intact.
The Linux operations worker inherits the patched module and lock; tini forwards
termination to its process group. `HNET_IMDB_RATINGS_SOURCE: upstream` in git is
the explicit revert. Unsupported sources fail closed. Kometa image bumps now
require a manual merge and source/real-image revalidation, overriding the broad
media Renovate automerge rule. The disposition labeler mirrors this rule; its
already-manual LazyLibrarian/calibre rules were missing and were synchronized.
See [the upgrade/revert runbook](../runbooks/kometa-upgrades.md).

Bounded probes against the actual v2.5.2 image on talosw01 verified:

- One official ratings download plus the image's unchanged parser took 2.226s
  and produced 1,718,756 rows. Movie/show lookups shared that snapshot; null and
  unknown IDs returned `None`; no per-title service call occurred; temporary
  dataset files were removed. This probe loaded no config or credentials, and
  ran with production's UID/GID 1000, seccomp and read-only root filesystem.
- The actual Kometa CLI parser recognized `--run`, `--operations-only` and
  `--timings`. A stub using the image's `_process_pool_context` verified runpy
  worker pickling, patched ratings, inherited argv and lock descriptor. A
  contender was excluded until tini group SIGTERM terminated parent/worker,
  then acquired the lock without an orphan holder.
- A separate cross-process test verified `flock` exclusion and release on the
  actual Ceph block PVC. Only a temporary probe lock was created/removed; no
  application data changed. All inspection/probe Jobs were cleaned up.

The repeatable image probe is `scripts/kometa-image-probe.py`. Local regressions
passed five launcher tests in 0.4s and sixteen deterministic upgrade-label cases;
Kustomize rendering and whitespace checks passed. The image probe caught and
corrected the need for tini's own `--` separator before Python; Kometa flags are
then forwarded without a separator so argparse sees them.

No full Kometa run was triggered before deployment. A bounded manual operations
Job after review/merge/Flux reconciliation will use the normal daily ratings
mutation, shared lock and a 10–15 minute deadline while all three jobs are idle.
Inspect provider timings and actual run completion before claiming a recovered
whole-job runtime. The Sep 21 bulk-path baseline is a measurement, not a guarantee.

Changing to `mdb_imdb` with the current 120-day cache would materially reduce
freshness. Shortening MDBList expiry applies to all its cached fields, and the
provider can itself lag IMDb. With 6,222 items, a seven-day evenly spread expiry
requires roughly 889 item refreshes per day before other MDBList uses; existing
cache ages make the first refresh much larger. This is not a transparent
substitute for current direct daily IMDb ratings. Weekly IMDb operations also
change freshness and retain the same one-run cost.
