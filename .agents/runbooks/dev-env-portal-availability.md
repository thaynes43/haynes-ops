# Portal availability (OP280, 2026-10-30)

This is the portal-only implementation slice of OpenProject OP280, Epic103 /
Saga85. Tom authorized reviewed routine non-destructive replica deployment at
03:13 UTC. It authorizes no node maintenance, user-session restart, credential,
network, admission, keeper or operator change. OP280 remains open for keeper,
storage and recovery acceptance.

## Replica contract and prerequisites

The deployed portal source is dev-env `3cb19139fad2b0e3e2d5c09c6f6ebcdaa831b615`.
Its required CI passed, including the disposable PostgreSQL integration gate:
[run 40988713602](https://github.com/thaynes43/dev-env/actions/runs/40988713602).
`web/portal/session/postgres_integration_test.go` exercises two independent pools:
one-use login consumption, shared sessions/revocation and concurrent send receipts.
This is synthetic database evidence, not real browser OIDC acceptance.

Both replicas use the same runtime Secret reference for SESSION_AEAD_KEY and
SESSION_GENERATION, the same OIDC and serving TLS references, and the fixed
Postgres read-write endpoint. No migration runs at portal startup. Each process
has two database connections maximum (four steady-state; six for a nominal
three-process surge). Terminating pods can overlap beyond replicas + maxSurge;
these connection counts are not hard rollout maxima. The existing runtime role
connection limit must also be checked against overlap before deployment.
No HOME, history or pairing volume is mounted. Launch stays disabled in
`config.yaml`: its process-local dispatch mutex requires separate qualification
before enabling launch with multiple replicas. Send/history remain configured
exactly as before.

Two replicas have required hostname anti-affinity; a third host is needed for a
surge with maxUnavailable 0 / maxSurge 1. The PDB minAvailable 1 constrains
voluntary evictions only, not involuntary loss or Deployment rolling updates.
Neither setting proves a safe host shutdown or storage tolerance.

At 03:19 UTC all nine nodes were Ready without pressure conditions. Effective
allocated requests were calculated as the maximum of regular-plus-restartable-init
requests and each init phase, plus Pod overhead, excluding terminal pods. The
portal has no init containers, overhead, PVC, node selector or tolerations.
One extra steady replica requests 25m CPU / 64Mi, limits 200m / 256Mi, with 16Mi
emptyDir. The nominal three non-terminating replicas request 75m / 192Mi; terminating
processes can temporarily add to that usage. Off-maintenance workers
w02 and w03 had respectively 1.71 / 2.92 CPU, 11840 / 32847Mi and 36 / 42 pod
slots free by requests. Other eligible hosts also had room; w04 had a NoSchedule
taint in that snapshot and is not relied on. Node metrics showed low utilization.
No namespace quota was returned; the LimitRange only supplies a default CPU
request, already explicitly overridden by the portal.

The live route is the internal Traefik IngressRoute in namespace dev, to the
portal Service in dev-agents on HTTPS 8443 using its existing ServersTransport.
There is no sticky-session requirement. Service and network/exposure stay fixed.
The current pod proves that this template/image was admitted previously;
listing newer Kyverno policy APIs was forbidden and was not bypassed. Acceptance
of replacement pods must be observed after normal Flux deployment.

## Reviewed deployment and rollback

1. Pin and independently review the exact manifest PR head. Required checks are
   Flux Local - Success and Diff Scope - Success. Inspect advisory findings if
   emitted; a skipped advisory is not an independent review.
2. Capture AgentSession UIDs, active pod UIDs, restart counts and current portal,
   broker/operator/keeper health. Record the actual RC identity observations
   available without reading credentials or restarting a session.
3. Declare portal-only activity through the existing operator API. Merge only
   this reviewed portal PR. Let normal GitOps reconcile; request a targeted
   reconcile only if the caller's existing permissions allow it. A denial stops
   that action; do not use another identity or route to bypass it.
4. Verify Flux Ready/Healthy at the merge revision, Deployment 2/2, Ready Service
   endpoints on distinct hosts, and PDB currentHealthy 2 / desiredHealthy 1.
   Verify HTTPS health/readiness through existing authorized routes. Do not
   retry a stopped Authentik browser flow or call preview real OIDC acceptance.
5. Recompare active session/pod UIDs, restart counts, status and available RC IDs.
   Report any concurrent owner action separately; UID continuity alone proves
   neither retained history recovery nor a completed remote-control interaction.
6. On failed rollout/admission, first preserve events and the surviving endpoint.
   maxUnavailable 0 preserves Kubernetes-Ready capacity only. Startup opens no
   database connection and /readyz is unconditional, so it does not guarantee
   retaining a database-connected endpoint. Roll back through
   a reviewed Git revert of this PR (replicas 1, remove anti-affinity and PDB),
   with the same CI/QA and continuity checks. Do not use direct scale, delete,
   rollout undo, auth repair or admission changes. End the activity in either case.

## Boundaries and remaining acceptance

Keeper remains one rotating-token owner. Controlled relocation must separately
qualify its existing Lease, durable journals and ambiguity fences, then fresh
mint/projection results; no blind scale-to-two. Portal replicas do not make agent
processes replica-safe. OP252, OP102 and OP222 remain the existing recovery paths;
no HOME deletion, finalizer bypass, false terminal proof or mount workaround.
A Suspended task with exit 0 and a Running hold pod is not terminal proof.

Host maintenance remains deferred. Qualify current Ceph/NAS tolerance, including
the historically ten HDD OSDs and one of five external monitors, and the pinned
dns-canary before any separately authorized maintenance. HEALTH_OK and Bound
PVCs do not establish node-loss tolerance or recoverability.

Source, required tests, deployment, actual failover and real OIDC/RC recovery
acceptance must be reported separately in OP280. The portal manifest PR alone
completes none of the wider story's keeper or node-loss acceptance criteria.

## Deployment hold found during independent source qualification

The login-start admission limiter in dev-env `web/portal/session/engine.go` is
per process, burst four / sustained one per second. Two replicas therefore allow
aggregate burst eight / two per second at steady state, and burst twelve /
three per second during a three-process rolling surge. The existing singleton
also permits a two-process surge today. This changes a security-relevant limit;
Tom's routine replica-deployment authorization does not silently approve it.
Deployment/merge of this PR stays held pending an explicit ruling on that exact
effect, or a separately reviewed implementation preserving the aggregate limit.
The native Codex question was issued, but phone delivery remains unverified;
the question alone is neither an answer nor permission. The feature lead was
notified. Keep this PR ready for CI/review, without auto-merge. Do not claim
OP280 accepted or deployed from it. Also, /readyz only checks process readiness;
a healthy endpoint does not independently prove database or OIDC availability.
