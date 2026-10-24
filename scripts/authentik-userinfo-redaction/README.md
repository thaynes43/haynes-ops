# Authentik UserInfo token-argument candidate

Immutable build of Authentik server `2026.8.5` with one source change: remove
`access_token=access_token` from the missing and expired DEBUG calls and the
revoked WARNING call in `authentik/providers/oauth2/utils.py`. Messages,
severity, denial responses, and the sanitized suspicious-request Event stay.

The base is `ghcr.io/goauthentik/server@sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5`
(source file `131cfcb81374f9beccb7cad6830fbcc9917f030ea5d3e4cfb26b470db6d6ebcb`,
patched file `ae1494c6c145d094fd1ad473517f4c0e5fbb8fdfc7b145c651235f29f1871b97`).
The linux/amd64 manifest is `sha256:6b924afb97053a27ca3f42f006c920464fb77763558c72219019365db191827b`
and its compressed layers are 377,501,332 bytes. The disposable canary uses the
same 2 CPU and 2 GiB envelope that already passed on that digest.

Pull requests build an OCI/docker archive and do not push. A push to `main`
loads that archive, pushes `ghcr.io/<owner>/authentik-userinfo-redaction:sha-<commit>`,
and signs the registry digest. The registry digest is the publish job's
`RepoDigests` value. It is not invented in source.

The HelmRelease pin is not part of this build. See the release packet.
