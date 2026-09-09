#!/usr/bin/env sh
#
# node-out-of-service — apply (and later remove) Kubernetes' "non-graceful node
# shutdown" taint so RWO-PVC workloads reschedule off a node that died hard.
#
# WHY (incident 2026-09-09): a hard power-off of the talosw02 Proxmox VM left every
# RWO-PVC pod scheduled there — prometheus-0, loki-0, emqx-core-0, alertmanager-1,
# plex, sabnzbd — Terminating for 3.5 hours. Kubernetes deliberately will NOT
# force-delete pods or detach Ceph RBD VolumeAttachments from a node that vanished
# without a graceful shutdown: it cannot prove the kubelet stopped writing, so a
# StatefulSet's "at most one" guarantee forbids rescheduling. The documented
# operator-supplied proof is the taint
#   node.kubernetes.io/out-of-service=nodeshutdown:NoExecute
# which lets the GC controller force-delete the pods and the attach/detach
# controller detach the volumes, so everything reschedules within ~1 minute.
# The taint MUST come off once the node is back, or that node stays unusable.
#
# PORTABILITY: written for POSIX sh, not bash — the container image
# (docker.io/alpine/kubectl) is alpine-minirootfs + curl + kubectl, so BusyBox ash
# is the only shell present. It still runs unchanged under bash/zsh on a laptop.
# Timestamps are converted in awk rather than date(1) on purpose: GNU date wants
# `-d <ts>`, BusyBox date wants `-D <fmt> -d <ts>`, and BSD/macOS date wants
# `-j -f <fmt>` — the awk days-from-civil arithmetic below behaves identically
# under gawk, mawk, BusyBox awk and BSD awk. jq is likewise avoided: the image does
# not ship it, so every field comes out of `kubectl -o jsonpath`.
#
# Read-only dry run from a laptop:
#   DRY_RUN=true KUBECONFIG=./kubeconfig ./node-out-of-service.sh
#
set -eu
# `pipefail` is not POSIX. bash and BusyBox ash both have it, dash does not — opt in
# only where it exists rather than aborting on an unknown option.
if (set -o pipefail 2>/dev/null); then set -o pipefail; fi

# How long a node must have been not-Ready before it is presumed dead rather than
# briefly flapping (kubelet restart, brief network blip, Talos upgrade reboot).
# 480s sits well above the ~5m pod-eviction-timeout, so normal churn never trips it.
THRESHOLD_SECONDS="${THRESHOLD_SECONDS:-480}"
# Blast-radius guard: if MORE than this many nodes are not-Ready at once, the far
# likelier explanation is a partition or an apiserver problem, not N dead hosts —
# so taint nothing and let a human look.
MAX_UNREADY="${MAX_UNREADY:-1}"
DRY_RUN="${DRY_RUN:-false}"
TAINT_KEY="${TAINT_KEY:-node.kubernetes.io/out-of-service}"
TAINT_VALUE="${TAINT_VALUE:-nodeshutdown}"
TAINT_EFFECT="${TAINT_EFFECT:-NoExecute}"
# Settle window before the taint comes off: a node reports Ready a beat before its
# CSI plugin has re-registered, and pulling the taint early lets pods land on a node
# that cannot yet attach their volumes.
READY_SETTLE_SECONDS="${READY_SETTLE_SECONDS:-60}"
# Every call is bounded so a wedged apiserver cannot outlive the Job's
# activeDeadlineSeconds and leave a stuck pod behind.
REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-15s}"

log() {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $*"
}

# In-cluster auth must be EXPLICIT. kubectl only auto-detects the in-cluster service
# account when the merged client config is byte-for-byte the built-in default
# (http://localhost:8080); any override — and --request-timeout IS one — makes it
# "a real config", so it never even looks at KUBERNETES_SERVICE_HOST and every call
# dies against localhost:8080 (seen live 2026-09-09: the first three Jobs logged
# nodes=0 while a plain `kubectl get nodes` in the same pod worked). So when the
# service-account token is mounted and no KUBECONFIG was handed in, write a minimal
# kubeconfig that points at the mounted token (tokenFile, so the secret never
# lands on a command line or in this file) and use that. A laptop run with
# KUBECONFIG set is untouched.
SA_DIR="${SA_DIR:-/var/run/secrets/kubernetes.io/serviceaccount}"
if [ -z "${KUBECONFIG:-}" ] && [ -n "${KUBERNETES_SERVICE_HOST:-}" ] && [ -r "${SA_DIR}/token" ]; then
    umask 077
    cat > "${TMPDIR:-/tmp}/node-out-of-service.kubeconfig" <<KCFG
apiVersion: v1
kind: Config
clusters:
- name: in-cluster
  cluster:
    server: https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT:-443}
    certificate-authority: ${SA_DIR}/ca.crt
users:
- name: sa
  user:
    tokenFile: ${SA_DIR}/token
contexts:
- name: in-cluster
  context:
    cluster: in-cluster
    user: sa
current-context: in-cluster
KCFG
    KUBECONFIG="${TMPDIR:-/tmp}/node-out-of-service.kubeconfig"
    export KUBECONFIG
fi

# Run a mutating kubectl, or print exactly what would have run under DRY_RUN.
mutate() {
    if [ "${DRY_RUN}" = "true" ]; then
        log "DRY_RUN: would run: kubectl --request-timeout=${REQUEST_TIMEOUT} $*"
    else
        kubectl --request-timeout="${REQUEST_TIMEOUT}" "$@"
    fi
}

# iso8601_epoch <RFC3339-UTC-timestamp> -> seconds since the Unix epoch, or "".
# Kubernetes metav1.Time is always UTC with second precision (2026-09-09T11:14:06Z),
# so no zone or fractional handling is needed. The character-class regex avoids
# interval expressions ({4}), which BusyBox awk does not enable by default.
iso8601_epoch() {
    echo "$1" | awk '
        function days_from_civil(y, m, d,   era, yoe, doy, doe) {
            if (m <= 2) y = y - 1
            era = int((y >= 0 ? y : y - 399) / 400)
            yoe = y - era * 400
            doy = int((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5) + d - 1
            doe = yoe * 365 + int(yoe / 4) - int(yoe / 100) + doy
            return era * 146097 + doe - 719468
        }
        {
            if ($0 !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]/) exit
            print days_from_civil(substr($0, 1, 4) + 0, substr($0, 6, 2) + 0, substr($0, 9, 2) + 0) * 86400 \
                + (substr($0, 12, 2) + 0) * 3600 + (substr($0, 15, 2) + 0) * 60 + (substr($0, 18, 2) + 0)
        }'
}

total=0
skipped=0
unready=0
tainted_count=0
untainted_count=0
to_taint=""    # space-separated "name=age_seconds"
to_untaint=""  # space-separated "name=age_seconds"

# One summary line always, even when `set -e` aborts on a failed kubectl — an
# unattended job that dies silently is indistinguishable from one that found nothing.
# The trap is armed before the first API call so the counters are always defined.
summary() {
    log "summary: nodes=${total} control_plane_skipped=${skipped} not_ready=${unready} tainted=${tainted_count} untainted=${untainted_count} dry_run=${DRY_RUN}"
}
trap summary EXIT

now="$(date -u +%s)"

# One listing of every node drives every decision below: name | Ready status |
# Ready lastTransitionTime | comma-terminated taint keys. The Ready condition is
# selected twice so the field count stays at four even for a node that somehow has
# no Ready condition at all; a node with no taints yields an empty fourth field
# rather than an error.
nodes="$(kubectl --request-timeout="${REQUEST_TIMEOUT}" get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{range .status.conditions[?(@.type=="Ready")]}{.status}{end}{"|"}{range .status.conditions[?(@.type=="Ready")]}{.lastTransitionTime}{end}{"|"}{range .spec.taints[*]}{.key}{","}{end}{"\n"}{end}')"

# ...and one name-only listing is the control-plane deny-list. This deliberately
# does NOT come out of the listing above: the control-plane label's VALUE is the
# empty string, and jsonpath renders an absent key and an empty-valued key
# identically — so "is this a control-plane node" is answered server-side by the
# label selector, where it cannot be misread. Tainting a control-plane node would
# NoExecute the apiserver off it; that must never happen by accident.
control_plane="$(kubectl --request-timeout="${REQUEST_TIMEOUT}" get nodes -l node-role.kubernetes.io/control-plane -o jsonpath='{range .items[*]}{.metadata.name}{" "}{end}')"

# Fed by here-doc, not a pipe: `... | while read` puts the loop in a subshell under
# POSIX sh, and every counter below would be discarded when it exits.
while IFS='|' read -r name ready ltt taints; do
    [ -n "${name}" ] || continue
    total=$((total + 1))

    case " ${control_plane} " in
        *" ${name} "*)
            skipped=$((skipped + 1))
            continue
            ;;
    esac

    [ "${ready}" = "True" ] || unready=$((unready + 1))

    tainted=false
    case ",${taints}" in
        *",${TAINT_KEY},"*) tainted=true ;;
    esac

    since="$(iso8601_epoch "${ltt}")"
    if [ -z "${since}" ]; then
        log "WARN ${name}: unparsable Ready lastTransitionTime '${ltt}' — leaving alone"
        continue
    fi
    age=$((now - since))

    if [ "${ready}" != "True" ] && [ "${tainted}" = "false" ] && [ "${age}" -gt "${THRESHOLD_SECONDS}" ]; then
        to_taint="${to_taint}${name}=${age} "
    elif [ "${ready}" = "True" ] && [ "${tainted}" = "true" ] && [ "${age}" -ge "${READY_SETTLE_SECONDS}" ]; then
        to_untaint="${to_untaint}${name}=${age} "
    fi
done <<EOF
${nodes}
EOF

if [ "${unready}" -gt "${MAX_UNREADY}" ]; then
    log "guard: ${unready} nodes not Ready (> MAX_UNREADY=${MAX_UNREADY}) — refusing to taint (possible partition/API issue)"
    to_taint=""
fi

for entry in ${to_taint}; do
    name="${entry%%=*}"
    age="${entry##*=}"
    log "taint ${name}: Ready!=True for ${age}s (> THRESHOLD_SECONDS=${THRESHOLD_SECONDS}) — applying ${TAINT_KEY}=${TAINT_VALUE}:${TAINT_EFFECT}"
    mutate taint node "${name}" "${TAINT_KEY}=${TAINT_VALUE}:${TAINT_EFFECT}"
    tainted_count=$((tainted_count + 1))
done

# Removal runs even when the guard tripped: getting a recovered node back into
# service is never the risky half, and a stuck taint keeps the node unschedulable.
for entry in ${to_untaint}; do
    name="${entry%%=*}"
    age="${entry##*=}"
    log "untaint ${name}: Ready for ${age}s (>= READY_SETTLE_SECONDS=${READY_SETTLE_SECONDS}) — removing ${TAINT_KEY}"
    mutate taint node "${name}" "${TAINT_KEY}-"
    untainted_count=$((untainted_count + 1))
done

# summary() fires from the EXIT trap.
