# Authentik UserInfo logging gate — bounded evidence

2026-10-23, Story178. No real token retrieved, printed or revoked. No user/group,
shared login flow, logging configuration or runtime changed.

Pinned Authentik `4ec0d19b98da3c329781894cd550da95d3033954`:

- [OAuth utility](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/providers/oauth2/utils.py)
  emits `access_token` at DEBUG for missing/expired tokens and WARNING for revoked
  tokens, then creates the suspicious-request Event on the revoked path.
- [Logging chain](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/lib/logging.py)
  adds level/logger/context/process/tenant/timestamp/exception data and formats
  JSON or console output. No token-key cleansing processor appears in that chain.
  Current GitOps `helmrelease.yaml` sets `authentik.log_level: debug`.
- [Event.new/from_http/save](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/events/models.py)
  sanitizes and cleanses context, adds path/method/cleansed query/user agent/user
  metadata, and saves/logs the event. It does not copy the Authorization header
  in the inspected `from_http` code.
- [Event model conversion](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/events/utils.py)
  serializes token/provider models to app/model/pk/name, not all model fields.
  [AccessToken.__str__](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/providers/oauth2/models.py)
  returns provider/user identifiers without the raw access token. These are source
  observations, not an executed revoked-token Event persistence test.

One live read-only negative probe used a deliberately synthetic, nonexistent
bearer at the fixed HTTPS UserInfo endpoint. Response **401**. A bounded server-log
read filtered only that canary and produced one structured DEBUG event
`Token does not exist`; its **access_token field equalled the synthetic input**.
Only selected booleans/event metadata were retained in the receipt. No foreign
log line or actual token was copied into the report.

This proves the deployed nonexistent-token path exposes the presented synthetic
input in the real log pipeline. It does not claim an executed revoked-token
WARNING/event test. The shared source chain plus the canary is sufficient to
retain a concrete live-UserInfo release hold: do not send production portal tokens
through this path until a reviewed narrowly scoped token-field redaction and
disposable canary proof cover missing/expired/revoked logs and persisted events.
Raising the global log level alone would leave the WARNING path. No blanket
logging change or shared IdP restart is approved or performed by this evidence.

## Exact installed-image disposable proof and minimal proposal

Hosted [run40024521259](https://github.com/thaynes43/haynes-ops/actions/runs/40024521259)
PASS at `714e6108946fcd3c37ec710d8fbf04130ac56cc6`. Uses the exact deployed
image digest `602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5`,
network disabled, read-only filesystem, dropped capabilities, two CPUs and
synthetic unsaved models. Installed OAuth utility SHA256 must equal pinned
upstream `131cfcb81374f9beccb7cad6830fbcc9917f030ea5d3e4cfb26b470db6d6ebcb`.

The actual decorated UserInfo view is called for missing/expired/revoked tokens,
with both opaque and JWT-shaped **noncredentials**. Token lookup and Event.new
are mocked; the latter still invokes actual model cleansing and HTTP context
serialization, with synthetic tenant/brand defaults and Model.save mocked.
Database cursors are blocked. Actual log processors/renderers run and each
expected branch message must be observed; merely suppressing output cannot pass.

| Phase | Cases | HTTP | Token visible in log | Token in Event context/response |
|---|---:|---|---|---|
| Installed baseline | 6 | all401 | all6 true | all false |
| Minimal proposal in memory | 6 | all401 | all6 false | all false |

All four revoked cases exercise the actual Event HTTP serialization path; no
database save occurs. Initial run40023963984 was **inconclusive/failed**, because
the fixture blocked a tenant-retention default query. That failed run was not
counted as proof. Synthetic tenant/brand defaults fixed the fixture, without
weakening token-log/Event assertions or enabling database/network access.

The [three-call proposal](pending/authentik-userinfo-log.patch) removes only the
`access_token=access_token` argument from missing/expired DEBUG and revoked
WARNING calls. Message, severity, denial response and sanitized suspicious Event
remain intact. The harness verifies the proposed diff exactly before applying it
only in process memory and rebuilding the actual view decorators.

**Deployment proposal for root:** review this minimal diff/regression evidence,
then separately authorize an Authentik image build/rollout retaining2026.8.5 and
these installed dependencies, with only this Python source delta. Both shared
server and worker workloads currently have3 replicas; an image rollout affects
shared IdP service and requires its own authorized operational scope. No image
tag, HelmRelease, shared log level, flow or pod is changed by this packet. After
authorized rollout verify exact digest and safe synthetic missing-token pipeline,
then complete disposable IdP Gate A token/revocation proof. This proposal does
not extend the portal-only grant to shared Authentik changes.

## F3 binding portal contract and remaining shared risk

Root selected the narrow contract: the BFF **never calls `/revoke` or
`/introspect`**, including logout, cleanup and error paths. It uses only the
exact session's current UserInfo and durable local logout, with short-lived
access-token storage. GoM6 owns the implementation/regression; root coordinates
its independent evidence. This packet neither edits Go nor expands OAuth scope.

Pinned [introspection.py](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/providers/oauth2/views/introspection.py#L63)
and [token_revoke.py](https://github.com/goauthentik/authentik/blob/4ec0d19b98da3c329781894cd550da95d3033954/authentik/providers/oauth2/views/token_revoke.py#L59)
also log unknown raw tokens at DEBUG. They are **outside this three-call patch**;
shared-system exposure remains, no global fix is claimed. No broader patch,
shared-image build/rollout or restart is authorized by the portal DB/auth grant.
Live UserInfo still waits for separately reviewed authorized three-call remediation
and synthetic full-pipeline proof. The installed-image12-case test bypasses Django
middleware and Go router logging; Model.save is mocked. It proves no event DB
persistence, signal/notification behavior or live revoked/expired path. The only
live safe probe was the synthetic missing-token401 case. Real production tokens
and audit database writes are excluded from this evidence.
