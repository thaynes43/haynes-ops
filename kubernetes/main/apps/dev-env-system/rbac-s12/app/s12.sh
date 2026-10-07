#!/bin/sh
# Spike S-12, the baseline guard (dev-env DESIGN-001 section 13, D-19; backlog 00-spikes).
# Runs as dev-agents/dev-env-agent and tries each haynes-ops #3392 path (each must be
# refused) and each runbook action (each must be allowed). Every write is a server-side
# dry run, so a guard that fails lets nothing through for real. Exec and proxy cannot be
# dry runs: they run `true` or read a page. One real flux reconcile of the haynes-ops
# GitRepository (a git fetch Flux does every 30 minutes anyway) checks the flux CLI's own
# calls.
#
# Output: one line per check, `PASS|FAIL <want> <id> <what>: <who decided> | <message>`,
# then a summary. `who` is the policy that refused, RBAC, or `admitted`.
# Light by design (CPU rule): about 40 kubectl calls, one at a time.
set -u
K="${KUBECTL:-kubectl}"
NOW=$(date +%s)
pass=0
fail=0

first_line() { printf '%s' "$1" | tr '\n' ' ' | cut -c1-260; }

who() {
  case "$1" in
    *dev-env-agent-guard*) echo dev-env-agent-guard ;;
    *dev-env-identity-guard*) echo dev-env-identity-guard ;;
    *dev-env-exec-guard*) echo kyverno/dev-env-exec-guard ;;
    *dev-env-v1-token-guard*) echo dev-env-v1-token-guard ;;
    *"cannot "*" in API group"*|*"cannot "*" resource"*) echo RBAC ;;
    *"denied the request"*|*"denied request"*) echo other-admission ;;
    *) echo "" ;;
  esac
}

# check <refused|allowed> <id> <what> <command...>
check() {
  want=$1 id=$2 what=$3
  shift 3
  out=$("$@" 2>&1)
  rc=$?
  by=$(who "$out")
  if [ -n "$by" ]; then got=refused; elif [ $rc -eq 0 ]; then got=allowed; by=admitted;
  else
    # Admitted, then failed for a reason that is not authorization (for example an exec
    # into a pod with no node, on a test API server).
    got=allowed; by="admitted (then: $(first_line "$out" | cut -c1-80))"
  fi
  if [ "$got" = "$want" ]; then res=PASS; pass=$((pass + 1)); else res=FAIL; fail=$((fail + 1)); fi
  echo "$res $want $id $what: $by | $(first_line "$out")"
}

job() { # job <namespace> <name> <extra pod-spec JSON fields> [container fields]
  cat <<EOF
{"apiVersion":"batch/v1","kind":"Job","metadata":{"name":"$2","namespace":"$1"},
 "spec":{"backoffLimit":0,"template":{"spec":{"restartPolicy":"Never"$3,
   "containers":[{"name":"s12","image":"docker.io/restic/restic:0.18.0","args":["version"]$4,
     "resources":{"limits":{"cpu":"100m","memory":"64Mi"}}}]}}}}
EOF
}
create_job() { job "$@" | $K create --dry-run=server -f -; }

# ── Targets: real objects, read as the agent (reads are cluster-wide) ──
first() { $K get "$@" -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}' 2>/dev/null |
  grep -v -E '^(dev-env-system|dev-agents|dev-tools|kube-system|flux-system)/' | head -1; }
SELF_NS=${SELF_NS:-dev-agents}
SELF_POD=${SELF_POD:-${HOSTNAME:-}}
DEPLOY=${DEPLOY:-$(first deployments -A)}
STS=${STS:-$(first statefulsets -A)}
DS=${DS:-$(first daemonsets -A)}
CRON=${CRON:-$(first cronjobs -A)}
KS=${KS:-$(first kustomizations.kustomize.toolkit.fluxcd.io -A)}
HR=${HR:-$(first helmreleases.helm.toolkit.fluxcd.io -A)}
GITREPO=${GITREPO:-flux-system/haynes-ops}
ES=${ES:-$(first externalsecrets.external-secrets.io -A)}
RS=${RS:-$($K get replicationsources.volsync.backube -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.spec.restic.repository}{"\n"}{end}' 2>/dev/null | head -1)}
PVC=${PVC:-$($K get pvc -n database -o jsonpath='{range .items[*]}database/{.metadata.name}{"\n"}{end}' 2>/dev/null | head -1)}
HEADLAMP=${HEADLAMP:-$($K get pods -n frontend -o jsonpath='{range .items[?(@.spec.serviceAccountName=="headlamp")]}frontend/{.metadata.name}{"\n"}{end}' 2>/dev/null | head -1)}
HOSTPID=${HOSTPID:-$($K get pods -n observability -o jsonpath='{range .items[?(@.spec.hostPID==true)]}observability/{.metadata.name}{"\n"}{end}' 2>/dev/null | head -1)}
FLUXPOD=${FLUXPOD:-$($K get pods -n flux-system -o jsonpath='{range .items[*]}flux-system/{.metadata.name}{"\n"}{end}' 2>/dev/null | head -1)}
KYVERNOPOD=${KYVERNOPOD:-$($K get pods -n kyverno -o jsonpath='{range .items[*]}kyverno/{.metadata.name}{"\n"}{end}' 2>/dev/null | head -1)}
KSYSPOD=${KSYSPOD:-$($K get pods -n kube-system -l k8s-app=kube-dns -o jsonpath='{range .items[*]}kube-system/{.metadata.name}{"\n"}{end}' 2>/dev/null | head -1)}
OPSPOD=${OPSPOD:-$($K get pods -n upgrade-agent -o jsonpath='{range .items[?(@.spec.serviceAccountName=="dev-env-ops")]}upgrade-agent/{.metadata.name}{"\n"}{end}' 2>/dev/null | head -1)}
TOOLS=${TOOLS:-$($K get pods -n rook-ceph -l app=rook-ceph-tools -o jsonpath='{range .items[*]}rook-ceph/{.metadata.name}{"\n"}{end}' 2>/dev/null | head -1)}
PROM=${PROM:-/api/v1/namespaces/observability/services/http:kube-prometheus-stack-prometheus:9090/proxy/api/v1/query?query=up}
ns() { echo "${1%%/*}"; }
nm() { echo "${1#*/}"; }

echo "S-12 as $($K auth whoami -o jsonpath='{.status.userInfo.username}' 2>/dev/null) at $(date -u +%FT%TZ)"
echo "targets: deploy=$DEPLOY sts=$STS ds=$DS cron=$CRON ks=$KS hr=$HR git=$GITREPO es=$ES volsync=$RS pvc=$PVC headlamp=$HEADLAMP hostpid=$HOSTPID flux=$FLUXPOD kyverno=$KYVERNOPOD kube-system=$KSYSPOD ops=$OPSPOD tools=$TOOLS self=$SELF_NS/$SELF_POD"

echo "── haynes-ops #3392 paths: each must be refused ──"
check refused P1 "Job as another ServiceAccount (frontend/headlamp)" \
  create_job frontend s12-as-headlamp ',"serviceAccountName":"headlamp"' ''
check refused P2 "Job mounting a Secret outside the short list" \
  create_job "$(ns "$RS")" s12-secret '' ',"envFrom":[{"secretRef":{"name":"s12-not-on-the-list"}}]'
check refused P2b "Job with a Secret volume outside the short list" \
  create_job "$(ns "$RS")" s12-secret-vol ',"volumes":[{"name":"s","secret":{"secretName":"s12-not-on-the-list"}}]' ''
check refused P2c "privileged Job" \
  create_job "$(ns "$RS")" s12-privileged '' ',"securityContext":{"privileged":true}'
check refused P2d "hostPath Job" \
  create_job "$(ns "$RS")" s12-hostpath ',"volumes":[{"name":"h","hostPath":{"path":"/"}}]' ''
check refused P2e "host-PID Job" \
  create_job "$(ns "$RS")" s12-hostpid ',"hostPID":true' ''
check refused P2f "volsync-named Job with SYS_ADMIN and Unconfined seccomp (Kyverno PSS exempts the name)" \
  create_job "$(ns "$RS")" volsync-src-s12 ',"securityContext":{"seccompProfile":{"type":"Unconfined"}}' \
  ',"securityContext":{"capabilities":{"add":["SYS_ADMIN","DAC_READ_SEARCH"]}}'
check refused P2g "Job with a hostPort" \
  create_job "$(ns "$RS")" s12-hostport '' ',"ports":[{"containerPort":8080,"hostPort":18080}]'
check refused P3 "Deployment image patch ($DEPLOY)" \
  $K -n "$(ns "$DEPLOY")" patch deployment "$(nm "$DEPLOY")" --dry-run=server --type=json \
  -p '[{"op":"replace","path":"/spec/template/spec/containers/0/image","value":"docker.io/library/busybox:1.37"}]'
check refused P3b "StatefulSet template patch ($STS)" \
  $K -n "$(ns "$STS")" patch statefulset "$(nm "$STS")" --dry-run=server --type=json \
  -p '[{"op":"add","path":"/spec/template/spec/containers/0/env","value":[{"name":"S12","value":"1"}]}]'
check refused P3c "DaemonSet ServiceAccount patch ($DS)" \
  $K -n "$(ns "$DS")" patch daemonset "$(nm "$DS")" --dry-run=server --type=merge \
  -p '{"spec":{"template":{"spec":{"serviceAccountName":"s12-other"}}}}'
check refused P3d "Deployment spec.paused patch ($DEPLOY)" \
  $K -n "$(ns "$DEPLOY")" patch deployment "$(nm "$DEPLOY")" --dry-run=server --type=merge -p '{"spec":{"paused":true}}'
check refused P3e "CronJob jobTemplate patch ($CRON)" \
  $K -n "$(ns "$CRON")" patch cronjob "$(nm "$CRON")" --dry-run=server --type=json \
  -p '[{"op":"replace","path":"/spec/jobTemplate/spec/template/spec/containers/0/image","value":"docker.io/library/busybox:1.37"}]'
check refused P4 "Flux Kustomization spec.path patch ($KS)" \
  $K -n "$(ns "$KS")" patch kustomizations.kustomize.toolkit.fluxcd.io "$(nm "$KS")" --dry-run=server --type=merge \
  -p '{"spec":{"path":"./kubernetes/main/apps/frontend"}}'
check refused P4b "Flux HelmRelease values patch ($HR)" \
  $K -n "$(ns "$HR")" patch helmreleases.helm.toolkit.fluxcd.io "$(nm "$HR")" --dry-run=server --type=merge \
  -p '{"spec":{"values":{"s12":true}}}'
check refused P4c "Flux GitRepository url patch ($GITREPO)" \
  $K -n "$(ns "$GITREPO")" patch gitrepositories.source.toolkit.fluxcd.io "$(nm "$GITREPO")" --dry-run=server --type=merge \
  -p '{"spec":{"url":"https://github.com/example/example"}}'
check refused P4d "Flux Kustomization label patch ($KS)" \
  $K -n "$(ns "$KS")" label kustomizations.kustomize.toolkit.fluxcd.io "$(nm "$KS")" --dry-run=server s12=1
check refused P4e "ExternalSecret spec patch ($ES)" \
  $K -n "$(ns "$ES")" patch externalsecrets.external-secrets.io "$(nm "$ES")" --dry-run=server --type=merge \
  -p '{"spec":{"refreshInterval":"1m"}}'
check refused P5 "exec into the headlamp pod ($HEADLAMP, cluster-admin)" \
  $K -n "$(ns "$HEADLAMP")" exec "$(nm "$HEADLAMP")" -- true
check refused P5b "exec into a host-PID pod ($HOSTPID)" \
  $K -n "$(ns "$HOSTPID")" exec "$(nm "$HOSTPID")" -- true
check refused P5c "exec into a flux-system pod ($FLUXPOD)" \
  $K -n "$(ns "$FLUXPOD")" exec "$(nm "$FLUXPOD")" -- true
check refused P5d "exec into a kyverno pod ($KYVERNOPOD; Kyverno never sees its own namespace)" \
  $K -n "$(ns "$KYVERNOPOD")" exec "$(nm "$KYVERNOPOD")" -- true
check refused P5e "exec into a kube-system pod ($KSYSPOD)" \
  $K -n "$(ns "$KSYSPOD")" exec "$(nm "$KSYSPOD")" -- true
check refused P5f "exec into the v1 dev-env-ops pod ($OPSPOD, v1 OPERATOR tier)" \
  $K -n "$(ns "$OPSPOD")" exec "$(nm "$OPSPOD")" -- true

echo "── the dev-env namespaces: each must be refused ──"
check refused N1 "exec into a dev-agents pod (own pod; the VAP on CONNECT)" \
  $K -n "$SELF_NS" exec "$SELF_POD" -- true
check refused N2 "API-server proxy into a dev-agents pod (CONNECT)" \
  $K get --raw "/api/v1/namespaces/$SELF_NS/pods/$SELF_POD/proxy/"
check refused N3 "pod delete in dev-agents" \
  $K -n "$SELF_NS" delete pod "$SELF_POD" --dry-run=server
check refused N4 "Job in dev-agents" \
  create_job "$SELF_NS" s12-in-dev-agents '' ''
check refused N5 "Secret read (RBAC)" \
  $K -n "$SELF_NS" get secrets

echo "── runbook actions: each must be allowed ──"
check allowed A1 "rollout restart Deployment ($DEPLOY; the patch kubectl rollout restart sends)" \
  $K -n "$(ns "$DEPLOY")" patch deployment "$(nm "$DEPLOY")" --dry-run=server \
  -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"kubectl.kubernetes.io/restartedAt\":\"$(date -u +%FT%TZ)\"}}}}}"
check allowed A1b "rollout restart StatefulSet ($STS; the patch kubectl rollout restart sends)" \
  $K -n "$(ns "$STS")" patch statefulset "$(nm "$STS")" --dry-run=server \
  -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"kubectl.kubernetes.io/restartedAt\":\"$(date -u +%FT%TZ)\"}}}}}"
check allowed A1c "rollout restart DaemonSet ($DS; the patch kubectl rollout restart sends)" \
  $K -n "$(ns "$DS")" patch daemonset "$(nm "$DS")" --dry-run=server \
  -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"kubectl.kubernetes.io/restartedAt\":\"$(date -u +%FT%TZ)\"}}}}}"
check allowed A2 "CronJob suspend ($CRON)" \
  $K -n "$(ns "$CRON")" patch cronjob "$(nm "$CRON")" --dry-run=server --type=merge -p '{"spec":{"suspend":true}}'
check allowed A3 "flux reconcile: annotate Kustomization ($KS)" \
  $K -n "$(ns "$KS")" annotate kustomizations.kustomize.toolkit.fluxcd.io "$(nm "$KS")" --dry-run=server --overwrite \
  "reconcile.fluxcd.io/requestedAt=s12-$NOW"
check allowed A3b "flux reconcile --force: annotate HelmRelease ($HR)" \
  $K -n "$(ns "$HR")" annotate helmreleases.helm.toolkit.fluxcd.io "$(nm "$HR")" --dry-run=server --overwrite \
  "reconcile.fluxcd.io/requestedAt=s12-$NOW" "reconcile.fluxcd.io/forceAt=s12-$NOW"
check allowed A3c "flux reconcile source git (real; $GITREPO)" \
  ${FLUX:-flux} reconcile source git "$(nm "$GITREPO")" -n "$(ns "$GITREPO")" --timeout=2m
check allowed A4 "flux suspend: Kustomization spec.suspend ($KS)" \
  $K -n "$(ns "$KS")" patch kustomizations.kustomize.toolkit.fluxcd.io "$(nm "$KS")" --dry-run=server --type=merge \
  -p '{"spec":{"suspend":true}}'
check allowed A4b "flux resume: HelmRelease spec.suspend ($HR)" \
  $K -n "$(ns "$HR")" patch helmreleases.helm.toolkit.fluxcd.io "$(nm "$HR")" --dry-run=server --type=merge \
  -p '{"spec":{"suspend":false}}'
check allowed A5 "volsync unlock Job ($RS)" \
  create_job "$(ns "$RS")" s12-restic-unlock '' ",\"envFrom\":[{\"secretRef\":{\"name\":\"$(nm "$RS")\"}}]"
check allowed A6 "ExternalSecret force-sync ($ES)" \
  $K -n "$(ns "$ES")" annotate externalsecrets.external-secrets.io "$(nm "$ES")" --dry-run=server --overwrite "force-sync=s12-$NOW"
check allowed A7 "pod delete ($TOOLS)" \
  $K -n "$(ns "$TOOLS")" delete pod "$(nm "$TOOLS")" --dry-run=server
check allowed A8 "exec into the rook toolbox ($TOOLS)" \
  $K -n "$(ns "$TOOLS")" exec "$(nm "$TOOLS")" -- true
check allowed A9 "API-server proxy to Prometheus" \
  $K get --raw "$PROM"
check allowed A10 "CNPG PVC delete in database ($PVC)" \
  $K -n "$(ns "$PVC")" delete pvc "$(nm "$PVC")" --dry-run=server --wait=false
check allowed A11 "read the v2 sessions" \
  $K -n dev-agents get agentsessions.dev-env.haynesops.com

echo "S-12 summary: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
