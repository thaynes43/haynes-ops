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

Pull-request CI builds an archive and does not publish or sign it. The four
keys in `scripts/authentik-userinfo-redaction/reviewed-digests.json` still
identify the accepted utils-only build below, so the new archive fails that
gate until those keys are replaced from the hosted report's actual bytes. The
report is written as `UNACCEPTED` before that failure. The pull-request job
uploads `authentik-userinfo-redaction-unaccepted-<commit>` and
`authentik-userinfo-redaction-digests-<commit>` when those files exist. The
draft archive SHA is the `archiveSha256` field of that report. It is not
stored in Git yet.

The accepted registry image
`ghcr.io/thaynes43/authentik-userinfo-redaction@sha256:18fc99f13617a4b83dfc470e4f8cda7ed76cf513c5147c90e1de888838325663`
does not sign this draft. The main publish job is unchanged and is not
authorized for this draft. No Actions permission was added.

The sections below describe the accepted utils-only artifact and the future
pin procedure. They are not approval to publish or roll this draft.

## What the candidate is

Authentik server and worker stay on chart `2026.8.5`. The image keeps the
installed base `ghcr.io/goauthentik/server@sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5`.
The linux/amd64 manifest inside that index is
`sha256:6b924afb97053a27ca3f42f006c920464fb77763558c72219019365db191827b`
(config `sha256:32dd380d0e766ff6a8100935f957b27574bd6af349832e1f0780c99f963b0204`,
compressed layers 377,501,332 bytes). Live server, worker, and `web-dist-copy`
on 2026-10-24 all resolved to that index digest. Each deployment is 3/3 ready.

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
40060895887. The Dockerfile and build context did not change between them:

| Identity | Value |
|---|---|
| Archive SHA-256 | `fd766ce876e872605eb1b2dcc19fd62697393644c29cd6cebcf77c00493baa00` |
| Image id / config | `sha256:27208b5ce501d57daf558c44730ee02fe0f9a4e1f0bc5bb96fbdf5abf247c870` |
| Buildx manifest | `sha256:d39684927413cf724566352022711c6e8955c98cbe21ddfffeabd5366f18f452` |
| Registry digest | not published |
| New layer | `authentik/providers/oauth2/utils.py` only |

Runtime user, entrypoint, command, environment hash, labels, and linux/amd64
matched the base, and the base rootfs layers were the prefix of the candidate.
Those four digests remain the enforcement gate in `reviewed-digests.json`.
The held draft's Dockerfile is not that build, so its hosted archive is
expected to miss the gate until the four keys are replaced from reviewed
bytes. Do not invent a registry digest in the Helm pin. The pin uses the main
publish job's `RepoDigests` value after root accepts an artifact. The accepted
image's signature does not extend to the held draft.

## Current effective refs

| Workload | Container | Image today | Replicas | Rollout |
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
