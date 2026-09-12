# Hardening backlog

Running list of **safety and observability defects found while doing other work** —
things that were quietly wrong rather than loudly broken. Owner rolls the dev-env half
out externally; the rest can be merged normally.

A finding earns a place here when it meets one of these bars:

- **A check that can pass without having run.** The worst class. A silent failure that
  renders as a green is more dangerous than a red, because it actively buys confidence.
- **Documentation that asserts a safety property the cluster does not have.** An agent
  reading it makes a wrong decision with full confidence.
- **A guard whose empty/degraded state is fail-open.**

Append new findings; do not delete resolved ones, mark them.

---

## Open

### H-1 — Runbook Prometheus queries can return a false green (PARTIALLY FIXED)

**Found:** 2026-09-12, running `.agents/runbooks/kyverno-enforce-verify.md` after the
Kyverno 3.9.1 bump.

The runbook's `q()` helper reaches Prometheus through the apiserver proxy
(`/api/v1/namespaces/observability/services/.../proxy/...`) and discarded errors with
`2>/dev/null`. The dev-env ServiceAccount is **forbidden** from `services/proxy` — the
OPERATOR tier grants `services: [get, list, watch]`, not the proxy subresource. So the
query returned empty, and empty rendered as "0 blocks". **The runbook reported all-green
while the true answer was 15 enforce blocks in 31 days.**

Fixed in this PR for that one runbook (`q()` now fails loudly and says to use the Grafana
MCP). **Still open: the same pattern anywhere else.** Audit every `.agents/` runbook and
script for `kubectl get --raw .../proxy/...` and for `2>/dev/null` wrapping a check whose
emptiness is interpreted as success.

Two ways to close it properly, owner's call:

1. **Keep the denial, fix the callers** (preferred — matches "do not fight RBAC denials,
   they are the design"). Every runbook uses the Grafana MCP for PromQL, and no check may
   treat an empty result as a pass.
2. **Grant `services/proxy: [get]` to the dev-env SA.** One line in
   `kubernetes/main/apps/dev/dev-env/app/rbac.yaml`, and it does **not** bounce the pod
   (only `app/resources/**` feeds the reloader ConfigMap). **But this is a real privilege
   expansion**: `services/proxy` reaches any service in the cluster through the apiserver,
   which sidesteps the default-deny egress CiliumNetworkPolicy entirely. Not taken here
   because it trades a documented, deliberate boundary for convenience.

### H-2 — Three Kyverno policies are documented as enforcing and are not

**Found:** 2026-09-12, same session.

`restrict-image-registries`, `restrict-rbac-escalation` and `pod-security-baseline` are all
live in **Audit**. Only `verify-haynesnetwork-images` sets `validationFailureAction:
Enforce`; the field defaults to `Audit` when omitted. The runbook describes all three as
denying at admission. Corrected in the runbook text; **the underlying question is
undecided** — should they enforce? If yes they each need the key added, and that should be
staged one at a time with the exception gaps checked, because turning on enforce is exactly
how the OpenEBS helper-pod incident happened.

### H-3 — `upgrade-shepherd` fails image signature verification on every run

**Found:** 2026-09-12, incidentally, via `PolicyViolation` events.

`verify-thaynes43-images/verify-self-built` reports `no signatures found` /
`unverified image` for `ghcr.io/thaynes43/upgrade-shepherd`. The policy is Audit, so the
job completes and nothing pages — it has simply been failing quietly. Either the build
should sign the image (matching what the policy expects) or the policy should not cover it.
Right now it is neither enforced nor clean, which is the worst of both.

---

## Resolved

*(none yet — mark, do not delete)*
