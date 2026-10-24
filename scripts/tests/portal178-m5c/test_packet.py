#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML==6.0.3", "jsonschema==4.25.1"]
# ///
"""Unregistered packet guard + synthetic mapping tests, not live IdP/DB proof."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import importlib.util
import re
from pathlib import Path
import sys
import textwrap
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[3]
PACKET = ROOT / ".agents/reports/portal178-m5c/pending"
BLUEPRINT = ROOT / "kubernetes/main/apps/network/authentik/app/blueprints/97-dev-env-v2-portal-oidc.yaml"
CLUSTER = ROOT / "kubernetes/main/apps/database/cloudnative-pg/cluster/cluster16.yaml"
WITNESS = ROOT / "kubernetes/main/apps/dev-env-system/rbac/app/portal-witness.yaml"
RUNTIME = "dev_env_v2_portal_runtime"
MIGRATOR = "dev_env_v2_portal_migrator"
VERIFY_CHECKS = {"target", "roles", "database_acl", "schema_acl", "tables_acl", "default_acl",
                 "role_settings", "portal_objects", "cluster_role_settings", "portal_backends"}


class BlueprintLoader(yaml.SafeLoader):
    pass

BlueprintLoader.add_constructor("!Find", lambda loader, node: {"!Find": loader.construct_sequence(node, deep=True)})
BlueprintLoader.add_constructor("!KeyOf", lambda loader, node: {"!KeyOf": loader.construct_scalar(node)})


def load(name):
    return yaml.load((PACKET / name).read_text(), Loader=BlueprintLoader)


def structural_schema(path):
    crd = yaml.safe_load(path.read_text())
    return next(v for v in crd["spec"]["versions"] if v["storage"])["schema"]["openAPIV3Schema"]


def matches(selector, labels):
    if any(labels.get(key) != value for key, value in selector.get("matchLabels", {}).items()):
        return False
    for expression in selector.get("matchExpressions", []):
        key, operator = expression["key"], expression["operator"]
        present = key in labels
        if operator == "In" and (not present or labels[key] not in expression["values"]):
            return False
        if operator == "NotIn" and present and labels[key] in expression["values"]:
            return False
        if operator == "Exists" and not present:
            return False
        if operator == "DoesNotExist" and present:
            return False
        if operator not in {"In", "NotIn", "Exists", "DoesNotExist"}:
            raise AssertionError("unhandled selector operator")
    return True


def network_exclusion_proof(policies, pods):
    proposal = load("bff-network-exclusions.json")
    selected = {p["metadata"]["name"]: p for p in policies
                if p["metadata"]["name"] in proposal["excludeFromClusterPolicies"]}
    assert set(selected) == set(proposal["excludeFromClusterPolicies"])
    bff = {"io.kubernetes.pod.namespace": "dev-agents", "app.kubernetes.io/name": "dev-env-v2-portal"}
    checked = 0
    for policy in selected.values():
        before = policy["spec"]["endpointSelector"]
        after = deepcopy(before)
        expression, = [e for e in after["matchExpressions"]
                       if e["key"] == "app.kubernetes.io/name" and e["operator"] == "NotIn"]
        assert proposal["podNameLabel"] not in expression["values"]
        expression["values"].append(proposal["podNameLabel"])
        assert matches(before, bff) and not matches(after, bff)
        for pod in pods:
            labels = dict(pod["metadata"].get("labels", {}))
            labels["io.kubernetes.pod.namespace"] = pod["metadata"]["namespace"]
            assert labels.get("app.kubernetes.io/name") != proposal["podNameLabel"], "name already exists; stop"
            assert matches(before, labels) == matches(after, labels), "other pod selector changed"
            checked += 1
    return checked


def compose_hba(registered, allow_rule):
    if hba_stage_violations(registered, allow_rule):
        raise AssertionError("refuse to compose a reject stage that already allows")
    index = next(i for i, line in enumerate(registered) if line.startswith("host all "))
    return registered[:index] + [allow_rule] + registered[index:]


def hba_stage_violations(registered, allow_rule):
    violations = []
    for line in registered:
        if line.startswith("hostssl ") or "scram-sha-256" in line or not line.endswith(" reject"):
            violations.append("allow in reject stage")
        if not (line.startswith("host replication ") or line.startswith("hostnossl ") or line.startswith("host all ")):
            violations.append("non-reject rule")
    if (MIGRATOR in allow_rule or not allow_rule.startswith("hostssl dev_env_v2_portal " + RUNTIME + " ")
            or "scram-sha-256" not in allow_rule):
        violations.append("migrator or non-runtime allow")
    return violations


def load_blueprint():
    return yaml.load(BLUEPRINT.read_text(), Loader=BlueprintLoader)


def check_crds(cluster_crd, database_crd):
    original = yaml.safe_load((ROOT / "kubernetes/main/apps/database/cloudnative-pg/cluster/cluster16.yaml").read_text())
    original["metadata"]["namespace"] = "database"
    patch = load("cluster-portal.patch.yaml")
    merged = deepcopy(original)
    merged["spec"]["managed"] = patch["spec"]["managed"]
    merged["spec"]["postgresql"].update(patch["spec"]["postgresql"])
    jsonschema.validate(merged, structural_schema(cluster_crd))
    jsonschema.validate(load("database.yaml"), structural_schema(database_crd))
    print("PASS: supplied pinned/live CRD structural validation (not CEL/admission/controller execution)")


class Packet(unittest.TestCase):
    def test_role_and_projection_isolation(self):
        patch = load("cluster-portal.patch.yaml")["spec"]
        self.assertEqual(set(patch), {"managed", "postgresql"})
        roles = patch["managed"]["roles"]
        self.assertEqual([r["name"] for r in roles], [RUNTIME, MIGRATOR])
        for r in roles:
            self.assertIs(r["login"], r["name"] == RUNTIME)
            self.assertEqual(r["connectionLimit"], 8 if r["name"] == RUNTIME else 1)
            for flag in ("inherit", "superuser", "createdb", "createrole", "replication", "bypassrls"):
                self.assertIs(r[flag], False)
            self.assertEqual(r["inRoles"], [])
        docs = list(yaml.safe_load_all((PACKET / "role-externalsecrets.yaml").read_text()))
        self.assertEqual(len(docs), 1, "NOLOGIN migrator receives no password/Secret")
        self.assertTrue(roles[1]["disablePassword"])
        self.assertNotIn("passwordSecret", roles[1])
        docs.append(load("runtime-externalsecret.yaml"))
        for i, doc in enumerate(docs):
            spec = doc["spec"]
            self.assertEqual(set(spec), {"secretStoreRef", "refreshInterval", "target", "data"})
            self.assertEqual(spec["secretStoreRef"], {"kind": "ClusterSecretStore", "name": "onepassword-connect"})
            fields = ["DB_USERNAME", "DB_PASSWORD"]
            if i == 1:
                fields += ["SESSION_AEAD_KEY", "SESSION_GENERATION"]
            item = "dev-env"
            self.assertEqual(spec["data"], [{"secretKey": f, "remoteRef": {"key": item, "property": f}} for f in fields])
            self.assertNotIn("dev-env-v2-portal", [entry["remoteRef"]["key"] for entry in spec["data"]])
            self.assertNotIn("dev-env-v2-portal-runtime", [entry["remoteRef"]["key"] for entry in spec["data"]])
            self.assertNotIn("dataFrom", spec)
            self.assertEqual(spec["target"]["deletionPolicy"], "Delete")
            self.assertEqual(spec["target"]["creationPolicy"], "Owner")
            if i == 0:
                self.assertEqual(spec["target"]["template"]["metadata"]["labels"], {"cnpg.io/reload": "true"})
            expected_keys = {"username", "password"} if i == 0 else set(fields)
            self.assertEqual(set(spec["target"]["template"]["data"]), expected_keys)

    def test_database_ownership_and_hba_order(self):
        db = load("database.yaml")["spec"]
        self.assertEqual(db["owner"], "postgres")
        self.assertEqual(db["databaseReclaimPolicy"], "retain")
        self.assertEqual(db["schemas"], [{"name": "portal", "owner": MIGRATOR, "ensure": "present"}])
        rules = load("cluster-portal.patch.yaml")["spec"]["postgresql"]["pg_hba"]
        both = RUNTIME + "," + MIGRATOR
        reject = load("hba-stage-reject.yaml")["pg_hba"]
        allow = load("hba-stage-allow.yaml")["rule"]
        self.assertEqual(hba_stage_violations(reject, allow), [])
        self.assertEqual(rules, compose_hba(reject, allow))
        self.assertEqual(rules, [
            f"host replication {both} 0.0.0.0/0 reject", f"host replication {both} ::/0 reject",
            f"hostnossl all {both} 0.0.0.0/0 reject", f"hostnossl all {both} ::/0 reject",
            f"hostssl dev_env_v2_portal {RUNTIME} 10.42.0.0/16 scram-sha-256",
            f"host all {both} 0.0.0.0/0 reject", f"host all {both} ::/0 reject"])
        shadowed = reject + [allow]
        catch = next(i for i, line in enumerate(shadowed) if line.startswith("host all "))
        self.assertGreater(shadowed.index(allow), catch, "appending the allow after the catch-all never matches")
        self.assertNotIn(MIGRATOR, allow)

    def test_live_mapping_semantics(self):
        expression = next(entry["attrs"]["expression"] for entry in load_blueprint()["entries"]
                          if entry.get("id") == "portal-live-profile")
        group = SimpleNamespace(pk="00000000-0000-4000-8000-000000000001", name="synthetic")
        other = SimpleNamespace(pk="00000000-0000-4000-8000-000000000002", name="synthetic")
        direct = []
        current = SimpleNamespace(is_active=True, groups=SimpleNamespace(all=lambda: direct))
        def fresh_user(**kwargs):
            self.assertEqual(kwargs, {"pk": 1})
            return current
        module = ModuleType("authentik.core.models")
        module.User = SimpleNamespace(objects=SimpleNamespace(get=fresh_user))
        previous = sys.modules.get(module.__name__)
        sys.modules[module.__name__] = module
        try:
            def evaluate(source):
                scope = {}
                exec("def mapping(user):\n" + textwrap.indent(source, "    "), scope)
                # Stale supplied active/groups must not mask the fresh ORM read.
                result = scope["mapping"](SimpleNamespace(pk=1, is_active=True, groups=[group]))
                self.assertEqual(set(result), {"portal_is_active", "portal_is_admin"})
                self.assertTrue(all(type(v) is bool for v in result.values()))
                return result
            direct[:] = [group]
            empty = re.sub(r'PINNED_ADMINS_SHA256 = "[a-f0-9]*"', 'PINNED_ADMINS_SHA256 = ""', expression)
            self.assertFalse(evaluate(empty)["portal_is_admin"], "empty pin must deny")
            self.assertIn('PINNED_ADMINS_SHA256 = "88619af8c7c3e52efd2c0368020478809cc7a673617406cfd1bf584c9b1d75ee"', expression)
            pin = hashlib.sha256(str(group.pk).encode()).hexdigest()
            ready = re.sub(r'PINNED_ADMINS_SHA256 = "[a-f0-9]*"', f'PINNED_ADMINS_SHA256 = "{pin}"', expression)
            self.assertTrue(evaluate(ready)["portal_is_admin"])
            group.name = "renamed synthetic group"
            self.assertTrue(evaluate(ready)["portal_is_admin"])
            direct[:] = [other]  # same mutable name, different immutable identity
            self.assertFalse(evaluate(ready)["portal_is_admin"])
            direct[:] = []  # inherited membership is deliberately absent here
            self.assertFalse(evaluate(ready)["portal_is_admin"])
            direct[:] = [group]
            # Claims report THIS account's active/direct status, never the
            # Actor parent. These booleans are not principal admission: BFF must
            # independently require exact issuer/sub, including for an Actor
            # directly added to Admins or an expired Actor still marked active.
            for kind in ("actor-mirror", "actor-copy", "actor-expired", "service_account", "internal_service_account"):
                current.type = kind
                direct[:] = []
                self.assertEqual(evaluate(ready), {"portal_is_active": True, "portal_is_admin": False})
                direct[:] = [group]
                self.assertEqual(evaluate(ready), {"portal_is_active": True, "portal_is_admin": True})
            current.is_active = False
            self.assertEqual(evaluate(ready), {"portal_is_active": False, "portal_is_admin": False})
        finally:
            if previous is None:
                del sys.modules[module.__name__]
            else:
                sys.modules[module.__name__] = previous

    def test_exact_mapping_set_and_one_shot_executor(self):
        blueprint = load_blueprint()
        providers = [entry for entry in blueprint["entries"]
                     if entry["model"] == "authentik_providers_oauth2.oauth2provider"]
        self.assertEqual(providers, [{
            "model": "authentik_providers_oauth2.oauth2provider", "id": "portal-provider", "state": "present",
            "identifiers": {"name": "dev-env-v2-portal-internal"},
            "attrs": providers[0]["attrs"] | {"property_mappings": [
                {"!Find": ["authentik_providers_oauth2.scopemapping", ["managed", "goauthentik.io/providers/oauth2/scope-openid"]]},
                {"!Find": ["authentik_providers_oauth2.scopemapping", ["managed", "goauthentik.io/providers/oauth2/scope-profile"]]},
                {"!KeyOf": "portal-live-profile"}]}}])
        self.assertEqual(len(providers[0]["attrs"]["property_mappings"]), 3)
        self.assertEqual(blueprint["entries"][0]["attrs"]["expression"], "return False")
        self.assertFalse((PACKET / "98-portal-live-profile.yaml").exists(), "98 must not compete with blueprint 97")
        self.assertFalse((PACKET / "setup-jobs.yaml").exists(), "no rerunnable migration Job")
        path = ROOT / "scripts/tests/portal178-m5c/peer_bootstrap.py"
        spec = importlib.util.spec_from_file_location("portal_peer", path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        for mode, filename, prefix in (("--apply-reviewed-sha256", "010-bootstrap.sql", ""),
                ("--migrate-reviewed-sha256", "020-tables.sql", "SET ROLE dev_env_v2_portal_migrator;\n")):
            calls = []
            def fake_invoke(args, **kwargs):
                calls.append((args, kwargs))
                payload = {"metadata": {"uid": "synthetic-cluster"}, "status": {"currentPrimary": "postgres16-1"}} if args[1] == "cluster.postgresql.cnpg.io" else {
                    "metadata": {"labels": {"cnpg.io/cluster": "postgres16"}, "ownerReferences": [{"kind": "Cluster", "name": "postgres16", "uid": "synthetic-cluster", "controller": True}]}}
                return SimpleNamespace(stdout=json.dumps(payload))
            digest = hashlib.sha256((PACKET / filename).read_bytes()).hexdigest()
            with mock.patch.object(helper, "invoke", fake_invoke), mock.patch.object(sys, "argv", [str(path), mode, digest]):
                helper.main()
            self.assertEqual(len(calls), 3, "one execution only")
            command, kwargs = calls[-1]
            self.assertEqual(command, ["exec", "-i", "-n", "database", "postgres16-1", "-c", "postgres", "--",
                "psql", "-X", "--no-password", "--set=ON_ERROR_STOP=1", "--host=/controller/run", "--username=postgres",
                "--dbname=dev_env_v2_portal", "--file=-"])
            self.assertEqual(kwargs["input"], prefix + (PACKET / filename).read_text())
            calls.clear()
            with mock.patch.object(helper, "invoke", fake_invoke), mock.patch.object(sys, "argv", [str(path), mode, "0"*64]):
                with self.assertRaisesRegex(RuntimeError, "differs from reviewed hash"):
                    helper.main()
            self.assertEqual(len(calls), 2, "hash mismatch must execute no SQL")
        verify = PACKET / "030-verify.sql"
        digest = hashlib.sha256(verify.read_bytes()).hexdigest()
        calls = []
        proof = json.dumps({"checks": {name: True for name in VERIFY_CHECKS}})
        def fake_verify(args, **kwargs):
            calls.append((args, kwargs))
            if args[0] == "exec":
                return SimpleNamespace(stdout=proof)
            payload = {"metadata": {"uid": "synthetic-cluster"}, "status": {"currentPrimary": "postgres16-1"}} if args[1] == "cluster.postgresql.cnpg.io" else {
                "metadata": {"labels": {"cnpg.io/cluster": "postgres16"}, "ownerReferences": [{"kind": "Cluster", "name": "postgres16", "uid": "synthetic-cluster", "controller": True}]}}
            return SimpleNamespace(stdout=json.dumps(payload))
        with mock.patch.object(helper, "invoke", fake_verify), mock.patch.object(sys, "argv", [str(path), "--verify-reviewed-sha256", digest]), redirect_stdout(io.StringIO()):
            helper.main()
        self.assertEqual(len(calls), 3)
        self.assertTrue(calls[-1][1]["input"].startswith(helper.VERIFY_PREFIX))
        self.assertIn("BEGIN READ ONLY;", calls[-1][1]["input"])
        pinned = (PACKET / "030-verify.sql").read_text()
        helper.assert_verify_readonly(pinned)
        for writable in ("BEGIN READ WRITE;", "SET TRANSACTION READ WRITE;",
                         "SET default_transaction_read_only = off;",
                         "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE;",
                         "SET transaction_read_only = off;",
                         "SET LOCAL transaction_read_only TO off;",
                         "RESET ALL;", "RESET ROLE;", "DISCARD ALL;",
                         "\\c postgres", "\\i other.sql", "\\!\n",
                         "\\set ON_ERROR_STOP off"):
            with self.assertRaisesRegex(RuntimeError, "read-only transaction"):
                helper.assert_verify_readonly(writable)
        helper.assert_verify_readonly("\\set ON_ERROR_STOP on\nBEGIN READ ONLY;\nCOMMIT;\n")
        attacked = pinned + "\nSET LOCAL transaction_read_only TO off;\n"
        attacked_digest = hashlib.sha256(attacked.encode()).hexdigest()
        original_read = Path.read_bytes

        def read_attack(self):
            if self.name == "030-verify.sql":
                return attacked.encode()
            return original_read(self)

        calls.clear()
        with mock.patch.object(Path, "read_bytes", read_attack), mock.patch.object(helper, "invoke", fake_verify), mock.patch.object(sys, "argv", [str(path), "--verify-reviewed-sha256", attacked_digest]):
            with self.assertRaisesRegex(RuntimeError, "read-only transaction"):
                helper.main()
        self.assertEqual(len(calls), 2, "read-only denylist must refuse before psql")
        errors = io.StringIO()
        with redirect_stderr(errors), self.assertRaises(SystemExit):
            helper.stop(json.JSONDecodeError("bad", "secret-doc", 0))
        self.assertEqual(errors.getvalue().strip(),
                         "STOP: peer bootstrap JSONDecodeError; no retry or alternate identity")
        self.assertNotIn("secret-doc", errors.getvalue())
        self.assertTrue(issubclass(json.JSONDecodeError, ValueError))
        self.assertIn(KeyError, helper.STOP_ERRORS)
        calls.clear()
        with mock.patch.object(helper, "invoke", fake_verify), mock.patch.object(sys, "argv", [str(path), "--verify-reviewed-sha256", "0"*64]):
            with self.assertRaisesRegex(RuntimeError, "differs from reviewed hash"):
                helper.main()
        self.assertEqual(len(calls), 2, "verify hash mismatch must execute no SQL")
        with mock.patch.object(sys, "argv", [str(path)]):
            with self.assertRaises(SystemExit):
                helper.main()

    def test_staged_registration_surface(self):
        cluster = yaml.safe_load(CLUSTER.read_text())
        reject = load("hba-stage-reject.yaml")["pg_hba"]
        allow = load("hba-stage-allow.yaml")["rule"]
        self.assertEqual(cluster["spec"]["postgresql"]["pg_hba"], reject)
        self.assertNotIn("managed", cluster["spec"])
        self.assertEqual(hba_stage_violations(reject, allow), [])
        self.assertTrue(hba_stage_violations(reject + [allow], allow))
        self.assertTrue(hba_stage_violations(reject, allow.replace(RUNTIME, MIGRATOR)))
        registered = list(yaml.safe_load_all(WITNESS.read_text()))
        contract = list(yaml.safe_load_all((PACKET / "witness-rbac.yaml").read_text()))
        self.assertEqual(registered, contract)
        self.assertIn("./portal-witness.yaml", (WITNESS.parent / "kustomization.yaml").read_text())
        widened = deepcopy(registered)
        widened[1]["rules"][0]["verbs"].append("list")
        self.assertNotEqual(widened, contract)
        self.assertNotIn("create", widened[3]["rules"][0]["verbs"])
        for path in (ROOT / "kubernetes").rglob("*.yaml"):
            text = path.read_text(errors="replace")
            self.assertNotIn("hostssl dev_env_v2_portal", text, str(path))
            self.assertNotIn("98-portal-live-profile", text, str(path))
            self.assertNotIn("cluster-portal.patch.yaml", text, str(path))
            self.assertNotIn("hba-stage-allow.yaml", text, str(path))
            if "kind: ConfigMap" in text and "name: dev-env-v2-portal-admission" in text:
                self.fail("CLOSED admission seed is registered in " + str(path))
            if "kind: ExternalSecret" in text and "dev-env-v2-portal" in text:
                self.fail("portal ExternalSecret is registered in " + str(path))
        authentik = (ROOT / "kubernetes/main/apps/network/authentik/app/kustomization.yaml").read_text()
        self.assertIn("97-dev-env-v2-portal-oidc.yaml", authentik)
        self.assertNotIn("98-", authentik)

    def test_bff_only_network_proposal(self):
        folder = ROOT / "kubernetes/main/apps/dev-env-system/network/app"
        policies = [yaml.safe_load((folder / (name + ".yaml")).read_text())
                    for name in ("session-platform", "session-web")]
        pods = [{"metadata": {"namespace": "dev-agents", "labels": labels}} for labels in [
            {}, {"app.kubernetes.io/name": "synthetic-session"},
            {"app.kubernetes.io/name": "synthetic-session", "dev-env.haynesops.com/egress": "ops"},
            {"dev-env.haynesops.com/project-canary": "synthetic"},
            {"app.kubernetes.io/name": "dev-env-owned-native-lifecycle"}]]
        self.assertEqual(network_exclusion_proof(policies, pods), 10)
        proposal = load("bff-network-exclusions.json")
        self.assertEqual(proposal["requiredPodDNS"], {"dnsPolicy": "ClusterFirst",
            "dnsConfig": {"options": [{"name": "ndots", "value": "1"}]}})
        policy = load("bff-networkpolicy.yaml")["spec"]
        self.assertEqual(policy["endpointSelector"], {"matchLabels": {"app.kubernetes.io/name": "dev-env-v2-portal"}})
        self.assertEqual(policy["enableDefaultDeny"], {"ingress": True, "egress": True})
        self.assertEqual(policy["ingress"][0]["fromEndpoints"], [{"matchLabels": {
            "io.kubernetes.pod.namespace": "network", "app.kubernetes.io/name": "traefik",
            "app.kubernetes.io/instance": "traefik-internal-network"}}])
        self.assertEqual(policy["egress"][-1]["toFQDNs"], [{"matchName": "authentik.haynesnetwork.com"}])

    def test_exact_witness_grant(self):
        seed = load("witness-closed-bootstrap.yaml")
        self.assertEqual(seed["metadata"], {"name": "dev-env-v2-portal-admission", "namespace": "dev-agents"})
        self.assertEqual(seed["data"], {"schema": "1", "state": "closed"})
        docs = list(yaml.safe_load_all((PACKET / "witness-rbac.yaml").read_text()))
        self.assertEqual([d["kind"] for d in docs], ["ServiceAccount", "Role", "RoleBinding", "Role", "RoleBinding"])
        self.assertEqual(docs[0]["metadata"], {"name": "dev-env-v2-portal", "namespace": "dev-agents"})
        self.assertIs(docs[0]["automountServiceAccountToken"], False)
        for role, binding, verbs, subject in [
            (docs[1], docs[2], ["get"], {"kind": "ServiceAccount", "name": "dev-env-v2-portal", "namespace": "dev-agents"}),
            (docs[3], docs[4], ["get", "update"], {"kind": "ServiceAccount", "name": "dev-env", "namespace": "dev"}),
        ]:
            self.assertEqual(role["metadata"]["namespace"], "dev-agents")
            self.assertEqual(role["rules"], [{"apiGroups": [""], "resources": ["configmaps"],
                "resourceNames": ["dev-env-v2-portal-admission"], "verbs": verbs}])
            self.assertEqual(binding["metadata"]["namespace"], "dev-agents")
            self.assertEqual(binding["roleRef"], {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": role["metadata"]["name"]})
            self.assertEqual(binding["subjects"], [subject])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster-crd", type=Path)
    parser.add_argument("--database-crd", type=Path)
    parser.add_argument("--network-snapshot", type=Path)
    args, test_args = parser.parse_known_args()
    if args.cluster_crd or args.database_crd:
        if not (args.cluster_crd and args.database_crd):
            parser.error("both CRDs required")
        check_crds(args.cluster_crd, args.database_crd)
    if args.network_snapshot:
        policies = json.loads((args.network_snapshot / "ccnps.json").read_text())["items"]
        pods = json.loads((args.network_snapshot / "dev-agents-pods.json").read_text())["items"]
        print("PASS: live snapshot proposed selector comparisons", network_exclusion_proof(policies, pods),
              "(no policy applied; not effective-network proof)")
    unittest.main(argv=[sys.argv[0], *test_args])
