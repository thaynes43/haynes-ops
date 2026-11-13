# Classified log retention: source preparation

This is an inactive preparation, not an enabled retention policy. Loki's rendered
configuration retains the global 90-day limit. Promtail removes both reserved
retention labels and emits no classification. The candidate runtime configuration
in `scripts/log-retention-policy/fixtures/runtime-candidate.json` is a test input;
nothing in the application kustomization loads it. It is not a release-ready
classifier or evidence that shorter retention is safe. In particular, no audit,
Traefik, Zigbee2MQTT, Loki query, VolSync, or other application record is currently
qualified for a shorter period by this preparation.

The proposed bounded labels are `retention_policy=classified-v1` and exactly one
`retention_class`: `protected_90`, `routine_30`, `noisy_14`, or `debug_3`. Unknown,
missing, malformed, ambiguous, and shadow-labeled records fall back to 90 days.
The short selectors in the fixture each require both exact labels. A collector must
remove untrusted values first, derive its decisions only from trusted source identity
and a validated, version-qualified format, and emit the enforceable labels only for
qualified records. An informational severity alone does not establish that an audit
record is non-security-related. Protected sources and warnings/errors must not be
downgraded by noisy or debug rules. Unresolved schemas and incomplete or late event
groups stay at 90 days. Classification state, cardinality, limits and eviction must
be reviewed and tested before any stateful collector implementation is activated.

## Ordered changes and gates

1. Review this source-only guard and tests. Its Promtail Secret and DaemonSet
   checksum change in a chart render. Applying it restarts collectors; the present
   source work does not authorize a rollout. It adds no class or policy label.
2. Separately implement and independently review a classifier and its disabled or
   shadow configuration. A shadow policy value such as `classified-v1-shadow`
   must never match the enforceable selector. Preserve the leading CRI stage and
   drop both reserved labels before emitting collector-owned values. Show exact
   changes, synthetic adversarial tests and actual coverage; absent reliable formats,
   leave records at 90 days. Neither a capacity model nor a test fixture proves
   production format coverage.
3. Before installing the runtime candidate, Root must complete its preflight: verify
   the actual chart, image and effective runtime overrides, exact target tenant and
   full replacement-list precedence; prove that **all three exact short selectors**
   match no existing indexed or ingester streams immediately before the change;
   examine record ages and recoverability and obtain action-time confirmation when
   required. A tenant override can replace an existing retention stream list. Do not
   overwrite existing overrides without an explicit combined reviewed configuration.
   Install and verify the runtime policy before allowing any collector to emit the
   enforceable labels. Policy installation is a separate reviewed GitOps change and
   remains held pending that gate. A no-match test must account for delayed writes,
   old streams and any other writer to the tenant, not just current collector config.
4. Only after policy verification, separately review and authorize enabling the
   classifier. Verify effective selectors, collector output and protected-source
   outcomes. Stop if source identity or schema qualification is uncertain.

Loki evaluates retention against stream labels for existing chunks. A policy change
is not inherently nonretroactive: matching old data may become eligible for deletion.
Disabling class emission prevents future enforceable labels; it does not remove labels
from already ingested streams. Reverting retention rules can stop future selection but
cannot recover data already deleted. Rollback and operational monitoring need their
own action-time plan and authorization.

## Evidence and limitations

Run `scripts/log-retention-policy/test_policy.py` with `LOKI_BIN`, `PROMTAIL_BIN`,
`LOKI_CHART`, and `PROMTAIL_CHART` pointing at the pinned public artifacts used by
`.github/workflows/log-retention-test.yml`. The check renders the current chart values,
compares every affected resource, verifies base Loki and Promtail syntax with the
pinned binaries, and feeds only synthetic records and hostile target labels to
Promtail. It checks exact candidate selectors and a few fallback cases. It does not
prove Loki runtime override loading or effective precedence, source trust, classifier
correctness, all adversarial event group behavior, deployed state, or storage savings.
Loki's `-verify-config` validates the base config; it does not load runtime overrides.
The runtime candidate therefore remains a proposal pending those tests and review.

A one-month trend check should compare used bytes, growth rates, retention-class
volumes and coverage, protected-event samples, compactor health and remaining
headroom against a privately recorded pre-change baseline. Separate actual measured
changes from modeled estimates. A checkpoint or scheduler is not created here, and
there is no capacity guarantee from the modeled mixture.
