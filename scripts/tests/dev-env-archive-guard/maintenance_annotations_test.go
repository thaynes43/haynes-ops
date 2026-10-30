package archiveguard

import (
	"context"
	"encoding/json"
	"testing"

	admissionv1 "k8s.io/api/admission/v1"
	authenticationv1 "k8s.io/api/authentication/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apiserver/pkg/admission"
	"k8s.io/apiserver/pkg/authentication/user"
)

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
