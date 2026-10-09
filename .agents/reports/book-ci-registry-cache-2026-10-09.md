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

Primary instructions: [Google public Docker Hub cache](https://docs.cloud.google.com/artifact-registry/docs/pull-cached-dockerhub-images),
[Docker BuildKit mirror configuration](https://docs.docker.com/build/ci/github-actions/configure-builder/#registry-mirror).
