#!/usr/bin/env python3
"""Render the unregistered Story178 consumer packet and reject live registration."""
import re
import subprocess
import unittest
from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPONENT = ROOT / ".agents/reports/portal178-m5d/component"
PENDING = ROOT / ".agents/reports/portal178-m5c/pending"
M5A = ROOT / ".agents/reports/portal178-m5a/pending/externalsecret.yaml"
CONTRACT = ROOT / ".agents/reports/portal178-m5d/DEPLOYMENT-CONTRACT.md"
CHECKLIST = ROOT / ".agents/reports/portal178-m5d/LAUNCH-CHECKLIST.md"
CLUSTER = ROOT / "kubernetes/main/apps/database/cloudnative-pg/cluster/cluster16.yaml"
RUNTIME_CONTRACT = Path("/home/dev/work/orders/portal178-runtime-assembly-20261024/canonical-item-01/RUNTIME-CONTRACT.md")
STABLE_CONTRACT = Path("/home/dev/work/orders/portal178-runtime-assembly-20261024/integration-fixes-02/RUNTIME-CONTRACT.md")
M6_HEAD = "6a522e843a8768099d8eb8086f9ff8272f94b725"

RESOURCES = [
    "oidc-externalsecret.yaml",
    "runtime-externalsecret.yaml",
    "role-externalsecret.yaml",
    "networkpolicy.yaml",
    "config.yaml",
    "postgres-ca.yaml",
    "admission-ca.yaml",
    "issuer-ca.yaml",
    "openproject-ca.yaml",
    "operator-ca.yaml",
    "deployment.yaml",
    "service.yaml",
    "ingressroute.yaml",
    "serverstransport.yaml",
]
OIDC_FIELDS = ["OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"]
RUNTIME_FIELDS = ["DB_USERNAME", "DB_PASSWORD", "SESSION_AEAD_KEY", "SESSION_GENERATION"]
DB_FIELDS = ["DB_USERNAME", "DB_PASSWORD"]
CANONICAL_ITEM = "dev-env"
HISTORICAL_ITEMS = {"dev-env-v2-portal", "dev-env-v2-portal-runtime"}
PORTAL_PROPERTIES = set(OIDC_FIELDS + RUNTIME_FIELDS)
STORE = {"kind": "ClusterSecretStore", "name": "onepassword-connect"}
FORBIDDEN_KINDS = {
    "Secret", "Certificate", "Issuer", "ClusterIssuer", "Role", "ClusterRole",
    "RoleBinding", "ClusterRoleBinding", "Ingress", "Gateway", "HTTPRoute",
    "Database", "Cluster", "ServiceAccount",
}
ATTESTATION = [
    "source cluster",
    "resource UID",
    "resourceVersion",
    "export UTC",
    "administrator/owner attestation",
    "SHA-256 of the delivered file",
    "DER SHA-256 fingerprint",
    "notBefore",
    "notAfter",
    "renewal owner/contact",
    "rotation statement",
]



def remote_keys(doc):
    return [item["remoteRef"]["key"] for item in doc["spec"]["data"]]


def allowed_properties(doc):
    name = doc["metadata"]["name"]
    if name.endswith("-oidc"):
        return set(OIDC_FIELDS)
    if name.endswith("-runtime-db"):
        return set(DB_FIELDS)
    return set(RUNTIME_FIELDS)


def projection_violations(doc, allowed):
    spec = doc["spec"]
    violations = []
    if "dataFrom" in spec:
        violations.append("dataFrom")
    projected = []
    for item in spec.get("data", []):
        ref = item["remoteRef"]
        if ref.get("key") != CANONICAL_ITEM:
            violations.append(ref.get("key"))
        if ref.get("key") in HISTORICAL_ITEMS:
            violations.append(ref.get("key"))
        if ref.get("property") not in allowed:
            violations.append(ref.get("property"))
        if set(ref) - {"key", "property"}:
            violations.append("extra-remoteRef")
        projected.append(ref.get("property"))
    if "data" in spec and set(projected) != set(allowed):
        violations.append("property-set")
    return violations


def dev_env_properties(tree):
    inline = re.compile(r"key:\s*dev-env(?![A-Za-z0-9_-])\s*,\s*property:\s*([A-Za-z0-9_]+)")
    block = re.compile(r"key:\s*dev-env(?![A-Za-z0-9_-])\s*\n\s*property:\s*([A-Za-z0-9_]+)")
    found = set()
    for path in tree.rglob("*.yaml"):
        text = path.read_text(errors="replace")
        found.update(inline.findall(text))
        found.update(block.findall(text))
    return found


def load(path):
    return yaml.safe_load(path.read_text())


def render():
    completed = subprocess.run(
        ["kustomize", "build", str(COMPONENT)],
        check=True, capture_output=True, text=True,
    )
    docs = [doc for doc in yaml.safe_load_all(completed.stdout) if doc]
    return completed.stdout, docs


def by_kind(docs, kind):
    return [doc for doc in docs if doc.get("kind") == kind]


def remote_properties(doc):
    return [item["remoteRef"]["property"] for item in doc["spec"]["data"]]


class ComponentRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rendered, cls.docs = render()

    def test_kustomize_resource_list_stays_closed(self):
        manifest = load(COMPONENT / "kustomization.yaml")
        self.assertEqual(manifest["resources"], RESOURCES)
        self.assertNotIn("ccnp-exclusion.yaml", manifest["resources"])
        self.assertIn("runtime-externalsecret.yaml", manifest["resources"])
        self.assertIn("role-externalsecret.yaml", manifest["resources"])
        self.assertEqual(len(self.docs), len(RESOURCES))

    def test_every_object_is_marked_unregistered(self):
        for doc in self.docs:
            annotations = doc["metadata"].get("annotations", {})
            self.assertEqual(annotations.get("dev-env.haynesops.com/portal-registration"), "unregistered", doc["kind"])
            self.assertNotEqual(doc["metadata"]["name"], "dev-env-v2-portal-admission")

    def test_exact_external_secret_projections(self):
        secrets = by_kind(self.docs, "ExternalSecret")
        self.assertEqual(
            sorted((doc["metadata"]["namespace"], doc["metadata"]["name"]) for doc in secrets),
            [
                ("database", "dev-env-v2-portal-runtime-db"),
                ("dev-agents", "dev-env-v2-portal-oidc"),
                ("dev-agents", "dev-env-v2-portal-runtime"),
            ],
        )
        for doc in secrets:
            self.assertEqual(doc["spec"]["secretStoreRef"], STORE)
            self.assertNotIn("dataFrom", doc["spec"])
            self.assertEqual(doc["spec"]["target"]["name"], doc["metadata"]["name"])
            self.assertNotIn("PORTAL_SUBJECT", remote_properties(doc))
        indexed = {(doc["metadata"]["namespace"], doc["metadata"]["name"]): doc for doc in secrets}
        oidc = indexed[("dev-agents", "dev-env-v2-portal-oidc")]
        runtime = indexed[("dev-agents", "dev-env-v2-portal-runtime")]
        database = indexed[("database", "dev-env-v2-portal-runtime-db")]
        self.assertEqual(remote_properties(oidc), OIDC_FIELDS)
        self.assertEqual(sorted(oidc["spec"]["target"]["template"]["data"]), OIDC_FIELDS)
        self.assertEqual([item["remoteRef"]["key"] for item in oidc["spec"]["data"]], [CANONICAL_ITEM, CANONICAL_ITEM])
        self.assertTrue(set(remote_properties(oidc)).isdisjoint(RUNTIME_FIELDS))
        self.assertEqual(load(COMPONENT / "runtime-externalsecret.yaml"), load(PENDING / "runtime-externalsecret.yaml"))
        self.assertEqual(load(COMPONENT / "role-externalsecret.yaml"), load(PENDING / "role-externalsecrets.yaml"))
        self.assertEqual(remote_properties(runtime), RUNTIME_FIELDS)
        self.assertEqual(sorted(runtime["spec"]["target"]["template"]["data"]), sorted(RUNTIME_FIELDS))
        self.assertEqual({item["remoteRef"]["key"] for item in runtime["spec"]["data"]}, {CANONICAL_ITEM})
        self.assertEqual({item["remoteRef"]["key"] for item in database["spec"]["data"]}, {CANONICAL_ITEM})
        self.assertEqual(remote_properties(database), DB_FIELDS)
        self.assertEqual(sorted(database["spec"]["target"]["template"]["data"]), ["password", "username"])
        self.assertEqual(database["spec"]["target"]["template"]["type"], "kubernetes.io/basic-auth")
        self.assertEqual(database["spec"]["target"]["template"]["metadata"]["labels"]["cnpg.io/reload"], "true")
        self.assertNotIn("SESSION_AEAD_KEY", remote_properties(database))
        self.assertNotIn("SESSION_GENERATION", remote_properties(database))
        historical = load(M5A)
        self.assertEqual(historical["metadata"]["namespace"], "dev-env-system")
        self.assertEqual(remote_properties(historical), OIDC_FIELDS)
        self.assertEqual({item["remoteRef"]["key"] for item in historical["spec"]["data"]}, {CANONICAL_ITEM})
        self.assertEqual(historical["spec"]["secretStoreRef"], STORE)

    def test_no_migration_credential_or_secret_material(self):
        self.assertNotIn("dev-env-v2-portal-migration", self.rendered)
        self.assertNotIn("BEGIN CERTIFICATE", self.rendered)
        self.assertNotIn("PRIVATE KEY", self.rendered)
        self.assertNotIn("dataFrom", self.rendered)
        for kind in FORBIDDEN_KINDS:
            self.assertEqual(by_kind(self.docs, kind), [])
        deployment = by_kind(self.docs, "Deployment")[0]
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertNotIn("env", container)
        self.assertNotIn("command", container)
        self.assertNotIn("args", container)

    def test_deployment_mounts_and_pending_runtime_image(self):
        deployment = by_kind(self.docs, "Deployment")[0]
        spec = deployment["spec"]
        self.assertEqual(spec["replicas"], 2)
        self.assertEqual(spec["strategy"]["rollingUpdate"], {"maxSurge": 1, "maxUnavailable": 0})
        pod = spec["template"]["spec"]
        self.assertEqual(pod["serviceAccountName"], "dev-env-v2-portal")
        self.assertIs(pod["automountServiceAccountToken"], False)
        self.assertEqual(pod["dnsPolicy"], "ClusterFirst")
        self.assertEqual(pod["dnsConfig"], {"options": [{"name": "ndots", "value": "1"}]})
        self.assertEqual(spec["template"]["metadata"]["labels"], {"app.kubernetes.io/name": "dev-env-v2-portal"})
        annotations = spec["template"]["metadata"]["annotations"]
        self.assertEqual(annotations["dev-env.haynesops.com/postgres-ca-sha256"], "UNVERIFIED")
        self.assertEqual(annotations["reloader.stakater.com/auto"], "true")
        self.assertEqual(annotations["dev-env.haynesops.com/oidc-population"], "user-reported-saved")
        self.assertEqual(
            annotations["configmap.reloader.stakater.com/reload"],
            "dev-env-v2-portal-config,dev-env-v2-portal-postgres-ca,dev-env-v2-portal-admission-ca,dev-env-v2-portal-issuer-ca,dev-env-v2-portal-openproject-ca,dev-env-v2-portal-operator-ca",
        )
        self.assertEqual(
            annotations["secret.reloader.stakater.com/reload"],
            "dev-env-v2-portal-oidc,dev-env-v2-portal-runtime,dev-env-v2-portal-tls",
        )
        self.assertEqual(pod["securityContext"]["runAsUser"], 65532)
        self.assertEqual(pod["securityContext"]["runAsGroup"], 65532)
        container = pod["containers"][0]
        self.assertEqual(container["image"], "unregistered.invalid/dev-env-v2-portal:UNPUBLISHED")
        self.assertNotIn("@sha256:", container["image"])
        self.assertNotIn("ghcr.io", container["image"])
        self.assertEqual(container["ports"], [{"name": "https", "containerPort": 8443, "protocol": "TCP"}])
        for probe, path in (("startupProbe", "/healthz"), ("livenessProbe", "/healthz"), ("readinessProbe", "/readyz")):
            http = container[probe]["httpGet"]
            self.assertEqual(http, {"path": path, "port": "https", "scheme": "HTTPS"})
            self.assertNotIn("httpHeaders", container[probe])
        mounts = {item["mountPath"]: item for item in container["volumeMounts"]}
        self.assertEqual(mounts["/etc/dev-env-portal/config"]["name"], "config")
        for field in OIDC_FIELDS:
            item = mounts["/etc/dev-env-portal/secrets/" + field]
            self.assertEqual(item["name"], "oidc")
            self.assertEqual(item["subPath"], field)
        for field in RUNTIME_FIELDS:
            item = mounts["/etc/dev-env-portal/secrets/" + field]
            self.assertEqual(item["name"], "runtime")
            self.assertEqual(item["subPath"], field)
        self.assertNotIn("/etc/dev-env-portal/secrets/PORTAL_SUBJECT", mounts)
        self.assertEqual(mounts["/etc/dev-env-portal/ca/ca.crt"]["subPath"], "ca.crt")
        self.assertEqual(mounts["/etc/dev-env-portal/ca/admission-ca.crt"]["subPath"], "admission-ca.crt")
        self.assertEqual(mounts["/etc/dev-env-portal/ca/issuer-ca.crt"]["subPath"], "issuer-ca.crt")
        self.assertEqual(mounts["/etc/dev-env-portal/ca/openproject-ca.crt"]["subPath"], "openproject-ca.crt")
        self.assertEqual(mounts["/etc/dev-env-portal/ca/operator-ca.crt"]["subPath"], "operator-ca.crt")
        self.assertEqual(mounts["/etc/dev-env-portal/tls/tls.crt"]["subPath"], "tls.crt")
        self.assertEqual(mounts["/etc/dev-env-portal/tls/tls.key"]["subPath"], "tls.key")
        self.assertNotIn("/etc/portal", self.rendered)
        self.assertNotIn("/portal/livez", self.rendered)
        volumes = {item["name"]: item for item in pod["volumes"]}
        self.assertEqual([item["key"] for item in volumes["oidc"]["secret"]["items"]], OIDC_FIELDS)
        self.assertEqual([item["key"] for item in volumes["runtime"]["secret"]["items"]], RUNTIME_FIELDS)
        self.assertEqual(volumes["postgres-ca"]["configMap"]["items"], [{"key": "ca.crt", "path": "ca.crt"}])
        self.assertEqual([item["key"] for item in volumes["serving-tls"]["secret"]["items"]], ["tls.crt", "tls.key"])
        projected = volumes["api"]["projected"]["sources"]
        self.assertEqual(projected[0]["serviceAccountToken"]["path"], "token")
        self.assertEqual(projected[0]["serviceAccountToken"]["expirationSeconds"], 3600)
        self.assertNotIn("audience", projected[0]["serviceAccountToken"])

    def test_public_ca_input_has_no_pem(self):
        configmaps = {doc["metadata"]["name"]: doc for doc in by_kind(self.docs, "ConfigMap")}
        for name in (
            "dev-env-v2-portal-postgres-ca",
            "dev-env-v2-portal-admission-ca",
            "dev-env-v2-portal-issuer-ca",
            "dev-env-v2-portal-openproject-ca",
            "dev-env-v2-portal-operator-ca",
        ):
            ca = configmaps[name]
            self.assertEqual(ca["data"], {"provenance": "absent"})
            self.assertNotIn("ca.crt", ca["data"])
            self.assertEqual(ca["metadata"]["annotations"]["dev-env.haynesops.com/ca-sha256"], "UNVERIFIED")
        config = configmaps["dev-env-v2-portal-config"]["data"]
        self.assertEqual(config, {
            "origin": "https://dev-env-v2.haynesops.com",
            "issuer": "https://authentik.haynesnetwork.com/application/o/dev-env-v2-portal/",
            "authorization-url": "https://authentik.haynesnetwork.com/application/o/authorize/",
            "token-url": "https://authentik.haynesnetwork.com/application/o/token/",
            "userinfo-url": "https://authentik.haynesnetwork.com/application/o/userinfo/",
            "jwks-url": "https://authentik.haynesnetwork.com/application/o/dev-env-v2-portal/jwks/",
            "openproject-base-url": "https://work.haynesnetwork.com",
            "operator-api-url": "https://dev-env-operator.dev-env-system.svc.cluster.local:8443",
            "admission-api-url": "https://kubernetes.default.svc.cluster.local",
            "admission-token-file": "/var/run/portal/api/token",
            "auth-style": "header",
            "login-ttl": "1m",
            "idle-ttl": "30m",
            "absolute-ttl": "1h",
            "fresh-for": "1m",
            "clock-skew": "0s",
            "witness-gets-per-second": "30",
            "witness-get-burst": "57",
        })
        self.assertNotIn("admission-uid", config)
        self.assertNotIn("subject", config)
        self.assertNotIn("session-bindings.json", config)
        self.assertNotIn("PENDING-CLOSED-BOOTSTRAP", self.rendered)
        self.assertNotIn("thaynes", "\n".join(config.values()))

    def test_internal_route_is_not_public(self):
        route = by_kind(self.docs, "IngressRoute")[0]
        self.assertEqual(route["metadata"]["namespace"], "dev")
        self.assertEqual(route["spec"]["entryPoints"], ["websecure"])
        self.assertEqual(route["spec"]["routes"][0]["match"], "Host(`dev-env-v2.haynesops.com`) && PathPrefix(`/`)")
        self.assertNotIn("middlewares", route["spec"]["routes"][0])
        service = route["spec"]["routes"][0]["services"][0]
        self.assertEqual(service["namespace"], "dev-agents")
        self.assertEqual(service["port"], 8443)
        self.assertEqual(service["scheme"], "https")
        self.assertIs(service["passHostHeader"], True)
        self.assertEqual(service["serversTransport"], "dev-env-v2-portal")
        self.assertEqual(route["spec"]["tls"], {"secretName": "certificate-haynesops"})
        self.assertEqual(route["metadata"]["annotations"]["external-dns.kubernetes.io/target"], "internal.haynesops")
        self.assertNotIn("cloudflare", self.rendered.lower())
        self.assertNotIn("traefik-external", self.rendered)
        transport = by_kind(self.docs, "ServersTransport")[0]
        self.assertEqual(transport["metadata"]["namespace"], "dev")
        self.assertIs(transport["spec"]["insecureSkipVerify"], False)
        self.assertEqual(transport["spec"]["serverName"], "dev-env-v2-portal.dev-agents.svc.cluster.local")
        self.assertEqual(transport["spec"]["rootCAsSecrets"], ["dev-env-v2-portal-serving-ca"])
        service_doc = by_kind(self.docs, "Service")[0]
        self.assertEqual(service_doc["spec"]["type"], "ClusterIP")
        self.assertEqual(service_doc["spec"]["ports"], [{"name": "https", "port": 8443, "protocol": "TCP", "targetPort": "https"}])

    def test_network_policy_matches_pending_and_broad_allows_remain(self):
        rendered = by_kind(self.docs, "CiliumNetworkPolicy")
        self.assertEqual(len(rendered), 1)
        pending = load(PENDING / "bff-networkpolicy.yaml")
        self.assertEqual(rendered[0]["spec"], pending["spec"])
        self.assertEqual(load(COMPONENT / "networkpolicy.yaml"), pending)
        self.assertEqual(by_kind(self.docs, "CiliumClusterwideNetworkPolicy"), [])
        exclusion = load(COMPONENT / "ccnp-exclusion.yaml")
        self.assertEqual(exclusion["append"], "dev-env-v2-portal")
        self.assertEqual(exclusion["operation"], "append-only")
        for relative in exclusion["targets"]:
            policy = load(ROOT / relative)
            expressions = policy["spec"]["endpointSelector"]["matchExpressions"]
            matched = [
                item for item in expressions
                if item.get("key") == "app.kubernetes.io/name" and item.get("operator") == "NotIn"
            ]
            self.assertEqual(len(matched), 1)
            self.assertNotIn("dev-env-v2-portal", matched[0]["values"])
            self.assertEqual(policy["spec"]["endpointSelector"]["matchLabels"]["io.kubernetes.pod.namespace"], "dev-agents")

    def test_live_tree_keeps_rejects_and_does_not_register_the_packet(self):
        cluster = load(CLUSTER)
        rejects = load(PENDING / "hba-stage-reject.yaml")["pg_hba"]
        self.assertEqual(len(rejects), 6)
        self.assertEqual(cluster["spec"]["postgresql"]["pg_hba"], rejects)
        self.assertNotIn("managed", cluster["spec"])
        for path in (ROOT / "kubernetes").rglob("*.yaml"):
            text = path.read_text(errors="replace")
            self.assertNotIn("portal178-m5d", text, str(path))
            self.assertNotIn("hostssl dev_env_v2_portal", text, str(path))
            if "kind: Deployment" in text and "name: dev-env-v2-portal" in text:
                self.fail("portal Deployment is registered in " + str(path))
            if "kind: IngressRoute" in text and "dev-env-v2.haynesops.com" in text:
                self.fail("portal route is registered in " + str(path))
            if "kind: ExternalSecret" in text and "dev-env-v2-portal" in text:
                self.fail("portal ExternalSecret is registered in " + str(path))
            if "kind: ConfigMap" in text and "name: dev-env-v2-portal-admission" in text:
                self.fail("CLOSED admission seed is registered in " + str(path))


    def test_historical_item_keys_and_unrelated_properties_are_rejected(self):
        rendered_secrets = by_kind(self.docs, "ExternalSecret")
        self.assertEqual(len(rendered_secrets), 3)
        for doc in rendered_secrets:
            self.assertEqual(projection_violations(doc, allowed_properties(doc)), [])
        mutated = deepcopy(rendered_secrets[0])
        mutated["spec"]["data"][0]["remoteRef"]["key"] = "dev-env-v2-portal"
        self.assertIn("dev-env-v2-portal", projection_violations(mutated, allowed_properties(mutated)))
        mutated = deepcopy(next(doc for doc in rendered_secrets if doc["metadata"]["name"] == "dev-env-v2-portal-runtime"))
        mutated["spec"]["data"].append({
            "secretKey": "CLAUDE_CODE_OAUTH_TOKEN",
            "remoteRef": {"key": CANONICAL_ITEM, "property": "CLAUDE_CODE_OAUTH_TOKEN"},
        })
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", projection_violations(mutated, set(RUNTIME_FIELDS)))
        mutated["spec"]["data"][-1] = {
            "secretKey": "PORTAL_SUBJECT",
            "remoteRef": {"key": CANONICAL_ITEM, "property": "PORTAL_SUBJECT"},
        }
        self.assertIn("PORTAL_SUBJECT", projection_violations(mutated, set(RUNTIME_FIELDS)))
        self.assertTrue(projection_violations({"spec": {"dataFrom": [{"extract": {"key": CANONICAL_ITEM}}], "data": []}}, PORTAL_PROPERTIES))
        occupied = dev_env_properties(ROOT / "kubernetes")
        self.assertTrue(occupied)
        self.assertTrue(PORTAL_PROPERTIES.isdisjoint(occupied))
        for path in [COMPONENT / "oidc-externalsecret.yaml", COMPONENT / "runtime-externalsecret.yaml",
                     COMPONENT / "role-externalsecret.yaml", PENDING / "runtime-externalsecret.yaml",
                     PENDING / "role-externalsecrets.yaml", M5A]:
            for ref_key in remote_keys(load(path)):
                self.assertEqual(ref_key, CANONICAL_ITEM, path)
                self.assertNotIn(ref_key, HISTORICAL_ITEMS)

    def test_contract_records_missing_runtime_and_attestation(self):
        contract = CONTRACT.read_text()
        checklist = CHECKLIST.read_text()
        self.assertNotIn("RUNTIME-CONTRACT.md: absent", contract)
        self.assertNotIn("RUNTIME-CONTRACT.md: absent", checklist)
        self.assertNotIn("is UNKNOWN", checklist)
        self.assertIn("USER-REPORTED SAVED", contract)
        self.assertIn("USER-REPORTED SAVED", checklist)
        self.assertIn("not SecretSynced", contract)
        self.assertIn("not value-verified", checklist)
        self.assertIn("/etc/dev-env-portal", contract)
        self.assertIn("/healthz", contract)
        self.assertIn("/readyz", contract)
        self.assertIn("Standard base64", contract)
        self.assertIn("dev_env_v2_portal_runtime", contract)
        self.assertIn("integration-fixes-02", contract)
        self.assertIn("config/subject", contract)
        self.assertIn(M6_HEAD, contract)
        self.assertIn("Historical record only; not loaded", contract)
        self.assertIn("HaynesKube/dev-env", contract)
        self.assertIn("canonical-item-01/RUNTIME-CONTRACT.md", contract)
        self.assertIn("Population of these properties is HELD", (PENDING / "role-externalsecrets.yaml").read_text())
        for field in ATTESTATION:
            self.assertIn(field, contract)
        if STABLE_CONTRACT.parent.is_dir():
            self.assertTrue(STABLE_CONTRACT.is_file(), "stable runtime contract is missing")
            stable = STABLE_CONTRACT.read_text()
            self.assertIn("/etc/dev-env-portal", stable)
            self.assertIn("/healthz", stable)
            self.assertIn("/readyz", stable)
            self.assertIn("https://authentik.haynesnetwork.com/application/o/authorize/", stable)
            self.assertIn("config/subject", stable)
            self.assertIn("USER-REPORTED SAVED", stable)
            self.assertIn("historical source", stable)


if __name__ == "__main__":
    unittest.main()
