# Story178 internal reader launch checklist

Source packet: `.agents/reports/portal178-m5d/`. Registration is a later reviewed change. A merged render packet still does not install the portal.

Status values are source or handoff facts. They are not live acceptance.

## Required

| Step | Owner | Status |
| --- | --- | --- |
| Keep this component out of every Flux Kustomization and `kubernetes/` resource list. | M5D `94bffabb-3e1f-47d9-8d6c-370ba5da2ffc` | Required now. This source does not register it. |
| Exact-head independent review, then required CI. Merging applies the registered OpenProject certificates, 8443 listener, and network edits. It does not register the portal Deployment, portal certificates, or the OpenProject ExternalSecret. | Coordinator picks the reviewer. M5D keeps correction custody. | Review is required before merge. This correction pass does not merge. |
| Follow approved-readers-06, dev-env PR 545 head `0e93e2ec3aa972456cc07d3ad821a6487db8506e`. `12ca8fe6` is not the current head. `integration-fixes-02/RUNTIME-CONTRACT.md` and `canonical-item-01/RUNTIME-CONTRACT.md` are historical. | M6 remains the sole dev-env writer. | Current contract read. Pagination correction is still pending. That head has no digest. |
| Pin `ghcr.io/thaynes43/dev-env-portal:sha-5b39ebb@sha256:b6491747e5730a7a220ac0b79b07493399449425a0acb1e177b52efa00e4233a` from the completed Publish (portal) job for merge `5b39ebb13f3651c8aa741a2b4358bc1eecf6dd30`. | M5D source | Pinned in the unregistered packet. Not admitted and not installed. |
| Supply public serving CA Secret `dev-agents/dev-env-v2-portal-serving-ca` under `tls.ca` or `ca.crt`, and leaf Secret `dev-agents/dev-env-v2-portal-tls` whose certificate has DNS SANs `dev-env-v2.haynesops.com` and `dev-env-v2-portal.dev-agents.svc.cluster.local`. | Certificate owner, then portal owner | Required before route registration. The CA Secret is in the ServersTransport namespace. No key or certificate is generated here. |
| Deliver public `ca.crt` for `postgres16-rw`, plus public peer files for admission, the issuer, OpenProject, and the operator. Set each CA hash annotation to that file SHA-256 in the same change. | Already-authorized certificate administrator, then portal owner | Required before the CA mounts can start. No PEM is in source. |
| Check the rendered OIDC ExternalSecret for `SecretSynced` and the two approved property names only. | M5D after a reviewed consumer exists | Required later. OIDC on `HaynesKube/dev-env` is USER-REPORTED SAVED, not SecretSynced and not value-verified. No duplicate OIDC request. Runtime properties are HELD. |
| Append `dev-env-v2-portal` to the two CCNP `NotIn` lists using `ccnp-exclusion.yaml`, after a rendered diff shows every other selector and rule unchanged. | M5D source, separate reviewed registration | Staged in the registered session policies. Not merged and not applied. |
| Point the unregistered CNP at OpenProject web pods on TCP 8443 and the operator on TCP 8443. Project `OPERATOR_TOKEN` in a directory mount, audience `dev-env-operator`, lifetime 3600. Keep `OPENPROJECT_TOKEN` optional. Leave `config/session-bindings.json` absent. | Coordinator supplies the token value through the existing item. M5D does not write it. | Source staged. The portal packet stays unregistered, so this egress is not applied. |
| Issue OpenProject portal certificates in `openproject-portal-pki` and wait for Certificates `openproject-portal-ca` and `openproject-portal-api` Ready before the OpenProject HelmRelease reconcile. | M5D source | Staged. Not applied. After the certificates are Ready, the web strategy Recreate deletes the single replica before its replacement starts, so browser 8081 and agent 8082 stop for that replacement. A missing certificate does not start that replacement. |
| At registration, apply unregistered `dev-env-v2-portal-policy` and verify its default deny before `dev-env-v2-portal` can create a pod. Keep both unregistered while inputs are absent. | M5D source | Not registered. One combined render does not prove isolation. The public route stays off. Admission stays CLOSED. Gate A stays required. |
| After a verified Publish of dev-env PR 545, pin that digest and set `--enable-portal-reader` with `--session-namespace=dev-agents`. | M5D after the signed image exists | Not this pass. No digest is invented. The operator rollout keeps `maxUnavailable: 0` and does not own session pods. |
| Create ConfigMap `dev-agents/dev-env-v2-portal-admission` once, `state=closed`, through the existing authorized operator route. Record its UID. Add `config/admission-uid` only with that UID. Do not write sentinel `PENDING-CLOSED-BOOTSTRAP` into the file. | Authorized operator, then M5D source for the UID pin | Required before Gate A. BFF cannot create it. The file is absent from this render. |
| Apply reviewed SQL `010` and `020`, then `030` with every effective-rights boolean true. Keep the six HBA rejects until that proof. Insert the single runtime hostssl allow only after that proof, before the `host all` catch-all. No migrator allow. | Portal owner using the existing fixed-target helper | Required before database connectivity. Not this packet. |
| Apply the positive database grant, the internal route, and the Tom-only reader admission only after secret sync and the Gate A evidence list in the internal-reader sequence. | Coordinator outcome, M5D infrastructure, M6 process | Required for internal use. Not authorized by merging this packet. |
| Gate A on the exact artifact: invalid OIDC/state/nonce/issuer/audience, Tom issuer plus subject and live active direct Admins, two-principal denial, revocation, logout, database failure, CSRF, hostile output, secret canaries, TLS and backend-bypass negatives, and no public route. Fixture identities only. | M6 and M5D, coordinator acceptance | Required. Not source-verified. |

## Optional

| Step | Owner | Status |
| --- | --- | --- |
| Commands, lifecycle, logs, prompts, embedded terminals or browsers, grant approval UI, a new CA controller, public ingress. | Separate gate | Optional. Not part of this reader. |
| Migration item `DB_USERNAME` / `DB_PASSWORD` and a LOGIN window for `dev_env_v2_portal_migrator`. | Separate review | Optional and unused. Steady state stays NOLOGIN via peer `SET ROLE`. |
| Further shared Authentik log hardening. | Separate shared-system work | Optional. PR4266 and pin PR4270 are accepted. This packet does not reopen them. |


## Future field source and secure handoff

Canonical item: existing `HaynesKube/dev-env`. No new item. No `dataFrom`. Consumers name individual properties. Unrelated existing fields stay on the item and are not mounted.

OIDC, already handed to Tom, not requested again. Tom confirmed both properties are saved. Status is USER-REPORTED SAVED. It is not SecretSynced and it is not value-verified.

- `OIDC_CLIENT_ID` = `dev-env-v2-portal-internal`
- `OIDC_CLIENT_SECRET` = the installed Authentik provider secret, not rotated and not pasted here

Runtime, same item, explicitly HELD. Do not tell Tom to populate these. No credential or key is generated, and no new item is requested.

- `DB_USERNAME` must equal the accepted SQL role `dev_env_v2_portal_runtime`. The published parser compares the mounted file with that role.
- `DB_PASSWORD` must match the reviewed CNPG basic-auth provisioning mechanism in the inert `dev-env-v2-portal-runtime-db` projection. The writer is not authorized.
- `SESSION_AEAD_KEY` must be standard base64 (`StdEncoding`, strict) of exactly 32 bytes, with no whitespace. That is the published `canonicalKey` and `base64.StdEncoding.Strict` check.
- `SESSION_GENERATION` must be 32–128 characters from letters, digits, `-`, and `_`. That is `validGeneration`.

`config/subject` source path is verified: installed provider 174 `user_uuid` maps to the active named user's `User.uuid`. The value is not materialized. No raw subject and no hash are in this repo. It is not a 1Password property. The file is absent. This packet does not invent `PORTAL_SUBJECT` or a new field.

Earlier handoff records said Tom alone writes these fields. That sentence is a historical handoff convention, not a 1Password technical limitation. The verified route is Tom's secure UI. No approved automated writer for these fields was found. Only a verified already-approved writer or that secure UI may populate them later. This packet does not authorize a new writer. The write is not a chat paste, a generated value in git, or a second 1Password item. Source projections of `dev-env` already use twelve other property names; none collide with the six portal names above. Fields on the item that this packet does not name are preserved by omission. The rendered OIDC consumer selects `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`. The rendered runtime and database projections select only their named properties and stay unpopulated.

## Unverified inputs

These are explicit and unresolved:

- A live pull or running pod of `ghcr.io/thaynes43/dev-env-portal:sha-5b39ebb@sha256:b6491747e5730a7a220ac0b79b07493399449425a0acb1e177b52efa00e4233a`. The source pin is not admission.
- The serving leaf and the public CA bytes. Required DNS SANs and the Traefik secret keys `tls.ca` or `ca.crt` are recorded above. No material is in source.
- Probe success against a running pod. The source sets Host through `httpHeaders`. That is not a live probe and not certificate verification.
- Public PostgreSQL CA bytes and the four peer CA files, each with fingerprint, validity, and renewal owner name.
- `SecretSynced` for the two OIDC properties. Tom's save is USER-REPORTED SAVED, not SecretSynced and not value-verified. Runtime properties are an inert projection. Population and install are HELD.
- `config/subject`. Source path is provider 174 `user_uuid` to the active named user's `User.uuid`. The value is not materialized. Not an item property. The file is absent. `PORTAL_SUBJECT` is not used.
- `OPENPROJECT_TOKEN` on `HaynesKube/dev-env`. The owner records it through Tom's secure UI after creating `dev-env-portal-reader` with only `view_project` and `view_work_packages` on project 3. No automated writer and no token is minted here. Sessions and Tasks are independent, so a missing OpenProject token does not by itself block startup. Historical published `5b39ebb` still couples the old metadata files. That coupling is not the current contract. `config/session-bindings.json` is not a production requirement.
- The live operator policy in `dev-env-system` already admits TCP 8443 from every `dev-agents` pod. This packet does not edit that policy.
- Admission ConfigMap UID and resourceVersion. `config/admission-uid` stays absent until that UID is recorded.
- Whether Reloader is installed and selects this namespace. The annotations follow the existing convention; this packet does not measure the controller.
- Effective CCNP selection, DNS resolution, Traefik header trust, and backend bypass. Source does not prove them.
- Tom issuer subject and Admins membership. Private values stay out of source. No group membership is changed.
