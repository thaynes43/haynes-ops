# sigoalumni alert tests (Sigo hardening story H4)

promtool unit tests for
`kubernetes/main/apps/frontend/sigoalumni/app/prometheusrule.yaml`. They live under
`scripts/`, not under `kubernetes/`, so no Flux Kustomization can pick them up.

## Run

Alertmanager route proof: `AMTOOL=<amtool 0.34.1> scripts/sigoalumni-alert-tests/alertmanager-route-check.sh`
(official release, sha256-verified, same version as the live Alertmanager).

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

Caught: widening every `<= 90` freshness gate to `<= 9000` fails O3c and O3d; removing the
zero-fill fails O1/O2a; widening the egress freshness from 20s to 60s fails O6e2; dropping
the egress both-failed clause fails eight O6 groups; setting the sustained rule to
`for: 0s` fails O1/O2e/O3d/O7.

NOT caught (the fixtures pass with these removed), so they are defence in depth that no
fixture pins: the `max by (address)` de-duplication (see above), the egress
`count(up{...} == 1) == 2` clause (a failed exporter scrape also stale-marks
`probe_success`, so the exporter-down case is already quiet; an `up == 0` with
`probe_success` still present is not fixtured), the `count(kube_pod_status_ready...) > 0`
guard, the `targetref_kind="Pod"` matcher and the 90s freshness gate on `kube_service_info`.
They are redundant in practice rather than wrong, and are untested.

## Replay (O8)

`replay.py` evaluates the real rule expressions with `query_range` over existing history and
`replay-receipt.json` is the saved result (windows: Oct 16-20 incident period, Oct 8-13
comparison window, Sep 20-Oct 20 thirty days; the comparison window is not known-quiet, it
contains one readiness firing and one KSM blip). Summary:

* Empty-Service observation: one firing in 30 days, 2026-10-16 11:29:01 and 11:29:31Z
  (two evaluation points; the receipt's :30/:00 stamps are the query-start alignment it
  discloses), matched to the known 2026-10-16 incident. No other firing in the window.
  This is an observed firing count, not a measured false-positive or detection rate: no
  independent ground truth was checked for the other 30 days.
* Readiness warning: 11 firings in 30 days. Matched to known incident windows: Oct 16 11:29
  (the incident), Oct 20 01:42 (W1), 07:59 (W2) and 10:26 (W4); W3 correctly does not
  fire. The other seven (Sep 22 07:13, Sep 24 13:36, Oct 12 19:41, Oct 13 01:18, Oct 13
  01:57, Oct 18 21:19, Oct 19 02:47) are unverified, no incident ground truth was checked:
  they may be real failed probes or noise. Roughly one a day lately; not tuned out, per the
  plan.
* Telemetry-incomplete: seven short `ksm_down_or_stale` blips in 30 days, longest three
  minutes, so none would have survived `for: 5m` (a count, not a verdict).

## Limits

* The empty-Service rules cannot see an outage that begins and ends between two consecutive
  KSM scrapes (no scrape lands inside it). An outage that overlaps a scrape is observed
  whatever its length, including under 60s; the readiness warning is the only signal that
  also covers the unsampled case. Not every short outage is detectable.
* Immediate notification of the `for: 0` critical depends on the dedicated Alertmanager
  route added in this PR (`kube-prometheus-stack/app/externalsecret.yaml`: matchers
  `alertname="SigoalumniServiceEndpointsEmptyObserved"` + `severity="critical"`,
  `group_wait: 0s`, same `pushover` receiver, placed just before the generic critical
  route). The root `group_wait` is 1m, and without the route an episode that resolves before
  the first group flush may never notify; the Oct 16 episode was true for two evaluations.
  If the route is removed or the alertname changes, that guarantee silently disappears.
  `alertmanager-route-check.sh` renders the template and checks it with amtool. The readiness
  and telemetry warnings route to the null receiver (UI only).
* The egress freshness bound (20s) is deliberately tight: a scrape that arrives late (older
  than 20s at evaluation) skips that point and resets `for:`, a silent miss and never a
  false alarm.
* A brand-new pod's first-ever failed readiness probe creates the `result="failed"` series
  at value >= 1 and contributes no `increase()`; the second failure is counted.
* The egress rule never fires on a single target, an exporter outage, or results older than
  20s. `up` for the probe targets only proves the exporter answered; the exporter itself has
  no scrape job in this cluster.
* New 15s probes are a future collection change, not better resolution of old data.
