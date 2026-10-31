# Proposal: a second, read-only Omni service account (`dev-env-omni-reader`)

Written 2026-10-31 by session `haynes-ops-1031-073433` under work order
OVERNIGHT-OMNIREADER-282. Tracked as
[OP#282](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/282)
(Tom's requirement, journal 3803).

**This is a proposal only.** Nothing was provisioned or changed. No service account, key,
1Password field, ExternalSecret, profile, RBAC rule, network policy, Omni write or Talos write
was made. This document activates nothing. Tom rules on it first, and the implementation is a
separate approved story. `dev-env-node-triage` stays Admin by design (Tom, 2026-10-31), and
nothing here changes it.

Sources:

- haynes-ops at `a5e12db9`.
- Read-only `omnictl` commands run on 2026-10-31 with this session's key. The client is
  v1.12.4 and the backend is v1.13.0.
- Omni source at tag `v1.13.0`.
- Talos source at `v1.13.3`, the cluster's version.
- The Omni and Talos docs.

No Secret or 1Password value was read. This document gives account names and roles, never
keys.

## Requirement

Tom, 2026-10-31 03:28 (relayed by Meatball): "And we need a second service account that is
Read only which doesn't require a grant to access."

The goal is routine, authorized inspection of Omni and Talos state from a session without a
per-task grant. "No grant" means ordinary reads. It does not mean relaxing any network or
security control.

## Inventory (read-only, 2026-10-31)

### Omni identities

From `omnictl serviceaccount list`, `omnictl get identities` and `omnictl get users`:

| Identity | Kind | Role | Key expires |
|---|---|---|---|
| `dev-env-node-triage` | service account | Admin | 2027-07-25 |
| `dev-env-omni-operator` | service account | Operator | 2027-08-20 |
| `haynes-m5max-ktlo` | service account | Admin | 2027-10-10 |
| Tom's login | user | Admin | n/a |

- **No Reader account exists.** `omni-service-account.md` says to create a Reader named
  `haynes-ops-agent`, but nobody did. The "Omni Reader kubeconfig" in `upgrade-shepherd.md`,
  `upgrade-health-gate.md` and `docs/renovate/` therefore does not exist. Those automations
  read Kubernetes through the in-cluster `upgrade-health-gate` ClusterRole.
- **No ACL is defined.** `omnictl get accesspolicies` returns nothing, so no cluster has its
  own role for any identity.
- **`haynes-m5max-ktlo` is not referenced in git.** This proposal does not touch it.

### Consumers of Omni keys in git

| Key | 1Password item / field | Secret (namespace), defined in | Consumer |
|---|---|---|---|
| `dev-env-node-triage` (Admin) | `dev-env` / `OMNI_SERVICE_ACCOUNT_KEY` | `dev-env-node-triage-secret` (`dev-agents`), `apps/dev-env-system/secrets/app/externalsecret-session.yaml` | v2 profiles `full` and `workbench`, env `OMNI_SERVICE_ACCOUNT_KEY` (`apps/dev-env-system/templates/app/templates.yaml`) |
| the same key | the same field | `dev-env-node-triage-secret` (`dev`), `apps/dev/dev-env/app/externalsecret.yaml` | the v1 pod's app container (`apps/dev/dev-env/app/helmrelease.yaml`) |
| `dev-env-omni-operator` (Operator) | `dev-env` / `OMNI_OPERATOR_KEY` | `dev-env-omni-operator-secret` (`dev`), same file | No deployment mounts it. The one-shot sync Job in `egpu-test-node.md` §2 opts in. |
| Tom's login | n/a | n/a | the workstation `OMNICONFIG` (`.envrc`) and `task omni:sync` |

These profiles carry no Omni key today: `dev`, `ops`, `rule-canary-claude` and
`rule-canary-codex`. No session mounts the Operator key.

### Who reads Omni today, and for what

- **Sessions on `full` and `workbench`, and the v1 pod, use it for node triage.** They run
  `omnictl get machines|machinestatus|clustermachinestatus`, `omnictl machine-logs` and
  `omnictl cluster template validate|diff`. Through `omnictl talosconfig` they also run
  `talosctl` commands: `dmesg`, `logs`, `services`, `get <resource>` and `read <file>`. The
  runbooks are `egpu-test-node`, `plex-igpu-transcode`, `talos-version-upgrade` and
  `reference/talos-omni-gotchas`. Today all of these reads run as Admin, the same identity
  that can write.
- **Omni writes** (template sync, config patches, upgrades) come from three places: Tom's
  login, the Operator-key Job, or the Admin key inside an authorized story.
- **In-cluster automation** (alert-responder, upgrade-shepherd, the upgrade gate) reads
  Kubernetes through the `upgrade-health-gate` ClusterRole. It holds no Omni key.

## What Omni's Reader role can and cannot do

This section comes from the Omni v1.13.0 and Talos v1.13.3 source, plus the docs. Check it
again when the Omni backend moves to a new minor version.

**Omni API.** In `internal/backend/runtime/omni/state_access.go`, reading (Get, List, Watch)
cluster, machine and config-patch resources needs Reader. Create, Update and Destroy need
Operator. Users, identities, service-account status, access policies and the backup-store
config are Admin-only, even to read. Only Admin or the Auditor role can read the audit log.

**Talos API through Omni.** In `internal/backend/grpc/router/talos_backend.go`, a caller below
Operator gets `hasModifyAccess = false`. Omni then forwards every Talos call from that caller
with the Talos role `os:reader` only. Talos applies its own RBAC table
(`internal/app/machined/pkg/system/services/machined.go`):

| `os:reader` may call | `os:reader` may not call |
|---|---|
| `dmesg`, `logs`, `services`, `version`, `get` of non-sensitive resources, `list`, `disks`, `mounts`, `processes`, `netstat`, `memory`, `stats`, `containers`, `etcd status`, `etcd members` | `reboot`, `shutdown`, `upgrade`, `reset`, `apply-config`, `rollback`, `bootstrap`, `service start/stop/restart`, `pcap`, resource create/update/destroy, `read` and `cp` (file contents), `kubeconfig` |

A write is therefore refused by two separate server-side layers: Omni's resource filter and
Talos RBAC.

**Kubernetes.** Omni puts Operator and higher in `system:masters`. A Reader gets no Kubernetes
group unless an ACL adds one. A Reader's Omni kubeconfig is therefore no use for `kubectl`, and
this proposal adds none. Sessions already read Kubernetes through their in-cluster
ServiceAccount.

**What a Reader key still exposes.** The key is read-only, but it can still reach secrets:

- **Config patches, including `spec.data`.** The machine-level `nut-client` patches on the
  masters contain the NUT `upsmon` password (`reference/talos-omni-gotchas.md`). Anyone using
  the Reader key keeps the existing rule: never print a patch's `spec.data`.
- **Join-token resources** (`canreadjointokens: true`). Assume a Reader can see the token.
- **Machine configs** are redacted for Reader (`RedactedClusterMachineConfig`).

So handle the Reader key exactly like the other keys in `dev-env`.

**Reads a Reader cannot do.** On Talos 1.12 and later, `talosctl read` and `cp` need
`os:admin`, and Omni forwards them with `os:admin` only for Operator and higher. Several
runbook reads use them: `/proc/cmdline`, `/etc/kubernetes/kubelet.yaml` and PCI sysfs files
(in `egpu-test-node`, `plex-igpu-transcode` and `talos-omni-gotchas`). Those reads stay on the
Admin account, in a session that holds it.

## Proposal

### Account

- **Name and role:** `dev-env-omni-reader`, role `Reader`, no ACL. The name follows
  `dev-env-omni-operator`.
- **Creation flags:** `--use-user-role=false --role Reader`. Without them, omnictl gives the
  new account its creator's role, and Tom's role is Admin.
- **TTL:** `8760h`. This is the Omni default and matches the three existing keys.
  - Renew at least 30 days before expiry with `omnictl serviceaccount renew
    dev-env-omni-reader`. Renewal keeps the name and role and registers a new key. I did not
    check whether the previous key stays valid after a renewal.
  - On a suspected leak, destroy the account (see Rollback).
  - Alternative TTL: `2160h` (90 days). That costs four 1Password updates a year.
    Recommendation: `8760h`.
- **Expiry tracking:** record the expiry date on the implementation story. This proposal adds
  no new expiry alert.

### Consumers

| Consumer | Gets | Why |
|---|---|---|
| v2 profile `dev` | the Reader key as `OMNI_SERVICE_ACCOUNT_KEY`, plus `OMNI_ENDPOINT` | It has no Omni access today. Plain `omnictl` and `talosctl` then run as the Reader with no wrapper. Its web egress tier already reaches the Omni SaaS endpoint on port 443. |
| v2 profiles `full` and `workbench` | **Admin unchanged** as `OMNI_SERVICE_ACCOUNT_KEY`. Recommended (Tom's choice): the Reader key added as `OMNI_READER_KEY`. | Routine reads can then run as the Reader: `OMNI_SERVICE_ACCOUNT_KEY="$OMNI_READER_KEY" omnictl …`. `talosctl` reads the same variable. The audit log then separates routine inspection from Admin work. Nothing about Admin changes. |
| v2 profile `ops` | nothing for now | Its egress tier has no web access, so it cannot reach Omni. Reaching it would take a network-policy change, which needs its own approval. |
| `rule-canary-*` | nothing | These profiles carry as few credentials as possible by design. |
| v1 pod (namespace `dev`) | nothing; it keeps Admin | v1 is being retired, and changing its wiring restarts the pod (a runtime hold). |
| In-cluster automation | nothing | It reads Kubernetes through its ClusterRole. A Reader kubeconfig grants no Kubernetes access. |

### Secret distribution (the existing route only)

The key uses the same route as every other session credential. There is no new store,
controller, mount type or namespace.

1. **1Password:** item `dev-env`, which already holds `OMNI_SERVICE_ACCOUNT_KEY` and
   `OMNI_OPERATOR_KEY`. New field: `OMNI_READER_KEY`.
2. **ExternalSecret:** a new `dev-env-omni-reader` in `dev-agents`, added to
   `kubernetes/main/apps/dev-env-system/secrets/app/externalsecret-session.yaml`.
   - Store: `ClusterSecretStore/onepassword-connect`, with `refreshInterval: 1h`.
   - Target: Secret `dev-env-omni-reader-secret`, `creationPolicy: Owner`, with one key,
     `OMNI_READER_KEY`.
   - It is its own ExternalSecret, following the isolation rule at the top of that file.
3. **Profile wiring in `templates.yaml`:** a `secretKeyRef` env entry with `optional: true` in
   `dev`, and in `full` and `workbench` if Tom chooses them.
   - Do not use `envFrom`, and do not add the key to `dev-env-mcp-secret`, which other
     profiles share.
   - `optional: true`, as `UNIFI_MCP_AUTH_TOKEN` uses, means a missing Secret cannot stop a
     session pod from starting.
4. **Reach:** session pods run as `dev-agents/dev-env-agent`, which cannot read Secrets. So
   the key reaches only the pods whose profile names it. A `workbench` session can exec into
   any session pod and read its env, as `templates.yaml` already notes.

**Ordering:** create the 1Password field before the ExternalSecret merges. `dev-env-secrets`
runs with `wait: true` and `timeout: 5m`, so a missing field fails that Kustomization and pages.

### Audit

- **What the Omni audit log records:**
  - on every event: the actor (`dev-env-omni-reader@serviceaccount.omni.sidero.dev`), its
    role, the user agent and the client address;
  - every Talos API call, with the method name and machine IP;
  - every resource create, update and destroy.

  I confirmed this on 2026-10-31 for `dev-env-node-triage`, looking at field names and event
  counts only.
- **Telling the keys apart:** all session pods leave the cluster through the same address. The
  actor field, not the client address, separates the Reader from the Admin key.
- **Who can read it:** only Admin (or an Auditor). The Reader cannot read its own trail.
  - Tom's review query: `omnictl audit-log --since 168h --actor
    dev-env-omni-reader@serviceaccount.omni.sidero.dev`.
  - A quick liveness check: the LAST ACTIVE column of `omnictl serviceaccount list`.
- **Caveats:**
  - The `denied` flag on a `talos_access` event marks only Omni's own refusal. Omni forwards a
    Reader's call as `os:reader`, and if Talos then refuses it, the event does not say denied.
  - I did not check how long the SaaS tier keeps the audit log.

### Negative-write acceptance without attempting a mutation

The session rules forbid testing the role by trying a write. Acceptance therefore rests on what
the server reports about the account. These are the options for Tom. I have run none of them.

**A. Server-reported role and capabilities (recommended).** These are all reads. The
implementation story runs them from a `dev` session, which holds only the Reader key.

1. `omnictl get currentuser -o yaml` shows `role: Reader`.
2. `omnictl get permissions -o yaml` shows every read flag true, and every manage, create,
   remove and access flag false. The false flags are `cancreateclusters`,
   `canremovemachines`, `canmanagemachineconfigpatches`, `canaccessmaintenancenodes`,
   `canmanageinstallationmedia`, `canmanageusers`, `canmanagebackupstore`,
   `canmanagejointokens`, `canaccessinternals` and `canreadauditlog`. Omni computes these flags
   from the caller's role (`internal/backend/runtime/omni/virtual/state.go`). Today the Admin
   key shows all of them true.
3. `omnictl get clusterpermissions haynes-ops -o yaml` shows exactly six flags true:
   `candownloadkubeconfig`, `candownloadtalosconfig`, `canreadconfigpatches`,
   `canreadmachineconfig`, `canreadkernelargs` and `canreadmachinependingupdates`. The other
   twelve are false: `canrebootmachines`, `canaddmachines`, `canremovemachines`,
   `canupdatekubernetes`, `canupdatetalos`, `canmanageconfigpatches`,
   `cansynckubernetesmanifests`, `canreadkubernetesmanifests`, `canmanageclusterfeatures`,
   `candownloadsupportbundle`, `canmanagemachineconfig` and `canmanagekernelargs`.
4. An Admin read-back, by Tom or a session that holds the Admin key:
   - `omnictl serviceaccount list` shows `dev-env-omni-reader` with role `Reader`;
   - `omnictl get accesspolicies` is still empty.

   An ACL can raise an account's role on one cluster, so this step is part of the acceptance.
5. **Optional, only if Tom allows a refused read:** `talosctl -n <node> read /proc/version`
   returns PermissionDenied.
   - This is a read, not a write.
   - Omni forwards `Read` with `os:admin` to Operator-or-higher callers. A refusal therefore
     shows that this caller's Talos path carries `os:reader`, which has no reboot, shutdown,
     apply or upgrade method.
   - The positive check: `talosctl -n <node> dmesg | tail -3` works.

The basis for A is the Omni v1.13.0 and Talos v1.13.3 source cited above. Check it again when
Omni moves to a new minor version.

**B. Audit review of real traffic.** About a week after activation, Tom runs the audit query
above. He checks that there are no create, update or destroy events and that every Talos method
is one `os:reader` may call. This shows what happened, not what is impossible. Pair it with A.

**C. A controlled denial run by Tom.** This is a write attempt, so only Tom runs it, or a story
he explicitly approves for it.

- With only the Reader key in his shell, he runs `omnictl delete
  configpatches.omni.sidero.dev <an ID that does not exist>`.
- In the destroy validation (`state_access.go`, `WithDestroyValidations`), Omni runs the role
  check even when the resource is missing. So a Reader gets PermissionDenied.
- If the role were somehow wrong, the result would be NotFound, and nothing would change.

This puts a refusal on record at the cost of one deliberate attempt.

**D. Documentation only.** Accept on the Omni docs ("can view clusters, machines, and other
resources, but cannot modify them") with no check. This is the weakest option.

**Recommendation:** run A at activation and B one week later. Use C only if Tom wants a refusal
on record.

### Rollback

- **Before any git change** (the account exists and the 1Password field is written): Tom runs
  `omnictl serviceaccount destroy dev-env-omni-reader` and deletes the `OMNI_READER_KEY` field.
- **After activation:**
  1. Revert the implementation PR. New session pods stop getting the key. Flux prunes the
     ExternalSecret (`prune: true`), and its Secret goes with it (`creationPolicy: Owner`).
  2. Tom runs `omnictl serviceaccount destroy dev-env-omni-reader`. This invalidates the key
     at once, including in running pods that still hold it in their env. The Admin and Operator
     accounts and their consumers are not touched.
  3. Delete the 1Password field only after the ExternalSecret is gone. Deleting it first
     breaks the sync and fails `dev-env-secrets`.
- **On a suspected leak:** do step 2 first, then steps 1 and 3.

### Tom's steps and the later story's steps

**Tom (as Admin, from his workstation):**

1. Rule on this proposal on OP#282. The choices are the account, the consumer list (including
   whether `full` and `workbench` also get the key), the TTL and the acceptance option.
2. Run `omnictl serviceaccount create --use-user-role=false --role Reader --ttl 8760h
   dev-env-omni-reader`. It prints `OMNI_ENDPOINT` and the key.
3. Store the key in 1Password item `dev-env`, field `OMNI_READER_KEY`, and nowhere else.
4. Confirm that `omnictl serviceaccount list` shows `dev-env-omni-reader` with role `Reader`.
   Note on the story that the field exists, without its value.

Steps 2 to 4 are Tom's. A session's Admin key could technically create an account, but no
session is authorized to create credentials. Doing it from a session would also pass the new
key through that session.

**A later approved story (git, through Flux):**

1. Add the ExternalSecret described above once Tom confirms the field exists. Then check that
   `kubectl get externalsecret -n dev-agents dev-env-omni-reader` reports `SecretSynced`.
2. Wire `templates.yaml` as Tom chose, with `optional: true`.
   - A templates change restarts no running session. New sessions get it at once, and the
     operator drains idle, unattached sessions onto the new revision.
   - The story must respect any template hold in force at the time. OP#282 already mentions
     template holds.
3. Update the "Credentials in this pod" row in
   `apps/dev-env-system/session-config/app/config/claude/CLAUDE.md`, which restarts nothing.
   The new row says:
   - on `dev`, sessions run as the Reader;
   - on `full` and `workbench`, routine reads use `OMNI_READER_KEY`, and the Admin key stays
     for authorized writes.
4. Run acceptance A from a fresh `dev` session, and record the output on the story with no key
   material.
5. Correct the outdated "Reader" wording listed below, keeping clear of the v1 restart path.

The ExternalSecret and the wiring can go in one PR, because of `optional: true`, or in two.

### Outdated "Reader" wording found during the inventory (not changed here)

These files still call the Admin account a Reader, or read-only:

- `kubernetes/main/apps/dev-env-system/templates/app/templates.yaml`: the `workbench`
  comments, for example "Omni Reader service account, read-only".
- `kubernetes/main/apps/dev-env-system/secrets/app/externalsecret-session.yaml`: "Full's Omni
  Reader identity", and the header's "full also retains v1's Omni Reader".
- `kubernetes/main/apps/dev/dev-env/app/helmrelease.yaml`: the v1 comment "Read-only BY ROLE
  (Omni Reader SA)".
- The `omnictl` row in v1's mounted `resources/config/claude/CLAUDE.md`. Editing it restarts
  the v1 pod, so it is held.
- `kubernetes/main/apps/dev/dev-env/app/externalsecret.yaml`: "Default omnictl access stays
  the READER SA".
- `.agents/runbooks/egpu-test-node.md` §2: "read-only Reader SA" and "its Omni SA is Reader".
- `.agents/reference/talos-omni-gotchas.md`: "dev-env's Omni account is a Reader".

Separately, these docs describe a Reader kubeconfig (`haynes-ops-agent`) that does not exist:
`.agents/runbooks/omni-service-account.md`, `upgrade-shepherd.md`, `upgrade-health-gate.md` and
`docs/renovate/`. Even if it existed, a Reader kubeconfig would not allow `kubectl get` without
an ACL.

Fix these in the implementation story or in a small docs story. Hold the file mounted into the
v1 pod. Because each of these asserts a safety property the cluster lacks, it also belongs in
`.agents/HARDENING.md`.
