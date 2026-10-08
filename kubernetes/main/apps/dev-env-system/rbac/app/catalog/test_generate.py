"""Small offline security and discovery-contract tests; no cluster access."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("grant_catalog", Path(__file__).with_name("generate.py"))
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)


def listing(group_version, names, verbs=None, namespaced=True):
    return {
        "groupVersion": group_version,
        "resources": [
            {
                "name": name,
                "kind": "Fixture",
                "namespaced": namespaced,
                "verbs": verbs or ["get", "list", "create", "update", "delete"],
            }
            for name in names
        ],
    }


def permissions(role):
    return {
        (group, resource, verb)
        for rule in role["rules"]
        for group in rule["apiGroups"]
        for resource in rule["resources"]
        for verb in rule["verbs"]
    }


class CatalogSecurityTests(unittest.TestCase):
    def test_dangerous_parent_resources_and_all_descendants_are_absent(self):
        blocked = {
            "v1": ["secrets", "serviceaccounts/token", "nodes/proxy", "pods/ephemeralcontainers", "pods/portforward", "pods/proxy", "services/proxy"],
            "certificates.k8s.io/v1": ["certificatesigningrequests/approval"],
            "cilium.io/v2": ["ciliumclusterwidenetworkpolicies"],
        }
        lists = [listing(gv, [name for parent in parents for name in (parent, parent + "/future")]) for gv, parents in blocked.items()]
        lists.append(listing("v1", ["pods", "pods/exec", "pods/attach", "configmaps"]))
        granted = permissions(catalog.role(catalog.normalize(lists)))
        for gv, parents in blocked.items():
            group = gv.rsplit("/", 1)[0] if "/" in gv else ""
            for parent in parents:
                self.assertFalse(any(g == group and (r == parent or r.startswith(parent + "/")) for g, r, _ in granted))
        self.assertIn(("", "pods/exec", "create"), granted)
        self.assertIn(("", "pods/attach", "create"), granted)

    def test_controller_and_authority_groups_have_no_permissions(self):
        groups = ["rbac.authorization.k8s.io", "admissionregistration.k8s.io", "apiextensions.k8s.io", "apiregistration.k8s.io", "dev-env.haynesops.com", "kyverno.io", "policies.kyverno.io", "future.kyverno.io", "external-secrets.io", "generators.external-secrets.io", "future.external-secrets.io", "source.toolkit.fluxcd.io", "future.fluxcd.io"]
        snapshot = catalog.normalize([listing(g + "/v1", ["objects", "objects/future"]) for g in groups] + [listing("v1", ["pods"])])
        self.assertEqual({group for group, _, _ in permissions(catalog.role(snapshot))}, {""})

    def test_dangerous_verbs_are_removed_even_in_a_new_group(self):
        snapshot = catalog.normalize([listing("new.example/v1", ["widgets"], ["get", "create", "bind", "escalate", "impersonate"])])
        self.assertEqual(permissions(catalog.role(snapshot)), {("new.example", "widgets", "get"), ("new.example", "widgets", "create")})

    def test_unknown_verbs_and_wildcard_metadata_fail_closed(self):
        for response in [listing("v1", ["pods"], ["get", "connect"]), listing("v1", ["*"]), listing("*/v1", ["widgets"]), listing("v1", ["pods"], ["*"])]:
            with self.subTest(response=response), self.assertRaises(catalog.CatalogError):
                catalog.normalize([response])

    def test_namespace_scope_conflicts_fail_closed(self):
        with self.assertRaises(catalog.CatalogError):
            catalog.normalize([listing("sample.example/v1", ["widgets"]), listing("sample.example/v2", ["widgets"], namespaced=False)])

    def test_actual_snapshot_and_role_have_no_wildcard_or_nonresource_rules(self):
        snapshot = catalog.validate_snapshot(json.loads(catalog.DEFAULT_SNAPSHOT.read_text()))
        rendered = json.loads(catalog.DEFAULT_ROLE.read_text())
        self.assertEqual(catalog.serialized(catalog.role(snapshot)), catalog.DEFAULT_ROLE.read_text())
        for rule in rendered["rules"]:
            self.assertEqual(set(rule), {"apiGroups", "resources", "verbs"})
            self.assertTrue(rule["apiGroups"] and rule["resources"] and rule["verbs"])
            self.assertFalse(any("*" in item for field in rule.values() for item in field))
            self.assertFalse({"bind", "escalate", "impersonate"} & set(rule["verbs"]))

    def test_watcher_has_no_resource_permission_and_is_cpu_limited(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is installed by hosted CI")
        documents = list(yaml.safe_load_all(catalog.DIRECTORY.parent.joinpath("grant-discovery.yaml").read_text()))
        watcher_role = next(d for d in documents if d["kind"] == "ClusterRole")
        self.assertEqual(watcher_role["rules"], [{"nonResourceURLs": ["/api", "/api/*", "/apis", "/apis/*"], "verbs": ["get"]}])
        cronjob = next(d for d in documents if d["kind"] == "CronJob")
        job = cronjob["spec"]["jobTemplate"]["spec"]
        pod = job["template"]["spec"]
        self.assertEqual(job["backoffLimit"], 0)
        self.assertLessEqual(job["activeDeadlineSeconds"], 300)
        self.assertEqual(pod["containers"][0]["resources"]["limits"]["cpu"], "100m")
        self.assertIn("requiredDuringSchedulingIgnoredDuringExecution", pod["affinity"]["nodeAffinity"])
        self.assertEqual({v["configMap"]["name"] for v in pod["volumes"]}, {"dev-env-grant-discovery"})


class DiscoveryContractTests(unittest.TestCase):
    def test_every_served_version_is_read_and_only_metadata_is_retained(self):
        responses = {
            "/api": {"versions": ["v1"], "serverAddressByClientCIDRs": [{"serverAddress": "not-public"}]},
            "/apis": {"groups": [{"name": "sample.example", "preferredVersion": {"version": "v2"}, "versions": [{"version": "v1", "groupVersion": "sample.example/v1"}, {"version": "v2", "groupVersion": "sample.example/v2"}]}]},
            "/api/v1": listing("v1", ["pods"]),
            "/apis/sample.example/v1": listing("sample.example/v1", ["legacy", "widgets"]),
            "/apis/sample.example/v2": listing("sample.example/v2", ["widgets"], ["get", "patch"]),
        }
        responses["/api/v1"]["resources"][0]["storageVersionHash"] = "omit-this"
        paths = []
        snapshot = catalog.discover(lambda path: paths.append(path) or responses[path])
        self.assertEqual(set(paths), set(responses))
        widget = next(r for r in snapshot["resources"] if r["resource"] == "widgets")
        self.assertEqual(widget["versions"], ["v1", "v2"])
        self.assertIn("patch", widget["verbs"])
        self.assertIn(("sample.example", "legacy", "create"), permissions(catalog.role(snapshot)))
        self.assertNotIn("not-public", catalog.serialized(snapshot))
        self.assertNotIn("omit-this", catalog.serialized(snapshot))

    def test_new_group_changes_snapshot_and_must_be_reviewed(self):
        initial = catalog.normalize([listing("v1", ["pods"])])
        added = catalog.normalize([listing("v1", ["pods"]), listing("new.example/v1", ["widgets"])])
        self.assertNotEqual(initial, added)
        self.assertNotEqual(catalog.role(initial), catalog.role(added))
        # A new excluded group also triggers discovery drift, without role growth.
        blocked = catalog.normalize([listing("v1", ["pods"]), listing("new.fluxcd.io/v1", ["widgets"])])
        self.assertNotEqual(initial, blocked)
        self.assertEqual(catalog.role(initial), catalog.role(blocked))

    def test_snapshot_rejects_extra_fields_duplicates_and_noncanonical_order(self):
        snapshot = catalog.normalize([listing("v1", ["pods", "configmaps"])])
        for changed in [dict(snapshot, address="not-public"), dict(snapshot, resources=snapshot["resources"] * 2), dict(snapshot, resources=list(reversed(snapshot["resources"])))] :
            with self.subTest(snapshot=changed), self.assertRaises(catalog.CatalogError):
                catalog.validate_snapshot(changed)

    def test_reader_rejects_object_urls_and_bounds_child_process(self):
        reader = catalog.DiscoveryReader(False)
        for path in ["/api/v1/namespaces", "/apis/apps/v1/deployments", "/api/v1/namespaces?watch=true", "/api/v1/../../secrets", "https://elsewhere/api", "/metrics"]:
            with self.subTest(path=path), self.assertRaises(catalog.CatalogError):
                reader(path)
        with patch.object(catalog.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b'{"versions":["v1"]}'
            reader("/api")
            self.assertEqual(run.call_args.args[0], ["kubectl", "get", "--raw", "/api"])
            self.assertLessEqual(run.call_args.kwargs["timeout"], 5)

    def test_repo_crd_new_served_version_or_subresource_requires_refresh(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML is installed by hosted CI for repo CRD coverage")
        crd = {"kind": "CustomResourceDefinition", "spec": {"group": "sample.example", "names": {"plural": "widgets", "kind": "Fixture"}, "scope": "Namespaced", "versions": [{"name": "v1", "served": True, "subresources": {"status": {}}}]}}
        complete = catalog.normalize([listing("sample.example/v1", ["widgets", "widgets/status"])])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "crd.yaml"
            # YAML leading kind is also required by the cheap scan.
            path.write_text("kind: CustomResourceDefinition\n" + yaml.safe_dump({"spec": crd["spec"]}))
            catalog.check_crds(complete, Path(temp))
            with self.assertRaises(catalog.CatalogError):
                catalog.check_crds(catalog.normalize([listing("sample.example/v1", ["widgets"])]), Path(temp))
            updated = copy.deepcopy(crd)
            updated["spec"]["versions"].append({"name": "v2", "served": True})
            path.write_text("kind: CustomResourceDefinition\n" + yaml.safe_dump({"spec": updated["spec"]}))
            with self.assertRaises(catalog.CatalogError):
                catalog.check_crds(complete, Path(temp))


if __name__ == "__main__":
    unittest.main()
