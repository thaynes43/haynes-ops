# Ransom native scanner proof: generic source preparation

Haynesnetwork #871 merged at `ea3cae62e61426812e2de9475d1155a1d24f5a47`
with all required checks and final advisory/e2e success. Its private offline clone
proves exactly seven Series/Volume lookup-key changes forward and inverse while
preserving all 82 tables / 34,433 logical rows, including saved-state dependencies.
That SQL shape does not prove native scanner behavior. The production writer remains
outside the authorized series-only strip profile and has not been approved or run.

The next prerequisite is actual target-only native `ScanSeries` plus repository
cleanup with the complete retained parsed-name set. See
[`scripts/book-native-scan-fixture/README.md`](../../scripts/book-native-scan-fixture/README.md).
The generic harness reflects and builds the published native DI graph but never invokes
Main/Start/Run, migrations or hosted workers. It refuses undeclared full-state changes,
requires target scan evidence, keeps the same IDs, exercises actual cleanup without
committing any removals, and independently verifies the private catalog inverse.
Unknown native metadata differences halt; they must not become automatic allowances.

All actual database/auth fields, EPUBs, saved state, source inventories and raw proof
snapshots stay private. Public CI compiles and launches only generic protocol tests and
native reflection against the exact deployed image. The dev pod has no SDK and MCR DNS
is denied; no alternate proxy or allowlist bypass was attempted. The already ratified
official public CI builder is the compilation path. Five finite public Python tests
passed locally under nice 19; initial source f657b5d8 compiled and launched its actual
apphost/reflection in the pinned native image in CI run 37968299781. Added exact native
DI DbContext path/inode and pre-scan no-constructor-write checks await current-head CI.

The proposed runtime uses only tmpfs, no production PVC/hostPath/network credentials or
service-account token, explicit Cilium all-entity network deny, one worker on talosw01,
500m CPU and the original at-most-180-second Job clock. The clone still has real auth
fields and is private. No test Job, scan, SQL repair, rename, strip, pause or production
mutation occurred in this preparation. The manifest is unbound and runtime approval
false. Exact host delivery/durable handback/UID cleanup and policy realization remain
separate review gates before root may grant a bounded test Job GO.

After actual native proof, root must present the concrete new-writer option to the
owner before any production catalog-key change. SOURCE/MAIN for v0.110.5 must be fresh
successors, never restamped old packets; health implementation #3653 now requires
actual uncached SQL before/after each complete SOURCE traversal as well as every
selected/retained/mutation boundary. Production schedules/acquisition remain running.
