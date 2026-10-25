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

## Current signed runtime and historical supersession

Current source is dev-env PR550, merged as `3221ca79f4d7fddf73b0cfafc71a24de28f13663` (2026-10-25 15:50:50Z). Publish run `40208871745` reached terminal SUCCESS at 15:54:50Z: push, keyless signing, and verification all succeeded. This packet therefore pins `ghcr.io/thaynes43/dev-env-portal:sha-3221ca7@sha256:033ff0b611eb15dc000f8d1bc653154f1f3f79d4f477b4eb62ac41d1e666bde2`. That immutable source artifact is not an installation or launch approval.

The current parser pins `https://openproject-web.frontend.svc.cluster.local:8443` and reads project 3 at `/api/v3/projects/3/work_packages?offset=<page>&pageSize=50&filters=%5B%5D`; `offset` is page number 1 onward, `pageSize` is 50, and the empty filters array is required. Sessions and Tasks are independent: an absent projected `OPERATOR_TOKEN` gives “Sessions not connected”; an absent optional `OPENPROJECT_TOKEN` gives “Tasks not connected”. `config/session-bindings.json` is not a production requirement, and no session-to-task join is inferred.

Historical record only: the prior packet pin `5b39ebb13f3651c8aa741a2b4358bc1eecf6dd30`, PR545, `approved-readers-06`, and their schema/binding assertions are superseded for current-runtime configuration. Their receipts and earlier failed/denied evidence remain historical; they do not authorize activation or alter this artifact. The process remains `dev-env-portal`, HTTPS on `:8443`, with `/healthz` and `/readyz`, mount root `/etc/dev-env-portal`, and uid/gid 65532.

Historical review disposition for frozen head `34d31aea958bd06523561b8a85bde70ed7f80cc2`: certificate ordering, projected-token directory mounting, and incomplete preflight evidence were corrected in that prior source pass. Its helper does not execute, a caller-supplied snapshot is not live permission proof, and no collector is implemented.

Approved reader source, not installed. `ExternalSecret` `dev-env-v2-portal-openproject` selects only existing property `OPENPROJECT_TOKEN` and stays unregistered. A second projected token uses audience `dev-env-operator` and `expirationSeconds: 3600`, mounted at `secrets/OPERATOR_TOKEN`. The admission token at `/var/run/portal/api` has no audience. No new Role or RoleBinding. OpenProject NGINX listens on 8443 for `GET /api/v3/projects/3/work_packages` only and proxies to `http://127.0.0.1:8080`. The portal policy reaches OpenProject web pods on TCP 8443 and the operator on TCP 8443. `dev-env-v2-portal` is appended to the two session `app.kubernetes.io/name` NotIn lists. The plaintext 8082 dev-agents rule excludes that name. `openproject-portal-api` allows TCP 8443 only from the portal. Dedicated cert-manager Issuers and Certificates use the existing ECDSA P-256 durations. Leaf keys mount on the services. CA keys stay in their CA Secrets. Portal certificates stay unregistered. OpenProject certificates, the listener, and the network edits are registered source and apply only if this branch merges. This pass does not merge.


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
| `config/subject` | Installed provider 174 `user_uuid` maps to the active named user's `User.uuid`. Not an item property | The parser reads `config/subject` | Source path is verified. The value is not materialized. No raw subject and no hash are in this repo. The file is absent. Do not invent `PORTAL_SUBJECT` or a new field. |

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
| CiliumNetworkPolicy | dev-agents | dev-env-v2-portal | Pending BFF rules plus exact OpenProject and operator egress. Not applied. |
| IngressRoute | dev | dev-env-v2-portal | Internal host only. |
| ServersTransport | dev-agents | dev-env-v2-portal | Backend TLS verification. Namespace is the Service namespace Traefik uses for an unqualified name. |

Store reference for every ExternalSecret: `ClusterSecretStore` `onepassword-connect`. No new SecretStore, no `dataFrom`, no migration item, and no field outside the lists above.

The Deployment uses ServiceAccount `dev-env-v2-portal`, which is already registered by `portal-witness.yaml`. This packet does not emit another ServiceAccount, Role, or RoleBinding. `automountServiceAccountToken` is false. A projected token and the namespace `kube-root-ca.crt` ConfigMap are mounted at `/var/run/portal/api` so the approved named-object GET can be exercised. That mount is not a new RBAC grant.

Secret and CA material are files, not environment variables. The process environment must not set `PGSERVICE`, `PGSERVICEFILE`, `PGPASSFILE`, `PGSSLCERT`, `PGSSLKEY`, `PGSSLROOTCERT`, `PGSSLCRL`, or `PGSSLCRLDIR`. The container has no `env`, `command`, or `args`.

Mounts:

| Volume | Source | Mount | Keys |
| --- | --- | --- | --- |
| config | ConfigMap `dev-env-v2-portal-config` | `/etc/dev-env-portal/config` | pinned lowercase files listed below |
| secrets | One projected directory, no `subPath` | `/etc/dev-env-portal/secrets` | OIDC and runtime keys are required. `OPENPROJECT_TOKEN` is `optional: true`. `OPERATOR_TOKEN` is the projected token `path`, audience `dev-env-operator`, lifetime 3600. |
| postgres-ca | ConfigMap `dev-env-v2-portal-postgres-ca` | `/etc/dev-env-portal/ca/ca.crt` | key absent |
| admission-ca | ConfigMap `dev-env-v2-portal-admission-ca` | `/etc/dev-env-portal/ca/admission-ca.crt` | key absent |
| issuer-ca | ConfigMap `dev-env-v2-portal-issuer-ca` | `/etc/dev-env-portal/ca/issuer-ca.crt` | key absent |
| openproject-ca | ConfigMap `dev-env-v2-portal-openproject-ca` | `/etc/dev-env-portal/ca/openproject-ca.crt` | key absent |
| operator-ca | ConfigMap `dev-env-v2-portal-operator-ca` | `/etc/dev-env-portal/ca/operator-ca.crt` | key absent |
| serving-tls | Secret `dev-env-v2-portal-tls`, not created here | `/etc/dev-env-portal/tls/tls.crt`, `/etc/dev-env-portal/tls/tls.key` | both absent |
| api | projected SA token plus `kube-root-ca.crt` | `/var/run/portal/api/token`, `/var/run/portal/api/ca.crt` | token lifetime 3600 seconds; `config/admission-token-file` names the token path |

`config/subject` is not in the ConfigMap. Its source path is installed provider 174 `user_uuid` to the active named user's `User.uuid`. The value is not materialized, and this repo does not store the raw subject or a hash of it. It is not a 1Password property. `PORTAL_SUBJECT` is not a mount and not an item field. `config/admission-uid` is not in the ConfigMap. Sessions and Tasks are independent optional readers as described above; `config/session-bindings.json` is absent from this render and not a production requirement. No new 1Password property or token consumer is added. The pod still cannot start: `config/subject` is missing, and the serving secret and every peer CA key are missing. The runtime ExternalSecret is selected in inert source and unpopulated.

Pinned config files:

- `origin` `https://dev-env-v2.haynesops.com`
- `issuer` `https://authentik.haynesnetwork.com/application/o/dev-env-v2-portal/`
- `authorization-url` `https://authentik.haynesnetwork.com/application/o/authorize/`
- `token-url` `https://authentik.haynesnetwork.com/application/o/token/`
- `userinfo-url` `https://authentik.haynesnetwork.com/application/o/userinfo/`
- `jwks-url` `https://authentik.haynesnetwork.com/application/o/dev-env-v2-portal/jwks/`
- `openproject-base-url` `https://openproject-web.frontend.svc.cluster.local:8443`
- `operator-api-url` `https://dev-env-operator.dev-env-system.svc.cluster.local:8443` from the operator HelmRelease `--api-url`
- `admission-api-url` `https://kubernetes.default.svc.cluster.local`
- `admission-token-file` `/var/run/portal/api/token`
- `auth-style` `header`
- `login-ttl` `1m`, `idle-ttl` `30m`, `absolute-ttl` `1h`, `fresh-for` `1m`, `clock-skew` `0s`
- `witness-gets-per-second` `30`, `witness-get-burst` `57`

The duration, auth-style, and witness values are source-tested fixture values inside the contract bounds. They are not a launch SLO.

Image: `ghcr.io/thaynes43/dev-env-portal:sha-3221ca7@sha256:033ff0b611eb15dc000f8d1bc653154f1f3f79d4f477b4eb62ac41d1e666bde2`. It is the terminal-success signed artifact for source `3221ca79f4d7fddf73b0cfafc71a24de28f13663`; signing identity is `https://github.com/thaynes43/dev-env/.github/workflows/publish.yml@refs/heads/main` with issuer `https://token.actions.githubusercontent.com`. uid and gid are 65532. The container requests `50m` CPU and limits CPU to `200m`, under the `dev-agents` ceiling of 8 CPU in `dev-env-require-cpu-limit`. Memory request `64Mi` and limit `256Mi` are unchanged. The CPU limit is a staged bound, not a measured capacity.

Probes:

- startup and liveness: HTTPS GET `/healthz` on port `https`
- readiness: HTTPS GET `/readyz` on port `https`
- each sets `httpHeaders` `Host: dev-env-v2.haynesops.com`
- none sets `httpGet.host`, so the kubelet still dials the pod IP

The published gate requires that Host and returns a body `ok`. Kubernetes HTTP probes set a virtual host through `httpHeaders`, not through `httpGet.host` (Pod `HTTPGetAction`: host is the dial name and defaults to the pod IP). Kubelet HTTPS probes skip certificate verification, so a successful probe is not serving-certificate proof and is not Gate A.

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

Traefik v3.7.16 `makeServersTransportKey` resolves an unqualified `serversTransport` with `nameBuilder.makeID` in the Service namespace. The rendered Service reference sets that namespace to `dev-agents`, so the lookup is `dev-agents/dev-env-v2-portal`. The IngressRoute stays in `dev` so it can use the reflected `certificate-haynesops` Secret. The ServersTransport object is in `dev-agents` with that name. It sets `insecureSkipVerify: false`, `serverName: dev-env-v2-portal.dev-agents.svc.cluster.local`, and `rootCAs: [{secret: dev-env-v2-portal-serving-ca}]`. The shared Traefik setting `serversTransport.insecureSkipVerify=true` remains in place for other backends. This object overrides it only for the portal service.

The leaf in Secret `dev-agents/dev-env-v2-portal-tls` is what the process loads from `tls/tls.crt` and `tls/tls.key`. `sanMatch` accepts the leaf only when `DNSNames` contains `dev-env-v2.haynesops.com`. Traefik verifies that same leaf against `serverName`, so the leaf also needs DNS SAN `dev-env-v2-portal.dev-agents.svc.cluster.local`. Those are the two required DNS SANs. No other name is added here.

`loadCASecret` reads the serving CA from the ServersTransport namespace. The future Secret is therefore `dev-agents/dev-env-v2-portal-serving-ca`, not a Secret in `dev`. Traefik chart 42.1.3 ships appVersion v3.7.16. A referenced Secret must contain the certificate under `tls.ca` or `ca.crt`. `rootCAsSecrets` is deprecated there; `rootCAs[].secret` is the current field, and this render uses it. Neither Secret is in the render. No Certificate, Issuer, private key, or CA is created. Reusing `dev-env-ca` or the PostgreSQL CA for the BFF listener is not approved here.

## Network isolation

The rendered CiliumNetworkPolicy keeps default deny and the pending BFF rules: ingress only from `network` / `traefik` / `traefik-internal-network` to TCP 8443, CoreDNS for the earlier three names, `postgres16` TCP 5432, kube-apiserver TCP 443 and 6443, and `authentik.haynesnetwork.com` TCP 443. It adds DNS `matchName` entries for `openproject-web.frontend.svc.cluster.local` and `dev-env-operator.dev-env-system.svc.cluster.local`, egress to OpenProject web pods on TCP 8443, and egress to pods labeled `app.kubernetes.io/name: dev-env-operator` in `dev-env-system` on TCP 8443. That label and port are the operator HelmRelease service `dev-env-operator` port 8443 and the endpoint selector in `kubernetes/main/apps/dev-env-system/network/app/operator.yaml`. There is no `matchPattern`, no namespace-only operator rule, and no metrics port 8080. The policy selects only `dev-env-v2-portal`. It is not applied.

At the current signed runtime, optional product readers are separate. The OpenProject reader uses the fixed internal origin and project-3 collection query above; it accepts only the paginated HAL contract. The operator reader uses its fixed service origin and calls strict, bounded session summaries; its server accepts only the reserved portal ServiceAccount class, pod-bound identity, and the fixed `dev-agents` namespace. The reader is disabled until the operator is released with `--enable-portal-reader`; no flag or operator HelmRelease is changed by this packet. `OPERATOR_TOKEN` is a short-lived projected ServiceAccount token for audience `dev-env-operator`, not a 1Password value. `OPENPROJECT_TOKEN` is a distinct optional approved project-reader item property. These source constraints do not prove live rights, isolation, or connectivity.

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
| Traefik to BFF | ServersTransport `dev-agents/dev-env-v2-portal` | HTTPS, verify CA, exact serverName | Required isolation in the internal-reader sequence | Template. Serving CA Secret is absent. |
| BFF pod | CNP `dev-agents/dev-env-v2-portal` | pending BFF rules plus exact OpenProject TCP 8443 and operator TCP 8443 | Pending `bff-networkpolicy.yaml` plus this source delta | Staged. Not applied; effective isolation is unproven. |
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

Tom reported `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` saved on `HaynesKube/dev-env`. That status is USER-REPORTED SAVED. It is not SecretSynced and it is not value-verified. This packet does not ask again. The four runtime properties are an inert staged projection. Population and install remain HELD. That projection is not a population request. Earlier handoff records said Tom alone writes these fields. That sentence is a historical handoff convention, not a 1Password technical limitation. The verified route is Tom's secure UI. No approved automated writer for these fields was found. Only a verified already-approved writer or that secure UI may populate them later. This packet does not authorize a new writer.
