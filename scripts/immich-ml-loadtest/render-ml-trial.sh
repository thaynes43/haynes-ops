#!/usr/bin/env bash
# Render a throwaway immich-machine-learning server as a Job on one GPU node, with the VRAM
# guard (kubernetes/main/apps/photos/immich/machine-learning/vram-guard/sitecustomize.py) and
# the HelmRelease's ML settings, so a candidate config or a different card can be load-tested
# without touching the real Deployment. Runbook: .agents/runbooks/immich-ml-backlog.md.
# Usage: render-ml-trial.sh <job-name> <node> [KEY=VALUE ...] | kubectl apply -f -
#   KEY=VALUE pairs override or add ML environment variables, e.g. IMMICH_ML_CUDA_MEM_LIMIT_MB=1024.
# Then point the load test at it:
#   ML_URL=http://$(kubectl get pod -n photos -l job-name=<job-name> -o jsonpath='{.items[0].status.podIP}'):3003
# Nothing routes Immich traffic to it. Delete the Job when done; it also stops by itself after
# an hour.
set -euo pipefail
name=${1:?job name}
node=${2:?node, e.g. talosm01}
shift 2
here=$(cd "$(dirname "$0")" && pwd)
guard="$here/../../kubernetes/main/apps/photos/immich/machine-learning/vram-guard/sitecustomize.py"
image=$(kubectl get deploy -n photos immich-machine-learning -o jsonpath='{.spec.template.spec.containers[?(@.name=="app")].image}')
declare -A env=(
  [TZ]=America/New_York
  [MACHINE_LEARNING_CACHE_FOLDER]=/cache
  [TRANSFORMERS_CACHE]=/cache
  [XDG_CONFIG_HOME]=/tmp/.config
  [MPLCONFIGDIR]=/tmp/.config/matplotlib
  [PYTHONPATH]=/tmp/vram-guard:/usr/src
)
# The HelmRelease's own ML settings, read from the live Deployment so the trial matches it.
while IFS='=' read -r k v; do
  case "$k" in MACHINE_LEARNING_REQUEST_THREADS|MACHINE_LEARNING_MAX_BATCH_SIZE__*|MACHINE_LEARNING_MODEL_TTL|IMMICH_ML_*) env[$k]=$v ;; esac
done < <(kubectl get deploy -n photos immich-machine-learning \
  -o jsonpath='{range .spec.template.spec.containers[?(@.name=="app")].env[*]}{.name}={.value}{"\n"}{end}')
for kv in "$@"; do env[${kv%%=*}]=${kv#*=}; done
cat <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: ${name}
  namespace: photos
  labels: {app: immich-ml-trial, app.kubernetes.io/name: immich-ml-trial}
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 3600
  ttlSecondsAfterFinished: 86400
  template:
    metadata:
      labels: {app: immich-ml-trial, app.kubernetes.io/name: immich-ml-trial}
    spec:
      restartPolicy: Never
      nodeSelector: {kubernetes.io/hostname: ${node}}
      runtimeClassName: nvidia
      enableServiceLinks: false
      containers:
        - name: ml
          image: ${image}
          command: ["sh", "-c"]
          env:
$(for k in "${!env[@]}"; do printf '            - {name: %s, value: "%s"}\n' "$k" "${env[$k]}"; done | sort)
          resources:
            requests: {cpu: 10m, memory: 512Mi}
            limits: {memory: 16Gi}
          volumeMounts:
            - {name: model-cache, mountPath: /cache-src, readOnly: true}
            - {name: cache, mountPath: /cache}
          args:
            - |
              set -e
              mkdir -p /tmp/vram-guard
              cat > /tmp/vram-guard/sitecustomize.py <<'PY'
$(sed 's/^/              /' "$guard")
              PY
              # A private copy of the models: nothing here ever writes to the shared cache PVC.
              cp -a /cache-src/clip /cache-src/facial-recognition /cache-src/ocr /cache/
              exec tini -- python -m immich_ml
      volumes:
        - name: model-cache
          persistentVolumeClaim: {claimName: immich-machine-learning, readOnly: true}
        - name: cache
          emptyDir: {sizeLimit: 4Gi}
YAML
