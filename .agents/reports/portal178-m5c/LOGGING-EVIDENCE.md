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
