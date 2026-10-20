# sigoalumni readiness trace (story H3)

Support material for the held PR that raises the sigoalumni readiness `failureThreshold`
from 3 to 9. Lives outside every Flux Kustomization path (`kubernetes/**`), so Flux never
applies it. Read-only, no cluster, no network except `helm pull` of the pinned chart in
`render-diff.sh`. Needs `kustomize`, `helm`, `python3` with PyYAML, and `node`.

- `render-diff.sh [BASE_REF]` renders the app at BASE_REF and the working tree (kustomize
  build, then helm template with the pinned app-template chart), prints the rendered diff
  and **exits non-zero unless the only rendered change is the app container's
  `readinessProbe.failureThreshold`** (no change at all also fails).
- `trace.mjs` is the deterministic R1-R4 model (kubelet 10s readiness schedule, GCP uptime
  alert model, the watchdog's own `steer.mjs` imported unmodified from sigoalumni-org with
  every effect mocked). Run `nice -n 19 node trace.mjs`; it takes about a second and exits
  non-zero if an assertion regresses. `--sigo-repo DIR --ref REF` pick the sigoalumni-org
  checkout (default `/home/dev/repos/sigoalumni-org`, `origin/main`).
  It **parses the readiness and liveness probe blocks from `helmrelease.yaml`**
  (`--helmrelease FILE` to point elsewhere), asserts they equal the constants the model
  assumes (path, port, period 10, timeout 5, successThreshold 1, failureThreshold 9;
  liveness tcpSocket 8080), and runs every "this PR" sweep with the parsed threshold. Editing
  the manifest value without updating the model therefore fails the run.

## Known model artifact: the rare alert in the 120s case

In the 120s-outage run (`chain_thresholdPR_120sOutage`) about 1 of 500 phase draws raises the
GCP alert even though the outage is over long before. The model forward-fills each checker's
latest sample (the stand-in for `ALIGN_NEXT_OLDER`), so when more than one checker happened to
sample inside the roughly 30s the Service is empty, ">1 false" is held for 300s after
recovery and the alert fires at about +420s. Whether real Cloud Monitoring behaves the same is
unproven. It is documented, not tuned away. The property that matters is asserted: by then
`/api/healthz` is healthy, so the watchdog's confirm declines (0 failing confirms, 0 steers).

The model's other assumptions and limits are in the header comment of `trace.mjs`.
