# 15: dev-env v2 break-glass discovery catalog

**Status:** implementation prepared; human approval is blocked on dev-env
DESIGN-001 Q-16's authority-boundary ruling. No deployment has been made.
Live catalog checks and the break-glass half of S-12 remain required after that ruling.
**Authority:** thaynes43/dev-env DESIGN-001 6.12, D-27; plan 07 H1. This is the
GitOps companion, not a new access decision or a change to the baseline roles.

## Contract

`dev-env-grant-breakglass` contains explicit discovered resources and verbs. Its
generator reads every served API version and merges the same group/resource across
versions. It excludes Secrets and their subresources, ServiceAccount tokens, the
node proxy, ephemeral containers, bind/escalate/impersonate, RBAC, CSR approval,
admission controls, Kyverno, CRDs, APIServices, Flux, External Secrets, cluster-wide
Cilium policies, and dev-env CRDs. It grants no wildcard or non-resource permission.
It additionally excludes pod port-forward and pod/Service proxy endpoints: they
bypass network/ingress isolation and lack the privileged-target admission lookup
that covers both pod exec and attach. Those paths fail closed until a guard is
deliberately designed and verified.
The existing identity and exec guards still apply to every `grant-*` identity;
they keep dev-env and controller namespaces protected and refuse privilege or new
Secret references in workloads. This change modifies neither those guards nor the
baseline roles, and binds the new role to no identity. Their present coverage does
not establish an owner authentication boundary; the verified gap below blocks human
approval enablement.

The checked-in `rbac/app/catalog/discovery.json` holds API metadata only: group,
resource/subresource, served versions, kind, namespace scope and advertised verbs.
It contains no object instances, names, credentials, cluster addresses or timestamps.
`generate.py` uses Python's standard library for capture, generation and live checks.

## CI and live drift

Hosted PR CI regenerates the role from the snapshot, checks forbidden permissions,
and checks that Flux-managed CRDs under `kubernetes/main/apps` have their served
resources in the snapshot. Historical bootstrap CRD backups are outside that scan.
It does not receive cluster credentials. PyYAML is used only for the optional
repo-CRD check; the generator and in-cluster checker have no third-party dependency.

The CPU-limited `dev-env-grant-discovery` CronJob compares the snapshot with live
discovery every 30 minutes. Its explicit RBAC permits only GET on `/api`, `/api/*`,
`/apis` and `/apis/*`, with no operational resource rights or GitHub credential.
Any API group, served version, resource or union of advertised verbs drift makes it fail;
the existing `KubeJobFailed` alert records the failure. New permissions do not
enter the static role automatically. Public CI cannot independently discover a
private API, so this live checker is the coverage for Helm-installed and other
runtime-only API additions.

When the checker fails, read its bounded group/resource-only diff. In a fresh
haynes-ops worktree, refresh metadata and inspect the role diff:

```bash
catalog=kubernetes/main/apps/dev-env-system/rbac/app/catalog/generate.py
nice -n 19 python3 "$catalog" capture
nice -n 19 python3 "$catalog" render
nice -n 19 python3 "$catalog" check --crd-root kubernetes/main/apps
nice -n 19 python3 "$catalog" check-live
```

Commit the snapshot and role together through a reviewed PR. Flux updates the role
and checker snapshot; verify the new live check passes. Do not use `kubectl apply`
or have the checker repair RBAC itself. Any newly discovered operator resource has
D-27's accepted residual risk: it can cause another controller to expose an existing
Secret. Review those permissions explicitly before merging a refresh.

## Before human break-glass is enabled

1. Verify the deployed role equals generation and has no excluded permission.
2. Run S-12's break-glass half under an isolated `grant-*` identity: allowed scratch
   workload operations, and refusals for Secret read, TokenRequest, RBAC, protected
   namespaces, Flux/ExternalSecret creation, CRD deletion, new Secret references and
   privileged/non-default-SA workloads and exec. Use server-side dry runs for writes.
3. Verify the owner-only, fresh-login human approval path separately. No standing
   policy or automatic binding is added here.

## Verified approval-boundary blocker

Read-only live Pod metadata on 2026-10-08 confirmed Authentik server and managed proxy
outposts run as `network/default`, its worker as `network/authentik`, and the
`postgres16` CNPG pods as `database/postgres16`. The current
[`dev-env-exec-guard`](../../../../kubernetes/main/apps/kyverno/policies/app/dev-env-exec-guard.yaml)
does not protect those identities or the stable labels `app.kubernetes.io/name=authentik`,
`app.kubernetes.io/name=authentik-outpost-proxy` and `cnpg.io/cluster=postgres16`.
Traefik's `network/traefik-external` and `network/traefik-internal` identities are
already on its privileged list. The identity guard's controller namespace list does
not include `network` or `database`.

The Authentik [HelmRelease](../../../../kubernetes/main/apps/network/authentik/app/helmrelease.yaml)
mounts its blueprint ConfigMap and application Secret; its
[ExternalSecret](../../../../kubernetes/main/apps/network/authentik/app/externalsecret.yaml)
configures `postgres16-rw.database.svc.cluster.local` as its database. Exec into these
pods can change login/session authority. A targeted exec rule alone also leaves
default-SA workload changes that keep existing Secret references, Authentik blueprint
ConfigMaps, services/endpoints/routes and shared auth-state volume access or operator
CR mutations. The shared database's break-glass remediation capability is part of
the design tradeoff. The owner must choose a complete enforcing boundary in
dev-env DESIGN-001 Q-16 before a
human approval ingress or break-glass policy is enabled. No partial guard change is
shipped with this catalog.

No CPU burners, stress tools or broad parallel test runs on shared nodes. The
discovery Job has a CPU limit and runs on worker nodes. A live acceptance Job also
needs a CPU limit; do not reproduce a timing failure under load.
