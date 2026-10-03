#!/usr/bin/env bash
# Render a read-only per-fan sampler Job (fans.py) for the eGPU test node.
# Usage: render-fans-job.sh <job-name> [seconds=180] | kubectl apply -f -
# Runs next to a soak Job (same node, same card) and needs no privileges. Job names that
# start with egpu-test- keep the runbook's cleanup glob simple.
set -euo pipefail
name=${1:?job name, e.g. egpu-test-fans-1}
secs=${2:-180}
node=${SOAK_NODE:-talosw04}
here=$(cd "$(dirname "$0")" && pwd)
cat <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: ${name}
  namespace: ai
  labels: {app: gpu-soak, app.kubernetes.io/name: gpu-soak}
spec:
  backoffLimit: 0
  activeDeadlineSeconds: $((secs + 600))
  ttlSecondsAfterFinished: 604800
  template:
    metadata:
      labels: {app: gpu-soak, app.kubernetes.io/name: gpu-soak}
    spec:
      restartPolicy: Never
      runtimeClassName: nvidia
      nodeSelector: {kubernetes.io/hostname: "${node}"}
      tolerations:
        - {key: haynesops.com/gpu-test, operator: Exists, effect: NoSchedule}
      securityContext: {runAsUser: 1000, runAsGroup: 1000, runAsNonRoot: true, seccompProfile: {type: RuntimeDefault}}
      containers:
        - name: fans
          image: docker.io/library/python:3.13-slim
          command: ["python", "-u", "-c"]
          securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: ["ALL"]}}
          env:
            - {name: NVIDIA_VISIBLE_DEVICES, value: all}
            - {name: NVIDIA_DRIVER_CAPABILITIES, value: utility}
          resources:
            requests: {cpu: 10m, memory: 32Mi}
            limits: {memory: 128Mi}
          args:
            - |
$(sed 's/^/              /' "$here/fans.py")
            - "${secs}"
YAML
