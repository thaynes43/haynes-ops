# Private rule canaries: installed prerequisites before admission

This bounded PR promotes merged dev-env #195/#196/#198 and signed v2.19.0.
It installs the required schemas, profiles, narrow network policy, nonempty
versioned catalog rules and retained common marker source. Canary/common enable
flags and global private admission remain false. No provider task is started.
Separate admission must follow verified installation; Ready dependencies alone
cannot prove the narrow egress reached the data plane before a task is admitted.

Use exact AgentSession and PublishedContext schemas from source24960dc, signed
operator sha-24960dc digest
13cb133177fa010a1ebdceb5e9f86ee5e46ce0ed713bf8fcb564ad8b9c4217ea,
and signed agent2.19.0 digest
4cf24d0c0bff7a9174a8a453340e3800d99fd9282b8787b6f7274f3474903210.
Publication runs38168334619 and38168847353 verified their respective main/tag
workflow identities. Preserve every current operator flag and fixture pin.
Templates add only two minimal rule-canary profiles. Dedicated exact-ID policies
permit required DNS/API/operator, github.com:443 and each provider's existing
subscription endpoint. Exclude renderer-owned canary labels from both generic
web/platform selectors so additive policy cannot retain broad inherited egress.
No new Secret/worker/clusterwide grant, v1 mounted resource or keeper change.

Reuse the two existing public common ConfigMaps and the installed named GET
Role from #3821. The common opaque marker appears only in the v2 common source;
the repository marker is already in merged repository AGENTS.md/CLAUDE.md.
Neither marker appears in the catalog wrapper, task prompt or developer inputs.
Nonempty catalog text defines fresh selected-repository fetch/pin, WIP-preserving
resume, private roots and operator coordination without granting more authority.
Derive exact catalog/rules/project/common/template revisions through existing
source helpers; do not invent revisions or use a build-stage image digest.

Root ratifies the later fixed records rule-canary-claude-1011 and
rule-canary-codex-1011 with permanent matching *-once idempotency keys, caller
dev/dev-env, project/repository dev-env, sizeS (100m/1Gi request,2CPU/4Gi limit),
180-second provider timeout, Claude claude-opus-5-5/xhigh/maxTurns1 and standalone
Codex gpt-6-astra/max/maxTurns0. CaptureOutput is true only in these immutable
trusted records. The later exact prompt is: Perform the instruction acceptance
probe described in your loaded instructions. Reply only with the common and
repository rule markers you actually received, and Unknown for absent
instructions. Do not use tools or fetch files. This PR installs no records or
enable flags. A later record must bind the final installed signed image and
source revisions; no default or owner Q-24 fleet UX ruling is inferred.

Provider readiness remains Unknown until the approved launch preflight/native
result proves usable subscription access. No standalone auth/Secret/HOME probe
is required. Keep existing static Claude plan and keeper-only Codex access-only
paths; no enrollment/refresh copies, new credential owner or paid API fallback.
Any refused/uncertain first attempt keeps its original identity and receipts;
no rearming/new key/cleanup-to-retry. The campaign permits at most one initial
attempt per fixed provider. Existing logical blocker counters still apply.

Native independent review, exact source/schema/digest/policy comparisons, bounded
renders and required hosted CI gate squash merge. Claude optional/down outcomes
are read and retained without retries. Before merge freeze a new immutable
baseline and a GET-only observer permitting only reviewed changes; operator
replicas may roll for their signed upgrade. Preserve every other Pod UID/restart,
all four Jobs, original CRD UIDs/complete unrelated schemas, existing authority,
keeper/catalog/template identities, failed native ledger and uncertain receipts.
No stress, CPU burners, wide/looped tests or local image builds.

After Flux applies the exact merge, separately verify installed schemas/image/
profiles/catalog/common/network and preservation before any admission PR. Source
and installed prerequisites do not accept actual loaded markers, private task
pins, stopped history, claims/messages, provider quotas/cost or full native chat
routing. Prospective directory/usage source and required guards must be installed
before any pilot claims those outcomes. Keep #173/#174/#175/#176/#91 open.

The frozen ten-path implementation packet is
`fb1e96361382aae858c8109b24ecc8f93da95dbe83091d21d2acc6b7a990f988`.
Twelve sequential bounded renders, exact signed/schema comparisons and 155
protected source files passed; saved thirteen-Pod source-reference audit found
no common ConfigMap/reloader impact on existing Pods. Official source helpers
derive catalog `9c5cc110d56f9afcd360a76947523d1b73cfa8bc685556c0c47f8e00c80d6d29`,
rules `1cebe4524e65483d4854960c34a31d3f75f1cfc0acb991f0fd490b10bbc98e2b`,
template `2.19.0-75f5661147`, and common
`3b9b829cf224ea4bc5b2b481ff3b7c1b4f0b173e1b4e304d84d4e3ecf4e2ca61`.
These are offline source bindings, not live accepted snapshots. Source/body
comparisons allow only declared additive schema changes and preserve old enum
members and retained status guards. Original validation corrections are saved.
