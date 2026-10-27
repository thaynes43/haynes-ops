#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML==6.0.3", "jsonschema==4.25.1"]
# ///
"""Offline configuration guard, NOT an identity integration test.

Uses the deployed 2026.8.5 schema with synthetic typed FK placeholders. It does
not resolve real users/groups, execute policies, exercise the Authentik importer,
or prove token/MFA/logout behavior. Run via uv with --schema PATH; optional
--rendered PATH checks that Kustomize mounted exactly this blueprint.
"""
import argparse
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "kubernetes/main/apps/network/authentik/app"
BLUEPRINT = APP / "blueprints/97-dev-env-v2-portal-oidc.yaml"
PROJECTION = ROOT / ".agents/reports/portal178-m5a/pending/externalsecret.yaml"
SCHEMA_SHA = "b59476bcbaf92dc1a5bd31d98936d44348bc2b048deae7e891227597c74e97e8"
SYNTHETIC_UUID = "00000000-0000-4000-8000-000000000001"
PINNED_ADMINS_SHA256 = "88619af8c7c3e52efd2c0368020478809cc7a673617406cfd1bf584c9b1d75ee"
DIRECTORY_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass
class Ref:
    tag: str
    value: object


class SecurityViolation(ValueError):
    """An intentional guard rejection, distinct from parser/programming errors."""


class Loader(yaml.SafeLoader):
    pass


def require(condition, message):
    if not condition:
        raise SecurityViolation(message)


def mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        require(key not in result, "duplicate YAML key")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
Loader.add_constructor("!Find", lambda l, n: Ref("Find", l.construct_sequence(n, deep=True)))
Loader.add_constructor("!KeyOf", lambda l, n: Ref("KeyOf", l.construct_scalar(n)))


def find(model, *conditions):
    return Ref("Find", [model, *[list(c) for c in conditions]])


EXPECTED_FINDS = [
    find("authentik_flows.flow", ("slug", "default-provider-authorization-implicit-consent")),
    find("authentik_flows.flow", ("slug", "default-provider-invalidation-flow")),
    find("authentik_providers_oauth2.scopemapping",
         ("managed", "goauthentik.io/providers/oauth2/scope-openid")),
    find("authentik_providers_oauth2.scopemapping",
         ("managed", "goauthentik.io/providers/oauth2/scope-profile")),
    find("authentik_crypto.certificatekeypair", ("name", "jwt-signing-key-rsa-longterm")),
    find("authentik_core.group", ("name", "authentik Admins")),
]


def materialize(value, ids):
    if isinstance(value, Ref):
        if value.tag == "Find":
            require(value in EXPECTED_FINDS, "unexpected directory lookup")
            return SYNTHETIC_UUID
        require(value.tag == "KeyOf" and value.value in ids, "unresolved entry reference")
        model = ids[value.value]
        return 1 if model == "authentik_providers_oauth2.oauth2provider" else SYNTHETIC_UUID
    if isinstance(value, dict):
        return {k: materialize(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [materialize(v, ids) for v in value]
    return value


def validate(blueprint, projection, schema):
    require(set(blueprint) == {"version", "metadata", "context", "entries"},
            "blueprint fields changed")
    require(blueprint["version"] == 1 and blueprint["context"] == {}, "blueprint version/context changed")
    require(blueprint["metadata"] == {
        "name": "dev-env-v2-portal-oidc",
        "labels": {"blueprints.goauthentik.io/description":
            "Story178 internal portal OIDC: confidential code/openid/profile; access by authentik Admins group."}},
        "blueprint metadata changed")
    entries = blueprint["entries"]
    require(len(entries) == 5, "expected deny policy, portal mapping, provider, application and one binding")
    ids = {e["id"]: e["model"] for e in entries if "id" in e}
    require(len(ids) == 4, "duplicate or missing entry ID")
    for index, e in enumerate(entries):
        require(not e.get("conditions") and not e.get("permissions"),
                "entries must reconcile unconditionally without permissions")
        # Group access (2026-10-26): the deny (order 0) and single-user (order 10)
        # bindings were deleted by an earlier import; their absent entries are gone.
        require(e.get("state") == "present", "every entry present")
        require(set(e) == ({"model", "id", "state", "identifiers", "attrs"} if index < 4
                          else {"model", "state", "identifiers", "attrs"}), "entry fields changed")
        allowed = schema["definitions"]["model_" + e["model"]]["properties"]
        require(set(e["attrs"]) <= set(allowed) and set(e["identifiers"]) <= set(allowed),
                "unknown model attribute")
    policy, mapping_entry, provider, application, *bindings = entries
    require(len(bindings) == 1, "expected deny policy, portal mapping, provider, application and one binding")
    require(policy["model"] == "authentik_policies_expression.expressionpolicy" and
            policy["id"] == "portal-staged-deny" and
            policy["identifiers"] == {"name": "dev-env-v2-portal-staged-deny"} and
            policy["attrs"] == {"expression": "return False", "execution_logging": False},
            "staged deny must remain closed")
    require(mapping_entry["model"] == "authentik_providers_oauth2.scopemapping" and
            mapping_entry["id"] == "portal-live-profile" and
            mapping_entry["identifiers"] == {"name": "dev-env-v2-portal-live-profile"},
            "portal mapping identity changed")
    mapped = mapping_entry["attrs"]
    require(set(mapped) == {"scope_name", "description", "expression"},
            "portal mapping attributes changed")
    require(mapped["scope_name"] == "profile", "portal mapping scope changed")
    require(PINNED_ADMINS_SHA256 in mapped["expression"] and "portal_is_active" in mapped["expression"]
            and "portal_is_admin" in mapped["expression"], "portal mapping pin changed")
    require("current.groups.all()" in mapped["expression"]
            and 'sha256(str(group.pk).lower().encode("utf-8")).hexdigest()' in mapped["expression"]
            and re.search(r"(?m)^\s*direct_admin\s*=\s*active\b", mapped["expression"]) is None,
            "portal mapping must read direct membership")
    require(DIRECTORY_UUID.search(mapped["expression"]) is None, "raw directory UUID in mapping")
    require(provider["model"] == "authentik_providers_oauth2.oauth2provider" and
            provider["id"] == "portal-provider" and
            provider["identifiers"] == {"name": "dev-env-v2-portal-internal"},
            "provider identity changed")
    p = provider["attrs"]
    require(set(p) == {"client_type", "client_id", "grant_types", "jwt_federation_providers",
            "jwt_federation_sources", "authorization_flow", "invalidation_flow", "redirect_uris",
            "property_mappings", "signing_key", "sub_mode", "issuer_mode", "include_claims_in_id_token",
            "access_code_validity", "access_token_validity"}, "provider attribute allowlist changed")
    require(p["client_type"] == "confidential" and p["client_id"] == "dev-env-v2-portal-internal",
            "confidential approved client required")
    require(p["grant_types"] == ["authorization_code"], "code grant only")
    require(p["jwt_federation_providers"] == [] and p["jwt_federation_sources"] == [],
            "JWT federation must remain empty")
    require(p["include_claims_in_id_token"] is True and p["access_code_validity"] == "minutes=1" and
            p["access_token_validity"] == "minutes=5", "claims/token lifetimes changed")
    require(p["redirect_uris"] == [{"matching_mode": "strict",
            "url": "https://dev-env-v2.haynesops.com/auth/callback",
            "redirect_uri_type": "authorization"}], "exact callback required")
    require(p["property_mappings"] == [EXPECTED_FINDS[2], EXPECTED_FINDS[3], Ref("KeyOf", "portal-live-profile")],
            "exact three mappings required")
    require(p["authorization_flow"] == EXPECTED_FINDS[0] and
            p["invalidation_flow"] == EXPECTED_FINDS[1] and
            p["signing_key"] == EXPECTED_FINDS[4], "required existing reference changed")
    require(p["sub_mode"] == "user_uuid" and p["issuer_mode"] == "per_provider",
            "immutable subject and per-provider issuer required")
    require("client_secret" not in p and "authentication_flow" not in p,
            "no secret or shared flow override")
    require(application["model"] == "authentik_core.application" and
            application["id"] == "portal-application" and
            application["identifiers"] == {"slug": "dev-env-v2-portal"},
            "application identity changed")
    a = application["attrs"]
    require(set(a) == {"name", "provider", "policy_engine_mode", "meta_hide", "meta_description"},
            "application attribute allowlist changed")
    require(a["name"] == "Dev Env V2 Portal (internal)" and
            a["meta_description"] == "Internal read-only portal; runtime activation pending Gate A.",
            "application metadata changed")
    require(a["provider"] == Ref("KeyOf", "portal-provider") and
            a["policy_engine_mode"] == "all" and a["meta_hide"] is True and
            "meta_launch_url" not in a and not a.get("backchannel_providers"),
            "all-policy application without launch/backchannel required")
    # The one binding: the existing authentik Admins group. No user or policy binding.
    (b,) = bindings
    require(b["model"] == "authentik_policies.policybinding" and
            b["identifiers"] == {"target": Ref("KeyOf", "portal-application"), "order": 20} and
            b["attrs"] == {"group": EXPECTED_FINDS[5], "enabled": True, "negate": False, "failure_result": False},
            "exactly the existing Admins group binding required")

    require(set(projection) == {"apiVersion", "kind", "metadata", "spec"}, "projection fields changed")
    require(projection["metadata"] == {"name": "dev-env-v2-portal-oidc", "namespace": "dev-env-system"},
            "projection metadata changed")
    require(projection["apiVersion"] == "external-secrets.io/v1" and
            projection["kind"] == "ExternalSecret", "expected pending ExternalSecret")
    spec = projection["spec"]
    require(set(spec) == {"secretStoreRef", "refreshInterval", "target", "data"},
            "only explicit field projection; no dataFrom")
    require(spec["refreshInterval"] == "1h", "projection refresh changed")
    require(spec["secretStoreRef"] ==
            {"kind": "ClusterSecretStore", "name": "onepassword-connect"}, "dedicated store required")
    fields = ["OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"]
    # Supersession 2026-10-24: the item is existing HaynesKube/dev-env.
    # The prior key dev-env-v2-portal is rejected. Unrelated properties stay unprojected.
    require(spec["data"] == [{"secretKey": f, "remoteRef":
            {"key": "dev-env", "property": f}} for f in fields],
            "only canonical dev-env properties")
    target = spec["target"]
    require(set(target) == {"name", "creationPolicy", "deletionPolicy", "template"},
            "projection target fields changed")
    require(target["name"] == "dev-env-v2-portal-oidc" and target["creationPolicy"] == "Owner" and
            target["deletionPolicy"] == "Delete", "isolated owned target required")
    require(target["template"] == {"engineVersion": "v2", "mergePolicy": "Replace", "type": "Opaque",
            "data": {f: "{{ ." + f + " }}" for f in fields}}, "exact server Secret keys")
    normalized = materialize(blueprint, ids)
    jsonschema.Draft7Validator(schema, format_checker=jsonschema.FormatChecker()).validate(normalized)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--rendered", type=Path)
    args = parser.parse_args()
    raw = args.schema.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == SCHEMA_SHA, "unverified schema bytes")
    schema = json.loads(raw)
    bp = yaml.load(BLUEPRINT.read_text(), Loader=Loader)
    projection = yaml.load(PROJECTION.read_text(), Loader=Loader)
    validate(bp, projection, schema)

    # Mutations exercise the guard's rejection, not Authentik or an OIDC flow.
    mutations = [
        ("any-policy", "all-policy application without launch/backchannel required",
         lambda b, p: b["entries"][3]["attrs"].update(policy_engine_mode="any")),
        ("public-client", "confidential approved client required",
         lambda b, p: b["entries"][2]["attrs"].update(client_type="public")),
        ("extra-grant", "code grant only",
         lambda b, p: b["entries"][2]["attrs"]["grant_types"].append("refresh_token")),
        ("extra-scope", "exact three mappings required", lambda b, p: b["entries"][2]["attrs"]["property_mappings"].append(
            find("authentik_providers_oauth2.scopemapping",
                 ("managed", "goauthentik.io/providers/oauth2/scope-email")))),
        ("callback-wildcard", "exact callback required", lambda b, p: b["entries"][2]["attrs"]["redirect_uris"][0].update(
            matching_mode="regex")),
        ("username-sub", "immutable subject and per-provider issuer required",
         lambda b, p: b["entries"][2]["attrs"].update(sub_mode="user_username")),
        ("secret-in-source", "provider attribute allowlist changed",
         lambda b, p: b["entries"][2]["attrs"].update(client_secret="SYNTHETIC_CANARY")),
        ("missing-group", "exactly the existing Admins group binding required",
         lambda b, p: b["entries"][4]["attrs"].pop("group")),
        ("disabled-binding", "exactly the existing Admins group binding required",
         lambda b, p: b["entries"][4]["attrs"].update(enabled=False)),
        ("group-binding-absent", "every entry present",
         lambda b, p: b["entries"][4].update(state="absent")),
        ("user-instead-of-group", "exactly the existing Admins group binding required",
         lambda b, p: (b["entries"][4]["attrs"].pop("group"), b["entries"][4]["attrs"].update(user=find(
             "authentik_core.user", ("username", "thaynes"), ("is_active", True))))),
        ("extra-user-binding", "expected deny policy, portal mapping, provider, application and one binding",
         lambda b, p: b["entries"].append({
             "model": "authentik_policies.policybinding", "state": "present",
             "identifiers": {"target": Ref("KeyOf", "portal-application"), "order": 10},
             "attrs": {"user": 1, "enabled": True, "negate": False, "failure_result": False}})),
        ("extra-deny-binding", "expected deny policy, portal mapping, provider, application and one binding",
         lambda b, p: b["entries"].append({
             "model": "authentik_policies.policybinding", "state": "absent",
             "identifiers": {"target": Ref("KeyOf", "portal-application"), "order": 0},
             "attrs": {"policy": Ref("KeyOf", "portal-staged-deny"), "enabled": True,
                       "negate": False, "failure_result": False}})),
        ("binding-order", "exactly the existing Admins group binding required",
         lambda b, p: b["entries"][4]["identifiers"].update(order=0)),
        ("deny-removed", "staged deny must remain closed",
         lambda b, p: b["entries"][0]["attrs"].update(expression="return True")),
        ("historical-item", "only canonical dev-env properties",
         lambda b, p: p["spec"]["data"][0]["remoteRef"].update(key="dev-env-v2-portal")),
        ("unrelated-property", "only canonical dev-env properties",
         lambda b, p: p["spec"]["data"].append({
             "secretKey": "CLAUDE_CODE_OAUTH_TOKEN",
             "remoteRef": {"key": "dev-env", "property": "CLAUDE_CODE_OAUTH_TOKEN"}})),
        ("data-from", "only explicit field projection; no dataFrom",
         lambda b, p: p["spec"].update(dataFrom=[{"extract": {"key": "openproject"}}])),
        ("federated-provider", "JWT federation must remain empty",
         lambda b, p: b["entries"][2]["attrs"].update(jwt_federation_providers=[1])),
        ("federated-source", "JWT federation must remain empty",
         lambda b, p: b["entries"][2]["attrs"].update(jwt_federation_sources=[1])),
        ("access-lifetime", "claims/token lifetimes changed",
         lambda b, p: b["entries"][2]["attrs"].update(access_token_validity="hours=1")),
        ("code-lifetime", "claims/token lifetimes changed",
         lambda b, p: b["entries"][2]["attrs"].update(access_code_validity="hours=1")),
        ("claims-disabled", "claims/token lifetimes changed",
         lambda b, p: b["entries"][2]["attrs"].update(include_claims_in_id_token=False)),
        ("refresh-lifetime", "provider attribute allowlist changed",
         lambda b, p: b["entries"][2]["attrs"].update(refresh_token_validity="hours=1")),
        ("external-logout", "provider attribute allowlist changed",
         lambda b, p: b["entries"][2]["attrs"].update(logout_uri="https://example.invalid/logout")),
        ("encryption-key", "provider attribute allowlist changed",
         lambda b, p: b["entries"][2]["attrs"].update(encryption_key=SYNTHETIC_UUID)),
        ("app-group", "application attribute allowlist changed",
         lambda b, p: b["entries"][3]["attrs"].update(group="Household")),
        ("instantiate-disabled", "blueprint metadata changed",
         lambda b, p: b["metadata"]["labels"].update({"blueprints.goauthentik.io/instantiate": "false"})),
        ("projection-namespace", "projection metadata changed",
         lambda b, p: p["metadata"].update(namespace="default")),
        ("projection-refresh", "projection refresh changed",
         lambda b, p: p["spec"].update(refreshInterval="0s")),
        ("projection-label", "projection metadata changed",
         lambda b, p: p["metadata"].update(labels={"unreviewed": "true"})),
        ("projection-target-extra", "projection target fields changed",
         lambda b, p: p["spec"]["target"].update(immutable=True)),
        ("null-signer", "required existing reference changed",
         lambda b, p: b["entries"][2]["attrs"].update(signing_key=None)),
        ("launch-url", "application attribute allowlist changed",
         lambda b, p: b["entries"][3]["attrs"].update(meta_launch_url="https://example.invalid/")),
        ("negated-group", "exactly the existing Admins group binding required",
         lambda b, p: b["entries"][4]["attrs"].update(negate=True)),
        ("group-fail-open", "exactly the existing Admins group binding required",
         lambda b, p: b["entries"][4]["attrs"].update(failure_result=True)),
        ("zero-bindings", "expected deny policy, portal mapping, provider, application and one binding",
         lambda b, p: b["entries"].pop()),
        ("drop-portal-mapping", "exact three mappings required",
         lambda b, p: b["entries"][2]["attrs"]["property_mappings"].pop()),
        ("mapping-pin", "portal mapping pin changed",
         lambda b, p: b["entries"][1]["attrs"].update(expression=b["entries"][1]["attrs"]["expression"].replace(
             PINNED_ADMINS_SHA256, "0" * 64))),
        ("mapping-scope", "portal mapping scope changed",
         lambda b, p: b["entries"][1]["attrs"].update(scope_name="email")),
        ("raw-directory-uuid", "raw directory UUID in mapping",
         lambda b, p: b["entries"][1]["attrs"].update(expression=b["entries"][1]["attrs"]["expression"] +
             "\n# 00000000-0000-4000-8000-000000000099\n")),
        ("direct-admin-equals-active", "portal mapping must read direct membership",
         lambda b, p: b["entries"][1]["attrs"].update(expression=(
             'PINNED_ADMINS_SHA256 = "%s"\n'
             "active = True\n"
             "direct_admin = active\n"
             'return {"portal_is_active": active, "portal_is_admin": direct_admin}\n'
         ) % PINNED_ADMINS_SHA256)),
    ]
    for name, reason, mutate in mutations:
        b, p = deepcopy(bp), deepcopy(projection)
        mutate(b, p)
        try:
            validate(b, p, schema)
        except SecurityViolation as exc:
            require(str(exc) == reason, f"mutation {name} rejected for unexpected reason: {exc}")
            print(f"REJECT {name}: {reason}")
            continue
        raise ValueError("negative mutation unexpectedly accepted: " + name)
    if args.rendered:
        docs = list(yaml.safe_load_all(args.rendered.read_text()))
        configs = [d for d in docs if d and d.get("kind") == "ConfigMap" and
                   d["metadata"]["name"] == "authentik-hnet-blueprints"]
        require(len(configs) == 1 and configs[0]["data"].get(BLUEPRINT.name) == BLUEPRINT.read_text(),
                "rendered blueprint differs")
        require(not any(d and d.get("kind") == "ExternalSecret" and
                "portal" in d["metadata"]["name"] for d in docs), "projection unexpectedly active")
    print(f"PASS: pinned schema, configuration constraints, {len(mutations)} rejected mutations"
          + (", rendered ConfigMap" if args.rendered else ""))
    print("NOT TESTED: directory resolution/import transaction, identity, tokens, MFA, logout, runtime Gate A")


if __name__ == "__main__":
    main()
