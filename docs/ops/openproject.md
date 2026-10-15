# OpenProject — deployment and acceptance

Status: proposed, not deployed or ready. No source work is imported. The separately
requested haynesnetwork migration follows only after login, API and persistence
acceptance. The disposable proof namespace was deleted without starting a Pod.

## Deployment

OpenProject Community 17.9.2, digest-pinned official slim image, bjw-s app-template
5.3.0. One web replica (Recreate) runs vendor migration/seeding as an init
container; a separate vendor background worker waits for the database schema.
Stock Memcached supplies the shared cache. A stock NGINX sidecar runs documented
`auth_request` configuration; no custom authentication service or application patch.

A dedicated CNPG PostgreSQL 17 singleton uses 20Gi Ceph RBD; attachments use a
20Gi CephFS RWX claim. Both stateful inventories carry prune protection. Do not
rename/move the Flux Kustomization or database/PVC without an explicit retention
cutover: inventory pruning can otherwise remove the claim and leave Pods Pending.
The singleton tolerates node-recovery downtime; backups are the recovery path,
not a claim of database HA. Capacity inspection found approximately 14TiB raw
Ceph free, with 3.8TiB maximum available in the relevant replicated pools.

`frontend/openproject` is the reconcile target. Merge only after the approved
identity/credential prerequisites below exist, required CI passes and relevant
review findings are resolved. Never deploy reachable `admin/admin`; the seeded
administrator has a generated password and is locked from creation.

## Private authentication and API

`https://work.haynesops.com` uses internal Traefik, the existing
`certificate-haynesops`, and UniFi internal DNS → 192.168.40.203. No public DNS,
Cloudflare route, external ingress or forwarded port is part of this change.

Humans authenticate through the existing Authentik/MFA path. The new single-app
proxy provider initially admits only the existing `authentik Admins` group;
Tom approved that exact initial group on 2026-10-15. No memberships change.
NGINX captures identity only from the successful outpost response, validates the
login shape, overwrites inbound assertions and appends its protected shared
secret. The app's optional-header mode is disabled, login is required and user
registration is disabled. Human application records come from verified LDAP
lookup and do not need a separate human password.

Dedicated proxy and LDAP outposts belong only to this app. Their resources are
ClusterIP, have no ingress or Kubernetes token mount, and use the internal
Authentik service for control traffic with its normal browser URL. Existing
outpost provider lists and human flows are unchanged. The new LDAP service bind
uses an app-password-only flow and only the `openproject-ldap` service account may
access that LDAP application. Its full-directory-search permission is scoped to
this single provider. The OpenProject search filter further selects active
members of `authentik Admins`. No LDAP attribute grants OpenProject administrator
status automatically. Account rename/reassignment and application-role changes
need deliberate handling; the LDAP/proxy key is the controlled username (`cn`).

LDAP uses verified TLS on the dedicated service DNS name. A generated certificate
named `OpenProject LDAP` must cover
`ak-outpost-openproject-ldap.network.svc.cluster.local`. Only its public trust
certificate enters the application's 1Password item. The private key remains in
Authentik's normal protected store/outpost lifecycle. Do not disable certificate
verification as a shortcut. Supply `givenName` and `sn` LDAP attributes for the
admitted users when absent, derived from their existing display names; this is an
explicit identity-metadata prerequisite, not a change to login identifiers.

The agent API is `http://openproject-web.frontend.svc.cluster.local:8082/api/v3/`
inside the cluster. Cilium admits it only from the existing dev-env workload.
Its stock NGINX listener preserves Bearer Authorization, strips cookies/identity
headers and rejects non-API paths. OpenProject performs token authentication and
project permissions. The direct application listener is bound to Pod loopback.
The additive dev-env API/DNS Cilium policy changes no existing Pod/config/session
and must not restart Chapter7 or modify its coordinator's resources.

Browser access tokens currently expire after five minutes. Measure cached-session
behavior; do not claim directory disable or group removal immediately revokes an
existing proxy/application session. Test logout, account locking, group removal,
proxy invalidation and API-token revocation before declaring ready. If the owner
requires a shorter revocation bound, set and verify that bound before use.

## Required action-time approvals and provisioning

The deployment authorization covers ordinary GitOps authoring, PRs and validation.
Tom approved the following exact scope on 2026-10-15. The 1Password Connect
token is read-only (item creation returned HTTP 403); populate the dedicated
item securely in 1Password before merge. No other credential flow is authorized:

1. Manually create only `HaynesKube/openproject` securely in 1Password. The
   existing Connect token cannot create items. Required fields:
   `DB_PASSWORD`, `HEADER_SECRET`, `BOOTSTRAP_ADMIN_PASSWORD`, and
   `LDAP_BIND_PASSWORD` must each contain 64 random hex characters;
   `SECRET_KEY_BASE` must contain 128 random hex characters; `LDAP_CA_CERT`
   contains the public PEM certificate below. No raw value is printed or
   committed. ExternalSecrets deliver app, CNPG basic-auth and the one-time
   LDAP provisioning Job's dedicated bind Secret. No unrelated application
   credential is reused.
   Reserve `API_TOKEN` (`opapi-` followed by 64 random hex characters) in the same
   item for the later restricted bot setup. Its delivery is held: no bot token
   mount or dev-env Secret-read grant is deployed by this PR. Choose an approved
   token-only consumer before enabling the bot; do not use exec into the web
   container as the bot's credential path. No dev-env restart is authorized.
2. Apply only `96-openproject-private.yaml`: new app/proxy/LDAP providers, dedicated
   service bind flow/account, provider-scoped search permission, app policies and
   two dedicated outposts. Generate the LDAP service app-password to match the
   dedicated 1Password field and its provider certificate through stock Authentik
   interfaces. Add only the required name attributes if absent. Do not modify
   existing human identities' passwords, identifiers, groups or MFA flows.
3. Apply the exact new private ingress/internal DNS and Cilium policies in this
   PR. The only admitted browser source is internal Traefik; the only bot source
   is dev-env. No public path or general unauthenticated API bypass is authorized.
4. Deliver existing approved `aws`/`volsync-hayesops` backup credentials to this
   application's backup controllers only, writing a dedicated Barman server prefix
   `openproject-postgres17-v1` in the existing `cnpg-haynesops` bucket and a distinct
   restic suffix `/openproject-assets`. No bucket/credential/retention change for
   other applications. Provision one named non-admin app writer and one reviewer
   role after startup, with API material stored through 1Password; workers cannot
   approve their own work. Tom approved `openproject-agent`, limited to the managed project, without
   deletion, approval or acceptance rights. Store its API token as `API_TOKEN`
   in the same dedicated item; never reuse a human credential.

After blueprint and ExternalSecret reconciliation, run the one-time in-cluster
provisioning Job with `scripts/openproject/provision-ldap-bind.py`. It consumes
only `dev/openproject-ldap-bind` by Secret reference, uses the existing dev-env
service account's approved `pods/exec` capability, and supplies the password on
stdin over the verified Kubernetes API to stock `ak shell`. It creates only the
dedicated app-password, prints metadata only, and refuses mismatched tokens.
Neither existing Authentik nor dev-env may read Secrets directly; no new RBAC or
server restart is needed. Verify the bind before accepting human login. LDAP
fails closed while the token is absent. Remove the one-time Job and its scoped
API-only policy after completion.

Verify the public LDAP certificate before storing it in 1Password:

```sh
openssl verify -CAfile ldap-ca-public.pem \
  -verify_hostname ak-outpost-openproject-ldap.network.svc.cluster.local \
  ldap-ca-public.pem
```

The private key stays in Authentik's protected certificate store. Track the
certificate expiry and renew both the Authentik certificate and 1Password public
trust field together before expiry; never disable verification as a workaround.

These actions are prerequisites, not permission to mark the service ready. The PR
must remain unmerged until the requisite approvals and 1Password fields/certificate
exist. The service must not be exposed temporarily to work around a credential or
identity failure. All lasting cluster settings remain GitOps-managed.

## Backup and restore

CNPG performs daily base backups plus WAL archiving to its dedicated Barman
prefix, retaining 30 days. Attachment snapshots are sent nightly through VolSync
to the app's separate restic repository, retaining ten daily versions and three
days of snapshots. PVC prune protection is not a backup.

Database and attachments are separate backup streams. Recovery must choose a
compatible point and verify every referenced attachment; no atomic cross-store
snapshot is claimed. For the initial acceptance drill, take the backups while the
unused app is quiescent, restore into isolated resources, and verify hierarchy,
metadata, source mapping, attachment bytes/hash and actor history. Before user
work, qualify the operating procedure for concurrent attachment deletion or use a
maintenance backup window. Never declare restore verified from a backup Job's
exit status alone.

## Acceptance before readiness and migration

- Confirm actual image/digest, no Enterprise token, generated locked bootstrap
  admin, healthy database/cache/web/worker and working persistent storage.
- Fresh Authentik login → named user via real LDAP provisioning; second access
  reuses the record, no local password prompt, no anonymous project visibility.
- Forged/missing/duplicate identity, Alice-to-Bob spoof, direct Pod listener,
  unapproved Pod, external ingress and wildcard tunnel paths fail. Verify logout
  and the documented revocation/cache boundary.
- Named restricted bot: real HTTP create/read/update/comment/status/parent/link/
  attachment/activity and stale `lockVersion` conflict tests. Create only a
  disposable fixture project; delete only those fixture items with authorized
  disposable scope. Prove a worker cannot approve itself.
- Configure Saga → Epic → Story, separate dependency graph, repository/source
  provenance, actor/conversation/revision fields and states Draft, Ungroomed,
  Needs answer, Needs approval, Approved, In progress, In review, Accepted, Paused.
  Generic parent links need an authoring/migration grammar check. Basic board
  dragging does not change status. Actual voice decisions use the real bot actor
  and conversation reference, never fabricated Tom authorship or self-approval.
- Verify data/attachment survival across a normal restart, backup age/health and
  the isolated restore above. Return observed results, not a running-service claim.
- No bulk migration here. The subsequent haynesnetwork agent pins 078–089 source
  commits, preserves original Markdown/IDs/dependencies/pauses/provenance and runs
  idempotent upserts. Imported work never authorizes execution. Preserve #991/#992
  pauses and the Chapter7 boundary.

The reviewable work-model contract is `scripts/openproject/work-model.json`.
It is not a bootstrap completion receipt. In particular, native status workflow
validation needs live HTTP negative tests for creation in Approved/Accepted and
simultaneous type/status changes, as well as ordinary transitions. Do not claim
that worker restrictions are enforced from workflow rows or a hidden UI control
alone. If a native API bypass exists, stop readiness and report the exact result;
do not silently weaken the requirement or patch application source.
