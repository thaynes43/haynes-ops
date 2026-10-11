# Private project roots: bounded promotion

This promotes reviewed dev-env roots source #181/release #182 to new sessions.
ADR-003 in thaynes43/dev-env controls repositories and coordination. V1 remains
running; no cutover, credential copy, provider turn or enrollment follows.

Image: `ghcr.io/thaynes43/dev-env:2.14.0@sha256:e69e676786db9a0d5c14213730429a73425834a09b95aada4c8c365c70f72177`.
Source: `2b6d59682499456998200535caa2df5af74609b2`. Publication
[38154456128](https://github.com/thaynes43/dev-env/actions/runs/38154456128)
passed image smoke, push, keyless signing and exact-tag signature verification.
The new-session template enables private roots alongside trusted bootstrap.
Existing session Pods keep their image and state; private-project task admission
stays disabled. Existing shared-workspace opt-ins are not enabled.

The separately named `dev-env-private-project-roots-check-1011` Job runs two
installed explicit refreshes against the named API catalog. Its private HOME
and temporary directory are emptyDirs. It has one Pod, RestartNever, backoff 0,
a 600-second deadline, worker placement, 500m CPU and 1Gi memory limits, no init
containers, no provider credentials or writable shared mounts, and the existing
check-only named catalog GET/token/egress scope. Both earlier terminal bootstrap
Jobs retain their exact names, bytes and inventory. Only the new Job is the
Flux health gate; no existing operator, keeper, native fixture or ledger changes.

The verifier binds the full release commit, catalog UID/resourceVersion/hash,
per-refresh source targets, owned synthetic Git root and detached anchor,
wrapper/snapshot hashes and both retained reports. Unchanged inputs must retain
root/anchor identities, HEAD/index and wrapper bytes. Changed catalog/source
inputs require proved controlled refresh against their own reports; fresh
timestamps are provenance, not a byte-equality failure. Safe stage diagnostics
contain no credentials or private transcript bodies.

Four narrow renders, syntax/spec checks, diff checks and seven finite offline
Git verifier cases passed. Native independent review bound all four manifest
hashes and Job SHA256
`57c6424cc8d407a9dde80fe81f0f250d929cd986a1b17413de8edbfda9edc9cc`.
Required Flux/Diff CI remains the merge gate. Claude reviews are optional under
the owner's 2026-10-11 ruling; record an unavailable outcome without a pass.

Installed acceptance requires the admitted Job's exact image/limits/access,
successful retained receipt, Ready Flux targets and unchanged protected Pod
UIDs/imageIDs/restarts and original ledger identity/hash. Roots trial count is
zero before deployment; stop after three logical failures or 60 minutes without
evidence. No stress, CPU burners, wide tests, local image builds or destructive
cleanup. Declare scoped activity before targeted reconciliation.

This model-free proof does not establish native daemon startup, private GitHub
authentication, actual Claude/Codex rule loading, concurrent enrollment routing
or a working MVP. Private native preflight, authorized history, claims/messages,
budgets/recovery/access and the owner pilot remain separate. Track built,
deployed and accepted receipts on
[#173](https://github.com/thaynes43/dev-env/issues/173) and
[#91](https://github.com/thaynes43/dev-env/issues/91); keep parents open. Q-24's
routing/UX decision stays pending. Local inference waits for GPU readiness and
household reservations.
