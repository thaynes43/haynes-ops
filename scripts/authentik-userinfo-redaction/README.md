# Authentik UserInfo candidate

Held draft of Authentik server `2026.8.5`. One image layer contains two
reviewed changes:

- remove `access_token=access_token` from the missing and expired DEBUG calls
  and the revoked WARNING call in `authentik/providers/oauth2/utils.py`
  (`131cfcb81374f9beccb7cad6830fbcc9917f030ea5d3e4cfb26b470db6d6ebcb` to
  `ae1494c6c145d094fd1ad473517f4c0e5fbb8fdfc7b145c651235f29f1871b97`)
- replace certifi `2026.10.19` with the pinned wheel `2026.10.23`
  (`e9b54de76e530bb6cf0ccd036787103acb58fd42ba930ea8bf948ddbaa2f61d1`)

Messages, severity, denial responses, and the sanitized suspicious-request
Event stay. The base image, user, entrypoint, command, environment, labels,
and `org.opencontainers.image.version` stay. Upstream certifi commit
`b60466c9f2882d9cf088943c650b0faac06c080c` changes `VERSION` and removes two
roots. That is the bundle delta this overlay carries. It is not a claim that a
private key was compromised. The October 19 and October 21 chain work does not
include this October 23 root removal.

The wheel file is `certifi-2026.10.23-py3-none-any.whl` from
`https://files.pythonhosted.org/packages/32/d1/a3652fe2c19134d0d15922177821d863cbd7e842d53a21bbe7e8e3a1b5ac/certifi-2026.10.23-py3-none-any.whl`.
The build mounts it read-only, checks that digest, and runs the overlay with
`--network=none`. The installed distribution must be `2026.10.23` for both
`certifi.__version__` and `importlib.metadata`. The CA proof is the old bundle
`50f79f98dac192b20dbf73f35c56f0b0c557ab267ff5199d1117cc1180b70785` to the new
bundle `2ef52ad56b4803e264045c915e55c236cbfa045b84b81efefc218a19eb44bd2b`, with
the DER fingerprint set losing only
`125609aa301da0a249b97a8239cb6a34216f44dcac9f3954b14292f2e8c8608f` and
`bc4d809b15189d78db3e1d8cf4f9726a795da1643ca5f1358e1ddb0edc0d7eb3`.

The base is `ghcr.io/goauthentik/server@sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5`.
Official Authentik is still `2026.8.5`; this draft does not use a newer base.
The linux/amd64 manifest is `sha256:6b924afb97053a27ca3f42f006c920464fb77763558c72219019365db191827b`
and its compressed layers are 377,501,332 bytes. `SOURCE_DATE_EPOCH` stays
`1792582296`. The disposable canary uses the same 2 CPU and 2 GiB envelope.

`reviewed-digests.json` is still the acceptance gate and still names the
accepted utils-only build. This draft's archive SHA is not known until the
hosted build writes `candidate-digests.json`. That report is `UNACCEPTED`
until those four keys are replaced from the report's actual bytes. Do not
invent them. `accepted-legacy-digests.json` keeps the accepted build and the
registry digest `sha256:18fc99f13617a4b83dfc470e4f8cda7ed76cf513c5147c90e1de888838325663`.
That signature does not sign this draft.

Pull requests build an OCI/docker archive and upload
`authentik-userinfo-redaction-unaccepted-<commit>` plus the digest report.
They do not push, publish, or sign. The publish job runs only on a `main`
push, only for an archive matching the reviewed gate, and keeps its existing
`packages` and `id-token` permissions. This draft is not authorized for that
job. The HelmRelease pin is not part of this build. See the release packet.
