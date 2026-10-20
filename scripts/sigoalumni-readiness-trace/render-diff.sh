#!/usr/bin/env bash
# Render the sigoalumni app (kustomize build -> HelmRelease values -> helm template with
# the repo's pinned app-template chart) at a base ref and at the working tree, then diff
# the rendered manifests. Read-only: no cluster access. Needs kustomize, helm, python3+yaml.
#   scripts/sigoalumni-readiness-trace/render-diff.sh [BASE_REF]   (default origin/main)
set -euo pipefail
BASE_REF="${1:-origin/main}"
ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
APP=kubernetes/main/apps/frontend/sigoalumni/app
CHART_VER="$(awk '/tag:/ {print $2; exit}' "$ROOT/kubernetes/shared/components/common/repos/app-template/ocirepository.yaml")"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
helm pull oci://ghcr.io/bjw-s-labs/helm/app-template --version "$CHART_VER" --untar --untardir "$TMP/chart" >/dev/null
mkdir -p "$TMP/base"; git -C "$ROOT" archive "$BASE_REF" kubernetes/main/apps/frontend/sigoalumni kubernetes/shared | tar -x -C "$TMP/base"
render() { # <root> <out>
  nice -n 19 kustomize build "$1/$APP" > "$2.kustomize.yaml"
  python3 -c 'import sys,yaml;d=[x for x in yaml.safe_load_all(open(sys.argv[1]+".kustomize.yaml")) if x and x.get("kind")=="HelmRelease"][0];yaml.safe_dump(d["spec"]["values"],open(sys.argv[1]+".values.yaml","w"),sort_keys=True)' "$2"
  nice -n 19 helm template sigoalumni "$TMP/chart/app-template" -n frontend -f "$2.values.yaml" > "$2.rendered.yaml"
}
render "$TMP/base" "$TMP/main"; render "$ROOT" "$TMP/branch"
echo "== rendered manifest diff (base $BASE_REF -> working tree)"; diff -u "$TMP/main.rendered.yaml" "$TMP/branch.rendered.yaml" || true
echo "== readiness probe as rendered"; grep -A9 'readinessProbe:' "$TMP/branch.rendered.yaml"
# Structural gate: the ONLY rendered difference may be the app container's readinessProbe.failureThreshold.
python3 - "$TMP/main.rendered.yaml" "$TMP/branch.rendered.yaml" <<'PY'
import sys, yaml
def load(p): return list(yaml.safe_load_all(open(p)))
def walk(a, b, path, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b: out.append((path + [k], a.get(k, "<absent>"), b.get(k, "<absent>")))
            else: walk(a[k], b[k], path + [k], out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)): walk(x, y, path + [i], out)
    elif a != b: out.append((path, a, b))
A, B = load(sys.argv[1]), load(sys.argv[2])
diffs = []
if len(A) != len(B): diffs.append((["<document count>"], len(A), len(B)))
else:
    for x, y in zip(A, B): walk(x, y, [(x or {}).get("kind", "?")], diffs)
fmt = lambda p: ".".join(map(str, p))
for p, a, b in diffs: print(f"rendered change: {fmt(p)}: {a!r} -> {b!r}")
ok = len(diffs) == 1 and fmt(diffs[0][0]).endswith(".readinessProbe.failureThreshold") and "initContainers" not in fmt(diffs[0][0])
print("== gate:", "PASS (only readinessProbe.failureThreshold changed)" if ok else "FAIL (expected exactly one change: readinessProbe.failureThreshold)")
sys.exit(0 if ok else 1)
PY
