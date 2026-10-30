package archiveguard

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	policies "github.com/kyverno/api/api/policies.kyverno.io/v1beta1"
	celengine "github.com/kyverno/kyverno/pkg/cel/engine"
	"github.com/kyverno/kyverno/pkg/cel/libs"
	celmatching "github.com/kyverno/kyverno/pkg/cel/matching"
	"github.com/kyverno/kyverno/pkg/cel/policies/vpol/compiler"
	vpolengine "github.com/kyverno/kyverno/pkg/cel/policies/vpol/engine"
	"github.com/kyverno/kyverno/pkg/clients/dclient"
	engineapi "github.com/kyverno/kyverno/pkg/engine/api"
	admissionv1 "k8s.io/api/admission/v1"
	authentication "k8s.io/api/authentication/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/yaml"
)

func maintenanceLifecyclePolicies(t *testing.T) []*policies.ValidatingPolicy {
	t.Helper()
	raw, err := os.ReadFile(repoPath("kubernetes/main/apps/kyverno/policies/app/maintenance-executor.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	var result []*policies.ValidatingPolicy
	for _, doc := range strings.Split(string(raw), "\n---\n") {
		var p policies.ValidatingPolicy
		if err = yaml.Unmarshal([]byte(doc), &p); err != nil {
			t.Fatal(err)
		}
		result = append(result, &p)
	}
	result = append(result, load(t, "kubernetes/main/apps/kyverno/policies/app/maintenance-exec.yaml"))
	return result
}

// Exercise the real webhook Engine.Handle/ExtractResources path, not a manually
// constructed AttributesRecord. resource.Get uses the actual context provider and
// client-go dynamic HTTP client against a bounded synthetic API endpoint. No fake
// GetResource implementation supplies a preconverted map and no live API is used.
func TestMaintenanceWebhookLifecycle(t *testing.T) {
	var objects map[string]obj
	var calls []string
	api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls = append(calls, r.Method+" "+r.URL.Path)
		if r.Method != "GET" {
			t.Errorf("unexpected API write: %s", r.Method)
			http.Error(w, "read-only", 405)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if value, ok := objects[r.URL.Path]; ok {
			_ = json.NewEncoder(w).Encode(value)
			return
		}
		w.WriteHeader(404)
		_ = json.NewEncoder(w).Encode(obj{"apiVersion": "v1", "kind": "Status", "status": "Failure", "reason": "NotFound", "code": 404})
	}))
	defer api.Close()
	dyn, err := dynamic.NewForConfig(&rest.Config{Host: api.URL})
	if err != nil {
		t.Fatal(err)
	}
	disco := dclient.NewFakeDiscoveryClient([]schema.GroupVersionResource{{Group: "dev-env.haynesops.com", Version: "v1alpha1", Resource: "accessgrants"}, {Version: "v1", Resource: "pods"}, {Version: "v1", Resource: "nodes"}})
	contextProvider, err := libs.NewContextProvider(dclient.NewFakeClientWithDisco(dyn, nil, disco), nil, nil, nil, true)
	if err != nil {
		t.Fatal(err)
	}
	source := maintenanceLifecyclePolicies(t)
	provider, err := vpolengine.NewProvider(compiler.NewCompiler(), []policies.ValidatingPolicyLike{source[0], source[1], source[2]}, nil)
	if err != nil {
		t.Fatal(err)
	}
	namespace := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: ns}}
	engine := vpolengine.NewEngine(provider, func(string) *corev1.Namespace { return namespace }, celmatching.NewMatcher())
	original := source[1].DeepCopy()
	original.Spec.Variables[0].Expression = "object == null ? oldObject : object"
	originalProvider, err := vpolengine.NewProvider(compiler.NewCompiler(), []policies.ValidatingPolicyLike{original}, nil)
	if err != nil {
		t.Fatal(err)
	}
	originalEngine := vpolengine.NewEngine(originalProvider, func(string) *corev1.Namespace { return namespace }, celmatching.NewMatcher())

	grantObj := obj{"apiVersion": "dev-env.haynesops.com/v1alpha1", "kind": "AccessGrant", "metadata": obj{"name": "grant-fixture", "namespace": ns, "uid": "grant-uid"}, "spec": obj{"type": "maintenance", "maintenance": obj{"node": "fixture-node", "nodeUID": "node-uid", "image": "registry.example/tool@sha256:" + strings.Repeat("a", 64), "capabilities": []any{"SYS_ADMIN"}, "cpuMillis": float64(100), "memoryMiB": float64(128)}}, "status": obj{"phase": "Active"}}
	owner := obj{"apiVersion": "dev-env.haynesops.com/v1alpha1", "kind": "AccessGrant", "name": "grant-fixture", "uid": "grant-uid", "controller": true}
	metadata := obj{"name": "grant-fixture", "namespace": ns, "uid": "executor-uid", "labels": obj{"dev-env.haynesops.com/maintenance": "grant-uid", "dev-env.haynesops.com/grant": "grant-fixture"}, "ownerReferences": []any{owner}}
	network := obj{"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": clone(metadata), "spec": obj{"podSelector": obj{"matchLabels": obj{"dev-env.haynesops.com/maintenance": "grant-uid"}}, "policyTypes": []any{"Ingress", "Egress"}}}
	pod := obj{"apiVersion": "v1", "kind": "Pod", "metadata": clone(metadata), "spec": obj{"nodeName": "fixture-node", "serviceAccountName": "dev-env-maintenance", "automountServiceAccountToken": false, "restartPolicy": "Never", "enableServiceLinks": false, "activeDeadlineSeconds": float64(1800), "terminationGracePeriodSeconds": float64(10), "securityContext": obj{"seccompProfile": obj{"type": "RuntimeDefault"}}, "containers": []any{obj{"name": "maintenance", "image": "registry.example/tool@sha256:" + strings.Repeat("a", 64), "command": []any{"/bin/sleep", "infinity"}, "securityContext": obj{"privileged": false, "allowPrivilegeEscalation": false, "readOnlyRootFilesystem": true, "runAsUser": float64(0), "capabilities": obj{"drop": []any{"ALL"}, "add": []any{"SYS_ADMIN"}}}, "resources": obj{"requests": obj{"cpu": "100m", "memory": "128Mi"}, "limits": obj{"cpu": "100m", "memory": "128Mi"}}}}}, "status": obj{"phase": "Failed"}}
	ordinary := clone(pod)
	ordinary["metadata"].(obj)["name"] = "ordinary"
	ordinary["metadata"].(obj)["labels"] = obj{}
	ordinary["spec"].(obj)["serviceAccountName"] = "default"
	options := obj{"apiVersion": "v1", "kind": "PodExecOptions", "container": "maintenance", "command": []any{"true"}, "stdin": false, "tty": false}
	type scenario struct {
		name, policy, kind, resource, group, operation, actor, subresource string
		next, old                                                          obj
		want                                                               engineapi.RuleStatus
		mutate                                                             func(map[string]obj)
	}
	cases := []scenario{
		{name: "original-network-delete-error", policy: "dev-env-maintenance-network", kind: "NetworkPolicy", resource: "networkpolicies", group: "networking.k8s.io", operation: "DELETE", actor: broker, old: network, want: engineapi.RuleStatusError},
		{name: "network-create", policy: "dev-env-maintenance-network", kind: "NetworkPolicy", resource: "networkpolicies", group: "networking.k8s.io", operation: "CREATE", actor: broker, next: network, want: engineapi.RuleStatusPass},
		{name: "network-delete", policy: "dev-env-maintenance-network", kind: "NetworkPolicy", resource: "networkpolicies", group: "networking.k8s.io", operation: "DELETE", actor: broker, old: network, want: engineapi.RuleStatusPass},
		{name: "network-wrong-grant", policy: "dev-env-maintenance-network", kind: "NetworkPolicy", resource: "networkpolicies", group: "networking.k8s.io", operation: "DELETE", actor: broker, old: network, want: engineapi.RuleStatusFail, mutate: func(m map[string]obj) {
			m["/apis/dev-env.haynesops.com/v1alpha1/namespaces/"+ns+"/accessgrants/grant-fixture"]["spec"].(obj)["type"] = "kube"
		}},
		{name: "network-wrong-uid", policy: "dev-env-maintenance-network", kind: "NetworkPolicy", resource: "networkpolicies", group: "networking.k8s.io", operation: "DELETE", actor: broker, old: network, want: engineapi.RuleStatusFail, mutate: func(m map[string]obj) {
			m["/apis/dev-env.haynesops.com/v1alpha1/namespaces/"+ns+"/accessgrants/grant-fixture"]["metadata"].(obj)["uid"] = "replacement"
		}},
		{name: "pod-create", policy: "dev-env-maintenance-executor", kind: "Pod", resource: "pods", operation: "CREATE", actor: broker, next: pod, want: engineapi.RuleStatusPass},
		{name: "terminal-pod-delete", policy: "dev-env-maintenance-executor", kind: "Pod", resource: "pods", operation: "DELETE", actor: broker, old: pod, want: engineapi.RuleStatusPass},
		{name: "nonterminal-pod-delete", policy: "dev-env-maintenance-executor", kind: "Pod", resource: "pods", operation: "DELETE", actor: broker, old: pod, want: engineapi.RuleStatusFail, mutate: nil},
		{name: "ordinary-create", policy: "dev-env-maintenance-executor", kind: "Pod", resource: "pods", operation: "CREATE", actor: agent, next: ordinary, want: engineapi.RuleStatusSkip},
		{name: "ordinary-delete", policy: "dev-env-maintenance-executor", kind: "Pod", resource: "pods", operation: "DELETE", actor: agent, old: ordinary, want: engineapi.RuleStatusSkip},
		{name: "operator-connect", policy: "dev-env-maintenance-exec", kind: "PodExecOptions", resource: "pods", operation: "CONNECT", actor: sa("dev-env-system/dev-env-operator"), subresource: "exec", next: options, want: engineapi.RuleStatusPass},
		{name: "agent-connect", policy: "dev-env-maintenance-exec", kind: "PodExecOptions", resource: "pods", operation: "CONNECT", actor: agent, subresource: "exec", next: options, want: engineapi.RuleStatusFail},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			objects = map[string]obj{"/apis/dev-env.haynesops.com/v1alpha1/namespaces/" + ns + "/accessgrants/grant-fixture": clone(grantObj), "/api/v1/nodes/fixture-node": {"apiVersion": "v1", "kind": "Node", "metadata": obj{"name": "fixture-node", "uid": "node-uid"}, "spec": obj{}}, "/api/v1/namespaces/" + ns + "/pods/grant-fixture": clone(pod)}
			calls = nil
			if test.mutate != nil {
				test.mutate(objects)
			}
			req := admissionv1.AdmissionRequest{Kind: metav1.GroupVersionKind{Group: test.group, Version: "v1", Kind: test.kind}, Resource: metav1.GroupVersionResource{Group: test.group, Version: "v1", Resource: test.resource}, Namespace: ns, Name: "grant-fixture", Operation: admissionv1.Operation(test.operation), SubResource: test.subresource, UserInfo: authentication.UserInfo{Username: test.actor}}
			if test.next != nil {
				raw, _ := json.Marshal(test.next)
				req.Object = runtime.RawExtension{Raw: raw}
			}
			if test.old != nil {
				old := clone(test.old)
				if test.name == "nonterminal-pod-delete" {
					old["status"].(obj)["phase"] = "Running"
				}
				raw, _ := json.Marshal(old)
				req.OldObject = runtime.RawExtension{Raw: raw}
			}
			wire, _ := json.Marshal(req)
			var normalized admissionv1.AdmissionRequest
			if err = json.Unmarshal(wire, &normalized); err != nil {
				t.Fatal(err)
			}
			if test.operation == "DELETE" && normalized.Object.Raw != nil {
				t.Fatal("DELETE must omit new resource")
			}
			selectedEngine := engine
			if test.name == "original-network-delete-error" {
				selectedEngine = originalEngine
			}
			result, err := selectedEngine.Handle(context.Background(), celengine.RequestFromAdmission(contextProvider, normalized), func(p policies.ValidatingPolicyLike) bool { return p.GetName() == test.policy })
			if err != nil {
				t.Fatal(err)
			}
			found := false
			for _, response := range result.Policies {
				for _, rule := range response.Rules {
					found = true
					if test.name == "original-network-delete-error" && (!strings.Contains(rule.Message(), "no such key: metadata") || len(calls) != 0) {
						t.Fatalf("wrong original error or unexpected GET: %s HTTP=%v", rule.Message(), calls)
					}
					if rule.Status() != test.want {
						t.Fatalf("status=%s want=%s message=%s HTTP=%v", rule.Status(), test.want, rule.Message(), calls)
					}
				}
			}
			if !found {
				t.Fatal("zero evaluated rules is not a pass")
			}
			if (test.operation == "DELETE" || test.operation == "CREATE") && test.want == engineapi.RuleStatusSkip && len(calls) != 0 {
				t.Fatalf("ordinary skip made lookup: %v", calls)
			}
		})
	}
}
