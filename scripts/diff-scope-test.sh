#!/usr/bin/env bash
# scripts/diff-scope-test.sh — adversarial regression suite for scripts/diff-scope.sh
# (the PRIMARY gate that must PREVENT a prompt-injected shepherd from auto-merging
# anything beyond a pure image/chart bump).
#
# Each case builds a synthetic PR diff in a throwaway git repo and asserts the gate's
# verdict (exit 0 = auto-mergeable shape; exit 1 = human review required). Run:
#   bash scripts/diff-scope-test.sh          # exit 0 = every case behaved as expected
#
# Two jobs:
#   1. Regression-lock GATE A (sensitive denylist) + GATE B (pure-bump shape) so a future
#      edit to diff-scope.sh can't silently weaken them.
#   2. Document, as executable proof, that the REAL supporting-edit upgrades (app-template
#      v5 automountServiceAccountToken, rook CSI RBAC/SA) are exactly the shapes GATE A
#      blocks — i.e. "auto-merge supporting edits" and "keep the security gate" are in
#      direct tension. See the SUPPORTING-EDIT block at the bottom.
set -uo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)/diff-scope.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0; declare -a FAILED=()

w() { mkdir -p "$(dirname "$1")"; printf '%s\n' "$2" > "$1"; }

# ── case content functions (all defined before any run_case call) ───────────────────
IMMICH=kubernetes/main/apps/photos/immich/server/helmrelease.yaml
hr_at() { # $1=repository $2=tag $3=digest-char $4=replicas -> writes an immich HR
  w "$IMMICH" "apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: immich-server
spec:
  values:
    controllers:
      server:
        containers:
          app:
            image:
              repository: $1
              tag: $2@sha256:$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3$3
        replicas: $4"
}
base_hr()   { hr_at ghcr.io/immich-app/immich-server v2.7.5 a 1; }
head_tag()  { hr_at ghcr.io/immich-app/immich-server v3.0.1 b 1; }
head_repo() { hr_at evil.example.com/immich-server   v2.7.5 a 1; }
head_repl() { hr_at ghcr.io/immich-app/immich-server v3.0.1 a 2; }

base_chart() { w kubernetes/main/apps/database/cloudnative-pg/app/helmrelease.yaml \
'spec:
  chart:
    spec:
      chart: cloudnative-pg
      version: 0.28.3'; }
head_chart() { w kubernetes/main/apps/database/cloudnative-pg/app/helmrelease.yaml \
'spec:
  chart:
    spec:
      chart: cloudnative-pg
      version: 0.29.0'; }

base_ren()  { w .renovate/holds.json5 '{ allowedVersions: "<6.2.1" }'; }
head_ren()  { w .renovate/holds.json5 '{ allowedVersions: "<99.0.0" }'; }
base_rbac() { w kubernetes/main/apps/foo/app/rbac.yaml 'kind: Role'; }
head_rbac() { w kubernetes/main/apps/foo/app/rbac.yaml 'kind: Role
metadata:
  name: x'; }
base_kv()   { w kubernetes/main/apps/kyverno/policies/pss.yaml 'kind: ClusterPolicy'; }
head_kv()   { w kubernetes/main/apps/kyverno/policies/pss.yaml 'kind: ClusterPolicy
metadata: {}'; }
base_self() { w scripts/diff-scope.sh 'echo old'; }
head_self() { w scripts/diff-scope.sh 'echo tampered'; }

FOO=kubernetes/main/apps/foo/app/helmrelease.yaml
sens_base() { w "$FOO" 'spec:
  values:
    x: 1'; }
sens_add()  { w "$FOO" "spec:
  values:
    x: 1
    $1"; }
h_hostpath()   { sens_add 'hostPath: /etc'; }
h_privileged() { sens_add 'privileged: true'; }
h_automount()  { sens_add 'automountServiceAccountToken: true'; }
h_secctx()     { sens_add 'securityContext:'; }
h_sa()         { sens_add 'serviceAccountName: foo'; }
h_word()       { sens_add 'subject: cluster-admin'; }
h_ns()         { sens_add 'namespace: kube-system'; }
NEW=kubernetes/main/apps/foo/app/extra.yaml
new_kind()   { w "$NEW" "$1"; }
h_deploy()   { new_kind 'kind: Deployment'; }
h_crole()    { new_kind 'kind: ClusterRole'; }
h_crd()      { new_kind 'kind: CustomResourceDefinition'; }
h_es()       { new_kind 'kind: ExternalSecret'; }

# The REAL supporting edits (the crux):
head_apptmpl() { # app-template v5: must keep automountServiceAccountToken TRUE
  w "$IMMICH" 'apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: immich-server
spec:
  values:
    defaultPodOptions:
      automountServiceAccountToken: true
    controllers:
      server:
        containers:
          app:
            image:
              repository: ghcr.io/immich-app/immich-server
              tag: v3.0.1@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
        replicas: 1'; }
CSI=kubernetes/main/apps/storage/rook-ceph/csi-drivers/helmrelease.yaml
base_rook() { w "$CSI" 'spec:
  values:
    drivers:
      cephfs: {}'; }
head_rook() { w "$CSI" 'spec:
  values:
    drivers:
      cephfs:
        serviceAccountName: rook-csi-cephfs-ctrlplugin-sa'; }

# ── harness ─────────────────────────────────────────────────────────────────────────
# run_case <name> <expect 0|1> <mkbase fn> <mkhead fn> [expected-substring]
run_case() {
  local name="$1" expect="$2" mkbase="$3" mkhead="$4" want="${5:-}"
  local repo="$TMP/repo"; rm -rf "$repo"; mkdir -p "$repo"
  ( cd "$repo"
    git init -q; git config user.email t@t; git config user.name t; git checkout -q -b main
    "$mkbase"; git add -A; git commit -qm base >/dev/null
    "$mkhead"; git add -A; git commit -qm change >/dev/null
  )
  local out rc ok=1
  out="$( cd "$repo" && DIFF_SCOPE_RANGE="HEAD~1..HEAD" bash "$SRC" 2>&1 )"; rc=$?
  [ "$rc" = "$expect" ] || ok=0
  [ -z "$want" ] || printf '%s' "$out" | grep -qiF -- "$want" || ok=0
  if [ "$ok" = 1 ]; then
    PASS=$((PASS+1)); printf '  [ok]   %-40s exit %s\n' "$name" "$rc"
  else
    FAIL=$((FAIL+1)); FAILED+=("$name")
    printf '  [FAIL] %-40s expected %s%s, got %s\n' "$name" "$expect" \
      "${want:+ +\"$want\"}" "$rc"
    printf '%s\n' "$out" | sed 's/^/         | /'
  fi
}

echo "── PASS: the auto-mergeable shape (pure bumps under kubernetes/**) ──"
run_case "pure image tag+digest bump" 0 base_hr    head_tag
run_case "pure chart version bump"    0 base_chart head_chart

echo ""
echo "── FAIL/GATE A: sensitive PATHS ──"
run_case "widen a hold (outside kubernetes/**)" 1 base_ren  head_ren  "outside kubernetes"
run_case "edit an rbac file"                    1 base_rbac head_rbac "rbac"
run_case "edit the Kyverno guardrail tree"      1 base_kv   head_kv   "Kyverno"
run_case "edit diff-scope.sh itself"            1 base_self head_self "diff-scope guard"

echo ""
echo "── FAIL/GATE A: sensitive ADDED content ──"
run_case "adds hostPath"                    1 sens_base h_hostpath   "security-sensitive"
run_case "adds privileged: true"            1 sens_base h_privileged "security-sensitive"
run_case "adds automountServiceAccountToken:true" 1 sens_base h_automount "security-sensitive"
run_case "adds securityContext:"            1 sens_base h_secctx     "security-sensitive"
run_case "adds serviceAccountName:"         1 sens_base h_sa         "security-sensitive"
run_case "adds a new Deployment"            1 sens_base h_deploy     "sensitive resource kind"
run_case "adds a ClusterRole"               1 sens_base h_crole      "sensitive resource kind"
run_case "adds a CRD"                       1 sens_base h_crd        "sensitive resource kind"
run_case "adds an ExternalSecret"           1 sens_base h_es         "sensitive resource kind"
run_case "adds cluster-admin token"         1 sens_base h_word       "sensitive token"
run_case "targets kube-system"              1 sens_base h_ns         "kube-system"

echo ""
echo "── FAIL/GATE B: shape (not a pure version bump) ──"
run_case "bump + unrelated replicas change" 1 base_hr head_repl "allowed shape"
run_case "registry swap (no version change)" 1 base_hr head_repo "allowed shape"

echo ""
echo "── PROOF: the REAL manual-tier supporting edits ARE the sensitive shapes ──"
echo "   (what a diff-scope 'widening' would have to admit — each collides with GATE A"
echo "    by design; admitting them re-opens the hole the gate exists to close. Expected"
echo "    verdict: REVIEW, not auto-merge.)"
run_case "app-template v5 supporting edit"  1 base_hr   head_apptmpl "security-sensitive"
run_case "rook CSI supporting edit"         1 base_rook head_rook    "security-sensitive"

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "diff-scope adversarial suite: ${PASS} passed, ${FAIL} failed."
if [ "$FAIL" -ne 0 ]; then printf 'FAILED: %s\n' "${FAILED[*]}"; exit 1; fi
echo "All cases behaved as expected — GATE A + GATE B guarantees hold."
exit 0
