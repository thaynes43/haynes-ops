# Book CI public registry cache correction

The unchanged pinned Python and Kavita Docker Hub bases returned HTTP 429 before
the LIVE host, Normal rehearsal and native projection CI reached their tests.
One explicitly authorized retry repeated the Python failure; no further retry
loop or weaker gate is permitted.

Use Google's documented public Docker Hub cache, keeping the exact canonical
Dockerfile digests. A bounded runner preflight must first hash the raw cached
index, require its unique linux/amd64 descriptor, and hash that exact child
manifest. The platform descriptor is authenticated by the unchanged index hash;
this does not claim an independent configuration-blob execution check.
Wrong, missing, ambiguous, oversized or redirected metadata refuses. Receipts
contain public digests and platform metadata only. No proof is claimed until
the actual runner preflight succeeds.

The copy workflow checks Python plus the unchanged PostgreSQL16 digest. BuildKit
uses the documented docker.io mirror configuration; the PG fixture uses the
same verified digest at mirror.gcr.io directly. The native workflow checks its
exact Kavita base before the same full SDK/native build and self-tests. No runner
daemon, pod egress, Dockerfile, runtime module, credential, test, publication or
signature gate changes. Cache absence fails visibly; it does not select another
image. Native cache absence requires a separate reviewed next option.

The actual advisory found that a duplicated Python pin would drift on Renovate
updates. Derive all three cache pins from their canonical literal Dockerfile
FROM or PG workflow reference instead. Require exactly one expected repository
with an explicit digest; tag-only, interpolated, malformed or ambiguous sources
refuse. Normal digest upgrades retain one source of truth and the same raw
index/platform proof. This correction does not freeze normal upgrades.

Finite offline fixtures cover valid index/child/config custody and malformed or
conflicting digest/platform/transport evidence. Necessary tests run serially at
nice19; no CPU burners, stress or repeated/wide test loops. This CI fetch change
grants no production runtime, hold, Job, baseline, library or COPY authority.

Local offline evidence: nine custody/closure cases passed in 0.008s and the added
HTTP-category/deadline case in 0.003s; seven native scope/admission cases passed
in 0.001s, all serially under nice19. Shared verifier changes now force native
source validation instead of being mistaken for a production-only upgrade.
Actual cache index/platform availability remains unproved until this PR's runner
preflights succeed. A cache miss or authentication/transport category will be
reported as its exact numeric HTTP status without a response payload or URL.

Initial runner evidence at `8ff35d78`: copy run37991423157 verified Python index
`f85c5697…a55d2` → amd64 `cfe2e24a…90ca9` and PostgreSQL index `ca0bd484…641d`
→ amd64 `75adc2a6…dc166`, then passed the unchanged full build and all copy suites.
Native run37991423274 verified Kavita index `ca6af7a1…da7982` → amd64
`5bbb42f8…1cf48`, then passed SDK/native compilation and actual apphost guards.
Independent initial receipt `266352657188fe09be6460dbd99b1398fcd91a73cd0360972d87d8c6e3b48e0e`
is superseded for source approval by the canonical-parser advisory correction.
Three focused canonical-change/refusal controls passed in 0.005s under nice19.
The corrected exact head still requires its own CI and independent/advisory read.

Primary instructions: [Google public Docker Hub cache](https://docs.cloud.google.com/artifact-registry/docs/pull-cached-dockerhub-images),
[Docker BuildKit mirror configuration](https://docs.docker.com/build/ci/github-actions/configure-builder/#registry-mirror).
