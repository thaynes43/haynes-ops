# Native Kavita fixture: purpose-scoped network isolation

Preparation for haynesnetwork #825 / #840 / #871 and ops #3657. Root authorized
a GitOps-applied deny policy before any private clone Job, not a runtime Job or
catalog writer. The private clone contains real authentication and saved-state rows;
the absence of external secret mounts does not by itself isolate those rows.

Add a policy-only Flux Kustomization `book-native-scan-fixture` in `media`. Its
CiliumNetworkPolicy selects **only** `app.kubernetes.io/name=book-native-scan-fixture`
and explicitly denies all entities in both directions. No existing service, schedule,
Pod, Secret, RBAC, storage or environment is changed and no worker is defined here.
The prepared #3657 Job uses that exact purpose label plus a separately reviewed phase
label, no host networking, no sidecars and only memory emptyDirs. Inputs use native
exec/CRI custody, never the Pod's application network.

At 2026-10-09T17:55:04Z the live Cilium DaemonSet was
`quay.io/cilium/cilium:v1.20.2@sha256:2939231d0d3e3ebddcd80fffa168b7ddcc78fdf0dc864d1c8c126ff523c54f01`,
policy enforcement `default`, host firewall false. The purpose selector matched no
Pods and there were no CiliumNetworkPolicies in media. Cilium 1.20.2's normative
[deny-policy documentation](https://docs.cilium.io/en/stable/security/policy/deny/)
states explicit deny takes precedence over allow from all three supported policy kinds.
The `all` entity includes external and cluster destinations. This is an explicit deny,
not an additive allow policy with an empty egress list. The namespace is not isolated.

Root will merge green checks and verify Flux/source identity, actual installed selector
and Cilium policy acceptance before considering a Job. For the exact admitted Job,
independently bind its Pod UID, purpose/phase labels, native Cilium endpoint and realized
ingress/egress deny to private proof; refuse unexpected init/sidecar/host-network/mount
admission changes. Realization and no admitted outbound traffic must be verified before
native clone input delivery. Do not infer live datapath enforcement from this source
template or policy-only apply. Keep normal production services/acquisition running.

No Job, probe Pod, library scan/strip/repair, production file/list/SQL mutation or service
pause occurred in preparing this policy. Exact Job approval, immutable inputs, original
180-second clock, private artifact ACK and foreground UID cleanup remain separate gates.
