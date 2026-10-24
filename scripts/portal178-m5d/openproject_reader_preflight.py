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


def evaluate(snapshot):
    """Return absent-ready or idempotent. Mismatch raises PreflightReject."""
    _reject_secret_keys(snapshot)
    roles = snapshot.get("roles") or []
    users = snapshot.get("users") or []
    role = next((item for item in roles if item.get("name") == ROLE_NAME), None)
    user = next((item for item in users if item.get("name") == USER_NAME), None)
    if role is None and user is None:
        return "absent-ready"
    if role is None or user is None:
        raise PreflightReject("partial-identity")
    if frozenset(role.get("permissions") or []) != PERMISSIONS:
        raise PreflightReject("role-permissions")
    if user.get("admin") or user.get("global_roles") or user.get("groups"):
        raise PreflightReject("extra-principal-rights")
    memberships = user.get("memberships") or []
    if len(memberships) != 1 or memberships[0].get("project_id") != PROJECT_ID:
        raise PreflightReject("membership-scope")
    if frozenset(memberships[0].get("roles") or []) != {ROLE_NAME}:
        raise PreflightReject("membership-role")
    return "idempotent"
