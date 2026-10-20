# sigoalumni alert tests (Sigo hardening story H4)

promtool unit tests for
`kubernetes/main/apps/frontend/sigoalumni/app/prometheusrule.yaml`. They live under
`scripts/`, not under `kubernetes/`, so no Flux Kustomization can pick them up.

## Run

promtool is not vendored. Fetch the official release matching the live Prometheus (3.15.0),
verify it, run:

```bash
V=3.15.0; F=prometheus-$V.linux-amd64.tar.gz; D=$(mktemp -d)
curl -sSL -o $D/$F https://github.com/prometheus/prometheus/releases/download/v$V/$F
curl -sSL -o $D/sha256sums.txt https://github.com/prometheus/prometheus/releases/download/v$V/sha256sums.txt
(cd $D && grep " $F\$" sha256sums.txt | sha256sum -c -) && tar -xzf $D/$F -C $D --strip-components=1 prometheus-$V.linux-amd64/promtool
PROMTOOL=$D/promtool scripts/sigoalumni-alert-tests/run.sh
```

`run.sh` extracts the rules with `yq`, runs `promtool check rules`, then `promtool test
rules` on two generated fixtures (it fails if they are stale relative to
`generate_fixtures.py`):

* `sigoalumni-rules-test.yaml`: the behaviour matrix, run on a copy of the rules with the
  annotations removed, so rewording an annotation does not churn 40 assertions.
* `sigoalumni-annotations-test.yaml`: five groups on the real rules that pin the rendered
  annotation text (`{{ query }}`, `$labels`, `$value`, the per-reason branches).

Regenerate after editing a scenario: `python3 -I generate_fixtures.py behavior >
sigoalumni-rules-test.yaml` (and `annotations > sigoalumni-annotations-test.yaml`).

## Timing model

The live Prometheus scrapes kube-state-metrics every 60s and evaluates rules every 30s. Each
KSM series therefore has a sample on every second 30s step, and the step in between sees
that sample aged 30s. The fixtures use exactly that; a staleness marker models "absent from
that scrape". The egress probes scrape every 15s. A rule with `for: 30s` needs two
consecutive true 30s evaluations.

## Plan matrix (O1-O7)

| ID | Fixture groups (names in the YAML start with the ID) |
|---|---|
| O1 | normal operation; the 2026-10-16 shape (ready=true absent from one scrape, ready=false listed, pod gauges 0) detects at the first evaluation that sees it |
| O2 | corroboration by ready=false only, by pod-ready-zero only, neither (no critical, `empty_uncorroborated` warning); the 19s pulse unsampled (no empty-Service alert, readiness counter alert) vs sampled (observation fires) |
| O3 | EndpointSlice family missing (with pods Ready and with pods not Ready), KSM down, KSM stopped without staleness markers, stale-held zero, Service absent, Deployment series missing, desired replicas 0 |
| O4 | EndpointSlice rotation, deployment roll with surge pod and terminating pod, single pod unready, duplicate HA scrape identities (quiet, and a single alert on an outage), another namespace's slice and a CronJob pod ignored |
| O5 | only successful probes, one failure, all three pods failing (one alert), counter reset without and with new failures, scrape gap |
| O6 | both fail (connect), one fails (either), one 15s blip, exporter down, held/stale results, both DNS failure, mixed, recovery, missing target |
| O7 | zero visible in two scrapes (observation only) vs three scrapes (observation, then the 2m rule at the 7m evaluation) |

`promql_expr_test` entries in O4 check the de-duplicated ready count (3, not 6). Those
tests evaluate a copy of the expression, so they document the dedupe behaviour but do not
guard it: de-duplication never changes whether the count is zero, so no alert-level fixture
can see it.

## Mutation spot-checks run during development

Widening every `<= 90` freshness gate to `<= 9000` fails O3c and O3d. Dropping the egress
both-failed clause fails eight O6 groups that assert a quiet result. Setting the
sustained rule to `for: 0s` fails O1/O2e/O3d/O7. (The de-duplication mutation was not
detected, see above.)

## Replay (O8)

`replay.py` evaluates the real rule expressions with `query_range` over existing history and
`replay-receipt.json` is the saved result (windows: Oct 16-20 incident period, Oct 8-13
normal comparison, Sep 20-Oct 20 thirty days). Summary:

* Empty-Service observation: exactly one episode in 30 days, 2026-10-16T11:29:30-11:30:00Z
  (two evaluation points). No false positive.
* Readiness warning: 11 episodes in 30 days; six between Oct 16 and Oct 20 (Oct 16 11:29,
  Oct 18 21:19, Oct 19 02:47, Oct 20 01:42 / 07:59 / 10:26, matching W1, W2 and W4), one in
  the Oct 8-13 comparison window (Oct 12 19:41) plus two on Oct 13. About one a day lately,
  which is the noise level the plan told us not to tune out.
* Telemetry-incomplete: seven short `ksm_down_or_stale` blips in 30 days, longest three
  minutes, so none would have survived `for: 5m`.

## Limits

* The empty-Service rules cannot see an outage shorter than the 60s KSM scrape that falls
  between two scrapes; the readiness warning is the only signal that survives that.
* A brand-new pod's first-ever failed readiness probe creates the `result="failed"` series
  at value >= 1 and contributes no `increase()`; the second failure is counted.
* The egress rule never fires on a single target, an exporter outage, or results older than
  20s. `up` for the probe targets only proves the exporter answered; the exporter itself has
  no scrape job in this cluster.
* New 15s probes are a future collection change, not better resolution of old data.
