#!/usr/bin/env bash
# No cluster/network/LLM calls: execute the shipped collector and responder filter
# against held H7 plus adjacent real failures. Requires bash, jq, awk.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="${SRC:-$ROOT/kubernetes/main/apps/upgrade-agent/health-gate/app/resources/coordination-lib.sh}"
RESP="${RESP:-$ROOT/kubernetes/main/apps/upgrade-agent/alert-responder/app/resources/respond.sh}"
. "$SRC"
NOW=1792015200
PROM=http://unused.invalid
log() { printf '%s\n' "$*" >&2; }
FLUX="$(jq -cn '
  {kind:"Kustomization", metadata:{namespace:"dev-env-system", name:"dev-env-h7-metadata-audit"},
   status:{conditions:[{type:"Ready",status:"False",lastTransitionTime:"2026-10-13T21:06:34Z"}]}} as $h
  | {items:[$h, ($h|.metadata.name="another-audit"),
     ($h|.metadata.namespace="another-namespace"), ($h|.kind="HelmRelease")]}')"
POD='{"metadata":{"ownerReferences":[{"kind":"Job","name":"dev-env-h7-metadata-audit-1013a"}]},"status":{"phase":"Failed"}}'
POD_RC=0
kubectl() {
  case "$*" in
    *"get kustomizations."*) printf '%s' "$FLUX" ;;
    *"get pod "*) [ "$POD_RC" -eq 0 ] || return 1; printf '%s' "$POD" ;;
    *) echo "unexpected kubectl call: $*" >&2; return 1 ;;
  esac
}
curl() { printf '{"data":{"result":[]}}'; }
collect_regressions
[ "$SIG_PODS_STATUS" = ok ]
[ "$(printf '%s\n' "$REG_IDS" | wc -l | tr -d ' ')" = 3 ]
! printf '%s\n' "$REG_IDS" | grep -qx 'flux/Kustomization/dev-env-system/dev-env-h7-metadata-audit'
printf '%s\n' "$REG_IDS" | grep -qx 'flux/HelmRelease/dev-env-system/dev-env-h7-metadata-audit'
printf '%s\n' "$REG_IDS" | grep -qx 'flux/Kustomization/another-namespace/dev-env-h7-metadata-audit'
printf '%s\n' "$REG_IDS" | grep -qx 'flux/Kustomization/dev-env-system/another-audit'
echo 'ok collector: only exact held Flux resource excluded; unrelated failures retained'
pod_is_held_audit dev-env-system retained-pod
! pod_is_held_audit other-namespace retained-pod
POD="$(printf '%s' "$POD" | jq '.status.phase="Pending"')"
! pod_is_held_audit dev-env-system retained-pod
POD="$(printf '%s' "$POD" | jq '.status.phase="Failed" | .metadata.ownerReferences[0].name="dev-env-h7-metadata-audit-next"')"
! pod_is_held_audit dev-env-system replacement-pod
POD_RC=1
! pod_is_held_audit dev-env-system unreadable-pod
echo 'ok pod hold: only retained terminal failure; pending/replacement/unreadable untouched'

SEVERITY=critical ALLOWLIST='.*' DENYLIST='^$' IGNORE_PAIRS='' MAX_AGE_HOURS=72
alerts="$(jq -cn '
  {labels:{alertname:"FluxReconciliationFailure",severity:"critical",customresource_kind:"Kustomization",
    exported_namespace:"dev-env-system",name:"dev-env-h7-metadata-audit"},startsAt:"2026-10-13T21:22:05Z"} as $h
  | [$h, ($h|.labels.name="another-audit"), ($h|.labels.exported_namespace="other-namespace"),
     ($h|.labels.customresource_kind="HelmRelease"), ($h|.labels.alertname="UnrelatedCritical")]')"
# Extract the real candidate assignment, not a copied version of its jq selector.
filter="$(awk '/^candidates=/{p=1} p && /^count=/{exit} p{print}' "$RESP")"
[ -n "$filter" ]
eval "$filter"
[ "$(printf '%s' "$candidates" | jq length)" = 4 ]
printf '%s' "$candidates" | jq -e 'all(.[]; .labels.name != "dev-env-h7-metadata-audit"
  or .labels.exported_namespace != "dev-env-system" or .labels.customresource_kind != "Kustomization"
  or .labels.alertname != "FluxReconciliationFailure")' >/dev/null
echo 'ok responder: held alert excluded; other name/namespace/kind/alert retained'
