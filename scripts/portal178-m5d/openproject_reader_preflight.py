"""Synthetic fixture check for the project-3 portal reader.

This module performs no HTTP, reads no secret, and is not a live permission
proof. No collector is implemented. A snapshot supplied by the caller is not
authoritative evidence.
"""

ROLE_NAME = "Dev-env portal reader"
USER_NAME = "dev-env-portal-reader"
PERMISSIONS = ("view_project", "view_work_packages")
PROJECT_ID = 3
PROJECT_IDENTIFIER = "haynes-engineering"
EVIDENCE_KIND = "synthetic-fixture"
SECRET_KEYS = frozenset({
    "token", "password", "secret", "api_key", "apikey", "authorization", "value",
})
TOP_KEYS = frozenset({
    "evidence_kind", "project", "roles", "users", "global_roles", "groups",
    "inherited_memberships", "other_project_memberships", "default_roles",
})
PROJECT_KEYS = frozenset({
    "id", "identifier", "visibility", "active", "parent", "inherited_children",
})
ROLE_KEYS = frozenset({"name", "permissions", "global"})
USER_KEYS = frozenset({"name", "admin", "global_roles", "groups", "memberships"})
MEMBERSHIP_KEYS = frozenset({"project_id", "roles", "inherited", "group"})
EMPTY_SCOPE = (
    "global_roles", "groups", "inherited_memberships",
    "other_project_memberships", "default_roles",
)


class PreflightReject(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _reject_secret_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in SECRET_KEYS:
                raise PreflightReject("secret-field")
            _reject_secret_keys(value)
    elif isinstance(node, list):
        for item in node:
            _reject_secret_keys(item)


def _keys(node, required):
    if not isinstance(node, dict):
        raise PreflightReject("wrong-type")
    present = set(node)
    if present - required:
        raise PreflightReject("unknown-scope")
    if required - present:
        raise PreflightReject("missing-evidence")


def _list(value):
    if not isinstance(value, list):
        raise PreflightReject("wrong-type")
    return value


def _unique(items, key):
    values = [item[key] for item in items]
    if len(values) != len(set(values)):
        raise PreflightReject("duplicate")


def _empty_named(snapshot, name, inherited):
    value = _list(snapshot[name])
    if value:
        raise PreflightReject("inherited-rights" if inherited else "extra-scope")


def _project(project):
    _keys(project, PROJECT_KEYS)
    if type(project["id"]) is not int or project["id"] != PROJECT_ID:
        raise PreflightReject("project-identity")
    if project["identifier"] != PROJECT_IDENTIFIER or project["visibility"] != "private":
        raise PreflightReject("project-identity")
    if project["active"] is not True or project["parent"] is not None:
        raise PreflightReject("project-identity")
    children = _list(project["inherited_children"])
    if children:
        raise PreflightReject("inherited-rights")


def _role(role):
    _keys(role, ROLE_KEYS)
    if not isinstance(role["name"], str):
        raise PreflightReject("wrong-type")
    if not isinstance(role["global"], bool):
        raise PreflightReject("wrong-type")
    if role["global"] is not False:
        raise PreflightReject("extra-scope")
    permissions = _list(role["permissions"])
    if any(not isinstance(item, str) for item in permissions):
        raise PreflightReject("wrong-type")
    if len(permissions) != len(set(permissions)):
        raise PreflightReject("duplicate")
    if set(permissions) != set(PERMISSIONS):
        raise PreflightReject("role-permissions")


def _membership(membership):
    _keys(membership, MEMBERSHIP_KEYS)
    if type(membership["project_id"]) is not int:
        raise PreflightReject("wrong-type")
    if membership["inherited"] is not False:
        if not isinstance(membership["inherited"], bool):
            raise PreflightReject("wrong-type")
        raise PreflightReject("inherited-rights")
    if membership["group"] is not None:
        raise PreflightReject("inherited-rights")
    roles = _list(membership["roles"])
    if len(roles) != len(set(roles)):
        raise PreflightReject("duplicate")
    if membership["project_id"] != PROJECT_ID or set(roles) != {ROLE_NAME}:
        raise PreflightReject("membership-scope")


def _user(user):
    _keys(user, USER_KEYS)
    if not isinstance(user["name"], str):
        raise PreflightReject("wrong-type")
    if user["admin"] is not False:
        if not isinstance(user["admin"], bool):
            raise PreflightReject("wrong-type")
        raise PreflightReject("extra-principal-rights")
    if _list(user["global_roles"]):
        raise PreflightReject("extra-scope")
    if _list(user["groups"]):
        raise PreflightReject("inherited-rights")
    memberships = _list(user["memberships"])
    for membership in memberships:
        _membership(membership)
    _unique(memberships, "project_id")


def _validate(snapshot):
    _reject_secret_keys(snapshot)
    _keys(snapshot, TOP_KEYS)
    if snapshot["evidence_kind"] != EVIDENCE_KIND:
        raise PreflightReject("unknown-scope")
    _project(snapshot["project"])
    for name in EMPTY_SCOPE:
        _empty_named(snapshot, name, name in {"groups", "inherited_memberships"})
    roles = _list(snapshot["roles"])
    users = _list(snapshot["users"])
    _unique(roles, "name")
    _unique(users, "name")
    for role in roles:
        _role(role)
    for user in users:
        _user(user)
    return roles, users


def _result(outcome):
    return {
        "outcome": outcome,
        "evidence_kind": EVIDENCE_KIND,
        "collector": "not-implemented",
        "live_permission_proof": False,
    }


def evaluate(snapshot):
    """Validate a synthetic fixture. Missing keys are not treated as false."""
    roles, users = _validate(snapshot)
    named_roles = [item for item in roles if item["name"] == ROLE_NAME]
    named_users = [item for item in users if item["name"] == USER_NAME]
    if roles or users:
        if not named_roles or not named_users or len(roles) != 1 or len(users) != 1:
            raise PreflightReject("partial-identity")
        if named_users[0]["memberships"] == []:
            raise PreflightReject("membership-scope")
        return _result("synthetic-idempotent")
    return _result("synthetic-absent-ready")


def absence_snapshot():
    """Complete explicit-absence fixture. Not a live read."""
    return {
        "evidence_kind": EVIDENCE_KIND,
        "project": {
            "id": PROJECT_ID,
            "identifier": PROJECT_IDENTIFIER,
            "visibility": "private",
            "active": True,
            "parent": None,
            "inherited_children": [],
        },
        "roles": [],
        "users": [],
        "global_roles": [],
        "groups": [],
        "inherited_memberships": [],
        "other_project_memberships": [],
        "default_roles": [],
    }


def matching_snapshot():
    snapshot = absence_snapshot()
    snapshot["roles"] = [{
        "name": ROLE_NAME,
        "permissions": list(PERMISSIONS),
        "global": False,
    }]
    snapshot["users"] = [{
        "name": USER_NAME,
        "admin": False,
        "global_roles": [],
        "groups": [],
        "memberships": [{
            "project_id": PROJECT_ID,
            "roles": [ROLE_NAME],
            "inherited": False,
            "group": None,
        }],
    }]
    return snapshot


def plan(snapshot):
    """Request shapes only. This function does not create a role, user, or token."""
    result = evaluate(snapshot)
    steps = []
    if result["outcome"] == "synthetic-absent-ready":
        steps.append({
            "action": "create-role",
            "executes": False,
            "supported_by": "none",
            "interface": "OpenProject v17.9.3 lib/api/v3/roles/roles_api.rb GET index and GET show only",
            "human_owned": "existing-administrator",
            "name": ROLE_NAME,
            "permissions": list(PERMISSIONS),
        })
        steps.append({
            "action": "create-user",
            "executes": False,
            "supported_by": "POST /api/v3/users",
            "login": USER_NAME,
            "admin": False,
            "status": "active",
            "omitted_inputs": ["email", "local-credential"],
        })
        steps.append({
            "action": "create-membership",
            "executes": False,
            "supported_by": "POST /api/v3/memberships",
            "project": "/api/v3/projects/3",
            "roles": [ROLE_NAME],
        })
    steps.append({
        "action": "mint-reader-api-token",
        "executes": False,
        "supported_by": "none",
        "interface": "My::AccessTokensController#generate_api_key for User.current after require_login",
        "blocker": "SSO_OPTIONAL false requires X-Authenticated-User, and no v3 route mints a token for another user",
        "rejected": ["new-authentik-identity", "impersonation", "openproject-agent-token", "vault-writer", "rails-token-create"],
        "human_owned": "existing-administrator",
        "record_property": "OPENPROJECT_TOKEN",
        "record_item": "HaynesKube/dev-env",
    })
    return {
        "executes": False,
        "collector": "not-implemented",
        "live_permission_proof": False,
        "evidence_kind": EVIDENCE_KIND,
        "outcome": result["outcome"],
        "steps": steps,
    }
