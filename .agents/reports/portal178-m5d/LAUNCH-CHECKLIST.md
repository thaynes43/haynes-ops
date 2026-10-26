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
| Operator reader | Current operator source/live metadata: `sha-617ed15@sha256:888c2a0cd1057091cd2bbac7ac743933a4819e100c4570b6eb9b7579afddd34b`, 2/2 ready at 16:14:44Z; no explicit `--enable-portal-reader` or `--session-namespace` args | Reader is not enabled; default namespace comment is not enablement evidence | Separate authorized operator release | Held activation; do not edit HelmRelease here |
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

Compatible operator source candidate: `ghcr.io/thaynes43/dev-env-operator:sha-af402e5@sha256:1377ef1ee051543eeef19e4cd7e284488333a557681bae07223f64d9539c848a`. This source-only packet does not change the registered operator image, flags, login image, policy, registration, admission or resource limits. The ordinary policy-before-workload ordering and all existing runtime holds stay unchanged. The public PostgreSQL `ca.crt` export remains pending its specific disposition/provenance; the denied Secret read is not retried.

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
