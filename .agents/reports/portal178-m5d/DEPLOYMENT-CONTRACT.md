# Story178 M5D deployment contract

Canonical OpenProject178 / Epic120 / Saga85. This document describes a renderable
packet that is not registered with Flux. Rendering, review, and required CI do
not install a Deployment, Service, route, ExternalSecret, policy, or admission
object. Live effective rights, TLS, isolation, database grants, secret sync,
identity, and Gate A stay measured acceptance items.

Foundations reused, unchanged by this packet:

- PR4246 `ad1c86b8bff46a32a28bfa43c4635dddadc8f769` OIDC blueprint.
- PR4257 `6501bf3978a6850a2edb91254cd6797a327bf56b` database, witness, and IdP preparation.
- PR4265 `9464389a167cff5386444f1fe85c690ed1697810` reject-only HBA, mapping owner, and witness RBAC.
- Pending manifests under `.agents/reports/portal178-m5c/pending/` for the runtime ExternalSecret, the CNPG basic-auth projection, and the BFF CiliumNetworkPolicy.
- Merged dev-env session constructors: exact origin host, callback `origin + /auth/callback`, postgres host `postgres16-rw.database.svc.cluster.local`, username `dev_env_v2_portal_runtime`, database `dev_env_v2_portal`, `sslmode=verify-full`, MaxConns 2, and admission API path `/api/v1/namespaces/dev-agents/configmaps/dev-env-v2-portal-admission`.

Runtime alignment, 2026-10-24. The current contract is `/home/dev/work/orders/portal178-runtime-assembly-20261024/integration-fixes-02/RUNTIME-CONTRACT.md`. It supersedes `/home/dev/work/orders/portal178-runtime-assembly-20261024/canonical-item-01/RUNTIME-CONTRACT.md`. Source PR541 head `6a522e843a8768099d8eb8086f9ff8272f94b725` remains the published runtime source this packet was checked against, together with the parser updates in that same worktree. Earlier packets recorded no contract. Those checks are superseded. This packet follows the integration-fixes contract. It is not a merge and it does not install the component.

The published process is executable `dev-env-portal`, HTTPS on `:8443`, probes `GET /healthz` and `GET /readyz`, mount root `/etc/dev-env-portal`, and uid/gid 65532. The image repository named by that contract is `ghcr.io/thaynes43/dev-env-portal`. No digest is published. The Deployment image stays `unregistered.invalid/dev-env-v2-portal:UNPUBLISHED`.


## Canonical 1Password item

Supersession, 2026-10-24. The canonical item is existing `HaynesKube/dev-env`. Prior instructions to use or create `dev-env-v2-portal` or `dev-env-v2-portal-runtime` are superseded. Those names remain in historical reports and in this paragraph so the earlier instruction stays visible. They are not remoteRef keys. Provider id `dev-env-v2-portal-internal`, application slug, callback, and Kubernetes object names are unchanged. Mount paths now follow the published runtime contract. Credentials are not rotated.

Every portal remoteRef is an individual `property` on `key: dev-env`. There is no `dataFrom` and no whole-item extract. Unrelated fields already stored on the item are not projected.

Source metadata under `kubernetes/` already projects these properties from `key: dev-env`: `CIGAR_JOURNAL_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLOUDFLARE_PERSONAL_DNS_TOKEN`, `CURSOR_MEATBALL_KEY`, `DEV_ENV_PUSHOVER_TOKEN`, `HW_SSH_PRIVATE_KEY_B64`, `OMNI_OPERATOR_KEY`, `OMNI_SERVICE_ACCOUNT_KEY`, `PROXMOX_OPERATOR_TOKEN_ID`, `PROXMOX_OPERATOR_TOKEN_SECRET`, `SSH_CA_PRIVATE_KEY_B64`, `SSH_CA_PUBLIC_KEY`. None of those names collide with `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `DB_USERNAME`, `DB_PASSWORD`, `SESSION_AEAD_KEY`, or `SESSION_GENERATION`. This list is source projection metadata, not a secret read and not a claim that the item has no other unreferenced fields.

`OIDC_CLIENT_ID` value remains the public client id `dev-env-v2-portal-internal`. `OIDC_CLIENT_SECRET` is the existing installed Authentik provider secret. Root already instructed Tom to put only those two properties on `HaynesKube/dev-env`.

Runtime properties stay on the same item and are HELD until provisioning and the runtime parser match. Do not populate them yet. Future format, still unresolved where noted:

| Property | Future source | Format known now | Unresolved |
| --- | --- | --- | --- |
| `OIDC_CLIENT_ID` | Existing item `dev-env` | Public value `dev-env-v2-portal-internal` | USER-REPORTED SAVED. Not SecretSynced. Not value-verified. No duplicate request. |
| `OIDC_CLIENT_SECRET` | Same item, installed provider secret | Existing secret, not rotated | USER-REPORTED SAVED. Not SecretSynced. Not value-verified. Value is never read here. |
| `DB_USERNAME` | Same item, later reviewed handoff | Must equal the accepted SQL role `dev_env_v2_portal_runtime` | HELD. The parser compares the mounted file with that role. Do not write it yet. |
| `DB_PASSWORD` | Same item, later reviewed handoff | Must match the reviewed CNPG basic-auth provisioning mechanism | HELD. Not generated here. The writer is not authorized. |
| `SESSION_AEAD_KEY` | Same item, later reviewed handoff | Standard base64 (`StdEncoding`, strict) of exactly 32 bytes, with no whitespace | HELD. The published parser is `canonicalKey` plus `base64.StdEncoding.Strict`. No key is generated. |
| `SESSION_GENERATION` | Same item, later reviewed handoff | 32–128 characters from letters, digits, `-`, and `_` | HELD. That is `validGeneration`. No value is generated. |
| `config/subject` | Existing issuer-plus-subject pin. Not an item property | The parser reads `config/subject` | Unresolved. The file is absent. Do not invent `PORTAL_SUBJECT` or ask for a new field. |

`ValidatePortalConsumer` accepts individual `remoteRef.property` entries for `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `DB_USERNAME`, `DB_PASSWORD`, `SESSION_AEAD_KEY`, and `SESSION_GENERATION` on item `dev-env`. `secretKey` must equal the property. Acceptance of the four runtime properties is an inert staged definition. It is not population, not installation, and not SecretSynced. `PORTAL_SUBJECT` and the two `SSH_CA_*` keeper fields are rejected. The unregistered render includes those inert projections. No file under `kubernetes/` references them.

## Render root

`.agents/reports/portal178-m5d/component`

`kustomize build` of that directory is the packet. No file under `kubernetes/`
references it. `ccnp-exclusion.yaml` is a review template and is not a resource.
The historical OIDC proposal `.agents/reports/portal178-m5a/pending/externalsecret.yaml`
stays unregistered and must not be applied in addition to this packet.

## Consumer shape

| Object | Namespace | Name | Notes |
| --- | --- | --- | --- |
| Deployment | dev-agents | dev-env-v2-portal | 2 replicas, surge 1, maxUnavailable 0. Pool bound is 2 connections per replica, so 2+1 replicas use 6 of the proposed connectionLimit 8. |
| Service | dev-agents | dev-env-v2-portal | ClusterIP TCP 8443. |
| ConfigMap | dev-agents | dev-env-v2-portal-config | Pinned non-secret files. `config/admission-uid` is absent. |
| ConfigMap | dev-agents | dev-env-v2-portal-postgres-ca | Public CA input. `ca.crt` is absent. Annotation `dev-env.haynesops.com/ca-sha256=UNVERIFIED`. |
| ConfigMap | dev-agents | dev-env-v2-portal-admission-ca | Peer CA input. `ca.crt` is absent. |
| ConfigMap | dev-agents | dev-env-v2-portal-issuer-ca | Peer CA input. `ca.crt` is absent. |
| ConfigMap | dev-agents | dev-env-v2-portal-openproject-ca | Peer CA input. `ca.crt` is absent. |
| ConfigMap | dev-agents | dev-env-v2-portal-operator-ca | Peer CA input. `ca.crt` is absent. |
| ExternalSecret | dev-agents | dev-env-v2-portal-oidc | 1Password item `dev-env`, properties `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` only. Kubernetes name unchanged. |
| ExternalSecret | dev-agents | dev-env-v2-portal-runtime | Inert staged projection of the four runtime properties. Population and install HELD. Kubernetes name unchanged. |
| ExternalSecret | database | dev-env-v2-portal-runtime-db | Inert staged basic-auth projection of `DB_USERNAME` and `DB_PASSWORD`. Population and install HELD. |
| CiliumNetworkPolicy | dev-agents | dev-env-v2-portal | Copy of the pending BFF policy. |
| IngressRoute | dev | dev-env-v2-portal | Internal host only. |
| ServersTransport | dev | dev-env-v2-portal | Backend TLS verification for this service only. |

Store reference for every ExternalSecret: `ClusterSecretStore` `onepassword-connect`. No new SecretStore, no `dataFrom`, no migration item, and no field outside the lists above.

The Deployment uses ServiceAccount `dev-env-v2-portal`, which is already registered by `portal-witness.yaml`. This packet does not emit another ServiceAccount, Role, or RoleBinding. `automountServiceAccountToken` is false. A projected token and the namespace `kube-root-ca.crt` ConfigMap are mounted at `/var/run/portal/api` so the approved named-object GET can be exercised. That mount is not a new RBAC grant.

Secret and CA material are files, not environment variables. The process environment must not set `PGSERVICE`, `PGSERVICEFILE`, `PGPASSFILE`, `PGSSLCERT`, `PGSSLKEY`, `PGSSLROOTCERT`, `PGSSLCRL`, or `PGSSLCRLDIR`. The container has no `env`, `command`, or `args`.

Mounts:

| Volume | Source | Mount | Keys |
| --- | --- | --- | --- |
| config | ConfigMap `dev-env-v2-portal-config` | `/etc/dev-env-portal/config` | pinned lowercase files listed below |
| oidc | Secret `dev-env-v2-portal-oidc` | `/etc/dev-env-portal/secrets/OIDC_CLIENT_ID`, `/etc/dev-env-portal/secrets/OIDC_CLIENT_SECRET` | those two properties only |
| runtime | Secret `dev-env-v2-portal-runtime`, not created by this render | `/etc/dev-env-portal/secrets/DB_USERNAME`, `DB_PASSWORD`, `SESSION_AEAD_KEY`, `SESSION_GENERATION` | filenames only; no ExternalSecret selects them |
| postgres-ca | ConfigMap `dev-env-v2-portal-postgres-ca` | `/etc/dev-env-portal/ca/ca.crt` | key absent |
| admission-ca | ConfigMap `dev-env-v2-portal-admission-ca` | `/etc/dev-env-portal/ca/admission-ca.crt` | key absent |
| issuer-ca | ConfigMap `dev-env-v2-portal-issuer-ca` | `/etc/dev-env-portal/ca/issuer-ca.crt` | key absent |
| openproject-ca | ConfigMap `dev-env-v2-portal-openproject-ca` | `/etc/dev-env-portal/ca/openproject-ca.crt` | key absent |
| operator-ca | ConfigMap `dev-env-v2-portal-operator-ca` | `/etc/dev-env-portal/ca/operator-ca.crt` | key absent |
| serving-tls | Secret `dev-env-v2-portal-tls`, not created here | `/etc/dev-env-portal/tls/tls.crt`, `/etc/dev-env-portal/tls/tls.key` | both absent |
| api | projected SA token plus `kube-root-ca.crt` | `/var/run/portal/api/token`, `/var/run/portal/api/ca.crt` | token lifetime 3600 seconds; `config/admission-token-file` names the token path |

`config/subject` is not in the ConfigMap. It is the existing issuer-plus-subject pin, not a 1Password property, and its value is unresolved. `PORTAL_SUBJECT` is not a mount and not an item field. `config/admission-uid` is not in the ConfigMap. `config/session-bindings.json`, `secrets/OPENPROJECT_TOKEN`, and `secrets/OPERATOR_TOKEN` are all absent, which is the published disabled-metadata state. The pod still cannot start: `config/subject` is missing, and the serving secret and every peer CA key are missing. The runtime ExternalSecret is inert and unpopulated.

Pinned config files:

- `origin` `https://dev-env-v2.haynesops.com`
- `issuer` `https://authentik.haynesnetwork.com/application/o/dev-env-v2-portal/`
- `authorization-url` `https://authentik.haynesnetwork.com/application/o/authorize/`
- `token-url` `https://authentik.haynesnetwork.com/application/o/token/`
- `userinfo-url` `https://authentik.haynesnetwork.com/application/o/userinfo/`
- `jwks-url` `https://authentik.haynesnetwork.com/application/o/dev-env-v2-portal/jwks/`
- `openproject-base-url` `https://work.haynesnetwork.com`
- `operator-api-url` `https://dev-env-operator.dev-env-system.svc.cluster.local:8443` from the operator HelmRelease `--api-url`
- `admission-api-url` `https://kubernetes.default.svc.cluster.local`
- `admission-token-file` `/var/run/portal/api/token`
- `auth-style` `header`
- `login-ttl` `1m`, `idle-ttl` `30m`, `absolute-ttl` `1h`, `fresh-for` `1m`, `clock-skew` `0s`
- `witness-gets-per-second` `30`, `witness-get-burst` `57`

The duration, auth-style, and witness values are the published `production_test.go` fixture and sit inside the contract bounds. They are not a launch SLO. The stable contract may replace them before freeze.

Image: `unregistered.invalid/dev-env-v2-portal:UNPUBLISHED`. There is no digest. The contract names `ghcr.io/thaynes43/dev-env-portal` and a `sha-<short>` tag from `main`. This packet does not invent that tag. uid and gid are 65532.

Probes:

- startup and liveness: HTTPS GET `/healthz` on port `https`
- readiness: HTTPS GET `/readyz` on port `https`

The published gate also requires `Host: dev-env-v2.haynesops.com` and returns a body `ok`. The Probe API cannot set the Host header, and this packet does not add one. Kubelet requests therefore fail closed at the process. Probe success is not claimed. Kubelet probe success is not certificate verification and is not Gate A.

DNS: `ClusterFirst` and `ndots=1`, matching `.agents/reports/portal178-m5c/pending/bff-network-exclusions.json`.

Pod template annotations `reloader.stakater.com/auto`, `configmap.reloader.stakater.com/reload`, and `secret.reloader.stakater.com/reload` name the config and secret objects above plus `dev-env-v2-portal-tls`. Reloader is the existing cluster mechanism. A reload annotation is not rotation proof.

## Internal host and backend TLS

IngressRoute namespace is `dev` because `certificate-haynesops` is already reflected into `dev`. This packet does not add `dev-agents` to the reflector allow list.

- Class `traefik-internal`, entrypoint `websecure` only.
- Match: Host(`dev-env-v2.haynesops.com`) && PathPrefix(`/`).
- TLS secret `certificate-haynesops`.
- ExternalDNS target `internal.haynesops`.
- Service `dev-agents/dev-env-v2-portal` port 8443, scheme `https`, `passHostHeader: true`, `serversTransport: dev-env-v2-portal`.
- No forward-auth middleware and no homepage annotation.

ServersTransport `dev/dev-env-v2-portal` sets `insecureSkipVerify: false`, `serverName: dev-env-v2-portal.dev-agents.svc.cluster.local`, and `rootCAsSecrets: [dev-env-v2-portal-serving-ca]`. The shared Traefik setting `serversTransport.insecureSkipVerify=true` remains in place for other backends. This object overrides it only for the portal service.

Secret `dev/dev-env-v2-portal-serving-ca` and Secret `dev-agents/dev-env-v2-portal-tls` are not in the render. No Certificate, Issuer, or private key is created. The PEM key Traefik chart 42.1.3 reads from `rootCAsSecrets` is unverified and must be confirmed before registration. Reusing `dev-env-ca` or the PostgreSQL CA for the BFF listener is not approved here.

## Network isolation

The rendered CiliumNetworkPolicy is the pending BFF policy: default deny, ingress only from `network` / `traefik` / `traefik-internal-network` to TCP 8443, egress only to CoreDNS for three exact names, `postgres16` TCP 5432, kube-apiserver TCP 443 and 6443, and `authentik.haynesnetwork.com` TCP 443.

That policy is additive. `dev-env-session-platform` and `dev-env-session-web` still select a `dev-agents` pod named `dev-env-v2-portal`. `ccnp-exclusion.yaml` records the append-only NotIn change and is not rendered. Until that reviewed edit is applied, the dedicated policy is not an effective isolation boundary.

## Authority table

| Identity | Resource or edge | Verb or traffic | Existing approval | This packet |
| --- | --- | --- | --- | --- |
| ServiceAccount `dev-agents/dev-env-v2-portal` | ConfigMap `dev-agents/dev-env-v2-portal-admission` | get | Witness grant, registered in PR4265 `portal-witness.yaml` | Referenced. Role is not copied or widened. |
| ServiceAccount `dev/dev-env` | same ConfigMap | get, update | Same witness grant. Recovery also has pre-existing read breadth that this Role does not define. | Unchanged. |
| BFF pod | Secret `dev-agents/dev-env-v2-portal-oidc` | mount two OIDC keys | Existing item `HaynesKube/dev-env` | Staged ExternalSecret. Historical `dev-env-system` proposal is not a second consumer. |
| BFF pod | Secret `dev-agents/dev-env-v2-portal-runtime` | mount four runtime filenames | Existing item `HaynesKube/dev-env` | Unregistered ExternalSecret. Population and install HELD. Not a live grant. |
| CNPG `postgres16` | Secret `database/dev-env-v2-portal-runtime-db` | username and password | Pending `role-externalsecrets.yaml` | Unregistered inert projection. `DB_PASSWORD` must match that reviewed basic-auth mechanism. Migrator stays NOLOGIN. |
| Traefik internal | IngressRoute `dev/dev-env-v2-portal` | Host `dev-env-v2.haynesops.com`, websecure, existing reflected certificate | Approved internal host | Staged. No public route and no reflector edit. |
| Traefik to BFF | ServersTransport `dev/dev-env-v2-portal` | HTTPS, verify CA, exact serverName | Required isolation in the internal-reader sequence | Template. Serving CA Secret is absent. |
| BFF pod | CNP `dev-agents/dev-env-v2-portal` | ingress and egress above | Pending `bff-networkpolicy.yaml` | Staged copy. |
| BFF pod | CCNPs `dev-env-session-platform` and `dev-env-session-web` | remove this name from the additive allows | Required before the dedicated policy is effective | Review template only. Live policies unchanged. |
| portal roles | `postgres16` `pg_hba` | six reject lines | PR4265 `cluster16.yaml` | Unchanged. No hostssl allow. |
| certificate owner | ConfigMap `dev-agents/dev-env-v2-portal-postgres-ca` key `ca.crt` | public certificate only | `PORTAL-PUBLIC-CA-OPERATOR-HANDOFF-20261024.md` | Input prepared. No PEM stored. |
| `flux-system/kustomize-controller` | ConfigMap update after registration | existing static write | Handoff names that publisher | Registration is still closed. That identity is not CA provenance. |
| authorized operator | ConfigMap `dev-agents/dev-env-v2-portal-admission` | create once, data `state=closed` | Witness grant. BFF and recovery cannot create it. | Not in this render. |

## Public CA version, hash, and reload

ConfigMap `dev-env-v2-portal-postgres-ca` is the public trust input for `postgres16-rw.database.svc.cluster.local`, mounted at `/etc/dev-env-portal/ca/ca.crt`. It has `provenance: absent` and no `ca.crt`. The admission, issuer, OpenProject, and operator peer ConfigMaps are the same shape for their contract filenames. The kubelet cannot start the pod while any of those keys is missing.

Registration of a real certificate is one reviewed change that does all of the following:

1. Accept only X.509 `CERTIFICATE` PEM blocks. Reject a private-key marker, a Secret dump, or any other PEM type without printing the material.
2. Set `data.ca.crt` to those blocks only.
3. Set ConfigMap annotation `dev-env.haynesops.com/ca-sha256` and pod annotation `dev-env.haynesops.com/postgres-ca-sha256` to the SHA-256 of the exact `ca.crt` bytes.
4. Keep `dev-env.haynesops.com/portal-registration: unregistered` until the component is deliberately registered by a later reviewed change. Adding the PEM does not by itself authorize Flux inclusion.
5. Reloader, or the changed pod annotation, rolls both replicas. The process must load the new CA and keep verifying the hostname. A static annotation is not that proof.

Renewal uses the same four content steps. The named certificate owner supplies the replacement public file and the attestation below. The portal owner verifies it, publishes the reviewed GitOps update, and then verifies reload and hostname checks. If the input is missing, stale, expired, or does not match the attestation, admission stays closed and database connections stay closed.

Historical record only; not loaded: digest `53b084dc3e8fe33fb9e6e1ffd49092cd806a396ef480324a2af0c21720e99f21` with recorded expiry 2026-11-05 03:55 UTC. That record is not this ConfigMap, not a fresh artifact, and not a renewed validity claim. A difference from it needs an owner-attested rotation explanation.

### Public CA attestation fields

Required on every delivery, with no value invented by this packet:

- source cluster
- resource UID
- resourceVersion
- export UTC
- administrator/owner attestation
- SHA-256 of the delivered file
- DER SHA-256 fingerprint
- notBefore
- notAfter
- renewal owner/contact
- rotation statement

The renewal owner/contact is whoever already holds authorized certificate access. This packet does not name a person and does not create a reader.

## Closed and unregistered

Every rendered object carries `dev-env.haynesops.com/portal-registration: unregistered`. The admission ConfigMap is absent from the render. The six HBA rejects remain the live `cluster16` list. No hostssl allow, Database, managed role, migrator credential, public entrypoint, or broad RBAC object is in the render.

Tom reported `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` saved on `HaynesKube/dev-env`. That status is USER-REPORTED SAVED. It is not SecretSynced and it is not value-verified. This packet does not ask again. The four runtime properties are an inert staged projection. Population and install remain HELD. That projection is not a population request.
