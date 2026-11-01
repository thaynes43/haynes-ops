package archiveguard

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	admissionv1 "k8s.io/api/admission/v1"
	authenticationv1 "k8s.io/api/authentication/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apiserver/pkg/admission"
	"k8s.io/apiserver/pkg/authentication/user"
)

// Exercise the published native predicate through the pinned policy compiler.
// A CEL error must never count as a successful negative fixture.
func TestReservedBreakglassDecision(t *testing.T) {
	h := compile(t, loadVAP(t, "kubernetes/main/apps/dev-env-system/rbac/app/maintenance-annotations.yaml"))
	operator := sa("dev-env-system/dev-env-operator")
	key := "dev-env.haynesops.com/maintenance-decision"
	for _, tc := range []struct {
		name  string
		allow bool
	}{
		{"reserved-breakglass", true}, {"maintenance", true},
		{"ordinary", false}, {"unreserved", false}, {"missing-reserved", false},
		{"wrong-role", false}, {"missing-kube", false}, {"broker", false},
		{"agent", false}, {"human", false}, {"spec-change", false},
		{"repeat", false}, {"clear", false}, {"status-change", false},
		{"create", false}, {"oversize", false}, {"limit", true},
		{"restore-change", false}, {"status-preserved", true},
		{"finalizer-preserved", true}, {"release-preserved", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			old := obj{"apiVersion": "dev-env.haynesops.com/v1alpha1", "kind": "AccessGrant", "metadata": obj{"name": "fixture", "namespace": ns}, "spec": obj{"type": "breakglass", "kube": obj{"reserved": true, "role": "dev-env-grant-breakglass"}}}
			spec := old["spec"].(obj)
			switch tc.name {
			case "maintenance":
				spec["type"] = "maintenance"
				delete(spec, "kube")
			case "ordinary":
				spec["type"] = "kube"
			case "unreserved":
				spec["kube"].(obj)["reserved"] = false
			case "missing-reserved":
				delete(spec["kube"].(obj), "reserved")
			case "wrong-role":
				spec["kube"].(obj)["role"] = "other"
			case "missing-kube":
				delete(spec, "kube")
			}
			next := clone(old)
			next["metadata"].(obj)["annotations"] = obj{key: "synthetic decision"}
			f := fixture{kind: "AccessGrant", resource: "accessgrants", group: "dev-env.haynesops.com", old: old, new: next, actor: operator, operation: "UPDATE", wantCalls: 0}
			switch tc.name {
			case "broker":
				f.actor = broker
			case "agent":
				f.actor = agent
			case "human":
				f.actor = sa("dev-env-system/dev-env-human")
			case "spec-change":
				next["spec"].(obj)["release"] = true
			case "repeat", "clear":
				old["metadata"].(obj)["annotations"] = obj{key: "prior decision"}
				if tc.name == "clear" {
					delete(next["metadata"].(obj), "annotations")
				}
			case "status-change":
				f.subresource = "status"
			case "create":
				f.operation = "CREATE"
				f.old = nil
			case "oversize":
				next["metadata"].(obj)["annotations"] = obj{key: strings.Repeat("x", 2049)}
			case "limit":
				next["metadata"].(obj)["annotations"] = obj{key: strings.Repeat("x", 2048)}
			case "restore-change":
				next["metadata"].(obj)["annotations"].(obj)["dev-env.haynesops.com/maintenance-restore"] = "report"
			case "status-preserved", "finalizer-preserved", "release-preserved":
				old["metadata"].(obj)["annotations"] = obj{key: "synthetic decision"}
				f.actor = broker
				if tc.name == "status-preserved" {
					f.subresource = "status"
					next["status"] = obj{"phase": "Active"}
				}
				if tc.name == "finalizer-preserved" {
					next["metadata"].(obj)["finalizers"] = []any{"dev-env.haynesops.com/revoke"}
				}
				if tc.name == "release-preserved" {
					f.actor = operator
					next["spec"].(obj)["release"] = true
				}
			}
			allowed, result := h.evaluate(t, f)
			if result == nil || result.Error != nil {
				t.Fatalf("CEL error or unmatched fixture: %+v", result)
			}
			if allowed != tc.allow {
				t.Fatalf("allowed=%v want=%v result=%+v", allowed, tc.allow, result)
			}
		})
	}
}

// Evaluate the unchanged source expressions with the real pinned request builder.
// The explicit-empty case alone supplies that key; the normal path omits it.
// Bypass resource matching for the impossible "unexpected" subresource to prove
// the expression itself denies protected-annotation changes if invoked there.
func TestMaintenanceAnnotationSubresources(t *testing.T) {
	h := compile(t, loadVAP(t, "kubernetes/main/apps/dev-env-system/rbac/app/maintenance-annotations.yaml"))
	operator := sa("dev-env-system/dev-env-operator")
	for _, annotation := range []string{"maintenance-decision", "maintenance-restore"} {
		for _, test := range []struct {
			name, actor, subresource        string
			explicitEmpty, unchanged, allow bool
		}{
			{"omitted", operator, "", false, false, true},
			{"explicit-empty", operator, "", true, false, true},
			{"status-change", operator, "status", false, false, false},
			{"unexpected-change", operator, "unexpected", false, false, false},
			{"agent-change", agent, "", false, false, false},
			{"broker-change", broker, "", false, false, false},
			{"human-change", sa("dev-env-system/dev-env-human"), "", false, false, false},
			{"broker-status-unchanged", broker, "status", false, true, true},
			{"agent-status-unchanged", agent, "status", false, true, true},
		} {
			t.Run(annotation+"/"+test.name, func(t *testing.T) {
				old := obj{"apiVersion": "dev-env.haynesops.com/v1alpha1", "kind": "AccessGrant", "metadata": obj{"name": "fixture", "namespace": ns}, "spec": obj{"type": "maintenance"}}
				next := clone(old)
				if !test.unchanged {
					next["metadata"].(obj)["annotations"] = obj{"dev-env.haynesops.com/" + annotation: "synthetic report"}
				}
				gvk := schema.GroupVersionKind{Group: "dev-env.haynesops.com", Version: "v1alpha1", Kind: "AccessGrant"}
				gvr := schema.GroupVersionResource{Group: gvk.Group, Version: gvk.Version, Resource: "accessgrants"}
				req := admissionv1.AdmissionRequest{Kind: metav1.GroupVersionKind(gvk), Resource: metav1.GroupVersionResource(gvr), Namespace: ns, Name: "fixture", Operation: admissionv1.Update, SubResource: test.subresource, UserInfo: authenticationv1.UserInfo{Username: test.actor}}
				attr := admission.NewAttributesRecord(fixtureObject(next), fixtureObject(old), gvk, ns, "fixture", gvr, test.subresource, admission.Update, nil, false, &user.DefaultInfo{Name: test.actor})
				raw, err := json.Marshal(req)
				if err != nil {
					t.Fatal(err)
				}
				var normalized map[string]any
				if err = json.Unmarshal(raw, &normalized); err != nil {
					t.Fatal(err)
				}
				if test.subresource == "" {
					if _, ok := normalized["subResource"]; ok {
						t.Fatal("normal empty field must be omitted")
					}
				}
				var requestMap func() (map[string]any, error)
				if test.explicitEmpty {
					normalized["subResource"] = ""
					requestMap = func() (map[string]any, error) { return normalized, nil }
				}
				result, err := h.policy.Evaluate(context.Background(), nil, attr, &req, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: ns}}, requestMap, h.mock)
				if err != nil || result == nil || result.Error != nil {
					t.Fatalf("engine error/unmatched is not a denial: result=%+v err=%v", result, err)
				}
				if result.Result != test.allow {
					t.Fatalf("allowed=%v want=%v result=%+v", result.Result, test.allow, result)
				}
			})
		}
	}
}
