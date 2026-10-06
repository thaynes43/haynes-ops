#!/usr/bin/env bash
# One-shot run of the overlay harness inside the cluster, for when no docker is at hand (the dev-env pod).
# Starts a throwaway Job on the pinned LazyLibrarian image (sleep only, 1 CPU cap), copies patches/ into it,
# runs run.py there, prints the result and deletes the Job. Sequential; nothing else runs in the Job.
#
#   oneshot.sh                       pins + tests (what CI runs)
#   oneshot.sh --tests-only --drop all
#   oneshot.sh write-diffs           regenerate tests/diffs/*.diff from the overlays (after an intended edit)
#   oneshot.sh replay OUT.tsv        historical replay over a read-only snapshot of the live database
#                                    (sqlite backup API, mode=ro); OUT.tsv holds download history: keep it
#                                    out of git
set -euo pipefail
ns=downloads
job=ll-overlay-harness-$(date +%s)
here=$(cd "$(dirname "$0")/.." && pwd)
hr="$here/../helmrelease.yaml"
image="$(awk '/repository: .*lazylibrarian/{print $2; exit}' "$hr"):$(awk '/^ *tag: version-/{print $2; exit}' "$hr")"

cleanup() { kubectl delete job -n "$ns" "$job" --wait=false >/dev/null 2>&1 || true; }
trap cleanup EXIT

kubectl apply -f - >/dev/null <<EOF
apiVersion: batch/v1
kind: Job
metadata: {name: $job, namespace: $ns, labels: {app.kubernetes.io/name: ll-overlay-harness}}
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 1800
  ttlSecondsAfterFinished: 300
  template:
    metadata: {labels: {app.kubernetes.io/name: ll-overlay-harness}}
    spec:
      restartPolicy: Never
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
              - matchExpressions: [{key: node-role.kubernetes.io/control-plane, operator: DoesNotExist}]
      securityContext: {runAsNonRoot: true, runAsUser: 1000, runAsGroup: 1000, seccompProfile: {type: RuntimeDefault}}
      containers:
        - name: harness
          image: $image
          command: [sleep, "1800"]
          securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
          resources: {requests: {cpu: 50m, memory: 256Mi}, limits: {cpu: "1", memory: 1Gi}}
EOF
kubectl wait -n "$ns" --for=condition=Ready pod -l "job-name=$job" --timeout=300s >/dev/null
pod=$(kubectl get pods -n "$ns" -l "job-name=$job" -o jsonpath='{.items[0].metadata.name}')
tar -C "$here/.." -cf - patches | kubectl exec -i -n "$ns" "$pod" -c harness -- sh -c 'mkdir -p /tmp/work && tar -C /tmp/work -xf -'

if [ "${1:-}" = write-diffs ]; then
  kubectl exec -n "$ns" "$pod" -c harness -- python3 /tmp/work/patches/tests/run.py --write-diffs /tmp/work/diffs
  for f in "$here"/*.py; do
    name=$(basename "$f" .py)
    kubectl exec -n "$ns" "$pod" -c harness -- cat "/tmp/work/diffs/$name.diff" > "$here/tests/diffs/$name.diff"
  done
  echo "diffs written to $here/tests/diffs"
elif [ "${1:-}" = replay ]; then
  out=${2:?usage: oneshot.sh replay OUT.tsv}
  live=$(kubectl get pods -n "$ns" -l app.kubernetes.io/name=lazylibrarian -o jsonpath='{.items[0].metadata.name}')
  kubectl exec -n "$ns" "$live" -c app -- python3 -c "import sqlite3
s = sqlite3.connect('file:/config/lazylibrarian.db?mode=ro', uri=True); d = sqlite3.connect('/tmp/ll-replay.db')
s.backup(d); d.close(); s.close()"
  kubectl exec -n "$ns" "$live" -c app -- cat /tmp/ll-replay.db |
    kubectl exec -i -n "$ns" "$pod" -c harness -- sh -c 'cat > /tmp/work/ll.db'
  kubectl exec -n "$ns" "$live" -c app -- rm -f /tmp/ll-replay.db
  kubectl exec -n "$ns" "$pod" -c harness -- python3 /tmp/work/patches/tests/run.py --replay /tmp/work/ll.db /tmp/work/out.tsv \
    2>&1 | grep -v '^20[0-9][0-9]-' || true
  kubectl exec -n "$ns" "$pod" -c harness -- cat /tmp/work/out.tsv > "$out"
  echo "replay written to $out"
else
  kubectl exec -n "$ns" "$pod" -c harness -- python3 /tmp/work/patches/tests/run.py "$@"
fi
