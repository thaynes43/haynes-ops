package archiveguard

// These are finite synthetic admissions, not live webhook or authorization probes.
// Use the installed Kyverno version's real compiler, lazy variables, matcher and
// resource library. Never alias oldObject to object or rewrite validation in Go.
import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/go-containerregistry/pkg/v1/remote"
	policies "github.com/kyverno/api/api/policies.kyverno.io/v1beta1"
	"github.com/kyverno/kyverno/pkg/cel/libs"
	celmatching "github.com/kyverno/kyverno/pkg/cel/matching"
	"github.com/kyverno/kyverno/pkg/cel/policies/vpol/compiler"
	admissionv1 "k8s.io/api/admission/v1"
	registration "k8s.io/api/admissionregistration/v1"
	authentication "k8s.io/api/authentication/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	utilyaml "k8s.io/apimachinery/pkg/util/yaml"
	"k8s.io/apiserver/pkg/admission"
	"k8s.io/apiserver/pkg/authentication/user"
	"sigs.k8s.io/yaml"
)

type obj = map[string]any

const ns = "dev-agents"
const agent = "system:serviceaccount:dev-agents:dev-env-agent"
const grant = "system:serviceaccount:dev-agents:grant-fixture"
const broker = "system:serviceaccount:dev-env-system:dev-env-broker"
const keeper = "system:serviceaccount:dev-env-system:dev-env-keeper"

var trusted = []string{
	"dev/dev-env", "upgrade-agent/dev-env-ops", "frontend/headlamp",
	"flux-system/kustomize-controller", "flux-system/helm-controller", "dev-env-system/dev-env-operator",
	"database/cloudnative-pg", "network/multus", "rook-ceph/rook-ceph-system",
	"volsync-system/volsync", "kube-system/reloader",
}

func sa(s string) string         { return "system:serviceaccount:" + strings.ReplaceAll(s, "/", ":") }
func controller(s string) string { return sa("kube-system/" + s) }

type fixture struct {
	name, kind, resource, group, subresource, operation, actor, namespace string
	new, old                                                              obj
	parents                                                               []obj
	fault                                                                 string
	allow                                                                 bool
	wantCalls                                                             int // -1: only assert the bounded upper limit
}

// Embed the real fake context for the interface, but refuse every non-Get effect.
// The pinned SDK converts NotFound to null and other errors to CEL errors.
type mockContext struct {
	*libs.FakeContextProvider
	objects map[string]obj
	fault   string
	calls   []string
}

func key(api, resource, namespace, name string) string {
	return strings.Join([]string{api, resource, namespace, name}, "/")
}

func (m *mockContext) GetResource(api, resource, namespace, name string) (*unstructured.Unstructured, error) {
	k := key(api, resource, namespace, name)
	m.calls = append(m.calls, k)
	switch m.fault {
	case "forbidden":
		return nil, apierrors.NewForbidden(schema.GroupResource{Resource: resource}, name, errors.New("synthetic forbidden"))
	case "transport":
		return nil, errors.New("synthetic lookup transport failure")
	case "empty":
		return &unstructured.Unstructured{Object: obj{}}, nil
	case "null":
		return &unstructured.Unstructured{}, nil
	case "malformed":
		return &unstructured.Unstructured{Object: obj{"metadata": "malformed"}}, nil
	case "wrong-kind", "missing-uid", "wrong-name", "wrong-namespace":
		o := workload("Pod", name, "")
		switch m.fault {
		case "wrong-kind":
			o["kind"] = "Deployment"
		case "missing-uid":
			delete(o["metadata"].(obj), "uid")
		case "wrong-name":
			o["metadata"].(obj)["name"] = "wrong"
		case "wrong-namespace":
			o["metadata"].(obj)["namespace"] = "wrong"
		}
		return &unstructured.Unstructured{Object: o}, nil
	}
	if o, ok := m.objects[k]; ok {
		response := clone(o)
		if m.fault == "response-missing-name" || ((resource == "deployments" || resource == "cronjobs") && m.fault == "root-missing-name") {
			delete(response["metadata"].(obj), "name")
		}
		if m.fault == "response-wrong-name" || ((resource == "deployments" || resource == "cronjobs") && m.fault == "root-wrong-name") {
			response["metadata"].(obj)["name"] = "wrong-response-name"
		}
		return &unstructured.Unstructured{Object: response}, nil
	}
	return nil, apierrors.NewNotFound(schema.GroupResource{Resource: resource}, name)
}

func (*mockContext) ListResources(string, string, string, map[string]string) (*unstructured.UnstructuredList, error) {
	panic("unexpected ListResources; admission lookups must be bounded Gets")
}
func (*mockContext) PostResource(string, string, string, map[string]any) (*unstructured.Unstructured, error) {
	panic("unexpected runtime write")
}
func (*mockContext) GetImageData(string, []remote.Option) (map[string]any, error) {
	panic("unexpected image/provider call")
}
func (*mockContext) GetGlobalReference(string, string) (any, error) {
	panic("unexpected global context")
}
func (*mockContext) GenerateResources(string, []map[string]any) error {
	panic("unexpected generated workload")
}

type harness struct {
	policy *compiler.Policy
	mock   *mockContext
}

func compile(t *testing.T, p *policies.ValidatingPolicy) *harness {
	t.Helper()
	m := &mockContext{FakeContextProvider: libs.NewFakeContextProvider()}
	libs.LibraryContext = m
	pCompiled, errs := compiler.NewCompiler().Compile(p, nil)
	if len(errs) != 0 {
		t.Fatalf("actual pinned Kyverno compiler: %v", errs)
	}
	return &harness{policy: pCompiled, mock: m}
}

func repoPath(p string) string { return filepath.Join("..", "..", "..", p) }

func load(t *testing.T, path string) *policies.ValidatingPolicy {
	t.Helper()
	b, err := os.ReadFile(repoPath(path))
	if err != nil {
		t.Fatal(err)
	}
	var p policies.ValidatingPolicy
	if err := yaml.UnmarshalStrict(b, &p); err != nil {
		t.Fatal(err)
	}
	return &p
}

func fixtureObject(o obj) runtime.Object {
	if o == nil {
		return nil
	}
	return &unstructured.Unstructured{Object: clone(o)}
}

func (h *harness) evaluate(t *testing.T, f fixture) (bool, *compiler.EvaluationResult) {
	t.Helper()
	h.mock.calls = nil
	h.mock.fault = f.fault
	h.mock.objects = make(map[string]obj)
	for _, p := range f.parents {
		meta := p["metadata"].(obj)
		h.mock.objects[key(p["apiVersion"].(string), resourceFor(p["kind"].(string)), meta["namespace"].(string), meta["name"].(string))] = p
	}
	namespace := f.namespace
	if namespace == "" {
		namespace = ns
	}
	name := f.name
	if name == "" {
		name = "fixture"
		for _, o := range []obj{f.new, f.old} {
			if meta, ok := o["metadata"].(obj); ok {
				if n, ok := meta["name"].(string); ok {
					name = n
					break
				}
			}
		}
	}
	version := "v1"
	gvk := schema.GroupVersionKind{Group: f.group, Version: version, Kind: f.kind}
	gvr := schema.GroupVersionResource{Group: f.group, Version: version, Resource: f.resource}
	req := admissionv1.AdmissionRequest{
		Kind: metav1.GroupVersionKind(gvk), Resource: metav1.GroupVersionResource(gvr),
		Namespace: namespace, Name: name, Operation: admissionv1.Operation(f.operation), SubResource: f.subresource,
		UserInfo: authentication.UserInfo{Username: f.actor, Groups: []string{"system:authenticated"}},
	}
	attr := admission.NewAttributesRecord(fixtureObject(f.new), fixtureObject(f.old), gvk, namespace, name, gvr,
		f.subresource, admission.Operation(f.operation), nil, false, &user.DefaultInfo{Name: f.actor, Groups: req.UserInfo.Groups})
	namespaceObject := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace, Labels: map[string]string{"kubernetes.io/metadata.name": namespace}}}
	matched, err := celmatching.NewMatcher().Match(&celmatching.MatchCriteria{Constraints: h.policy.MatchConstraints()}, attr, namespaceObject)
	if err != nil {
		t.Fatal(err)
	}
	if !matched {
		return true, nil
	}
	// A nil request-map callback uses Kyverno's own builder for this synthetic request.
	result, err := h.policy.Evaluate(context.Background(), nil, attr, &req, namespaceObject, nil, h.mock)
	if err != nil {
		return false, &compiler.EvaluationResult{Error: err}
	}
	return result == nil || (result.Error == nil && result.Result), result
}

func (h *harness) check(t *testing.T, f fixture) {
	t.Helper()
	allow, result := h.evaluate(t, f)
	if allow != f.allow {
		t.Fatalf("allow=%v, want %v; result=%+v; lookups=%v", allow, f.allow, result, h.mock.calls)
	}
	if len(h.mock.calls) > 2 {
		t.Fatalf("unbounded lookups: %v", h.mock.calls)
	}
	if f.wantCalls >= 0 && len(h.mock.calls) != f.wantCalls {
		t.Fatalf("lookups=%v, want %d", h.mock.calls, f.wantCalls)
	}
}

func clone(o obj) obj {
	b, _ := json.Marshal(o)
	var c obj
	if err := json.Unmarshal(b, &c); err != nil {
		panic(err)
	}
	return c
}

func metadata(name string) obj {
	return obj{"name": name, "namespace": ns, "uid": "uid-" + name, "labels": obj{"app": "fixture"}}
}

func podSpec(claim string) obj {
	p := obj{"serviceAccountName": "fixture-sa", "containers": []any{obj{"name": "main", "image": "example.invalid/fixture:v1", "args": []any{"sleep"},
		"resources": obj{"limits": obj{"cpu": "100m"}}, "volumeMounts": []any{obj{"name": "archive", "mountPath": "/fixture"}}}}}
	if claim != "" {
		p["volumes"] = []any{obj{"name": "archive", "persistentVolumeClaim": obj{"claimName": claim}}}
	}
	return p
}

func workload(kind, name, claim string) obj {
	api := "apps/v1"
	if kind == "Pod" || kind == "ReplicationController" {
		api = "v1"
	}
	if kind == "Job" || kind == "CronJob" {
		api = "batch/v1"
	}
	o := obj{"apiVersion": api, "kind": kind, "metadata": metadata(name)}
	p := podSpec(claim)
	template := obj{"metadata": obj{"labels": obj{"app": "fixture"}, "annotations": obj{"keep": "same"}}, "spec": p}
	switch kind {
	case "Pod":
		o["spec"] = p
	case "CronJob":
		o["spec"] = obj{"schedule": "0 0 * * *", "jobTemplate": obj{"spec": obj{"template": template}}}
	case "ControllerRevision":
		template["$patch"] = "replace"
		o["data"] = obj{"spec": obj{"template": template}}
		o["revision"] = float64(1)
	default:
		o["spec"] = obj{"replicas": float64(1), "template": template}
	}
	return o
}

func template(o obj) obj {
	switch o["kind"] {
	case "Pod":
		return o
	case "ControllerRevision":
		return o["data"].(obj)["spec"].(obj)["template"].(obj)
	case "CronJob":
		return o["spec"].(obj)["jobTemplate"].(obj)["spec"].(obj)["template"].(obj)
	default:
		return o["spec"].(obj)["template"].(obj)
	}
}

func own(child, parent obj) {
	m := parent["metadata"].(obj)
	child["metadata"].(obj)["ownerReferences"] = []any{obj{"apiVersion": parent["apiVersion"], "kind": parent["kind"], "name": m["name"], "uid": m["uid"], "controller": true}}
}

func resourceFor(kind string) string {
	return map[string]string{"Pod": "pods", "PodExecOptions": "pods", "PodAttachOptions": "pods", "PodPortForwardOptions": "pods", "Scale": "deployments", "ReplicationController": "replicationcontrollers", "Deployment": "deployments", "ReplicaSet": "replicasets", "StatefulSet": "statefulsets", "DaemonSet": "daemonsets", "Job": "jobs", "CronJob": "cronjobs", "ControllerRevision": "controllerrevisions"}[kind]
}

func request(o obj, actor, operation string, allow bool) fixture {
	api := o["apiVersion"].(string)
	group := ""
	if strings.Contains(api, "/") {
		group = strings.Split(api, "/")[0]
	}
	return fixture{kind: o["kind"].(string), resource: resourceFor(o["kind"].(string)), group: group, new: o, actor: actor, operation: operation, allow: allow, wantCalls: -1}
}

func run(t *testing.T, h *harness, name string, f fixture) {
	t.Run(name, func(t *testing.T) { h.check(t, f) })
}

func TestArchiveGuard(t *testing.T) {
	p := load(t, "kubernetes/main/apps/kyverno/policies/app/dev-env-archive-guard.yaml")
	if p.Spec.FailurePolicy == nil || *p.Spec.FailurePolicy != registration.Fail {
		t.Fatal("guard must fail closed")
	}
	if fmt.Sprint(p.Spec.ValidationAction) != "[Deny]" {
		t.Fatal("guard must Deny")
	}
	h := compile(t, p)
	kinds := []string{"Pod", "ReplicationController", "Deployment", "ReplicaSet", "StatefulSet", "DaemonSet", "Job", "CronJob", "ControllerRevision"}
	for _, claim := range []string{"dev-env-history-archive", "dev-env-coordination-archive"} {
		for _, kind := range kinds {
			for _, actor := range []string{agent, grant, broker, keeper, sa("unknown/actor")} {
				o := workload(kind, "fixture", claim)
				run(t, h, claim+"/"+kind+"/"+actor+"/create", request(o, actor, "CREATE", false))
				f := request(o, actor, "UPDATE", false)
				f.old = workload(kind, "fixture", "")
				run(t, h, claim+"/"+kind+"/"+actor+"/add", f)
				f = request(workload(kind, "fixture", ""), actor, "UPDATE", false)
				f.old = o
				run(t, h, claim+"/"+kind+"/"+actor+"/remove", f)
				f = request(o, actor, "UPDATE", false)
				f.old = clone(o)
				f.subresource = "status"
				f.new = clone(o)
				f.new["metadata"].(obj)["ownerReferences"] = []any{obj{"kind": "Deployment", "name": "dev-env-shelf", "uid": "forged", "controller": true}}
				run(t, h, claim+"/"+kind+"/"+actor+"/status-reparent", f)
			}
			for _, actor := range trusted {
				f := request(workload(kind, "fixture", claim), sa(actor), "CREATE", true)
				f.fault = "transport"
				f.wantCalls = 0
				run(t, h, claim+"/"+kind+"/trusted/"+actor, f)
			}
		}
	}
	for _, kind := range kinds {
		f := request(workload(kind, "ordinary", ""), broker, "CREATE", true)
		f.wantCalls = 0
		run(t, h, "nonarchive/"+kind, f)
	}
	for _, actor := range []string{"tom-fixture", "system:kube-controller-manager", "system:node:fixture"} {
		f := request(workload("Deployment", "dev-env-shelf", "dev-env-history-archive"), actor, "CREATE", true)
		f.fault = "transport"
		f.wantCalls = 0
		run(t, h, "explicit-outside-SA-boundary/"+actor, f)
	}
	for _, kind := range []string{"Pod", "ReplicaSet", "Deployment"} {
		name := "dev-env-shelf-forged"
		if kind == "Deployment" {
			name = "dev-env-shelf"
		}
		f := request(workload(kind, name, ""), broker, "CREATE", false)
		run(t, h, "premount/reserved/"+kind, f)
		f.operation = "UPDATE"
		f.old = clone(f.new)
		f.subresource = "status"
		f.new["metadata"].(obj)["labels"] = obj{}
		run(t, h, "premount/remove-label/status/"+kind, f)
	}
	testConnect(t, h)
	testRestart(t, h)
	testControllers(t, h)
	testCleanup(t, h)
	testStatusFallback(t, h)
	for _, actor := range []string{agent, broker, keeper, sa("dev-agents/dev-env-workbench")} {
		o := workload("Job", "fixture", "dev-env-history-archive")
		template(o)["spec"].(obj)["serviceAccountName"] = "dev-env-operator"
		own(o, workload("CronJob", "fake-parent", "dev-env-history-archive"))
		run(t, h, "chosen-operator-SA-forged-owner/"+actor, request(o, actor, "CREATE", false))
	}
	f := request(workload("Pod", "fixture", "dev-env-history-archive"), broker, "DELETE", true)
	f.old = f.new
	f.new = nil
	f.wantCalls = 0
	run(t, h, "delete-is-not-materialization", f)
	f = request(workload("Pod", "fixture", "dev-env-history-archive"), broker, "CREATE", true)
	f.namespace = "ordinary"
	f.wantCalls = 0
	run(t, h, "other-namespace", f)
}

func testConnect(t *testing.T, h *harness) {
	for _, sub := range []string{"exec", "attach", "portforward", "ephemeralcontainers", "resize", "binding", "proxy"} {
		for _, claim := range []string{"dev-env-history-archive", "dev-env-coordination-archive", ""} {
			pod := workload("Pod", "fixture", claim)
			if claim == "" {
				pod["metadata"].(obj)["name"] = "dev-env-shelf-forged"
			}
			for _, actor := range []string{agent, grant, broker, keeper, sa("unknown/actor")} {
				f := request(pod, actor, "CONNECT", false)
				f.subresource = sub
				f.parents = []obj{pod}
				f.wantCalls = 1
				if sub == "ephemeralcontainers" || sub == "resize" {
					f.operation = "UPDATE"
					f.old = clone(pod)
				}
				if sub == "binding" {
					f.operation = "CREATE"
				}
				run(t, h, "access/"+sub+"/"+claim+"/"+actor, f)
			}
			for _, actor := range trusted {
				f := request(pod, sa(actor), "CONNECT", true)
				f.subresource = sub
				f.fault = "transport"
				f.wantCalls = 0
				if sub == "ephemeralcontainers" || sub == "resize" {
					f.operation = "UPDATE"
					f.old = clone(pod)
				}
				if sub == "binding" {
					f.operation = "CREATE"
				}
				run(t, h, "trusted-access/"+sub+"/"+claim+"/"+actor, f)
			}
		}
		f := request(workload("Pod", "fixture", ""), broker, "CONNECT", true)
		f.subresource = sub
		f.parents = []obj{f.new}
		f.wantCalls = 1
		if sub == "ephemeralcontainers" || sub == "resize" {
			f.operation = "UPDATE"
			f.old = clone(f.new)
		}
		if sub == "binding" {
			f.operation = "CREATE"
		}
		run(t, h, "nonarchive-access/"+sub, f)
	}
	for _, fault := range []string{"notfound", "null", "empty", "malformed", "forbidden", "transport"} {
		f := request(workload("Pod", "fixture", ""), broker, "CONNECT", false)
		f.subresource = "exec"
		f.fault = fault
		f.wantCalls = 1
		run(t, h, "lookup/"+fault, f)
	}
	// Real CONNECT carries options, not a target Pod: require the live Get.
	pod := workload("Pod", "fixture", "dev-env-history-archive")
	opts := obj{"apiVersion": "v1", "kind": "PodExecOptions", "command": []any{"true"}}
	f := request(opts, broker, "CONNECT", false)
	f.name = "fixture"
	f.subresource = "exec"
	f.parents = []obj{pod}
	f.wantCalls = 1
	run(t, h, "actual-CONNECT-options", f)
}

func testRestart(t *testing.T, h *harness) {
	for _, kind := range []string{"Deployment", "StatefulSet", "DaemonSet"} {
		old := workload(kind, "dev-env-shelf", "dev-env-history-archive")
		current := clone(old)
		template(current)["metadata"].(obj)["annotations"].(obj)["kubectl.kubernetes.io/restartedAt"] = "2026-10-13T20:00:00Z"
		f := request(current, sa("unknown/restart"), "UPDATE", true)
		f.old = old
		f.wantCalls = 0
		run(t, h, "restart/"+kind, f)
		remove := f
		remove.old = clone(current)
		remove.new = clone(old)
		run(t, h, "restart/remove-only/"+kind, remove)
		mutations := map[string]func(obj){
			"image":              func(o obj) { template(o)["spec"].(obj)["containers"].([]any)[0].(obj)["image"] = "bad" },
			"claims":             func(o obj) { template(o)["spec"].(obj)["volumes"] = []any{} },
			"spec-add":           func(o obj) { o["spec"].(obj)["newField"] = true },
			"spec-remove":        func(o obj) { delete(o["spec"].(obj), "replicas") },
			"labels":             func(o obj) { template(o)["metadata"].(obj)["labels"].(obj)["new"] = "bad" },
			"metadata-labels":    func(o obj) { o["metadata"].(obj)["labels"].(obj)["new"] = "bad" },
			"metadata-extra":     func(o obj) { o["metadata"].(obj)["generateName"] = "different-" },
			"annotations-add":    func(o obj) { template(o)["metadata"].(obj)["annotations"].(obj)["extra"] = "bad" },
			"annotations-remove": func(o obj) { delete(template(o)["metadata"].(obj)["annotations"].(obj), "keep") },
			"owners":             func(o obj) { own(o, workload("Deployment", "forged", "")) },
			"finalizers":         func(o obj) { o["metadata"].(obj)["finalizers"] = []any{"bad"} },
		}
		for name, mutation := range mutations {
			f.new = clone(current)
			mutation(f.new)
			f.allow = false
			run(t, h, "restart/mixed/"+kind+"/"+name, f)
		}
	}
	scale := obj{"apiVersion": "autoscaling/v1", "kind": "Scale", "metadata": metadata("dev-env-shelf"), "spec": obj{"replicas": float64(2)}}
	f := request(scale, broker, "UPDATE", true)
	f.kind = "Scale"
	f.resource = "deployments"
	f.group = "apps"
	f.subresource = "scale"
	f.old = clone(scale)
	f.old["spec"].(obj)["replicas"] = float64(1)
	f.wantCalls = 0
	run(t, h, "scale/replicas-only", f)
	for _, field := range []string{"ownerReferences", "labels", "annotations", "finalizers"} {
		f.new = clone(scale)
		f.allow = false
		f.new["metadata"].(obj)[field] = []any{"forged"}
		run(t, h, "scale/metadata-tamper/"+field, f)
	}
}

func testControllers(t *testing.T, h *harness) {
	claim := "dev-env-history-archive"
	for _, chain := range []struct{ child, parent, actor string }{
		{"ReplicaSet", "Deployment", "deployment-controller"}, {"Job", "CronJob", "cronjob-controller"},
		{"Pod", "ReplicaSet", "replicaset-controller"}, {"Pod", "Job", "job-controller"},
		{"Pod", "ReplicationController", "replication-controller"}, {"Pod", "StatefulSet", "statefulset-controller"},
		{"Pod", "DaemonSet", "daemon-set-controller"}, {"ControllerRevision", "StatefulSet", "statefulset-controller"},
		{"ControllerRevision", "DaemonSet", "daemon-set-controller"},
	} {
		parent := workload(chain.parent, "parent", claim)
		child := workload(chain.child, "child", claim)
		own(child, parent)
		f := request(child, controller(chain.actor), "CREATE", true)
		f.parents = []obj{parent}
		f.wantCalls = 1
		run(t, h, "controller/"+chain.child+"/"+chain.parent, f)
		if chain.child == "ReplicaSet" || chain.child == "Job" {
			generated := f
			generated.new = clone(child)
			labels := template(generated.new)["metadata"].(obj)["labels"].(obj)
			keys := []string{"pod-template-hash"}
			if chain.child == "Job" {
				keys = []string{"controller-uid", "job-name", "batch.kubernetes.io/controller-uid", "batch.kubernetes.io/job-name"}
			}
			for _, k := range keys {
				labels[k] = "generated"
			}
			run(t, h, "controller/generated-labels/"+chain.child, generated)
			labels["unrelated"] = "tampered"
			generated.allow = false
			run(t, h, "controller/unrelated-label/"+chain.child, generated)
		}
		status := f
		status.operation = "UPDATE"
		status.old = clone(child)
		status.new = clone(child)
		status.subresource = "status"
		run(t, h, "controller/status/"+chain.child+"/"+chain.parent, status)
		status.new["metadata"].(obj)["ownerReferences"] = []any{}
		status.allow = false
		// Removing every owner selects no parent lookup; old-object protection still denies.
		status.wantCalls = 0
		run(t, h, "controller/status-reparent/"+chain.child+"/"+chain.parent, status)
		wrong := f
		wrong.actor = broker
		wrong.allow = false
		wrong.wantCalls = 0
		run(t, h, "controller/wrong-actor/"+chain.child+"/"+chain.parent, wrong)
		for _, fault := range []string{"notfound", "empty", "null", "malformed", "forbidden", "transport"} {
			wrong = f
			wrong.fault = fault
			wrong.parents = nil
			wrong.allow = false
			run(t, h, "controller/lookup/"+chain.child+"/"+chain.parent+"/"+fault, wrong)
		}
		for _, fault := range []string{"response-missing-name", "response-wrong-name"} {
			wrong = f
			wrong.fault = fault
			wrong.allow = false
			run(t, h, "controller/malformed-name/"+chain.child+"/"+chain.parent+"/"+fault, wrong)
		}
		for _, mode := range []string{"uid", "missing-uid", "namespace", "kind", "apiVersion", "claims", "sa"} {
			wrong = f
			wrong.allow = false
			wrong.parents = []obj{clone(parent)}
			switch mode {
			case "uid":
				wrong.parents[0]["metadata"].(obj)["uid"] = "recreated"
			case "missing-uid":
				delete(wrong.parents[0]["metadata"].(obj), "uid")
			case "namespace":
				wrong.parents[0]["metadata"].(obj)["namespace"] = "elsewhere"
			case "kind":
				wrong.parents[0]["kind"] = "Wrong"
			case "apiVersion":
				wrong.parents[0]["apiVersion"] = "apps/v9"
			case "claims":
				template(wrong.parents[0])["spec"].(obj)["volumes"] = []any{}
			case "sa":
				template(wrong.parents[0])["spec"].(obj)["serviceAccountName"] = "different"
			}
			if mode == "kind" || mode == "namespace" || mode == "apiVersion" {
				wrong.parents = nil
			}
			run(t, h, "controller/parent/"+chain.child+"/"+chain.parent+"/"+mode, wrong)
		}
	}
	// CronJob descendants retain their admitted Job snapshot after a root edit.
	cj := workload("CronJob", "cron-root", claim)
	job := workload("Job", "historic-job", claim)
	own(job, cj)
	jp := workload("Pod", "historic-job-pod", claim)
	own(jp, job)
	template(cj)["spec"].(obj)["volumes"] = []any{}
	jobFixture := request(jp, controller("job-controller"), "CREATE", true)
	jobFixture.parents = []obj{job, cj}
	jobFixture.wantCalls = 2
	run(t, h, "history/job-current-cronjob", jobFixture)
	for _, fault := range []string{"root-missing-name", "root-wrong-name"} {
		bad := jobFixture
		bad.fault = fault
		bad.allow = false
		run(t, h, "history/job-current-cronjob/"+fault, bad)
	}
	jobFixture.parents = []obj{job}
	jobFixture.allow = false
	run(t, h, "history/job-missing-cronjob", jobFixture)
	// Pod → historical RS → current Deployment: current template may differ.
	root := workload("Deployment", "dev-env-shelf", claim)
	rs := workload("ReplicaSet", "dev-env-shelf-old", claim)
	own(rs, root)
	pod := workload("Pod", "dev-env-shelf-old-pod", claim)
	own(pod, rs)
	template(root)["spec"].(obj)["containers"].([]any)[0].(obj)["image"] = "new-current-image"
	f := request(pod, controller("replicaset-controller"), "CREATE", true)
	f.parents = []obj{rs, root}
	f.wantCalls = 2
	run(t, h, "history/old-RS-current-root", f)
	for _, fault := range []string{"root-missing-name", "root-wrong-name"} {
		bad := f
		bad.fault = fault
		bad.allow = false
		run(t, h, "history/old-RS-current-root/"+fault, bad)
	}
	for _, mode := range []string{"missing-root", "recreated-root", "missing-root-uid"} {
		wrong := f
		wrong.allow = false
		wrong.parents = []obj{rs, clone(root)}
		if mode == "missing-root" {
			wrong.parents = []obj{rs}
		}
		if mode == "recreated-root" {
			wrong.parents[1]["metadata"].(obj)["uid"] = "new-root-uid"
		}
		if mode == "missing-root-uid" {
			delete(wrong.parents[1]["metadata"].(obj), "uid")
		}
		run(t, h, "history/"+mode, wrong)
	}
	// Deployment controller may scale a historical RS without rewriting its template.
	f = request(clone(rs), controller("deployment-controller"), "UPDATE", true)
	f.old = clone(rs)
	f.new["spec"].(obj)["replicas"] = float64(0)
	f.parents = []obj{root}
	f.wantCalls = 1
	run(t, h, "history/scale-old-RS", f)
	f = request(clone(rs), controller("replicaset-controller"), "UPDATE", true)
	f.old = clone(rs)
	f.subresource = "status"
	f.parents = []obj{root}
	f.wantCalls = 1
	run(t, h, "history/RS-status", f)
	f.new["metadata"].(obj)["ownerReferences"] = []any{}
	f.allow = false
	// Removing every owner selects no parent lookup; old-object protection still denies.
	f.wantCalls = 0
	run(t, h, "history/RS-status-reparent", f)
	for _, kind := range []string{"StatefulSet", "DaemonSet"} {
		actor := "statefulset-controller"
		if kind == "DaemonSet" {
			actor = "daemon-set-controller"
		}
		root = workload(kind, "root", claim)
		rev := workload("ControllerRevision", "root-history", claim)
		own(rev, root)
		pod = workload("Pod", "child", claim)
		own(pod, root)
		pod["metadata"].(obj)["labels"].(obj)["controller-revision-hash"] = "root-history"
		if kind == "DaemonSet" {
			pod["metadata"].(obj)["labels"].(obj)["controller-revision-hash"] = "history"
		}
		template(root)["spec"].(obj)["volumes"] = []any{}
		f = request(pod, controller(actor), "CREATE", true)
		f.parents = []obj{root, rev}
		f.wantCalls = 2
		run(t, h, "history/revision/"+kind, f)
		for _, mode := range []string{"missing", "uid", "parent-uid", "data", "patch", "foreign-field"} {
			wrong := f
			wrong.allow = false
			wrong.parents = []obj{root, clone(rev)}
			switch mode {
			case "missing":
				wrong.parents = []obj{root}
			case "uid":
				delete(wrong.parents[1]["metadata"].(obj), "uid")
			case "parent-uid":
				wrong.parents[1]["metadata"].(obj)["ownerReferences"].([]any)[0].(obj)["uid"] = "foreign-root"
			case "data":
				wrong.parents[1]["data"] = obj{}
			case "patch":
				template(wrong.parents[1])["$patch"] = "delete"
			case "foreign-field":
				wrong.parents[1]["data"].(obj)["metadata"] = obj{"name": "injection"}
			}
			run(t, h, "history/revision/"+kind+"/"+mode, wrong)
		}
		f = request(clone(rev), broker, "UPDATE", false)
		f.old = clone(rev)
		f.new["data"].(obj)["spec"].(obj)["template"].(obj)["spec"].(obj)["containers"].([]any)[0].(obj)["image"] = "injection"
		f.wantCalls = 0
		run(t, h, "history/untrusted-revision-data/"+kind, f)
	}
	// Real dedupCurHistories changes only the bare hash label to a current history.
	ds := workload("DaemonSet", "daemon", claim)
	rev := workload("ControllerRevision", "daemon-newhash", claim)
	own(rev, ds)
	dp := workload("Pod", "daemon-pod", claim)
	own(dp, ds)
	dp["metadata"].(obj)["labels"].(obj)["controller-revision-hash"] = "oldhash"
	dedup := request(clone(dp), controller("daemon-set-controller"), "UPDATE", true)
	dedup.old = dp
	dedup.new["metadata"].(obj)["labels"].(obj)["controller-revision-hash"] = "newhash"
	dedup.parents = []obj{ds, rev}
	dedup.wantCalls = 2
	run(t, h, "history/daemon-dedup", dedup)
	for _, mode := range []string{"wrong-actor", "owner", "execution", "label", "annotation", "missing-revision", "revision-uid", "revision-owner", "revision-data"} {
		bad := dedup
		bad.new = clone(dedup.new)
		bad.parents = []obj{ds, clone(rev)}
		bad.allow = false
		bad.wantCalls = -1
		switch mode {
		case "wrong-actor":
			bad.actor = broker
		case "owner":
			bad.new["metadata"].(obj)["ownerReferences"] = []any{}
		case "execution":
			bad.new["spec"].(obj)["containers"].([]any)[0].(obj)["image"] = "bad"
		case "label":
			bad.new["metadata"].(obj)["labels"].(obj)["unrelated"] = "bad"
		case "annotation":
			bad.new["metadata"].(obj)["annotations"] = obj{"unrelated": "bad"}
		case "missing-revision":
			bad.parents = []obj{ds}
		case "revision-uid":
			delete(bad.parents[1]["metadata"].(obj), "uid")
		case "revision-owner":
			bad.parents[1]["metadata"].(obj)["ownerReferences"].([]any)[0].(obj)["uid"] = "bad"
		case "revision-data":
			template(bad.parents[1])["spec"].(obj)["containers"].([]any)[0].(obj)["image"] = "different"
		}
		run(t, h, "history/daemon-dedup/"+mode, bad)
	}
}

func testStatusFallback(t *testing.T, h *harness) {
	for _, actor := range []string{broker, controller("pod-garbage-collector")} {
		for _, fault := range []string{"notfound", "null", "empty", "malformed", "forbidden", "transport", "wrong-kind", "missing-uid", "wrong-name", "wrong-namespace"} {
			f := request(workload("Pod", "fixture", ""), actor, "UPDATE", false)
			f.subresource = "status"
			f.fault = fault
			f.wantCalls = 1
			run(t, h, "status-fallback/"+actor+"/"+fault, f)
		}
		f := request(workload("Pod", "fixture", ""), actor, "UPDATE", true)
		f.subresource = "status"
		f.parents = []obj{clone(f.new)}
		f.wantCalls = 1
		run(t, h, "status-fallback/valid-unprotected/"+actor, f)
		f.parents = []obj{workload("Pod", "fixture", "dev-env-history-archive")}
		f.allow = false
		run(t, h, "status-fallback/protected-without-old/"+actor, f)
	}
}

func testCleanup(t *testing.T, h *harness) {
	claim := "dev-env-history-archive"
	gc := controller("generic-garbage-collector")
	for _, kind := range []string{"Pod", "ReplicationController", "Deployment", "ReplicaSet", "StatefulSet", "DaemonSet", "Job", "CronJob", "ControllerRevision"} {
		old := workload(kind, "protected", claim)
		own(old, workload("Deployment", "owner", claim))
		old["metadata"].(obj)["ownerReferences"].([]any)[0].(obj)["blockOwnerDeletion"] = true
		old["metadata"].(obj)["finalizers"] = []any{"foregroundDeletion", "orphan", "example.invalid/keep"}
		f := request(clone(old), gc, "UPDATE", true)
		f.old = old
		f.wantCalls = 0
		f.new["metadata"].(obj)["ownerReferences"] = []any{}
		f.new["metadata"].(obj)["finalizers"] = []any{"example.invalid/keep"}
		run(t, h, "cleanup/GC/remove/"+kind, f)
		unblock := f
		unblock.new = clone(old)
		unblock.new["metadata"].(obj)["ownerReferences"].([]any)[0].(obj)["blockOwnerDeletion"] = false
		run(t, h, "cleanup/GC/unblock/"+kind, unblock)
		reblock := unblock
		reblock.old = clone(unblock.new)
		reblock.new = clone(old)
		reblock.allow = false
		run(t, h, "cleanup/GC/refuse-reblock/"+kind, reblock)
		foreign := unblock
		foreign.new = clone(unblock.new)
		foreign.new["metadata"].(obj)["ownerReferences"].([]any)[0].(obj)["controller"] = false
		foreign.allow = false
		run(t, h, "cleanup/GC/refuse-owner-role-change/"+kind, foreign)
		for _, mode := range []string{"actor", "identity", "execution", "owner-insert", "reparent", "finalizer-add", "finalizer-remove-foreign", "label", "status"} {
			bad := f
			bad.new = clone(f.new)
			bad.allow = false
			switch mode {
			case "actor":
				bad.actor = broker
			case "identity":
				bad.new["metadata"].(obj)["uid"] = "recreated"
			case "execution":
				template(bad.new)["spec"].(obj)["containers"].([]any)[0].(obj)["image"] = "bad"
			case "owner-insert":
				own(bad.new, workload("Deployment", "forged", claim))
			case "reparent":
				bad.new["metadata"].(obj)["ownerReferences"] = clone(old)["metadata"].(obj)["ownerReferences"]
				bad.new["metadata"].(obj)["ownerReferences"].([]any)[0].(obj)["uid"] = "forged"
			case "finalizer-add":
				bad.new["metadata"].(obj)["finalizers"] = []any{"example.invalid/keep", "forged"}
			case "finalizer-remove-foreign":
				bad.new["metadata"].(obj)["finalizers"] = []any{}
			case "label":
				bad.new["metadata"].(obj)["labels"].(obj)["forged"] = "bad"
			case "status":
				bad.new["status"] = obj{"phase": "Failed"}
			}
			run(t, h, "cleanup/GC/tamper/"+kind+"/"+mode, bad)
		}
	}
	// A trusted GC orphan keeps its admitted archive template as a standalone RS.
	rs := workload("ReplicaSet", "orphaned", claim)
	pod := workload("Pod", "orphaned-child", claim)
	own(pod, rs)
	f := request(pod, controller("replicaset-controller"), "CREATE", true)
	f.parents = []obj{rs}
	f.wantCalls = 1
	run(t, h, "cleanup/GC/orphan-descendant", f)
	f = request(clone(rs), controller("replicaset-controller"), "UPDATE", true)
	f.old = clone(rs)
	f.subresource = "status"
	f.wantCalls = 0
	run(t, h, "cleanup/GC/orphan-RS-status", f)
	for _, actor := range []string{"pod-garbage-collector", "node-controller", "disruption-controller"} {
		old := workload("Pod", "dev-env-shelf-fixture", claim)
		old["status"] = obj{"phase": "Running", "conditions": []any{obj{"type": "Ready", "status": "True"}}}
		if actor == "disruption-controller" {
			old["status"].(obj)["conditions"] = append(old["status"].(obj)["conditions"].([]any), obj{"type": "DisruptionTarget", "status": "True", "reason": "DeletionByPodGC"})
		}
		f = request(clone(old), controller(actor), "UPDATE", true)
		f.old = old
		f.subresource = "status"
		f.wantCalls = 0
		condition := obj{"type": "DisruptionTarget", "status": "True", "reason": "DeletionByPodGC", "observedGeneration": float64(1), "lastTransitionTime": "2026-10-13T20:00:00Z"}
		if actor == "node-controller" {
			condition["reason"] = "DeletionByTaintManager"
		}
		if actor == "disruption-controller" {
			condition["status"] = "False"
			delete(condition, "reason")
		}
		if actor == "disruption-controller" {
			f.new["status"].(obj)["conditions"].([]any)[1] = condition
		} else {
			f.new["status"].(obj)["conditions"] = append(f.new["status"].(obj)["conditions"].([]any), condition)
		}
		if actor == "pod-garbage-collector" {
			f.new["status"].(obj)["phase"] = "Failed"
			f.new["status"].(obj)["observedGeneration"] = float64(1)
		}
		run(t, h, "cleanup/status/"+actor, f)
		for _, mode := range []string{"actor", "execution", "owner", "label", "annotation", "finalizer", "condition", "status-field"} {
			bad := f
			bad.new = clone(f.new)
			bad.allow = false
			switch mode {
			case "actor":
				bad.actor = broker
			case "execution":
				bad.new["spec"].(obj)["volumes"] = []any{}
			case "owner":
				own(bad.new, workload("ReplicaSet", "forged", claim))
			case "label":
				bad.new["metadata"].(obj)["labels"].(obj)["forged"] = "bad"
			case "annotation":
				bad.new["metadata"].(obj)["annotations"] = obj{"forged": "bad"}
			case "finalizer":
				bad.new["metadata"].(obj)["finalizers"] = []any{"forged"}
			case "condition":
				bad.new["status"].(obj)["conditions"].([]any)[0].(obj)["status"] = "False"
			case "status-field":
				bad.new["status"].(obj)["podIP"] = "forged"
			}
			run(t, h, "cleanup/status/tamper/"+actor+"/"+mode, bad)
		}
	}
	old := workload("Pod", "dev-env-shelf-node", claim)
	old["spec"].(obj)["nodeName"] = "fixture-node"
	old["status"] = obj{"phase": "Running", "conditions": []any{
		obj{"type": "Ready", "status": "True", "reason": "ContainersReady", "message": "ready", "observedGeneration": float64(1), "lastProbeTime": "2026-10-13T19:00:00Z", "lastTransitionTime": "2026-10-13T19:00:00Z"},
		obj{"type": "Initialized", "status": "True"}}}
	f = request(clone(old), controller("node-controller"), "UPDATE", true)
	f.old = old
	f.subresource = "status"
	f.wantCalls = 0
	f.new["status"].(obj)["conditions"].([]any)[0].(obj)["status"] = "False"
	f.new["status"].(obj)["conditions"].([]any)[0].(obj)["lastTransitionTime"] = "2026-10-13T20:00:00Z"
	run(t, h, "cleanup/node-MarkPodsNotReady", f)
	for _, mode := range []string{"actor", "ready-true", "reason", "ready-new-field", "ready-remove-field", "other-condition", "owner", "execution", "label", "status-field"} {
		bad := f
		bad.old = clone(old)
		bad.new = clone(f.new)
		bad.allow = false
		switch mode {
		case "actor":
			bad.actor = broker
		case "ready-true":
			bad.old["status"].(obj)["conditions"].([]any)[0].(obj)["status"] = "False"
			bad.new["status"].(obj)["conditions"].([]any)[0].(obj)["status"] = "True"
		case "reason":
			bad.new["status"].(obj)["conditions"].([]any)[0].(obj)["reason"] = "forged"
		case "ready-new-field":
			bad.new["status"].(obj)["conditions"].([]any)[0].(obj)["forged"] = "bad"
		case "ready-remove-field":
			delete(bad.new["status"].(obj)["conditions"].([]any)[0].(obj), "message")
		case "other-condition":
			bad.new["status"].(obj)["conditions"].([]any)[1].(obj)["status"] = "False"
		case "owner":
			own(bad.new, workload("ReplicaSet", "forged", claim))
		case "execution":
			bad.new["spec"].(obj)["volumes"] = []any{}
		case "label":
			bad.new["metadata"].(obj)["labels"].(obj)["forged"] = "bad"
		case "status-field":
			bad.new["status"].(obj)["podIP"] = "forged"
		}
		run(t, h, "cleanup/node-MarkPodsNotReady/tamper/"+mode, bad)
	}
}

// Evaluate the actual complementary source expressions for each trusted identity.
// This proves the source contract, not installed VAP/webhook delivery or live RBAC.
func loadVAP(t *testing.T, path string) *policies.ValidatingPolicy {
	t.Helper()
	b, err := os.ReadFile(repoPath(path))
	if err != nil {
		t.Fatal(err)
	}
	var p registration.ValidatingAdmissionPolicy
	decoder := utilyaml.NewYAMLOrJSONDecoder(bytes.NewReader(b), 4096)
	for {
		if err = decoder.Decode(&p); err != nil {
			if err == io.EOF {
				t.Fatal("native policy document not found")
			}
			t.Fatal(err)
		}
		// A comment before an opening --- is an empty YAML document.
		if p.Kind != "" {
			break
		}
	}
	if p.APIVersion != registration.SchemeGroupVersion.String() || p.Kind != "ValidatingAdmissionPolicy" || len(p.Spec.Validations) == 0 {
		t.Fatal("expected a nonempty native ValidatingAdmissionPolicy")
	}
	return &policies.ValidatingPolicy{ObjectMeta: p.ObjectMeta, Spec: policies.ValidatingPolicySpec{
		MatchConstraints: p.Spec.MatchConstraints, MatchConditions: p.Spec.MatchConditions,
		Variables: p.Spec.Variables, Validations: p.Spec.Validations, FailurePolicy: p.Spec.FailurePolicy,
		ValidationAction: []registration.ValidationAction{registration.Deny},
	}}
}

func TestComplementaryAgentGrantBorrowing(t *testing.T) {
	identity := compile(t, loadVAP(t, "kubernetes/main/apps/dev-env-system/rbac/app/identity-guard.yaml"))
	execPolicy := compile(t, load(t, "kubernetes/main/apps/kyverno/policies/app/dev-env-exec-guard.yaml"))
	for _, who := range []string{agent, grant} {
		for _, target := range trusted {
			parts := strings.Split(target, "/")
			for _, kind := range []string{"Pod", "Job", "CronJob", "ReplicationController", "Deployment", "ReplicaSet", "StatefulSet", "DaemonSet"} {
				o := workload(kind, "fixture", "")
				template(o)["spec"].(obj)["serviceAccountName"] = parts[1]
				f := request(o, who, "CREATE", false)
				f.namespace = parts[0]
				f.wantCalls = 0
				run(t, identity, "borrow/workload/"+who+"/"+target+"/"+kind, f)
			}
			f := request(workload("Pod", "fixture", ""), who, "CREATE", false)
			f.namespace = parts[0]
			f.resource = "serviceaccounts"
			f.kind = "TokenRequest"
			f.subresource = "token"
			f.wantCalls = 0
			run(t, identity, "borrow/token/"+who+"/"+target, f)
			pod := workload("Pod", "fixture", "")
			pod["metadata"].(obj)["namespace"] = parts[0]
			pod["spec"].(obj)["serviceAccountName"] = parts[1]
			f = request(pod, who, "CONNECT", false)
			f.namespace = parts[0]
			f.subresource = "exec"
			f.parents = []obj{pod}
			f.wantCalls = -1
			allowed, _ := identity.evaluate(t, f)
			if allowed {
				run(t, execPolicy, "borrow/exec/"+who+"/"+target, f)
			} else {
				run(t, identity, "borrow/namespace-exec/"+who+"/"+target, f)
			}
		}
	}
}
