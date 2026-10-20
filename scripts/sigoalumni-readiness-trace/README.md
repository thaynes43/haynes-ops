# sigoalumni readiness trace (story H3)

Support material for the held PR that raises the sigoalumni readiness `failureThreshold`
from 3 to 9. Lives outside every Flux Kustomization path (`kubernetes/**`), so Flux never
applies it. Read-only, no cluster, no network except `helm pull` of the pinned chart in
`render-diff.sh`.

- `render-diff.sh [BASE_REF]` renders the app at BASE_REF and the working tree (kustomize
  build, then helm template with the pinned app-template chart) and diffs the manifests.
- `trace.mjs` is the deterministic R1-R4 model (kubelet 10s readiness schedule, GCP uptime
  alert model, the watchdog's own `steer.mjs` imported unmodified from sigoalumni-org with
  every effect mocked). Run `nice -n 19 node trace.mjs`; it takes about a second and exits
  non-zero if an assertion regresses. `--sigo-repo DIR --ref REF` pick the sigoalumni-org
  checkout (default `/home/dev/repos/sigoalumni-org`, `origin/main`).

The model's assumptions and limits are in the header comment of `trace.mjs`.
