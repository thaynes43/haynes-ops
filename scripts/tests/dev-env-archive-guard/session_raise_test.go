package archiveguard

import (
	"os"
	"strings"
	"testing"
)

// Raising a running session (dev-env DESIGN-066 slice (a), install steps 1 to 4):
// finite synthetic admissions through the same pinned compiler and matcher as the
// other native-policy fixtures here. They prove the source expressions and the
// selectors, not installed delivery. A CEL error never counts as a denial, and a
// fixture that should match but does not fails.

const raiseDir = "kubernetes/main/apps/dev-env-system/session-raise/app/"
const raiseSystem = "dev-env-system"

var raiseOperator = sa("dev-env-system/dev-env-operator")
var raiseFlux = sa("flux-system/kustomize-controller")
var raiseCollector = controller("generic-garbage-collector")

const raiseAdmin = "fixture-cluster-admin"

// Find one named policy in a file that may hold several policies and bindings.
func loadRaiseVAP(t *testing.T, file, name string) *harness {
	t.Helper()
	b, err := os.ReadFile(repoPath(raiseDir + file))
	if err != nil {
		t.Fatal(err)
	}
	for _, doc := range strings.Split(string(b), "\n---\n") {
		if p, err := decodeVAP([]byte(doc), name); err == nil {
			return compile(t, p)
		}
	}
	t.Fatalf("policy %s not found in %s", name, file)
	return nil
}

type raiseCase struct {
	name           string
	f              fixture
	matched, allow bool
}

func runRaise(t *testing.T, h *harness, cases []raiseCase) {
	t.Helper()
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			allowed, result := h.evaluate(t, tc.f)
			if !tc.matched {
				if result != nil {
					t.Fatalf("selectors must skip this request: %+v", result)
				}
				return
			}
			if result == nil || result.Error != nil {
				t.Fatalf("CEL error or unmatched fixture: %+v", result)
			}
			if allowed != tc.allow {
				t.Fatalf("allowed=%v want=%v result=%+v", allowed, tc.allow, result)
			}
		})
	}
}

// labels and annotations: nil leaves the map out entirely.
func raiseSession(labels, annotations obj) obj {
	meta := obj{"name": "fixture", "namespace": ns, "uid": "uid-fixture"}
	if labels != nil {
		meta["labels"] = labels
	}
	if annotations != nil {
		meta["annotations"] = annotations
	}
	return obj{"apiVersion": "dev-env.haynesops.com/v1alpha1", "kind": "AgentSession", "metadata": meta, "spec": obj{"agent": "claude"}}
}

func sessionWrite(old, next obj, actor, operation string) fixture {
	return fixture{kind: "AgentSession", resource: "agentsessions", group: "dev-env.haynesops.com", version: "v1alpha1",
		old: old, new: next, actor: actor, operation: operation}
}

func TestSessionRaiseRecord(t *testing.T) {
	h := loadRaiseVAP(t, "record-guard.yaml", "dev-env-session-raise-record")
	const raiseKey = "dev-env.haynesops.com/raise"
	depth := obj{"dev-env.haynesops.com/depth": "2"}
	plain := raiseSession(depth, nil)
	raised := raiseSession(depth, obj{raiseKey: `{"v":1}`})
	changed := raiseSession(depth, obj{raiseKey: `{"v":1,"state":"active"}`})
	emptied := raiseSession(depth, obj{raiseKey: ""})
	other := raiseSession(depth, obj{raiseKey: `{"v":1}`, "fixture/other": "x"})
	bare := raiseSession(nil, nil)
	owned := clone(raised)
	owned["metadata"].(obj)["ownerReferences"] = []any{obj{"apiVersion": "v1", "kind": "ConfigMap", "name": "fixture-owner", "uid": "uid-owner"}}
	finalized := clone(raised)
	finalized["metadata"].(obj)["finalizers"] = []any{"dev-env.haynesops.com/rescue"}
	statusChange := clone(plain)
	statusChange["status"] = obj{"phase": "Running"}
	statusWrite := sessionWrite(plain, raiseSession(depth, obj{raiseKey: `{"v":1}`}), agent, "UPDATE")
	statusWrite.subresource = "status"
	runRaise(t, h, []raiseCase{
		{"operator-adds", sessionWrite(plain, raised, raiseOperator, "UPDATE"), true, true},
		{"operator-changes", sessionWrite(raised, changed, raiseOperator, "UPDATE"), true, true},
		{"operator-removes", sessionWrite(raised, plain, raiseOperator, "UPDATE"), true, true},
		{"agent-adds", sessionWrite(plain, raised, agent, "UPDATE"), true, false},
		{"broker-changes", sessionWrite(raised, changed, broker, "UPDATE"), true, false},
		{"keeper-removes", sessionWrite(raised, plain, keeper, "UPDATE"), true, false},
		{"admin-adds", sessionWrite(plain, raised, raiseAdmin, "UPDATE"), true, false},
		{"flux-adds", sessionWrite(plain, raised, raiseFlux, "UPDATE"), true, false},
		{"agent-adds-empty-value", sessionWrite(plain, emptied, agent, "UPDATE"), true, false},
		{"agent-drops-annotations-map", sessionWrite(raised, raiseSession(depth, nil), agent, "UPDATE"), true, false},
		{"flux-unchanged-with-record", sessionWrite(raised, clone(raised), raiseFlux, "UPDATE"), true, true},
		{"flux-unchanged-no-maps", sessionWrite(bare, clone(bare), raiseFlux, "UPDATE"), true, true},
		{"collector-owner-change", sessionWrite(raised, owned, raiseCollector, "UPDATE"), true, true},
		{"admin-finalizer-change", sessionWrite(raised, finalized, raiseAdmin, "UPDATE"), true, true},
		{"agent-other-annotation", sessionWrite(raised, other, agent, "UPDATE"), true, true},
		{"flux-create-without-record", sessionWrite(nil, plain, raiseFlux, "CREATE"), true, true},
		{"flux-create-no-maps", sessionWrite(nil, bare, raiseFlux, "CREATE"), true, true},
		{"operator-create-with-record", sessionWrite(nil, raised, raiseOperator, "CREATE"), true, true},
		{"agent-create-with-record", sessionWrite(nil, raised, agent, "CREATE"), true, false},
		{"status-subresource-skipped", statusWrite, false, true},
		{"status-unchanged-skipped", func() fixture {
			f := sessionWrite(plain, statusChange, raiseOperator, "UPDATE")
			f.subresource = "status"
			return f
		}(), false, true},
		{"delete-skipped", sessionWrite(raised, nil, agent, "DELETE"), false, true},
	})
}

func TestSessionDepthLabel(t *testing.T) {
	h := loadRaiseVAP(t, "label-guards.yaml", "dev-env-session-depth-label")
	const depthKey = "dev-env.haynesops.com/depth"
	two := raiseSession(obj{depthKey: "2"}, nil)
	three := raiseSession(obj{depthKey: "3"}, nil)
	empty := raiseSession(obj{depthKey: ""}, nil)
	noDepth := raiseSession(obj{"fixture/other": "x"}, nil)
	bare := raiseSession(nil, nil)
	raised := raiseSession(obj{depthKey: "2"}, obj{"dev-env.haynesops.com/raise": `{"v":1}`})
	finalized := clone(two)
	finalized["metadata"].(obj)["finalizers"] = []any{"dev-env.haynesops.com/rescue"}
	statusWrite := sessionWrite(two, three, raiseOperator, "UPDATE")
	statusWrite.subresource = "status"
	runRaise(t, h, []raiseCase{
		{"operator-changes", sessionWrite(two, three, raiseOperator, "UPDATE"), true, false},
		{"operator-removes", sessionWrite(two, noDepth, raiseOperator, "UPDATE"), true, false},
		{"operator-drops-labels-map", sessionWrite(two, bare, raiseOperator, "UPDATE"), true, false},
		{"admin-garbles", sessionWrite(two, raiseSession(obj{depthKey: "x"}, nil), raiseAdmin, "UPDATE"), true, false},
		{"agent-adds", sessionWrite(noDepth, two, agent, "UPDATE"), true, false},
		{"agent-adds-empty", sessionWrite(noDepth, empty, agent, "UPDATE"), true, false},
		{"operator-writes-record", sessionWrite(two, raised, raiseOperator, "UPDATE"), true, true},
		{"flux-unchanged", sessionWrite(two, clone(two), raiseFlux, "UPDATE"), true, true},
		{"flux-unchanged-no-maps", sessionWrite(bare, clone(bare), raiseFlux, "UPDATE"), true, true},
		{"collector-finalizer", sessionWrite(two, finalized, raiseCollector, "UPDATE"), true, true},
		{"create-skipped", sessionWrite(nil, two, agent, "CREATE"), false, true},
		{"delete-skipped", sessionWrite(two, nil, agent, "DELETE"), false, true},
		{"status-skipped", statusWrite, false, true},
	})
}

func raisePod(labels obj) obj {
	meta := obj{"name": "fixture", "namespace": ns, "uid": "uid-fixture"}
	if labels != nil {
		meta["labels"] = labels
	}
	return obj{"apiVersion": "v1", "kind": "Pod", "metadata": meta, "spec": podSpec("")}
}

func podWrite(old, next obj, actor, operation string) fixture {
	return fixture{kind: "Pod", resource: "pods", old: old, new: next, actor: actor, operation: operation}
}

func TestSessionPodProfileLabel(t *testing.T) {
	h := loadRaiseVAP(t, "label-guards.yaml", "dev-env-session-pod-profile-label")
	const profileKey = "dev-env.haynesops.com/profile"
	const app = "app.kubernetes.io/name"
	full := raisePod(obj{app: "dev-env-session", profileKey: "full"})
	workbench := raisePod(obj{app: "dev-env-session", profileKey: "workbench"})
	unlabelled := raisePod(obj{app: "dev-env-session"})
	stripped := raisePod(obj{profileKey: "workbench"})
	owned := clone(full)
	owned["metadata"].(obj)["ownerReferences"] = []any{obj{"apiVersion": "v1", "kind": "ConfigMap", "name": "fixture-owner", "uid": "uid-owner"}}
	finalized := clone(full)
	finalized["metadata"].(obj)["finalizers"] = []any{"dev-env.haynesops.com/rescue"}
	toolPod := raisePod(obj{app: "fixture-tool", profileKey: "full"})
	toolPodChanged := raisePod(obj{app: "fixture-tool", profileKey: "workbench"})
	elsewhere := podWrite(full, workbench, raiseAdmin, "UPDATE")
	elsewhere.namespace = "dev-tools"
	status := podWrite(full, workbench, "system:node:fixture-node", "UPDATE")
	status.subresource = "status"
	binding := podWrite(full, workbench, "system:kube-scheduler", "UPDATE")
	binding.subresource = "binding"
	runRaise(t, h, []raiseCase{
		{"operator-changes", podWrite(full, workbench, raiseOperator, "UPDATE"), true, true},
		{"admin-changes", podWrite(full, workbench, raiseAdmin, "UPDATE"), true, false},
		{"agent-changes", podWrite(full, workbench, agent, "UPDATE"), true, false},
		{"keeper-changes", podWrite(full, workbench, keeper, "UPDATE"), true, false},
		{"admin-removes", podWrite(full, unlabelled, raiseAdmin, "UPDATE"), true, false},
		{"admin-adds", podWrite(unlabelled, full, raiseAdmin, "UPDATE"), true, false},
		{"admin-strips-selector-and-changes", podWrite(full, stripped, raiseAdmin, "UPDATE"), true, false},
		{"collector-owner-change", podWrite(full, owned, raiseCollector, "UPDATE"), true, true},
		{"admin-finalizer-change", podWrite(full, finalized, raiseAdmin, "UPDATE"), true, true},
		{"flux-unchanged", podWrite(full, clone(full), raiseFlux, "UPDATE"), true, true},
		{"unchanged-without-profile", podWrite(unlabelled, clone(unlabelled), raiseAdmin, "UPDATE"), true, true},
		{"other-namespace-skipped", elsewhere, false, true},
		{"non-session-pod-skipped", podWrite(toolPod, toolPodChanged, raiseAdmin, "UPDATE"), false, true},
		{"create-skipped", podWrite(nil, full, raiseAdmin, "CREATE"), false, true},
		{"delete-skipped", podWrite(full, nil, raiseAdmin, "DELETE"), false, true},
		{"status-skipped", status, false, true},
		{"binding-skipped", binding, false, true},
	})
}

func raiseConfigMap(name string) obj {
	return obj{"apiVersion": "v1", "kind": "ConfigMap", "metadata": obj{"name": name, "namespace": raiseSystem}}
}

func configMapWrite(name, namespace, actor, operation string) fixture {
	old := raiseConfigMap(name)
	old["metadata"].(obj)["namespace"] = namespace
	next := clone(old)
	next["data"] = obj{"ledger.json": "{}"}
	f := fixture{kind: "ConfigMap", resource: "configmaps", name: name, namespace: namespace, old: old, new: next, actor: actor, operation: operation}
	switch operation {
	case "CREATE":
		f.old = nil
	case "DELETE":
		f.new = nil
	}
	return f
}

func TestSessionRaiseLedgers(t *testing.T) {
	for _, owner := range []struct {
		policy, actor, stranger string
		names                   []string
	}{
		{"dev-env-session-raise-operator-ledger", raiseOperator, keeper,
			[]string{"dev-env-session-raise-ledger", "dev-env-session-raise-operator-public"}},
		{"dev-env-session-raise-keeper-ledger", keeper, raiseOperator,
			[]string{"dev-env-keeper-raise-notices", "dev-env-session-raise-keeper-public"}},
	} {
		h := loadRaiseVAP(t, "ledger-guards.yaml", owner.policy)
		for _, name := range owner.names {
			t.Run(owner.policy+"/"+name, func(t *testing.T) {
				runRaise(t, h, []raiseCase{
					{"owner-updates", configMapWrite(name, raiseSystem, owner.actor, "UPDATE"), true, true},
					{"owner-deletes", configMapWrite(name, raiseSystem, owner.actor, "DELETE"), true, true},
					{"other-owner-updates", configMapWrite(name, raiseSystem, owner.stranger, "UPDATE"), true, false},
					{"flux-updates", configMapWrite(name, raiseSystem, raiseFlux, "UPDATE"), true, false},
					{"admin-updates", configMapWrite(name, raiseSystem, raiseAdmin, "UPDATE"), true, false},
					{"admin-deletes", configMapWrite(name, raiseSystem, raiseAdmin, "DELETE"), true, false},
					{"collector-deletes", configMapWrite(name, raiseSystem, raiseCollector, "DELETE"), true, false},
					{"agent-updates", configMapWrite(name, raiseSystem, agent, "UPDATE"), true, false},
					{"flux-creates-skipped", configMapWrite(name, raiseSystem, raiseFlux, "CREATE"), false, true},
					{"other-namespace-skipped", configMapWrite(name, ns, raiseAdmin, "UPDATE"), false, true},
				})
			})
		}
		t.Run(owner.policy+"/other-configmap-skipped", func(t *testing.T) {
			runRaise(t, h, []raiseCase{
				{"templates", configMapWrite("dev-env-templates", raiseSystem, raiseFlux, "UPDATE"), false, true},
				{"ask-ledger", configMapWrite("dev-env-keeper-ask-notifications", raiseSystem, raiseAdmin, "DELETE"), false, true},
			})
		})
	}
}
