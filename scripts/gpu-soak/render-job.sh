#!/usr/bin/env bash
# Render the GPU soak Job with soak.py inlined (dev-env cannot create ConfigMaps in `ai`).
# Usage: render-job.sh <gpu-uuid> <job-name> [SOAK_PLAN] | kubectl apply -f -
# Results live in the Job log (kept 7 d) and in Loki: {namespace="ai", app="gpu-soak"} |= "R,"
set -euo pipefail
uuid=${1:?gpu uuid, e.g. GPU-d8a856f1-f955-f683-bc24-654561496774}
name=${2:?job name}
plan=${3:-}
here=$(cd "$(dirname "$0")" && pwd)
# deadline = the plan's own length + 15 min, so a long airtime plan is not killed at 2 h
deadline=$(echo "${plan:-trigger:4:240:90,sustain:1:0:1200,hottrigger:2:240:90,cool:1:300:0}" | tr ',' '\n' \
  | awk -F: '{t += $2 * ($3 + $4)} END {print t + 900}')
image=$(kubectl get sts -n ai comfyui -o jsonpath='{.spec.template.spec.containers[0].image}')  # torch + CUDA, already cached
cat <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: ${name}
  namespace: ai
  labels: {app: gpu-soak, app.kubernetes.io/name: gpu-soak}
  annotations: {haynes-ops/issue: "3052", haynes-ops/gpu: "${uuid}"}
spec:
  backoffLimit: 0
  activeDeadlineSeconds: ${deadline}
  ttlSecondsAfterFinished: 604800
  template:
    metadata:
      labels: {app: gpu-soak, app.kubernetes.io/name: gpu-soak}
    spec:
      restartPolicy: Never
      runtimeClassName: nvidia
      nodeSelector: {feature.node.kubernetes.io/nvidia-3090-gpu: "true"}
      securityContext: {runAsUser: 1000, runAsGroup: 1000, runAsNonRoot: true}
      containers:
        - name: soak
          image: ${image}
          command: ["python", "-u", "-c"]
          env:
            - {name: NVIDIA_VISIBLE_DEVICES, value: "${uuid}"}
            - {name: NVIDIA_DRIVER_CAPABILITIES, value: "compute,utility"}
            - {name: HOME, value: /tmp}
$( [ -n "$plan" ] && echo "            - {name: SOAK_PLAN, value: \"${plan}\"}" )
          resources:
            requests: {cpu: "1", memory: 2Gi}
            limits: {memory: 8Gi}
          args:
            - |
$(sed 's/^/              /' "$here/soak.py")
YAML
