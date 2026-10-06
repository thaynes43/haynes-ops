#!/usr/bin/env bash
# Render the immich-machine-learning load-test Job with loadtest.py inlined (dev-env cannot
# create ConfigMaps). Runbook: .agents/runbooks/immich-ml-backlog.md, "Load test".
# Usage: render-job.sh <job-name> | kubectl apply -f -
#
# Optional environment:
#   ML_URL   the ML endpoint to load (default: the immich-machine-learning Service). Point it at
#            a trial server from render-ml-trial.sh to test a candidate config first.
#   PLAN     phases "name:seconds:clip:faces:ocr,..." (default in loadtest.py:
#            baseline 120 s, default 300 s at 2/2/1, heavy 300 s at 4/4/4, recovery 120 s)
#   WHISPER  Wyoming STT host:port to time (default whisper.ai:10300)
#   PROBE_INTERVAL_S  seconds between voice probes (default 15)
#
# It reads the worst-case previews read-only from the immich database (picks.sql) and reads
# those JPEGs from the photo share mounted read-only. It never writes to Immich.
set -euo pipefail
name=${1:?job name, e.g. immich-ml-loadtest-$(date +%m%d%H%M)}
here=$(cd "$(dirname "$0")" && pwd)
picks=$(kubectl exec -i -n database postgres16-pgvecto-1 -c postgres -- psql -d immich -tA -F' ' < "$here/picks.sql")
test -n "$picks"
# The ML image has numpy, Pillow and a font, and talosm05 already has it cached.
image=$(kubectl get deploy -n photos immich-machine-learning -o jsonpath='{.spec.template.spec.containers[?(@.name=="app")].image}')
indent() { sed 's/^/              /'; }
cat <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: ${name}
  namespace: photos
  labels: {app: immich-ml-loadtest, app.kubernetes.io/name: immich-ml-loadtest}
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 7200
  ttlSecondsAfterFinished: 604800
  template:
    metadata:
      labels: {app: immich-ml-loadtest, app.kubernetes.io/name: immich-ml-loadtest}
    spec:
      restartPolicy: Never
      # Not a GPU pod. It runs beside ML on talosm05 because the image is cached there; its own
      # work is a few threads waiting on HTTP.
      nodeSelector: {kubernetes.io/hostname: talosm05}
      enableServiceLinks: false
      securityContext: {runAsUser: 1000, runAsGroup: 1000, runAsNonRoot: true}
      containers:
        - name: loadtest
          image: ${image}
          command: ["python", "-u", "-c"]
          securityContext: {allowPrivilegeEscalation: false}
          env:
            - {name: NVIDIA_VISIBLE_DEVICES, value: void}
            - {name: HOME, value: /tmp}
$( [ -n "${ML_URL:-}" ] && echo "            - {name: ML_URL, value: \"${ML_URL}\"}" )
$( [ -n "${PLAN:-}" ] && echo "            - {name: PLAN, value: \"${PLAN}\"}" )
$( [ -n "${WHISPER:-}" ] && echo "            - {name: WHISPER, value: \"${WHISPER}\"}" )
$( [ -n "${PROBE_INTERVAL_S:-}" ] && echo "            - {name: PROBE_INTERVAL_S, value: \"${PROBE_INTERVAL_S}\"}" )
            - name: PICKS
              value: |
$(printf '%s\n' "$picks" | sed 's/^/                /')
          resources:
            requests: {cpu: 500m, memory: 1Gi}
            limits: {memory: 4Gi}
          volumeMounts:
            - {name: upload, mountPath: /usr/src/app/upload, subPath: data/photos/immich, readOnly: true}
          args:
            - |
$(indent < "$here/loadtest.py")
      volumes:
        - name: upload
          nfs: {server: gasha01.haynesnetwork, path: /hdd-nfs-repl, readOnly: true}
YAML
