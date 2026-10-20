#!/usr/bin/env python3
"""Generate sigoalumni-rules-test.yaml (promtool unit-test fixtures for O1-O7).

The fixtures are generated because every scenario repeats the same ~12 kube-state-metrics
series with small per-minute edits, and a hand-written 30-group YAML hides those edits.
Re-run after changing a scenario, then commit both files:

    python3 -I generate_fixtures.py > sigoalumni-rules-test.yaml

Timing model (matches the live Prometheus): KSM is scraped every 60s, rules run every
30s, so a KSM series has a sample on every second 30s step and the other step sees the
previous sample (aged 30s). Value tokens, one per minute: a number is a scraped sample,
"S" is a staleness marker (the series was absent from that scrape), "_" is no sample at
all (the series did not exist yet, or the scrape did not happen).
"""
import sys

KSM = 'instance="10.42.7.113:8080",job="kube-state-metrics",pod="kube-state-metrics-79c64885dd-lwfk2"'
# kube_pod_status_ready carries its own pod label (the sigoalumni pod), so no KSM pod label there
KSMP = 'instance="10.42.7.113:8080",job="kube-state-metrics"'
PODS = [
    ("sigoalumni-7bb7489855-csvbf", "10.42.0.234"),
    ("sigoalumni-7bb7489855-8ln95", "10.42.7.95"),
    ("sigoalumni-7bb7489855-7z66v", "10.42.8.149"),
]
SLICE = "sigoalumni-lwf92"


def tok(v):
    if v is None:
        return "_"
    if v == "S":
        return "stale"
    return str(v)


def s60(minutes):
    """per-minute tokens -> promtool values on a 30s grid (sample, gap, sample, gap...)."""
    out = []
    for v in minutes:
        out += [tok(v), "_"]
    return " ".join(out)


def s15(points):
    """tokens already on a 15s grid."""
    return " ".join(tok(v) for v in points)


def rep(v, n):
    return [v] * n


def feed(series, minutes):
    return {"series": series, "values": s60(minutes)}


def ksm_feeds(n, ha=False, **ov):
    """Normal 'all three pods ready' world of n minutes. ov maps a key to a replacement
    per-minute list (or None to drop the feed). HA duplicates every KSM feed under a second
    scrape identity (same data)."""
    ones = rep(1, n)
    zero = rep(0, n)
    feeds = {
        "up": (f'up{{{KSM},namespace="observability",service="kube-state-metrics"}}', ones),
        "svc": (f'kube_service_info{{{KSM},namespace="frontend",service="sigoalumni"}}', ones),
        "dep": (f'kube_deployment_spec_replicas{{{KSM},namespace="frontend",deployment="sigoalumni",service="kube-state-metrics"}}', rep(3, n)),
        "slice": (f'kube_endpointslice_info{{{KSM},namespace="frontend",endpointslice="{SLICE}",service="kube-state-metrics"}}', ones),
        # same-named-prefix CronJob pod: never Ready, must not count as a Deployment pod
        "digest": (f'kube_pod_status_ready{{{KSMP},namespace="frontend",pod="sigoalumni-legacy-digest-29874960-mbrbp",condition="true",service="kube-state-metrics"}}', zero),
        # another namespace's ready endpoints must never mask a sigoalumni outage
        "other_ns": (f'kube_endpointslice_endpoints{{{KSM},namespace="media",endpointslice="sigoalumni-abcde",address="10.9.9.9",ready="true",targetref_kind="Pod",targetref_name="sigoalumni-7bb7489855-zzzzz",service="kube-state-metrics"}}', ones),
    }
    for i, (pod, addr) in enumerate(PODS):
        feeds[f"ep{i}"] = (f'kube_endpointslice_endpoints{{{KSM},namespace="frontend",endpointslice="{SLICE}",address="{addr}",ready="true",serving="true",terminating="false",targetref_kind="Pod",targetref_name="{pod}",service="kube-state-metrics"}}', ones)
        feeds[f"pod{i}"] = (f'kube_pod_status_ready{{{KSMP},namespace="frontend",pod="{pod}",condition="true",service="kube-state-metrics"}}', ones)
    for k, v in ov.items():
        if v is None:
            feeds.pop(k, None)
        elif isinstance(v, tuple):  # (series, minutes) full replacement / new feed
            feeds[k] = v
        else:
            feeds[k] = (feeds[k][0], v)
    out = [feed(s, m) for s, m in feeds.values()]
    if ha:
        for s, m in feeds.values():
            s2 = s.replace('instance="10.42.7.113:8080"', 'instance="10.42.9.9:8080"').replace(
                "kube-state-metrics-79c64885dd-lwfk2", "kube-state-metrics-79c64885dd-ha2x1")
            out.append(feed(s2, m))
    return out


def ep_false(i, minutes, terminating="false"):
    pod, addr = PODS[i]
    return (f'kube_endpointslice_endpoints{{{KSM},namespace="frontend",endpointslice="{SLICE}",address="{addr}",ready="false",serving="false",terminating="{terminating}",targetref_kind="Pod",targetref_name="{pod}",service="kube-state-metrics"}}', minutes)


CRIT = {"severity": "critical", "service": "sigoalumni"}
WARN = {"severity": "warning", "service": "sigoalumni"}
OBS = "SigoalumniServiceEndpointsEmptyObserved"
SUS = "SigoalumniServiceEndpointsEmptySustained"
TEL = "SigoalumniTelemetryIncomplete"
RDY = "SigoalumniReadinessProbeFailing"
EGR = "SigoalumniSharedEgressSuspected"


def at(t, alertname, *alerts, ann=None):
    items = []
    for a in alerts:
        item = {"exp_labels": a}
        if ann is not None:
            item["exp_annotations"] = ann
        items.append(item)
    return {"eval_time": t, "alertname": alertname, "exp_alerts": items}


def none(times, *names):
    return [at(t, n) for t in times for n in names]


def mins(*ts):
    return list(ts)


def tl(n, step=30):
    """eval times every 30s from 0 to n minutes."""
    out = []
    for i in range(int(n * 60 / step) + 1):
        s = i * step
        out.append(f"{s // 60}m{s % 60}s" if s % 60 else f"{s // 60}m")
    return [t if t != "0m" else "0s" for t in out]


def group(name, series, alert_tests, exprs=None, interval="30s"):
    g = {"name": name, "interval": interval, "input_series": series, "alert_rule_test": alert_tests}
    if exprs:
        g["promql_expr_test"] = exprs
    return g


groups = []
ALL_KSM = (OBS, SUS, TEL)
ANN_TEXT = {
    "obs": {
        "description": "sigoalumni (frontend) had no ready endpoints when last sampled, at 1970-01-01 00:05:00 +0000 UTC. Single observation; the 2m escalation says whether it persists. Pods: kubectl -n frontend get pods,endpointslices -l app.kubernetes.io/name=sigoalumni",
        "summary": "OBSERVATION: the sigoalumni Service had zero ready endpoints at the last evaluation (desired replicas > 0, corroborated by not-ready pods or endpoints). This is a sampled instant, not a measured outage duration.",
    },
    "sus": {
        "description": "sigoalumni (frontend) is still without ready endpoints after 2m of fresh samples. Pods: kubectl -n frontend get pods,endpointslices -l app.kubernetes.io/name=sigoalumni",
        "summary": "The sigoalumni Service has had zero ready endpoints at every fresh evaluation for 2 minutes.",
    },
    "tel": {
        "description": "No fresh EndpointSlice series for the Service. No outage is claimed. Check kube-state-metrics, then kubectl -n frontend get svc,endpointslices,pods -l app.kubernetes.io/name=sigoalumni",
        "summary": "sigoalumni endpoint telemetry is incomplete (endpointslice_family_missing); an empty-Service verdict cannot be made. This is not an outage claim.",
    },
    "rdy": {
        "description": 'Readiness (/api/healthz) failed at least once. A burst on all pods at once points at the shared dependency (database path), one pod at that pod. By pod: sum by (pod) (increase(prober_probe_total{namespace="frontend",pod=~"sigoalumni-.*",probe_type="Readiness",result="failed"}[5m]))',
        "summary": "sigoalumni pods reported failed Readiness probes in the last 5 minutes (about 1.167 failures across the Service).",
    },
    "egr": {
        "description": 'Both probe targets failed. outcome=connect_failed: name resolved, TCP dial failed. dns_resolution_failed: name resolution failed for both (resolver possible, remote path not implied). mixed: one of each. Not evidence of WAN, router or ISP fault. Per-target series: probe_success{job=~"probe/observability/sigoalumni-egress-.+"}, and compare Gatus external_sigoalumni and the Cloud Run origin.',
        "summary": "Shared egress suspected (connect_failed): both sqladmin.googleapis.com:443 and www.cloudflare.com:443 failed from the blackbox exporter for two consecutive evaluations. Cause not localised.",
    },
}

# ---- O1: normal operation, and the October 16 reference ------------------------------
n = 12
groups.append(group(
    "O1 all ready, normal operation 12m: nothing fires (CronJob pod Ready=0 and another namespace's slice ignored)",
    ksm_feeds(n),
    none(tl(11), *ALL_KSM, RDY),
))

# 2026-10-16 11:29:00.993: ready=true series ABSENT from that one scrape, ready=false endpoints
# listed, every sigoalumni pod gauge explicitly 0, Service/Deployment/KSM up. The next scrape
# (6m) is healthy again.
oct16 = ksm_feeds(
    n,
    ep0=rep(1, 5) + ["S"] + rep(1, 6),
    ep1=rep(1, 5) + ["S"] + rep(1, 6),
    ep2=rep(1, 5) + ["S"] + rep(1, 6),
    pod0=rep(1, 5) + [0] + rep(1, 6),
    pod1=rep(1, 5) + [0] + rep(1, 6),
    pod2=rep(1, 5) + [0] + rep(1, 6),
    f0=ep_false(0, [None] * 5 + [1, "S"] + [None] * 5),
    f1=ep_false(1, [None] * 5 + [1, "S"] + [None] * 5),
    f2=ep_false(2, [None] * 5 + [1, "S"] + [None] * 5),
)
groups.append(group(
    "O1/O7 October 16 fixture: the one empty scrape is observed immediately (for:0); the 2m rule and telemetry warning stay quiet",
    oct16,
    [at("4m30s", OBS), at("5m", OBS, CRIT), at("5m30s", OBS, CRIT), at("6m", OBS), at("6m30s", OBS)]
    + none(tl(11), SUS, TEL, RDY),
))

# ---- O2: corroboration classes and the 19s pulse -------------------------------------
three_ep_gone = dict(ep0=rep(1, 5) + ["S"] + rep(1, 6), ep1=rep(1, 5) + ["S"] + rep(1, 6), ep2=rep(1, 5) + ["S"] + rep(1, 6))
groups.append(group(
    "O2a corroboration by ready=false endpoint series only (no pod gauge family at all): observation triggers",
    ksm_feeds(n, pod0=None, pod1=None, pod2=None, **three_ep_gone,
              f0=ep_false(0, [None] * 5 + [1, "S"] + [None] * 5)),
    [at("4m30s", OBS), at("5m", OBS, CRIT), at("6m", OBS)] + none(tl(11), TEL),
))
groups.append(group(
    "O2b corroboration by explicit all-pod-ready-zero only (no ready=false series): observation triggers",
    ksm_feeds(n, **three_ep_gone, pod0=rep(1, 5) + [0] + rep(1, 6), pod1=rep(1, 5) + [0] + rep(1, 6), pod2=rep(1, 5) + [0] + rep(1, 6)),
    [at("4m30s", OBS), at("5m", OBS, CRIT), at("6m", OBS)],
))
groups.append(group(
    "O2c zero ready=true with NO corroboration (pods report Ready=1, no not-ready endpoint): no critical; after 5m only the telemetry warning",
    ksm_feeds(14, ep0=rep(1, 3) + ["S"] * 11, ep1=rep(1, 3) + ["S"] * 11, ep2=rep(1, 3) + ["S"] * 11),
    none(tl(13), OBS, SUS) + [at("7m30s", TEL), at("8m", TEL, dict(WARN, reason="empty_uncorroborated"))],
))
# 19s pulse: the Service is empty between 4m20s and 4m39s. KSM scrapes at 4m and 5m both see 3
# ready endpoints, so nothing observable; kubelet's cumulative counter still moved.
fail = 'prober_probe_total{container="app",endpoint="https-metrics",job="kubelet",namespace="frontend",pod="%s",probe_type="Readiness",result="failed",service="kube-prometheus-stack-kubelet"}'
unsampled = ksm_feeds(n) + [feed(fail % p, rep(7, 5) + rep(9, 7)) for p, _ in PODS]
groups.append(group(
    "O2d 19s pulse UNSAMPLED (falls between 60s scrapes): no empty-Service alert at all, but the readiness counter jump is still alerted",
    unsampled,
    none(tl(11), OBS, SUS, TEL) + [at("4m30s", RDY), at("5m", RDY, WARN), at("8m", RDY, WARN)],
))
sampled = ksm_feeds(
    n, **three_ep_gone,
    pod0=rep(1, 5) + [0] + rep(1, 6), pod1=rep(1, 5) + [0] + rep(1, 6), pod2=rep(1, 5) + [0] + rep(1, 6),
    f0=ep_false(0, [None] * 5 + [1, "S"] + [None] * 5),
)
groups.append(group(
    "O2e same 19s pulse SAMPLED (a scrape lands inside it): observation fires at that evaluation - explicitly different from O2d",
    sampled + [feed(fail % p, rep(7, 5) + rep(9, 7)) for p, _ in PODS],
    [at("4m30s", OBS), at("5m", OBS, CRIT), at("6m", OBS)] + none(tl(11), SUS),
))

# ---- O3: missing / stale telemetry never fabricates an outage ------------------------
fam = dict(slice=rep(1, 3) + ["S"] * 11, ep0=rep(1, 3) + ["S"] * 11, ep1=rep(1, 3) + ["S"] * 11, ep2=rep(1, 3) + ["S"] * 11)
groups.append(group(
    "O3a EndpointSlice family missing entirely (pods still Ready=1): no critical; telemetry warning endpointslice_family_missing after 5m",
    ksm_feeds(14, **fam),
    none(tl(13), OBS, SUS) + [at("7m30s", TEL), at("8m", TEL, dict(WARN, reason="endpointslice_family_missing"))],
))
groups.append(group(
    "O3a2 EndpointSlice family missing AND pods Ready=0: still no critical (missing collection alone), telemetry warning",
    ksm_feeds(14, pod0=rep(1, 3) + rep(0, 11), pod1=rep(1, 3) + rep(0, 11), pod2=rep(1, 3) + rep(0, 11), **fam),
    none(tl(13), OBS, SUS) + [at("8m", TEL, dict(WARN, reason="endpointslice_family_missing"))],
))
down = {k: rep(1, 3) + ["S"] * 11 for k in ("svc", "dep", "slice", "ep0", "ep1", "ep2", "pod0", "pod1", "pod2")}
groups.append(group(
    "O3b KSM down (up=0, its series stale): no critical, no endpoint claim; ksm_down_or_stale warning after 5m",
    ksm_feeds(14, up=rep(1, 3) + rep(0, 11), **down),
    none(tl(13), OBS, SUS) + [at("7m30s", TEL), at("8m", TEL, dict(WARN, reason="ksm_down_or_stale"))],
))
gap = {k: rep(1, 3) + [None] * 11 for k in ("up", "svc", "dep", "slice", "ep0", "ep1", "ep2", "pod0", "pod1", "pod2")}
groups.append(group(
    "O3c KSM scrape stops without staleness markers (Prometheus holds last values 5m): freshness gate sees them aged out - no critical, stale warning",
    ksm_feeds(14, **gap),
    none(tl(13), OBS, SUS) + [at("8m", TEL), at("9m", TEL, dict(WARN, reason="ksm_down_or_stale"))],
))
# Zero first seen at 2m, then every KSM series stops updating: the held zero is fresh for at most
# 90s, so the observation fires at 2m..3m30s and the 2m rule can never accumulate on stale samples.
held = {k: rep(1, 3) + [None] * 9 for k in ("up", "svc", "dep", "slice")}
held.update(ep0=rep(1, 2) + ["S"] + [None] * 9, ep1=rep(1, 2) + ["S"] + [None] * 9, ep2=rep(1, 2) + ["S"] + [None] * 9,
            pod0=rep(1, 2) + [0] + [None] * 9, pod1=rep(1, 2) + [0] + [None] * 9, pod2=rep(1, 2) + [0] + [None] * 9)
groups.append(group(
    "O3d stale-held zero: observation only while the sample is <=90s old; sustained 2m rule never fires on held samples",
    ksm_feeds(12, **held),
    [at("1m30s", OBS), at("2m", OBS, CRIT), at("3m30s", OBS, CRIT), at("4m", OBS)] + none(tl(11), SUS),
))
groups.append(group(
    "O3e Service object absent while Deployment wants replicas: no fabricated zero; service_absent_desired_replicas warning",
    ksm_feeds(14, svc=rep(1, 3) + ["S"] * 11),
    none(tl(13), OBS, SUS) + [at("8m", TEL, dict(WARN, reason="service_absent_desired_replicas"))],
))
groups.append(group(
    "O3f Deployment series missing while Service exists: deployment_metric_missing_or_stale warning only",
    ksm_feeds(14, dep=rep(3, 3) + ["S"] * 11),
    none(tl(13), OBS, SUS) + [at("8m", TEL, dict(WARN, reason="deployment_metric_missing_or_stale"))],
))
groups.append(group(
    "O3g desired replicas 0 (intentionally scaled down), no ready endpoints: nothing at all",
    ksm_feeds(14, dep=rep(0, 14), ep0=rep(1, 2) + ["S"] * 12, ep1=rep(1, 2) + ["S"] * 12, ep2=rep(1, 2) + ["S"] * 12,
              pod0=rep(0, 14), pod1=rep(0, 14), pod2=rep(0, 14)),
    none(tl(13), *ALL_KSM),
))

# ---- O4: churn must not look like a zero or double-count -----------------------------
NEW = "sigoalumni-q7x2m"
new_eps = {
    f"nep{i}": (f'kube_endpointslice_endpoints{{{KSM},namespace="frontend",endpointslice="{NEW}",address="{PODS[i][1]}",ready="true",serving="true",terminating="false",targetref_kind="Pod",targetref_name="{PODS[i][0]}",service="kube-state-metrics"}}',
                [None] * 2 + [1] * 10)
    for i in range(3)
}
rot = ksm_feeds(
    n, **new_eps,
    slice=(f'kube_endpointslice_info{{{KSM},namespace="frontend",endpointslice="{SLICE}",service="kube-state-metrics"}}', rep(1, 3) + ["S"] + [None] * 8),
    ep0=rep(1, 3) + ["S"] + [None] * 8, ep1=rep(1, 3) + ["S"] + [None] * 8, ep2=rep(1, 3) + ["S"] + [None] * 8,
    nslice=(f'kube_endpointslice_info{{{KSM},namespace="frontend",endpointslice="{NEW}",service="kube-state-metrics"}}', [None] * 2 + [1] * 10),
)
groups.append(group(
    "O4a EndpointSlice rotation (old+new slice overlap 2m-3m with the same addresses): no alert; overlap is counted once, not twice",
    rot,
    none(tl(11), *ALL_KSM),
    exprs=[{
        "expr": 'sum(max by (address) (kube_endpointslice_endpoints{namespace="frontend",endpointslice=~"sigoalumni-[a-z0-9]{5}",targetref_kind="Pod",targetref_name=~"sigoalumni-[a-z0-9]+-[a-z0-9]{5}",ready="true"}))',
        "eval_time": "2m30s", "exp_samples": [{"labels": "{}", "value": 3}],
    }],
))
# Rolling update, maxUnavailable 0: pod C replaced by D (new address, not ready for a minute), the old pod terminates.
D_ADDR = "10.42.5.77"
roll = ksm_feeds(
    n,
    ep2=rep(1, 4) + ["S"] * 8,
    pod2=rep(1, 4) + ["S"] * 8,
    f2=ep_false(2, [None] * 4 + [1, 1] + [None] * 6, terminating="true"),
    d_false=(f'kube_endpointslice_endpoints{{{KSM},namespace="frontend",endpointslice="{SLICE}",address="{D_ADDR}",ready="false",serving="false",terminating="false",targetref_kind="Pod",targetref_name="sigoalumni-7bb7489855-d4c7x",service="kube-state-metrics"}}', [None] * 3 + [1, 1] + ["S"] + [None] * 6),
    d_true=(f'kube_endpointslice_endpoints{{{KSM},namespace="frontend",endpointslice="{SLICE}",address="{D_ADDR}",ready="true",serving="true",terminating="false",targetref_kind="Pod",targetref_name="sigoalumni-7bb7489855-d4c7x",service="kube-state-metrics"}}', [None] * 5 + [1] * 7),
    d_pod=(f'kube_pod_status_ready{{{KSMP},namespace="frontend",pod="sigoalumni-7bb7489855-d4c7x",condition="true",service="kube-state-metrics"}}', [None] * 3 + [0, 0] + [1] * 7),
)
groups.append(group(
    "O4b deployment roll with a not-ready surge pod and a terminating old pod: ready count never 0, no alert",
    roll,
    none(tl(11), *ALL_KSM),
))
one_down = ksm_feeds(n, ep2=rep(1, 4) + ["S"] * 8, pod2=rep(1, 4) + [0] * 8, f2=ep_false(2, [None] * 4 + [1] * 8))
groups.append(group(
    "O4c single pod terminates / goes unready (2 of 3 still ready): no alert",
    one_down,
    none(tl(11), *ALL_KSM),
))
groups.append(group(
    "O4d duplicate HA scrape identities, normal operation: no alert; ready endpoints are counted once (3, not 6)",
    ksm_feeds(n, ha=True),
    none(tl(11), *ALL_KSM),
    exprs=[{
        "expr": 'sum(max by (address) (kube_endpointslice_endpoints{namespace="frontend",endpointslice=~"sigoalumni-[a-z0-9]{5}",targetref_kind="Pod",targetref_name=~"sigoalumni-[a-z0-9]+-[a-z0-9]{5}",ready="true"}))',
        "eval_time": "3m", "exp_samples": [{"labels": "{}", "value": 3}],
    }],
))
oct16_ha = ksm_feeds(
    n, ha=True,
    ep0=rep(1, 5) + ["S"] + rep(1, 6), ep1=rep(1, 5) + ["S"] + rep(1, 6), ep2=rep(1, 5) + ["S"] + rep(1, 6),
    pod0=rep(1, 5) + [0] + rep(1, 6), pod1=rep(1, 5) + [0] + rep(1, 6), pod2=rep(1, 5) + [0] + rep(1, 6),
)
groups.append(group(
    "O4e duplicate HA scrape identities during the October 16 style zero: exactly ONE observation alert (no label fan-out)",
    oct16_ha,
    [at("4m30s", OBS), at("5m", OBS, CRIT), at("6m", OBS)],
))

# ---- O5: readiness-failure counter ----------------------------------------------------
def rd(pod, minutes, result="failed"):
    return feed(fail.replace('result="failed"', f'result="{result}"') % pod, minutes)

P0, P1, P2 = (p for p, _ in PODS)
groups.append(group(
    "O5a only successful probes (no failed series yet): nothing",
    [rd(P0, rep(1000, 10), "successful"), rd(P1, rep(1000, 10), "successful")],
    none(tl(9), RDY),
))
groups.append(group(
    "O5b one failed probe on one pod: warning for ~5m of window, then clears; no per-pod label",
    [rd(P0, rep(5, 3) + rep(6, 11)), rd(P1, rep(0, 14)), rd(P2, rep(0, 14))],
    [at("2m", RDY), at("3m", RDY, WARN), at("6m", RDY, WARN), at("8m", RDY)],
))
groups.append(group(
    "O5c all three pods fail twice (W1 shape): ONE warning for the whole Service, not three",
    [rd(P0, rep(3, 3) + rep(5, 11)), rd(P1, rep(3, 3) + rep(5, 11)), rd(P2, rep(3, 3) + rep(5, 11))],
    [at("2m", RDY), at("3m", RDY, WARN), at("5m", RDY, WARN)],
    exprs=[{
        "expr": 'count(increase(prober_probe_total{namespace="frontend",pod=~"sigoalumni-.*",probe_type="Readiness",result="failed"}[5m]) > 0)',
        "eval_time": "3m", "exp_samples": [{"labels": "{}", "value": 3}],
    }],
))
groups.append(group(
    "O5d counter reset (pod restart 40 -> 0) with no new failures: no negative count, no alert",
    [rd(P0, rep(40, 4) + rep(0, 10))],
    none(tl(12), RDY),
))
groups.append(group(
    "O5e counter reset followed by two new failures: fires with the post-reset increase",
    [rd(P0, rep(40, 4) + [0, 0, 2, 2, 2, 2])],
    [at("4m30s", RDY), at("6m", RDY, WARN)],
))
groups.append(group(
    "O5f scrape gap (3 missed scrapes) around one failure: no alert during the gap, increase recovered afterwards, never negative",
    [rd(P0, rep(5, 3) + [None, None, None, 6] + rep(6, 6))],
    [at("3m", RDY), at("5m", RDY), at("6m", RDY, WARN), at("6m30s", RDY, WARN), at("7m", RDY), at("9m", RDY)],
    exprs=[{
        "expr": 'sum(increase(prober_probe_total{namespace="frontend",pod=~"sigoalumni-.*",probe_type="Readiness",result="failed"}[5m])) >= 0',
        "eval_time": "5m", "exp_samples": [{"labels": "{}", "value": 0}],
    }],
))

# ---- O6: shared egress suspected -------------------------------------------------------
SQL = 'job="probe/observability/sigoalumni-egress-sqladmin",instance="sqladmin.googleapis.com:443",namespace="observability"'
CF = 'job="probe/observability/sigoalumni-egress-cloudflare",instance="www.cloudflare.com:443",namespace="observability"'


def probe(side, success, proto, up=None, n15=24):
    lbl = SQL if side == "sql" else CF
    ok = [1] * n15 if up is None else up
    return [
        {"series": f"probe_success{{{lbl}}}", "values": s15(success)},
        {"series": f"probe_ip_protocol{{{lbl}}}", "values": s15(proto)},
        {"series": f"up{{{lbl}}}", "values": s15(ok)},
    ]


N = 24  # 6 minutes of 15s samples
good = rep(1, N)
four = rep(4, N)
zero = rep(0, N)
ONE = {"outcome": "connect_failed", "target_group": "sigoalumni-shared-egress"}
EG = dict(WARN, **ONE)
fail_from_2m = rep(1, 8) + rep(0, N - 8)  # failing from t=2m (index 8 at 15s)
fail_2m_to_2m15 = rep(1, 8) + [0] + rep(1, N - 9)
groups.append(group(
    "O6a both targets healthy: nothing", probe("sql", good, four) + probe("cf", good, four),
    none(tl(5), EGR), interval="15s"))
groups.append(group(
    "O6b both targets fail TCP connect from 2m: pending at the first evaluation point, warning at the second (30s later), outcome connect_failed",
    probe("sql", fail_from_2m, four) + probe("cf", fail_from_2m, four),
    [at("1m30s", EGR), at("2m", EGR), at("2m30s", EGR, EG), at("4m", EGR, EG)], interval="15s"))
groups.append(group(
    "O6c only ONE target fails (diagnostic, no alert)",
    probe("sql", fail_from_2m, four) + probe("cf", good, four),
    none(tl(5), EGR), interval="15s"))
groups.append(group(
    "O6c2 other single target fails",
    probe("sql", good, four) + probe("cf", fail_from_2m, four),
    none(tl(5), EGR), interval="15s"))
groups.append(group(
    "O6d both fail for one 15s sample only (<2 evaluation points): no alert",
    probe("sql", fail_2m_to_2m15, four) + probe("cf", fail_2m_to_2m15, four),
    none(tl(5), EGR), interval="15s"))
groups.append(group(
    "O6e blackbox exporter / scrape down (up=0, probe series stale): no alert",
    probe("sql", rep(1, 8) + ["S"] * (N - 8), four, up=rep(1, 8) + rep(0, N - 8)) +
    probe("cf", rep(1, 8) + ["S"] * (N - 8), four, up=rep(1, 8) + rep(0, N - 8)),
    none(tl(5), EGR), interval="15s"))
groups.append(group(
    "O6e2 one failed sample, then results stop arriving without staleness markers (held values): freshness gate, no alert",
    probe("sql", rep(1, 8) + rep(0, 1) + [None] * (N - 9), four, up=rep(1, 9) + [None] * (N - 9)) +
    probe("cf", rep(1, 8) + rep(0, 1) + [None] * (N - 9), four, up=rep(1, 9) + [None] * (N - 9)),
    none(tl(5), EGR), interval="15s"))
dns0 = rep(4, 8) + rep(0, N - 8)
groups.append(group(
    "O6f DNS failure on both (probe_ip_protocol 0): suspected, but classified dns_resolution_failed, never as remote path loss",
    probe("sql", fail_from_2m, dns0) + probe("cf", fail_from_2m, dns0),
    [at("2m", EGR), at("2m30s", EGR, dict(WARN, outcome="dns_resolution_failed", target_group="sigoalumni-shared-egress"))], interval="15s"))
groups.append(group(
    "O6f2 one DNS failure plus one connect failure: outcome mixed",
    probe("sql", fail_from_2m, dns0) + probe("cf", fail_from_2m, four),
    [at("2m", EGR), at("2m30s", EGR, dict(WARN, outcome="mixed", target_group="sigoalumni-shared-egress"))], interval="15s"))
groups.append(group(
    "O6g recovery: both recover after 2m15s, alert resolves",
    probe("sql", rep(1, 8) + rep(0, 10) + rep(1, N - 18), four) + probe("cf", rep(1, 8) + rep(0, 10) + rep(1, N - 18), four),
    [at("2m30s", EGR, EG), at("4m", EGR, EG), at("5m30s", EGR)], interval="15s"))
groups.append(group(
    "O6h one probe series missing entirely (target removed / not yet scraped): count != 2, no alert",
    probe("sql", fail_from_2m, four),
    none(tl(5), EGR), interval="15s"))

# ---- O7: immediate vs sustained --------------------------------------------------------
def zero_for(first, last_inclusive, n=14):
    """ready=true absent from scrape minute `first` through `last_inclusive`."""
    v = {}
    for k in ("ep0", "ep1", "ep2"):
        v[k] = [1 if (m < first or m > last_inclusive) else "S" for m in range(n)]
    for k in ("pod0", "pod1", "pod2"):
        v[k] = [1 if (m < first or m > last_inclusive) else 0 for m in range(n)]
    return v

groups.append(group(
    "O7a zero visible in 2 scrapes (5m, 6m): observation at 5m..6m30s; the 2m rule never fires (needs fresh true at 5m and 7m)",
    ksm_feeds(14, **zero_for(5, 6)),
    [at("5m", OBS, CRIT), at("6m", OBS, CRIT), at("6m30s", OBS, CRIT), at("7m", OBS)] + none(tl(13), SUS),
))
groups.append(group(
    "O7b zero visible in 3 scrapes (5m, 6m, 7m): observation immediately; sustained 2m rule pending until the 7m evaluation, then fires",
    ksm_feeds(14, **zero_for(5, 7)),
    [at("5m", OBS, CRIT), at("5m", SUS), at("6m30s", SUS), at("7m", SUS, CRIT), at("7m30s", SUS, CRIT), at("8m", SUS)],
))

# ---- annotation rendering (full rules, exact strings) ---------------------------------
# The behaviour groups above run against a copy of the rules with annotations removed, so
# wording changes do not churn 40 assertions. These few groups run against the real rules and
# pin the rendered annotation text (template syntax, {{ query }}, $labels, $value).
ANN = {}  # filled below from ANN_TEXT


def ann_groups():
    out = []
    out.append(group(
        "ANN OBS/SUS observed + sustained annotations render",
        ksm_feeds(14, **zero_for(5, 7)),
        [at("5m", OBS, CRIT, ann=ANN_TEXT["obs"]), at("7m", SUS, CRIT, ann=ANN_TEXT["sus"])],
    ))
    out.append(group(
        "ANN TEL annotations render with the reason",
        ksm_feeds(14, **fam),
        [at("8m", TEL, dict(WARN, reason="endpointslice_family_missing"), ann=ANN_TEXT["tel"])],
    ))
    out.append(group(
        "ANN RDY annotations render with the humanized increase",
        [rd(P0, rep(5, 3) + rep(6, 11)), rd(P1, rep(0, 14)), rd(P2, rep(0, 14))],
        [at("3m", RDY, WARN, ann=ANN_TEXT["rdy"])],
    ))
    out.append(group(
        "ANN EGR annotations render with the outcome",
        probe("sql", fail_from_2m, four) + probe("cf", fail_from_2m, four),
        [at("2m30s", EGR, EG, ann=ANN_TEXT["egr"])], interval="15s",
    ))
    return out


def y(v, ind=0):
    pad = "  " * ind
    out = []
    if isinstance(v, dict):
        for k, val in v.items():
            if isinstance(val, (dict, list)) and val:
                out.append(f"{pad}{k}:")
                out.append(y(val, ind + 1))
            elif isinstance(val, (dict, list)):
                out.append(f"{pad}{k}: {'{}' if isinstance(val, dict) else '[]'}")
            else:
                out.append(f"{pad}{k}: {q(val)}")
    else:
        for item in v:
            if isinstance(item, dict):
                first = True
                for k, val in item.items():
                    lead = f"{pad}- " if first else f"{pad}  "
                    first = False
                    if isinstance(val, (dict, list)) and val:
                        out.append(f"{lead}{k}:")
                        out.append(y(val, ind + 2))
                    elif isinstance(val, (dict, list)):
                        out.append(f"{lead}{k}: {'{}' if isinstance(val, dict) else '[]'}")
                    else:
                        out.append(f"{lead}{k}: {q(val)}")
            else:
                out.append(f"{pad}- {q(item)}")
    return "\n".join(out)


def q(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


mode = sys.argv[1] if len(sys.argv) > 1 else "behavior"
header = """# GENERATED by generate_fixtures.py (%s) - do not edit by hand.
# promtool unit tests for kubernetes/main/apps/frontend/sigoalumni/app/prometheusrule.yaml
# (plan O1-O7). run.sh extracts the rules next to a copy of this file (rules.yaml, plus
# rules-noann.yaml with annotations removed for the behaviour matrix).
"""
doc = {
    "rule_files": ["rules-noann.yaml" if mode == "behavior" else "rules.yaml"],
    "evaluation_interval": "30s",
    "tests": groups if mode == "behavior" else ann_groups(),
}
sys.stdout.write(header % mode + y(doc) + "\n")
