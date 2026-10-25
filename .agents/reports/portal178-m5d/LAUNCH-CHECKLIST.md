# Story178 current-runtime launch checklist

This is a source packet only. It remains outside every Flux Kustomization and
does not install a Deployment, Secret, route, policy, admission object, or
credential. “Ready” below means only the cited source or live-read fact; it is
not whole-Story178 acceptance.

## Launch-gate matrix

| Gate | Exact evidence / action | State at 2026-10-25 | Accountable next actor | Scope |
| --- | --- | --- | --- | --- |
| Immutable portal artifact | dev-env `3221ca79f4d7fddf73b0cfafc71a24de28f13663`; Publish `40208871745` terminal SUCCESS 15:54:50Z; portal `sha-3221ca7@sha256:033ff0b611eb15dc000f8d1bc653154f1f3f79d4f477b4eb62ac41d1e666bde2` | Source pin updated and focused-tested; not installed | Root delivery owner after review/CI | This source PR only |
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
