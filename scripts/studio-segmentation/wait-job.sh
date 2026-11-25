#!/usr/bin/env bash
# Wait for the Job and capture diagnostics once its pods have stopped on failure.
set -euo pipefail

job=${1:?Job name required}
deadline=$((SECONDS + ${2:?Wait seconds required}))
condition=
while (( SECONDS < deadline )); do
  condition=$(kubectl -n ai --request-timeout=10s get "job/$job" -o json |
    jq -r '[.status.conditions[]? | select(.status == "True" and
      (.type == "Complete" or .type == "Failed"))][0].type // ""') || condition=
  case $condition in
    Complete) exit 0 ;;
    Failed) break ;;
  esac
  sleep 5
done

printf 'Job %s ended the wait with %s\n' "$job" "${condition:-no terminal condition}" >&2
kubectl -n ai --request-timeout=10s get "job/$job" -o yaml >&2 || true
kubectl -n ai --request-timeout=10s get pods -l "batch.kubernetes.io/job-name=$job" -o json |
  jq '[.items[] | {pod: .metadata.name, phase: .status.phase,
       containers: [.status.containerStatuses[]? | {name, state, lastState}]}]' >&2 || true
kubectl -n ai --request-timeout=10s logs "job/$job" -c segmentation >&2 || true
exit 1
