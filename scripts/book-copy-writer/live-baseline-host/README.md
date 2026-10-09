# Reviewed read-only LIVE baseline host

This is the durable host source for the manual PLAN-074 / haynesnetwork #831
read-only producer lifecycle. It is not installed in a Deployment or CronJob and
does not authorize COPY, app capture, service stops, or publisher changes.

The V2 launch refused on 2026-10-09 before creating a Job: `kubectl get pods -o
json` produced kubectl's synthesized `List`, while the reviewed host required
the native `PodList`. Its cleanup guard refused the same response. The V3 host
uses the exact namespace API endpoints through `kubectl get --raw`; it requires
typed responses, the expected API version, a resource version, and a complete
unpaginated inventory. It retains the union of phase, controller UID, and Job
owner name/UID when checking owned Pods and the foreground UID deletion guards.
Native typed-list items can omit redundant type/version fields. After validating
the parent envelope and rejecting any explicit item mismatch, the host restores
only those omitted fields from the known endpoint type for the existing Job
comparison. The actual V3 deadline refusal and independently verified cleanup are
recorded in the linked [report](../../../.agents/reports/book-census-producer-closure-2026-10-09.md).

The old V2 packet and failed receipt remain unchanged outside git. The V3 review
packet binds a new name, phase, output directory, manifest, launcher, and test
result. Existing image, collector, native verifier, helper, input transport, ACK,
and selected scope byte pins remain unchanged. `frozen-dependencies.json` records
those byte pins and their reviewed source paths; the generic dependencies are
retained byte-for-byte under `dependencies/`, so worktree cleanup cannot remove
the source needed for a new review. It contains no credentials or captured
production data. Only the synthetic baseline needed by the finite tests is
checked in here. The exact closed manifest supplies the profile template.

Preserved bounds: worker `talosw01`; one CPU / 512 MiB; Job deadline 180 seconds,
backoff zero; original host collection deadline 180 seconds and total cleanup
budget 200 seconds; 32 MiB raw artifact; 12-second private input delivery and
5-second ACK; zero retries. NFS is mounted read-only, the root filesystem is
read-only, UID/GID are 1000, and no ServiceAccount token is mounted. The host
verifies the actual native identity, placement, image, and mount before input
delivery or corpus capture. It writes only private temporary artifacts and
receipts, with no PostgreSQL, app API, or library writes.

Run finite tests once, at low concurrency:

```sh
nice -n 19 python3 -B scripts/book-copy-writer/live-baseline-host/test-live-baseline-lifecycle.py
```

An actual launch requires fresh root ratification of the exact V3 immutable
packet and command. A prior authorization is consumed by its single attempt;
do not retry, change pins, extend a deadline, overwrite an output, or restamp an
artifact. Keep original capture clocks. This read-only lifecycle is independent
of the web app release, but cannot authorize COPY or replace a fresh post-release
app capture. The future paused SOURCE/COPY chain keeps its separate original
180-second artifact expiry and 170-second restoration trigger.

To prepare a packet, run `prepare-review-packet.py` with absolute
`--selected-scope`, `--output-dir`, `--packet-dir`, and `--kubeconfig` paths.
The selected scope remains a private, independently reviewed input; its hash
must match the frozen scope pin. The kubeconfig is referenced without being
read. The preparer verifies every host dependency pin, requires an unused output
directory, and writes a private contract plus immutable review packet. It never
calls kubectl or launches a Job. The packet's `launch_argv` is the only concrete
command to ratify for that review. Do not commit either the private selected
scope or captured artifacts. For a future attempt, a reviewer must explicitly
rebind the launcher name, manifest name/phase and resulting manifest pin before
preparing a new packet; this is preparation, not an automatic retry.
