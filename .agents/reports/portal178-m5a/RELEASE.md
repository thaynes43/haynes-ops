# Story178 M5A: internal portal identity foundation and release packet

Marker: `PORTAL178-M5A-CLIENT-GITOPS-20261023-01`. Canonical queue:
[Story178](https://work.haynesnetwork.com/work_packages/178), Epic120 / Saga85.
This is a source and release-readiness packet, not a runtime acceptance receipt.

## Ownership, authorization and state

M5C identity/config successor is V2 `haynes-ops-1023-202021`, managed local branch
`agent/haynes-ops-1023-202021`, adopting public PR4246 and its remote branch
`agent/haynes-ops-1023-185406` after explicit source relinquishment in Story178
journal1122. The old private home/history remain protected and unread. Root assigns
independent native security review of the actual final PR head and coordinates
the release gates; this successor retains correction custody through review/CI
and authorized merge/readback. Grok retains
the separate M4 UX scope. This packet creates no second backlog and does not
repurpose Future grooming Story121.

Tom approved the confidential client and, separately, the exact Tom-only
issuer+immutable-subject **AND** existing `authentik Admins` metadata reader and
internal host below, after reviewed deployment and negative checks. The unchanged
scope needs no repeat approval. Additional security access requires an exact
proposal and its own approval, not invented credentials.

| State | Evidence / condition |
|---|---|
| M3 component source | dev-env PR527 merged as `8b192402b8ec4b564ccab0ee1b9dfdb001f7f213`; supplied handoff, not a runtime artifact |
| M5A source | This PR adds a provider/application, all-policy bindings and explicit staged deny |
| Provider installed | Pending reviewed merge, Flux/blueprint success and selected live metadata readback |
| Secret provisioned | Pending reviewed provider, Tom's direct UI-to-1Password copy, reviewed consumer |
| Runtime built / installed | Not delivered here; entrypoint, production shared store and dedicated readers absent from this scope |
| Internal usable / accepted | Gate A unfulfilled; no route activated |
| Public release | Separate ASVS5 Level 2 gate unfulfilled and out of scope |

Preflight freshly fetched `main` at
`cde225d0937761c4d6a3dd71728199d44682c34a`; worktree was clean, root AGENTS.md
absent, CLAUDE.md / .agents README / HARDENING / relevant rule / blueprint README
read. Open PR file metadata had no portal/Auth path overlap. Read-only deployment
metadata confirmed server and worker images `ghcr.io/goauthentik/server:2026.8.5`
with three ready replicas each. This is not an upgrade.

At preflight the cluster had zero portal-named/referencing ExternalSecrets and
`onepassword-connect` was Ready with vault `HaynesKube`. Root's supplied
18:54:53 UTC readback found zero portal provider/application. This owner has not
independently repeated that authenticated provider lookup: a documented existing
Authentik token-only helper is not available in this pod. No ad hoc key, broad
API/DB credential, raw provider response or secret was retrieved.

## Exact identity configuration

- Provider name and public client ID: `dev-env-v2-portal-internal`; confidential.
- Application slug: `dev-env-v2-portal`.
- Exact strict authorization callback:
  `https://dev-env-v2.haynesops.com/auth/callback`.
- Only grant `authorization_code`; only managed scope mappings `openid` and
  `profile`. No email, offline access, refresh grant, client credentials or
  operator grants. JWT federation provider/source lists are explicitly empty.
- Expected issuer:
  `https://authentik.haynesnetwork.com/application/o/dev-env-v2-portal/`.
- Subject mode `user_uuid`; signing key is existing
  `jwt-signing-key-rsa-longterm`. Shared login/MFA/invalidation flows are not edited.
- Admission uses separate enabled, non-negated bindings to existing active
  username `thaynes` and existing group `authentik Admins`, with
  `policy_engine_mode: all`. Neither binding creates or edits a directory object.
- Additional `dev-env-v2-portal-staged-deny` expression/binding returns false.
  Admission is intentionally denied for everyone until reviewed activation.
  Application is hidden and has no launch URL or outpost.

The BFF must independently pin the exact issuer and **immutable subject**. The
private subject proof stays with root. Its approved SHA256 fingerprint is
`1e1d2e9d3d6f7d59251827a98250802dea327c408c4bae5ae6c3f75aaf0a661d`.
The fingerprint preimage is the lowercase, hyphenated 36-character `user.uuid`,
encoded as UTF-8 with **no trailing newline**. It is not 64-character `user.uid`.
Do not commit its raw value or any directory/group identifiers. Username lookup
in the blueprint is not a substitute for that BFF pin. The supplied 15:27 UTC
directory proof of active Tom/direct Admins membership is historical metadata,
not a token, fresh MFA or logout test.

### Atomicity, references and limits of validation

The actual deployed `/blueprints/schema.json` is titled
`authentik 2026.8.5 Blueprint schema`, SHA256
`b59476bcbaf92dc1a5bd31d98936d44348bc2b048deae7e891227597c74e97e8`.
It matches upstream commit `4ec0d19b98da3c329781894cd550da95d3033954`
(`version/2026.8.5`) byte for byte.

Pinned implementation evidence, also consistent with
[Authentik's blueprint transaction documentation](https://docs.goauthentik.io/customize/blueprints/):

- [Importer apply/validation](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/blueprints/v1/importer.py):
  `apply()` wraps all entries in one database transaction and rolls back on
  validation/save failure. Partial updates preserve omitted attributes, including
  the generated client secret.
- [Find resolution](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/blueprints/v1/common.py):
  missing `!Find` returns None; it does not itself guarantee a hard error.
  [Binding serializer](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/policies/api/bindings.py)
  requires one nonempty policy/user/group. An unresolved required user/group
  therefore invalidates its binding and rolls back this complete import.
- [Policy engine](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/policies/engine.py):
  `all` requires all enabled binding results. User binding compares the resolved
  **effective-policy-user** account PK; group binding uses Authentik membership,
  including inherited groups. MIRROR actors are evaluated as their parent, so an
  actor of Tom can pass both positive bindings while its own OIDC `sub` differs.
  COPY actors can copy Tom's user-bound conjunction onto a different actor identity,
  making the copied all-mode conjunction unsatisfiable. These are not ordinary
  direct-user-only gates. Activation must verify no MIRROR/COPY actors with Tom as
  parent; the BFF's exact issuer + immutable Tom `sub` pin remains mandatory even
  when the application policies pass. Do not authorize from mutable group names
  in the normal profile claim.
  Dynamic-policy cache is not a continuous authorization signal for the BFF.
- [Policy binding model](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/policies/models.py):
  disabling or cascade-deleting a positive binding removes a conjunct and widens
  access. With zero enabled bindings, `core_default_app_access=True` permits all
  authenticated users. Missing Tom/group references also make each blueprint
  reconciliation fail, preventing it from re-creating a deleted deny. Treat a
  blueprint instance `status=error` or binding drift as alert-worthy; do not infer
  denial from YAML presence. At installation assert the **exact** three enabled,
  non-negated, failure-result-false bindings: order 0 staged deny, 10 active Tom,
  20 existing Admins, with no additional bindings. The staged deny remains during
  integration; runtime independently denies unavailable or changed authorization.
- [OAuth2 signer](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/providers/oauth2/models.py):
  `signing_key` is nullable with `SET_NULL`; missing/renamed `!Find` or key deletion
  can silently select HS256 using the client secret. Import/schema success alone
  does not prove the expected RSA signer. Selected readback must resolve a
  **non-null** signer to `jwt-signing-key-rsa-longterm`, compare its expected key
  fingerprint privately, and verify the application's JWKS exposes the matching
  RSA signing key/RS256. The BFF must pin **RS256 only**, deny HS256/algorithm
  confusion and fail unavailable on missing/wrong keys. Never print key material
  from an authenticated certificate/provider response.
- [Authorize PKCE check](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/providers/oauth2/views/authorize.py):
  accepts `plain` and `S256` and permits an absent challenge. A supplied challenge
  is stored on the code;
  [token validation](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/providers/oauth2/token/authorization_code.py)
  verifies it during exchange. There is no provider S256-only switch in this
  schema. The BFF must initiate S256 with state/nonce and reject
  invalid exchanges; do not claim Authentik rejects plain.

The offline guard loads tagged YAML, validates schema with synthetic typed FK
placeholders, enforces exact attribute/metadata/projection allowlists and checks
34 unsafe mutations with a specific expected rejection reason. Programming errors
or unrelated schema failures never count as successful negative checks. The guard
runs in the required Flux Local aggregate. Kustomize rendering verifies actual mounted
content. These checks do **not** resolve live directory objects, run an Authentik
import, or constitute identity integration. Required repo CI and actual native
security review of the final head remain separate gates.

## Secret delivery: only after the reviewed provider exists

These are instructions for **Tom**, to deliver after provider installation and
selected metadata review. Only Tom writes 1Password.

1. In Authentik Admin UI, open the reviewed provider
   `dev-env-v2-portal-internal`. Confirm confidential, exact callback, approved
   scopes, signing key/subject mode, application linkage, all-policy bindings and
   staged deny. Do not copy any raw API response into a receipt.
2. In vault **HaynesKube**, create dedicated item **dev-env-v2-portal** with
   top-level fields:
   - `OIDC_CLIENT_ID` = `dev-env-v2-portal-internal`
   - `OIDC_CLIENT_SECRET` = copy directly from that provider's Admin UI client
     secret field into 1Password.
3. Never paste the secret into agent chat, terminal commands, logs, Git, a PR or
   a tracker. Agents do not retrieve or print it. No new user/group/membership,
   persistent API/DB key or broad dev-env/homepage/OpenProject credentials.
4. Keep [the proposed projection](pending/externalsecret.yaml) unapplied until
   the runtime consumer and namespace are reviewed. It uses existing
   `ClusterSecretStore/onepassword-connect` and projects only those two fields to
   isolated server Secret `dev-env-v2-portal-oidc`. Proposed namespace
   `dev-env-system` is not a settled runtime placement. It is unregistered:
   no Kustomization, HelmRelease, mount or workload references it.
5. At reviewed deployment, verify ESO Ready and only expected key names using
   metadata/conditions, never Secret values. Mount only the isolated server
   consumer. Browser code receives neither this secret nor agent credentials.
   ESO sync and credential removal are not immediate portal-session revocation;
   missing fields/sync errors must keep release closed. Rotation/revocation and
   session invalidation need explicit tested runtime behavior.

Omitting `client_secret` is intentional: Authentik generates it on create and
preserves it on blueprint reconcile. Removing blueprint management does not
revoke a provider or its secret.

## Runtime contract reserved for Gate A

No deployable placeholder, Service, certificate, ingress, DNS or network policy
is activated here. The runtime entrypoint, deployable reviewed image/digest,
production shared session store (atomic one-time state, revocation, expiry and
multi-replica failure behavior), independent authorization and dedicated metadata
projection still require source and negative tests.

Reader boundary, after verified Tom issuer+immutable-subject AND current existing
Admins membership:

| Backend | Allowed resources and selected output |
|---|---|
| OpenProject | GET work packages **120,121,122,123,178** in **haynes-engineering** only. ID, title, status, accountable owner, next actor/action, source freshness and reviewed SHA. No descriptions, journals, attachments or arbitrary fields. |
| Operator | AgentSession GET/list **summary only**, namespace **dev-agents**, immutable catalog-verified **project=dev-env AND repo=dev-env**. Name, UID, phase, provider, model, capabilities, heartbeat/freshness and validated official link. Deny projectless/unverified binding. |

No detail/prompt/log/history/artifact/stream, guessed-resource disclosure,
commands, mutations, lifecycle, grant or approval endpoints. ServiceAccount
TokenReview callers are not human authorization. Existing
`GET /v1/sessions/{name}` exposes prompt and is unsuitable; do not reuse it.
Supplied live `/v1/session-directory` returned **404**; no working summary API is
claimed. A dedicated operator projection and narrow backend trust/identity
contracts are prerequisites. If implementation needs a new security grant,
prepare the exact identity/resource/verb/field/TTL proposal for review; this
packet grants none. Never compensate with broad API or database credentials.

Exact route requirements:

- Host only `dev-env-v2.haynesops.com`, class `traefik-internal`, entrypoint
  `websecure`, TLS `certificate-haynesops`, external-DNS target
  **`internal.haynesops`** only. No public controller, DNS, Tunnel or aliases.
- Actual ingress proxy pods are in `network`, with both
  `app.kubernetes.io/name=traefik` **AND**
  `app.kubernetes.io/instance=traefik-internal-network`.
- Existing shared controller enables `insecureSkipVerify`, cross-namespace
  references and ExternalName services. Leave it unchanged. The portal needs a
  per-backend **verified CA and serverName** ServersTransport, no verification
  bypass, same-namespace backend and explicit scheme/transport association.
- Backend Service, port and certificate are proposals pending a reviewed runtime
  artifact. Restrict ingress by NetworkPolicy to the actual proxy namespace AND
  both pod labels (same peer, not OR). Restrict egress to reviewed destinations;
  test direct backend access, spoofed forwarded identity and host headers,
  external controller and unintended destinations. NetworkPolicy supplements
  independent request authentication/authorization; labels are not human identity.

`max_age=0` and `acr_values` do not prove fresh MFA. RP logout does not prove
Authentik SSO revocation. Local portal revoke/logout/expiry must work independently
and deny changed/unavailable authorization. Observe actual IdP behavior and
record its limits without claiming directory metadata proves any of them.

## Acceptance sequence and evidence checklist

Source merge installs only the denied identity foundation. Runtime activation is
a later reviewed change: explicitly set both deny binding and deny policy entries
to **`state: absent`**, while retaining both positive bindings enabled. Merely
deleting the deny YAML entries leaves the deny in Authentik; disabling/deleting
positive bindings weakens authorization and is never the activation procedure.
Read back the exact two positive bindings and absent deny only at that authorized
Gate A transition. Do not remove a positive binding as an access shortcut.

- [ ] Fresh final-head diff/ownership check; root-assigned **independent native
  security review of actual final SHA**, findings fixed or concretely dispositioned.
- [ ] Required `Flux Local - Success` and `Diff Scope - Success` green on that head.
  Read any emitted advisory findings; record verdict/availability separately.
- [ ] **Before merge** (Flux can apply immediately), use documented token-only
  metadata access to recheck duplicates and required active Tom/group/flows/
  openid/profile/signing key. Stop creation/adoption on mismatch or ambiguity.
  Compare the subject fingerprint using the exact preimage above and expected
  group/key fingerprints from root's private proof. Check no MIRROR/COPY actors
  with Tom as parent; no real user/group/actor edits are authorized here.
  Never emit raw provider GET (it can include client_secret).
- [ ] Merge ready PR within authorized scope. Observe Flux `network/authentik`
  and successful blueprint status/hash; verify only selected provider/application
  identifiers, exact callback/grants, scope names, empty federation inputs,
  subject/issuer, non-null expected RSA signer and matching JWKS RS256,
  hidden application, **exact** order 0/10/20 binding set (enabled=true,
  negate=false, failure_result=false) and false deny expression. Blueprint
  instance must be `successful` with expected `last_applied_hash`; `error`,
  missing/disabled/extra bindings or null/wrong signer block release and require
  an explicit alert/handoff. Verify no shared
  flow/outpost/application changes or session/V1 restarts.
- [ ] Tom completes direct UI-to-1Password copy only after reviewed provider exists.
  Review actual consumer before projecting/mounting the isolated two-field Secret.
- [ ] Reviewed runtime entrypoint/image, shared store, dedicated backend projection
  and per-backend identity/CA/serverName/network controls exist and pass tests.
- [ ] **Invalid OIDC:** bad signature/algorithm/issuer/audience/azp/subject/time,
  nonce/state mismatch or replay, missing/wrong PKCE/verifier, exchange failure,
  extra scopes, callback confusion, malformed/oversized inputs all denied.
- [ ] **Authorization:** two real authorized/unauthorized identities, two projects,
  projectless or unverified sessions, guessed IDs/UID changes, stale/replayed
  authorization and current Admins withdrawal are denied appropriately. Recheck
  existing required bindings; do not mutate real memberships without separate
  authority. Use reviewed fixtures/test identities where needed.
- [ ] **Safe output:** exact field allowlists, output bounds, escaping and secret
  canaries in excluded backend fields; no descriptions/prompts/logs/attachments,
  credentials, arbitrary links or indirect injection reach browser/audit output.
- [ ] **CSRF/session:** login and logout CSRF/origin checks, cookie flags and host
  scope, fixation/rotation, multi-tab/replay, local revoke/logout/expiry and store
  outage/races; actual IdP MFA/re-auth/logout behavior separately observed.
- [ ] **Proxy/isolation:** spoofed identity/forwarded/host headers, untrusted proxy,
  direct Service/backend access, missing/wrong CA/serverName, external controller
  and disallowed egress fail. No permissive transport fallback.
- [ ] **Exposure/operations:** exact internal host/TLS/DNS and no public
  route/Tunnel/alias, bounded request sizes/rates/timeouts and redacted audit
  events. No raw logs or prompts in the portal. Verified rollback/revocation path.
- [ ] Root reviews complete Gate A evidence against the deployed artifact; only
  then activate the exact approved internal route/admission and perform positive
  Tom acceptance plus negative checks. Record built, installed and accepted
  commits/artifacts separately; no public ASVS5 L2 acceptance claim.

Rollback containment: retain/reinstate the explicit deny through GitOps. Unmounting
a blueprint only stops management and **leaves objects behind**. Revocation after
activation must also invalidate local sessions independently; do not alter shared
flows/groups or restart active sessions/V1. If denial or access boundaries block
a check, record exact action/target/reason and denied/pending/uncertain state and
continue unrelated safe work. No bypass, ad hoc credential or silent fallback.
