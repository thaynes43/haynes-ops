package archiveguard

import (
	"slices"
	"testing"
	"time"
)

const (
	podExecGrantName = "grant-pod-exec-fixture"
	podExecGrantAPI  = "dev-env.haynesops.com/v1alpha1"
)

var podExecActor = sa("dev-agents/" + podExecGrantName)

func podExecRequest(namespace, name, container, actor string) fixture {
	return connectFixture(summoningConnectCase{"exec", "exec", name,
		obj{"apiVersion": "v1", "kind": "PodExecOptions", "container": container}}, namespace, actor, nil, false)
}

func TestPodExecGrantAdmission(t *testing.T) {
	h := compile(t, load(t, execGuardPath))
	now := time.Now().UTC()
	const uid = "11111111-1111-1111-1111-111111111111"
	for _, tc := range []struct {
		name               string
		edit               func(*fixture, obj, obj)
		allow, lookupError bool
		lookups            []string
	}{
		{name: "exact guarded target", allow: true, lookups: []string{"grant", "pod"}},
		{name: "other pod", edit: func(f *fixture, _, _ obj) { f.name = "other" }, lookups: []string{"grant"}},
		{name: "other namespace", edit: func(f *fixture, _, _ obj) { f.namespace = "other" }, lookups: []string{"grant"}},
		{name: "other container", edit: func(f *fixture, _, _ obj) { f.new["container"] = "sidecar" }, lookups: []string{"grant"}},
		{name: "other container of ordinary pod", edit: func(f *fixture, _, pod obj) {
			pod["spec"].(obj)["hostPID"] = false
			f.new["container"] = "sidecar"
		}, lookups: []string{"grant"}},
		{name: "container omitted", edit: func(f *fixture, _, _ obj) { delete(f.new, "container") }, lookups: []string{"grant"}},
		{name: "pod replaced", edit: func(_ *fixture, _, pod obj) {
			pod["metadata"].(obj)["uid"] = "22222222-2222-2222-2222-222222222222"
		}, lookups: []string{"grant", "pod"}},
		{name: "inactive", edit: func(_ *fixture, g, _ obj) { g["status"].(obj)["phase"] = "Pending" }, lookups: []string{"grant"}},
		{name: "expired during Active", edit: func(_ *fixture, g, _ obj) {
			g["status"].(obj)["expiresAt"] = now.Add(-time.Minute).Format(time.RFC3339)
		}, lookups: []string{"grant"}},
		{name: "release during Active", edit: func(_ *fixture, g, _ obj) { g["spec"].(obj)["release"] = true }, lookups: []string{"grant"}},
		{name: "grant deleting", edit: func(_ *fixture, g, _ obj) { g["metadata"].(obj)["deletionTimestamp"] = now.Format(time.RFC3339) }, lookups: []string{"grant"}},
		{name: "no authenticated approval", edit: func(_ *fixture, g, _ obj) { g["status"].(obj)["approvedBy"] = "policy/fixture" }, lookups: []string{"grant"}},
		{name: "no installed account", edit: func(_ *fixture, g, _ obj) { delete(g["status"].(obj), "serviceAccount") }, lookups: []string{"grant"}},
		{name: "other grant type", edit: func(_ *fixture, g, _ obj) { g["spec"].(obj)["type"] = "kube" }, lookups: []string{"grant"}},
		{name: "missing grant", edit: func(f *fixture, _, pod obj) { f.parents = []obj{pod} }, lookups: []string{"grant"}},
		{name: "missing pod", edit: func(f *fixture, g, _ obj) { f.parents = []obj{g} }, lookups: []string{"grant", "pod"}},
		{name: "lookup forbidden", edit: func(f *fixture, _, _ obj) { f.fault = "forbidden" }, lookupError: true, lookups: []string{"grant", "pod"}},
		{name: "lookup transport error", edit: func(f *fixture, _, _ obj) { f.fault = "transport" }, lookupError: true, lookups: []string{"grant", "pod"}},
		{name: "attach", edit: func(f *fixture, _, _ obj) {
			f.subresource, f.kind = "attach", "PodAttachOptions"
			f.new["kind"] = f.kind
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := obj{
				"apiVersion": podExecGrantAPI, "kind": "AccessGrant",
				"metadata": obj{"namespace": ns, "name": podExecGrantName},
				"spec":     obj{"type": "pod-exec", "podExec": obj{"namespace": "fixture", "pod": "target", "container": "app", "podUID": uid}},
				"status": obj{"phase": "Active", "ask": "ask-fixture", "approvedBy": "authentik/approver",
					"serviceAccount": podExecGrantName, "expiresAt": now.Add(30 * time.Minute).Format(time.RFC3339)},
			}
			pod := workload("Pod", "target", "")
			pod["metadata"].(obj)["namespace"], pod["metadata"].(obj)["uid"] = "fixture", uid
			pod["spec"].(obj)["hostPID"] = true
			pod["spec"].(obj)["serviceAccountName"] = "default"
			pod["spec"].(obj)["containers"] = []any{obj{"name": "app", "image": "fixture"}, obj{"name": "sidecar", "image": "fixture"}}
			f := podExecRequest("fixture", "target", "app", podExecActor)
			f.parents = []obj{g, pod}
			if tc.edit != nil {
				tc.edit(&f, g, pod)
			}
			allowed, result := h.evaluate(t, f)
			if allowed != tc.allow || (result != nil && result.Error != nil) != tc.lookupError {
				t.Fatalf("allow=%v, want %v; result=%+v", allowed, tc.allow, result)
			}
			want := make([]string, 0, len(tc.lookups))
			for _, lookup := range tc.lookups {
				switch lookup {
				case "grant":
					want = append(want, key(podExecGrantAPI, "accessgrants", ns, podExecGrantName))
				case "pod":
					want = append(want, key("v1", "pods", f.namespace, f.name))
				}
			}
			if !slices.Equal(h.mock.calls, want) {
				t.Fatalf("lookups=%v, want %v", h.mock.calls, want)
			}
		})
	}
}

func TestPodExecGrantProtectedNamespaces(t *testing.T) {
	h := compile(t, loadVAP(t, identityGuardPath))
	for _, namespace := range []string{"dev-agents", "dev-env-system", "dev-tools", "dev-env-admin-issuance",
		"kube-system", "kyverno", "flux-system", "external-secrets", "cert-manager", "ceph-csi", "openebs-system", "volsync-system", "k8tz"} {
		t.Run(namespace, func(t *testing.T) {
			f := podExecRequest(namespace, "target", "app", podExecActor)
			f.wantCalls = 0
			checkSummoningDecision(t, h, f, "dev-env-identity-guard")
		})
	}
}

func TestPodExecBrokerGuard(t *testing.T) {
	h := compile(t, loadVAP(t, "kubernetes/main/apps/dev-env-system/rbac/app/broker-guard.yaml"))
	for _, namespace := range []string{ns, "fixture", "kube-public"} {
		t.Run("direct exec/"+namespace, func(t *testing.T) {
			f := podExecRequest(namespace, "target", "app", broker)
			f.allow, f.wantCalls = namespace == ns, 0
			checkSummoningDecision(t, h, f, "dev-env-broker-guard")
		})
	}
	for _, mode := range []string{"exact role", "wide exec", "different pod", "non-grant role", "skipped namespace", "cleanup", "foreign cleanup"} {
		t.Run(mode, func(t *testing.T) {
			rules := []any{
				obj{"apiGroups": []any{""}, "resources": []any{"pods"}, "verbs": []any{"get"}, "resourceNames": []any{"target"}},
				obj{"apiGroups": []any{""}, "resources": []any{"pods/exec"}, "verbs": []any{"create"}, "resourceNames": []any{"target"}},
			}
			o := obj{"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "rules": rules,
				"metadata": obj{"name": podExecGrantName, "labels": obj{"dev-env.haynesops.com/grant": podExecGrantName, "dev-env.haynesops.com/grant-uid": "fixture"}}}
			f := fixture{name: podExecGrantName, kind: "Role", resource: "roles", group: "rbac.authorization.k8s.io",
				namespace: "fixture", actor: broker, operation: "CREATE", new: o}
			switch mode {
			case "exact role":
				f.allow = true
			case "wide exec":
				delete(rules[1].(obj), "resourceNames")
			case "different pod":
				rules[1].(obj)["resourceNames"] = []any{"other"}
			case "non-grant role":
				f.name, o["metadata"].(obj)["name"] = "grant-fixture", "grant-fixture"
			case "skipped namespace":
				f.namespace = "kube-node-lease"
			case "cleanup", "foreign cleanup":
				f.operation, f.old, f.new = "DELETE", f.new, nil
				f.allow = mode == "cleanup"
				if !f.allow {
					delete(o["metadata"].(obj)["labels"].(obj), "dev-env.haynesops.com/grant-uid")
				}
			}
			checkSummoningDecision(t, h, f, "dev-env-broker-guard")
		})
	}
	for _, mode := range []string{"exact binding", "different role", "different subject", "protected namespace", "cluster binding"} {
		t.Run(mode, func(t *testing.T) {
			ref := obj{"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": podExecGrantName}
			subject := obj{"kind": "ServiceAccount", "namespace": ns, "name": podExecGrantName}
			o := obj{"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": obj{"name": podExecGrantName},
				"roleRef": ref, "subjects": []any{subject}}
			f := fixture{name: podExecGrantName, kind: "RoleBinding", resource: "rolebindings", group: "rbac.authorization.k8s.io",
				namespace: "fixture", actor: broker, operation: "CREATE", new: o, allow: mode == "exact binding"}
			switch mode {
			case "different role":
				ref["name"] = "other"
			case "different subject":
				subject["name"] = "other"
			case "protected namespace":
				f.namespace = "dev-tools"
			case "cluster binding":
				f.kind, f.resource, f.namespace = "ClusterRoleBinding", "clusterrolebindings", "fixture"
				o["kind"] = f.kind
			}
			checkSummoningDecision(t, h, f, "dev-env-broker-guard")
		})
	}
}
