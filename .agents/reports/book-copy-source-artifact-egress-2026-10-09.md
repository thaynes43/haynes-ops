# Direct Flux artifact access — 2026-10-09

The cached Stop preparation needs to verify the actual GitRepository artifact
bytes before accepting its digest and six manifest pins. The dev-env policy has
no source-controller destination, so artifact access is an explicit prerequisite
to the still-Normal rehearsal. This change permits one cluster service, without
changing mounted dev-env resources or restarting the pod.

Read-only native inspection found the actual artifact URL host
`source-controller.flux-system.svc.cluster.local.`. Its Service selects
`app: source-controller`, accepts TCP 80 and maps named `http` to the matching
Pod's TCP 9090. The egress rule therefore allows backend port 9090 only for
`app: source-controller` in `flux-system`, plus that exact DNS name. Cilium's
[`MatchName` schema](https://github.com/cilium/cilium/blob/main/pkg/k8s/apis/cilium.io/client/crds/v2/ciliumnetworkpolicies.yaml)
normalizes the DNS root dot; the policy includes the observed fully qualified
name. No wildcard, proxy, additional namespace or public endpoint is added.

Required validation is the normal manifest CI and advisory review, Flux applying
the exact policy, then one bounded direct read of the currently advertised Normal
artifact with matching size and SHA-256 bracketed by native source/controller
identity observations. The read must issue no holds, Jobs or app/library writes.
It proves reachability and byte custody only; cached Stop hold persistence and
restoration timing still require separately ratified rehearsal.
