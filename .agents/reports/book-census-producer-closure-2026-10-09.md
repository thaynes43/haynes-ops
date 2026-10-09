# Book census producer closure — 2026-10-09

The signed image and read-only mount are verified. The first actual V2 host
attempt refused before creating a Job or reading corpus bytes. A narrow host
inventory correction has passed finite tests and server admission, and is
prepared for independent review and a separately ratified attempt. No COPY or
production pause is authorized by this evidence.

## Image and preparation

[Ops #3620](https://github.com/thaynes43/haynes-ops/pull/3620) merged at
`04c611e7ccf6d057f5b6b466dc4d52f5bb5672c8`.
[Publication run 37868326665](https://github.com/thaynes43/haynes-ops/actions/runs/37868326665)
validated the image, transferred the exact validated image without rebuilding,
published it on main, and signed its immutable digest:
`ghcr.io/thaynes43/book-copy-writer@sha256:fe12dd95f77cbdf33f2bd57cbe8ecb752e9d730a7de6b26b329beca474954d5f`.

Full cosign v3.1.3 verification passed for the exact main workflow identity,
GitHub OIDC issuer, repository, and source SHA. It validated claims, the trusted
certificate chain, and offline transparency-log inclusion. The verifier binary
SHA `4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71`
matches the official release asset. The safe verification stdout SHA is
`45fb3bccaca20edead2ce8f230154c4732f7a311f9d70a25d08931b19a90acc9`.
This closes the prior registry proof's explicit certificate-chain gap. The
workflow signs images; it does not generate a separate build provenance
attestation. A bounded `cosign download attestation` query returned no records.
These are signature and exact validated-image publication claims, not a SLSA
provenance claim. See the official [verification documentation](https://docs.sigstore.dev/cosign/verifying/verify/).

The immutable V2 packet
`/home/dev/work/hn-825c-fence-prepared/live-byte-baseline-lifecycle-prepared-v2/immutable-review-packet.json`
has SHA `c9501cdf207add2242176a63da81f573abb74d23799788a51018f69878f559fe`;
all 14 file pins matched. Its later independent review at 02:02:39Z passed the
prepared host lifecycle, superseding the stale report's 01:59 blocker. Prepared
reviews did not prove actual corpus capture, ACK delivery, or foreground cleanup.

## Worker image and mount smoke

The bounded read-only smoke Job ran on `talosw01` from 15:08:49Z to 15:08:55Z
(six seconds; container exit zero, no restart). Its CPU limit was 500m, memory
256 MiB, deadline 120 seconds, and backoff zero. It confirmed the exact image
digest, all six module hashes, psycopg 3.3.6 and Python 3.14.8 imports, and the
reachable EBooks root under UID/GID 1000. `/data/cephfs-hdd` was the required
container read-only NFS mount from `gasha01.haynesnetwork:/hdd-nfs-repl`, backed by
the normal read-write export. Corpus files read: zero. Production writes: zero.
Foreground deletion completed, followed by empty Job and Pod inventories for
that smoke Job. The frozen actual LIVE manifest already pins `nodeName:
talosw01`; no placement revision was needed.

## Consumed V2 attempt and correction

The single ratified attempt ran from 15:13:31.944301Z to 15:13:32.553967Z and
refused with `complete_pod_inventory`. Job and Pod UIDs stayed null; only the
private ready manifest and failure receipt were written. The host's cleanup
also refused the typed inventory guard and correctly made no absence claim.
A separate complete native Jobs/Pods inventory confirmed no exact-name,
phase-labelled, or Job-owned object existed. No Job was created, no corpus bytes
were read, and no PostgreSQL or production library write occurred. The preserved
private failure receipt is
`/home/dev/work/hn-831-copy05-live-byte-baseline-actual/actual-receipt.json`.

The cause was a host/API mismatch: `kubectl get pods/jobs -o json` synthesized
`kind: List` with an empty resource version. The raw namespace API returned the
expected `PodList` and `JobList`, with native resource versions. V3 requests the
raw typed endpoint and refuses wrong schema, missing resource version, pagination,
or truncated inventory. It preserves the original 180-second collection clock,
200-second total cleanup budget, 32 MiB artifact cap, UID-precondition foreground
deletion, and phase/controller/owner union. The durable source and finite tests
are under `scripts/book-copy-writer/live-baseline-host/`; external dependency
pins and V2 evidence remain unchanged. A fresh V3 packet, name, phase, output,
independent review, and explicit one-attempt ratification are required.

The durable package retains all five generic dependencies byte-for-byte, the
unchanged ACK receiver, a synthetic `Author/Book.epub` fixture, the exact closed
profile manifest, dependency pins, and a non-runtime packet preparer. It contains
no private selected-scope data, credentials, or progress receipts. The preparer
reads no kubeconfig and makes no API call. Twenty-six finite tests passed in
0.528 seconds under `nice -n 19`, including the actual inventory failure,
pagination/schema failures, ownership union, UID-precondition foreground delete,
and the frozen manifest's pin and valid phase Downward API. Current real raw
inventories passed the new guard. The final V3 manifest passed server dry-run and
the existing helper's exact declared-template comparison on `talosw01`.

The final V3 packet is
`/home/dev/work/hn-825c-fence-prepared/live-byte-baseline-lifecycle-prepared-v3-native-inventory-final26/immutable-review-packet.json`,
SHA `ac5f3c55bf53e11e8ad81c1491cea59997bce7c2834ac09c0e28fc17305ece6e`.
It binds Job `issue831-live-byte-baseline-1009-02`, a fresh phase and unused private
output. The only frozen manifest changes are its Job name and two phase labels.
The phase environment remains the original Downward API entry. A preparation
mistake that briefly supplied both `value` and `valueFrom` was caught by peer
review, removed before any launch, and covered by the new regression. Provisional
packets remain unexecuted; the original failed V2 packet and receipt are preserved.

Production services, CronJobs, Flux, library data, and app settings remained in
their normal state. This Python NFS-only lifecycle is independent of the pending
haynesnetwork v0.110.5 release. It supplies no app capture and no COPY approval;
future mutation must use the fresh expanded corpus and deployed app version,
with original expiry/restoration bounds and without reusing the old 1956 approval.
