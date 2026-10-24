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
and `org.opencontainers.image.version` stay. The inspected base config image
id must be `sha256:32dd380d0e766ff6a8100935f957b27574bd6af349832e1f0780c99f963b0204`.

Certifi `2026.10.19` already fixes GHSA-6q48-6c7w-9gw2. The curated GitHub
advisory names that release as patched. The October 19 and October 21
dependency remediations stay valid. Certifi commit
`b60466c9f2882d9cf088943c650b0faac06c080c` then changes `VERSION` and removes
the emSign C1 and C3 roots. That removal is dual-purpose root retirement under
Mozilla Root Store Policy 7.5.3, Mozilla bug 2083709. It is not a new
compromise and not a new CVE. The CA states that it issued no non-test TLS
server certificates, no S/MIME certificates, and no cross-sign from another
NSS root. That statement is attributed to the CA. It is not proof that every
configured Authentik integration is compatible with the smaller bundle.

The overlay changes the default Python certifi bundle used by the shared
Requests session and by HTTPX's default SSL context. It does not change the
system CA store, LDAP `ldap3` trust, Go outpost transports, or Rust
`webpki-roots`. A configured `verify` path, `REQUESTS_CA_BUNDLE`,
`SSL_CERT_FILE`, or disabled verification selects another trust source. No
target was probed.

The removed tree is the installed `certifi-2026.10.19.dist-info` directory,
including `INSTALLER` and `REQUESTED`, and the main `certifi/__pycache__`
directory (`__init__`, `__main__`, and `core` bytecode). The unchanged
`certifi/tests/__pycache__` files stay. This draft does not remove every
`.pyc` in the image.

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
hosted build writes `candidate-digests.json`. Structural proof and the canary
are separate fields. `qualified` stays false, and acceptance stays
`UNACCEPTED`, until the structural proof, the four reviewed pins, and the
canary comparison all pass. A pin mismatch remains unaccepted even if the
structural proof and the canary pass. Do not invent the four digests.
`accepted-legacy-digests.json` keeps the accepted build and the registry
digest `sha256:18fc99f13617a4b83dfc470e4f8cda7ed76cf513c5147c90e1de888838325663`.
That signature does not sign this draft. Returning from this draft to that
image would put C1 and C3 back into Python certifi. The stock official base
restores the UserInfo logging defect and is not the rollback for this draft.

Pull requests build an OCI/docker archive and upload
`authentik-userinfo-redaction-unaccepted-<commit>` plus the digest report.
They do not push, publish, or sign. The publish job runs only on a `main`
push, only for an archive matching the reviewed gate, and keeps its existing
`packages` and `id-token` permissions. This draft is not authorized for that
job. The HelmRelease pin is not part of this build. See the release packet.

## Certifi pin maintenance

Renovate's existing Authentik extractor still reads only the `Authentik server
2026.8.5` comment and the pinned `ghcr.io/goauthentik/server` digest in this
Dockerfile. That extractor is unchanged. It does not see this certifi wheel,
so the wheel pin is not covered by automatic dependency extraction. This file
is the visible record. Do not add a broad Renovate ignore to hide that gap,
and do not add a new extractor or trust-activation job here.

The upgrade owner, during ordinary dependency qualification, checks official
certifi releases, the curated GitHub advisory, and CA retirement sources.
After a duplicate search, the owner records a follow-up on the existing
canonical story and proposes a held source change. That change updates the
exact wheel version, hash, file and DER expectations, tests, and reviewed
artifact pins from a real build. Requalification has to show the imported
version, the installed distribution version, absence of the old dist-info and
the main bytecode cache, the unchanged tests bytecode cache, the exact root
additions and removals, the preserved redaction, layer and runtime identity,
and the CI artifact's provenance and signature limits. Nothing in that process
activates trust or edits a live store.

A later Authentik base bump does not remove this overlay by itself. Compare
the new base's certifi distribution and root set, and the redaction behavior,
before changing the candidate. Keep an explicitly compatible overlay and
requalify it, or remove the overlay only when that accepted base already
supplies the intended certifi trust state and the three redactions still hold.
Old hash and layer checks stay fail-closed. Do not bypass them to make a base
bump pass. Root owns monitoring. No recurring automation is added here.
