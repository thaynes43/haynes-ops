"""Synthetic preflight for the project-3 portal reader. No live API and no secret output."""

ROLE_NAME = "Dev-env portal reader"
USER_NAME = "dev-env-portal-reader"
PERMISSIONS = frozenset({"view_project", "view_work_packages"})
PROJECT_ID = 3
SECRET_KEYS = frozenset({
    "token", "password", "secret", "api_key", "apikey", "authorization", "value",
})


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


def _role(snapshot):
    return next((item for item in snapshot.get("roles") or [] if item.get("name") == ROLE_NAME), None)


def _user(snapshot):
    return next((item for item in snapshot.get("users") or [] if item.get("name") == USER_NAME), None)


def _check_role(role):
    if role.get("global"):
        raise PreflightReject("global-role")
    if frozenset(role.get("permissions") or []) != PERMISSIONS:
        raise PreflightReject("role-permissions")


def _check_user(user):
    if user.get("admin") or user.get("global_roles") or user.get("groups"):
        raise PreflightReject("extra-principal-rights")
    memberships = user.get("memberships") or []
    if len(memberships) != 1 or memberships[0].get("project_id") != PROJECT_ID:
        raise PreflightReject("membership-scope")
    membership = memberships[0]
    if membership.get("inherited") or membership.get("group"):
        raise PreflightReject("inherited-membership")
    if frozenset(membership.get("roles") or []) != {ROLE_NAME}:
        raise PreflightReject("membership-role")


def evaluate(snapshot):
    """Return absent-ready or idempotent. Mismatch raises PreflightReject."""
    _reject_secret_keys(snapshot)
    role, user = _role(snapshot), _user(snapshot)
    if role is None and user is None:
        return "absent-ready"
    if role is None or user is None:
        raise PreflightReject("partial-identity")
    _check_role(role)
    _check_user(user)
    return "idempotent"


def plan(snapshot):
    """Idempotent steps. This function performs no HTTP and returns no credential."""
    _reject_secret_keys(snapshot)
    role, user = _role(snapshot), _user(snapshot)
    if role is not None:
        _check_role(role)
    if user is not None:
        _check_user(user)
    if (role is None) != (user is None):
        raise PreflightReject("partial-identity")
    steps = []
    if role is None:
        steps.append({
            "action": "owner-create-role",
            "method": "admin-ui",
            "reason": "OpenProject 17.9.3 public API has no role write",
            "name": ROLE_NAME,
            "permissions": sorted(PERMISSIONS),
            "project_id": PROJECT_ID,
        })
    else:
        steps.append({"action": "use-existing-role", "name": ROLE_NAME})
    if user is None:
        steps.append({
            "action": "owner-create-user",
            "method": "POST /api/v3/users",
            "login": USER_NAME,
            "admin": False,
            "status": "active",
            "credential": "owner-supplied-at-execution-not-stored",
        })
        steps.append({
            "action": "owner-create-membership",
            "method": "POST /api/v3/memberships",
            "project": "/api/v3/projects/3",
            "roles": [ROLE_NAME],
        })
    else:
        steps.append({"action": "use-existing-user", "login": USER_NAME, "project_id": PROJECT_ID})
    steps.append({
        "action": "owner-record-token",
        "store": "HaynesKube/dev-env",
        "property": "OPENPROJECT_TOKEN",
        "route": "Tom secure UI",
        "writer": "none",
    })
    return {"outcome": "absent-ready" if role is None else "idempotent", "steps": steps}
