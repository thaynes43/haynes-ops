# HELD option: one-shot MAM cookie apply to Prowlarr indexer 17 (OpenProject #319)

**HELD DRAFT. Do not merge.** This is one held operational option inside the homelab operator access bundle (dev-env, Tom's correction 2026-10-31: "agents will need access to everything it's a home lab"). It is not a permanent service exception.

What it does: once, in `downloads`, a Job runs as its own ServiceAccount `prowlarr-mam-cookie-apply`. That account has `automountServiceAccountToken: false` and no RBAC.
- It reads two existing keys as env: `myanonamouse-secret/MAM_ID_PROWLARR` and `prowlarr-secret/PROWLARR_API_KEY`.
- It sends GET to `http://prowlarr.downloads.svc.cluster.local:9696/api/v1/indexer/17`.
- It refuses unless the indexer is id 17, implementation `MyAnonamouse`, with exactly one `mamId` field.
- It replaces only that value and PUTs the same resource back, so every other field and the enabled state are kept. Prowlarr tests the indexer on save.
- It prints only HTTP status codes and fixed messages.

Limits:
- Job: `backoffLimit: 0`, `activeDeadlineSeconds: 180`; non-root, read-only root filesystem, no capabilities, seccomp RuntimeDefault.
- Egress (CiliumNetworkPolicy): only Prowlarr's pods on TCP 9696 and kube-dns. The Job never contacts MAM.

Authority: this is the normal reviewed GitOps route, applied by Flux. It changes no agent access, RBAC, GrantPolicy, guard or egress tier. It does read and apply a credential, so merging is executing.

Shared-secret dependency (unavoidable): the Job uses whatever the Secret holds when it starts. Tom's 1Password change reaches `myanonamouse-secret` at the next ExternalSecret refresh (hourly, or a force-sync). qbittorrent mounts `MAM_ID_SEEDBOX` from the same Secret and has `reloader.stakater.com/auto: "true"`, so that refresh restarts the qbittorrent pod.

Order:
1. The ExternalSecret refreshes (qbittorrent restarts).
2. Merge.
3. Check the Job is `Complete` and logs `HTTP 202`. Check the governor's next 15-minute tick has no `actuationError` and the alert clears.
4. A cleanup PR removes the four objects, and Flux prunes them. There is no TTL, because Flux would re-create a TTL-deleted Job.

Tests: the kustomize render is clean. An offline fake-Prowlarr test passed: only `mamId` changes; HTTP 400 exits 1; a wrong implementation or a duplicate `mamId` makes no PUT; empty inputs are refused; no secret is printed.
