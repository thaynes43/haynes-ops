# SMART known-warning fixtures

Run `PROMTOOL=/path/to/promtool scripts/smartctl-alert-tests/run.sh` with `yq`,
Python 3 and official checksum-verified promtool (validated with 3.15.1, matching
live Prometheus). Fixtures evaluate the actual GitOps rules through 20 minutes,
including their 15-minute alert hold; they never contact hardware.

The matrix covers both physical cache identities, current/old/new device indexes,
wrong/missing/ambiguous identity (including a serial duplicated across devices),
cross-job/host/device join isolation, additional
and combined warning bits, independent overall/legacy/exit-status failures,
error-log growth and resets, missing prerequisite metrics, media errors, current
temperature versus rated limits, low spare, and healthy unrelated devices.

OP328: the exception requires mask 4, overall health 0, smartctl exit 8, media
errors 0 and error-log entries exactly 183. There is no independent error-log
growth alert in the existing rules. A count change therefore deliberately ends
the exception and restores critical/overall alerts. Temperature/spare alerts stay
independent. Missing identity or prerequisite health data never grants exemption;
a missing metric cannot itself be evaluated as a failure by the original rule.
This acknowledges a known reliability warning, not proof of a wear-only cause or
safe storage. No self-test-history metric is available on these NVMe drives.
