# 05 — kubectl RBAC tiers for the dev-env ServiceAccount

**Status:** backlog — blocked on Decision log #1
**Depends on:** 02
**Parallel with:** 03, 04

## Goal

In-cluster kubectl that covers the *operational breadth Claude actually uses today
from the Mac* without handing a yolo agent cluster-admin. The current Mac session
(via Omni kubeconfig) is effectively cluster-admin; the question is what subset the
pod gets (Decision log #1).

## What "breadth of capabilities you have now" actually decomposes to

Audit of the verbs used in real sessions (CLAUDE.md workflow + runbooks + memory):

- **Read everything**: get/list/watch all resources, all namespaces; logs; events.
  (The existing `upgrade-health-gate` ClusterRole minus its secrets exclusion — note
  cluster-inspection.md says never dump Secrets; sops/age work happens in-repo, not
  via kubectl.)
- **Targeted writes** (the GitOps-legal mutations):
  - `delete pods` (restart an app; clear a wedged pod)
  - `patch deployments/statefulsets/daemonsets` (rollout restart = annotation patch)
  - `patch/annotate` Flux CRs (`flux reconcile`, suspend/resume)
  - `create/delete jobs` (volsync unlock/restore manual jobs)
  - `patch cronjobs` (suspend kill-switches)
  - ConfigMap create/update in designated runtime-state namespaces
- **Explicitly withheld**: secrets read, exec into other pods, node ops, RBAC/webhook
  mutation, namespace/CRD delete, anything in kube-system beyond read.

## Proposed tiers (pick one at Decision #1, revisit after PoC)

1. **read-only** (PoC default, already in 02) — reuse `upgrade-health-gate`.
2. **operator** (recommended target) — read-all + the targeted-write list above as a
   new `dev-env-operator` ClusterRole. Matches the "restart/reconcile/verify" loop
   without cluster-admin. Verify against `restrict-rbac-escalation` Kyverno policy;
   add an exception in `exceptions/rbac-legit.yaml` if the binding trips it.
3. **cluster-admin** — full breadth, maximum blast radius; only if the operator tier
   proves too annoying in practice AND we accept saga Hard news #1 in full.

Optional refinement: TWO SAs — default kubeconfig context is `operator`; a second
projected token for `read-only` that dispatched (unattended) agents get, so yolo
autonomy and human-driven sessions carry different power.

## Acceptance

- From in-pod: `kubectl get pods -A`, `flux get ks -A`, `kubectl rollout restart
  deploy/<app>`, `flux reconcile ks <app> -n <ns> --with-source`, `kubectl delete pod`
  all behave per the chosen tier; withheld verbs are denied and the denial is clean
  (agents shouldn't retry-loop on RBAC errors).
- Kyverno reports no violations (or a documented exception exists).
