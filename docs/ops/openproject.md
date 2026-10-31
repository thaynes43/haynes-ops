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

## Authenticated browser access and private API

`https://work.haynesnetwork.com` follows the established external ingress pattern:
external-dns creates the proxied hostname toward `ingress-ext.haynesnetwork.com`,
the existing tunnel's domain rule delivers to external Traefik, and the existing
`certificate-haynesnetwork` covers TLS. Tom explicitly approved this Internet
reachability on 2026-10-15 at 21:03 UTC so lab users can work without VPN. It
supersedes the earlier LAN/VPN-only `haynesops.com` direction for this app.
No tunnel, wildcard-route or shared gateway configuration is changed. Only this
exact Host rule reaches the browser gateway, which checks Authentik before any
application content. The Rails backend, database, LDAP and bot API stay private.

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
   Add `API_TOKEN` (`opapi-` followed by 64 random hex characters) in the same
   item. It is delivered only to the dedicated `dev/openproject-agent-credentials`
   helper, which has no service, network access, Kubernetes token or other secret.
   `scripts/openproject/agent-api.py` runs inside existing dev-env and captures
   only that token file through its existing approved `pods/exec` capability,
   in process memory, then authenticates to the fixed private API origin. It
   follows no redirects and never prints or stores the token. No dev-env
   Secret-read grant, mount/env change or restart is required. Do not exec into
   the application web container to obtain the bot credential.
2. Apply only `96-openproject-private.yaml`: new app/proxy/LDAP providers, dedicated
   service bind flow/account, provider-scoped search permission, app policies and
   two dedicated outposts. Generate the LDAP service app-password to match the
   dedicated 1Password field and its provider certificate through stock Authentik
   interfaces. Add only the required name attributes if absent. Do not modify
   existing human identities' passwords, identifiers, groups or MFA flows.
3. Apply the exact new external browser ingress/DNS and Cilium policies in this
   PR, approved by Tom at 21:03 UTC. The only admitted browser source is external
   Traefik; the only bot source remains dev-env. The Internet path must pass
   Authentik/MFA and the unchanged `authentik Admins` policy. No anonymous app
   content, public bot route or general API authentication exemption is allowed.
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
provisioning Job via `KUBECONFIG=… python3 scripts/openproject/launch-ldap-bind-job.py`.
The Job runs `scripts/openproject/provision-ldap-bind.py`. It consumes
only `dev/openproject-ldap-bind` by Secret reference, uses the existing dev-env
service account's approved `pods/exec` capability, and supplies the password on
stdin over the verified Kubernetes API to stock `ak shell`. It creates only the
dedicated app-password, prints metadata only, and refuses mismatched tokens.
Neither existing Authentik nor dev-env may read Secrets directly; no new RBAC or
server restart is needed. Verify the bind before accepting human login. LDAP
fails closed while the token is absent. Remove the one-time Job and its scoped
API-only policy after completion:

```sh
kubectl -n dev delete job,ciliumnetworkpolicy openproject-ldap-bind-init
```

The Job uses the existing dev-env operator authority within the approved dedicated
OpenProject provisioning scope; it creates no service account, Role or binding.
Before readiness, remove `openproject-ldap-externalsecret.yaml` from its GitOps
resources and delete the file through a reviewed follow-up PR. Reconcile dev-env
and verify the ExternalSecret and its owned Secret are absent. The authoritative
bind credential remains in 1Password and Authentik; do not delete either. A future
explicit rotation can temporarily reintroduce the same dedicated delivery flow.

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
  unapproved Pod and alternate/wildcard Host paths fail. Unauthenticated Internet
  requests get only the Authentik sign-in path, never work content. Verify logout
  and the documented revocation/cache boundary.
- Named restricted bot: real HTTP create/read/update/comment/status/parent/link/
  attachment/activity and stale `lockVersion` conflict tests. Create only a
  disposable fixture project; delete only those fixture items with authorized
  disposable scope. Worker comments and imports never count as an owner's approval
  decision (the old "prove a worker cannot approve itself" test is superseded, see below).
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
It is not a bootstrap completion receipt.

**Superseded 2026-10-19 (Tom, work model of 2026-10-16/19): no status firewall.** The
earlier requirement for live negative tests of worker creation/transition into
Approved/Accepted, and to stop readiness if a native API bypass exists, is withdrawn.
It is not a preferred permanent human-only lifecycle. Grooming and authorizing *new*
scope stays with Tom/Meatball; an agent marking its own already-authorized story complete
once its actual acceptance criteria are met is the intended path (see the `opwork`
section). Still required: reading the real read-back state, never claiming enforcement
from workflow rows or a hidden UI control, and honest reporting of what the API allows.
Observed 2026-10-19: the existing native workflow denies `Approved` and `Accepted` to the
agent role (HTTP 422). That is an **access gap and a held decision for Tom** (whether and
how agents may self-accept), not something agents work around; no role or workflow change
is made here.

## Initial rollout recovery

The first cold start left five empty application tables and an empty migration
ledger. A separate empty database loaded the pinned vendor `db/structure.sql`
successfully (217 tables, 257 migration versions, zero users/work packages). The
failed original database was preserved as `openproject_partial_20261015`, with
connections disabled; the successful vendor-loaded database was renamed to the
configured `openproject`. No database or data was deleted. Remove that retained
partial database only with explicit cleanup approval.

The worker now waits for a nonempty migration ledger using a read-only `psql`
query before booting Rails' vendor wait script. This prevents the Rails migration
checker from creating its metadata tables during initial SQL schema import.
Helm gets a 15-minute initial startup window. VolSync's local cache explicitly
uses ReadWriteOnce, independently of the attachment mover's CephFS RWX mode.

The LDAP provisioning Job uses the digest-pinned existing dev-env tooling image
and kubectl's POST/SPDY exec transport to run stock `ak shell`. The Python
WebSocket exec path required GET permission that the existing SA does not have;
no new RBAC was granted. Verify `create pods --subresource=exec`, rather than the
ambiguous `create pods/exec` command spelling, when checking this authority.

## Agent backlog CLI (`opwork`) — Story97 slice, 2026-10-19

`scripts/openproject/opwork.py` is the one shared tool Claude Code and Codex sessions use
for the central backlog (project `haynes-engineering`, Saga=whole project, Epic=usable
deliverable, Story=session-sized outcome). It wraps `agent-api.py` (same private API, same
in-memory token, no delete, no new credential/role/RBAC) and adds no server, daemon or
approval layer. Tom and Meatball groom scope; the routine lifecycle writes are the agent's.

```bash
opwork.py search "text" [--status S] [--type T] [--parent ID] [--source-key K] [--all] [--page N]
opwork.py show 97 --history --relations          # read-only, includes comment history
opwork.py claim 97 --note "starting" [--assignee me]  # Execution owner = this session, In progress
opwork.py comment 97 "progress"                  # attributed; `-` reads stdin
opwork.py status 97 "In review" --note "PR #1"
opwork.py complete 97 --evidence "tests ..."     # evidence + Accepted when the workflow allows
opwork.py create --epic 95 --origin 97 --source-key followup:97:topic --subject "..."
opwork.py relate 130 97 --type relates
opwork.py checkpoint 97 "state" | pending | flush  # tracker unreachable: local record, replay
```

- **Session identity** (Execution owner + the `**[session]**` comment prefix): `OPWORK_SESSION`,
  else `<OPWORK_AGENT or agent> | worktree <name> | branch <branch>`. All agents share the
  `openproject-agent` login, so the prefix, not the OpenProject author, says who acted.
  Never write as Tom. `OPWORK_PROJECT` overrides the project.
- **Search first**: `create` reuses any item with the same `--source-key` (matched client-side,
  because the API offers no custom-field filter) and stops with exit 4 on a similar subject
  under the Epic unless `--confirm-new`. New items are filed Ungroomed under the Epic, with a
  `relates` link to the originating Story and the creator in Decision provenance. A create is
  never retried: if the answer is lost, the source key is searched and the result reported.
- **Conflicts**: every write sends `lockVersion`; a stale one (409), another owner, or an
  explicit hold or pause (`Needs answer`, `Needs approval`, `Paused`) is re-read and refused (exit 3), never
  overwritten. `--force` is a takeover/override for an explicitly authorized case only.
- **Read back**: success is printed only after a read back. A write missing on read-back is
  UNCONFIRMED (exit 6); an unavailable connection/read-back is exit 5.
  `show --history` marks any truncated text; `--full` returns it whole. `claim --assignee me` also sets the
  native assignee (opt-in; never Tom automatically; the session owner field is kept).
- **Workflow completion**: `complete` preflights the form and records the evidence. If
  `Accepted` is not initially allowed, it moves the item to `In review` when permitted,
  re-checks the form with the read-back lock, and continues to `Accepted` when the workflow
  allows it. Otherwise, exit 7 reports the recorded evidence, actual read-back state and
  allowed next statuses, or the form re-check failure with instructions to inspect the item
  instead of repeating `complete`. The 2026-10-19 observation that this role could not reach
  `Accepted` was state-specific, not a permanent limit. Native workflow rules still apply.
- **Transient reads**: GET retries connection refused, host/network unreachable, timeout,
  connection reset, a temporary DNS failure (`EAI_AGAIN`), an incomplete response body, and
  HTTP 502/503/504 on the same URL, origin and credential. The retry budget is about 120
  seconds including attempts (each timeout is at most 30 seconds and bounded by the remaining
  budget; no attempt starts with less than 1 second left); backoff starts at 1 second,
  doubles and caps at 16. HTTP 401/403, all other 4xx and non-transient errors stop without
  retry. Redirects remain disabled. Each attempt that will be retried, and a call that ends
  as unavailable, emits one bounded stderr diagnostic with UTC time, method, fixed
  destination host, category and errno; an ordinary answer (including an expected 404, 409
  or 422) prints none, and nothing carries exception text or request/response secrets.
- **Tracker unavailable**: a clear `TRACKER UNAVAILABLE` error (exit 5). Bounded authorized work
  continues. POST/PATCH are never automatically retried, and DELETE remains forbidden.
  A failed mutation may have landed: its outcome is **UNCONFIRMED**, never invented success.
  Notes are saved under `~/.local/state/opwork/pending/` (`OPWORK_STATE_DIR`; directories
  0700, files 0600, written atomically) with their exact body and original dedupe token.
  `pending` lists them with their token; a corrupt record is reported and skipped.
  A note whose POST returned 2xx but was not visible on read back (or whose read back
  failed after the 2xx) is saved UNCONFIRMED with that fact (and the activity id when the
  response had one). After recovery, `flush` checks history for the token: present means the
  record is removed; absent for a record with a recorded 2xx means it is **kept for manual
  review and never reposted** (inspect with `show ID --history`). Only a record with no
  recorded 2xx is posted by `flush`, and only when absent from complete history; it is read
  back before the record is removed, and a 2xx it gets that stays invisible is recorded so
  later flushes cannot repost it. Incomplete history keeps an unseen note for manual review.
  If the tracker drops during `flush`, that record is reported UNCONFIRMED, the lines
  for notes already flushed are printed, later records are not attempted, and the
  exit is 5. Legacy local checkpoints still use their existing deferred envelope. Failed
  status/create/claim/complete/relation commands retain their original command intent and
  any existing note key/body; `flush` keeps these "for manual inspection (show/search before
  repeating; note token T may have landed)" and never replays them. No sync daemon or
  additional queue.
- **Networking**: v1 (dev/dev-env) reaches the API today. v2 session pods need the separate,
  held Cilium policy (egress + frontend ingress, TCP 8082); until applied v2 gets exit 5.
- **Tests**: `python3 -I -B scripts/tests/openproject/test_opwork.py -v` (offline fake API:
  pagination, claims, conflicts, attribution, uncertain create, outage). These are mocked
  tests; the live fixture acceptance is recorded in the story result, not by CI.
