#!/usr/bin/env bash
# Render the GPU soak Job with soak.py inlined (dev-env cannot create ConfigMaps in `ai`).
# Usage: render-job.sh <gpu-uuid|all> <job-name> [SOAK_PLAN] | kubectl apply -f -
#        render-job.sh --cap-only <egpu-test-name> > /tmp/cap-job.yaml
# --cap-only renders the fixed Card B/talosw04/250 W no-load setter. Rendering
# grants no execution permission: V2 dev-env-agent-guard denies SYS_ADMIN Jobs.
# Results live in the Job log (kept 7 d) and in Loki: {namespace="ai", app="gpu-soak"} |= "R,"
#
# Optional environment (the eGPU test node, .agents/runbooks/egpu-test-node.md):
#   SOAK_NODE=talosw04      pin to that node and tolerate its haynesops.com/gpu-test taint
#                           (default: any node labelled nvidia-3090-gpu, no toleration)
#   SOAK_POWER_LIMIT_W=150  cap board power before any load. This runs the pod as root with
#                           CAP_SYS_ADMIN, which Kyverno admits only for Jobs named
#                           egpu-test-* (kyverno/policies/app/exceptions/egpu-test-capabilities.yaml).
#   SOAK_ABORT_C=83         stop the load the moment the core reaches this temperature.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cap_only=false
if [ "${1:-}" = --cap-only ]; then
  [ "$#" -eq 2 ] || { echo 'usage: render-job.sh --cap-only <egpu-test-name>' >&2; exit 1; }
  cap_only=true
  set -- GPU-18bf6eab-c76a-26ba-74c8-76093b705b8b "$2"
fi
uuid=${1:?gpu uuid, e.g. GPU-d8a856f1-f955-f683-bc24-654561496774}
name=${2:?job name}
plan=${3:-}
node=${SOAK_NODE:-}
power=${SOAK_POWER_LIMIT_W:-}
abort_c=${SOAK_ABORT_C:-}
if "$cap_only"; then
  node=talosw04
  power=250
  abort_c=
fi
if [ -n "$power" ] && [[ "$name" != egpu-test-* ]]; then
  echo "SOAK_POWER_LIMIT_W needs CAP_SYS_ADMIN; Kyverno admits that only for Jobs named egpu-test-*" >&2
  exit 1
fi
if [ -n "$node" ]; then
  placement="nodeSelector: {kubernetes.io/hostname: \"${node}\"}
      tolerations:
        - {key: haynesops.com/gpu-test, operator: Exists, effect: NoSchedule}"
else
  placement='nodeSelector: {feature.node.kubernetes.io/nvidia-3090-gpu: "true"}'
fi
if [ -n "$power" ]; then
  # nvidia-smi -pl: the driver's admin check is capable(CAP_SYS_ADMIN), so root + that cap.
  pod_sc='{runAsUser: 0, runAsGroup: 0, runAsNonRoot: false}'
  ctr_sc='securityContext: {capabilities: {add: ["SYS_ADMIN"]}}'
else
  pod_sc='{runAsUser: 1000, runAsGroup: 1000, runAsNonRoot: true}'
  ctr_sc='securityContext: {allowPrivilegeEscalation: false}'
fi
# deadline = the plan's own length + 15 min, so a long airtime plan is not killed at 2 h
deadline=$(echo "${plan:-trigger:4:240:90,sustain:1:0:1200,hottrigger:2:240:90,cool:1:300:0}" | tr ',' '\n' \
  | awk -F: '{t += $2 * ($3 + $4)} END {print t + 900}')
image=$(kubectl get sts -n ai comfyui -o jsonpath='{.spec.template.spec.containers[0].image}')  # torch + CUDA, already cached
command='["python", "-u", "-c"]'
script="$here/soak.py"
requests='{cpu: "1", memory: 2Gi}'
limits='{memory: 8Gi}'
driver=compute,utility
extra_pod=
extra_env=
if "$cap_only"; then
  deadline=180
  command='["/bin/sh", "-c"]'
  script="$here/../../kubernetes/main/apps/ai/llama-server/app/card-b-power-cap.sh"
  ctr_sc='securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: ["ALL"], add: ["SYS_ADMIN"]}}'
  requests='{cpu: 50m, memory: 32Mi}'
  limits='{cpu: 100m, memory: 128Mi}'
  driver=utility
  extra_pod='automountServiceAccountToken: false'
  extra_env='            - name: POD_NODE_NAME
              valueFrom: {fieldRef: {fieldPath: spec.nodeName}}'
  placement='nodeSelector: {kubernetes.io/hostname: "talosw04"}
      tolerations:
        - {key: haynesops.com/gpu-test, operator: Equal, value: "true", effect: NoSchedule}'
fi
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
      ${extra_pod}
      runtimeClassName: nvidia
      ${placement}
      securityContext: ${pod_sc}
      containers:
        - name: soak
          image: ${image}
          command: ${command}
          ${ctr_sc}
          env:
            - {name: NVIDIA_VISIBLE_DEVICES, value: "${uuid}"}
            - {name: NVIDIA_DRIVER_CAPABILITIES, value: "${driver}"}
            - {name: HOME, value: /tmp}
${extra_env}
$( [ -n "$plan" ] && echo "            - {name: SOAK_PLAN, value: \"${plan}\"}" )
$( [ -n "$power" ] && echo "            - {name: SOAK_POWER_LIMIT_W, value: \"${power}\"}" )
$( [ -n "$abort_c" ] && echo "            - {name: SOAK_ABORT_C, value: \"${abort_c}\"}" )
          resources:
            requests: ${requests}
            limits: ${limits}
          args:
            - |
$(sed 's/^/              /' "$script")
YAML
