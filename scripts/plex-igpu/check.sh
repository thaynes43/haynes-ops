#!/usr/bin/env bash
# Check that Plex on the iGPU twins can hardware-transcode.
#
#   scripts/plex-igpu/check.sh [plexops|plex|all] [--no-transcode] [--force]
#
# For each server it prints the guest i915 state of the node the pod runs on (read-only,
# Talos debugfs via an Omni-proxied talosconfig), then runs the daily canary's probe in the
# Plex pod: a ~20 s test transcode (4K HDR -> 1080p SDR when the server has one, else 4K HEVC
# -> 1080p, else its newest 1080p episode -> 720p) that samples /transcode/sessions and stops.
# While anyone is streaming it runs no test (unless --force) and reports a real video transcode
# instead, if one is running. The Plex token stays inside the pod.
# Exit 0 only when every checked server reported result=hw (hardware decode AND encode).
# Runbook: .agents/runbooks/plex-igpu-transcode.md
set -euo pipefail

target=all transcode=1 force=""
for arg in "$@"; do
  case "$arg" in
    plexops | plex | all) target=$arg ;;
    --no-transcode) transcode=0 ;;
    --force) force=force ;;
    *) echo "usage: $0 [plexops|plex|all] [--no-transcode] [--force]" >&2; exit 2 ;;
  esac
done
case "$target" in all) servers=(plexops plex) ;; *) servers=("$target") ;; esac

for cmd in kubectl talosctl; do
  command -v "$cmd" >/dev/null || { echo "plex-igpu: missing $cmd" >&2; exit 2; }
done
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
tc=${TALOSCONFIG:-}
if [[ -z $tc ]]; then
  tc=$tmp/talosconfig
  omnictl talosconfig "$tc" >/dev/null 2>&1 || { echo "plex-igpu: omnictl talosconfig failed" >&2; exit 2; }
fi

# The in-pod check is the daily canary's probe (one implementation for both): it runs inside
# the Plex container (POSIX sh + curl) and the token never leaves the pod.
probe=$(dirname "$0")/../../kubernetes/main/apps/media/plex-hw-canary/app/resources/probe.sh
in_pod=$(cat "$probe") || { echo "plex-igpu: cannot read $probe" >&2; exit 2; }

fail=0
for app in "${servers[@]}"; do
  # Every probe below tolerates failure: a dead read prints "?" and fails the check
  # instead of aborting the run under set -e.
  pod=$(kubectl get pods -n media -l "app.kubernetes.io/name=$app" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -z $pod ]]; then echo "$app: no pod found"; fail=1; continue; fi
  node=$(kubectl get pod -n media "$pod" -o jsonpath='{.spec.nodeName}' 2>/dev/null || true)
  ip=$(kubectl get node "$node" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)
  dbg() { talosctl --talosconfig "$tc" -n "$ip" read "/sys/kernel/debug/dri/0/$1" 2>/dev/null || true; }
  wedged=$(dbg i915_wedged)
  guc=$(dbg gt0/uc/guc_info | sed -n 's/^\s*status: //p' | head -1)
  huc=$(dbg gt1/uc/huc_info | sed -n 's/^\s*status: //p' | head -1)
  errlog=$(dbg gt0/uc/guc_load_err_log_dump | wc -c)
  echo "$app pod=$pod node=$node wedged=${wedged:-?} guc=${guc:-?} huc=${huc:-?} guc_load_err_log=${errlog}B"
  [[ $wedged == 0 && $guc == RUNNING ]] || fail=1
  ((transcode)) || continue
  # The probe tests only when nobody is streaming (or --force). With streams it reports a real
  # h264/hevc video transcode that is already running (mode=observed) or result=busy.
  result=$(kubectl exec -n media "$pod" -c app -- sh -c "$in_pod" probe "igpu-check-$app-$(date +%s)" \
    "$force" 2>/dev/null | grep -m1 '^result=' || echo "result=error reason=exec-failed")
  echo "$app transcode $result"
  [[ $result == "result=hw "* ]] || fail=1
done
exit "$fail"
