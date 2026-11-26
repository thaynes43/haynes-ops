# Git preservation incidents and notification ownership

This is source preparation for dev-env DESIGN-050. Runtime adoption is held
until the operator source, schema and exact rollout consequences are qualified.
No ownership entries are installed by this change.

`RescueFailed` says that Git preservation is not proved. A missing declared
clone does not establish transcript failure, an empty HOME or safe deletion.
Even a proven `BootstrapInterrupted` remains Failed and protected. A Session
with a pending deletion timestamp can proceed to destructive deletion after
actual successful rescue; do not trigger success or change that classification
as a notification fix. Never strip finalizers or use a fabricated success flag.

The operator's 15-minute retry, newest attempt timestamp/stamp, original condition
transition and Alertmanager repeat interval are distinct clocks. Retry-only
changes do not establish a new incident. The notification metric preserves exact
Session UID, generation, reason, typed risk key and pending-deletion state.
The old metric remains a fallback for operators lacking this source.

An optional deployment-owned `--rescue-incident-owners-file` can qualify one
native acknowledgment per exact Session UID and typed missing-clone risk.
Verify the actual native acknowledgment, hash/reference, scope and expiry before
configuring it. It must bind generation, reason, risk key, deletion state,
owner Session UID and native conversation. Its maximum duration is 24 hours;
there is no automatic renewal. An absent/expired/mismatched acknowledgment or
missing, stale, terminal or different owner-native evidence stays unowned.
A task cap, saved handoff or ready Pod does not establish native availability.
The owner must still be Running/Idle and requesting Running mode. Even matching
recent process metadata does not certify provider authentication, API health or
next-turn availability; the acknowledgment establishes bounded accountability.
There is no autonomous renewal.

An unchanged, exactly owned incident stays visible in Alertmanager and receives
normal-priority Pushover notifications with a longer repeat interval. Unowned,
changed-risk and unrelated critical alerts retain the urgent receiver. This is
not a blanket rescue silence. Ownership changes notification urgency only;
it grants no rescue, preservation, deletion, storage or credential authority.
The recorded failed verdict is immediately notification-eligible: the old
20-minute pending delay and 30-minute keep-firing are removed so risk/ownership
changes cannot hide continuous failure or retain an obsolete owned identity.

The responder uses typed incident identity in addition to fingerprint/start time.
Changed UID, generation, reason, risk or deletion state cannot inherit an old
claim or generic cooldown. A current exact owner avoids a duplicate model
diagnosis; the independent direct Alertmanager route still notifies. Failure to
read/write responder state fails closed for model spend, while direct safety
notification remains independent. A responder claim is not native ownership.

Offline validation: `uv run scripts/tests/rescue-notifications/test_notifications.py`.
Fixtures execute the production eligibility loop with deterministic local state
and evaluate the actual committed routing template. They contact no cluster,
provider, Pushover receiver or account.
