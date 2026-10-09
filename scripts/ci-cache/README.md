# Exact public CI image cache

The copy-writer and native-scanner profiles verify the complete canonical image
set before using Google's documented DockerHub cache. Every cached raw index must
hash to its literal canonical digest and contain exactly one linux/amd64 child;
the child's size, raw digest and manifest schema must also match. Only an exact
HTTP 404 routes the entire profile to unchanged canonical DockerHub references.
Other transport, authorization, digest or schema failures stop the build.

The builder is part of both profiles. Its canonical Renovate-owned FROM asset is
`buildkit/Dockerfile`; it pins the same default BuildKit digest that successful
canonical native run 37993022596 and copy run 37993022576 pulled on 2026-10-09:
`sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea`
(BuildKit v0.33.1). The asset supplies builder metadata.
The verified whole-profile route supplies the structured setup-buildx
`driver-opts` image input, before that builder starts. No tag-only fallback or
alternate builder is allowed. PostgreSQL, Python, native Kavita, SDK and all full
application/native tests retain their existing pins and checks.

Finite fake-HTTP controls cover exact digest/platform custody, canonical source
parsing, whole-profile 404 routing, fatal failures and truthful action outputs.
Run serially under `nice -n 19`; never CPU burners, wide parallel runs or test loops.
