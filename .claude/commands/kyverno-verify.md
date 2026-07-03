---
description: Run the Kyverno enforce periodic verification runbook (read-only)
---
Read `.agents/runbooks/kyverno-enforce-verify.md` and execute its verification against the
live cluster.

- Run all three read-only check blocks (enforce-block lookback, stuck-PVC/pod scan,
  Kyverno controller health + alert rules loaded).
- Report each result plainly, with the runbook's pass/fail interpretation, and state
  whether the overall result is all-green.
- If anything is NOT green, follow the runbook's "On a finding" section to identify what
  was blocked (events/logs/`describe pvc`) and recommend the fix (a scoped PolicyException,
  or a controller memory bump). Do NOT apply any change without asking first.
- Stay strictly within the runbook's read-only commands. Do not modify cluster state.

$ARGUMENTS
