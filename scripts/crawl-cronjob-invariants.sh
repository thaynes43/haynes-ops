#!/usr/bin/env bash
# Invariant check for the cigar-journal crawl CronJobs.
#
# WHY THIS EXISTS
# ---------------
# The four crawl CronJobs used to be raw manifests in crawler-cronjobs.yaml.
# Raw manifests have one safety property a Helm chart does not: deleting a field
# deletes a LINE, and the field is then genuinely absent. bjw-s app-template
# substitutes a default instead, and for this workload three of those defaults
# are actively harmful — each verified by render against chart 5.1.0, not read
# off the docs:
#
#   * activeDeadlineSeconds omitted -> the chart emits NOTHING (`{{- with ... }}`),
#     so a wedged crawl runs forever. That is the exact failure the deadlines were
#     sized to prevent (cigar-journal#155: a kill strands crawl_runs at 'running',
#     but no kill at all is worse — the pod just never stops).
#   * startingDeadlineSeconds omitted -> 30, not absent. A controller hiccup longer
#     than 30 s makes the CronJob SKIP that fire entirely. For a weekly offers walk
#     that is a silently missed week with no error anywhere.
#   * backoffLimit omitted -> 6, not 1. A wedged crawl then retries six times, six
#     pods against one vendor, instead of failing once and staying failed.
#   * successfulJobsHistory / failedJobsHistory omitted -> 1, not 3.
#
# A MISSPELLED key is already loud: the chart's values.schema.json is
# additionalProperties:false, so `helm template` (and therefore flux-local test in
# CI) exits 1. A MISSING key is silent and always will be. This script covers only
# the silent half.
#
# It also replaces the guard that the consolidation retired. The old file carried
# four independent `image:` pins and a
#   grep -c '^ *image: ghcr.io/thaynes43/cigar-journal:v' crawler-cronjobs.yaml
# check that expected 4, because a bump PR editing one line left stale-image
# CronJobs behind — which is how v0.27.1 shipped with two Deployments on the new
# tag and four CronJobs on the old one (haynes-ops#2689). There is now one pin,
# the &mainImage anchor, so the guard inverts: assert that every controller
# resolves to the SAME image reference.
#
# WHAT IT DELIBERATELY DOES NOT ASSERT
# ------------------------------------
# Values that legitimately differ per job — schedule, suspend, and the MAGNITUDE
# of activeDeadlineSeconds (9000 for the Fox offers walk, 5400 for the other
# three) — are not pinned here. Duplicating them would make every honest retune a
# two-file edit and would rot. Only the uniform fields, and the mere PRESENCE of
# activeDeadlineSeconds, are enforced.
#
# NOT WIRED INTO CI. Whether an assertion like this belongs as a step in
# .github/workflows/flux-local.yaml — a control surface shared by every app in
# this repo — is the owner's call, not one lane's. Until then this is run by hand
# from a PR that touches the crawl controllers. It needs no cluster access.
#
# Usage:  ./scripts/crawl-cronjob-invariants.sh
# Needs:  helm, kustomize, yq (network access to ghcr.io for `helm pull`).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${REPO_ROOT}/kubernetes/main/apps/frontend/cigar-journal/app"
OCI_REPO="${REPO_ROOT}/kubernetes/shared/components/common/repos/app-template/ocirepository.yaml"

for bin in helm kustomize yq; do
  command -v "$bin" >/dev/null || { echo "FATAL: $bin not on PATH"; exit 2; }
done

# The chart version is read from the OCIRepository the cluster actually uses, so
# this check can never validate against a different chart than Flux renders.
CHART_URL="$(yq -r '.spec.url' "$OCI_REPO")"
CHART_VER="$(yq -r '.spec.ref.tag' "$OCI_REPO")"
echo "chart: ${CHART_URL} @ ${CHART_VER}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

helm pull "$CHART_URL" --version "$CHART_VER" --untar --untardir "$WORK" >/dev/null

# Values come out of the real HelmRelease via kustomize, so the YAML anchors
# (&mainImage, &crawlEnv, ...) are resolved exactly as kustomize-controller
# resolves them before helm-controller ever sees them.
kustomize build "$APP_DIR" | yq 'select(.kind=="HelmRelease") | .spec.values' > "$WORK/values.yaml"
helm template cigar-journal "$WORK/app-template" -n frontend \
  -f "$WORK/values.yaml" --kube-version 1.35.5 > "$WORK/rendered.yaml"

fail=0
note() { printf '  %-4s %s\n' "$1" "$2"; }
check() { # check <label> <actual> <expected>
  if [[ "$2" == "$3" ]]; then note "OK" "$1 = $2"; else note "FAIL" "$1 = $2 (expected $3)"; fail=1; fi
}

# --- one image pin across every controller -----------------------------------
echo
echo "IMAGE PIN — every controller must resolve the single &mainImage anchor"
# values.yaml is a single document, so a plain `yq` walk is unambiguous here.
# Both initContainers and containers are walked: the &mainImage anchor is DEFINED
# on the migrate init container, so a bump that missed it would show up here.
mapfile -t cj_images < <(
  yq -r '.controllers[]
           | ((.initContainers // {}) + .containers)[]
           | .image.repository + ":" + .image.tag' "$WORK/values.yaml" \
    | grep '^ghcr.io/thaynes43/cigar-journal:' | sort -u
)
# postgres-init is a genuinely different image and is expected to differ; every
# cigar-journal reference must collapse to exactly one tag.
if [[ ${#cj_images[@]} -eq 1 ]]; then
  note "OK" "all cigar-journal containers on ${cj_images[0]}"
else
  note "FAIL" "cigar-journal image pins diverge: ${cj_images[*]}"
  fail=1
fi

# --- per-CronJob silent-default guards ---------------------------------------
# `yq ea '[.] | ...'` loads the whole multi-document render as one sequence.
# A plain `yq 'select(...)'` would instead emit one result PER DOCUMENT — blank
# lines and `---` separators for every non-match — which silently turns every
# assertion below into a comparison against the empty string.
mapfile -t jobs < <(yq ea -r '[.] | map(select(.kind=="CronJob")) | .[] | .metadata.name' "$WORK/rendered.yaml")
if [[ ${#jobs[@]} -ne 2 ]]; then
  echo; note "FAIL" "expected 2 crawl CronJobs (the fleet pair, ADR-015), rendered ${#jobs[@]}: ${jobs[*]}"
  fail=1
fi

for job in "${jobs[@]}"; do
  echo
  echo "$job"
  q() { yq ea -r "[.] | map(select(.kind==\"CronJob\" and .metadata.name==\"$job\")) | .[0]$1" "$WORK/rendered.yaml"; }

  # PRESENCE only — the magnitude legitimately differs per vendor and mode.
  adl="$(q '.spec.jobTemplate.spec.activeDeadlineSeconds // "MISSING"')"
  if [[ "$adl" == "MISSING" || -z "$adl" ]]; then
    note "FAIL" "activeDeadlineSeconds MISSING — a wedged crawl would run forever"
    fail=1
  else
    note "OK" "activeDeadlineSeconds = $adl (present)"
  fi

  check "startingDeadlineSeconds"    "$(q '.spec.startingDeadlineSeconds')"                     600
  check "backoffLimit"               "$(q '.spec.jobTemplate.spec.backoffLimit')"               1
  check "successfulJobsHistoryLimit" "$(q '.spec.successfulJobsHistoryLimit')"                  3
  check "failedJobsHistoryLimit"     "$(q '.spec.failedJobsHistoryLimit')"                      3
  check "concurrencyPolicy"          "$(q '.spec.concurrencyPolicy')"                           Forbid
  check "restartPolicy"              "$(q '.spec.jobTemplate.spec.template.spec.restartPolicy')" Never
done

echo
if [[ $fail -eq 0 ]]; then
  echo "PASS — crawl CronJob invariants hold."
else
  echo "FAIL — see above."
fi
exit $fail
