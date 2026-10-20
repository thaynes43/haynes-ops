#!/usr/bin/env bash
# Run the sigoalumni PrometheusRule unit tests with promtool.
#   PROMTOOL=/path/to/promtool scripts/sigoalumni-alert-tests/run.sh
# promtool is not vendored; use the official Prometheus release tarball (the live server runs
# 3.15.0) and verify it against the release's sha256sums.txt. See README.md.
set -o errexit -o pipefail -o nounset
here=$(dirname "$(readlink -f "$0")")
repo=$(readlink -f "${here}/../..")
rule=${repo}/kubernetes/main/apps/frontend/sigoalumni/app/prometheusrule.yaml
promtool=${PROMTOOL:-promtool}
tmp=$(mktemp -d)
trap 'rm -rf "${tmp}"' EXIT

# a PrometheusRule's .spec is already a plain Prometheus rule file ({groups: [...]}).
# The behaviour matrix runs on a copy without annotations (wording changes must not churn
# every assertion); the annotation file runs on the real rules and pins the rendered text.
yq '.spec' "${rule}" > "${tmp}/rules.yaml"
yq 'del(.groups[].rules[].annotations)' "${tmp}/rules.yaml" > "${tmp}/rules-noann.yaml"
for mode in behavior annotations; do
  case ${mode} in behavior) name=sigoalumni-rules-test.yaml ;; *) name=sigoalumni-annotations-test.yaml ;; esac
  python3 -I "${here}/generate_fixtures.py" "${mode}" > "${tmp}/${name}.gen"
  if ! diff -q "${tmp}/${name}.gen" "${here}/${name}" > /dev/null; then
    echo "${name} is stale: rerun generate_fixtures.py ${mode} > ${name} and commit" >&2
    exit 1
  fi
  cp "${here}/${name}" "${tmp}/${name}"
done

"${promtool}" check rules "${tmp}/rules.yaml"
nice -n 19 "${promtool}" test rules "${tmp}/sigoalumni-rules-test.yaml" "${tmp}/sigoalumni-annotations-test.yaml"
