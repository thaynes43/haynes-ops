#!/bin/bash
# sa-can-i.sh — authoritative "can subject X do Y?" via SubjectAccessReview.
#
# WHY: `kubectl auth can-i --as=<subject>` through the Omni-proxied kubeconfig
# is a SILENT FALSE POSITIVE — the SaaS proxy drops Impersonate-* headers
# (`kubectl auth whoami --as=<anything>` still returns your own admin identity),
# so every such check is answered as system:masters → always "yes".
# A SubjectAccessReview asks the API server about the subject directly (no
# impersonation involved) and names the binding that decided the answer.
# See .agents/reference/cluster-inspection.md ("Verifying RBAC").
#
# Usage:
#   scripts/sa-can-i.sh <verb> <resource>[.<apiGroup>][/<subresource>] [-n <namespace>] [--name <resourceName>] <subject>
#     <subject>  sa:<namespace>:<name>  or  user:<name>
#
# Examples:
#   scripts/sa-can-i.sh delete persistentvolumeclaims -n database sa:dev:dev-env
#   scripts/sa-can-i.sh patch kustomizations.kustomize.toolkit.fluxcd.io -n flux-system sa:dev:dev-env
#
# Exit code mirrors `kubectl auth can-i`: 0 = allowed, 1 = denied.
set -euo pipefail

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: missing required command: $1" >&2
    exit 2
  }
}
need_cmd kubectl
need_cmd jq

usage() { grep '^#' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

verb="" resource="" namespace="" resource_name="" subject=""
while [ $# -gt 0 ]; do
  case "$1" in
    -n|--namespace) namespace="$2"; shift 2 ;;
    --name) resource_name="$2"; shift 2 ;;
    -h|--help) usage ;;
    *)
      if [ -z "$verb" ]; then verb="$1"
      elif [ -z "$resource" ]; then resource="$1"
      elif [ -z "$subject" ]; then subject="$1"
      else echo "error: unexpected argument: $1" >&2; usage
      fi
      shift ;;
  esac
done
[ -n "$verb" ] && [ -n "$resource" ] && [ -n "$subject" ] || usage

# <resource>[.<apiGroup>][/<subresource>] — same shapes kubectl accepts.
subresource="${resource#*/}"; [ "$subresource" = "$resource" ] && subresource=""
resource="${resource%%/*}"
api_group="${resource#*.}"; [ "$api_group" = "$resource" ] && api_group=""
resource="${resource%%.*}"

case "$subject" in
  sa:*:*)
    sa_ns="$(echo "$subject" | cut -d: -f2)"
    sa_name="$(echo "$subject" | cut -d: -f3)"
    user="system:serviceaccount:${sa_ns}:${sa_name}"
    groups="$(jq -cn --arg ns "$sa_ns" \
      '["system:serviceaccounts", "system:serviceaccounts:\($ns)", "system:authenticated"]')"
    ;;
  user:*)
    user="${subject#user:}"
    groups='["system:authenticated"]'
    ;;
  *) echo "error: subject must be sa:<ns>:<name> or user:<name>" >&2; usage ;;
esac

body="$(jq -cn \
  --arg user "$user" --argjson groups "$groups" \
  --arg ns "$namespace" --arg verb "$verb" --arg resource "$resource" \
  --arg group "$api_group" --arg subresource "$subresource" --arg name "$resource_name" \
  '{apiVersion: "authorization.k8s.io/v1", kind: "SubjectAccessReview",
    spec: {user: $user, groups: $groups,
           resourceAttributes: ({namespace: $ns, verb: $verb, resource: $resource,
                                 group: $group, subresource: $subresource, name: $name}
                                | with_entries(select(.value != "")))}}')"

status="$(printf '%s' "$body" \
  | kubectl create --raw /apis/authorization.k8s.io/v1/subjectaccessreviews -f - \
  | jq -c .status)"

allowed="$(printf '%s' "$status" | jq -r .allowed)"
reason="$(printf '%s' "$status" | jq -r '.reason // empty')"

if [ "$allowed" = "true" ]; then
  echo "ALLOWED${reason:+ — $reason}"
else
  echo "DENIED${reason:+ — $reason}"
  exit 1
fi
