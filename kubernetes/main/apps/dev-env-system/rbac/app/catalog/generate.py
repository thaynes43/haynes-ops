#!/usr/bin/env python3
"""Explicit dev-env break-glass RBAC from Kubernetes discovery, never object data.

Capture/render/check-live use only Python's standard library. The optional
--crd-root CI coverage check and offline sync-crds command import PyYAML to read
repo-managed CRD manifests.
"""

from __future__ import annotations

import argparse
import collections
import difflib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request


DIRECTORY = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT = DIRECTORY / "discovery.json"
DEFAULT_ROLE = DIRECTORY.parent / "grant-breakglass.yaml"
ROLE_NAME = "dev-env-grant-breakglass"
SCHEMA_VERSION = 1
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_DISCOVERY_SECONDS = 240
ALLOWED_VERBS = frozenset(
    {"get", "list", "watch", "create", "update", "patch", "delete", "deletecollection"}
)
EXCLUDED_VERBS = frozenset({"bind", "escalate", "impersonate"})
EXCLUDED_GROUPS = frozenset(
    {
        "rbac.authorization.k8s.io",
        "admissionregistration.k8s.io",
        "apiextensions.k8s.io",
        "apiregistration.k8s.io",
        "dev-env.haynesops.com",
        "kyverno.io",
        "external-secrets.io",
        "fluxcd.io",
        "flowcontrol.apiserver.k8s.io",
    }
)
EXCLUDED_RESOURCES = {
    "": frozenset(
        {
            "secrets",
            "serviceaccounts/token",
            "nodes/proxy",
            "pods/ephemeralcontainers",
            # These bypass Cilium/ingress and lack the privileged-target lookup that
            # dev-env-exec-guard applies to exec AND attach. Fail closed until an
            # equivalent admission guard is deliberately designed and verified.
            "pods/portforward",
            "pods/proxy",
            "services/proxy",
        }
    ),
    "certificates.k8s.io": frozenset({"certificatesigningrequests/approval"}),
}
CRD_RESOURCE_VERBS = tuple(sorted(ALLOWED_VERBS))
CRD_SUBRESOURCE_VERBS = ("get", "patch", "update")
GROUP_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)?\Z")
RESOURCE_RE = re.compile(r"[a-z0-9][a-z0-9.-]*(?:/[a-z0-9][a-z0-9.-]*)*\Z")
VERSION_RE = re.compile(r"v[0-9]+(?:(?:alpha|beta)[0-9]+)?\Z")


class CatalogError(Exception):
    """A bounded, safe error suitable for CI and Job logs."""


def _strings(value, label: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise CatalogError(f"{label} must be a non-empty string array")
    return sorted(set(value))


def normalize(lists: list[dict]) -> dict:
    """Union metadata across every served version; omit hashes and cluster addresses."""
    resources = {}
    for listing in lists:
        if not isinstance(listing, dict):
            raise CatalogError("discovery response must be an object")
        group_version = listing.get("groupVersion", "")
        if not isinstance(group_version, str):
            raise CatalogError("discovery groupVersion must be a string")
        parts = group_version.split("/")
        group, version = ("", parts[0]) if len(parts) == 1 else tuple(parts) if len(parts) == 2 else (None, None)
        if group is None or not GROUP_RE.fullmatch(group) or not VERSION_RE.fullmatch(version):
            raise CatalogError("invalid discovery groupVersion")
        entries = listing.get("resources")
        if not isinstance(entries, list):
            raise CatalogError(f"missing resources for {group_version}")
        for entry in entries:
            if not isinstance(entry, dict):
                raise CatalogError(f"invalid resource entry in {group_version}")
            name = entry.get("name", "")
            kind = entry.get("kind", "")
            if not isinstance(name, str) or not RESOURCE_RE.fullmatch(name):
                raise CatalogError(f"invalid resource name in {group_version}")
            if not isinstance(kind, str) or not kind or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", kind):
                raise CatalogError(f"invalid resource kind in {group_version}")
            namespaced = entry.get("namespaced")
            if not isinstance(namespaced, bool):
                raise CatalogError(f"missing namespace scope for {group_version}/{name}")
            verbs = _strings(entry.get("verbs"), f"verbs for {group_version}/{name}")
            unknown = set(verbs) - ALLOWED_VERBS - EXCLUDED_VERBS
            if unknown:
                raise CatalogError(f"unknown discovery verbs for {group_version}/{name}: {', '.join(sorted(unknown))}")
            key = (group, name)
            if key not in resources:
                resources[key] = {
                    "apiGroup": group,
                    "resource": name,
                    "namespaced": namespaced,
                    "versions": set(),
                    "kinds": set(),
                    "verbs": set(),
                }
            record = resources[key]
            if record["namespaced"] != namespaced:
                raise CatalogError(f"conflicting namespace scopes for {group}/{name}")
            record["versions"].add(version)
            record["kinds"].add(kind)
            record["verbs"].update(verbs)
    if not resources:
        raise CatalogError("discovery has no resources")
    records = []
    for key in sorted(resources):
        record = resources[key]
        for field in ("versions", "kinds", "verbs"):
            record[field] = sorted(record[field])
        records.append(record)
    return {"schemaVersion": SCHEMA_VERSION, "resources": records}


def validate_snapshot(snapshot: dict) -> dict:
    if not isinstance(snapshot, dict) or set(snapshot) != {"schemaVersion", "resources"}:
        raise CatalogError("invalid snapshot fields")
    if snapshot["schemaVersion"] != SCHEMA_VERSION or not isinstance(snapshot["resources"], list):
        raise CatalogError("unsupported discovery snapshot schema")
    listings = collections.defaultdict(list)
    seen = set()
    for record in snapshot["resources"]:
        fields = {"apiGroup", "resource", "namespaced", "versions", "kinds", "verbs"}
        if not isinstance(record, dict) or set(record) != fields:
            raise CatalogError("invalid discovery snapshot resource fields")
        group = record["apiGroup"]
        name = record["resource"]
        if not isinstance(group, str) or not isinstance(name, str):
            raise CatalogError("snapshot group/resource must be strings")
        if (group, name) in seen:
            raise CatalogError("duplicate discovery snapshot resource")
        seen.add((group, name))
        versions = _strings(record["versions"], "snapshot versions")
        kinds = _strings(record["kinds"], "snapshot kinds")
        for version in versions:
            gv = f"{group}/{version}" if group else version
            for kind in kinds:
                listings[gv].append({"name": name, "kind": kind, "namespaced": record["namespaced"], "verbs": record["verbs"]})
    result = normalize([{"groupVersion": gv, "resources": resources} for gv, resources in sorted(listings.items())])
    if snapshot != result:
        raise CatalogError("snapshot is not canonically sorted and deduplicated")
    return result


def excluded(group: str, resource: str) -> bool:
    if group in EXCLUDED_GROUPS or group.endswith((".fluxcd.io", ".external-secrets.io", ".kyverno.io")):
        return True
    # Data-plane identity, node/IPAM, redirects and shared CIDR/LB configuration
    # bypass namespace policy just as directly as the excluded cluster-wide policy.
    # Fail closed for future Cilium resources; permit only the namespaced policy API.
    if group == "cilium.io":
        return resource != "ciliumnetworkpolicies" and not resource.startswith("ciliumnetworkpolicies/")
    return any(resource == blocked or resource.startswith(blocked + "/") for blocked in EXCLUDED_RESOURCES.get(group, ()))


def role(snapshot: dict) -> dict:
    buckets = collections.defaultdict(list)
    for record in validate_snapshot(snapshot)["resources"]:
        group, resource = record["apiGroup"], record["resource"]
        if excluded(group, resource) or (group == "cilium.io" and not record["namespaced"]):
            continue
        verbs = tuple(v for v in record["verbs"] if v in ALLOWED_VERBS)
        if verbs:
            buckets[(group, verbs)].append(resource)
    if not buckets:
        raise CatalogError("break-glass role has no permitted resources")
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRole",
        "metadata": {
            "name": ROLE_NAME,
            "labels": {"dev-env.haynesops.com/grant-role": "true"},
            "annotations": {"dev-env.haynesops.com/catalog-source": "API discovery and declared repo CRDs minus DESIGN-001 D-27 exclusions; see catalog/generate.py"},
        },
        "rules": [{"apiGroups": [group], "resources": sorted(resources), "verbs": list(verbs)} for (group, verbs), resources in sorted(buckets.items())],
    }


def serialized(value: dict) -> str:
    # JSON is valid YAML. Avoid a runtime dependency in the discovery Job.
    return json.dumps(value, indent=2, ensure_ascii=True) + "\n"


class DiscoveryReader:
    def __init__(self, in_cluster: bool):
        self.deadline = time.monotonic() + MAX_DISCOVERY_SECONDS
        self.in_cluster = in_cluster
        if in_cluster:
            base = Path("/var/run/secrets/kubernetes.io/serviceaccount")
            self.token = (base / "token").read_text().strip()
            self.context = ssl.create_default_context(cafile=str(base / "ca.crt"))
            host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
            port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
            if not host or not port.isdecimal():
                raise CatalogError("missing Kubernetes service address")
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            self.base_url = f"https://{host}:{port}"
            # Never follow redirects with the projected bearer token.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None
            self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=self.context))

    def __call__(self, path: str) -> dict:
        discovery_path = (
            path in {"/api", "/apis"}
            or re.fullmatch(r"/api/v[0-9]+(?:(?:alpha|beta)[0-9]+)?", path)
            or re.fullmatch(r"/apis/[a-z0-9][a-z0-9.-]*/v[0-9]+(?:(?:alpha|beta)[0-9]+)?", path)
        )
        if not discovery_path:
            raise CatalogError("refused a non-discovery request path")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CatalogError("discovery exceeded its total deadline")
        timeout = min(5, remaining)
        try:
            if self.in_cluster:
                request = urllib.request.Request(self.base_url + path, headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"})
                with self.opener.open(request, timeout=timeout) as response:
                    payload = response.read(MAX_RESPONSE_BYTES + 1)
            else:
                # An explicit kubectl request-timeout flag disables its default
                # in-cluster fallback when no kubeconfig exists. Bound the entire
                # child process instead, preserving the operator's auth context.
                completed = subprocess.run(["kubectl", "get", "--raw", path], capture_output=True, timeout=timeout, check=False)
                if completed.returncode:
                    raise CatalogError(f"discovery GET failed: {path}")
                payload = completed.stdout
            if len(payload) > MAX_RESPONSE_BYTES:
                raise CatalogError(f"discovery response too large: {path}")
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise CatalogError(f"invalid discovery response: {path}")
            return data
        except (urllib.error.URLError, TimeoutError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            # Do not include HTTP bodies, command stderr, headers or credentials.
            raise CatalogError(f"discovery GET failed: {path} ({type(error).__name__})") from None


def discover(read) -> dict:
    core = read("/api")
    groups = read("/apis")
    versions = _strings(core.get("versions"), "core versions")
    paths = []
    for version in versions:
        if not VERSION_RE.fullmatch(version):
            raise CatalogError("invalid core discovery version")
        paths.append((version, f"/api/{version}"))
    entries = groups.get("groups")
    if not isinstance(entries, list) or len(entries) > 1000:
        raise CatalogError("invalid API group discovery")
    for group in entries:
        if not isinstance(group, dict) or not isinstance(group.get("name"), str) or not group["name"] or not GROUP_RE.fullmatch(group["name"]):
            raise CatalogError("invalid API group name")
        if not isinstance(group.get("versions"), list) or not group["versions"]:
            raise CatalogError("API group has no served versions")
        for version in group["versions"]:
            if not isinstance(version, dict) or not isinstance(version.get("version"), str) or not VERSION_RE.fullmatch(version["version"]):
                raise CatalogError("invalid served API version")
            gv = f"{group['name']}/{version['version']}"
            if version.get("groupVersion") != gv:
                raise CatalogError("inconsistent served API groupVersion")
            paths.append((gv, f"/apis/{gv}"))
    lists = []
    for expected, path in sorted(set(paths)):
        listing = read(path)
        if listing.get("groupVersion") != expected:
            raise CatalogError(f"discovery groupVersion mismatch: {path}")
        lists.append(listing)
    return normalize(lists)


def repo_crds(root: Path) -> list[tuple[Path, dict]]:
    """Read explicit repo CRDs, never embedded blueprints or object instances."""
    try:
        import yaml
    except ImportError:
        raise CatalogError("repo CRD coverage/sync needs PyYAML; CI installs its pinned version") from None
    if not root.is_dir():
        raise CatalogError("repo CRD root is not a directory")
    declarations = []
    declared_identities = {}
    for path in sorted(root.rglob("*.yaml")) + sorted(root.rglob("*.yml")):
        contents = path.read_text()
        # Skip embedded Authentik blueprints and ordinary Kubernetes manifests.
        if not re.search(r"^kind:\s*CustomResourceDefinition\s*$", contents, re.MULTILINE):
            continue
        try:
            documents = list(yaml.safe_load_all(contents))
        except yaml.YAMLError:
            raise CatalogError(f"cannot parse repo CRD manifest: {path}") from None
        for document in documents:
            if not isinstance(document, dict) or document.get("kind") != "CustomResourceDefinition":
                continue
            spec = document.get("spec", {})
            if not isinstance(spec, dict) or not isinstance(spec.get("names"), dict) or spec.get("scope") not in {"Namespaced", "Cluster"}:
                raise CatalogError(f"invalid repo CRD metadata: {path}")
            group, name, kind = spec.get("group"), spec["names"].get("plural"), spec["names"].get("kind")
            versions = spec.get("versions")
            if not isinstance(group, str) or not group or not isinstance(versions, list) or not versions:
                raise CatalogError(f"invalid repo CRD group/versions: {path}")
            seen_versions = set()
            for version in versions:
                if not isinstance(version, dict) or not isinstance(version.get("name"), str) or not VERSION_RE.fullmatch(version["name"]) or not isinstance(version.get("served"), bool):
                    raise CatalogError(f"invalid repo CRD version: {path}")
                if version["name"] in seen_versions:
                    raise CatalogError(f"duplicate repo CRD version: {path}")
                seen_versions.add(version["name"])
                subresources = version.get("subresources", {})
                if not isinstance(subresources, dict) or set(subresources) - {"status", "scale"}:
                    raise CatalogError(f"unknown repo CRD subresource: {path}")
                # Reuse the strict discovery field validation for declared metadata.
                normalize([{"groupVersion": f"{group}/{version['name']}", "resources": [{"name": name, "kind": kind, "namespaced": spec["scope"] == "Namespaced", "verbs": list(CRD_RESOURCE_VERBS)}]}])
            if not any(v["served"] for v in versions):
                raise CatalogError(f"repo CRD has no served version: {path}")
            identity = (group, name)
            metadata = (spec["scope"], kind)
            if identity in declared_identities and declared_identities[identity] != metadata:
                raise CatalogError(f"conflicting repo CRD scope/kind: {group}/{name} ({path})")
            declared_identities[identity] = metadata
            declarations.append((path, spec))
    return declarations


def sync_crds(snapshot: dict, root: Path) -> dict:
    """Add declarations before deploy; preserve all captured records and metadata.

    Existing scope/kind conflicts fail rather than rewriting live-derived metadata.
    Retiring versions is a capture-after-deploy operation, never a silent prune.
    """
    snapshot = validate_snapshot(snapshot)
    indexed = {(r["apiGroup"], r["resource"]): r for r in snapshot["resources"]}
    lists = []
    # Reconstitute existing metadata without dropping kinds, versions or verbs.
    for record in snapshot["resources"]:
        for version in record["versions"]:
            gv = f"{record['apiGroup']}/{version}" if record["apiGroup"] else version
            lists.append({"groupVersion": gv, "resources": [{"name": record["resource"], "kind": kind, "namespaced": record["namespaced"], "verbs": record["verbs"]} for kind in record["kinds"]]})
    for path, spec in repo_crds(root):
        group, name, kind = spec["group"], spec["names"]["plural"], spec["names"]["kind"]
        namespaced = spec["scope"] == "Namespaced"
        for version in spec["versions"]:
            if not version["served"]:
                continue
            resources = [(name, kind, CRD_RESOURCE_VERBS)]
            resources.extend((f"{name}/{subresource}", "Scale" if subresource == "scale" else kind, CRD_SUBRESOURCE_VERBS) for subresource in sorted(version.get("subresources", {})))
            entries = []
            for resource, resource_kind, standard_verbs in resources:
                captured = indexed.get((group, resource))
                if captured and (captured["namespaced"] != namespaced or resource_kind not in captured["kinds"]):
                    raise CatalogError(f"repo CRD conflicts with captured scope/kind: {group}/{resource} ({path})")
                entries.append({"name": resource, "kind": resource_kind, "namespaced": namespaced, "verbs": captured["verbs"] if captured else list(standard_verbs)})
            lists.append({"groupVersion": f"{group}/{version['name']}", "resources": entries})
    return normalize(lists)


def check_crds(snapshot: dict, root: Path):
    indexed = {(r["apiGroup"], r["resource"]): r for r in snapshot["resources"]}
    declarations = repo_crds(root)
    for path, spec in declarations:
        group, name = spec["group"], spec["names"]["plural"]
        record = indexed.get((group, name))
        served = {v["name"] for v in spec.get("versions", []) if v.get("served")}
        if not record or not served or not served.issubset(record["versions"]) or record["namespaced"] != (spec.get("scope") == "Namespaced") or spec.get("names", {}).get("kind") not in record["kinds"]:
            raise CatalogError(f"repo CRD is missing/stale in discovery: {group}/{name} ({path}); run sync-crds --crd-root {root} before deployment, then render")
        for version in spec.get("versions", []):
            if not version.get("served"):
                continue
            for subresource in version.get("subresources", {}):
                subrecord = indexed.get((group, f"{name}/{subresource}"))
                expected_kind = "Scale" if subresource == "scale" else spec["names"]["kind"]
                if not subrecord or version["name"] not in subrecord["versions"] or subrecord["namespaced"] != (spec["scope"] == "Namespaced") or expected_kind not in subrecord["kinds"]:
                    raise CatalogError(f"repo CRD subresource/version is missing in discovery: {group}/{name}/{subresource}/{version['name']}; run sync-crds --crd-root {root} before deployment, then render")
    print(f"repo-managed CRD coverage passed ({len(declarations)} CRDs)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "sync-crds", "render", "check", "check-live"))
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--role", type=Path, default=DEFAULT_ROLE)
    parser.add_argument("--in-cluster", action="store_true")
    parser.add_argument("--crd-root", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            snapshot = discover(DiscoveryReader(args.in_cluster))
            args.snapshot.write_text(serialized(snapshot))
            print(f"captured {len(snapshot['resources'])} discovered resources; no object data")
            return 0
        snapshot = validate_snapshot(json.loads(args.snapshot.read_text()))
        if args.command == "sync-crds":
            if not args.crd_root:
                raise CatalogError("sync-crds requires --crd-root; it reads local manifests only")
            merged = sync_crds(snapshot, args.crd_root)
            args.snapshot.write_text(serialized(merged))
            print(f"synchronized repo CRDs offline ({len(merged['resources']) - len(snapshot['resources'])} new resources); captured metadata preserved")
            return 0
        if args.command == "check-live":
            live = discover(DiscoveryReader(args.in_cluster))
            if live != snapshot:
                # Metadata only; bounded so group churn cannot flood Job logs.
                diff = list(difflib.unified_diff(serialized(snapshot).splitlines(), serialized(live).splitlines(), fromfile="checked-in discovery", tofile="live discovery", lineterm=""))
                print("\n".join(diff[:160]))
                if len(diff) > 160:
                    print(f"... {len(diff) - 160} additional diff lines omitted")
                raise CatalogError("API discovery drift; refresh snapshot and generated role through a reviewed haynes-ops PR")
            print(f"live discovery matches ({len(snapshot['resources'])} resources)")
            return 0
        generated = serialized(role(snapshot))
        if args.command == "render":
            args.role.write_text(generated)
            print(f"generated {ROLE_NAME} with explicit resources and verbs")
        elif args.role.read_text() != generated:
            raise CatalogError("generated role differs; run generate.py render and inspect the diff")
        else:
            print("generated break-glass role is current and obeys the exclusions")
        if args.crd_root:
            if not args.crd_root.is_dir():
                raise CatalogError("repo CRD root is not a directory")
            check_crds(snapshot, args.crd_root)
        return 0
    except (CatalogError, OSError, json.JSONDecodeError) as error:
        print(f"grant catalog: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
