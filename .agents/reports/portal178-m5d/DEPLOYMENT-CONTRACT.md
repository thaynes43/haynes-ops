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

RUNTIME-CONTRACT.md: absent

The first packet checked `/home/dev/work/orders/portal178-runtime-assembly-20261024/RUNTIME-CONTRACT.md` and it was absent. This continuation checks `/home/dev/work/orders/portal178-runtime-assembly-20261024/canonical-item-01/RUNTIME-CONTRACT.md`. Merged `NewRuntimeAssembly` still returns an empty `AssemblyConfig` and answers every path with unavailable. Independent portions below use the accepted constructors and the existing pending manifests. They do not claim the Go process reads these files yet.


## Canonical 1Password item

Supersession, 2026-10-24. The canonical item is existing `HaynesKube/dev-env`. Prior instructions to use or create `dev-env-v2-portal` or `dev-env-v2-portal-runtime` are superseded. Those names remain in historical reports and in this paragraph so the earlier instruction stays visible. They are not remoteRef keys. Provider id `dev-env-v2-portal-internal`, application slug, callback, Kubernetes object names, and mount paths are unchanged. Credentials are not rotated.

Every portal remoteRef is an individual `property` on `key: dev-env`. There is no `dataFrom` and no whole-item extract. Unrelated fields already stored on the item are not projected.

Source metadata under `kubernetes/` already projects these properties from `key: dev-env`: `CIGAR_JOURNAL_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLOUDFLARE_PERSONAL_DNS_TOKEN`, `CURSOR_MEATBALL_KEY`, `DEV_ENV_PUSHOVER_TOKEN`, `HW_SSH_PRIVATE_KEY_B64`, `OMNI_OPERATOR_KEY`, `OMNI_SERVICE_ACCOUNT_KEY`, `PROXMOX_OPERATOR_TOKEN_ID`, `PROXMOX_OPERATOR_TOKEN_SECRET`, `SSH_CA_PRIVATE_KEY_B64`, `SSH_CA_PUBLIC_KEY`. None of those names collide with `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `DB_USERNAME`, `DB_PASSWORD`, `SESSION_AEAD_KEY`, or `SESSION_GENERATION`. This list is source projection metadata, not a secret read and not a claim that the item has no other unreferenced fields.

`OIDC_CLIENT_ID` value remains the public client id `dev-env-v2-portal-internal`. `OIDC_CLIENT_SECRET` is the existing installed Authentik provider secret. Root already instructed Tom to put only those two properties on `HaynesKube/dev-env`.

Runtime properties stay on the same item and are HELD until provisioning and the runtime parser match. Do not populate them yet. Future format, still unresolved where noted:

| Property | Future source | Format known now | Unresolved |
| --- | --- | --- | --- |
| `OIDC_CLIENT_ID` | Existing item `dev-env` | Public value `dev-env-v2-portal-internal` | Whether Tom has saved it. UNKNOWN. |
| `OIDC_CLIENT_SECRET` | Same item, installed provider secret | Existing secret, not rotated | Population UNKNOWN. Value is never read here. |
| `DB_USERNAME` | Same item, later reviewed handoff | Intended username `dev_env_v2_portal_runtime` when provisioning matches | HELD. Do not write it yet. |
| `DB_PASSWORD` | Same item, later reviewed handoff | Not generated here | HELD. Encoding and writer are not authorized now. |
| `SESSION_AEAD_KEY` | Same item, later reviewed handoff | Prior contract said canonical base64 of 32 bytes | HELD until M6 parsing matches that format. |
| `SESSION_GENERATION` | Same item, later reviewed handoff | Prior contract said 32–128 characters aligned with the fence | HELD. No value is generated. |

M6 contract path checked: `/home/dev/work/orders/portal178-runtime-assembly-20261024/canonical-item-01/RUNTIME-CONTRACT.md`.

RUNTIME-CONTRACT.md: absent

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
| ConfigMap | dev-agents | dev-env-v2-portal-config | Non-secret accepted settings, including admission UID sentinel `PENDING-CLOSED-BOOTSTRAP`. |
| ConfigMap | dev-agents | dev-env-v2-portal-postgres-ca | Public CA input. `ca.crt` is absent. Annotation `dev-env.haynesops.com/ca-sha256=UNVERIFIED`. |
| ExternalSecret | dev-agents | dev-env-v2-portal-oidc | 1Password item `dev-env`, properties `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` only. Kubernetes name unchanged. |
| ExternalSecret | dev-agents | dev-env-v2-portal-runtime | Same item `dev-env`, four runtime properties only. Population HELD. Kubernetes name unchanged. |
| ExternalSecret | database | dev-env-v2-portal-runtime-db | Same item `dev-env`, `DB_USERNAME` and `DB_PASSWORD` only, `kubernetes.io/basic-auth`, `cnpg.io/reload=true`. Population HELD. |
| CiliumNetworkPolicy | dev-agents | dev-env-v2-portal | Copy of the pending BFF policy. |
| IngressRoute | dev | dev-env-v2-portal | Internal host only. |
| ServersTransport | dev | dev-env-v2-portal | Backend TLS verification for this service only. |

Store reference for every ExternalSecret: `ClusterSecretStore` `onepassword-connect`. No new SecretStore, no `dataFrom`, no migration item, and no field outside the lists above.

The Deployment uses ServiceAccount `dev-env-v2-portal`, which is already registered by `portal-witness.yaml`. This packet does not emit another ServiceAccount, Role, or RoleBinding. `automountServiceAccountToken` is false. A projected token and the namespace `kube-root-ca.crt` ConfigMap are mounted at `/var/run/portal/api` so the approved named-object GET can be exercised. That mount is not a new RBAC grant.

Secret and CA material are files, not environment variables. The process environment must not set `PGSERVICE`, `PGSERVICEFILE`, `PGPASSFILE`, `PGSSLCERT`, `PGSSLKEY`, `PGSSLROOTCERT`, `PGSSLCRL`, or `PGSSLCRLDIR`. The container has no `env`, `command`, or `args`.

Mounts:

| Volume | Source | Mount | Keys |
| --- | --- | --- | --- |
| config | ConfigMap `dev-env-v2-portal-config` | `/etc/portal/config` | accepted non-secret keys |
| oidc | Secret `dev-env-v2-portal-oidc` | `/etc/portal/oidc` | `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` |
| runtime | Secret `dev-env-v2-portal-runtime` | `/etc/portal/runtime` | `DB_USERNAME`, `DB_PASSWORD`, `SESSION_AEAD_KEY`, `SESSION_GENERATION` |
| postgres-ca | ConfigMap `dev-env-v2-portal-postgres-ca` | `/etc/portal/postgres/ca.crt` | `ca.crt` required and currently absent |
| serving-tls | Secret `dev-env-v2-portal-tls` | `/etc/portal/tls` | `tls.crt`, `tls.key`; Secret is not created here |
| api | projected SA token plus `kube-root-ca.crt` | `/var/run/portal/api/token`, `/var/run/portal/api/ca.crt` | token lifetime 3600 seconds |

Image: `unregistered.invalid/dev-env-v2-portal:UNPUBLISHED`. There is no digest. Replace this only with the immutable reference M6 publishes in `RUNTIME-CONTRACT.md`.

Probes, proposed by this deployment contract and unmet by merged Go:

- startup and liveness: HTTPS GET `/portal/livez` on port `https`
- readiness: HTTPS GET `/portal/readyz` on port `https`

Kubelet probe success is not certificate verification and is not Gate A. M6 must implement those paths so they can return success while every reader route stays fail-closed, and so they do not inherit the public-host check that rejects kubelet requests. Until that process exists, these probes do not make a pod Ready.

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
| BFF pod | Secret `dev-agents/dev-env-v2-portal-runtime` | mount four runtime keys | Existing item `HaynesKube/dev-env` | Staged ExternalSecret. Population HELD. |
| CNPG `postgres16` | Secret `database/dev-env-v2-portal-runtime-db` | username and password | Pending `role-externalsecrets.yaml` | Staged. Migrator stays NOLOGIN with no mounted credential. |
| Traefik internal | IngressRoute `dev/dev-env-v2-portal` | Host `dev-env-v2.haynesops.com`, websecure, existing reflected certificate | Approved internal host | Staged. No public route and no reflector edit. |
| Traefik to BFF | ServersTransport `dev/dev-env-v2-portal` | HTTPS, verify CA, exact serverName | Required isolation in the internal-reader sequence | Template. Serving CA Secret is absent. |
| BFF pod | CNP `dev-agents/dev-env-v2-portal` | ingress and egress above | Pending `bff-networkpolicy.yaml` | Staged copy. |
| BFF pod | CCNPs `dev-env-session-platform` and `dev-env-session-web` | remove this name from the additive allows | Required before the dedicated policy is effective | Review template only. Live policies unchanged. |
| portal roles | `postgres16` `pg_hba` | six reject lines | PR4265 `cluster16.yaml` | Unchanged. No hostssl allow. |
| certificate owner | ConfigMap `dev-agents/dev-env-v2-portal-postgres-ca` key `ca.crt` | public certificate only | `PORTAL-PUBLIC-CA-OPERATOR-HANDOFF-20261024.md` | Input prepared. No PEM stored. |
| `flux-system/kustomize-controller` | ConfigMap update after registration | existing static write | Handoff names that publisher | Registration is still closed. That identity is not CA provenance. |
| authorized operator | ConfigMap `dev-agents/dev-env-v2-portal-admission` | create once, data `state=closed` | Witness grant. BFF and recovery cannot create it. | Not in this render. |

## Public CA version, hash, and reload

The ConfigMap is the only public trust input for `postgres16-rw.database.svc.cluster.local`. It currently has `provenance: absent` and no `ca.crt`. The Deployment mounts `ca.crt`, so the kubelet cannot start the pod while the key is missing.

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

1Password population is UNKNOWN for the two OIDC properties on existing item `dev-env`. Runtime properties on that same item are HELD and are not a population request. Absence of the ExternalSecrets does not show whether Tom has written the OIDC fields. This packet does not ask again.
