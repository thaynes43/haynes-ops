# Authentik UserInfo logging candidate

Source and artifact only. This packet does not change
`kubernetes/main/apps/network/authentik/app/helmrelease.yaml` and does not roll
a pod. Root reviews the immutable artifact, then continues the ordinary GitOps
pin. A synthetic canary is not proof that a real user token was logged.

## Held draft, not accepted

This branch adds an unaccepted certifi `2026.10.23` overlay on the same
Authentik `2026.8.5` base and keeps all three `access_token` redactions. It
does not change the live trust store, a Kubernetes pin, or a running pod, and
it does not fix the GPU outage. Official Authentik remains `2026.8.5`.

Certifi `2026.10.19`, already in the accepted image, is the patched release
for GHSA-6q48-6c7w-9gw2 in the curated GitHub advisory. The October 19 and
October 21 remediations stay valid. The `2026.10.23` change removes emSign C1
and C3 under Mozilla Root Store Policy 7.5.3 (Mozilla bug 2083709). That is
root retirement, not a new compromise or CVE. The CA states that non-test TLS
issuance is absent; that is the CA's statement, not compatibility proof for
every Authentik integration. The overlay affects default Python certifi trust
only. It leaves the system store, LDAP trust, Go outposts, and Rust roots
unchanged, and it leaves the unchanged certifi tests bytecode cache in place.

Pull-request CI builds an archive and does not publish or sign it. Repair run
40122430827, job 120956687802, from source
`cb7cd7a36d7f48f6eb07556f9010213fded726e8`, passed structural proof and the
32-case synthetic container canary. Two independently downloaded archives
reproduced the same four identities. Native Sol approved those four values for
held-draft CI expectations only, not for registry publication, deployment, or
signature. `reviewed-digests.json` now holds them:

| Identity | Value |
|---|---|
| Archive SHA-256 | `95466c87de78c7d1387eedd4a1ad9b18991766ebb9fb16f9460ed5b1d9298f09` |
| Image id / config | `sha256:3429fca1edba0aeedab48a9e2668b166cff59545d574aa6d420ad142cff202e7` |
| Buildx manifest | `sha256:749b5a2378c8e2bba220958f15de3ba0b3f38e1947d5ebb89cd1f9b68d5eb11a` |
| Registry digest | not published |

The fresh CI of the commit that records these expectations is still pending.
That run has to report structural proof, canary comparison, expect match, and
qualified all true, with acceptance `REVIEWED`, while signed and published
stay false. Until that run is green, this source is not fully CI-qualified.
The pull-request job uploads `authentik-userinfo-redaction-unaccepted-<commit>`
and `authentik-userinfo-redaction-digests-<commit>` when those files exist.

The accepted registry image
`ghcr.io/thaynes43/authentik-userinfo-redaction@sha256:18fc99f13617a4b83dfc470e4f8cda7ed76cf513c5147c90e1de888838325663`
does not sign this draft and is not overwritten. The main publish job is
unchanged and is not authorized for this draft. No Actions permission was
added.

The sections below keep the historical accepted utils-only artifact and the
future pin procedure. They are not approval to publish or roll this draft.

## What the candidate is

Authentik server and worker stay on chart `2026.8.5`. The image keeps the
installed base `ghcr.io/goauthentik/server@sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5`.
The linux/amd64 manifest inside that index is
`sha256:6b924afb97053a27ca3f42f006c920464fb77763558c72219019365db191827b`
(config `sha256:32dd380d0e766ff6a8100935f957b27574bd6af349832e1f0780c99f963b0204`,
compressed layers 377,501,332 bytes). That stock index is not the live image.
The installed accepted image is the overlaid
`sha256:18fc99f13617a4b83dfc470e4f8cda7ed76cf513c5147c90e1de888838325663`.

The accepted utils-only image's only source change is the [three-call patch](pending/authentik-userinfo-log.patch):
delete `access_token=access_token` from the missing DEBUG, expired DEBUG, and
revoked WARNING calls in `authentik/providers/oauth2/utils.py`. Installed file
`131cfcb81374f9beccb7cad6830fbcc9917f030ea5d3e4cfb26b470db6d6ebcb` becomes
`ae1494c6c145d094fd1ad473517f4c0e5fbb8fdfc7b145c651235f29f1871b97`. Messages,
severity, 401 `invalid_token` denials, and the sanitized suspicious-request
Event stay. That accepted image did not reinstall a dependency and did not
change log level, flow, scope, grant, lifetime, credential, or ingress.

The accepted build context contained only the Dockerfile. That pull-request CI
checked runtime user/entrypoint/cmd/env hash/platform against the base, checked
the new layer was only that Python file, and ran the canary on both images at
DEBUG and INFO. The held draft adds the pinned wheel and installer to the
context and allows only that certifi delta plus the same utils file.
The measured canary envelope is 2 CPU and 2 GiB, the same limits as the
passing disposable proof on this digest. A push to main builds that Dockerfile
again. Publish loads the archive from that same main run and refuses to push
unless its archive SHA-256 and image id match `reviewed-digests.json`. It then
pushes `ghcr.io/thaynes43/authentik-userinfo-redaction:sha-<commit>`, signs the
registry digest, and writes that digest to the job summary. The registry
digest is that job's `RepoDigests` value, not a value stored in Git.

The digests below were measured on run 40060699220 and reproduced on run
40060895887. They are the historical accepted utils-only gate, preserved
unchanged in `accepted-legacy-digests.json`. They are not the current
enforcement gate. The Dockerfile and build context of that accepted build did
not change between those two runs:

| Identity | Value |
|---|---|
| Archive SHA-256 | `fd766ce876e872605eb1b2dcc19fd62697393644c29cd6cebcf77c00493baa00` |
| Image id / config | `sha256:27208b5ce501d57daf558c44730ee02fe0f9a4e1f0bc5bb96fbdf5abf247c870` |
| Buildx manifest | `sha256:d39684927413cf724566352022711c6e8955c98cbe21ddfffeabd5366f18f452` |
| Registry digest | not published |
| New layer | `authentik/providers/oauth2/utils.py` only |

Runtime user, entrypoint, command, environment hash, labels, and linux/amd64
matched the base, and the base rootfs layers were the prefix of the candidate.
Those four historical digests stay in `accepted-legacy-digests.json`. The
current enforcement gate in `reviewed-digests.json` is the certifi candidate
archive, config, image id, and buildx manifest in the held-draft section.
Recording that gate does not overwrite the accepted `18fc` artifact or its
signature. Do not invent a registry digest in the Helm pin. The pin uses the
main publish job's `RepoDigests` value after root accepts an artifact. The
accepted image's signature does not extend to this candidate. Main publication
is not authorized.

## Historical pre-rollout refs

This table is the snapshot from before the accepted utils-only image was
pinned. It is not the live image. Live server, worker, and `web-dist-copy`
resolve to the overlaid
`sha256:18fc99f13617a4b83dfc470e4f8cda7ed76cf513c5147c90e1de888838325663`,
not to the stock official index below.

| Workload | Container | Image in that snapshot | Replicas | Rollout |
|---|---|---|---|---|
| `authentik-server` | `server` | `ghcr.io/goauthentik/server:2026.8.5` → `sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5` | 3 | RollingUpdate maxSurge 25%, maxUnavailable 25% |
| `authentik-server` | `web-dist-copy` | same digest | init | same pod |
| `authentik-worker` | `worker` | same digest | 3 | same strategy |

`init-db`, `web-dist-lower`, and `k8tz` stay on their current images. Outposts
stay on `ghcr.io/goauthentik/%(type)s:%(version)s`. Chart version, resources,
probes, and Helm timeout stay:

- server request 200m / 1Gi, memory limit 2Gi; probes `/-/health/ready/` and `/-/health/live/`
- worker request 200m / 512Mi, memory limit 1536Mi; probe `ak healthcheck`
- startup `failureThreshold` 60; Helm timeout 20m; remediation retries 3

No replica change, no surge change, and no forced restart.

## Future pin, not applied

Chart `2026.8.5` appends `@digest` on the server and worker containers when
`global.image.digest` is set. The `web-dist-copy` override in the HelmRelease
does not. A later commit has to set all three refs to the reviewed registry
digest. Server and worker:

```yaml
global:
  image:
    repository: ghcr.io/thaynes43/authentik-userinfo-redaction
    tag: "2026.8.5"
    digest: sha256:<registry digest from the main publish job>
```

The tag is only the chart's `repository:tag@digest` prefix. The digest is the
pin. `web-dist-copy` must use the same digest suffix the chart uses, in place
of the current tag-only template:

```yaml
image: '{{ default .Values.global.image.repository .Values.server.image.repository }}:{{ default (include "authentik.defaultTag" .) .Values.server.image.tag }}{{- if (default .Values.global.image.digest .Values.server.image.digest) -}}@{{ default .Values.global.image.digest .Values.server.image.digest }}{{- end }}'
```

Leave replicas, resources, strategy, probes, chart version, blueprints, and
admission as they are. The Kyverno verify policy does not list this repository;
this packet does not edit it. The main job still signs with the existing cosign
flags. The namespace has no imagePullSecret today; publication has to be
pullable the same way other `ghcr.io/thaynes43` images already run. Do not add
a pull secret or RBAC.

## Readiness and rollback

The rollback command below is the historical procedure for the accepted
utils-only image, back to the stock official base. It is not the rollback for
this certifi candidate. The candidate stays unpublished. Moving from a future
candidate back to `18fc` would restore emSign C1 and C3 and needs a conscious
trust decision. The stock base restores the UserInfo logging defect.

After root applies the pin, wait for both deployments to report 3 ready and 3
updated on the new digest, with the existing probes. Do not scale down to make
room. Confirm `web-dist-copy` imageID equals the server and worker imageID.

Rollback restores the logging defect. Before restoring the old pin, leave the
portal staged deny and CLOSED admission as they are. Then set server, worker,
and `web-dist-copy` back to:

`ghcr.io/goauthentik/server@sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5`

Wait again for 3 ready server and 3 ready worker on that digest. Do not open
admission as part of the rollback.

## Proof limits

The canary uses opaque and JWT-shaped synthetic noncredentials. Token lookup
and `Model.save` are mocked, database cursors and the network are blocked, and
the real log processors and Event cleansing run. DEBUG must show the branch
text. INFO must hide the two DEBUG lines and still show the revoked WARNING.
The base image must show the synthetic marker in those formatted lines; the
patched image must not. That is test sensitivity, not a production leak. No
production token is retrieved, revoked, or written.
