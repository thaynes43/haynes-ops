# Cluster inspection

> Migrated and adapted from the old Cursor `mcp-kubernetes-access` rule. That rule assumed the editor could **not** reach the cluster and routed everything through a Kubernetes MCP server with schema files under `~/.cursor/...`. In Claude Code that premise no longer holds — `kubectl`/`flux`/`talosctl`/`omnictl` work directly — so the schema-first MCP dance is unnecessary. The durable guidance below is what still matters.

## How to inspect

- Use `kubectl` directly for read-only inspection: `get`, `describe`, `logs`, `events`, `-o jsonpath`/`-o yaml`, label/field selectors.
- Use `flux get kustomizations -A` / `flux get helmreleases -A` for reconcile state, and `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph -s` for Ceph.
- Machine config that `talosctl get mc` is denied for (Omni gates it) is readable via `omnictl get clustermachineconfig|configpatch|operatorconfig ...`.

## Safety guardrails (cluster data)

- **Never dump Secrets** (`kind: Secret`, or `-o yaml` on objects with inlined secret data) unless the user explicitly asks and understands the exposure. Secret values may be cached/logged.
- **Prefer narrow queries**: namespace-scoped lists with label/field selectors and `jsonpath` projections keep output small and avoid leaking unrelated data.
- Read-only by default. Mutations (cordon, rollout restart, reconcile, delete-pod) are fine when they serve verification/remediation, but persistent changes go through Git (see [repo-overview.md](repo-overview.md)).
