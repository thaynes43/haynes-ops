# Story178 current-runtime launch checklist

This is a source packet only. It remains outside every Flux Kustomization and
does not install a Deployment, Secret, route, policy, admission object, or
credential. “Ready” below means only the cited source or live-read fact; it is
not whole-Story178 acceptance.

## Launch-gate matrix

| Gate | Exact evidence / action | State at 2026-10-25 | Accountable next actor | Scope |
| --- | --- | --- | --- | --- |
| Immutable portal artifact | dev-env `af402e533379bddd81e9266ee80ec6c565b81f29`; Publish `40368529556` and post-merge CI `40368529884` terminal SUCCESS; portal `sha-af402e5@sha256:71d3c5d64d1948a78acdc695337fb8edac542ec46eb81fcddaddafec30df0772` | Source pin updated and focused-tested; not installed | Root delivery owner after review/CI | This source PR only |
| Current parser contract | OpenProject `https://openproject-web.frontend.svc.cluster.local:8443`; project-3 query `/api/v3/projects/3/work_packages?offset=<page>&pageSize=50&filters=%5B%5D`; Sessions and Tasks independent | Source aligned and focused-tested | Root verifies exact-head review | This source PR only |
| Packet registration | `dev-agents/dev-env-v2-portal` Deployment was API NotFound at 15:57:20Z; packet resources remain unregistered | Not installed; expected | Root, only after all prerequisites | Held activation |
| OpenProject PKI release | `openproject-portal-ca`, `openproject-portal-api`, and `openproject-portal-pki` Ready; OpenProject HelmRelease Ready at 15:58:13Z | Issuance/release-ready only | Certificate/portal owner verifies propagation | Held trust/activation |
| Portal serving TLS and peer CAs | Deliver serving TLS and public PostgreSQL, admission, issuer, OpenProject, and operator CA provenance; verify hashes, validity, and reload | Unknown; no contents read | Authorized certificate owner | Held provisioning/security |
| Runtime fields | `DB_USERNAME`, `DB_PASSWORD`, `SESSION_AEAD_KEY`, `SESSION_GENERATION` | HELD: no population or install | Authorized provisioning owner | Held credential/provisioning |
| OIDC projection | `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` are USER-REPORTED SAVED | Not SecretSynced and not value-verified | Authorized owner verifies after reviewed consumer exists | Held provisioning; no duplicate request |
| Subject and admission | `config/subject` and admission ConfigMap UID must be recorded; current config keeps both absent | Unknown / absent | Authorized identity and operator route | Held identity/admission |
| Database effective rights | Reviewed SQL/HBA/role path and effective-rights evidence | Unknown; reject posture remains | Authorized database owner | Held provisioning/security |
| Isolation | Dedicated policy default deny plus CCNP exclusions and endpoint/DNS behavior must be observed | Source staged only; not effective | Root with authorized runtime owner | Held security/activation |
| Operator reader | Story178 S3 source: operator HelmRelease pins `sha-af402e5@sha256:1377ef1ee051543eeef19e4cd7e284488333a557681bae07223f64d9539c848a` with explicit `--enable-portal-reader` and `--session-namespace=dev-agents`, after the S2 AgentSession CRD sync. Before S3 the live operator was `sha-617ed15@sha256:888c2a0cd1057091cd2bbac7ac743933a4819e100c4570b6eb9b7579afddd34b`, 2/2 ready at 16:14:44Z, with no reader args | Source only; the reader is live only after Flux rolls both replicas to the new image | Coordinator merges S2, then S3, and verifies the rollout | Operator image and two args only; keeper, broker, task worker and templates pins unchanged |
| OpenProject reader token | `OPENPROJECT_TOKEN` is a separate optional approved project-reader item property; `OPERATOR_TOKEN` is projected ServiceAccount audience `dev-env-operator` | No token read, minted, or synced; missing token means the matching product view is not connected | Authorized owner after reader prerequisites | Held credential/access |
| Gate A | Exact artifact: OIDC/state/nonce/issuer/audience, active subject/admission, two-principal denial, revocation/logout, DB failure, CSRF, hostile output, secret canaries, TLS/backend-bypass negatives, and no public route | Unresolved | Root acceptance owner | Held acceptance |

## Field handoff boundaries

The canonical existing item is `HaynesKube/dev-env`; no new item and no
`dataFrom` are authorized. `OIDC_CLIENT_ID` is public
`dev-env-v2-portal-internal`; `OIDC_CLIENT_SECRET` is the installed provider
secret. Both remain USER-REPORTED SAVED, not SecretSynced or value-verified.

The four runtime fields remain HELD. This packet generates no value, reads no
secret, and authorizes no writer. `config/subject` is neither an item property
nor a public value. No private identity subject or UUID values, credentials or
private keys, or personal data belongs in this repository. This preserves the
DEPLOYMENT-CONTRACT's verified non-secret resource UID/admissionUID pin and its
public-only CA certificate/provenance route: a public CA certificate is not a
private key, and a resource UID is not a personal user UUID. This packet does
not populate or export any value.

Historical records for the prior `5b39ebb` image, PR545 parser assumptions,
and their failed/denied receipts remain historical only. They do not change the
current artifact, gates, or ownership.

## Next supported route

Root obtains independent exact-head review and required hosted CI for this
source PR. If accepted, it may merge this unregistered packet. A later bounded,
specifically authorized provisioning/activation change must close every held
gate before registering any portal resource; this checklist neither authorizes
nor requests those actions.

## Nonsecret readiness correction, 2026-10-26

The dated 2026-10-25 matrix above is historical installation evidence, not a current absence claim. Current own-identity named metadata reads confirm:

- OIDC ExternalSecret `dev-agents/dev-env-v2-portal-oidc` Ready=True, selecting only `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`. This is sync metadata, not value inspection or login acceptance. Do not request OIDC entry again.
- Witness `dev-agents/dev-env-v2-portal-admission` UID `8d463394-7b50-4c12-9bfb-0c2eb6f5054d`, schema1, state closed. Preserve it; do not recreate or open it. A resource UID is not the private identity subject.
- OpenProject `openproject-portal-ca` and `openproject-portal-api` Certificates Ready=True. Issuance is not BFF trust/reload proof.
- Runtime and OpenProject credential ExternalSecrets, database runtime projection, portal config, portal serving Certificates and portal Deployment are NotFound. Their absence does NOT establish absent saved vault fields; secure input presence/current validity remain Unknown and uninspected.

Compatible operator source candidate: `ghcr.io/thaynes43/dev-env-operator:sha-af402e5@sha256:1377ef1ee051543eeef19e4cd7e284488333a557681bae07223f64d9539c848a`. This source-only packet does not change the registered operator image, flags, login image, policy, registration, admission or resource limits; the separate Story178 S3 PR moves the operator image and adds the reader flags. The ordinary policy-before-workload ordering and all existing runtime holds stay unchanged. The public PostgreSQL `ca.crt` export remains pending its specific disposition/provenance; the denied Secret read is not retried.

## DB-only prerequisite continuation, 2026-10-26

The owner-approved DB setup (journal1116, Sentinel_74419fc9bed881919d615a60f381d3e0) now registers only `database/dev-env-v2-portal-runtime-db` through the existing CNPG cluster Kustomization. It projects only `DB_USERNAME` and `DB_PASSWORD` from existing HaynesKube/dev-env into the reviewed basic-auth target. It does not create a new vault item, project session keys, create portal roles/databases, change HBA, install the portal, change trust or open admission. The inert packet's projection remains a reference; do not register the whole packet or create a second Flux owner for the registered precursor.

Observe only ExternalSecret Ready/error metadata, never Secret values. Missing consumers did not prove missing saved fields. Stop if a precise property is unavailable or projection/parser prerequisites mismatch; hand off only the identified field through Tom's existing secure UI. After Ready and all reviewed prerequisites, roles-only preproof and retained Database/schema precede fixed-hash010/020/030; all six HBA rejects stay throughout this DB prerequisite. The post-proof HBA allow, portal activation, OIDC and witness changes remain outside it.

## Roles-only preproof registration, 2026-10-26

After #4309, the ExternalSecret `database/dev-env-v2-portal-runtime-db` was Ready (SecretSynced). Tom confirmed that both `DB_USERNAME` and `DB_PASSWORD` are saved in HaynesKube/dev-env (#178 journal 1778).

This stage registers two things through the existing `cloudnative-pg-cluster` Kustomization:
- the two roles from `roles-stage-preproof.patch.yaml`, verbatim as `postgres16` `spec.managed.roles`. The runtime role uses `passwordSecret`; the migrator has NOLOGIN and no password.
- `pending/database.yaml`, verbatim as `portal-database.yaml`. Its prune is disabled and its reclaim policy is retain.

All six HBA rejects stay unchanged, so neither role can connect yet.

Flux Ready does not prove the roles and schema were applied. Before 010, read back metadata only:
- `Database/dev-env-v2-portal` `status.applied` is true, and `status.schemas` shows `portal` applied;
- `Cluster/postgres16` `status.managedRolesStatus` lists both roles, and `cannotReconcile` is empty;
- `peer_bootstrap.py --preflight` reports `targetRoleCount` 2 and `targetDatabaseCount` 1.

The CNPG Database controller retries after 30 seconds if it created the schema before the migrator role. Allow that one retry before treating `applied: false` as a stop.

Next, in order, through `peer_bootstrap.py` and the reviewed hashes:
1. `010-bootstrap.sql` (`9c2d4c11…c230e`) with `--apply-reviewed-sha256`;
2. `020-tables.sql` (`4408926b…ab64`) with `--migrate-reviewed-sha256`;
3. `030-verify.sql` (`b3a0b176…c572f`) with `--verify-reviewed-sha256`.

Excluded from this stage: the runtime hostssl allow, portal activation, OIDC, the witness and admission changes.

## Activation step S1: portal network policy, 2026-10-26

Tom approved starting the activation stage (#178 journal 1784). The packet's `registration/policy-ks.yaml` would have pointed Flux at this report directory and placed the Kustomization in `dev-agents`.

Instead, the policy is registered verbatim through its own Flux Kustomization:
- `dev-env-system/dev-env-v2-portal-policy`;
- path `kubernetes/main/apps/dev-env-system/portal/policy/app`;
- target `dev-agents`.

Nothing depends on it. It selects only `app.kubernetes.io/name: dev-env-v2-portal`, and no pod has that label yet. The shared session-tier exclusions for that label are already live (#4277).

Acceptance is metadata only:
- the CNP is Valid;
- the Kustomization is Ready;
- no portal pod exists.

The workload Kustomization will `dependsOn` this one.

## Activation step S7: runtime HBA allow, 2026-10-26

After the 010/020/030 proof (#178 journal 1782), `cluster16.yaml` registers the reviewed post-proof HBA list, the same as `cluster-portal.patch.yaml`: the six rejects plus `hostssl dev_env_v2_portal dev_env_v2_portal_runtime 10.42.0.0/16 scram-sha-256`. The allow sits before the two `host all` rejects. The migrator still has no allow.

CNPG asks PostgreSQL to reload the HBA rules without a restart. PostgreSQL parses the file, and a rejected `pg_hba.conf` silently keeps the old rules while `pg_conf_load_time()` still advances. Each instance reloads on its own.

Acceptance runs a read-only peer query on **all three** `postgres16` instances. On each instance, together:
- `pg_hba_file_rules` shows no errors, and the allow comes before the `host all` rejects;
- `pg_conf_load_time()` is later than the reconcile.

Neither result alone proves the new rules are active. A replica that kept the old rules would refuse the runtime role after a failover.

## Activation steps S4+S6: portal inputs, 2026-10-26

Owner direction, relayed by the coordinator on 2026-10-26: take the fastest path to a working demo without security edge cases. Tom dropped the admission witness and recovery fence; a separate change removes them from the portal source. So this step registers no admission input, and it adds the Postgres CA now instead of waiting for S5.

A new Flux Kustomization `dev-env-system/dev-env-v2-portal` registers the portal's inputs:
- path `kubernetes/main/apps/dev-env-system/portal/app`;
- `dependsOn` the S1 policy, `cert-manager` and `external-secrets-stores`;
- `wait`, timeout 5m, retry 1m;
- `healthCheckExprs` require `Ready=True` on the Certificates and the ExternalSecret. Plain kstatus does not read their `Ready`;
- no `targetNamespace`: every object names `dev-agents`;
- nothing depends on it.

There is no Deployment, route or admission object.

| Object (`dev-agents`) | Source |
| --- | --- |
| Issuers and Certificates (`dev-env-v2-portal-ca`, `-serving`) | The packet, unchanged. The leaf is Secret `dev-env-v2-portal-tls`. |
| Service `dev-env-v2-portal` | The packet, unchanged. |
| ServersTransport `dev-env-v2-portal` | The packet, fixed. Its `rootCAs` named a Secret nothing creates, and the installed CRD has no `rootCAs` field. It now uses `rootCAsSecrets: [dev-env-v2-portal-tls]`, whose `ca.crt` cert-manager fills with the issuing CA. The packet IngressRoute names this object. |
| ConfigMap `dev-env-v2-portal-config` | The packet's keys without `admission-api-url`, `admission-token-file` or `admission-uid`. The `witness-*` keys were removed by haynes-ops#4326. No `config/subject`. |
| ConfigMap `dev-env-v2-portal-postgres-ca` (`ca.crt`) | See the Postgres CA bullet below. |
| ConfigMap `dev-env-v2-portal-issuer-ca` (`ca.crt`) | Five roots for the Cloudflare edge of `authentik.haynesnetwork.com`; see below. File SHA-256 `1b80197679a4666ee8eaa26db3f3d1380d37da0d1b9b2b8d5120201d3dcc608e`. |
| ConfigMap `dev-env-v2-portal-openproject-ca` (`ca.crt`) | See the OpenProject CA bullet below. |
| ExternalSecret `dev-env-v2-portal-runtime` | The reviewed pending spec: `DB_USERNAME`, `DB_PASSWORD`, `SESSION_AEAD_KEY`, `SESSION_GENERATION` from `HaynesKube/dev-env`. No `dataFrom`. |

Provenance:
- **Postgres CA**:
  - CNPG `postgres16` CA, Secret `database/postgres16-ca` key `ca.crt`, certificate only;
  - UID `39740a0c-5539-4829-9b16-78524a120476`, resourceVersion `694827978`, exported 2026-10-26 18:02Z;
  - 603 bytes, file SHA-256 `f89c6cc6c2ddf495b5112023c7863142696ddecd413cba98bf36f1c940bae818`, DER SHA-256 `53b084dc3e8fe33fb9e6e1ffd49092cd806a396ef480324a2af0c21720e99f21`;
  - this copy's dates end 2026-11-05 03:55Z. CNPG renews the CA with the same key, and from dev-env#564 the portal trusts the pinned key with the dates opened, so the renewal needs no re-pin. Re-pin only if the CA key changes.
- **Issuer roots**: GTS Root R4, ISRG Root X1, ISRG Root X2, SSL.com TLS ECC Root CA 2022, and Sectigo Public Server Authentication Root E46.
  - The live chain on 2026-10-26 was `haynesnetwork.com` ← WE1 ← GTS Root R4.
  - CT logs since 2025-06 show the edge using GTS (primary), Sectigo and SSL.com (backups); Let's Encrypt is Cloudflare's other issuing CA.
  - The roots come from the Debian Mozilla bundle, and each matched the CA's published copy. The DER hashes are pinned in the guard.
- **OpenProject CA**:
  - CertificateRequest `frontend/openproject-portal-ca-1` `status.certificate` (UID `5df3bebb-9ed6-41f9-b1d7-74324910b0d5`, resourceVersion `853890739`), read without a Secret;
  - file SHA-256 `c190c3de7feaadcf23e7fa7a320409875cd366bae3239812cd33a117450bb21f`, DER SHA-256 `9bd738852a33a019cf8b75ec9c2c574402d5217c0f6b57fbcff25cfe0122d986`, valid until 2036-10-21.
- **Runtime fields**: Tom saved all four (relayed 2026-10-26). The DB pair is proven by the SecretSynced `database/dev-env-v2-portal-runtime-db`. This ExternalSecret's Ready is the first proof of the two `SESSION_*` fields.

Left for S8:
- The Deployment maps `dev-agents/dev-env-api-ca` `ca.crt` to `ca/operator-ca.crt`. There is no duplicate ConfigMap.
- It keeps `dev-env-v2-portal-tls` in its Reloader annotation. The portal loads its TLS files only at startup.
- If S8 drops the ServersTransport reference, Traefik's shared `insecureSkipVerify` applies and this object can go.

Checks:
- The guard is `scripts/tests/portal178-m5d/test_component.py` `RegisteredInputsTest`.
- An earlier pass checked the rendered config and the issuer, OpenProject and operator CAs against dev-env `b98247c`'s own parser. With the issuer roots it fetched the live JWKS; with the wrong CA it failed TLS.

If the runtime ExternalSecret cannot sync:
- no Secret is created;
- the Kustomization fails its health check after 5m, and after 15m the critical `FluxReconciliationFailure` alert fires;
- nothing depends on the Kustomization.

Acceptance, read from metadata:
- the Kustomization is Ready;
- both Certificates are Ready;
- the ExternalSecret is SecretSynced;
- no portal pod exists yet.

## Activation steps S8+S9: portal Deployment and internal route, 2026-10-26

Owner direction (Tom, relayed by the coordinator): fastest path to a working demo. The same Flux Kustomization `dev-env-system/dev-env-v2-portal` now also registers the portal Deployment and its LAN-only route. Its `dependsOn`, `wait`, timeout and health checks are unchanged; `wait` judges the Deployment through kstatus, so the Kustomization is Ready only once the portal pod is Ready.

**Deployment `dev-agents/dev-env-v2-portal`**, one replica:
- ServiceAccount `dev-env-v2-portal`, already registered by `dev-env-rbac` (`portal-serviceaccount.yaml`, haynes-ops#4326); not duplicated. No API token automount.
- ClusterFirst DNS with `ndots` 1, as the S1 policy requires. Pod Security restricted: non-root uid 65532, read-only root, no capabilities, RuntimeDefault seccomp. CPU limit 200m (the Kyverno dev-agents rule).
- Probes: HTTPS `/healthz` (startup, liveness) and `/readyz` (readiness) with `Host: dev-env-v2.haynesops.com`. The portal answers them only over TLS for its own host. Both are static: they do not test the database or Authentik.
- The image is one line: `ghcr.io/thaynes43/dev-env-portal:sha-fd89b72@sha256:a7b5b32756394c123b5aacf185867f4a883652ca5e7500bcddd0b3caf0ae7472`. That is dev-env v2.52.0 (main `fd89b72`, release #592; publish run 40616512876 signed and verified it). Since the previous portal it adds one thing, dev-env#591: the activity decoder accepts an optional `conversationUsage` key (exact, re-validated), and the Sessions page shows a Conversation usage panel and a conversation table on the Usage view for interactive sessions that report one. The operator sends that key only with `--portal-conversation-usage`, which is not set, so the page renders what it did, plus the wording changes listed in the PR. Send, history and launch stay off. The previous portal was `ghcr.io/thaynes43/dev-env-portal:sha-11be6ed@sha256:5ac0f12f142bb0e4a798bc1d64460e0444089ab4e69ccdef181076494aa6368e` (v2.50.0).
- Historical v2.52.0 operator pin: `sha-fd89b72@sha256:053d1905ecc6fb18c9a00189c50f1961949389264eaddd29b4e20c743eb8f5b3`, from dev-env v2.52.0 (main `fd89b72`, release #592, publish run 40616512876). The previous operator was `sha-11be6ed@sha256:dcaee481863793a702a8d8b187dbaa85a97270246557ce52ca3a19edd324fd4d` (v2.50.0, main `11be6ed`, publish run 40597197081). v2.52.0 adds attested grants (D-88), hw-ssh credential grants (D-89) and the usage unit B backend, all default off here, with CRD changes (credentialjobs, grantpolicies, agentsessions) that dev-env-crds carries first; it does not change the portal reader or launch routes, and running sessions are untouched. Earlier pins: dev-env#580 added the default-off portal launch route, registered only with `--portal-launch-repo` plus a required `--portal-launch-profile` (neither is set); v2.49.0 (`sha-e1f91a0`) brought the Remote Control entry in the summary; dev-env#574 the default-off message and conversation routes (no targets set); dev-env#576 the pilot agent image flags.

What the portal reads (dev-env `web/portal/session/production.go` after dev-env#560) and where each file comes from:

| Under `/etc/dev-env-portal` | Source |
| --- | --- |
| `config/<key>` | ConfigMap `dev-env-v2-portal-config`, whole. |
| `secrets/OIDC_CLIENT_ID`, `secrets/OIDC_CLIENT_SECRET` | Secret `dev-env-v2-portal-oidc` (its ExternalSecret is in `dev-env-secrets`) |
| `secrets/DB_USERNAME`, `DB_PASSWORD`, `SESSION_AEAD_KEY`, `SESSION_GENERATION` | Secret `dev-env-v2-portal-runtime` |
| `secrets/OPENPROJECT_TOKEN` | Secret `dev-env-v2-portal-openproject`, **optional**. Nothing creates it yet. |
| `secrets/OPERATOR_TOKEN` | Projected ServiceAccount token, audience `dev-env-operator`, one hour. The operator reader accepts only this identity. |
| `ca/ca.crt` | ConfigMap `dev-env-v2-portal-postgres-ca` |
| `ca/issuer-ca.crt` | ConfigMap `dev-env-v2-portal-issuer-ca` |
| `ca/openproject-ca.crt` | ConfigMap `dev-env-v2-portal-openproject-ca` |
| `ca/operator-ca.crt` | ConfigMap `dev-env-api-ca` (dev-env-pki's pinned copy in `dev-agents`; it verified the live operator API on 2026-10-26). No duplicate. |
| `tls/tls.crt`, `tls/tls.key` | Secret `dev-env-v2-portal-tls`. Its `ca.crt` is not mounted, and the CA key Secret never is. |

The portal starts without `OPENPROJECT_TOKEN`: the file is optional and Tasks shows "not connected". When Tom saves the property, a follow-up registers the `dev-env-v2-portal-openproject` ExternalSecret and changes the pod template (for example an annotation), because Reloader does not act on a Secret's creation. Reloader restarts the portal when any other mounted input changes.

**IngressRoute `dev/dev-env-v2-portal`**, the pattern of the other internal apps here (for example `dev/dev-env`):
- class `traefik-internal`; `traefik-external` serves only its own class;
- `external-dns.kubernetes.io/target: internal.haynesops`. `haynesops.com` belongs to the UniFi external-dns, and the Cloudflare instance excludes it, so no public record is written;
- entry point `websecure`, host `dev-env-v2.haynesops.com`, TLS `certificate-haynesops`. It sits in `dev` because that wildcard certificate is reflected to `dev`, not to `dev-agents`; `traefik-internal` allows the cross-namespace Service;
- backend `dev-agents/dev-env-v2-portal:8443` over HTTPS with ServersTransport `dev-env-v2-portal`. Traefik v3.7.16 resolves that name in the Service's namespace and checks the portal leaf against the portal CA;
- no middleware, no Authentik forward-auth (the portal does its own OIDC), no Cloudflare tunnel entry.

Checks: `RegisteredInputsTest` pins the file map above, the pod's identity, DNS and security context, the one image line, and the route's class, DNS target, host, entry point, TLS and backend. Mutations that would expose the route publicly (external class, Cloudflare host or annotation, extra entry point, middleware, plain HTTP backend) or break a mount fail it.

A throwaway run of `LoadProduction` at dev-env#560 head `3bc4d44` read a tree built from this Deployment's rendered mounts (rendered ConfigMaps, the live `dev-env-api-ca`, synthetic secrets and leaf, a synthetic operator token, no OpenProject token). It loaded, answered `/healthz` and `/readyz` with the probe's Host, refused a probe without it, and refused to start without `ca/operator-ca.crt`. Nothing from that run is committed.

Acceptance, after the image bump and merge:
- the Kustomization is Ready and the pod is Ready;
- `dev-env-v2.haynesops.com` resolves on the LAN only, and sign-in through Authentik reaches the Agents page for an Admins member;
- Sessions lists the dev-env sessions in `dev-agents`; Tasks shows not connected until the OpenProject token exists.

### Operator-only remote-session guard, 2026-10-28

The historical v2.62.1 operator pin is `sha-397f688@sha256:5b4526802554d844b864b6090185f6f1f40f449116aa29ed9790e7ac34bd8a8a` from dev-env v2.62.1 (`397f688ef1dfdfc433ee967bbcbde148578f1f60`, PR630/631). Publish40783327912 signed and verified the exact operator digest. Remote Control sessions no longer automatically drain on a template revision, including the fresh pre-stop check; already-stopped recovery and explicit owner operations remain supported. All operator flags, mounts, permissions and templates retain their existing values. No Send/history activation, Reminder API activation, agent-image adoption, RC restart or capacity change is included. The operator rollout and filtered original Session/Pod/PVC/registration metadata readback are recorded on OpenProject104; metadata continuity alone does not prove phone pairing or conversation-content acceptance.

### Default-off task-pilot operator binary, 2026-10-29

The v2.63.0 operator pin was `sha-3a22ca9@sha256:a02da1535aa083678419c9b65368da6191d264fc96a62c31e6c9bd9a59de797b`, signed v2.63.0 source `3a22ca9b0463279d1fa312c14538a996d2096838` (dev-env#632/#633; publish40793043582 keyless Sign/Verify success). It adds a separate default-off Codex TASK image tuple in source. Neither `--task-pilot-agent-image` nor `--task-pilot-agent-session` is configured here: no new task pilot is enabled. The prior RC drain guard remains, as do every current flag and the original Claude local/remote and Codex remote pilot mapping. Templates, agent/pilot worker images, mounts, profiles, permissions, credentials, capacity and active sessions are unchanged. No API or CRD schema changes occur between2.62.1 and2.63.0. Operator-only rollout and filtered original RC/local-pilot metadata continuity readback are recorded on OpenProject104; this is not phone/private-history or live Terra-serving acceptance.

## Separate future Codex TASK image selector, 2026-10-29

The operator-only v2.63.0 installation (#4390) left the new selector off. The
subsequent independently reviewed configuration selects only the future
`dev-env-1019-205125/terra61-next-worker-20261029-01` parent/idempotency pair for
`ghcr.io/thaynes43/dev-env:2.63.0@sha256:010ce2db9371e0de7f12e9b2d5243a182f7c684faea2aab874c67e8a328a3a9f`.
Normal tag publication40793066742 signed and verified that exact agent digest.
A targeted preflight found no existing Session with this key. Controller/API
qualification is dev-env#632 and release v2.63.0, not a new security entitlement.

Only ordinary Codex TASK pods for repository dev-env qualify, with exact
parent/key and Session UID ownership. Existing Pod early-return, original HOME
binding, executor reservation and retained/uncertain-attempt checks remain in
force. Workspace, project-canary, private-pilot, hold and budget-bound paths are
excluded. The API still derives the parent from authenticated identity and
retains admission/profile, idempotency, depth/capacity and budget enforcement.
The selector is not a lifetime one-use limit; using this key once and removing
both task-pilot flags afterward are operating rules.

The old remote/local image pair, global templates/default agent image, current
Sessions and task-budget worker-image guard are unchanged. No Session creation,
capacity polling, eviction, credential/grant or HASS operation is part of this
configuration. The future separately authorized worker must request
`gpt-6.1-terra` / `medium`; configured is not served-model evidence. Model
serving stays Unknown until a supported runtime/provider receipt exists.
Installation and five-target metadata continuity are recorded under OpenProject
#104; active selector readiness is not a task launch or live model acceptance.

### Next separately authorized source task, 2026-10-29

The earlier `terra61-next-worker-20261029-01` key was consumed by the successful
one-turn provider-reported Terra acceptance. Do not reuse it. Story235 now owns
one ordinary managed `gpt-6.1-terra` / `medium` source worker under the fresh exact
`dev-env-1019-205125/provider-child-cap-20261029-01` pair. Only the TASK selector
key changes; its signed2.63.0 agent digest and old remote/local mapping remain
unchanged. Existing smoke pod/retention proof, templates, permissions, profiles,
budget-image and active-child admission stay unchanged by this rotation.
The cap source task may change admission only through its own reviewed PR;
this configuration does not itself expand the current cap or start a Session.

### Operator-only v2.63.1 pin, 2026-10-29

The current operator pin is `sha-3e3e93d@sha256:8cf8a2ba541033280563cb483aa891f49e990358d2a9f347648140f827afdd10`, dev-env v2.63.1 source `3e3e93d3cace4d8a02af08c442f80aae557d0e68` (dev-env#634/#635/#636 and the Kubernetes client-library patch #638). Publish40810610315 succeeded and this exact digest was independently keyless-verified. Only the operator image moves; flags, pilot and worker images, templates, permissions, volumes and existing Sessions are unchanged. The previous pin was the v2.63.0 `sha-3a22ca9` above.
