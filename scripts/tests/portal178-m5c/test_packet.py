#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML==6.0.3", "jsonschema==4.25.1"]
# ///
"""Unregistered packet guard + synthetic mapping tests, not live IdP/DB proof."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import textwrap
from types import ModuleType, SimpleNamespace
import unittest

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[3]
PACKET = ROOT / ".agents/reports/portal178-m5c/pending"
RUNTIME = "dev_env_v2_portal_runtime"
MIGRATOR = "dev_env_v2_portal_migrator"


def load(name):
    return yaml.safe_load((PACKET / name).read_text())


def structural_schema(path):
    crd = json.loads(path.read_text())
    return next(v for v in crd["spec"]["versions"] if v["storage"])["schema"]["openAPIV3Schema"]


def check_crds(cluster_crd, database_crd):
    original = yaml.safe_load((ROOT / "kubernetes/main/apps/database/cloudnative-pg/cluster/cluster16.yaml").read_text())
    original["metadata"]["namespace"] = "database"
    patch = load("cluster-portal.patch.yaml")
    merged = deepcopy(original)
    merged["spec"]["managed"] = patch["spec"]["managed"]
    merged["spec"]["postgresql"].update(patch["spec"]["postgresql"])
    jsonschema.validate(merged, structural_schema(cluster_crd))
    jsonschema.validate(load("database.yaml"), structural_schema(database_crd))
    print("PASS: live CRD structural validation (not CEL/admission/controller execution)")


class Packet(unittest.TestCase):
    def test_role_and_projection_isolation(self):
        patch = load("cluster-portal.patch.yaml")["spec"]
        self.assertEqual(set(patch), {"managed", "postgresql"})
        roles = patch["managed"]["roles"]
        self.assertEqual([r["name"] for r in roles], [RUNTIME, MIGRATOR])
        for r in roles:
            self.assertTrue(r["login"])
            for flag in ("inherit", "superuser", "createdb", "createrole", "replication", "bypassrls"):
                self.assertIs(r[flag], False)
            self.assertEqual(r["inRoles"], [])
        docs = list(yaml.safe_load_all((PACKET / "role-externalsecrets.yaml").read_text()))
        docs.append(load("runtime-externalsecret.yaml"))
        for i, doc in enumerate(docs):
            spec = doc["spec"]
            self.assertEqual(set(spec), {"secretStoreRef", "refreshInterval", "target", "data"})
            self.assertEqual(spec["secretStoreRef"], {"kind": "ClusterSecretStore", "name": "onepassword-connect"})
            fields = ["DB_USERNAME", "DB_PASSWORD"]
            if i == 2:
                fields += ["SESSION_AEAD_KEY", "SESSION_GENERATION"]
            item = "dev-env-v2-portal-migration" if i == 1 else "dev-env-v2-portal-runtime"
            self.assertEqual(spec["data"], [{"secretKey": f, "remoteRef": {"key": item, "property": f}} for f in fields])
            self.assertEqual(spec["target"]["deletionPolicy"], "Delete")
            self.assertEqual(spec["target"]["creationPolicy"], "Owner")
            expected_keys = {"username", "password"} if i < 2 else set(fields)
            self.assertEqual(set(spec["target"]["template"]["data"]), expected_keys)

    def test_database_ownership_and_hba_order(self):
        db = load("database.yaml")["spec"]
        self.assertEqual(db["owner"], "postgres")
        self.assertEqual(db["databaseReclaimPolicy"], "retain")
        self.assertEqual(db["schemas"], [{"name": "portal", "owner": MIGRATOR, "ensure": "present"}])
        rules = load("cluster-portal.patch.yaml")["spec"]["postgresql"]["pg_hba"]
        both = RUNTIME + "," + MIGRATOR
        self.assertEqual(rules, [
            f"hostnossl all {both} 0.0.0.0/0 reject", f"hostnossl all {both} ::/0 reject",
            f"hostssl dev_env_v2_portal {RUNTIME} 10.42.0.0/16 scram-sha-256",
            f"hostssl dev_env_v2_portal {MIGRATOR} 10.42.0.0/16 scram-sha-256",
            f"host all {both} 0.0.0.0/0 reject", f"host all {both} ::/0 reject"])

    def test_live_mapping_semantics(self):
        expression = load("98-portal-live-profile.yaml")["entries"][0]["attrs"]["expression"]
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
            self.assertFalse(evaluate(expression)["portal_is_admin"], "empty production pin must deny")
            pin = hashlib.sha256(str(group.pk).encode()).hexdigest()
            ready = expression.replace('PINNED_ADMINS_SHA256 = ""', f'PINNED_ADMINS_SHA256 = "{pin}"')
            self.assertTrue(evaluate(ready)["portal_is_admin"])
            group.name = "renamed synthetic group"
            self.assertTrue(evaluate(ready)["portal_is_admin"])
            direct[:] = [other]  # same mutable name, different immutable identity
            self.assertFalse(evaluate(ready)["portal_is_admin"])
            direct[:] = []  # inherited membership is deliberately absent here
            self.assertFalse(evaluate(ready)["portal_is_admin"])
            direct[:] = [group]
            current.is_active = False
            self.assertEqual(evaluate(ready), {"portal_is_active": False, "portal_is_admin": False})
        finally:
            if previous is None:
                del sys.modules[module.__name__]
            else:
                sys.modules[module.__name__] = previous

    def test_isolated_executor_candidates(self):
        jobs = list(yaml.safe_load_all((PACKET / "setup-jobs.yaml").read_text()))
        self.assertEqual(len(jobs), 2)
        for job, role, secret in zip(jobs, ["postgres", MIGRATOR],
                                    ["cloudnative-pg-secret", "dev-env-v2-portal-migration-db"], strict=True):
            pod = job["spec"]["template"]["spec"]
            self.assertIs(pod["automountServiceAccountToken"], False)
            self.assertEqual(job["spec"]["backoffLimit"], 0)
            self.assertEqual(pod["restartPolicy"], "Never")
            self.assertNotIn("serviceAccountName", pod)
            container, = pod["containers"]
            self.assertNotIn("envFrom", container)
            env = {e["name"]: e for e in container["env"]}
            self.assertEqual(env["PGUSER"]["value"], role)
            self.assertEqual(env["PGHOST"]["value"], "postgres16-rw.database.svc.cluster.local")
            self.assertEqual(env["PGSSLMODE"]["value"], "verify-full")
            self.assertEqual(env["PGPASSWORD"]["valueFrom"], {"secretKeyRef": {"name": secret, "key": "password"}})
            public_ca, = [v for v in pod["volumes"] if "secret" in v]
            self.assertEqual(public_ca["secret"], {"secretName": "postgres16-ca", "items": [{"key": "ca.crt", "path": "ca.crt"}]})
            self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        policy = load("setup-networkpolicy.yaml")["spec"]
        self.assertEqual(policy["podSelector"], {"matchLabels": {"app.kubernetes.io/part-of": "dev-env-v2-portal-db-setup"}})
        self.assertEqual(policy["ingress"], [])
        self.assertEqual(policy["egress"][1], {"to": [{"podSelector": {"matchLabels": {"cnpg.io/cluster": "postgres16"}}}],
                                                "ports": [{"port": 5432, "protocol": "TCP"}]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster-crd", type=Path)
    parser.add_argument("--database-crd", type=Path)
    args, test_args = parser.parse_known_args()
    if args.cluster_crd or args.database_crd:
        if not (args.cluster_crd and args.database_crd):
            parser.error("both CRDs required")
        check_crds(args.cluster_crd, args.database_crd)
    unittest.main(argv=[sys.argv[0], *test_args])
