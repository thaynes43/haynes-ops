# Story 181 — current PR3915 compiler incompatibility proof

2026-10-23. **PR3915 remains blocked by an upstream Kyverno/Kubernetes Go API incompatibility. No semantically identical local fix was found.** Driver `/root` retains ownership; no new story, edits, tests, pushes, runtime or credential/policy actions were performed.

Current shared `origin/main` inspected at `5a5fb146fd2a1ff8db7411d19f28ee0ddfdf6dfb`, plus public main and the exact PR head. [Current module](https://github.com/thaynes43/haynes-ops/blob/5a5fb146fd2a1ff8db7411d19f28ee0ddfdf6dfb/scripts/tests/dev-env-archive-guard/go.mod) retains Kyverno1.19.2 and Kubernetes modules0.36.3. The [PR head6d4863b083774664c8266f787e9bbc78bfbb94de](https://github.com/thaynes43/haynes-ops/blob/6d4863b083774664c8266f787e9bbc78bfbb94de/scripts/tests/dev-env-archive-guard/go.mod) selects0.37.1 while retaining Kyverno1.19.2.

## Exact observed failure

[Archive Guard Source run40001332472, job120577882311](https://github.com/thaynes43/haynes-ops/actions/runs/40001332472/job/120577882311) checked out merge6f4bb0d of that PR head and base2aeda315. Go1.27.3, fresh cache miss, serial readonly test invocation. It failed before fixtures ran:

```
kyverno@v1.19.2/pkg/admissionpolicy/mutate.go:346:42:
cannot use newVersionedObject (runtime.Object)
as admission.LazyObject value in assignment
```

This is a dependency compilation error, not a failing assertion, stale cache, Go compiler floor or local fixture-object error. Changing the workflow's stale `go1.26.6` cache-key label would not fix it.

## Immutable upstream source proof

Kyverno1.19.2 resolves to commit **e63308f1e16c05cd009ac4864be6ec25c66b49be**. [Its go.mod](https://github.com/kyverno/kyverno/blob/e63308f1e16c05cd009ac4864be6ec25c66b49be/go.mod#L81) selects Kubernetes0.36.3. [mutate.go:345–346](https://github.com/kyverno/kyverno/blob/e63308f1e16c05cd009ac4864be6ec25c66b49be/pkg/admissionpolicy/mutate.go#L345) marks dirty and assigns the patcher's `runtime.Object` directly to `VersionedAttributes.VersionedObject`.

Kubernetes apiserver0.36.3 commit **72f5171bff564be05f1011ccef0233a2ea76ebb4** [defines that field as runtime.Object](https://github.com/kubernetes/apiserver/blob/72f5171bff564be05f1011ccef0233a2ea76ebb4/pkg/admission/conversion.go#L26). Version0.37.1 commit **4fb3912e297aa00fef85917c9ef52e503fba1ae0** [changes both versioned fields to concrete LazyObject](https://github.com/kubernetes/apiserver/blob/4fb3912e297aa00fef85917c9ef52e503fba1ae0/pkg/admission/conversion.go#L32), adds [UpdateObject(runtime.Object)](https://github.com/kubernetes/apiserver/blob/4fb3912e297aa00fef85917c9ef52e503fba1ae0/pkg/admission/conversion.go#L48) and the [lazy wrapper/cache](https://github.com/kubernetes/apiserver/blob/4fb3912e297aa00fef85917c9ef52e503fba1ae0/pkg/admission/conversion.go#L107). The underlying apimachinery runtime.Object interface itself remains unchanged.

Local wrappers cannot change a field assignment inside this upstream package. Upstream must adapt mutation updates and wrapper consumers/cache semantics; merely changing fixture arguments cannot do it.

## Why the real validating compiler necessarily compiles mutation code

The exact tagged, ordinary Go-file import chain is:

1. [vpol/compiler/compiler.go:14](https://github.com/kyverno/kyverno/blob/e63308f1e16c05cd009ac4864be6ec25c66b49be/pkg/cel/policies/vpol/compiler/compiler.go#L14) → `pkg/cel/libs`.
2. [libs/context.go:10](https://github.com/kyverno/kyverno/blob/e63308f1e16c05cd009ac4864be6ec25c66b49be/pkg/cel/libs/context.go#L10) → `pkg/background/common`.
3. [background/common/context.go:11](https://github.com/kyverno/kyverno/blob/e63308f1e16c05cd009ac4864be6ec25c66b49be/pkg/background/common/context.go#L11) → `pkg/engine`.
4. [engine/generation.go:10](https://github.com/kyverno/kyverno/blob/e63308f1e16c05cd009ac4864be6ec25c66b49be/pkg/engine/generation.go#L10) → `pkg/engine/internal`.
5. [engine/internal/match.go:8](https://github.com/kyverno/kyverno/blob/e63308f1e16c05cd009ac4864be6ec25c66b49be/pkg/engine/internal/match.go#L8) → `pkg/admissionpolicy`, including mutate.go.

Go type-checks imported packages before dead-code elimination. Removing the local explicit libs/FakeContextProvider import cannot break this chain: the runtime-matched compiler itself imports libs. A fixture-only alternate compiler, copied/forked package, dependency patch, mixed Kubernetes replacement or rewritten assertion would evade the required evidence, not fix this update. None was attempted.

## Published path checked and next action

[Latest official release](https://github.com/kyverno/kyverno/releases/tag/v1.19.2) is still1.19.2, published October20. The [public Go module proxy's latest](https://proxy.golang.org/github.com/kyverno/kyverno/@latest) independently resolves to1.19.2/e63308f1. No newer published compatible compiler/runtime pair was established. Existing SDK dependencies do not remove the compiler's own mandatory import chain.

All existing1593fixtures/assertions and runtime-matched Kyverno1.19.2 remain untouched. No local suite was run: the exact existing CI error and upstream type/import proof settle this compile gate without repeating expensive compilation.

**Custodian/next action:** driver records this evidence on Story181/PR3915 and retains current0.36.3 modules. Kyverno upstream must publish a compatible release; then the driver can reassess a coordinated runtime/compiler/dependency update with every existing fixture/assertion preserved. No new story or automatic continuation was started. Raw public source/log evidence: `/tmp/renovate-181-3915-review/`.
