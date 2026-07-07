# alert-responder

**Track-B1 of the 2026-07-06 agent-ops plan: ENRICH, DON'T ACT.** A CronJob that
watches Alertmanager for `severity=critical` alerts and, for each **new**
incident, runs **one read-only Claude Code diagnosis in-pod** and pages the result
to Pushover **priority 0** — a follow-up to the priority-1 page Alertmanager
already sent. It never touches the cluster. The human stays the actuator; graduated
per-playbook actuation is **phase B2 (future work)**.

Where the upgrade-shepherd family acts on *upgrade*-attributable regressions, the
responder is the enrichment layer for **any** firing critical (hardware, HA, Ceph,
a crash-loop with no recent merge) — the alerts a human would otherwise wake up and
grep for at 3AM.

| | |
|---|---|
| **Object** | CronJob `alert-responder`, ns `upgrade-agent`, schedule `*/10` |
| **Image** | `ghcr.io/thaynes43/upgrade-shepherd` (shares the shepherd image; different entrypoint) |
| **Source** | [`alert-responder/app/resources/respond.sh`](../../kubernetes/main/apps/upgrade-agent/alert-responder/app/resources/respond.sh) — ConfigMap-mounted at `/opt/responder`, so the whole logic is auditable in PR diffs (no baked-in behaviour) |
| **Reads** | Alertmanager `kube-prometheus-stack-alertmanager.observability.svc.cluster.local:9093`, API **v2**, `active` + unsilenced + uninhibited |
| **Selects** | `severity=critical`, allowlist `.*` ∩ **not** denylist `^(Watchdog\|InfoInhibitor\|AlertmanagerReceiversNotConfigured)$`, incident age ≤ 24h |
| **Healthy path** | one curl, **$0** — no new incident → no clone, no LLM, no page |
| **Kill switch** | `kubectl -n upgrade-agent patch cronjob alert-responder -p '{"spec":{"suspend":true}}'` |

---

## What one run does

1. **Poll.** One curl to Alertmanager v2 for active/unsilenced/uninhibited
   `severity=critical` alerts. Apply allowlist ∩ ¬denylist ∩ age ≤ `RESPONDER_MAX_AGE_HOURS`.
2. **Key each incident.** `incident_key = sha256("<fingerprint>|<startsAt>")` first
   12 hex. **A resolve+refire is a NEW incident** (startsAt changed → new key) — a
   flapping alert re-diagnoses each firing, deliberately.
3. **Claim before summon (at-most-once).** For a new key, write the claim into the
   state CM **before** the LLM is summoned — crash-proof: a pod that dies mid-LLM
   still leaves the claim, so the incident is never double-diagnosed. **Unreadable
   state ⇒ FAIL CLOSED** (skip — Alertmanager has already paged the human, so the
   safe failure is *don't* spend, not *re-diagnose*).
4. **Diagnose (read-only, in-pod).** One Claude Code run: gather evidence with the
   allowlisted tools, produce the structured report.
5. **Page.** POST the report to Pushover **priority 0** with suffix
   `[auto-diagnosis — verify before acting]`. An **empty** report ⇒ **silent** (the
   original Alertmanager page stands on its own) but the incident is still recorded
   as *attempted* — no retry loop.
6. **Record spend**, prune state entries older than `RESPONDER_STATE_TTL_HOURS`.

`RESPONDER_MAX_PER_RUN` (default **1**) caps diagnoses per run — an alert storm
can't fan out into N concurrent LLM summons.

---

## The diagnosis: tools & report

Model `sonnet` (`RESPONDER_MODEL`), **25 turns**, **12m** timeout. Runs
`dontAsk` with a **read-only allowlist** — there is no path to a cluster write:

| Tool | Notes |
|---|---|
| `Read` / `Grep` / `Glob` | over the repo clone (below) |
| `kubectl get` / `kubectl describe` | via the shared read-only ClusterRole (no `get pods/log`, no exec) |
| `flux get` | reconcile state |
| `grep` / `cat` / `git log` / `git show` | repo introspection |
| `/opt/responder/prom-query.sh '<promql>'` | deterministic Prometheus wrapper |
| `/opt/responder/loki-query.sh '<logql>' [minutes] [limit]` | deterministic Loki wrapper — **this is how it reads pod logs**, because the shared ClusterRole deliberately denies `pods/log` |
| **disabled** | `WebFetch`, `WebSearch` — no egress to the open web for the LLM |

> **Pod logs come from Loki, not `kubectl logs`.** The shared read-only
> `upgrade-health-gate` ClusterRole denies `pods/log` (same posture as the Omni
> Reader SA). The `loki-query.sh` wrapper is the only log path — point the LLM at
> LogQL, not `kubectl logs`.

**Repo context = ANONYMOUS shallow clone** of the *public* repo. **No credential of
any kind exists in the pod** — no bot App PEM, no `ghs_` token, nothing that could
push or merge. If the clone fails, diagnosis proceeds **without** it (degraded, not
blocked).

**Report format the LLM must emit** (≤900 chars):

```
CAUSE: … | EVIDENCE: … | FIX: … | RUNBOOK: <path|none>
```

The wrapper pages that verbatim with the `[auto-diagnosis — verify before acting]`
suffix. Empty report ⇒ no page (see step 5).

---

## Env knobs

| Name | Default | Meaning |
|---|---|---|
| `RESPONDER_SEVERITY` | `critical` | AM severity label to select |
| `RESPONDER_ALERT_ALLOWLIST` | `.*` | regex — alertnames to include |
| `RESPONDER_ALERT_DENYLIST` | `^(Watchdog\|InfoInhibitor\|AlertmanagerReceiversNotConfigured)$` | regex — always-firing / meta alerts to drop |
| `RESPONDER_MAX_PER_RUN` | `1` | max diagnoses per run (storm cap) |
| `RESPONDER_MAX_AGE_HOURS` | `24` | ignore incidents older than this |
| `RESPONDER_STATE_TTL_HOURS` | `168` | prune state entries older than this (7d) |
| `RESPONDER_MODEL` | `sonnet` | Claude model for the diagnosis |
| `RESPONDER_MAX_TURNS` | `25` | LLM turn cap |
| `RESPONDER_MAX_BUDGET_USD` | `2.00` | per-run spend cap |
| `RESPONDER_MONTHLY_CAP_USD` | `15` | month-to-date spend cap (own envelope) |
| `RESPONDER_TIMEOUT` | `12m` | wall-clock per diagnosis |
| `RESPONDER_DRY` | `0` | `1` = log the page instead of sending it |

### Spend — its own envelope

Month-to-date spend lives in CM `alert-responder-spend` (runtime state, **NOT**
git-managed). Cap = `RESPONDER_MONTHLY_CAP_USD` (**$15/mo**) + `RESPONDER_MAX_BUDGET_USD`
(**$2/run**). **Deliberately separate from the shepherd's $50 envelope** — an alert
storm can't eat the upgrade budget, and vice-versa.

### State — its own CM, fail-closed

At-most-once state lives in CM `alert-responder-state` (runtime, **NOT** git-managed).
Claim is written **before** the summon (crash-proof). A read failure on this CM
**skips** the run (fail closed) rather than risking a double-diagnosis — the human
is already paged by Alertmanager regardless.

```bash
kubectl -n upgrade-agent get cm alert-responder-state  -o jsonpath='{.data}'   # handled incidents
kubectl -n upgrade-agent get cm alert-responder-spend  -o jsonpath='{.data}'   # month-to-date
kubectl -n upgrade-agent delete cm alert-responder-spend                        # reset (recreated next run)
```

---

## Containment

Least-privilege, no write path anywhere:

- **RBAC** — ClusterRoleBinding to the **shared read-only `upgrade-health-gate`
  ClusterRole** (same role the gate/shepherd verify on; no exec, no `pods/log`). A
  namespace `Role` grants read/write on **only its two CMs** (`-state`, `-spend`).
- **Egress** — a CiliumNetworkPolicy allows exactly: **DNS**, **kube-apiserver**,
  **observability ns** ports `9093`/`9090`/`3100`/`80`/`8080` (AM / Prometheus /
  Loki), **GitHub** (the anonymous clone), **api.anthropic.com** (the LLM),
  **api.pushover.net** (the page). **No ingress.**
- **DNS gotcha (2026-07-04 Cilium lesson):** the egress policy uses **exact
  `matchNames`** for every multi-label service FQDN — a `*` wildcard **does not
  cross dots** in Cilium DNS policy, so a wildcard would silently fail to resolve
  the AM/Prometheus/Loki service names. Enumerate each FQDN explicitly.

---

## Drill (proven live 2026-07-06, cost $0.18)

End-to-end test without touching a real critical. `warning` severity does **not**
page via Alertmanager (root route is `null`, only `critical` pages) — so a synthetic
`warning` alert exercises the whole path silently, and the responder is pointed at it
by overriding `RESPONDER_SEVERITY=warning` for the one run.

1. **Post a synthetic alert.** Port-forward Alertmanager, POST an alert with
   `severity=warning`, `alertname=ResponderDrill`, `endsAt` ~25 min out.
2. **Fire a one-off job** from the CronJob with the two overrides injected via `jq`:

   ```bash
   kubectl -n upgrade-agent create job responder-drill-$(date +%s) \
     --from=cronjob/alert-responder --dry-run=client -o json \
   | jq '.spec.template.spec.containers |= map(
       if .name=="app"
       then .env += [{name:"RESPONDER_SEVERITY",value:"warning"},
                     {name:"RESPONDER_ALERT_ALLOWLIST",value:"^ResponderDrill$"}]
       else . end)' \
   | kubectl apply -f -
   ```

3. **Expect:** claim logged → diagnosis logged + paged
   `[responder] ResponderDrill (upgrade-agent)` → `-state` **and** `-spend` CMs
   updated.
4. **Re-run the same job shape** → `already handled — skip` (**at-most-once proof**).

`RESPONDER_DRY=1` logs the page instead of sending it, if you want the diagnosis
without the Pushover notification.

> In the live 2026-07-06 drill the diagnosis **self-identified the synthetic alert
> as a drill**, found the drill job's own pod, checked `git log`, and concluded no
> action was needed — exactly the read-only "explain, don't act" behaviour intended.

---

## Relationship to the other upgrade-agent pods

Three CronJobs share ns `upgrade-agent` and the read-only ClusterRole; they do
**different jobs** and are **deliberately not all coupled**.

| Pod | Trigger | LLM? | Acts? | Scope |
|---|---|---|---|---|
| **gate** ([upgrade-health-gate](upgrade-health-gate.md)) | post-reconcile, every cycle | **no** — deterministic | no — pages only | upgrade health tripwire → Pushover |
| **triage** ([upgrade-shepherd](upgrade-shepherd.md) `upgrade-shepherd-triage`) | recent merge **AND** regression now | **no** — deterministic | auto-summons shepherd `remediate` | **upgrade-attributable** regressions only |
| **responder** (this) | **any** firing `severity=critical` | **yes** — sonnet, read-only | **never** — enriches + pages | **any** critical, upgrade-related or not |

- **gate** = the deterministic upgrade safety net (no LLM, page on regression).
- **triage** = the deterministic auto-summon of the shepherd's `remediate` mode when
  a regression is *plausibly caused by a recent merge*.
- **responder** = the LLM enrichment layer for **any** critical (hardware, HA, Ceph,
  a crash-loop with no merge behind it), **read-only, never remediates**.

**The responder is deliberately NOT wired to the gate/triage coordination CM.** It
is an independent read-only observer — it must not gate, block, or be blocked by the
upgrade-flow state, exactly as the health-gate stays the independent Pushover
tripwire and triage stays decoupled from it. Keep it that way.

---

## See also

- [upgrade-health-gate.md](upgrade-health-gate.md) — the deterministic post-reconcile gate the responder shares a ClusterRole with; healthy/benign-warn/regression criteria.
- [upgrade-shepherd.md](upgrade-shepherd.md) — the shepherd + triage auto-summon (the acting side of the family), its own $50 spend envelope, and the "deliberately decoupled" health-gate principle the responder follows.
- [omni-service-account.md](omni-service-account.md) — the read-only-SA posture (no exec, no `pods/log`) the responder's ClusterRole mirrors; why logs come via Loki.
