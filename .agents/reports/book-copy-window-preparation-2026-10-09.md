# Fresh COPY2 Stop/inverse preparation — 2026-10-09

This is preparation only. Production services, acquisition, schedules and Flux
remain normal. No watcher, hold, Stop, SOURCE Job or COPY is authorized here.

The never-used Stop/inverse PRs #3622/#3623 are closed. Their inverse was stacked
on the Stop branch; it cannot satisfy the supervisor's green inverse against
`main` gate before the Stop squash is replayed. The authoritative historical
packet already pins a copy-aware replay helper (SHA `6095e34a94895b329d2d698ee1e3559c3aab16e360afde36c6517ff1499ca6c4`)
and v5 watcher. The older default core-only helper is not this packet's helper.
The fresh tracked package preserves that supported replay instead of weakening
the inverse gate or reusing consumed identities.

Conditional staging order, ratified for preparation: arm independent recovery
against verified normal Git and live workloads; establish all four Flux holds
and verify their actual UID/spec/status/resourceVersion after parent reconcile;
merge desired Stop under those holds while workloads remain normal; replay the
inverse onto actual squash `main` and require its exact six-file manifest bytes,
semantic fields, required checks and clean current advisory. Only then may a
fresh reviewed supervisor/packet be armed. Actual Stop needs separate runtime GO.

Any pre-stage cancellation must recover normal Git before releasing a hold.
The watcher retains holds when it cannot prove exact normal source, no owned
Jobs/Pods or PG leases, and actual normal controller state. It reports incomplete
recovery truthfully; retry cannot authorize COPY or reset clocks. Desired Stop
must never be applied while inverse checks are still pending.

The six changed manifests contain only six schedule suspensions, two temporary
`replicas: 0` fields and Libretto's disabled acquisition URL. Both branches retain
all other bytes, current app v0.110.5, converter STRIP=0 and Ransom hold. A fresh
inverse removes the two temporary replica fields and restores the six schedules
and acquisition URL. Normal main is restored before any hold is released.

Original limits remain: LIVE 180s / host 200s; consumer byte age original start
plus 300s; watcher restore request at first actual Stop plus 170s; supervisor
abort at first Stop plus 250s / service ceiling 300s / restore reserve 50s.
MAIN is additionally bounded by final publisher start+65s and finish+35s. The
minimum of these clocks applies; no successful preparation implies enough time.

## Concrete held pair and finite proof

Fresh [Stop #3659](https://github.com/thaynes43/haynes-ops/pull/3659) is held draft
head `7bcdbb18` (+9/-7 across the exact six files). Its [inverse #3660](https://github.com/thaynes43/haynes-ops/pull/3660)
is held stacked draft head `27156481` (+7/-9). The inverse is intentionally not
base-main/green yet; reviewed replay and new current-main gates remain mandatory.
Neither PR was merged or applied. These replace the closed, never-used examples.

The tracked package passed 25 finite tests serially under `nice -n 19` in 6.279s.
A real private local bare Git repository plus fake `gh` responses proved Stop
squash replay, exact before/after blob recovery, validation commit and a second
idempotent invocation. The public dummy test credential bypassed real credential
reads; fixtures performed no API writes. Fake clocks and native typed fixtures
covered 170/600 clock distinction, completed-parent hold persistence, reset or
replaced hold refusal, writer UID foreground deletion/full phase-name-UID-owner
union absence, reused identity refusal, pending inverse gates, and source or
cleanup failure retaining holds. Actual Flux hold persistence is still unproved;
no native hold, watcher or capture was started.

The SOURCE-owner outcome mailbox is a separate required integration. Successful
MAIN completion will create a private immutable receipt binding actual completed
Job/Pod UID/spec/image, exact durable final event SHA, current three-path scope,
assembly snapshot and selected count. That receipt SHA must bind the owner
request; recent SOURCE health events cannot replace actual SQL brackets on each
outcome walk. The pre-admission MAIN bridge remains PG-free read-only proof.

Concurrent Git proof is explicit: unrelated main commits survive replay and
restoration follows current main after proving inverse-merge ancestry and exact
normal six blobs. A same-six image/settings change revokes COPY durably and
requests owned writer/PG cleanup while retaining holds. It refuses the stale
normal snapshot instead of rolling back a newer setting. Original revocation and
clocks survive state reload. Current-main recovery needs a freshly reviewed
inverse/contract; the frozen package cannot guarantee the service ceiling through
that conflict. Actual Stop remains blocked pending a concrete bounded recovery
ratification.

A cheap actual v0.110.5 capture-import check reached the required Node builtins
and the `LL_BOOKS_SQL` export through tsx. Seven direct/transitive sync module SHA
values matched released source `8374aeca2456ec7aba035530be4755fd89216cf1` on actual
signed image index `e264e8a6…4c6a`. Receipt SHA
`1d6e82aef11deb04c5ece3c5307c2818b00abf38aab7cd0a1225c26ad1119354`
binds this read-only import check; it invoked no capture, database constructor,
query, API or library operation. This adds exact import reachability to the prior
eight-module deployment audit without claiming an actual producer run.

The independent code review found that a favorable advisory verdict can coexist
with unresolved MEDIUM findings. Both supervisor and watcher now require an
explicit no-findings current comment and refuse severity/needs-changes markers;
a finite actual gate fixture covers the contradictory verdict. SOURCE review
also corrected partial response publication and Complete-Job/running-Pod
acceptance; successor sender pin `675d47a6…cce82` binds that reviewed completion
helper. The SOURCE component remains a separate source-only preparation.
