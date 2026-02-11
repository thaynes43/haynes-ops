#!/bin/bash
set -euo pipefail

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: missing required command: $1" >&2
    exit 1
  }
}

need_cmd flux

have_kubectl=true
command -v kubectl >/dev/null 2>&1 || have_kubectl=false

section() {
  echo
  echo "==> $1"
}

print_non_ready() {
  # Prints either:
  # - "(all ready)" when every row is READY=True
  # - header + only the non-ready rows otherwise
  local out header non_ready
  out="$("$@" 2>&1 || true)"

  if [[ -z "${out}" ]]; then
    echo "(no output)"
    return 0
  fi

  header="$(echo "${out}" | awk 'NR==1 {print}')"
  non_ready="$(
    echo "${out}" | awk '
      NR==1 { next }                                   # skip first header (we print it ourselves)
      /^[[:space:]]*$/ { next }                        # skip blank lines
      /^NAMESPACE[[:space:]]/ { next }                 # skip repeated headers (sources prints multiple tables)
      $0 ~ /[[:space:]]True[[:space:]]/ { next }       # skip ready rows
      { print }                                        # keep non-ready rows
    '
  )"

  if [[ -z "${non_ready}" ]]; then
    echo "(all ready)"
    return 0
  fi

  echo "${header}"
  echo "${non_ready}"
}

section "HelmReleases"
print_non_ready flux get helmreleases -A

section "Kustomizations"
print_non_ready flux get kustomizations -A

section "Sources (Git/Helm/OCI/Bucket)"
print_non_ready flux get sources all -A

if [[ "${have_kubectl}" == "true" ]]; then
  section "flux-system pods (not Running/Completed)"
  kubectl -n flux-system get pods | awk 'NR==1 || $3 !~ /^(Running|Completed)$/ {print}'

  section "All pods (not Running/Completed, excluding flux-system)"
  # Note: the STATUS column differs between -n and -A output.
  # With -A the columns are: NAMESPACE NAME READY STATUS RESTARTS AGE
  kubectl get pods -A | awk 'NR==1 || ($1 != "flux-system" && $4 !~ /^(Running|Completed)$/) {print}'

  section "Rook / Ceph health (rook-ceph)"
  if kubectl get ns rook-ceph >/dev/null 2>&1; then
    if kubectl -n rook-ceph get cephcluster >/dev/null 2>&1; then
      # Print health per CephCluster. If HEALTH_OK we consider it clean.
      kubectl -n rook-ceph get cephcluster -o jsonpath='{range .items[*]}{.metadata.name}{"\tstate="}{.status.state}{"\thealth="}{.status.ceph.health}{"\n"}{end}'

      # Surface any non-OK health as a failure signal.
      bad_health="$(kubectl -n rook-ceph get cephcluster -o jsonpath='{range .items[*]}{.status.ceph.health}{"\n"}{end}' | awk '$1 != "HEALTH_OK" && $1 != "" {print; exit 0}')"
      if [[ -n "${bad_health}" ]]; then
        echo "warning: Ceph health is not HEALTH_OK"
      else
        echo "(HEALTH_OK)"
      fi
    else
      echo "CephCluster CRD not found (rook not installed?)"
    fi

    section "Recent Warning events (rook-ceph, last 100)"
    kubectl -n rook-ceph get events --field-selector type=Warning --sort-by='.lastTimestamp' 2>/dev/null | tail -n 100 || true
  else
    echo "rook-ceph namespace not found"
  fi

  section "Recent Warning events (all namespaces, last 200)"
  kubectl get events -A --field-selector type=Warning --sort-by='.lastTimestamp' 2>/dev/null | tail -n 200 || true
else
  section "kubectl not found"
  echo "Skipping pod/event checks because kubectl is not installed."
fi

echo
echo "done"
