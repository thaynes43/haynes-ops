#!/usr/bin/env bash
# PROMTOOL=/path/to/promtool scripts/smartctl-alert-tests/run.sh
set -euo pipefail
here=$(dirname "$(readlink -f "$0")")
repo=$(readlink -f "${here}/../..")
tmp=$(mktemp -d)
trap 'rm -rf "${tmp}"' EXIT
promtool=${PROMTOOL:-promtool}
yq '.spec' "${repo}/kubernetes/main/apps/observability/smartctl-exporter/app/prometheusrule.yaml" > "${tmp}/rules.yaml"
"${promtool}" check rules "${tmp}/rules.yaml"
# Test actual expressions and alert timing; leave annotation wording out of the matrix.
yq 'del(.groups[].rules[].annotations)' "${tmp}/rules.yaml" > "${tmp}/rules-noann.yaml"
python3 -I -B "${here}/fixtures.py" > "${tmp}/tests.yaml"
(cd "${tmp}" && GOMAXPROCS=${DEV_ENV_CPU_LIMIT:-2} nice -n 19 "${promtool}" test rules tests.yaml)
