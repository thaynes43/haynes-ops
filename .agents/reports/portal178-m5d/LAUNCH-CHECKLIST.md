# Story178 internal reader launch checklist

Source packet: `.agents/reports/portal178-m5d/`. Registration is a later reviewed change. A merged render packet still does not install the portal.

Status values are source or handoff facts. They are not live acceptance.

## Required

| Step | Owner | Status |
| --- | --- | --- |
| Keep this component out of every Flux Kustomization and `kubernetes/` resource list. | M5D `94bffabb-3e1f-47d9-8d6c-370ba5da2ffc` | Required now. This source does not register it. |
| Exact-head independent review of the staged source, then required CI. Do not merge a change that adds a live route, policy, ExternalSecret, or admission object. | Coordinator picks the reviewer. M5D keeps correction custody. | Required before merge. |
| Publish `RUNTIME-CONTRACT.md` and a tested process that reads this mount layout, serves TLS on 8443 with the exact public host, implements `/portal/livez` and `/portal/readyz`, and stays fail-closed while admission is closed. | M6 `8372053b-36b5-4cf2-b2c8-5a7146a42c45` | Required. RUNTIME-CONTRACT.md: absent. Merged assembly is empty. |
| Replace `unregistered.invalid/dev-env-v2-portal:UNPUBLISHED` with the M6 immutable image reference. No digest is invented here. | M6, then M5D source bump | Required before any pod is admitted. |
| Confirm the Traefik 42.1.3 `rootCAsSecrets` PEM key. Supply public serving CA Secret `dev/dev-env-v2-portal-serving-ca` and leaf Secret `dev-agents/dev-env-v2-portal-tls` from an already authorized certificate owner. | Certificate owner and portal owner | Required before route registration. Authority to mint a new issuer is not granted here. |
| Deliver public `ca.crt` for `database/postgres16-ca` with the attestation fields in `DEPLOYMENT-CONTRACT.md`. Set both CA hash annotations to that file SHA-256 in the same change. | Already-authorized certificate administrator, then portal owner | Required before the database mount can start. No PEM is in source. |
| Check consumer ExternalSecret existence and `SecretSynced`, and the approved key names only. | M5D after a reviewed consumer exists | Required later. Population is UNKNOWN. Tom already received the OIDC handoff. No duplicate request. Runtime item fields remain `DB_USERNAME`, `DB_PASSWORD`, `SESSION_AEAD_KEY`, `SESSION_GENERATION`. |
| Append `dev-env-v2-portal` to the two CCNP `NotIn` lists using `ccnp-exclusion.yaml`, after a rendered diff shows every other selector and rule unchanged. | M5D source, separate reviewed registration | Required before the dedicated policy is effective. Not applied. |
| Create ConfigMap `dev-agents/dev-env-v2-portal-admission` once, `state=closed`, through the existing authorized operator route. Record its UID. Replace sentinel `PENDING-CLOSED-BOOTSTRAP` only with that UID. | Authorized operator, then M5D source for the UID pin | Required before Gate A. BFF cannot create it. Not in this render. |
| Apply reviewed SQL `010` and `020`, then `030` with every effective-rights boolean true. Keep the six HBA rejects until that proof. Insert the single runtime hostssl allow only after that proof, before the `host all` catch-all. No migrator allow. | Portal owner using the existing fixed-target helper | Required before database connectivity. Not this packet. |
| Apply the positive database grant, the internal route, and the Tom-only reader admission only after secret sync and the Gate A evidence list in the internal-reader sequence. | Coordinator outcome, M5D infrastructure, M6 process | Required for internal use. Not authorized by merging this packet. |
| Gate A on the exact artifact: invalid OIDC/state/nonce/issuer/audience, Tom issuer plus subject and live active direct Admins, two-principal denial, revocation, logout, database failure, CSRF, hostile output, secret canaries, TLS and backend-bypass negatives, and no public route. Fixture identities only. | M6 and M5D, coordinator acceptance | Required. Not source-verified. |

## Optional

| Step | Owner | Status |
| --- | --- | --- |
| Commands, lifecycle, logs, prompts, embedded terminals or browsers, grant approval UI, a new CA controller, public ingress. | Separate gate | Optional. Not part of this reader. |
| Migration item `DB_USERNAME` / `DB_PASSWORD` and a LOGIN window for `dev_env_v2_portal_migrator`. | Separate review | Optional and unused. Steady state stays NOLOGIN via peer `SET ROLE`. |
| Further shared Authentik log hardening. | Separate shared-system work | Optional. PR4266 and pin PR4270 are accepted. This packet does not reopen them. |

## Unverified inputs

These are explicit and unresolved:

- M6 executable, image repository, digest, probe implementation, and file loader.
- Serving certificate issuer, Traefik CA secret key, and leaf certificate.
- Public PostgreSQL CA bytes, fingerprint, validity, and renewal owner name.
- 1Password item population for both approved items.
- Admission ConfigMap UID and resourceVersion.
- Whether Reloader is installed and selects this namespace. The annotations follow the existing convention; this packet does not measure the controller.
- Effective CCNP selection, DNS resolution, Traefik header trust, and backend bypass. Source does not prove them.
- Tom issuer subject and Admins membership. Private values stay out of source. No group membership is changed.
