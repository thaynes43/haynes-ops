# Runbook: Kyverno enforce — periodic verification

**Run this monthly, and always right after you add a new operator/app or a new
StorageClass.** ~5 minutes. Runnable by you or by a summoned agent (all read-only).

## Why this exists

Kyverno's three enforce policies (`restrict-image-registries`, `restrict-rbac-escalation`,
`pod-security-baseline`) deny non-conforming resources at admission. The dangerous gap is
**ephemeral, controller-spawned pods** — a provisioner/operator creates a privileged
helper pod that lives 1–2 seconds, so a point-in-time scan never sees it, so it may not be
in the exceptions, so it gets **silently denied**. That already bit us twice:

- **OpenEBS localpv helper pods** denied → every new `openebs-hostpath` PVC hung `Pending`
  forever (fixed: `exceptions/pss-baseline.yaml` `init-pvc-*`/`cleanup-pvc-*`/`quota-pvc-*`).
- The reports-controller **OOMKilled** for a day unnoticed (fixed: raised its memory).

**The continuous watch is already automated** — the `kyverno-guardrail.rules`
PrometheusRule pages Pushover (critical) on OOM/controller-down/stuck-PVC/repeated-denial,
**with no agent running.** This runbook is the *periodic manual deep-check* for the slow
or transient cases that stay under the alert thresholds, plus the proactive
"did the thing I just deployed spawn a helper pod that's being blocked?" check.

> Exceptions live in `kubernetes/main/apps/kyverno/policies/app/exceptions/`. Never set a
> policy to `background: false` to enable `subjects:`-scoped exceptions — Kyverno forbids
> `subjects` under `background: true` (verified 2026-07-03), and losing background scans
> blinds this whole runbook + the audit reports. Name/namespace-scoped exceptions only.

---

## The check (run all three blocks; all-green = nothing to do)

```bash
PROXY="/api/v1/namespaces/observability/services/http:kube-prometheus-stack-prometheus:9090/proxy/api/v1"
q() { kubectl get --raw "${PROXY}/query?query=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$1")" 2>/dev/null; }

# ── 1. Did ENFORCE block anything real? (admission_request + enforce + fail — NOT audit,
#       NOT background scan.) Use a window matching your cadence, e.g. [31d] if monthly.
#       Any non-zero => something got denied at admission; go to "On a finding".
q 'sum by (policy_name) (increase(kyverno_policy_results_total{rule_execution_cause="admission_request",policy_validation_mode="enforce",rule_result="fail"}[31d]))' \
  | jq -r '.data.result[] | "\(.metric.policy_name): \(.value[1]|tonumber|floor) blocks"'

# ── 2. Any stuck provisioning right now? (the OpenEBS-class symptom) ──
kubectl get pvc -A --no-headers | awk '$4=="Pending"{print $1"/"$2" PENDING"}'
kubectl get pods -A --field-selector=status.phase=Pending --no-headers \
  | grep -v Completed | awk '{print $1"/"$2}' | head

# ── 3. Guardrail health: Kyverno controllers not OOM/crash-looping, alerts loaded ──
kubectl get pods -n kyverno -o json | jq -r '.items[]|select(.status.containerStatuses)|
  "\(.metadata.name): restarts=\([.status.containerStatuses[].restartCount]|add) oom=\(any(.status.containerStatuses[];.lastState.terminated.reason=="OOMKilled"))"'
kubectl get --raw "${PROXY}/rules" | jq -r '[.data.groups[]|select(.name=="kyverno-guardrail.rules")|.rules[]]
  | "guardrail alerts: loaded=\(length) firing=\([.[]|select(.state=="firing")]|length) errored=\([.[]|select(.health!="ok")]|length)"'
```

**All-green looks like:** block counts `0` for every policy; no `PENDING` PVCs/pods
(that aren't legitimately WaitForFirstConsumer with no consumer); every controller
`restarts=0 oom=false`; `loaded=4 firing=0 errored=0`.

> Metric history began 2026-07-03 (when Kyverno metrics scraping was enabled), so a `[31d]`
> window only reflects data since then until ~2026-08-03.

---

## On a finding

**Block count > 0 (check 1) or a stuck PVC/pod (check 2)** — find *what* was denied:

```bash
kubectl get events -A --field-selector reason=PolicyViolation | tail          # if recent (events expire ~1h)
kubectl logs -n kyverno -l app.kubernetes.io/component=admission-controller --since=48h \
  | grep -iE 'blocked|resource .* was blocked' | grep -vi 'dry.?run' | tail
kubectl describe pvc <ns>/<name> | tail                                        # for a Pending PVC: the ProvisioningFailed event names the policy
```

Then decide:
- **Legit block** (a genuinely bad manifest / an attack was denied) → the guardrail worked;
  no change. Note it.
- **Exception gap** (a legitimate controller-spawned/ephemeral pod was denied — the OpenEBS
  shape) → add a **scoped** PolicyException to
  `kubernetes/main/apps/kyverno/policies/app/exceptions/` (namespace + name globs; match a
  stable pod label if the controller sets one). Commit → Flux → confirm the block clears.

**A controller OOM/crash (check 3)** → raise its memory in
`kubernetes/main/apps/kyverno/kyverno/app/helmrelease.yaml` (`<controller>.resources`), like
the reports-controller bump to 256Mi/512Mi.

---

## After you add a new operator / app / StorageClass (do this proactively)

New controllers are the usual source of a new ephemeral-helper class. Right after deploying:

1. **Exercise it** so it spawns its helpers: for a StorageClass/provisioner, create a scratch
   PVC + a consumer pod; for a backup operator, trigger a run; for an app, deploy it.
   ```bash
   kubectl apply -f - <<'EOF'
   apiVersion: v1
   kind: PersistentVolumeClaim
   metadata: { name: probe, namespace: default }
   spec: { accessModes: ["ReadWriteOnce"], resources: { requests: { storage: 100Mi } }, storageClassName: <new-sc> }
   EOF
   kubectl run probe-c --image=docker.io/library/busybox:1.37.0 --restart=Never -n default \
     --overrides='{"spec":{"volumes":[{"name":"v","persistentVolumeClaim":{"claimName":"probe"}}],"containers":[{"name":"c","image":"docker.io/library/busybox:1.37.0","command":["sleep","20"],"volumeMounts":[{"name":"v","mountPath":"/v"}]}]}}'
   kubectl get pvc probe -n default -w     # Bound = OK; Pending + ProvisioningFailed/denied = an exception gap
   kubectl delete pod probe-c -n default; kubectl delete pvc probe -n default
   ```
2. **Re-run check 1** (block count) — a fresh non-zero means the new thing hit an exception gap.
3. If blocked → add the scoped exception (as above), re-exercise, confirm Bound/Ready.

---

## Cross-references
- Exceptions: [`kubernetes/main/apps/kyverno/policies/app/exceptions/`](../../kubernetes/main/apps/kyverno/policies/app/exceptions/)
- Alerts: `kubernetes/main/apps/kyverno/kyverno/app/prometheusrule.yaml` (the always-on watch)
- Incident background: agent memory `opsplex-launch-gotchas`; the OpenEBS bug report was
  `HANDOFF-2026-07-02-kyverno-ephemeral-helpers.md`.
