# 02 — PoC deployment (namespace, HelmRelease, PVC, ingress, egress)

**Status:** backlog
**Depends on:** 01 (image published)

## Goal

One dev-env pod running 24/7 in the main cluster: code-server reachable in a browser,
a persistent home, in-cluster kubectl working, and a human able to
`kubectl exec` in and run an agent CLI interactively under tmux.

## Scope

- New domain `kubernetes/main/apps/dev/` (namespace `dev`), wired into the
  top-level `kubernetes/main/apps/kustomization.yaml`.
- `dev-env/ks.yaml` + `app/` in the standard app shape (see saga README layout).
- **HelmRelease** (bjw-s app-template):
  - `type: deployment`, `strategy: Recreate` (RWO PVC), replicas 1.
  - Main container: code-server bound to 0.0.0.0:8443 with `--auth none`
    (Authentik is the auth layer), plus a long-lived tmux server.
  - `HOME=/home/dev`, `CLAUDE_CONFIG_DIR=/home/dev/.claude`,
    `CODEX_HOME=/home/dev/.codex`.
  - Persistence: `home` PVC (Ceph block, size per Decision log #7) at `/home/dev`;
    emptyDir `/tmp`. `readOnlyRootFilesystem: true` should still be achievable since
    HOME is a PVC — verify code-server tolerates it.
  - Resources: generous requests (agents compile things); no CPU limit, memory limit
    ~8Gi to start.
- **Ingress**: Traefik IngressRoute + the existing Authentik middleware pattern
  (crib from `frontend/haynesnetwork`), GATUS_SUBDOMAIN wiring optional.
- **RBAC**: start with a **read-only** binding (reuse the `upgrade-health-gate`
  ClusterRole pattern); upgrading power is backlog 05 — do not block the PoC on the
  RBAC debate.
- **CiliumNetworkPolicy**: default-deny + enumerated egress. Broader than the
  shepherd's by necessity: DNS, apiserver, github, anthropic (api + claude.ai for
  /remote-control + statsig/sentry endpoints Claude Code uses), openai/chatgpt.com,
  registry.npmjs.org, pypi.org/files.pythonhosted.org, ghcr.io, proxy.golang.org,
  release hosts for tool updates. Keep it enumerated — this list IS the exfil
  boundary given yolo agents (saga Hard news #1). Expect iteration; watch Hubble
  drops during bring-up.
- **ExternalSecret**: placeholder for now (03/04 fill it in).
- PVC prune protection: `kustomize.toolkit.fluxcd.io/prune: disabled` on the home
  PVC per `.agents/rules/flux-pvc-prune-safety.md`.

## Acceptance

- Pod Ready; code-server loads through Authentik from LAN.
- `kubectl exec -it deploy/dev-env -n dev -- tmux new -s test` works.
- In-pod `kubectl get nodes` succeeds via the SA (read-only).
- `git clone https://github.com/thaynes43/haynes-ops` succeeds in-pod.
- Flux ks Ready; kubeconform green; PVC survives a pod delete.
