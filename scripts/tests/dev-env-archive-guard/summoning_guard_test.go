package archiveguard

// DESIGN-064 5.8, OP859: these fixtures use the source policies' real resource
// matcher, identity conditions, lazy CEL variables and (mocked) resource.Get.
// They prove admission decisions, not RBAC permission or installed webhooks.
import (
	"bytes"
	"io"
	"os"
	"slices"
	"strings"
	"testing"

	registration "k8s.io/api/admissionregistration/v1"
	utilyaml "k8s.io/apimachinery/pkg/util/yaml"
)

const identityGuardPath = "kubernetes/main/apps/dev-env-system/rbac/app/identity-guard.yaml"
const execGuardPath = "kubernetes/main/apps/kyverno/policies/app/dev-env-exec-guard.yaml"

// loadVAP preserves the source match and expressions but adapts them to Kyverno's
// compiler. Check the native binding too: an Audit-only or narrower binding must
// not silently turn these Deny fixtures into a claim about live enforcement.
func TestSummoningConfigMapBinding(t *testing.T) {
	p := loadVAP(t, identityGuardPath)
	if p.Spec.FailurePolicy == nil || *p.Spec.FailurePolicy != registration.Fail {
		t.Fatal("identity guard must fail closed")
	}
	b, err := os.ReadFile(repoPath(identityGuardPath))
	if err != nil {
		t.Fatal(err)
	}
	decoder := utilyaml.NewYAMLOrJSONDecoder(bytes.NewReader(b), 4096)
	bindings := 0
	for {
		var binding registration.ValidatingAdmissionPolicyBinding
		if err := decoder.Decode(&binding); err != nil {
			if err == io.EOF {
				break
			}
			t.Fatal(err)
		}
		if binding.Kind != "ValidatingAdmissionPolicyBinding" {
			continue
		}
		bindings++
		if binding.APIVersion != registration.SchemeGroupVersion.String() || binding.Name != p.Name ||
			binding.Spec.PolicyName != p.Name || binding.Spec.ParamRef != nil || binding.Spec.MatchResources != nil ||
			!slices.Equal(binding.Spec.ValidationActions, []registration.ValidationAction{registration.Deny}) {
			t.Fatalf("expected the unscoped Deny binding for %s", p.Name)
		}
	}
	if bindings != 1 {
		t.Fatalf("found %d bindings, want exactly one", bindings)
	}
}

func configMapFixture(name, namespace, actor, change string, allow bool) fixture {
	original := obj{
		"apiVersion": "v1", "kind": "ConfigMap",
		"metadata": obj{"name": name, "namespace": namespace, "labels": obj{"fixture": "original"}},
		"data":     obj{"script": "original"},
	}
	f := fixture{kind: "ConfigMap", resource: "configmaps", namespace: namespace, name: name,
		actor: actor, operation: "UPDATE", old: original, new: clone(original), allow: allow}
	switch change {
	case "create-data", "apply-create":
		f.operation, f.old = "CREATE", nil
	case "create-binaryData":
		f.operation, f.old = "CREATE", nil
		delete(f.new, "data")
		f.new["binaryData"] = obj{"script": "Zml4dHVyZQ=="}
	case "create-metadata":
		f.operation, f.old = "CREATE", nil
		delete(f.new, "data")
	case "update-data", "patch", "apply-update":
		f.new["data"] = obj{"script": "changed"}
	case "update-binaryData":
		f.new["binaryData"] = obj{"script": "Zml4dHVyZQ=="}
	case "update-metadata":
		delete(f.new["metadata"].(obj), "labels")
		f.new["metadata"].(obj)["annotations"] = obj{"fixture": "changed"}
	case "delete":
		f.operation, f.new = "DELETE", nil // AdmissionReview.object is null on DELETE.
	default:
		panic("unknown ConfigMap fixture change: " + change)
	}
	return f
}

// Patch and apply arrive at admission as UPDATE, or CREATE when apply creates a
// missing name. These are complete admitted objects, not HTTP patch documents.
var configMapChanges = []string{
	"create-data", "create-binaryData", "create-metadata", "apply-create",
	"update-data", "update-binaryData", "update-metadata", "patch", "apply-update", "delete",
}

// Reject evaluation errors as proof of an intentional refusal. Only the separate
// missing-Pod/error fixtures below expect fail-closed evaluation errors.
func checkSummoningDecision(t *testing.T, h *harness, f fixture, message string) {
	t.Helper()
	allowed, result := h.evaluate(t, f)
	if allowed != f.allow {
		t.Fatalf("allow=%v, want %v; result=%+v; lookups=%v", allowed, f.allow, result, h.mock.calls)
	}
	if result != nil && result.Error != nil {
		t.Fatalf("unexpected evaluation error: %v", result.Error)
	}
	if !f.allow && (result == nil || !strings.Contains(result.Message, message)) {
		t.Fatalf("expected refusal naming %q, got %+v", message, result)
	}
	if len(h.mock.calls) != f.wantCalls {
		t.Fatalf("lookups=%v, want %d", h.mock.calls, f.wantCalls)
	}
}

func TestSummoningConfigMapWrites(t *testing.T) {
	h := compile(t, loadVAP(t, identityGuardPath))
	// Option A has no name allowlist. Include each mounted code/trust input from
	// the design, v1's scripts, unrelated state, and a previously absent name.
	names := []string{
		"alert-responder-scripts", "upgrade-shepherd-scripts", "upgrade-health-gate-scripts",
		"upgrade-coordination-lib", "dev-env-api-client", "cigar-curation-scripts",
		"dev-env-api-ca", "dev-env-ops-scripts", "unrelated-configmap", "not-created-yet",
	}
	actors := []struct {
		name  string
		allow bool
	}{
		{agent, false}, {grant, false},
		{sa("dev-agents/grant-breakglass-fixture"), false},
		{sa("dev-agents/another-session"), false},
		{sa("dev-agents/dev-env-workbench-lookalike"), false},
		{sa("dev-agents/dev-env-workbench"), true},
		{sa("flux-system/kustomize-controller"), true},
		{sa("flux-system/helm-controller"), true},
		{sa("upgrade-agent/dev-env-ops"), true},
		{sa("outside/dev-env-agent"), true}, {"fixture-human", true},
	}
	for _, caller := range summonCallers(t) {
		actors = append(actors, struct {
			name  string
			allow bool
		}{sa(caller), true})
	}
	for _, actor := range actors {
		for _, name := range names {
			for _, change := range configMapChanges {
				t.Run(actor.name+"/"+name+"/"+change, func(t *testing.T) {
					f := configMapFixture(name, "upgrade-agent", actor.name, change, actor.allow)
					checkSummoningDecision(t, h, f, "DESIGN-064 5.8")
				})
			}
		}
	}
	for _, actor := range []string{agent, grant} {
		for _, change := range configMapChanges {
			t.Run("other-namespace/"+actor+"/"+change, func(t *testing.T) {
				f := configMapFixture("alert-responder-scripts", "ordinary", actor, change, true)
				checkSummoningDecision(t, h, f, "")
			})
		}
	}
}

// The workflow already watches caller-policies/**. A future caller in another
// namespace must extend the guard: use a new arbitrary code name, not a list of
// today's generated names or a hardcoded assertion about the namespace string.
func TestSummoningConfigMapNamespaceCoverage(t *testing.T) {
	h := compile(t, loadVAP(t, identityGuardPath))
	for _, caller := range summonCallers(t) {
		namespace, _, _ := strings.Cut(caller, "/")
		for _, actor := range []string{agent, grant} {
			for _, change := range configMapChanges {
				t.Run(caller+"/"+actor+"/"+change, func(t *testing.T) {
					f := configMapFixture("future-caller-code", namespace, actor, change, false)
					checkSummoningDecision(t, h, f, "DESIGN-064 5.8")
				})
			}
		}
	}
}

type summoningConnectCase struct {
	name, subresource, requestName string
	options                        obj
}

func summoningConnectCases() []summoningConnectCase {
	cases := []summoningConnectCase{
		{"exec", "exec", "fixture", obj{"apiVersion": "v1", "kind": "PodExecOptions", "command": []any{"true"}}},
		{"attach", "attach", "fixture", obj{"apiVersion": "v1", "kind": "PodAttachOptions", "stdout": true}},
		{"portforward", "portforward", "fixture", obj{"apiVersion": "v1", "kind": "PodPortForwardOptions", "ports": []any{int64(8080)}}},
	}
	// Kubernetes ConnectResource emits CONNECT and PodProxyOptions for BOTH
	// HTTP GET and POST; AdmissionRequest has no HTTP-method field. Test both
	// equivalent admission envelopes, including numeric, named and empty ports.
	for _, method := range []string{"GET", "POST"} {
		for _, name := range []string{"fixture", "fixture:8080", "fixture:http", "http:fixture:8080", "https:fixture:https", ":fixture:8080", "fixture:", "http:fixture:"} {
			cases = append(cases, summoningConnectCase{
				"proxy/" + method + "/" + name, "proxy", name,
				obj{"apiVersion": "v1", "kind": "PodProxyOptions", "path": "healthz"},
			})
		}
	}
	return cases
}

func connectFixture(tc summoningConnectCase, namespace, actor string, pod obj, allow bool) fixture {
	f := fixture{name: tc.requestName, kind: tc.options["kind"].(string), resource: "pods",
		namespace: namespace, actor: actor, operation: "CONNECT", subresource: tc.subresource,
		new: tc.options, allow: allow, wantCalls: 1}
	if pod != nil {
		f.parents = []obj{pod}
	}
	return f
}

func TestSummoningPodConnections(t *testing.T) {
	p := load(t, execGuardPath)
	if p.Spec.FailurePolicy == nil || *p.Spec.FailurePolicy != registration.Fail ||
		!slices.Equal(p.Spec.ValidationAction, []registration.ValidationAction{registration.Deny}) {
		t.Fatal("exec guard must fail closed with Deny")
	}
	h := compile(t, p)
	targets := append(summonCallers(t), "frontend/headlamp", "upgrade-agent/default", "ordinary/default")
	for _, target := range targets {
		namespace, account, _ := strings.Cut(target, "/")
		pod := workload("Pod", "fixture", "")
		pod["metadata"].(obj)["namespace"] = namespace
		pod["spec"].(obj)["serviceAccountName"] = account
		for _, actor := range []string{agent, grant, sa("dev-agents/dev-env-workbench"), sa("upgrade-agent/dev-env-ops"), sa("upgrade-agent/alert-responder"), sa("flux-system/kustomize-controller"), "fixture-human"} {
			for _, tc := range summoningConnectCases() {
				t.Run(target+"/"+actor+"/"+tc.name, func(t *testing.T) {
					exempt := actor != agent && actor != grant
					f := connectFixture(tc, namespace, actor, pod, exempt || account == "default")
					if exempt {
						f.wantCalls = 0
					}
					checkSummoningDecision(t, h, f, "dev-env-exec-guard")
					if !exempt && h.mock.calls[0] != key("v1", "pods", namespace, "fixture") {
						t.Fatalf("lookup must resolve the actual Pod name: %v", h.mock.calls)
					}
				})
			}
		}
	}
	// The other privileged-target tests remain in effect for all four routes,
	// even when the target uses an ordinary account.
	for _, mode := range []string{"hostPID", "privileged-container", "privileged-initContainer"} {
		pod := workload("Pod", "fixture", "")
		pod["metadata"].(obj)["namespace"] = "upgrade-agent"
		spec := pod["spec"].(obj)
		switch mode {
		case "hostPID":
			spec["hostPID"] = true
		case "privileged-container":
			spec["containers"].([]any)[0].(obj)["securityContext"] = obj{"privileged": true}
		case "privileged-initContainer":
			spec["initContainers"] = []any{obj{"name": "init", "image": "fixture", "securityContext": obj{"privileged": true}}}
		}
		for _, actor := range []string{agent, grant} {
			for _, tc := range summoningConnectCases() {
				t.Run(mode+"/"+actor+"/"+tc.name, func(t *testing.T) {
					checkSummoningDecision(t, h, connectFixture(tc, "upgrade-agent", actor, pod, false), "dev-env-exec-guard")
				})
			}
		}
	}
}

func TestSummoningPodLookupFailure(t *testing.T) {
	h := compile(t, load(t, execGuardPath))
	for _, actor := range []string{agent, grant, sa("dev-agents/dev-env-workbench"), "fixture-human"} {
		for _, fault := range []string{"not-found", "forbidden", "transport"} {
			for _, tc := range summoningConnectCases() {
				t.Run(actor+"/"+fault+"/"+tc.name, func(t *testing.T) {
					exempt := actor != agent && actor != grant
					f := connectFixture(tc, "upgrade-agent", actor, nil, exempt)
					f.fault = fault
					if exempt {
						f.wantCalls = 0
					}
					allowed, result := h.evaluate(t, f)
					if allowed != exempt || len(h.mock.calls) != f.wantCalls {
						t.Fatalf("allow=%v, want %v; result=%+v; lookups=%v", allowed, exempt, result, h.mock.calls)
					}
					if !exempt && (result == nil || result.Error == nil || h.mock.calls[0] != key("v1", "pods", "upgrade-agent", "fixture")) {
						t.Fatalf("expected a fail-closed Pod lookup error: result=%+v; lookups=%v", result, h.mock.calls)
					}
				})
			}
		}
	}
}
