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

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "kubernetes/main/apps/network/authentik/app"
BLUEPRINT = APP / "blueprints/97-dev-env-v2-portal-oidc.yaml"
PROJECTION = ROOT / ".agents/reports/portal178-m5a/pending/externalsecret.yaml"
SCHEMA_SHA = "b59476bcbaf92dc1a5bd31d98936d44348bc2b048deae7e891227597c74e97e8"
SYNTHETIC_UUID = "00000000-0000-4000-8000-000000000001"


@dataclass
class Ref:
    tag: str
    value: object


class Loader(yaml.SafeLoader):
    pass


def require(condition, message):
    if not condition:
        raise ValueError(message)


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
    find("authentik_core.user", ("username", "thaynes"), ("is_active", True)),
    find("authentik_core.group", ("name", "authentik Admins")),
]


def materialize(value, ids):
    if isinstance(value, Ref):
        if value.tag == "Find":
            require(value in EXPECTED_FINDS, "unexpected directory lookup")
            return 1 if value.value[0] == "authentik_core.user" else SYNTHETIC_UUID
        require(value.tag == "KeyOf" and value.value in ids, "unresolved entry reference")
        model = ids[value.value]
        return 1 if model == "authentik_providers_oauth2.oauth2provider" else SYNTHETIC_UUID
    if isinstance(value, dict):
        return {k: materialize(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [materialize(v, ids) for v in value]
    return value


def validate(blueprint, projection, schema):
    entries = blueprint["entries"]
    require(len(entries) == 6, "expected provider/application/deny and three bindings")
    ids = {e["id"]: e["model"] for e in entries if "id" in e}
    require(len(ids) == 3, "duplicate or missing entry ID")
    normalized = materialize(blueprint, ids)
    jsonschema.Draft7Validator(schema, format_checker=jsonschema.FormatChecker()).validate(normalized)
    for e in entries:
        require(e.get("state") == "present" and not e.get("conditions") and not e.get("permissions"),
                "entries must reconcile unconditionally without permissions")
        require(set(e) <= {"model", "id", "state", "identifiers", "attrs"}, "unknown entry field")
        allowed = schema["definitions"]["model_" + e["model"]]["properties"]
        require(set(e["attrs"]) <= set(allowed) and set(e["identifiers"]) <= set(allowed),
                "unknown model attribute")
    policy, provider, application, *bindings = entries
    require(policy["model"] == "authentik_policies_expression.expressionpolicy" and
            policy["id"] == "portal-staged-deny" and
            policy["identifiers"] == {"name": "dev-env-v2-portal-staged-deny"} and
            policy["attrs"] == {"expression": "return False", "execution_logging": False},
            "staged deny must remain closed")
    require(provider["model"] == "authentik_providers_oauth2.oauth2provider" and
            provider["id"] == "portal-provider" and
            provider["identifiers"] == {"name": "dev-env-v2-portal-internal"},
            "provider identity changed")
    p = provider["attrs"]
    require(p["client_type"] == "confidential" and p["client_id"] == "dev-env-v2-portal-internal",
            "confidential approved client required")
    require(p["grant_types"] == ["authorization_code"], "code grant only")
    require(p["redirect_uris"] == [{"matching_mode": "strict",
            "url": "https://dev-env-v2.haynesops.com/auth/callback",
            "redirect_uri_type": "authorization"}], "exact callback required")
    require(p["property_mappings"] == EXPECTED_FINDS[2:4], "openid/profile only")
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
    require(a["provider"] == Ref("KeyOf", "portal-provider") and
            a["policy_engine_mode"] == "all" and a["meta_hide"] is True and
            "meta_launch_url" not in a and not a.get("backchannel_providers"),
            "all-policy application without launch/backchannel required")
    for b, order, subject in zip(bindings, [0, 10, 20], [
            {"policy": Ref("KeyOf", "portal-staged-deny")},
            {"user": EXPECTED_FINDS[5]}, {"group": EXPECTED_FINDS[6]}], strict=True):
        require(b["model"] == "authentik_policies.policybinding" and
                b["identifiers"] == {"target": Ref("KeyOf", "portal-application"), "order": order} and
                b["attrs"] == {**subject, "enabled": True, "negate": False, "failure_result": False},
                "deny AND exact active Tom AND existing Admins bindings required")

    require(projection["apiVersion"] == "external-secrets.io/v1" and
            projection["kind"] == "ExternalSecret", "expected pending ExternalSecret")
    spec = projection["spec"]
    require(set(spec) == {"secretStoreRef", "refreshInterval", "target", "data"},
            "only explicit field projection; no dataFrom")
    require(spec["secretStoreRef"] ==
            {"kind": "ClusterSecretStore", "name": "onepassword-connect"}, "dedicated store required")
    fields = ["OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"]
    require(spec["data"] == [{"secretKey": f, "remoteRef":
            {"key": "dev-env-v2-portal", "property": f}} for f in fields],
            "only dedicated item/client fields")
    target = spec["target"]
    require(target["name"] == "dev-env-v2-portal-oidc" and target["creationPolicy"] == "Owner" and
            target["deletionPolicy"] == "Delete", "isolated owned target required")
    require(target["template"] == {"engineVersion": "v2", "mergePolicy": "Replace", "type": "Opaque",
            "data": {f: "{{ ." + f + " }}" for f in fields}}, "exact server Secret keys")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--rendered", type=Path)
    args = parser.parse_args()
    raw = args.schema.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == SCHEMA_SHA, "unverified schema bytes")
    schema = json.loads(raw)
    bp = yaml.load(BLUEPRINT.read_text(), Loader=Loader)
    projection = yaml.safe_load(PROJECTION.read_text())
    validate(bp, projection, schema)

    # Mutations exercise the guard's rejection, not Authentik or an OIDC flow.
    mutations = [
        ("any-policy", lambda b, p: b["entries"][2]["attrs"].update(policy_engine_mode="any")),
        ("public-client", lambda b, p: b["entries"][1]["attrs"].update(client_type="public")),
        ("extra-grant", lambda b, p: b["entries"][1]["attrs"]["grant_types"].append("refresh_token")),
        ("extra-scope", lambda b, p: b["entries"][1]["attrs"]["property_mappings"].append(
            find("authentik_providers_oauth2.scopemapping",
                 ("managed", "goauthentik.io/providers/oauth2/scope-email")))),
        ("callback-wildcard", lambda b, p: b["entries"][1]["attrs"]["redirect_uris"][0].update(
            matching_mode="regex")),
        ("username-sub", lambda b, p: b["entries"][1]["attrs"].update(sub_mode="user_username")),
        ("secret-in-source", lambda b, p: b["entries"][1]["attrs"].update(client_secret="SYNTHETIC_CANARY")),
        ("missing-user", lambda b, p: b["entries"][4]["attrs"].pop("user")),
        ("missing-group", lambda b, p: b["entries"][5]["attrs"].pop("group")),
        ("disabled-binding", lambda b, p: b["entries"][5]["attrs"].update(enabled=False)),
        ("deny-removed", lambda b, p: b["entries"][0]["attrs"].update(expression="return True")),
        ("broad-item", lambda b, p: p["spec"]["data"][0]["remoteRef"].update(key="dev-env")),
        ("data-from", lambda b, p: p["spec"].update(dataFrom=[{"extract": {"key": "openproject"}}])),
    ]
    for name, mutate in mutations:
        b, p = deepcopy(bp), deepcopy(projection)
        mutate(b, p)
        try:
            validate(b, p, schema)
        except (ValueError, KeyError, jsonschema.ValidationError):
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
