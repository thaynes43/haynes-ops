#!/usr/bin/env python3
"""Render the unregistered Story178 consumer packet and reject live registration."""
import base64
import hashlib
import json
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
# Story178 S4+S6: the registered portal inputs.
PORTAL_TREE = ROOT / "kubernetes/main/apps/dev-env-system/portal"
PORTAL_INPUTS = PORTAL_TREE / "app"
PORTAL_RUNTIME = PORTAL_INPUTS / "runtime-externalsecret.yaml"
PORTAL_OPENPROJECT = PORTAL_INPUTS / "openproject-externalsecret.yaml"
# Story178 S8+S9: the registered Deployment and its internal-only route.
PORTAL_DEPLOYMENT = PORTAL_INPUTS / "deployment.yaml"
PORTAL_ROUTE = PORTAL_INPUTS / "ingressroute.yaml"
PORTAL_HOST = "dev-env-v2.haynesops.com"
# One image line, pinned by tag and digest. The digest changes with each portal release.
REGISTERED_PORTAL_IMAGE = re.compile(r"ghcr\.io/thaynes43/dev-env-portal:sha-[0-9a-f]{7,40}@sha256:[0-9a-f]{64}")
# What LoadProduction reads under /etc/dev-env-portal (dev-env web/portal/session/production.go
# after dev-env#560), and where each file comes from: (kind, name, key).
PRODUCTION_FILES = {
    **{"config/" + key: ("ConfigMap", "dev-env-v2-portal-config", key)
       for key in ("origin", "issuer", "authorization-url", "token-url", "userinfo-url", "jwks-url",
                   "openproject-base-url", "operator-api-url", "auth-style", "login-ttl", "idle-ttl",
                   "absolute-ttl", "fresh-for", "clock-skew", "pages-alertmanager.json")},
    "secrets/OIDC_CLIENT_ID": ("Secret", "dev-env-v2-portal-oidc", "OIDC_CLIENT_ID"),
    "secrets/OIDC_CLIENT_SECRET": ("Secret", "dev-env-v2-portal-oidc", "OIDC_CLIENT_SECRET"),
    "secrets/DB_USERNAME": ("Secret", "dev-env-v2-portal-runtime", "DB_USERNAME"),
    "secrets/DB_PASSWORD": ("Secret", "dev-env-v2-portal-runtime", "DB_PASSWORD"),
    "secrets/SESSION_AEAD_KEY": ("Secret", "dev-env-v2-portal-runtime", "SESSION_AEAD_KEY"),
    "secrets/SESSION_GENERATION": ("Secret", "dev-env-v2-portal-runtime", "SESSION_GENERATION"),
    "secrets/OPENPROJECT_TOKEN": ("Secret?", "dev-env-v2-portal-openproject", "OPENPROJECT_TOKEN"),
    "secrets/OPERATOR_TOKEN": ("ServiceAccountToken", "dev-env-operator", 3600),
    "ca/ca.crt": ("ConfigMap", "dev-env-v2-portal-postgres-ca", "ca.crt"),
    "ca/issuer-ca.crt": ("ConfigMap", "dev-env-v2-portal-issuer-ca", "ca.crt"),
    "ca/openproject-ca.crt": ("ConfigMap", "dev-env-v2-portal-openproject-ca", "ca.crt"),
    "ca/operator-ca.crt": ("ConfigMap", "dev-env-api-ca", "ca.crt"),
    "tls/tls.crt": ("Secret", "dev-env-v2-portal-tls", "tls.crt"),
    "tls/tls.key": ("Secret", "dev-env-v2-portal-tls", "tls.key"),
}
POLICY_KS = PORTAL_TREE / "policy/ks.yaml"
INPUTS_KS = PORTAL_TREE / "ks.yaml"
READY_EXPR = "status.conditions.exists(e, e.type == 'Ready' && e.status == 'True')"
# The config/* files the portal reads, without the admission witness Tom dropped
# on 2026-10-26 (dev-env#560). No subject file.
PRODUCTION_CONFIG_FILES = {
    "origin", "issuer", "authorization-url", "token-url", "userinfo-url", "jwks-url",
    "openproject-base-url", "operator-api-url", "auth-style", "login-ttl", "idle-ttl",
    "absolute-ttl", "fresh-for", "clock-skew", "pages-alertmanager.json",
}
PAGES_CONFIG = '{"source_url":"http://alertmanager-operated.observability.svc.cluster.local:9093/api/v2/alerts"}'
ADMISSION_CONFIG_FILES = {"admission-api-url", "admission-uid", "admission-token-file"}
# The witness's API rate limits, in the reviewed packet and no longer registered.
WITNESS_CONFIG_FILES = {"witness-gets-per-second", "witness-get-burst"}
# The S1 packet policy's witness-only rules, absent from the registered policy.
WITNESS_DNS_NAME = {"matchName": "kubernetes.default.svc.cluster.local"}
WITNESS_APISERVER_RULE = {"toEntities": ["kube-apiserver"], "toPorts": [{"ports": [
    {"port": "443", "protocol": "TCP"}, {"port": "6443", "protocol": "TCP"}]}]}
# Public CA inputs: file SHA-256, then the DER SHA-256 of each certificate in order.
ISSUER_CA = (
    "1b80197679a4666ee8eaa26db3f3d1380d37da0d1b9b2b8d5120201d3dcc608e",
    [
        "349dfa4058c5e263123b398ae795573c4e1313c83fe68f93556cd5e8031b3c7d",  # GTS Root R4
        "96bcec06264976f37460779acf28c5a7cfe8a3c0aae11a8ffcee05c0bddf08c6",  # ISRG Root X1
        "69729b8e15a86efc177a57afb7171dfc64add28c2fca8cf1507e34453ccb1470",  # ISRG Root X2
        "c32ffd9f46f936d16c3673990959434b9ad60aafbb9e7cf33654f144cc1ba143",  # SSL.com TLS ECC Root CA 2022
        "c90f26f0fb1b4018b22227519b5ca2b53e2ca5b3be5cf18efe1bef47380c5383",  # Sectigo Public Server Authentication Root E46
    ],
)
OPENPROJECT_CA = (
    "c190c3de7feaadcf23e7fa7a320409875cd366bae3239812cd33a117450bb21f",
    ["9bd738852a33a019cf8b75ec9c2c574402d5217c0f6b57fbcff25cfe0122d986"],  # openproject-portal-ca
)
POSTGRES_CA = (
    "f89c6cc6c2ddf495b5112023c7863142696ddecd413cba98bf36f1c940bae818",
    ["53b084dc3e8fe33fb9e6e1ffd49092cd806a396ef480324a2af0c21720e99f21"],  # postgres16, expires 2026-11-05
)
CURRENT_RUNTIME_HEAD = "af402e533379bddd81e9266ee80ec6c565b81f29"
PORTAL_IMAGE = (
    "ghcr.io/thaynes43/dev-env-portal:sha-af402e5"
    "@sha256:71d3c5d64d1948a78acdc695337fb8edac542ec46eb81fcddaddafec30df0772"
)
OPENPROJECT_ORIGIN = "https://openproject-web.frontend.svc.cluster.local:8443"
OPENPROJECT_TASKS_QUERY = "/api/v3/projects/3/work_packages?offset=<page>&pageSize=50&filters=%5B%5D"
OPERATOR_IMAGE = (
    "sha-397f688"
    "@sha256:5b4526802554d844b864b6090185f6f1f40f449116aa29ed9790e7ac34bd8a8a"
)
PREVIOUS_OPERATOR_IMAGE = (
    "sha-11be6ed"
    "@sha256:dcaee481863793a702a8d8b187dbaa85a97270246557ce52ca3a19edd324fd4d"
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


def approved_portal_runtime_violations(path, doc):
    """Only the reviewed four-property runtime projection, at its one registered path."""
    expected = {
        "apiVersion": "external-secrets.io/v1", "kind": "ExternalSecret",
        "metadata": {"name": "dev-env-v2-portal-runtime", "namespace": "dev-agents"},
        "spec": {
            "secretStoreRef": STORE, "refreshInterval": "1h",
            "target": {
                "name": "dev-env-v2-portal-runtime", "creationPolicy": "Owner", "deletionPolicy": "Delete",
                "template": {"engineVersion": "v2", "mergePolicy": "Replace", "type": "Opaque", "data": {
                    field: "{{ .%s }}" % field for field in RUNTIME_FIELDS
                }},
            },
            "data": [
                {"secretKey": field, "remoteRef": {"key": CANONICAL_ITEM, "property": field}}
                for field in RUNTIME_FIELDS
            ],
        },
    }
    return [] if path.resolve() == PORTAL_RUNTIME.resolve() and doc == expected else ["noncanonical-portal-runtime"]


def approved_portal_openproject_violations(path, doc):
    """Fast path, 2026-10-26: the agents' existing token, one property, one path."""
    expected = {
        "apiVersion": "external-secrets.io/v1", "kind": "ExternalSecret",
        "metadata": {"name": "dev-env-v2-portal-openproject", "namespace": "dev-agents"},
        "spec": {
            "secretStoreRef": STORE, "refreshInterval": "1h",
            "target": {
                "name": "dev-env-v2-portal-openproject", "creationPolicy": "Owner", "deletionPolicy": "Delete",
                "template": {"engineVersion": "v2", "mergePolicy": "Replace", "type": "Opaque",
                             "data": {"OPENPROJECT_TOKEN": "{{ .OPENPROJECT_TOKEN }}"}},
            },
            "data": [{"secretKey": "OPENPROJECT_TOKEN", "remoteRef": {"key": "openproject", "property": "API_TOKEN"}}],
        },
    }
    return [] if path.resolve() == PORTAL_OPENPROJECT.resolve() and doc == expected else ["noncanonical-portal-openproject"]


APPROVED_LIVE_PATHS = {PORTAL_OIDC.resolve(), PORTAL_DB.resolve(), PORTAL_RUNTIME.resolve(), PORTAL_OPENPROJECT.resolve()}


def approved_live_projection(path, doc):
    return (not approved_portal_oidc_violations(path, doc) or not approved_portal_db_violations(path, doc)
            or not approved_portal_runtime_violations(path, doc)
            or not approved_portal_openproject_violations(path, doc))


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
        if path.resolve() in APPROVED_LIVE_PATHS:
            documents = list(yaml.safe_load_all(text))
            if len(documents) == 1 and approved_live_projection(path, documents[0]):
                continue
        found.update(inline.findall(text))
        found.update(block.findall(text))
    return found


def load(path):
    return yaml.safe_load(path.read_text())


def _flux_files(tree):
    return sorted(set(tree.rglob("*.yaml")) | set(tree.rglob("*.yml")) | set(tree.rglob("*.json")))


def _names_entry(value, name):
    entries = value if isinstance(value, list) else [value]
    return any(isinstance(item, dict) and item.get("name") == name for item in entries)


def _depends_on_names(node, name):
    if isinstance(node, str):
        # An inline kustomize patch carries YAML inside a string. Only parsed
        # mappings and lists are searched again, so a scalar cannot recurse.
        if "dependsOn" not in node:
            return False
        try:
            docs = list(yaml.safe_load_all(node))
        except yaml.YAMLError:
            return True
        return any(_depends_on_names(doc, name) for doc in docs if isinstance(doc, (dict, list)))
    if isinstance(node, dict):
        # A JSON6902 operation: path .../dependsOn..., value {name: ...} or a list of them.
        path = node.get("path")
        if isinstance(path, str) and "dependsOn" in path and _names_entry(node.get("value"), name):
            return True
        for key, value in node.items():
            if key == "dependsOn" and isinstance(value, list) and _names_entry(value, name):
                return True
            if _depends_on_names(value, name):
                return True
    elif isinstance(node, list):
        return any(_depends_on_names(item, name) for item in node)
    return False


def dependents(tree, name):
    """Files with a dependsOn entry naming exactly `name`, in YAML or JSON at any depth.

    A file that mentions both dependsOn and the name but does not parse is
    reported too, so an unreadable dependent cannot pass silently.
    """
    exact = re.compile(r"(?<![A-Za-z0-9-])" + re.escape(name) + r"(?![A-Za-z0-9-])")
    found = []
    for path in _flux_files(tree):
        text = path.read_text(errors="replace")
        if "dependsOn" not in text or not exact.search(text):
            continue
        try:
            docs = [json.loads(text)] if path.suffix == ".json" else list(yaml.safe_load_all(text))
        except (ValueError, yaml.YAMLError):
            found.append(path.resolve())
            continue
        if any(_depends_on_names(doc, name) for doc in docs):
            found.append(path.resolve())
    return found


def policy_mentions(tree):
    return {path.resolve() for path in _flux_files(tree)
            if "dev-env-v2-portal-policy" in path.read_text(errors="replace")}


def policy_dependents(tree):
    return dependents(tree, "dev-env-v2-portal-policy")


def pem_certificates(text):
    """DER bytes of each PEM block. Any non-CERTIFICATE block or stray text is an error."""
    blocks = re.findall(r"-----BEGIN ([A-Z0-9 ]+)-----\n(.*?)-----END \1-----\n", text, re.S)
    rebuilt = "".join("-----BEGIN %s-----\n%s-----END %s-----\n" % (kind, body, kind) for kind, body in blocks)
    if rebuilt != text or not blocks:
        raise ValueError("ca.crt must be PEM blocks only")
    if any(kind != "CERTIFICATE" for kind, _ in blocks):
        raise ValueError("ca.crt must hold CERTIFICATE blocks only")
    return [base64.b64decode("".join(body.split()), validate=True) for _, body in blocks]


def is_ca_certificate(der):
    completed = subprocess.run(
        ["openssl", "x509", "-inform", "DER", "-noout", "-ext", "basicConstraints"],
        input=der, capture_output=True, check=True,
    )
    return b"CA:TRUE" in completed.stdout


def render_inputs():
    completed = subprocess.run(
        ["kustomize", "build", str(PORTAL_INPUTS)], check=True, capture_output=True, text=True,
    )
    return completed.stdout, [doc for doc in yaml.safe_load_all(completed.stdout) if doc]


INPUT_OBJECTS = {
    ("Deployment", "dev-env-v2-portal"),
    ("IngressRoute", "dev-env-v2-portal"),
    ("ConfigMap", "dev-env-v2-portal-config"),
    ("ConfigMap", "dev-env-v2-portal-issuer-ca"),
    ("ConfigMap", "dev-env-v2-portal-openproject-ca"),
    ("ConfigMap", "dev-env-v2-portal-postgres-ca"),
    ("Service", "dev-env-v2-portal"),
    ("Issuer", "dev-env-v2-portal-selfsigned"),
    ("Issuer", "dev-env-v2-portal-ca"),
    ("Certificate", "dev-env-v2-portal-ca"),
    ("Certificate", "dev-env-v2-portal-serving"),
    ("ExternalSecret", "dev-env-v2-portal-runtime"),
    ("ExternalSecret", "dev-env-v2-portal-openproject"),
    ("ServersTransport", "dev-env-v2-portal"),
}


def mounted_files(deployment):
    """Every file the Deployment puts under /etc/dev-env-portal, and its source.

    The source is (kind, name, key), "Secret?" for an optional Secret, or
    ("ServiceAccountToken", audience, expirationSeconds). A subPath mount, an
    unknown volume type or a source without items is reported as ("unsupported", ...).
    """
    pod = deployment["spec"]["template"]["spec"]
    volumes = {v["name"]: v for v in pod.get("volumes", [])}
    files = {}

    def add_items(prefix, kind, name, items):
        if not items:
            files[prefix + "*"] = ("unsupported", name, "no items")
            return
        for item in items:
            files[prefix + item["path"]] = (kind, name, item["key"])

    for container in pod["containers"]:
        for mount in container.get("volumeMounts", []):
            path = mount["mountPath"]
            if not path.startswith("/etc/dev-env-portal/"):
                continue
            prefix = path[len("/etc/dev-env-portal/"):] + "/"
            volume = volumes.get(mount["name"], {})
            if "subPath" in mount or "subPathExpr" in mount:
                files[prefix] = ("unsupported", mount["name"], "subPath")
            elif "configMap" in volume:
                cm = volume["configMap"]
                if cm.get("items"):
                    add_items(prefix, "ConfigMap", cm["name"], cm["items"])
                else:
                    files[prefix + "*"] = ("ConfigMap", cm["name"], "*")
            elif "secret" in volume:
                secret = volume["secret"]
                kind = "Secret?" if secret.get("optional") else "Secret"
                add_items(prefix, kind, secret["secretName"], secret.get("items"))
            elif "projected" in volume:
                for source in volume["projected"]["sources"]:
                    if "secret" in source:
                        kind = "Secret?" if source["secret"].get("optional") else "Secret"
                        add_items(prefix, kind, source["secret"]["name"], source["secret"].get("items"))
                    elif "configMap" in source:
                        kind = "ConfigMap?" if source["configMap"].get("optional") else "ConfigMap"
                        add_items(prefix, kind, source["configMap"]["name"], source["configMap"].get("items"))
                    elif "serviceAccountToken" in source:
                        token = source["serviceAccountToken"]
                        files[prefix + token["path"]] = (
                            "ServiceAccountToken", token.get("audience"), token.get("expirationSeconds"))
                    else:
                        files[prefix + "?"] = ("unsupported", mount["name"], sorted(source))
            else:
                files[prefix + "?"] = ("unsupported", mount["name"], sorted(volume))
    return files


def expand_config_files(files, docs):
    """Replace a whole-ConfigMap mount with one file per key of the rendered ConfigMap."""
    out = {}
    configmaps = {doc["metadata"]["name"]: doc for doc in docs if doc.get("kind") == "ConfigMap"}
    for path, source in files.items():
        if path.endswith("*") and source[0] == "ConfigMap" and source[2] == "*" and source[1] in configmaps:
            for key in configmaps[source[1]].get("data", {}):
                out[path[:-1] + key] = ("ConfigMap", source[1], key)
        else:
            out[path] = source
    return out


def deployment_violations(doc, docs):
    """The S8 portal Deployment: what it mounts, what it runs as, and how it resolves names."""
    violations = []
    spec = doc.get("spec", {})
    template = spec.get("template", {})
    pod = template.get("spec", {})
    labels = {"app.kubernetes.io/name": "dev-env-v2-portal"}
    if spec.get("selector") != {"matchLabels": labels} or template.get("metadata", {}).get("labels") != labels:
        violations.append("labels")
    containers = pod.get("containers", [])
    if len(containers) != 1 or pod.get("initContainers") or pod.get("ephemeralContainers"):
        violations.append("containers")
        return violations
    container = containers[0]
    if not REGISTERED_PORTAL_IMAGE.fullmatch(str(container.get("image"))):
        violations.append("image")
    if container.get("command") or container.get("args") or container.get("env") or container.get("envFrom"):
        violations.append("command-or-env")
    if pod.get("serviceAccountName") != "dev-env-v2-portal" or pod.get("automountServiceAccountToken") is not False:
        violations.append("service-account")
    if pod.get("dnsPolicy") != "ClusterFirst" or pod.get("dnsConfig") != {"options": [{"name": "ndots", "value": "1"}]}:
        violations.append("dns")
    if any(pod.get(key) for key in ("hostNetwork", "hostPID", "hostIPC", "hostUsers")):
        violations.append("host-namespace")
    pod_security = pod.get("securityContext", {})
    security = container.get("securityContext", {})
    if (pod_security.get("runAsNonRoot") is not True
            or pod_security.get("seccompProfile") != {"type": "RuntimeDefault"}
            or security.get("allowPrivilegeEscalation") is not False
            or security.get("readOnlyRootFilesystem") is not True
            or security.get("capabilities") != {"drop": ["ALL"]}
            or security.get("privileged")):
        violations.append("security-context")
    if "cpu" not in container.get("resources", {}).get("limits", {}):
        violations.append("cpu-limit")
    if container.get("ports") != [{"name": "https", "containerPort": 8443, "protocol": "TCP"}]:
        violations.append("ports")
    files = expand_config_files(mounted_files(doc), docs)
    if files != PRODUCTION_FILES:
        violations.append("mounted-files")
    named = yaml.safe_dump(pod.get("volumes", []))
    if "admission" in named or "witness" in named:
        violations.append("admission-input")
    for volume in pod.get("volumes", []):
        secret = volume.get("secret", {}).get("secretName")
        projected = [s["secret"]["name"] for s in volume.get("projected", {}).get("sources", []) if "secret" in s]
        if "dev-env-v2-portal-ca" in [secret] + projected:
            violations.append("ca-key-mounted")
        if "hostPath" in volume or "persistentVolumeClaim" in volume:
            violations.append("volume-type:" + volume["name"])
    return violations


def route_violations(doc):
    """The S9 route: internal Traefik, internal DNS, one host, the portal Service over checked TLS."""
    violations = []
    meta = doc.get("metadata", {})
    annotations = meta.get("annotations", {})
    if annotations.get("kubernetes.io/ingress.class") != "traefik-internal":
        violations.append("ingress-class")
    if annotations.get("external-dns.kubernetes.io/target") != "internal.haynesops":
        violations.append("dns-target")
    if set(annotations) != {"kubernetes.io/ingress.class", "external-dns.kubernetes.io/target"}:
        violations.append("annotations")
    spec = doc.get("spec", {})
    if spec.get("entryPoints") != ["websecure"]:
        violations.append("entrypoints")
    if spec.get("tls") != {"secretName": "certificate-haynesops"}:
        violations.append("tls")
    routes = spec.get("routes", [])
    if len(routes) != 1:
        violations.append("routes")
        return violations
    route = routes[0]
    if route.get("kind") != "Rule" or route.get("match") != "Host(`%s`) && PathPrefix(`/`)" % PORTAL_HOST:
        violations.append("match")
    if route.get("middlewares"):
        violations.append("middlewares")
    if route.get("services") != [{
            "kind": "Service", "name": "dev-env-v2-portal", "namespace": "dev-agents", "port": 8443,
            "scheme": "https", "passHostHeader": True, "serversTransport": "dev-env-v2-portal"}]:
        violations.append("backend")
    text = yaml.safe_dump(doc)
    for needle in ("traefik-external", "haynesnetwork.com", "cloudflare", "insecureSkipVerify"):
        if needle in text:
            violations.append("public:" + needle)
    return violations


def registered_input_violations(docs):
    """What S4, S6, S8 and S9 may register, and what stays out."""
    violations = []
    found = {(doc.get("kind"), doc.get("metadata", {}).get("name")) for doc in docs}
    if found != INPUT_OBJECTS or len(found) != len(docs):
        violations.append("object-set")
    for doc in docs:
        meta = doc.get("metadata", {})
        expected_namespace = "dev" if doc.get("kind") == "IngressRoute" else "dev-agents"
        if meta.get("namespace") != expected_namespace:
            violations.append("namespace:" + str(meta.get("name")))
        if doc.get("kind") in {"StatefulSet", "DaemonSet", "Pod", "Job", "CronJob", "Ingress", "HTTPRoute",
                               "Gateway", "IngressRouteTCP", "Middleware", "Secret", "ServiceAccount", "Role",
                               "RoleBinding", "ClusterRole", "ClusterRoleBinding", "CiliumNetworkPolicy"}:
            violations.append("forbidden-kind:" + doc["kind"])
        if doc.get("kind") == "Deployment":
            violations += ["deployment:" + v for v in deployment_violations(doc, docs)]
        if doc.get("kind") == "IngressRoute":
            violations += ["route:" + v for v in route_violations(doc)]
        if doc.get("kind") == "Service" and doc.get("spec", {}).get("type", "ClusterIP") != "ClusterIP":
            violations.append("service-type")
        if doc.get("kind") == "ConfigMap" and meta.get("name") in {
                "dev-env-v2-portal-admission", "dev-env-v2-portal-admission-ca", "dev-env-v2-portal-operator-ca"}:
            violations.append("held-configmap:" + meta["name"])
        if doc.get("kind") == "ConfigMap" and meta.get("name") == "dev-env-v2-portal-config":
            data = doc.get("data", {})
            if set(data) != PRODUCTION_CONFIG_FILES:
                violations.append("config-keys")
            if (ADMISSION_CONFIG_FILES | WITNESS_CONFIG_FILES) & set(data):
                violations.append("admission-config")
            if data.get("pages-alertmanager.json") != PAGES_CONFIG:
                violations.append("pages-config")
        if doc.get("kind") == "ServersTransport" and "rootCAs" in doc.get("spec", {}):
            violations.append("rootCAs-pruned-by-installed-crd")
        if (doc.get("kind") == "ExternalSecret" and approved_portal_runtime_violations(PORTAL_RUNTIME, doc)
                and approved_portal_openproject_violations(PORTAL_OPENPROJECT, doc)):
            violations.append("runtime-externalsecret")
    text = yaml.safe_dump_all(docs)
    for needle in ("PORTAL_SUBJECT", "config/subject", "PRIVATE KEY", "dataFrom"):
        if needle in text:
            violations.append("text:" + needle)
    if any("subject" in doc.get("data", {}) for doc in docs if doc.get("kind") == "ConfigMap"):
        violations.append("subject-key")
    return violations


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


def policy_without_witness(policy):
    """The packet policy minus its witness-only egress (exactly one of each, or it fails)."""
    out = deepcopy(policy)
    egress = out["spec"]["egress"]
    if egress.count(WITNESS_APISERVER_RULE) != 1:
        raise AssertionError("packet policy lacks exactly one kube-apiserver rule")
    egress.remove(WITNESS_APISERVER_RULE)
    dns = egress[0]["toPorts"][0]["rules"]["dns"]
    if dns.count(WITNESS_DNS_NAME) != 1:
        raise AssertionError("packet policy lacks exactly one kubernetes.default DNS name")
    dns.remove(WITNESS_DNS_NAME)
    return out


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
        # Story178 S3: the reader is on, with the namespace it requires stated.
        self.assertIn("--enable-portal-reader", operator["args"])
        # Measured conversation usage (2026-10-28): reported to the portal reader's activity view,
        # once, with the reader on, and no portal action (send, history, launch) enabled by it.
        self.assertEqual([a for a in operator["args"] if a.startswith("--portal-conversation-usage")],
                         ["--portal-conversation-usage=true"])
        self.assertFalse([a for a in operator["args"] if a.startswith(("--portal-send-target", "--portal-history-target", "--portal-launch"))])
        self.assertIn("--session-namespace=dev-agents", operator["args"])
        self.assertEqual(sum(arg.startswith("--session-namespace") for arg in operator["args"]), 1)
        self.assertFalse(any(arg.startswith("--enable-child-usage-capture") for arg in operator["args"]))
        # The measured-usage pilot (2026-10-28): one new key and the signed 2.52.0 agent image,
        # each flag once. The parent is the coordinator session and no portal action is enabled.
        self.assertEqual([a for a in operator["args"] if a.startswith("--pilot-agent-image")], [
            "--pilot-agent-image=ghcr.io/thaynes43/dev-env:2.52.0@sha256:87e8ab9346c2f656c0dfe19eb3d09ae89e220a341dd86e30e9ef0d43230204f9"])
        self.assertEqual([a for a in operator["args"] if a.startswith("--pilot-agent-session")], [
            "--pilot-agent-session=dev-env-1027-111645/usage-pilot-1"])
        self.assertFalse([a for a in operator["args"] if a.startswith(("--portal-send-target", "--portal-history-target", "--portal-launch"))])
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
            # Story178 S8+S9 registered the Deployment and its route, each at one path.
            if "kind: Deployment" in text and "name: dev-env-v2-portal" in text:
                self.assertEqual(path.resolve(), PORTAL_DEPLOYMENT.resolve(), "portal Deployment in " + str(path))
            if "dev-env-v2.haynesops.com" in text and re.search(r"kind:\s*(IngressRoute|Ingress|HTTPRoute)\b", text):
                self.assertEqual(path.resolve(), PORTAL_ROUTE.resolve(), "portal route in " + str(path))
            if "kind: ExternalSecret" in text:
                for doc in yaml.safe_load_all(text):
                    if doc and doc.get("kind") == "ExternalSecret" and "dev-env-v2-portal" in doc.get("metadata", {}).get("name", ""):
                        self.assertTrue(approved_live_projection(path, doc), str(path))
                        self.assertIn(path.resolve(), APPROVED_LIVE_PATHS)
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
        self.assertIn(PREVIOUS_OPERATOR_IMAGE, checklist)
        self.assertIn("explicit `--enable-portal-reader` and `--session-namespace=dev-agents`", checklist)
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
        # The registered policy removes witness-only egress and adds the approved
        # scoped Alertmanager read; every other packet policy field remains exact.
        registered_policy = load(registered_dir / "app/networkpolicy.yaml")
        packet_policy = load(COMPONENT / "policy/networkpolicy.yaml")
        expected_policy = policy_without_witness(packet_policy)
        expected_policy["spec"]["egress"][0]["toPorts"][0]["rules"]["dns"].append(
            {"matchName": "alertmanager-operated.observability.svc.cluster.local"})
        expected_policy["spec"]["egress"].append({
            "toEndpoints": [{"matchLabels": {
                "io.kubernetes.pod.namespace": "observability",
                "app.kubernetes.io/name": "alertmanager",
                "app.kubernetes.io/instance": "kube-prometheus-stack",
            }}],
            "toPorts": [{"ports": [{"port": "9093", "protocol": "TCP"}],
                         "rules": {"http": [{"method": "^GET$", "path": "^/api/v2/alerts$"}]}}],
        })
        self.assertEqual(registered_policy, expected_policy)
        self.assertNotEqual(registered_policy, packet_policy)
        registered_text = yaml.safe_dump(registered_policy)
        self.assertNotIn("kube-apiserver", registered_text)
        self.assertNotIn("kubernetes.default", registered_text)
        system = load(ROOT / "kubernetes/main/apps/dev-env-system/kustomization.yaml")["resources"]
        self.assertEqual(system.count("./portal/policy/ks.yaml"), 1)
        # S4 relaxes "no dependents" to exactly one: the inputs Kustomization's dependsOn.
        self.assertEqual(policy_mentions(ROOT / "kubernetes"), {POLICY_KS.resolve(), INPUTS_KS.resolve()})
        self.assertEqual(policy_dependents(ROOT / "kubernetes"), [INPUTS_KS.resolve()])
        self.assertEqual(load(INPUTS_KS)["spec"]["dependsOn"][0],
                         {"name": "dev-env-v2-portal-policy", "namespace": "dev-env-system"})
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


def without_registration_marker(doc):
    doc = deepcopy(doc)
    annotations = doc["metadata"].get("annotations", {})
    annotations.pop("dev-env.haynesops.com/portal-registration", None)
    if not annotations:
        doc["metadata"].pop("annotations", None)
    return doc


class RegisteredInputsTest(unittest.TestCase):
    """Story178 S4+S6: the portal's inputs, registered before any portal pod."""

    @classmethod
    def setUpClass(cls):
        cls.rendered, cls.docs = render_inputs()
        _, packet = render()
        cls.packet = {(doc["kind"], doc["metadata"]["name"]): without_registration_marker(doc) for doc in packet}
        cls.by_key = {(doc["kind"], doc["metadata"]["name"]): doc for doc in cls.docs}

    def test_flux_kustomization_is_exact_and_has_no_dependents(self):
        self.assertEqual(load(INPUTS_KS), {
            "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
            "kind": "Kustomization",
            "metadata": {"name": "dev-env-v2-portal", "namespace": "dev-env-system"},
            "spec": {
                "dependsOn": [
                    {"name": "dev-env-v2-portal-policy", "namespace": "dev-env-system"},
                    {"name": "cert-manager", "namespace": "cert-manager"},
                    {"name": "external-secrets-stores", "namespace": "external-secrets"},
                ],
                "path": "./kubernetes/main/apps/dev-env-system/portal/app",
                "prune": True,
                "sourceRef": {"kind": "GitRepository", "name": "haynes-ops", "namespace": "flux-system"},
                "wait": True,
                "healthCheckExprs": [
                    {"apiVersion": "cert-manager.io/v1", "kind": "Certificate", "current": READY_EXPR},
                    {"apiVersion": "external-secrets.io/v1", "kind": "ExternalSecret", "current": READY_EXPR},
                ],
                "interval": "30m",
                "retryInterval": "1m",
                "timeout": "5m",
            },
        })
        # Every kind that kstatus cannot judge has an explicit Ready expression.
        checked = {(e["apiVersion"], e["kind"]) for e in load(INPUTS_KS)["spec"]["healthCheckExprs"]}
        self.assertTrue({(d["apiVersion"], d["kind"]) for d in self.docs
                         if d["kind"] in {"Certificate", "ExternalSecret"}} <= checked)
        self.assertNotIn("targetNamespace", load(INPUTS_KS)["spec"])
        system = load(ROOT / "kubernetes/main/apps/dev-env-system/kustomization.yaml")["resources"]
        self.assertEqual(system.count("./portal/ks.yaml"), 1)
        self.assertEqual(system.count("./portal/policy/ks.yaml"), 1)
        self.assertEqual(dependents(ROOT / "kubernetes", "dev-env-v2-portal"), [])
        self.assertEqual(policy_dependents(ROOT / "kubernetes"), [INPUTS_KS.resolve()])

    def test_dependents_scan_finds_yaml_and_json(self):
        with tempfile.TemporaryDirectory() as scratch:
            tree = Path(scratch)
            (tree / "a.yaml").write_text("spec:\n  dependsOn:\n    - name: dev-env-v2-portal\n")
            (tree / "b.json").write_text('{"spec": {"dependsOn": [{"name": "dev-env-v2-portal"}]}}')
            (tree / "c.yaml").write_text("spec:\n  dependsOn:\n    - name: dev-env-v2-portal-policy\n")
            (tree / "d.yaml").write_text("dependsOn: [\n  name: dev-env-v2-portal\n")
            (tree / "e.yaml").write_text(
                "patches:\n  - patch: |\n      kind: Kustomization\n      spec:\n"
                "        dependsOn:\n          - name: dev-env-v2-portal\n")
            (tree / "f.yaml").write_text("patches:\n  - patch: |\n      spec:\n        dependsOn: []\n"
                                         "        path: dev-env-v2-portal\n")
            (tree / "g.yaml").write_text(
                "patches:\n  - patch: |\n      - op: add\n        path: /spec/dependsOn/-\n"
                "        value:\n          name: dev-env-v2-portal\n")
            (tree / "h.yaml").write_text(
                "patches:\n  - patch: |\n      - op: add\n        path: /spec/dependsOn/-\n"
                "        value:\n          name: dev-env-v2-portal-policy\n")
            self.assertEqual(sorted(p.name for p in dependents(tree, "dev-env-v2-portal")),
                             ["a.yaml", "b.json", "d.yaml", "e.yaml", "g.yaml"])
            self.assertFalse(_depends_on_names("/spec/dependsOn/-", "dev-env-v2-portal"))
            self.assertEqual([p.name for p in policy_dependents(tree)], ["c.yaml", "h.yaml"])

    def test_inputs_are_exactly_the_registered_set(self):
        manifest = load(PORTAL_INPUTS / "kustomization.yaml")
        self.assertEqual(manifest["resources"], [
            "./certificates.yaml", "./service.yaml", "./serverstransport.yaml",
            "./config.yaml", "./runtime-externalsecret.yaml", "./openproject-externalsecret.yaml",
            "./deployment.yaml", "./ingressroute.yaml",
        ])
        self.assertNotIn("namespace", manifest)
        self.assertNotIn("components", manifest)
        self.assertEqual(manifest["generatorOptions"], {"disableNameSuffixHash": True})
        self.assertEqual(sorted(p.name for p in PORTAL_INPUTS.iterdir()), sorted([
            "kustomization.yaml", "certificates.yaml", "service.yaml", "serverstransport.yaml",
            "config.yaml", "runtime-externalsecret.yaml", "openproject-externalsecret.yaml", "issuer-ca.crt", "openproject-ca.crt",
            "postgres-ca.crt", "deployment.yaml", "ingressroute.yaml",
        ]))
        self.assertEqual(registered_input_violations(self.docs), [])
        self.assertNotIn("portal-registration", self.rendered)

    def test_guard_rejects_later_steps_and_drift(self):
        second_route = deepcopy(self.by_key[("IngressRoute", "dev-env-v2-portal")])
        second_route["metadata"]["name"] = "dev-env-v2-portal-public"
        duplicate_deployment = deepcopy(self.by_key[("Deployment", "dev-env-v2-portal")])
        admission = {"apiVersion": "v1", "kind": "ConfigMap",
                     "metadata": {"name": "dev-env-v2-portal-admission", "namespace": "dev-agents"},
                     "data": {"schema": "1", "state": "closed"}}
        admission_ca = {"apiVersion": "v1", "kind": "ConfigMap",
                        "metadata": {"name": "dev-env-v2-portal-admission-ca", "namespace": "dev-agents"},
                        "data": {"ca.crt": "x"}}
        for extra, expected in (
            (second_route, "object-set"),
            (duplicate_deployment, "object-set"),
            (admission, "held-configmap:dev-env-v2-portal-admission"),
            (admission_ca, "held-configmap:dev-env-v2-portal-admission-ca"),
        ):
            violations = registered_input_violations(self.docs + [deepcopy(extra)])
            self.assertIn("object-set", violations)
            self.assertIn(expected, violations)

        def mutated(kind, name, change):
            docs = deepcopy(self.docs)
            change(next(d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name))
            return registered_input_violations(docs)

        config = ("ConfigMap", "dev-env-v2-portal-config")
        self.assertIn("subject-key", mutated(*config, lambda d: d["data"].update(subject="x")))
        self.assertIn("config-keys", mutated(*config, lambda d: d["data"].update(subject="x")))
        self.assertIn("config-keys", mutated(*config, lambda d: d["data"].pop("origin")))
        self.assertIn("admission-config", mutated(*config, lambda d: d["data"].update({"admission-uid": "x"})))
        self.assertIn("admission-config", mutated(*config, lambda d: d["data"].update({"witness-get-burst": "57"})))
        self.assertIn("text:PORTAL_SUBJECT", mutated(*config, lambda d: d["data"].update(origin="PORTAL_SUBJECT")))
        self.assertIn("namespace:dev-env-v2-portal", mutated("Service", "dev-env-v2-portal",
                                                             lambda d: d["metadata"].update(namespace="dev")))
        self.assertIn("rootCAs-pruned-by-installed-crd", mutated(
            "ServersTransport", "dev-env-v2-portal",
            lambda d: d["spec"].update(rootCAs=[{"secret": "dev-env-v2-portal-tls"}])))
        runtime = ("ExternalSecret", "dev-env-v2-portal-runtime")
        for change in (
            lambda d: d["spec"]["data"].append({"secretKey": "PORTAL_SUBJECT",
                                                "remoteRef": {"key": CANONICAL_ITEM, "property": "PORTAL_SUBJECT"}}),
            lambda d: d["spec"].update(dataFrom=[{"extract": {"key": CANONICAL_ITEM}}]),
            lambda d: d["spec"]["data"][0]["remoteRef"].update(key="dev-env-v2-portal-runtime"),
            lambda d: d["spec"]["target"].update(name="another-recipient"),
        ):
            self.assertIn("runtime-externalsecret", mutated(*runtime, change))

    def test_serving_pki_and_service_match_the_reviewed_packet(self):
        for key in (("Issuer", "dev-env-v2-portal-selfsigned"), ("Issuer", "dev-env-v2-portal-ca"),
                    ("Certificate", "dev-env-v2-portal-ca"), ("Certificate", "dev-env-v2-portal-serving"),
                    ("Service", "dev-env-v2-portal")):
            self.assertEqual(self.by_key[key], self.packet[key], key)

    def test_servers_transport_trusts_the_ca_that_issues_the_leaf(self):
        transport = self.by_key[("ServersTransport", "dev-env-v2-portal")]
        packet = deepcopy(self.packet[("ServersTransport", "dev-env-v2-portal")])
        # The one reviewed delta: the packet's rootCAs named a Secret nothing creates,
        # and the installed CRD has no rootCAs field.
        self.assertEqual(packet["spec"].pop("rootCAs"), [{"secret": "dev-env-v2-portal-serving-ca"}])
        packet["spec"]["rootCAsSecrets"] = ["dev-env-v2-portal-tls"]
        self.assertEqual(transport, packet)
        self.assertNotIn("rootCAs", transport["spec"])
        leaf = self.by_key[("Certificate", "dev-env-v2-portal-serving")]
        ca = self.by_key[("Certificate", "dev-env-v2-portal-ca")]
        ca_issuer = self.by_key[("Issuer", "dev-env-v2-portal-ca")]
        self.assertEqual(transport["spec"]["rootCAsSecrets"], [leaf["spec"]["secretName"]])
        self.assertEqual(leaf["spec"]["issuerRef"], {"name": ca_issuer["metadata"]["name"], "kind": "Issuer"})
        self.assertEqual(ca_issuer["spec"]["ca"]["secretName"], ca["spec"]["secretName"])
        self.assertIs(ca["spec"]["isCA"], True)
        self.assertIn(transport["spec"]["serverName"], leaf["spec"]["dnsNames"])
        self.assertIn("dev-env-v2.haynesops.com", leaf["spec"]["dnsNames"])
        self.assertIs(transport["spec"]["insecureSkipVerify"], False)
        self.assertEqual(transport["metadata"]["namespace"], leaf["metadata"]["namespace"])
        # Every Secret the inputs name is one cert-manager creates here.
        created = {doc["spec"]["secretName"] for doc in self.docs if doc["kind"] == "Certificate"}
        self.assertEqual(created, {"dev-env-v2-portal-ca", "dev-env-v2-portal-tls"})
        self.assertTrue(set(transport["spec"]["rootCAsSecrets"]) <= created)

    def test_config_is_the_packet_without_admission(self):
        config = self.by_key[("ConfigMap", "dev-env-v2-portal-config")]
        packet = self.packet[("ConfigMap", "dev-env-v2-portal-config")]
        dropped = ADMISSION_CONFIG_FILES | WITNESS_CONFIG_FILES
        self.assertTrue(WITNESS_CONFIG_FILES <= set(packet["data"]))
        expected = {k: v for k, v in packet["data"].items() if k not in dropped}
        expected["pages-alertmanager.json"] = PAGES_CONFIG
        self.assertEqual(config["data"], expected)
        self.assertEqual(set(config["data"]), PRODUCTION_CONFIG_FILES)
        self.assertTrue(dropped.isdisjoint(config["data"]))
        self.assertNotIn("subject", config["data"])
        self.assertNotIn("session-bindings.json", config["data"])
        for key, value in config["data"].items():
            self.assertIsInstance(value, str, key)
            self.assertLess(len(value.encode()), 4096, key)
            self.assertEqual(value, value.strip(), key)
            self.assertNotIn("\n", value, key)

    def test_pages_config_is_exact_and_rejects_drift(self):
        self.assertEqual(registered_input_violations(self.docs), [])
        bad_values = [
            '{"source_url":"http://wrong.example/api/v2/alerts"}',
            '{"source_url":"http://alertmanager-operated.observability.svc.cluster.local:9093/api/v2/alerts","issuer":"x"}',
            '{"source_url":"http://alertmanager-operated.observability.svc.cluster.local:9093/api/v2/alerts","subject":"x"}',
            '{"source_url":42}', '{"source_url":null}', 'null', '{',
            '{"source_url":"http://alertmanager-operated.observability.svc.cluster.local:9093/api/v2/alerts","unknown":true}',
            '{"source_url":"http://alertmanager-operated.observability.svc.cluster.local:9093/api/v2/alerts","source_url":"http://alertmanager-operated.observability.svc.cluster.local:9093/api/v2/alerts"}',
        ]
        for value in bad_values:
            docs = deepcopy(self.docs)
            config = next(d for d in docs if d.get("kind") == "ConfigMap" and d.get("metadata", {}).get("name") == "dev-env-v2-portal-config")
            config["data"]["pages-alertmanager.json"] = value
            self.assertIn("pages-config", registered_input_violations(docs), value)
        deployment = self.by_key[("Deployment", "dev-env-v2-portal")]
        self.assertEqual(deployment["spec"]["strategy"], {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}})
        image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
        self.assertRegex(image, REGISTERED_PORTAL_IMAGE)
        self.assertEqual(image, "ghcr.io/thaynes43/dev-env-portal:sha-f7e06be@sha256:0598e284d01cd0c9a98ca96caae7eea8066e64684469760bd86eadac4eacd25e")
        self.assertEqual(expand_config_files(mounted_files(deployment), self.docs), PRODUCTION_FILES)

    def test_public_ca_bundles_are_pinned_certificates_only(self):
        for name, filename, (file_sha, der_shas) in (
            ("dev-env-v2-portal-issuer-ca", "issuer-ca.crt", ISSUER_CA),
            ("dev-env-v2-portal-openproject-ca", "openproject-ca.crt", OPENPROJECT_CA),
            ("dev-env-v2-portal-postgres-ca", "postgres-ca.crt", POSTGRES_CA),
        ):
            doc = self.by_key[("ConfigMap", name)]
            self.assertEqual(set(doc["data"]), {"ca.crt"}, name)
            self.assertNotIn("binaryData", doc)
            raw = doc["data"]["ca.crt"].encode()
            self.assertEqual(raw, (PORTAL_INPUTS / filename).read_bytes(), name)
            self.assertLessEqual(len(raw), 64 << 10, name)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), file_sha, name)
            self.assertEqual(doc["metadata"]["annotations"], {
                "dev-env.haynesops.com/ca-input": "public-only",
                "dev-env.haynesops.com/ca-sha256": file_sha,
            }, name)
            ders = pem_certificates(doc["data"]["ca.crt"])
            self.assertEqual([hashlib.sha256(der).hexdigest() for der in ders], der_shas, name)
            for der in ders:
                self.assertTrue(is_ca_certificate(der), name)
        with self.assertRaises(ValueError):
            pem_certificates("-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")
        with self.assertRaises(ValueError):
            pem_certificates("comment\n" + (PORTAL_INPUTS / "openproject-ca.crt").read_text())

    def test_runtime_external_secret_is_the_reviewed_projection(self):
        doc = self.by_key[("ExternalSecret", "dev-env-v2-portal-runtime")]
        self.assertEqual(approved_portal_runtime_violations(PORTAL_RUNTIME, doc), [])
        self.assertEqual(doc["spec"], load(PENDING / "runtime-externalsecret.yaml")["spec"])
        self.assertEqual(doc, self.packet[("ExternalSecret", "dev-env-v2-portal-runtime")])
        self.assertTrue(approved_portal_runtime_violations(PORTAL_RUNTIME.with_name("other.yaml"), doc))
        self.assertEqual(dev_env_properties_from_documents([(PORTAL_RUNTIME, doc)]), set())
        self.assertEqual(dev_env_properties_from_documents([(PORTAL_RUNTIME.with_name("other.yaml"), doc)]),
                         set(RUNTIME_FIELDS))
        # DB_USERNAME and DB_PASSWORD come from the same item properties the role reads.
        db = load(PORTAL_DB)
        self.assertEqual(
            [item["remoteRef"] for item in db["spec"]["data"]],
            [item["remoteRef"] for item in doc["spec"]["data"] if item["secretKey"] in DB_FIELDS],
        )
        # The OIDC Secret stays with its existing owner; it is not duplicated here.
        self.assertNotIn(("ExternalSecret", "dev-env-v2-portal-oidc"), self.by_key)
        openproject = self.by_key[("ExternalSecret", "dev-env-v2-portal-openproject")]
        self.assertEqual(approved_portal_openproject_violations(PORTAL_OPENPROJECT, openproject), [])
        self.assertTrue(approved_portal_openproject_violations(PORTAL_RUNTIME, openproject))
        self.assertEqual(dev_env_properties_from_documents([(PORTAL_OPENPROJECT, openproject)]), set())

    def test_deployment_mounts_exactly_what_the_portal_reads(self):
        deployment = self.by_key[("Deployment", "dev-env-v2-portal")]
        self.assertEqual(deployment_violations(deployment, self.docs), [])
        self.assertEqual(expand_config_files(mounted_files(deployment), self.docs), PRODUCTION_FILES)
        # Every source the pod needs is registered here, by dev-env-secrets or by dev-env-pki,
        # and OPENPROJECT_TOKEN is mounted optional but now registered too.
        oidc = load(PORTAL_OIDC)
        self.assertEqual((oidc["metadata"]["namespace"], oidc["spec"]["target"]["name"]),
                         ("dev-agents", "dev-env-v2-portal-oidc"))
        registered = {doc["metadata"]["name"] for doc in self.docs if doc["kind"] == "ConfigMap"}
        registered |= {doc["spec"]["target"]["name"] for doc in self.docs if doc["kind"] == "ExternalSecret"}
        registered |= {doc["spec"]["secretName"] for doc in self.docs if doc["kind"] == "Certificate"}
        registered |= {"dev-env-v2-portal-oidc", "dev-env-api-ca"}
        for path, (kind, name, _) in PRODUCTION_FILES.items():
            if kind in {"ConfigMap", "Secret"}:
                self.assertIn(name, registered, path)
            elif kind == "Secret?":
                self.assertEqual(path, "secrets/OPENPROJECT_TOKEN")
                self.assertIn(name, registered)
        # The operator's CA is dev-env-pki's pinned dev-agents/dev-env-api-ca, not a copy.
        pki = load(ROOT / "kubernetes/main/apps/dev-env-system/pki/app/kustomization.yaml")
        self.assertIn({"name": "dev-env-api-ca", "namespace": "dev-agents", "files": ["ca.crt"]},
                      pki["configMapGenerator"])
        self.assertNotIn("dev-env-v2-portal-operator-ca", self.rendered)
        # Reloader restarts the portal when any mounted input changes.
        annotations = deployment["metadata"]["annotations"]
        reload = set(annotations["configmap.reloader.stakater.com/reload"].split(","))
        reload |= set(annotations["secret.reloader.stakater.com/reload"].split(","))
        self.assertEqual(reload, {name for _, name, _ in PRODUCTION_FILES.values()} - {"dev-env-operator"})
        # The ServiceAccount is dev-env-rbac's; the operator reader names the same one.
        # It is the file's only object: no witness Role or RoleBinding.
        rbac = [doc for doc in yaml.safe_load_all(
            (ROOT / "kubernetes/main/apps/dev-env-system/rbac/app/portal-serviceaccount.yaml").read_text()) if doc]
        self.assertEqual([(d["kind"], d["metadata"]["namespace"], d["metadata"]["name"]) for d in rbac],
                         [("ServiceAccount", "dev-agents", "dev-env-v2-portal")])
        self.assertEqual(deployment["spec"]["template"]["spec"]["serviceAccountName"], "dev-env-v2-portal")
        # The probes reach /healthz and /readyz over TLS with the portal's own Host.
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        for probe, path in (("startupProbe", "/healthz"), ("livenessProbe", "/healthz"), ("readinessProbe", "/readyz")):
            self.assertEqual(container[probe]["httpGet"], {
                "path": path, "port": "https", "scheme": "HTTPS",
                "httpHeaders": [{"name": "Host", "value": PORTAL_HOST}]}, probe)
        # The image is one line, so a release bump touches one line.
        self.assertEqual(PORTAL_DEPLOYMENT.read_text().count("ghcr.io/thaynes43/dev-env-portal"), 1)
        # The Service and the policy select the Deployment's pods.
        service = self.by_key[("Service", "dev-env-v2-portal")]
        self.assertEqual(service["spec"]["selector"], deployment["spec"]["template"]["metadata"]["labels"])
        policy = load(POLICY_KS.parent / "app/networkpolicy.yaml")
        self.assertEqual(policy["spec"]["endpointSelector"]["matchLabels"],
                         deployment["spec"]["template"]["metadata"]["labels"])

    def test_deployment_guard_rejects_drift(self):
        def mutated(change):
            docs = deepcopy(self.docs)
            change(next(d for d in docs if d["kind"] == "Deployment"))
            return registered_input_violations(docs)

        pod = lambda d: d["spec"]["template"]["spec"]
        container = lambda d: pod(d)["containers"][0]
        volume = lambda d, name: next(v for v in pod(d)["volumes"] if v["name"] == name)
        for change, expected in (
            (lambda d: container(d).update(image="ghcr.io/thaynes43/dev-env-portal:latest"), "image"),
            (lambda d: container(d).update(image="ghcr.io/other/dev-env-portal:sha-b98247c@sha256:" + "0" * 64), "image"),
            (lambda d: pod(d).update(automountServiceAccountToken=True), "service-account"),
            (lambda d: pod(d).update(serviceAccountName="default"), "service-account"),
            (lambda d: pod(d).update(hostNetwork=True), "host-namespace"),
            (lambda d: pod(d).pop("dnsConfig"), "dns"),
            (lambda d: pod(d).update(dnsPolicy="Default"), "dns"),
            (lambda d: container(d)["securityContext"].update(readOnlyRootFilesystem=False), "security-context"),
            (lambda d: container(d)["securityContext"]["capabilities"].update(add=["NET_ADMIN"]), "security-context"),
            (lambda d: container(d)["resources"]["limits"].pop("cpu"), "cpu-limit"),
            (lambda d: container(d).update(env=[{"name": "X", "value": "y"}]), "command-or-env"),
            (lambda d: container(d).update(args=["serve"]), "command-or-env"),
            (lambda d: d["spec"]["template"]["metadata"]["labels"].update(extra="x"), "labels"),
            # A file the parser needs goes missing or moves.
            (lambda d: volume(d, "ca")["projected"]["sources"].pop(), "mounted-files"),
            (lambda d: volume(d, "tls")["secret"]["items"].append({"key": "ca.crt", "path": "ca.crt"}), "mounted-files"),
            (lambda d: volume(d, "secrets")["projected"]["sources"][1]["secret"].update(optional=True), "mounted-files"),
            (lambda d: volume(d, "secrets")["projected"]["sources"][2]["secret"].pop("optional"), "mounted-files"),
            (lambda d: volume(d, "secrets")["projected"]["sources"][3]["serviceAccountToken"].update(
                audience="https://kubernetes.default.svc"), "mounted-files"),
            (lambda d: container(d)["volumeMounts"][2].update(subPath="ca.crt"), "mounted-files"),
            # The CA key Secret, an admission input, or host storage.
            (lambda d: volume(d, "tls")["secret"].update(secretName="dev-env-v2-portal-ca"), "ca-key-mounted"),
            (lambda d: volume(d, "ca")["projected"]["sources"].append(
                {"configMap": {"name": "dev-env-v2-portal-admission-ca"}}), "admission-input"),
            (lambda d: pod(d)["volumes"].append({"name": "host", "hostPath": {"path": "/"}}), "volume-type:host"),
        ):
            self.assertIn("deployment:" + expected, mutated(change), expected)

    def test_route_is_internal_only(self):
        route = self.by_key[("IngressRoute", "dev-env-v2-portal")]
        self.assertEqual(route_violations(route), [])
        self.assertEqual(route["metadata"]["namespace"], "dev")
        self.assertTrue(PORTAL_HOST.endswith(".haynesops.com"))
        # Internal DNS: only the UniFi external-dns writes haynesops.com; Cloudflare excludes it.
        dns = ROOT / "kubernetes/main/apps/network/external-dns"
        cloudflare = load(dns / "cloudflare/helmrelease.yaml")["spec"]["values"]
        unifi = load(dns / "unifi/helmrelease.yaml")["spec"]["values"]
        self.assertIn("haynesops.com", cloudflare["excludeDomains"])
        self.assertNotIn("haynesops.com", cloudflare["domainFilters"])
        self.assertEqual(unifi["domainFilters"], ["haynesops.com"])
        # Internal Traefik only: the external instance serves only its own class.
        traefik = ROOT / "kubernetes/main/apps/network/traefik"
        external = load(traefik / "traefik-external/app/helmrelease.yaml")["spec"]["values"]["providers"]
        internal = load(traefik / "traefik-internal/app/helmrelease.yaml")["spec"]["values"]["providers"]
        self.assertEqual(external["kubernetesCRD"]["ingressClass"], "traefik-external")
        self.assertEqual(internal["kubernetesCRD"]["ingressClass"], "traefik-internal")
        # The cross-namespace Service needs it on the internal instance.
        self.assertIs(internal["kubernetesCRD"]["allowCrossNamespace"], True)
        # The wildcard certificate is reflected to dev and not to dev-agents.
        wildcard = load(ROOT / "kubernetes/main/apps/cert-manager/certificates/certificates-letsencrypt-wildcard"
                        "/certificate-haynesops-prod.yaml")
        reflected = wildcard["spec"]["secretTemplate"]["annotations"][
            "reflector.v1.k8s.emberstack.com/reflection-allowed-namespaces"].split(",")
        self.assertIn("dev", reflected)
        # No tunnel, public route or other manifest names the portal host or Service.
        for path in (ROOT / "kubernetes").rglob("*.y*ml"):
            text = path.read_text(errors="replace")
            if PORTAL_HOST in text:
                self.assertIn(path.resolve(), {
                    PORTAL_ROUTE.resolve(), PORTAL_DEPLOYMENT.resolve(), (PORTAL_INPUTS / "config.yaml").resolve(),
                    (PORTAL_INPUTS / "certificates.yaml").resolve(),
                    (ROOT / "kubernetes/main/apps/network/authentik/app/blueprints/97-dev-env-v2-portal-oidc.yaml").resolve(),
                }, str(path))
            if "cloudflare-tunnel" in str(path):
                self.assertNotIn("dev-env-v2", text, str(path))
            is_route = re.search(r"kind:\s*(IngressRoute|Ingress|HTTPRoute)\b", text)
            if is_route and "dev-env-v2-portal" in text and path.resolve() != PORTAL_ROUTE.resolve():
                self.fail("another route names the portal in " + str(path))
        # Traefik checks the backend with the portal's own CA (Service namespace lookup).
        self.assertEqual(unqualified_transport_lookup(route), ("dev-agents", "dev-env-v2-portal"))
        transport = self.by_key[("ServersTransport", "dev-env-v2-portal")]
        self.assertEqual(transport["metadata"]["namespace"], "dev-agents")
        self.assertIs(transport["spec"]["insecureSkipVerify"], False)

    def test_route_guard_rejects_public_exposure(self):
        def mutated(change):
            docs = deepcopy(self.docs)
            change(next(d for d in docs if d["kind"] == "IngressRoute"))
            return registered_input_violations(docs)

        annotations = lambda d: d["metadata"]["annotations"]
        service = lambda d: d["spec"]["routes"][0]["services"][0]
        for change, expected in (
            (lambda d: annotations(d).update({"kubernetes.io/ingress.class": "traefik-external"}), "route:ingress-class"),
            (lambda d: annotations(d).pop("kubernetes.io/ingress.class"), "route:ingress-class"),
            (lambda d: annotations(d).update({"external-dns.kubernetes.io/target": "external.haynesnetwork.com"}),
             "route:dns-target"),
            (lambda d: annotations(d).update({"external-dns.kubernetes.io/cloudflare-proxied": "true"}), "route:annotations"),
            (lambda d: d["spec"]["routes"][0].update(match="Host(`dev-env-v2.haynesnetwork.com`)"), "route:match"),
            (lambda d: d["spec"]["routes"][0].update(match="HostRegexp(`.+`)"), "route:match"),
            (lambda d: d["spec"].update(entryPoints=["websecure", "web"]), "route:entrypoints"),
            (lambda d: d["spec"].update(tls={"secretName": "certificate-haynesnetwork"}), "route:tls"),
            (lambda d: d["spec"]["routes"][0].update(middlewares=[{"name": "default-headers", "namespace": "network"}]),
             "route:middlewares"),
            (lambda d: service(d).pop("serversTransport"), "route:backend"),
            (lambda d: service(d).update(scheme="http"), "route:backend"),
            (lambda d: d["spec"]["routes"].append(deepcopy(d["spec"]["routes"][0])), "route:routes"),
            (lambda d: d["metadata"].update(namespace="dev-agents"), "namespace:dev-env-v2-portal"),
        ):
            self.assertIn(expected, mutated(change), expected)
        exposed = deepcopy(self.docs)
        next(d for d in exposed if d["kind"] == "Service")["spec"]["type"] = "LoadBalancer"
        self.assertIn("service-type", registered_input_violations(exposed))

    def test_checklist_records_the_activation_steps(self):
        checklist = CHECKLIST.read_text()
        self.assertIn("## Activation steps S4+S6: portal inputs, 2026-10-26", checklist)
        self.assertIn("## Activation steps S8+S9: portal Deployment and internal route, 2026-10-26", checklist)
        for needle in ("internal.haynesops", "traefik-internal", "OPENPROJECT_TOKEN", "dev-env#560"):
            self.assertIn(needle, checklist)
        for needle in (ISSUER_CA[0], OPENPROJECT_CA[0], POSTGRES_CA[0], "rootCAsSecrets",
                       "dev-env-api-ca", "openproject-portal-ca-1", "so the renewal needs no re-pin", "dropped the admission witness"):
            self.assertIn(needle, checklist)


if __name__ == "__main__":
    unittest.main()
