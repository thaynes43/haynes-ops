"""Finite stdlib-only fixtures; no cluster/client construction or CPU load loops."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import time
import unittest
from unittest import mock
import urllib.error

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "kubernetes/main/apps/dev-env-system/h7-metadata-reader/app"
SPEC = importlib.util.spec_from_file_location("collector", APP / "collector.py")
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
EXPECTED = json.loads((APP / "expected.json").read_text())
SECRET = "PRIVATE_INLINE_ENV_TOKEN_NEVER_OUTPUT"


def meta(name, uid="fixture-uid", namespace=None):
    return {"name": name, "uid": uid, "resourceVersion": "900", "generation": 1,
            **({"namespace": namespace} if namespace else {})}


def actual_template(source):
    value = copy.deepcopy(source)
    value["metadata"]["creationTimestamp"] = None
    spec = value["spec"]
    spec.update({"dnsPolicy": "ClusterFirst", "restartPolicy": "Always", "schedulerName": "default-scheduler",
                 "terminationGracePeriodSeconds": 30, "serviceAccount": spec["serviceAccountName"]})
    for container in spec["containers"]:
        container.update({"imagePullPolicy": "IfNotPresent", "terminationMessagePath": "/dev/termination-log",
                          "terminationMessagePolicy": "File"})
    return value


def baseline():
    rows = []
    for key, info in EXPECTED["lineage"].items():
        kind, name = key.split("/")
        obj = {"apiVersion": "v1" if kind == "Pod" else "apps/v1", "kind": kind,
               "metadata": meta(name, info["uid"], C.NAMESPACE)}
        t = actual_template(EXPECTED["shelfTemplates"][info["sourceCommit"]])
        obj["metadata"]["labels"] = {"app.kubernetes.io/name": "dev-env-shelf"}
        if kind == "Deployment":
            obj["metadata"]["labels"].update({"kustomize.toolkit.fluxcd.io/name": "dev-env-shelf",
                                              "kustomize.toolkit.fluxcd.io/namespace": "dev-env-system"})
        else:
            parent_kind, parent_name = info["parent"].split("/")
            obj["metadata"]["ownerReferences"] = [{"apiVersion": "apps/v1", "kind": parent_kind,
                                                    "name": parent_name, "uid": EXPECTED["lineage"][info["parent"]]["uid"],
                                                    "controller": True, "blockOwnerDeletion": True}]
            t["metadata"]["labels"]["pod-template-hash"] = name.split("-")[-1] if kind == "ReplicaSet" else "b5ff85487"
        if kind == "Pod":
            obj["metadata"]["labels"] = t["metadata"]["labels"] | {"topology.kubernetes.io/region": "main", "topology.kubernetes.io/zone": "w"}
            obj["metadata"]["annotations"] = {"k8s.v1.cni.cncf.io/network-status": SECRET}
            obj["spec"] = t["spec"] | {"nodeName": "talosw02", "priority": 0, "preemptionPolicy": "PreemptLowerPriority",
                                       "tolerations": [{"key": k, "operator": "Exists", "effect": "NoExecute", "tolerationSeconds": 300}
                                                       for k in ("node.kubernetes.io/not-ready", "node.kubernetes.io/unreachable")]}
        else:
            obj["spec"] = {"template": t}
        rows.append(obj)
    return rows


def policy_object(source):
    obj = copy.deepcopy(source)
    obj.pop("resource")
    obj["metadata"] = meta(source["metadata"]["name"])
    obj["spec"] = C.normalized_policy(obj["spec"], obj["kind"])
    if obj["kind"] == "ValidatingPolicy":
        obj["status"] = {"conditionStatus": {"ready": True, "message": "", "conditions": [
            {"type": key, "status": "True", "reason": "Succeeded", "message": SECRET, "observedGeneration": 0}
            for key in ("WebhookConfigured", "PolicyCached", "RBACPermissionsGranted")]}}
    elif obj["kind"] == "ValidatingAdmissionPolicy":
        obj["status"] = {"observedGeneration": 1, "typeChecking": {"expressionWarnings": []}}
    return obj


def hooks():
    rows = []
    for source in EXPECTED["policies"]:
        if source["kind"] != "ValidatingPolicy":
            continue
        name = source["metadata"]["name"]
        selector = copy.deepcopy(source["spec"]["matchConstraints"].get("namespaceSelector", {}))
        selector["matchExpressions"] = [{"key": "kubernetes.io/metadata.name", "operator": "NotIn", "values": [v]}
                                        for v in ("kube-system", "kyverno")]
        rows.append({"name": "vpol.validate.kyverno.svc-fail-finegrained-" + name,
                     "clientConfig": {"service": {"name": "kyverno-svc", "namespace": "kyverno", "port": 443,
                                                  "path": "/vpol/" + name}, "caBundle": SECRET},
                     "failurePolicy": "Fail", "timeoutSeconds": 10, "matchPolicy": "Equivalent",
                     "sideEffects": "NoneOnDryRun", "admissionReviewVersions": ["v1"],
                     "rules": copy.deepcopy(source["spec"]["matchConstraints"]["resourceRules"]),
                     "namespaceSelector": selector, "objectSelector": {},
                     "matchConditions": copy.deepcopy(source["spec"].get("matchConditions", []))})
    return rows


class Fake:
    def __init__(self):
        self.named, self.rows, self.requests, self.bytes = {}, {}, 0, 0
        for source in EXPECTED["policies"]:
            self.named[C.api_path(source["apiVersion"], source["resource"], name=source["metadata"]["name"])] = policy_object(source)
        self.named[C.api_path("v1", "namespaces", name=C.NAMESPACE)] = {
            "apiVersion": "v1", "kind": "Namespace", "metadata": meta(C.NAMESPACE) | {
                "labels": {"kubernetes.io/metadata.name": C.NAMESPACE}}}
        self.named[C.api_path("admissionregistration.k8s.io/v1", "validatingwebhookconfigurations", name="kyverno-resource-validating-webhook-cfg")] = {
            "apiVersion": "admissionregistration.k8s.io/v1", "kind": "ValidatingWebhookConfiguration",
            "metadata": meta("kyverno-resource-validating-webhook-cfg"), "webhooks": hooks()}
        for obj in baseline():
            self.named[C.api_path(obj["apiVersion"], next(r for v, r, k in C.KINDS if k == obj["kind"]), C.NAMESPACE, obj["metadata"]["name"])] = obj
        for version, resource, kind in C.KINDS:
            self.rows[C.api_path(version, resource, C.NAMESPACE)] = {
                "apiVersion": version, "kind": kind + "List", "metadata": {"resourceVersion": "900"},
                "items": [o for o in baseline() if o["kind"] == kind]}

    def get(self, path, query=None):
        self.requests += 1
        if query:
            assert query == {"limit": C.PAGE_SIZE}, "Unexpected pagination or retry in full fixture"
            return copy.deepcopy(self.rows[path])
        return copy.deepcopy(self.named[path])


def report():
    return {"state": "Failed", "policies": [], "hooks": [], "lists": [], "protectedObjects": []}


class CollectorTests(unittest.TestCase):
    def test_frozen_public_source_hashes(self):
        for path, expected in EXPECTED["sourceFileSha256"].items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), expected)
        self.assertEqual(len(EXPECTED["policies"]), 4)
        self.assertEqual(len(EXPECTED["shelfTemplates"]), 3)

    def test_complete_audit_sanitizes_values_and_rechecks_current_uids(self):
        reader, result = Fake(), report()
        C.audit(reader, EXPECTED, result)
        self.assertEqual(result["state"], "Ready")
        self.assertEqual(len(result["lists"]), 9)
        self.assertEqual(len(result["protectedObjects"]), 5)
        self.assertTrue(result["namedObjectsStable"])
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertLess(reader.requests, C.MAX_REQUESTS)
        self.assertFalse(result["policies"][0]["readiness"]["generationAttested"])
        for actual, public in zip(result["hooks"], hooks()):
            self.assertEqual(actual["namespaceSelector"], public["namespaceSelector"])
            self.assertEqual(actual["rules"], [json.loads(rule) for rule in C.normalize_rules(public["rules"])])

    def test_policy_and_hook_fail_closed(self):
        changes = {
            "policy-drift": lambda f: f.named[next(iter(f.named))]["spec"].update({"failurePolicy": "Ignore"}),
            "not-ready": lambda f: f.named[next(iter(f.named))]["status"]["conditionStatus"].update({"ready": False}),
            "missing-condition": lambda f: f.named[next(iter(f.named))]["status"]["conditionStatus"]["conditions"].pop(),
            "missing-hook": lambda f: next(v for v in f.named.values() if v["kind"] == "ValidatingWebhookConfiguration")["webhooks"].pop(),
            "exclude-namespace": lambda f: next(v for v in f.named.values() if v["kind"] == "ValidatingWebhookConfiguration")["webhooks"][0]["namespaceSelector"].update({"matchLabels": {"kubernetes.io/metadata.name": "other"}}),
            "extra-condition": lambda f: next(v for v in f.named.values() if v["kind"] == "ValidatingWebhookConfiguration")["webhooks"][0]["matchConditions"].append({"name": "skip", "expression": "false"}),
            "missing-rule": lambda f: next(v for v in f.named.values() if v["kind"] == "ValidatingWebhookConfiguration")["webhooks"][0]["rules"].pop(),
            "stale-native-generation": lambda f: next(v for v in f.named.values() if v["kind"] == "ValidatingAdmissionPolicy")["status"].update({"observedGeneration": 0}),
            "warning": lambda f: next(v for v in f.named.values() if v["kind"] == "ValidatingAdmissionPolicy")["status"]["typeChecking"].update({"expressionWarnings": [{"warning": SECRET}]}),
        }
        for label, change in changes.items():
            with self.subTest(label=label):
                fake, result = Fake(), report()
                change(fake)
                with self.assertRaises(C.Refused):
                    C.audit(fake, EXPECTED, result)
                self.assertNotIn(SECRET, json.dumps(result))
                self.assertEqual(result["state"], "Failed")

    def test_hook_rejects_private_selector_and_unknown_rule_fields_without_output(self):
        changes = {
            # This NotIn requirement still matches an absent namespace label.
            "matching-private-exclusion": lambda h: h["namespaceSelector"]["matchExpressions"].append(
                {"key": "private.example/selector", "operator": "NotIn", "values": [SECRET]}),
            "unknown-selector-field": lambda h: h["namespaceSelector"].update({"private": SECRET}),
            "private-object-selector": lambda h: h["objectSelector"].update({"matchExpressions": [
                {"key": "private.example/selector", "operator": "NotIn", "values": [SECRET]}]}),
            # Existing rule normalization rejects unknown keys before emission.
            "unknown-rule-field": lambda h: h["rules"][0].update({"private": SECRET}),
        }
        for index in (0, 1):
            for label, change in changes.items():
                with self.subTest(hook=index, label=label):
                    fake, output = Fake(), io.StringIO()
                    config = next(v for v in fake.named.values() if v["kind"] == "ValidatingWebhookConfiguration")
                    change(config["webhooks"][index])
                    if label == "matching-private-exclusion":
                        self.assertTrue(C.selector_matches(config["webhooks"][index]["namespaceSelector"],
                                                           {"kubernetes.io/metadata.name": C.NAMESPACE}))
                    with mock.patch.object(C.pathlib.Path, "read_text", return_value=json.dumps(EXPECTED)), \
                            mock.patch.object(C, "Reader", return_value=fake), contextlib.redirect_stdout(output):
                        self.assertEqual(C.main(), 1)
                    value = json.loads(output.getvalue())
                    self.assertEqual(value["state"], "Failed")
                    self.assertEqual(value["failure"]["code"], "MalformedWebhookRule" if label == "unknown-rule-field" else
                                     "WebhookExcludesTarget" if label == "private-object-selector" else "WrongWebhookNamespaceSelector")
                    self.assertNotIn(SECRET, output.getvalue())

    def test_archive_slots_and_malformed_revision(self):
        for version, resource, kind in C.KINDS:
            for claim in sorted(C.ARCHIVES):
                with self.subTest(kind=kind, claim=claim):
                    t = {"spec": {"containers": [{"name": "test", "env": [{"name": "PRIVATE", "value": SECRET}]}],
                                  "volumes": [{"name": "archive", "persistentVolumeClaim": {"claimName": claim}}]}}
                    o = {"apiVersion": version, "kind": kind, "metadata": meta("unexpected", namespace=C.NAMESPACE)}
                    if kind == "Pod":
                        o["spec"] = t["spec"]
                    elif kind == "ControllerRevision":
                        o["data"] = {"spec": {"template": t}}
                    elif kind == "CronJob":
                        o["spec"] = {"jobTemplate": {"spec": {"template": t}}}
                    else:
                        o["spec"] = {"template": t}
                    with self.assertRaisesRegex(C.Refused, "") as caught:
                        C.verify_baseline(baseline() + [o], EXPECTED)
                    self.assertEqual(caught.exception.code, "UnexpectedArchiveBearingObject")
        for data in (None, "raw-unexpected", {}, {"spec": {"template": None}}):
            with self.subTest(revision=data):
                o = {"apiVersion": "apps/v1", "kind": "ControllerRevision", "metadata": meta("history", namespace=C.NAMESPACE), "data": data}
                with self.assertRaises(C.Refused):
                    C.workload_facts(o)

    def test_owner_and_template_tampering(self):
        for mode in ("uid", "owner-uid", "owner-name", "owner-kind", "unknown-env", "wrong-image", "extra-capability", "extra-volume", "missing-object", "shelf-sa-job", "shelf-template-label-job"):
            with self.subTest(mode=mode):
                rows = baseline()
                rs = next(o for o in rows if o["kind"] == "ReplicaSet")
                if mode == "uid":
                    rs["metadata"]["uid"] = "changed-uid"
                elif mode.startswith("owner-"):
                    rs["metadata"]["ownerReferences"][0][mode[6:]] = "Wrong" if mode == "owner-kind" else "wrong"
                elif mode == "unknown-env":
                    rs["spec"]["template"]["spec"]["containers"][0]["env"].append({"name": "PRIVATE", "value": SECRET})
                elif mode == "wrong-image":
                    rs["spec"]["template"]["spec"]["containers"][0]["image"] = "arbitrary"
                elif mode == "extra-capability":
                    rs["spec"]["template"]["spec"]["containers"][0]["securityContext"]["capabilities"]["add"] = ["SYS_ADMIN"]
                elif mode == "extra-volume":
                    rs["spec"]["template"]["spec"]["volumes"].append({"name": "secret", "secret": {"secretName": "private"}})
                elif mode == "missing-object":
                    rows.remove(rs)
                else:
                    rows.append({"apiVersion": "batch/v1", "kind": "Job", "metadata": meta("chosen-shelf", namespace=C.NAMESPACE),
                                 "spec": {"template": {"metadata": {"labels": {"app.kubernetes.io/name": "dev-env-shelf"}} if mode == "shelf-template-label-job" else {},
                                                       "spec": {"serviceAccountName": "default" if mode == "shelf-template-label-job" else "dev-env-shelf",
                                                                "containers": [{"name": "test"}]}}}})
                with self.assertRaises(C.Refused):
                    C.verify_baseline(rows, EXPECTED)

    def test_complete_pagination_and_refusals(self):
        pod = next(o for o in baseline() if o["kind"] == "Pod")
        def page(rv="900", continuation="", items=None, remaining=None):
            return {"apiVersion": "v1", "kind": "PodList", "metadata": {"resourceVersion": rv, "continue": continuation,
                     **({"remainingItemCount": remaining} if remaining is not None else {})}, "items": [] if items is None else items}
        class Pages:
            def __init__(self, rows):
                self.rows = iter(rows)
                self.calls = 0
            def get(self, path, query):
                self.calls += 1
                return next(self.rows)
        other = pod | {"metadata": meta("other", "other-uid", C.NAMESPACE)}
        third = pod | {"metadata": meta("third", "third-uid", C.NAMESPACE)}
        valid = {
            "all-counts": [page(continuation="opaque", items=[pod], remaining=1), page(items=[other], remaining=0)],
            "no-counts": [page(continuation="opaque", items=[pod]), page(items=[other])],
            "first-omitted": [page(continuation="opaque", items=[pod]), page(items=[other], remaining=0)],
            "terminal-omitted": [page(continuation="opaque", items=[pod], remaining=1), page(items=[other])],
            "middle-and-terminal-omitted": [page(continuation="opaque", items=[pod], remaining=2),
                                            page(continuation="b", items=[other]), page(items=[third])],
            "first-and-terminal-omitted": [page(continuation="opaque", items=[pod]),
                                            page(continuation="b", items=[other], remaining=1), page(items=[third])],
        }
        for name, pages in valid.items():
            with self.subTest(valid=name):
                rows, summary = C.complete_list(Pages(pages), "v1", "pods", "Pod")
                self.assertEqual(len(rows), len(pages))
                self.assertTrue(summary["complete"])
                self.assertNotIn("opaque", json.dumps(summary))
        cases = {
            "changed-rv": [page(continuation="a", items=[pod]), page(rv="901")],
            "repeated-token": [page(continuation="a", items=[pod]), page(continuation="a", items=[pod | {"metadata": meta("other", "other", C.NAMESPACE)}])],
            "duplicate": [page(continuation="a", items=[pod]), page(items=[pod])],
            "remaining-nonzero": [page(remaining=3)],
            "missing-rv": [page(rv=None)],
            "null-items": [{"apiVersion": "v1", "kind": "PodList", "metadata": {"resourceVersion": "900"}, "items": None}],
            "empty-continuation-page": [page(continuation="a")],
            "page-bound": [page(continuation=str(i), items=[pod | {"metadata": meta("pod-"+str(i), "uid-"+str(i), C.NAMESPACE)}]) for i in range(C.MAX_PAGES)],
            "advertised-item-missing": [page(continuation="a", items=[pod], remaining=1), page(remaining=0)],
            "advertised-item-missing-terminal-omitted": [page(continuation="a", items=[pod], remaining=1), page()],
            "inconsistent-positive-count": [page(continuation="a", items=[pod], remaining=2),
                                             page(continuation="b", items=[other], remaining=2)],
            "advertised-total-overrun-omitted": [page(continuation="a", items=[pod], remaining=1), page(items=[other, third])],
            "continuation-after-advertised-total": [page(continuation="a", items=[pod], remaining=1),
                                                    page(continuation="b", items=[other])],
        }
        for name, pages in cases.items():
            with self.subTest(case=name), self.assertRaises(C.Refused):
                C.complete_list(Pages(pages), "v1", "pods", "Pod")

    def test_named_current_object_change_refuses(self):
        fake, result = Fake(), report()
        get = fake.get
        path = C.api_path("apps/v1", "deployments", C.NAMESPACE, "dev-env-shelf")
        def changing(name, query=None):
            obj = get(name, query)
            if name == path:
                obj["metadata"]["resourceVersion"] = "901"
            return obj
        fake.get = changing
        with self.assertRaises(C.Refused) as caught:
            C.audit(fake, EXPECTED, result)
        self.assertEqual(caught.exception.code, "NamedObjectChangedDuringAudit")
        self.assertEqual(result["state"], "Failed")

    def test_http_error_and_main_output_never_emit_body_header_or_token(self):
        reader = C.Reader.__new__(C.Reader)
        reader.started, reader.requests, reader.bytes, reader.token = time.monotonic(), 0, 0, SECRET
        class ErrorOpener:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, 403, SECRET, {"Authorization": SECRET}, io.BytesIO(SECRET.encode()))
        reader.opener = ErrorOpener()
        with self.assertRaises(C.Refused) as caught:
            reader.get(C.api_path("v1", "pods", C.NAMESPACE))
        self.assertEqual((caught.exception.code, caught.exception.http_status), ("HTTPReadRefused", 403))
        output = io.StringIO()
        with mock.patch.object(C.pathlib.Path, "read_text", return_value=json.dumps(EXPECTED)), \
                mock.patch.object(C, "Reader", return_value=reader), contextlib.redirect_stdout(output):
            self.assertEqual(C.main(), 1)
        value = json.loads(output.getvalue())
        self.assertEqual(value["failure"], {"code": "HTTPReadRefused", "httpStatus": 403})
        self.assertNotIn(SECRET, output.getvalue())

    def test_no_redirect_and_budget_checks(self):
        with self.assertRaises(C.Refused):
            C.NoRedirect().redirect_request(None, None, 302, SECRET, {"Location": SECRET}, "https://provider.invalid")
        reader = C.Reader.__new__(C.Reader)
        reader.started, reader.requests, reader.bytes = time.monotonic(), C.MAX_REQUESTS, 0
        with self.assertRaises(C.Refused):
            reader.get(C.api_path("v1", "pods", C.NAMESPACE))
        reader.requests, reader.started = 0, time.monotonic() - C.MAX_SECONDS - 1
        with self.assertRaises(C.Refused):
            reader.get(C.api_path("v1", "pods", C.NAMESPACE))

    def test_matcher_normalization_preserves_literal_and_identifier_boundaries(self):
        values = lambda value: [{"name": "fixture", "expression": value}]
        self.assertEqual(C.normalize_conditions(values("request.namespace == 'dev-agents'")),
                         C.normalize_conditions(values("request.namespace\n ==  'dev-agents'")))
        self.assertNotEqual(C.normalize_conditions(values("request.userInfo.username in ['trusted']")),
                            C.normalize_conditions(values("request.userInfo.usernamein ['trusted']")))
        self.assertNotEqual(C.normalize_conditions(values("'a b' == 'a b'")),
                            C.normalize_conditions(values("'ab' == 'ab'")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
