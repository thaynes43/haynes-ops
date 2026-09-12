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
# Control-plane nodes are IN SCOPE (2026-09-12) but get their own, longer window.
# A bare-metal master's POST + Talos boot is slower than a VM's, and a false
# positive on a master costs more, so give it more rope. This adds delay, not
# information — it is the weakest of the guards below, not the main one.
CP_THRESHOLD_SECONDS="${CP_THRESHOLD_SECONDS:-900}"
# Blast-radius guard: if MORE than this many nodes have an UNADJUDICATED failure
# at once, the far likelier explanation is a partition or an apiserver problem,
# not N dead hosts — so taint nothing and let a human look. "Unadjudicated" =
# not-Ready AND not already carrying our taint: a node we tainted days ago is a
# decided case, not fresh evidence of a partition, and counting it would wedge
# remediation for every node that fails afterwards.
MAX_UNREADY="${MAX_UNREADY:-1}"
DRY_RUN="${DRY_RUN:-false}"
TAINT_KEY="${TAINT_KEY:-node.kubernetes.io/out-of-service}"
TAINT_VALUE="${TAINT_VALUE:-nodeshutdown}"
TAINT_EFFECT="${TAINT_EFFECT:-NoExecute}"
# ...but NoSchedule on control-plane nodes. Both consumers of this taint —
# podgc.gcTerminating and attachdetach/reconciler.hasOutOfServiceTaint — match on
# the KEY only and ignore value and effect, and the upstream docs say either
# effect is valid. So NoSchedule delivers the whole remedy (force-delete the
# already-Terminating pods, force-detach their volumes) while avoiding two
# NoExecute-only side effects on a master: evicting the ~11 DaemonSets that
# tolerate not-ready/unreachable but not this taint (multus, both Ceph CSI
# nodeplugins, node-exporter, promtail, ...), and the mirror-pod delete/recreate
# loop a stuck taint would cause on a recovered master (tainteviction has no
# readiness check — it acts on the taint alone, and kubelet just puts static pods
# straight back). Flag: if a target workload is ever given an INFINITE unreachable
# toleration, NoSchedule silently stops rescuing it — it relies on the default
# 300s unreachable:NoExecute to set the DeletionTimestamp that podgc needs.
CP_TAINT_EFFECT="${CP_TAINT_EFFECT:-NoSchedule}"
# Auditable escape hatch. A node labelled this way is never tainted. Its empty
# state is the SAFE default (nothing excluded), unlike the old hardcoded
# control-plane deny-list whose empty state was fail-open.
EXCLUDE_LABEL="${EXCLUDE_LABEL:-node-out-of-service.haynesops.com/exclude=true}"
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
skipped=0   # nodes carrying EXCLUDE_LABEL
unready=0   # not-Ready AND not already tainted — see MAX_UNREADY
threshold="${THRESHOLD_SECONDS}"
effect="${TAINT_EFFECT}"
tainted_count=0
untainted_count=0
to_taint=""    # space-separated "name=age_seconds"
to_untaint=""  # space-separated "name=age_seconds"

# One summary line always, even when `set -e` aborts on a failed kubectl — an
# unattended job that dies silently is indistinguishable from one that found nothing.
# The trap is armed before the first API call so the counters are always defined.
summary() {
    log "summary: nodes=${total} excluded=${skipped} unadjudicated_not_ready=${unready} tainted=${tainted_count} untainted=${untainted_count} dry_run=${DRY_RUN}"
}
trap summary EXIT

now="$(date -u +%s)"

# One listing of every node drives every decision below: name | Ready status |
# Ready lastTransitionTime | comma-terminated taint keys. The Ready condition is
# selected twice so the field count stays at four even for a node that somehow has
# no Ready condition at all; a node with no taints yields an empty fourth field
# rather than an error.
nodes="$(kubectl --request-timeout="${REQUEST_TIMEOUT}" get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{range .status.conditions[?(@.type=="Ready")]}{.status}{end}{"|"}{range .status.conditions[?(@.type=="Ready")]}{.lastTransitionTime}{end}{"|"}{range .spec.taints[*]}{.key}{","}{end}{"\n"}{end}')"

# ...and one name-only listing says which nodes are control-plane. This deliberately
# does NOT come out of the listing above: the control-plane label's VALUE is the
# empty string, and jsonpath renders an absent key and an empty-valued key
# identically — so "is this a control-plane node" is answered server-side by the
# label selector, where it cannot be misread.
#
# This is no longer a deny-list. It now only selects which THRESHOLD and EFFECT a
# node gets. The old code skipped these nodes outright, justified by "tainting a
# control-plane node would NoExecute the apiserver off it" — which is false. The
# apiserver, scheduler and controller-manager are Talos-supervised static pods;
# their API objects are mirror pods, and deleting a mirror pod is a pure API
# operation that kubelet immediately undoes from the on-disk manifest (kubelet.go
# HandlePodRemoves takes the wasMirror branch and never reaches deletePod).
# Moreover the default unreachable:NoExecute toleration already deletes those same
# mirror pods at T+300s, a full 180s BEFORE this job's old 480s window — so by the
# time our taint lands, the eviction it supposedly caused has already happened, with
# no ill effect. etcd is not a Kubernetes object on Talos at all (machined-managed,
# `kubectl get pods -A | grep etcd` is empty), so quorum is untouchable from here.
control_plane="$(kubectl --request-timeout="${REQUEST_TIMEOUT}" get nodes -l node-role.kubernetes.io/control-plane -o jsonpath='{range .items[*]}{.metadata.name}{" "}{end}')"
# FAIL CLOSED. If this selector ever comes back empty — label rename, a filtered
# response, an RBAC change — every master would silently fall into the worker class
# and be tainted on the shorter window with NoExecute. Refuse to act instead.
if [ -z "${control_plane}" ]; then
    log "FATAL: control-plane label selector returned no nodes — refusing to act"
    exit 1
fi

# Opt-out list. Unlike the control-plane selector this one is ALLOWED to be empty:
# "nothing is excluded" is the safe default.
excluded="$(kubectl --request-timeout="${REQUEST_TIMEOUT}" get nodes -l "${EXCLUDE_LABEL}" -o jsonpath='{range .items[*]}{.metadata.name}{" "}{end}')"

# Fed by here-doc, not a pipe: `... | while read` puts the loop in a subshell under
# POSIX sh, and every counter below would be discarded when it exits.
while IFS='|' read -r name ready ltt taints; do
    [ -n "${name}" ] || continue
    total=$((total + 1))

    tainted=false
    case ",${taints}" in
        *",${TAINT_KEY},"*) tainted=true ;;
    esac

    # The guard counter is computed for EVERY node, control-plane included, and
    # BEFORE any skip. The old code did `continue` on control-plane nodes before
    # this line, so a NotReady master was invisible to MAX_UNREADY: losing two of
    # three masters — the textbook "possible partition/API issue" — did not trip
    # the guard at all, and the job would still force-detach a worker's volumes
    # mid-incident. Already-tainted nodes are excluded so one long-dead node does
    # not permanently wedge remediation for everything that fails after it.
    if [ "${ready}" != "True" ] && [ "${tainted}" = "false" ]; then
        unready=$((unready + 1))
    fi

    case " ${excluded} " in
        *" ${name} "*)
            skipped=$((skipped + 1))
            continue
            ;;
    esac

    case " ${control_plane} " in
        *" ${name} "*) threshold="${CP_THRESHOLD_SECONDS}"; effect="${CP_TAINT_EFFECT}" ;;
        *)             threshold="${THRESHOLD_SECONDS}";    effect="${TAINT_EFFECT}" ;;
    esac

    since="$(iso8601_epoch "${ltt}")"
    if [ -z "${since}" ]; then
        log "WARN ${name}: unparsable Ready lastTransitionTime '${ltt}' — leaving alone"
        continue
    fi
    age=$((now - since))

    # Ready=Unknown ONLY — never Ready=False. These are different claims and
    # Kubernetes encodes the difference deliberately (nodelifecycle maps Unknown ->
    # node.kubernetes.io/unreachable, False -> node.kubernetes.io/not-ready):
    # Unknown means "kubelet stopped posting node status", i.e. nobody is home,
    # while False means the kubelet is alive and self-reporting a problem — Cilium
    # down on a master gives Ready=False with every container still running and
    # still writing. Force-detaching a Ceph RBD image out from under a live writer
    # is the documented data-corruption path, and upstream's precondition is
    # explicit: "it should be verified that the node is already in shutdown or
    # power off state". The old `!= "True"` test included False and was one Cilium
    # outage away from doing exactly that. This narrows the window; it does not
    # close it (a wedged kubelet that stops heartbeating with containers alive
    # still reads Unknown) — upstream has no better signal either.
    if [ "${ready}" = "Unknown" ] && [ "${tainted}" = "false" ] && [ "${age}" -gt "${threshold}" ]; then
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

# REMOVAL RUNS FIRST, and unconditionally. Putting it second meant `set -e` aborted
# the whole script the moment any single `kubectl taint` failed, so one erroring
# node could leave a fully recovered node tainted indefinitely — directly
# contradicting the README's promise that removal "runs even when the guard
# tripped". That matters far more now that masters are in scope: a stuck taint on
# a recovered master keeps the only nodes that can host the IoT-VLAN workloads
# unusable. Each mutation is wrapped so its failure is a WARN, not an abort.
for entry in ${to_untaint}; do
    name="${entry%%=*}"
    age="${entry##*=}"
    log "untaint ${name}: Ready for ${age}s (>= READY_SETTLE_SECONDS=${READY_SETTLE_SECONDS}) — removing ${TAINT_KEY}"
    if mutate taint node "${name}" "${TAINT_KEY}-"; then
        [ "${DRY_RUN}" = "true" ] || untainted_count=$((untainted_count + 1))
    else
        log "WARN ${name}: untaint failed — will retry next tick"
    fi
done

for entry in ${to_taint}; do
    name="${entry%%=*}"
    age="${entry##*=}"
    # Recompute the class here so the log line names the threshold actually used.
    case " ${control_plane} " in
        *" ${name} "*) threshold="${CP_THRESHOLD_SECONDS}"; effect="${CP_TAINT_EFFECT}"; class="control-plane" ;;
        *)             threshold="${THRESHOLD_SECONDS}";    effect="${TAINT_EFFECT}";    class="worker" ;;
    esac
    log "taint ${name} (${class}): Ready=Unknown for ${age}s (> ${threshold}s) — applying ${TAINT_KEY}=${TAINT_VALUE}:${effect}"
    if mutate taint node "${name}" "${TAINT_KEY}=${TAINT_VALUE}:${effect}"; then
        [ "${DRY_RUN}" = "true" ] || tainted_count=$((tainted_count + 1))
    else
        log "WARN ${name}: taint failed — will retry next tick"
    fi
done

# summary() fires from the EXIT trap.
