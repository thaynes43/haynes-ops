#!/usr/bin/env bash
# Prove the sigoalumni child route in kube-prometheus-stack's alertmanager ExternalSecret.
#   AMTOOL=/path/to/amtool scripts/sigoalumni-alert-tests/alertmanager-route-check.sh
# amtool is not vendored: use the official release matching the live Alertmanager (0.34.1),
# sha256-verified against the release's sha256sums.txt.
# The ExternalSecret template is rendered by emulation (unescape the {{ "{{" }} Go-template
# escapes, substitute dummy secret values); Alertmanager's own {{ .Alerts }} templates stay.
set -o errexit -o pipefail -o nounset
here=$(dirname "$(readlink -f "$0")")
repo=$(readlink -f "${here}/../..")
es=kubernetes/main/apps/observability/kube-prometheus-stack/app/externalsecret.yaml
amtool=${AMTOOL:-amtool}
tmp=$(mktemp -d); trap 'rm -rf "${tmp}"' EXIT

render() { # <externalsecret file> <out>
  yq -r '.spec.target.template.data["alertmanager.yaml"]' "$1" | python3 -I -c '
import re, sys
t = sys.stdin.read()
for lit in ("{{-", "{{", "}}"):
    t = t.replace("{{ \"%s\" }}" % lit, lit)
t = t.replace("{{ .ALERTMANAGER_CLUSTER_MAIN_HEARTBEAT_URL }}", "https://hc.example.invalid/ping")
t = re.sub(r"\{\{ \.([A-Z_]+) \}\}", r"dummy-\1", t)
sys.stdout.write(t)' > "$2"
}
git -C "${repo}" show "origin/main:${es}" > "${tmp}/base-es.yaml"
render "${tmp}/base-es.yaml" "${tmp}/base.yaml"
render "${repo}/${es}" "${tmp}/new.yaml"
"${amtool}" check-config "${tmp}/base.yaml" "${tmp}/new.yaml"

# a copy whose new route has its own receiver shows WHICH route an alert hits
python3 -I - "${tmp}/new.yaml" "${tmp}/probe.yaml" <<'PY'
import sys
t = open(sys.argv[1]).read()
old = "    - receiver: pushover\n      group_wait: 0s"
assert t.count(old) == 1, "expected exactly one group_wait: 0s route"
t = t.replace(old, "    - receiver: probe-new\n      group_wait: 0s")
t = t.replace('\n  - name: "null"', '\n  - name: probe-new\n  - name: "null"', 1)
open(sys.argv[2], "w").write(t)
PY
check() { # <expected in probe> <labels...>
  local want=$1; shift
  local got; got=$("${amtool}" config routes test --config.file="${tmp}/probe.yaml" "$@" | tr -d '\n')
  local base new
  base=$("${amtool}" config routes test --config.file="${tmp}/base.yaml" "$@" | tr -d '\n')
  new=$("${amtool}" config routes test --config.file="${tmp}/new.yaml" "$@" | tr -d '\n')
  printf '%-62s probe=%-12s base=%-14s new=%s\n' "$*" "${got}" "${base}" "${new}"
  # base and new must route every sample alert to the same receiver (the new route uses the
  # same receiver as the generic critical one); the probe copy shows which route was hit
  [[ ${got} == "${want}" && ${base} == "${new}" ]] || { echo "FAIL: $*" >&2; exit 1; }
}
check probe-new alertname=SigoalumniServiceEndpointsEmptyObserved severity=critical
check null      alertname=SigoalumniServiceEndpointsEmptyObserved severity=warning
check pushover  alertname=SigoalumniServiceEndpointsEmptySustained severity=critical
check pushover  alertname=KubePodCrashLooping severity=critical
check null      alertname=SigoalumniReadinessProbeFailing severity=warning
check null      alertname=SigoalumniSharedEgressSuspected severity=warning
check heartbeat alertname=Watchdog severity=none
check pushover-daytime alertname=PlexHwCanaryStale severity=warning
"${amtool}" config routes show --config.file="${tmp}/new.yaml"
python3 -I - "${tmp}/new.yaml" <<'PY'
import json, subprocess, sys
r = json.loads(subprocess.run(["yq", "-o=json", ".route", sys.argv[1]], capture_output=True, text=True, check=True).stdout)
print("root group_wait:", r["group_wait"], "group_by:", r["group_by"])
for c in r["routes"]:
    if "Sigoalumni" in json.dumps(c):
        print("sigoalumni route:", json.dumps(c))
PY
