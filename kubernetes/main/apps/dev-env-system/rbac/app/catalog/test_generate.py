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
        groups = ["rbac.authorization.k8s.io", "admissionregistration.k8s.io", "apiextensions.k8s.io", "apiregistration.k8s.io", "flowcontrol.apiserver.k8s.io", "dev-env.haynesops.com", "kyverno.io", "policies.kyverno.io", "future.kyverno.io", "external-secrets.io", "generators.external-secrets.io", "future.external-secrets.io", "source.toolkit.fluxcd.io", "future.fluxcd.io"]
        snapshot = catalog.normalize([listing(g + "/v1", ["objects", "objects/future"]) for g in groups] + [listing("v1", ["pods"])])
        self.assertEqual({group for group, _, _ in permissions(catalog.role(snapshot))}, {""})

    def test_cilium_is_fail_closed_except_namespaced_network_policy(self):
        blocked = ["ciliumendpoints", "ciliumidentities", "ciliumcidrgroups", "ciliumloadbalancerippools", "ciliuml2announcementpolicies", "ciliumnodes", "ciliumlocalredirectpolicies", "ciliumclusterwidenetworkpolicies", "futureciliumstate", "ciliumnetworkpoliciesevil"]
        snapshot = catalog.normalize([listing("cilium.io/v2", [name for parent in blocked for name in (parent, parent + "/status")] + ["ciliumnetworkpolicies", "ciliumnetworkpolicies/status", "ciliumnetworkpolicies/status/future"])])
        self.assertEqual({r for _, r, _ in permissions(catalog.role(snapshot))}, {"ciliumnetworkpolicies", "ciliumnetworkpolicies/status", "ciliumnetworkpolicies/status/future"})
        cluster_scoped = catalog.normalize([listing("cilium.io/v2", ["ciliumnetworkpolicies"], namespaced=False), listing("v1", ["pods"])])
        self.assertFalse(any(g == "cilium.io" for g, _, _ in permissions(catalog.role(cluster_scoped))))

    def test_actual_role_cilium_and_flow_control_exclusions(self):
        granted = permissions(json.loads(catalog.DEFAULT_ROLE.read_text()))
        self.assertFalse(any(g == "flowcontrol.apiserver.k8s.io" for g, _, _ in granted))
        self.assertEqual({r for g, r, _ in granted if g == "cilium.io"}, {"ciliumnetworkpolicies", "ciliumnetworkpolicies/status"})

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


class RepoCRDSyncTests(unittest.TestCase):
    def setUp(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is installed by hosted CI for repo CRD coverage")
        self.yaml = yaml
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write_crd(self, group="dev-env.haynesops.com", name="newcrds", versions=None, scope="Namespaced", kind="Fixture"):
        spec = {"group": group, "names": {"plural": name, "kind": kind}, "scope": scope, "versions": versions or [{"name": "v1", "served": True, "subresources": {"status": {}, "scale": {}}}]}
        self.root.joinpath("crd.yaml").write_text("kind: CustomResourceDefinition\n" + self.yaml.safe_dump({"spec": spec}))

    def test_new_excluded_crd_passes_predeploy_without_permission_growth(self):
        initial = catalog.normalize([listing("v1", ["pods"])])
        self.write_crd()
        with self.assertRaises(catalog.CatalogError):
            catalog.check_crds(initial, self.root)
        merged = catalog.sync_crds(initial, self.root)
        catalog.check_crds(merged, self.root)
        self.assertEqual(catalog.role(initial), catalog.role(merged))
        self.assertNotEqual(initial, merged)  # Runtime discovery must still flag absence.
        self.assertEqual(merged, catalog.sync_crds(merged, self.root))
        records = {r["resource"]: r for r in merged["resources"]}
        self.assertEqual(records["newcrds/scale"]["kinds"], ["Scale"])
        self.assertEqual(records["newcrds/status"]["verbs"], ["get", "patch", "update"])

    def test_new_version_preserves_captured_metadata_and_permissions(self):
        initial = catalog.normalize([listing("v1", ["pods"]), listing("sample.example/v1", ["widgets"], ["get", "patch"])])
        self.write_crd(group="sample.example", name="widgets", versions=[{"name": "v1", "served": True}, {"name": "v2", "served": True}, {"name": "v3", "served": False}])
        merged = catalog.sync_crds(initial, self.root)
        catalog.check_crds(merged, self.root)
        self.assertEqual(catalog.role(initial), catalog.role(merged))
        original_pod = next(r for r in initial["resources"] if r["resource"] == "pods")
        self.assertIn(original_pod, merged["resources"])
        widget = next(r for r in merged["resources"] if r["resource"] == "widgets")
        self.assertEqual(widget["versions"], ["v1", "v2"])
        self.assertEqual(widget["verbs"], ["get", "patch"])

    def test_retired_version_remains_until_explicit_live_capture(self):
        initial = catalog.normalize([listing("dev-env.haynesops.com/v1", ["widgets"]), listing("dev-env.haynesops.com/v2", ["widgets"]), listing("v1", ["pods"])])
        self.write_crd(name="widgets", versions=[{"name": "v1", "served": False}, {"name": "v2", "served": True}])
        self.assertEqual(initial, catalog.sync_crds(initial, self.root))

    def test_scope_or_kind_conflict_fails_instead_of_rewriting_source(self):
        initial = catalog.normalize([listing("v1", ["pods"]), listing("sample.example/v1", ["widgets"])])
        for changed in [{"scope": "Cluster"}, {"kind": "ChangedKind"}]:
            self.write_crd(group="sample.example", name="widgets", **changed)
            with self.subTest(changed=changed), self.assertRaises(catalog.CatalogError):
                catalog.sync_crds(initial, self.root)

    def test_new_cilium_data_plane_declaration_adds_no_permission(self):
        initial = catalog.normalize([listing("v1", ["pods"])])
        self.write_crd(group="cilium.io", name="futurestate", scope="Cluster")
        merged = catalog.sync_crds(initial, self.root)
        catalog.check_crds(merged, self.root)
        self.assertEqual(catalog.role(initial), catalog.role(merged))

    def test_unknown_subresources_and_conflicting_declarations_fail_closed(self):
        initial = catalog.normalize([listing("v1", ["pods"])])
        self.write_crd(versions=[{"name": "v1", "served": True, "subresources": {"future": {}}}])
        with self.assertRaises(catalog.CatalogError):
            catalog.sync_crds(initial, self.root)
        self.write_crd()
        self.root.joinpath("other.yaml").write_text(self.root.joinpath("crd.yaml").read_text().replace("scope: Namespaced", "scope: Cluster"))
        with self.assertRaises(catalog.CatalogError):
            catalog.sync_crds(initial, self.root)


if __name__ == "__main__":
    unittest.main()
