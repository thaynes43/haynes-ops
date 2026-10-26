#!/usr/bin/env python3
"""Render the unregistered Story178 consumer packet and reject live registration."""
import re
import subprocess
import tempfile
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
PORTAL_OIDC = ROOT / "kubernetes/main/apps/dev-env-system/secrets/app/externalsecret-portal-oidc.yaml"
PORTAL_DB = ROOT / "kubernetes/main/apps/database/cloudnative-pg/cluster/portal-runtime-db-externalsecret.yaml"
CURRENT_RUNTIME_HEAD = "af402e533379bddd81e9266ee80ec6c565b81f29"
PORTAL_IMAGE = (
    "ghcr.io/thaynes43/dev-env-portal:sha-af402e5"
    "@sha256:71d3c5d64d1948a78acdc695337fb8edac542ec46eb81fcddaddafec30df0772"
)
OPENPROJECT_ORIGIN = "https://openproject-web.frontend.svc.cluster.local:8443"
OPENPROJECT_TASKS_QUERY = "/api/v3/projects/3/work_packages?offset=<page>&pageSize=50&filters=%5B%5D"
OPERATOR_IMAGE = (
    "sha-617ed15"
    "@sha256:888c2a0cd1057091cd2bbac7ac743933a4819e100c4570b6eb9b7579afddd34b"
)

RESOURCES = [
    "oidc-externalsecret.yaml",
    "openproject-externalsecret.yaml",
    "runtime-externalsecret.yaml",
    "certificates.yaml",
    "role-externalsecret.yaml",
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
    "Secret", "ClusterIssuer", "Role", "ClusterRole",
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
    if name.endswith("-openproject"):
        return {"OPENPROJECT_TOKEN"}
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


def approved_portal_oidc_violations(path, doc):
    """The one approved live portal consumer must be exact, not name-matched."""
    expected = {
        "apiVersion": "external-secrets.io/v1",
        "kind": "ExternalSecret",
        "metadata": {"name": "dev-env-v2-portal-oidc", "namespace": "dev-agents"},
        "spec": {
            "secretStoreRef": STORE,
            "refreshInterval": "1h",
            "target": {
                "name": "dev-env-v2-portal-oidc", "creationPolicy": "Owner", "deletionPolicy": "Delete",
                "template": {"engineVersion": "v2", "mergePolicy": "Replace", "type": "Opaque", "data": {
                    "OIDC_CLIENT_ID": "{{ .OIDC_CLIENT_ID }}",
                    "OIDC_CLIENT_SECRET": "{{ .OIDC_CLIENT_SECRET }}",
                }},
            },
            "data": [
                {"secretKey": "OIDC_CLIENT_ID", "remoteRef": {"key": CANONICAL_ITEM, "property": "OIDC_CLIENT_ID"}},
                {"secretKey": "OIDC_CLIENT_SECRET", "remoteRef": {"key": CANONICAL_ITEM, "property": "OIDC_CLIENT_SECRET"}},
            ],
        },
    }
    return [] if path.resolve() == PORTAL_OIDC.resolve() and doc == expected else ["noncanonical-portal-oidc"]



def approved_portal_db_violations(path, doc):
    """Only the reviewed two-field CNPG precursor may join live consumers."""
    expected = {
        "apiVersion": "external-secrets.io/v1", "kind": "ExternalSecret",
        "metadata": {"name": "dev-env-v2-portal-runtime-db", "namespace": "database"},
        "spec": {
            "secretStoreRef": STORE, "refreshInterval": "1h",
            "target": {
                "name": "dev-env-v2-portal-runtime-db", "creationPolicy": "Owner", "deletionPolicy": "Delete",
                "template": {
                    "engineVersion": "v2", "mergePolicy": "Replace",
                    "metadata": {"labels": {"cnpg.io/reload": "true"}},
                    "type": "kubernetes.io/basic-auth",
                    "data": {"username": "{{ .DB_USERNAME }}", "password": "{{ .DB_PASSWORD }}"},
                },
            },
            "data": [
                {"secretKey": "DB_USERNAME", "remoteRef": {"key": CANONICAL_ITEM, "property": "DB_USERNAME"}},
                {"secretKey": "DB_PASSWORD", "remoteRef": {"key": CANONICAL_ITEM, "property": "DB_PASSWORD"}},
            ],
        },
    }
    return [] if path.resolve() == PORTAL_DB.resolve() and doc == expected else ["noncanonical-portal-db"]


def approved_live_projection(path, doc):
    return not approved_portal_oidc_violations(path, doc) or not approved_portal_db_violations(path, doc)


def dev_env_properties_from_documents(documents):
    found = set()
    for path, doc in documents:
        if not doc or doc.get("kind") != "ExternalSecret":
            continue
        if approved_live_projection(path, doc):
            continue
        for item in doc.get("spec", {}).get("data", []):
            ref = item.get("remoteRef", {})
            if ref.get("key") == CANONICAL_ITEM:
                found.add(ref.get("property"))
    return found


def dev_env_properties(tree):
    inline = re.compile(r"key:\s*dev-env(?![A-Za-z0-9_-])\s*,\s*property:\s*([A-Za-z0-9_]+)")
    block = re.compile(r"key:\s*dev-env(?![A-Za-z0-9_-])\s*\n\s*property:\s*([A-Za-z0-9_]+)")
    found = set()
    for path in tree.rglob("*.yaml"):
        text = path.read_text(errors="replace")
        if path.resolve() in {PORTAL_OIDC.resolve(), PORTAL_DB.resolve()}:
            documents = list(yaml.safe_load_all(text))
            if len(documents) == 1 and approved_live_projection(path, documents[0]):
                continue
        found.update(inline.findall(text))
        found.update(block.findall(text))
    return found


def load(path):
    return yaml.safe_load(path.read_text())


def render():
    chunks = []
    for path in (COMPONENT, COMPONENT / "policy"):
        completed = subprocess.run(
            ["kustomize", "build", str(path)],
            check=True, capture_output=True, text=True,
        )
        chunks.append(completed.stdout)
    rendered = "\n---\n".join(chunks)
    docs = [doc for doc in yaml.safe_load_all(rendered) if doc]
    return rendered, docs


def portal_pod_creation_allowed(policy_ready, workload_depends_on_policy):
    """A portal pod is allowed only after the dedicated policy stage is ready."""
    return policy_ready is True and workload_depends_on_policy is True


def same_apply_proves_isolation():
    """One kustomize build that contains both objects does not prove isolation."""
    return False


def by_kind(docs, kind):
    return [doc for doc in docs if doc.get("kind") == kind]


def remote_properties(doc):
    return [item["remoteRef"]["property"] for item in doc["spec"]["data"]]


def metadata_dns_rule(pending_rule):
    """Complete CoreDNS rule: the pending rule plus the two fixed metadata names."""
    rule = deepcopy(pending_rule)
    rule["toPorts"][0]["rules"]["dns"].extend([
        {"matchName": "openproject-web.frontend.svc.cluster.local"},
        {"matchName": "dev-env-operator.dev-env-system.svc.cluster.local"},
    ])
    return rule


def dns_policy_violation(spec, pending_spec):
    """Reject any CoreDNS rule that is not the one minimal port-53 rule.

    Equality covers port, protocol, selector, and DNS names. A later egress
    rule that also selects kube-system or kube-dns is separate breadth.
    """
    expected = metadata_dns_rule(pending_spec["egress"][0])
    if spec["egress"][0] != expected:
        return "coredns rule differs from the complete minimal rule"
    for rule in spec["egress"][1:]:
        for endpoint in rule.get("toEndpoints", []):
            labels = endpoint.get("matchLabels", {})
            if labels.get("k8s-app") == "kube-dns" or labels.get("io.kubernetes.pod.namespace") == "kube-system":
                return "extra coredns egress rule"
    return None


def replacement_allowed(certificate_ready, dependency_recorded):
    """A missing serving certificate must not replace the running OpenProject web pod."""
    return bool(certificate_ready) and bool(dependency_recorded)


def openproject_egress_violation(spec):
    """The portal may reach only the OpenProject web pods on TCP 8443."""
    found = False
    for rule in spec["egress"]:
        ports = {
            (item["port"], item["protocol"])
            for block in rule.get("toPorts", [])
            for item in block["ports"]
        }
        if ("8081", "TCP") in ports or ("8082", "TCP") in ports:
            return "plaintext-openproject"
        for endpoint in rule.get("toEndpoints", []):
            labels = endpoint.get("matchLabels", {})
            if labels.get("io.kubernetes.pod.namespace") != "frontend":
                continue
            if labels != {
                "io.kubernetes.pod.namespace": "frontend",
                "app.kubernetes.io/instance": "openproject",
                "app.kubernetes.io/controller": "web",
            } or ports != {("8443", "TCP")}:
                return "broad-frontend"
            found = True
    if not found:
        return "missing-openproject"
    return None


def unqualified_transport_lookup(route):
    """Traefik v3.7.16 makeServersTransportKey for a name without '@'.

    The parent namespace is the Service namespace when the reference sets one,
    otherwise the IngressRoute namespace. The returned pair is the object
    Traefik indexes with nameBuilder.makeID.
    """
    service = route["spec"]["routes"][0]["services"][0]
    name = service["serversTransport"]
    if "@" in name:
        raise AssertionError("qualified ServersTransport names are a different resolver path")
    parent = service.get("namespace") or route["metadata"]["namespace"]
    return parent, name


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
        # certificates.yaml is four objects in one resource file.
        # The network policy is a separate kustomize root and adds one object.
        self.assertEqual(len(self.docs), len(RESOURCES) + 4)

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
                ("dev-agents", "dev-env-v2-portal-openproject"),
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
        self.assertEqual(
            sorted(doc["metadata"]["name"] for doc in by_kind(self.docs, "Certificate")),
            ["dev-env-v2-portal-ca", "dev-env-v2-portal-serving"],
        )
        self.assertEqual(
            sorted(doc["metadata"]["name"] for doc in by_kind(self.docs, "Issuer")),
            ["dev-env-v2-portal-ca", "dev-env-v2-portal-selfsigned"],
        )
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
            "dev-env-v2-portal-oidc,dev-env-v2-portal-runtime,dev-env-v2-portal-openproject,dev-env-v2-portal-tls",
        )
        self.assertEqual(pod["securityContext"]["runAsUser"], 65532)
        self.assertEqual(pod["securityContext"]["runAsGroup"], 65532)
        container = pod["containers"][0]
        self.assertEqual(container["image"], PORTAL_IMAGE)
        self.assertNotIn("UNPUBLISHED", container["image"])
        self.assertEqual(container["ports"], [{"name": "https", "containerPort": 8443, "protocol": "TCP"}])
        self.assertEqual(container["resources"]["requests"]["cpu"], "50m")
        self.assertEqual(container["resources"]["limits"]["cpu"], "200m")
        self.assertEqual(container["resources"]["limits"]["memory"], "256Mi")
        for probe, path in (("startupProbe", "/healthz"), ("livenessProbe", "/healthz"), ("readinessProbe", "/readyz")):
            http = container[probe]["httpGet"]
            self.assertEqual(http["path"], path)
            self.assertEqual(http["port"], "https")
            self.assertEqual(http["scheme"], "HTTPS")
            self.assertNotIn("host", http)
            self.assertEqual(http["httpHeaders"], [{"name": "Host", "value": "dev-env-v2.haynesops.com"}])
        mounts = {item["mountPath"]: item for item in container["volumeMounts"]}
        self.assertEqual(mounts["/etc/dev-env-portal/config"]["name"], "config")
        secrets_mount = mounts["/etc/dev-env-portal/secrets"]
        self.assertEqual(secrets_mount["name"], "secrets")
        self.assertNotIn("subPath", secrets_mount)
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
        sources = volumes["secrets"]["projected"]["sources"]
        by_secret = {item["secret"]["name"]: item["secret"] for item in sources if "secret" in item}
        self.assertEqual([item["key"] for item in by_secret["dev-env-v2-portal-oidc"]["items"]], OIDC_FIELDS)
        self.assertNotIn("optional", by_secret["dev-env-v2-portal-oidc"])
        self.assertEqual([item["key"] for item in by_secret["dev-env-v2-portal-runtime"]["items"]], RUNTIME_FIELDS)
        self.assertNotIn("optional", by_secret["dev-env-v2-portal-runtime"])
        self.assertIs(by_secret["dev-env-v2-portal-openproject"]["optional"], True)
        self.assertEqual(by_secret["dev-env-v2-portal-openproject"]["items"], [{"key": "OPENPROJECT_TOKEN", "path": "OPENPROJECT_TOKEN"}])
        operator_token = next(item["serviceAccountToken"] for item in sources if "serviceAccountToken" in item)
        self.assertEqual(operator_token["path"], "OPERATOR_TOKEN")
        self.assertEqual(operator_token["audience"], "dev-env-operator")
        self.assertEqual(operator_token["expirationSeconds"], 3600)
        self.assertEqual(volumes["postgres-ca"]["configMap"]["items"], [{"key": "ca.crt", "path": "ca.crt"}])
        self.assertEqual([item["key"] for item in volumes["serving-tls"]["secret"]["items"]], ["tls.crt", "tls.key"])
        mounted_secrets = [item["secret"]["secretName"] for item in pod["volumes"] if "secret" in item]
        self.assertNotIn("dev-env-v2-portal-ca", mounted_secrets)
        self.assertNotIn("openproject-portal-ca", mounted_secrets)
        api_mount = mounts["/var/run/portal/api"]
        self.assertNotIn("subPath", api_mount)
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
            "openproject-base-url": OPENPROJECT_ORIGIN,
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
        lookup_ns, lookup_name = unqualified_transport_lookup(route)
        matched = [
            doc for doc in by_kind(self.docs, "ServersTransport")
            if doc["metadata"]["namespace"] == lookup_ns and doc["metadata"]["name"] == lookup_name
        ]
        self.assertEqual(len(matched), 1)
        transport = matched[0]
        self.assertEqual(route["metadata"]["namespace"], "dev")
        self.assertNotEqual(transport["metadata"]["namespace"], route["metadata"]["namespace"])
        contract = CONTRACT.read_text()
        self.assertIn(lookup_ns + "/dev-env-v2-portal-serving-ca", contract)
        self.assertIs(transport["spec"]["insecureSkipVerify"], False)
        self.assertEqual(transport["spec"]["serverName"], "dev-env-v2-portal.dev-agents.svc.cluster.local")
        self.assertNotIn("rootCAsSecrets", transport["spec"])
        self.assertEqual(transport["spec"]["rootCAs"], [{"secret": "dev-env-v2-portal-serving-ca"}])
        service_doc = by_kind(self.docs, "Service")[0]
        self.assertEqual(service_doc["spec"]["type"], "ClusterIP")
        self.assertEqual(service_doc["spec"]["ports"], [{"name": "https", "port": 8443, "protocol": "TCP", "targetPort": "https"}])

    def test_network_policy_matches_pending_and_broad_allows_remain(self):
        rendered = by_kind(self.docs, "CiliumNetworkPolicy")
        self.assertEqual(len(rendered), 1)
        spec = rendered[0]["spec"]
        pending = load(PENDING / "bff-networkpolicy.yaml")
        pending_spec = pending["spec"]
        self.assertEqual(spec["endpointSelector"], {"matchLabels": {"app.kubernetes.io/name": "dev-env-v2-portal"}})
        self.assertEqual(spec["enableDefaultDeny"], {"ingress": True, "egress": True})
        self.assertEqual(spec["ingress"], pending_spec["ingress"])
        self.assertEqual(pending_spec["egress"][0], {
            "toEndpoints": [{"matchLabels": {
                "io.kubernetes.pod.namespace": "kube-system",
                "k8s-app": "kube-dns",
            }}],
            "toPorts": [{
                "ports": [{"port": "53", "protocol": "ANY"}],
                "rules": {"dns": [
                    {"matchName": "authentik.haynesnetwork.com"},
                    {"matchName": "postgres16-rw.database.svc.cluster.local"},
                    {"matchName": "kubernetes.default.svc.cluster.local"},
                ]},
            }],
        })
        self.assertIsNone(dns_policy_violation(spec, pending_spec))
        self.assertEqual(spec["egress"][0], metadata_dns_rule(pending_spec["egress"][0]))
        broadened = deepcopy(spec)
        broadened["egress"][0]["toPorts"].append({"ports": [{"port": "8080", "protocol": "TCP"}]})
        self.assertEqual(dns_policy_violation(broadened, pending_spec), "coredns rule differs from the complete minimal rule")
        broadened = deepcopy(spec)
        broadened["egress"][0]["toPorts"][0]["ports"].append({"port": "53", "protocol": "UDP"})
        self.assertIsNotNone(dns_policy_violation(broadened, pending_spec))
        broadened = deepcopy(spec)
        del broadened["egress"][0]["toEndpoints"][0]["matchLabels"]["k8s-app"]
        self.assertIsNotNone(dns_policy_violation(broadened, pending_spec))
        broadened = deepcopy(spec)
        broadened["egress"].append({
            "toEndpoints": [{"matchLabels": {
                "io.kubernetes.pod.namespace": "kube-system",
                "k8s-app": "kube-dns",
            }}],
            "toPorts": [{"ports": [{"port": "8080", "protocol": "TCP"}]}],
        })
        self.assertEqual(dns_policy_violation(broadened, pending_spec), "extra coredns egress rule")
        self.assertEqual(spec["egress"][1], pending_spec["egress"][1])
        self.assertEqual(spec["egress"][2], pending_spec["egress"][2])
        self.assertEqual(spec["egress"][3], pending_spec["egress"][3])
        self.assertEqual(spec["egress"][4], {
            "toEndpoints": [{"matchLabels": {
                "io.kubernetes.pod.namespace": "frontend",
                "app.kubernetes.io/instance": "openproject",
                "app.kubernetes.io/controller": "web",
            }}],
            "toPorts": [{"ports": [{"port": "8443", "protocol": "TCP"}]}],
        })
        operator_rule = spec["egress"][5]
        self.assertEqual(operator_rule, {
            "toEndpoints": [{"matchLabels": {
                "io.kubernetes.pod.namespace": "dev-env-system",
                "app.kubernetes.io/name": "dev-env-operator",
            }}],
            "toPorts": [{"ports": [{"port": "8443", "protocol": "TCP"}]}],
        })
        self.assertEqual(len(spec["egress"]), 6)
        policy_text = (COMPONENT / "policy/networkpolicy.yaml").read_text()
        self.assertNotIn("work.haynesnetwork.com", policy_text)
        self.assertNotIn("matchPattern", policy_text)
        for rule in spec["egress"]:
            for endpoint in rule.get("toEndpoints", []):
                labels = endpoint.get("matchLabels", {})
                if labels.get("io.kubernetes.pod.namespace") == "dev-env-system":
                    self.assertEqual(set(labels), {"io.kubernetes.pod.namespace", "app.kubernetes.io/name"})
                    self.assertEqual(labels["app.kubernetes.io/name"], "dev-env-operator")
        operator_policy = load(ROOT / "kubernetes/main/apps/dev-env-system/network/app/operator.yaml")
        self.assertEqual(
            operator_policy["spec"]["endpointSelector"]["matchLabels"],
            {"app.kubernetes.io/name": "dev-env-operator"},
        )
        self.assertEqual(operator_policy["metadata"]["namespace"], "dev-env-system")
        release = load(ROOT / "kubernetes/main/apps/dev-env-system/operator/app/helmrelease.yaml")
        api = release["spec"]["values"]["service"]["api"]
        self.assertEqual(api["controller"], "dev-env-operator")
        self.assertEqual(api["ports"]["https"]["port"], 8443)
        operator = release["spec"]["values"]["controllers"]["dev-env-operator"]["containers"]["app"]
        self.assertEqual(operator["image"]["tag"], OPERATOR_IMAGE)
        self.assertNotIn("--enable-portal-reader", operator["args"])
        self.assertNotIn("--session-namespace=dev-agents", operator["args"])
        self.assertNotEqual(spec, pending_spec)
        for doc in by_kind(self.docs, "ExternalSecret"):
            self.assertNotIn("OPERATOR_TOKEN", remote_properties(doc))
            if doc["metadata"]["name"] == "dev-env-v2-portal-openproject":
                self.assertEqual(remote_properties(doc), ["OPENPROJECT_TOKEN"])
            else:
                self.assertNotIn("OPENPROJECT_TOKEN", remote_properties(doc))
        config = by_kind(self.docs, "ConfigMap")
        config_data = next(item["data"] for item in config if item["metadata"]["name"] == "dev-env-v2-portal-config")
        self.assertNotIn("session-bindings.json", config_data)
        contract = CONTRACT.read_text()
        self.assertIn("config/session-bindings.json` is not a production requirement", contract)
        self.assertIn("Sessions and Tasks are independent", contract)
        self.assertIn(CURRENT_RUNTIME_HEAD, contract)
        self.assertIn("--enable-portal-reader", contract)
        self.assertIn(OPENPROJECT_TASKS_QUERY, contract)
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
            self.assertEqual(matched[0]["values"][-1], "dev-env-v2-portal")
            self.assertEqual(matched[0]["values"].count("dev-env-v2-portal"), 1)
            self.assertEqual(policy["spec"]["endpointSelector"]["matchLabels"]["io.kubernetes.pod.namespace"], "dev-agents")
        web = load(ROOT / "kubernetes/main/apps/dev-env-system/network/app/session-web.yaml")
        egress_notin = [
            item for item in web["spec"]["endpointSelector"]["matchExpressions"]
            if item.get("key") == "dev-env.haynesops.com/egress"
        ]
        self.assertEqual(egress_notin, [{"key": "dev-env.haynesops.com/egress", "operator": "NotIn", "values": ["ops"]}])

    def test_live_tree_keeps_rejects_and_does_not_register_the_packet(self):
        cluster = load(CLUSTER)
        rejects = load(PENDING / "hba-stage-reject.yaml")["pg_hba"]
        self.assertEqual(len(rejects), 6)
        allow = yaml.safe_load((PENDING / "hba-stage-allow.yaml").read_text())["rule"]
        self.assertEqual(cluster["spec"]["postgresql"]["pg_hba"], rejects[:4] + [allow] + rejects[4:])
        self.assertEqual(cluster["spec"]["managed"], load(PENDING / "roles-stage-preproof.patch.yaml")["spec"]["managed"])
        self.assertEqual(load(CLUSTER.parent / "portal-database.yaml"), load(PENDING / "database.yaml"))
        database_kinds = []
        for path in (ROOT / "kubernetes").rglob("*.yaml"):
            text = path.read_text(errors="replace")
            if "kind: Database" in text:
                database_kinds += [(path.resolve(), doc["kind"]) for doc in yaml.safe_load_all(text)
                                   if doc and doc.get("kind") in {"Database", "DatabaseRole"}]
            self.assertNotIn("portal178-m5d", text, str(path))
            if path.resolve() == CLUSTER.resolve():
                self.assertEqual(text.count("hostssl dev_env_v2_portal"), 1)
            else:
                self.assertNotIn("hostssl dev_env_v2_portal", text, str(path))
            if "kind: Deployment" in text and "name: dev-env-v2-portal" in text:
                self.fail("portal Deployment is registered in " + str(path))
            if "kind: IngressRoute" in text and "dev-env-v2.haynesops.com" in text:
                self.fail("portal route is registered in " + str(path))
            if "kind: ExternalSecret" in text:
                for doc in yaml.safe_load_all(text):
                    if doc and doc.get("kind") == "ExternalSecret" and "dev-env-v2-portal" in doc.get("metadata", {}).get("name", ""):
                        self.assertTrue(approved_live_projection(path, doc), str(path))
                        self.assertIn(path.resolve(), {PORTAL_OIDC.resolve(), PORTAL_DB.resolve()})
            if "kind: ConfigMap" in text and "name: dev-env-v2-portal-admission" in text:
                self.fail("CLOSED admission seed is registered in " + str(path))
        self.assertEqual(database_kinds, [((CLUSTER.parent / "portal-database.yaml").resolve(), "Database")])


    def test_registered_db_projection_is_exact_and_keeps_other_properties_closed(self):
        approved = load(PORTAL_DB)
        self.assertEqual(approved_portal_db_violations(PORTAL_DB, approved), [])
        self.assertEqual(approved["spec"], load(PENDING / "role-externalsecrets.yaml")["spec"])
        manifest = load(PORTAL_DB.parent / "kustomization.yaml")
        self.assertEqual(manifest["resources"].count("./portal-runtime-db-externalsecret.yaml"), 1)
        self.assertEqual(dev_env_properties_from_documents([(PORTAL_DB, approved)]), set())
        for mutate in (
            lambda doc: doc["spec"]["data"][0]["remoteRef"].update(key="wrong-item"),
            lambda doc: doc["spec"]["data"][0]["remoteRef"].update(property="SESSION_AEAD_KEY"),
            lambda doc: doc["spec"]["data"].append({"secretKey": "EXTRA", "remoteRef": {"key": CANONICAL_ITEM, "property": "EXTRA"}}),
            lambda doc: doc["spec"].update(dataFrom=[{"extract": {"key": CANONICAL_ITEM}}]),
            lambda doc: doc["metadata"].update(namespace="dev-agents"),
            lambda doc: doc["spec"]["target"].update(name="another-recipient"),
            lambda doc: doc["spec"]["target"]["template"]["data"].update(username="{{ .SESSION_AEAD_KEY }}"),
        ):
            invalid = deepcopy(approved)
            mutate(invalid)
            self.assertTrue(approved_portal_db_violations(PORTAL_DB, invalid))
        wrong_path = PORTAL_DB.with_name("another-consumer.yaml")
        self.assertTrue(approved_portal_db_violations(wrong_path, approved))
        self.assertEqual(dev_env_properties_from_documents([(wrong_path, approved)]), set(DB_FIELDS))

    def test_historical_item_keys_and_unrelated_properties_are_rejected(self):
        rendered_secrets = by_kind(self.docs, "ExternalSecret")
        self.assertEqual(len(rendered_secrets), 4)
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
        approved = load(PORTAL_OIDC)
        self.assertEqual(approved_portal_oidc_violations(PORTAL_OIDC, approved), [])
        for mutate in (
            lambda doc: doc["spec"]["data"].append({"secretKey": "EXTRA", "remoteRef": {"key": CANONICAL_ITEM, "property": "EXTRA"}}),
            lambda doc: doc["spec"]["data"][0]["remoteRef"].update(key="wrong-item"),
            lambda doc: doc["spec"].update(dataFrom=[{"extract": {"key": CANONICAL_ITEM}}]),
        ):
            invalid = deepcopy(approved)
            mutate(invalid)
            self.assertTrue(approved_portal_oidc_violations(PORTAL_OIDC, invalid))
        self.assertTrue(approved_portal_oidc_violations(
            ROOT / "kubernetes/main/apps/dev-env-system/secrets/app/another-portal-oidc.yaml", approved))
        occupied = dev_env_properties(ROOT / "kubernetes")
        self.assertTrue(occupied)
        self.assertTrue(set(OIDC_FIELDS).isdisjoint(occupied))
        self.assertTrue(set(RUNTIME_FIELDS).isdisjoint(occupied))
        collision = deepcopy(approved)
        collision["metadata"]["name"] = "unrelated-consumer"
        self.assertEqual(approved_portal_oidc_violations(PORTAL_OIDC, collision), ["noncanonical-portal-oidc"])
        self.assertEqual(dev_env_properties_from_documents([(PORTAL_OIDC, collision)]), set(OIDC_FIELDS))
        collision_path = ROOT / "kubernetes/main/apps/example/app/externalsecret.yaml"
        self.assertTrue(approved_portal_oidc_violations(collision_path, approved))
        self.assertEqual(dev_env_properties_from_documents([(collision_path, approved)]), set(OIDC_FIELDS))
        with tempfile.TemporaryDirectory() as scratch:
            inline = Path(scratch) / "inline"
            inline.mkdir()
            (inline / "nested.yaml").write_text("""\
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
spec:
  values:
    rawResources:
      - apiVersion: external-secrets.io/v1
        kind: ExternalSecret
        spec:
          data:
            - remoteRef: {key: dev-env, property: DB_PASSWORD}
""")
            block = Path(scratch) / "block"
            block.mkdir()
            (block / "nested.yaml").write_text("""\
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
spec:
  values:
    rawResources:
      - apiVersion: external-secrets.io/v1
        kind: ExternalSecret
        spec:
          data:
            - remoteRef:
                key: dev-env
                property: DB_PASSWORD
""")
            self.assertEqual(dev_env_properties(inline), {"DB_PASSWORD"})
            self.assertEqual(dev_env_properties(block), {"DB_PASSWORD"})
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
        self.assertIn(CURRENT_RUNTIME_HEAD, contract)
        self.assertIn("config/subject", contract)
        self.assertIn(PORTAL_IMAGE, contract)
        self.assertIn(OPENPROJECT_ORIGIN, contract)
        self.assertIn(OPENPROJECT_TASKS_QUERY, contract)
        self.assertIn("dev-env-operator.dev-env-system.svc.cluster.local", contract)
        self.assertIn("Sessions and Tasks are independent", contract)
        self.assertIn("Tasks not connected", contract)
        self.assertIn("fixed `dev-agents` namespace", contract)
        self.assertIn(OPERATOR_IMAGE, checklist)
        self.assertIn("no explicit `--enable-portal-reader`", checklist)
        self.assertIn("provider 174", contract)
        self.assertIn("not materialized", contract)
        self.assertIn("historical handoff convention", contract)
        self.assertIn("not a 1Password technical limitation", contract)
        self.assertIn("Tom's secure UI", contract)
        self.assertIn("No approved automated writer", contract)
        self.assertNotIn("6a522e843a8768099d8eb8086f9ff8272f94b725", contract)
        self.assertNotIn("Probe API cannot", contract)
        self.assertNotIn("no ExternalSecret selects", contract)
        self.assertIn("selected in inert source", contract)
        self.assertIn("httpHeaders", contract)
        self.assertIn("200m", contract)
        self.assertIn("tls.ca", contract)
        self.assertIn("dev-env-v2-portal.dev-agents.svc.cluster.local", contract)
        self.assertIn("Historical record only; not loaded", contract)
        self.assertIn("HaynesKube/dev-env", contract)
        self.assertIn("Population of these properties is HELD", (PENDING / "role-externalsecrets.yaml").read_text())
        for field in ATTESTATION:
            self.assertIn(field, contract)

    def test_approved_reader_route_certificates_and_preflight(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "openproject_reader_preflight",
            ROOT / "scripts/portal178-m5d/openproject_reader_preflight.py",
        )
        preflight = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(preflight)
        with self.assertRaises(preflight.PreflightReject) as empty:
            preflight.evaluate({})
        self.assertEqual(empty.exception.reason, "missing-evidence")
        absent = preflight.absence_snapshot()
        self.assertEqual(preflight.evaluate(absent)["outcome"], "synthetic-absent-ready")
        self.assertIs(preflight.evaluate(absent)["live_permission_proof"], False)
        self.assertEqual(preflight.evaluate(absent)["collector"], "not-implemented")
        ready = preflight.matching_snapshot()
        self.assertEqual(preflight.evaluate(ready)["outcome"], "synthetic-idempotent")
        omitted_admin = deepcopy(ready)
        del omitted_admin["users"][0]["admin"]
        omitted_groups = deepcopy(ready)
        del omitted_groups["users"][0]["groups"]
        omitted_global = deepcopy(ready)
        del omitted_global["users"][0]["global_roles"]
        omitted_inherited = deepcopy(absent)
        del omitted_inherited["inherited_memberships"]
        for incomplete in (omitted_admin, omitted_groups, omitted_global, omitted_inherited):
            with self.assertRaises(preflight.PreflightReject) as missing:
                preflight.evaluate(incomplete)
            self.assertEqual(missing.exception.reason, "missing-evidence")
        inherited_list = deepcopy(absent)
        inherited_list["inherited_memberships"] = [{"project_id": 3}]
        with self.assertRaises(preflight.PreflightReject) as inherited_top:
            preflight.evaluate(inherited_list)
        self.assertEqual(inherited_top.exception.reason, "inherited-rights")
        mismatched = deepcopy(ready)
        mismatched["users"][0]["memberships"].append({
            "project_id": 8, "roles": ["Dev-env portal reader"], "inherited": False, "group": None,
        })
        with self.assertRaises(preflight.PreflightReject) as raised:
            preflight.evaluate(mismatched)
        self.assertEqual(raised.exception.reason, "membership-scope")
        typed = deepcopy(ready)
        typed["users"][0]["admin"] = "false"
        with self.assertRaises(preflight.PreflightReject) as typed_reject:
            preflight.evaluate(typed)
        self.assertEqual(typed_reject.exception.reason, "wrong-type")
        duplicated = deepcopy(ready)
        duplicated["roles"].append(deepcopy(ready["roles"][0]))
        with self.assertRaises(preflight.PreflightReject) as duplicate_reject:
            preflight.evaluate(duplicated)
        self.assertEqual(duplicate_reject.exception.reason, "duplicate")
        unknown = deepcopy(absent)
        unknown["extra"] = []
        with self.assertRaises(preflight.PreflightReject) as unknown_reject:
            preflight.evaluate(unknown)
        self.assertEqual(unknown_reject.exception.reason, "unknown-scope")
        with self.assertRaises(preflight.PreflightReject):
            preflight.evaluate({**absent, "token": "nope"})

        import json
        fixture = json.loads((ROOT / "scripts/portal178-m5d/openproject_query_contract.json").read_text())
        template = (ROOT / "kubernetes/main/apps/frontend/openproject/app/nginx.conf.template").read_text()
        portal_server = template.split("listen 8443 ssl;", 1)[1]
        pattern = re.search(r'\$args !~ "(\^.*\$)"', portal_server).group(1)
        def query_ok(args):
            return re.fullmatch(pattern, args) is not None
        self.assertEqual(fixture["canonical"], "offset=1&pageSize=50&filters=%5B%5D")
        self.assertIs(fixture["offsetIsPageNumber"], True)
        for good in fixture["accepted"]:
            self.assertTrue(query_ok(good), good)
        for bad in fixture["rejected"]:
            self.assertFalse(query_ok(bad), bad)
        self.assertNotIn("location = /api/v3/projects/8/work_packages", portal_server)
        self.assertNotIn("location = /api/v3/work_packages/", portal_server)
        self.assertIn("if ($request_method != GET)", portal_server)
        inherited = deepcopy(ready)
        inherited["users"][0]["memberships"][0]["inherited"] = True
        with self.assertRaises(preflight.PreflightReject) as inherited_reject:
            preflight.plan(inherited)
        self.assertEqual(inherited_reject.exception.reason, "inherited-rights")
        created = preflight.plan(absent)
        self.assertIs(created["executes"], False)
        self.assertIs(created["live_permission_proof"], False)
        self.assertEqual(created["collector"], "not-implemented")
        self.assertIn("mint-reader-api-token", [item["action"] for item in created["steps"]])
        self.assertIn("create-role", [item["action"] for item in created["steps"]])
        keys = set()
        def collect(node):
            if isinstance(node, dict):
                keys.update(node)
                for value in node.values():
                    collect(value)
            elif isinstance(node, list):
                for value in node:
                    collect(value)
        collect(created)
        self.assertTrue({"token", "password", "secret", "value"}.isdisjoint(keys))
        self.assertIs(created["steps"][-1]["executes"], False)
        self.assertEqual(created["steps"][-1]["record_property"], "OPENPROJECT_TOKEN")
        self.assertIn("openproject-agent-token", created["steps"][-1]["rejected"])
        policy_ks = load(COMPONENT / "registration/policy-ks.yaml")
        workload_ks = load(COMPONENT / "registration/workload-ks.yaml")
        self.assertEqual(policy_ks["spec"]["path"], ".agents/reports/portal178-m5d/component/policy")
        self.assertEqual(workload_ks["spec"]["dependsOn"], [{"name": "dev-env-v2-portal-policy", "namespace": "dev-agents"}])
        self.assertNotEqual(policy_ks["spec"]["path"], workload_ks["spec"]["path"])
        self.assertEqual(load(COMPONENT / "policy/kustomization.yaml")["resources"], ["networkpolicy.yaml"])
        self.assertNotIn("networkpolicy.yaml", load(COMPONENT / "kustomization.yaml")["resources"])
        self.assertFalse(portal_pod_creation_allowed(False, True))
        self.assertTrue(portal_pod_creation_allowed(True, True))
        self.assertFalse(same_apply_proves_isolation())
        # S1 registers the policy alone, verbatim, in its own Kustomization.
        registered_dir = ROOT / "kubernetes/main/apps/dev-env-system/portal/policy"
        registered_ks = load(registered_dir / "ks.yaml")
        self.assertEqual(registered_ks["metadata"], {"name": "dev-env-v2-portal-policy", "namespace": "dev-env-system"})
        self.assertEqual(registered_ks["spec"]["targetNamespace"], "dev-agents")
        self.assertEqual(registered_ks["spec"]["path"], "./kubernetes/main/apps/dev-env-system/portal/policy/app")
        self.assertNotIn("dependsOn", registered_ks["spec"])
        self.assertEqual(load(registered_dir / "app/kustomization.yaml")["resources"], ["./networkpolicy.yaml"])
        self.assertEqual(load(registered_dir / "app/networkpolicy.yaml"), load(COMPONENT / "policy/networkpolicy.yaml"))
        system = load(ROOT / "kubernetes/main/apps/dev-env-system/kustomization.yaml")["resources"]
        self.assertEqual(system.count("./portal/policy/ks.yaml"), 1)
        for path in (ROOT / "kubernetes").rglob("*.yaml"):
            text = path.read_text(errors="replace")
            if path.resolve() != (registered_dir / "ks.yaml").resolve():
                self.assertNotIn("dev-env-v2-portal-policy", text, str(path))
        for path in (ROOT / "kubernetes").rglob("*.json"):
            text = path.read_text(errors="replace")
            self.assertNotIn("dev-env-v2-portal-policy", text, str(path))
        for path in (ROOT / "kubernetes").rglob("*.yaml"):
            text = path.read_text(errors="replace")
            if "dependsOn" in text:
                self.assertNotIn("name: dev-env-v2-portal-policy", text, str(path))
        self.assertIn("location = /api/v3/projects/3/work_packages", portal_server)
        self.assertIn('proxy_pass http://127.0.0.1:8080;', portal_server)
        self.assertIn('proxy_set_header Cookie "";', portal_server)
        self.assertIn('proxy_set_header Authorization $http_authorization;', portal_server)
        self.assertIn("listen 0.0.0.0:8081;", template)
        self.assertIn("listen 0.0.0.0:8082;", template)

        policies = list(yaml.safe_load_all((ROOT / "kubernetes/main/apps/frontend/openproject/app/networkpolicy.yaml").read_text()))
        plain = next(item for item in policies if item["metadata"]["name"] == "openproject")
        dev_agents = [
            rule for rule in plain["spec"]["ingress"]
            if rule["toPorts"][0]["ports"][0]["port"] == "8082"
            and rule["fromEndpoints"][0]["matchLabels"].get("io.kubernetes.pod.namespace") == "dev-agents"
        ]
        self.assertEqual(dev_agents[0]["fromEndpoints"][0]["matchExpressions"][0]["values"], ["dev-env-v2-portal"])
        portal_api = next(item for item in policies if item["metadata"]["name"] == "openproject-portal-api")
        self.assertEqual(portal_api["spec"]["ingress"][0]["toPorts"][0]["ports"], [{"port": "8443", "protocol": "TCP"}])
        self.assertNotIn("egress", portal_api["spec"])
        portal_spec = by_kind(self.docs, "CiliumNetworkPolicy")[0]["spec"]
        self.assertIsNone(openproject_egress_violation(portal_spec))
        broadened = deepcopy(portal_spec)
        broadened["egress"].append({
            "toEndpoints": [{"matchLabels": {"io.kubernetes.pod.namespace": "frontend"}}],
            "toPorts": [{"ports": [{"port": "8082", "protocol": "TCP"}]}],
        })
        self.assertEqual(openproject_egress_violation(broadened), "plaintext-openproject")

        leaf = next(doc for doc in self.docs if doc.get("kind") == "Certificate" and doc["metadata"]["name"] == "dev-env-v2-portal-serving")
        self.assertEqual(leaf["spec"]["dnsNames"], [
            "dev-env-v2.haynesops.com",
            "dev-env-v2-portal.dev-agents.svc.cluster.local",
        ])
        self.assertEqual(leaf["spec"]["secretName"], "dev-env-v2-portal-tls")
        self.assertEqual(leaf["spec"]["privateKey"]["rotationPolicy"], "Always")

        import os
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "OPERATOR_TOKEN"
            current.write_text("first")
            subpath_snapshot = current.read_text()
            replacement = Path(directory) / "OPERATOR_TOKEN.next"
            replacement.write_text("second")
            os.replace(replacement, current)
            self.assertEqual(current.read_text(), "second")
            self.assertEqual(subpath_snapshot, "first")

        pki = load(ROOT / "kubernetes/main/apps/frontend/openproject/pki/ks.yaml")
        app = load(ROOT / "kubernetes/main/apps/frontend/openproject/ks.yaml")
        self.assertEqual(pki["spec"]["healthChecks"], [
            {"apiVersion": "cert-manager.io/v1", "kind": "Certificate", "name": "openproject-portal-ca", "namespace": "frontend"},
            {"apiVersion": "cert-manager.io/v1", "kind": "Certificate", "name": "openproject-portal-api", "namespace": "frontend"},
        ])
        self.assertIs(pki["spec"]["wait"], True)
        self.assertIn({"name": "openproject-portal-pki", "namespace": "frontend"}, app["spec"]["dependsOn"])
        app_resources = load(ROOT / "kubernetes/main/apps/frontend/openproject/app/kustomization.yaml")["resources"]
        self.assertNotIn("./certificates.yaml", app_resources)
        self.assertFalse(replacement_allowed(False, True))
        self.assertTrue(replacement_allowed(True, True))
        web = load(ROOT / "kubernetes/main/apps/frontend/openproject/app/helmrelease.yaml")
        self.assertEqual(web["spec"]["values"]["controllers"]["web"]["strategy"], "Recreate")


if __name__ == "__main__":
    unittest.main()
