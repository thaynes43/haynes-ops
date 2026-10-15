"""One-time Job program: run in-cluster with the existing dev-env SA.

LDAP_BIND_PASSWORD comes only from the dedicated ExternalSecret. Existing
pods/exec permission sends it over the verified Kubernetes API to stock ak shell.
No new RBAC, human password, source patch, argv credential or credential output.
Run after blueprint/ESO sync. Existing mismatched tokens fail closed.
"""
import json
import os
import subprocess

password = os.environ["LDAP_BIND_PASSWORD"]
assert len(password) == 64 and all(c in "0123456789abcdefABCDEF" for c in password), "Unexpected dedicated password format"
program = '''
from django.db import transaction
from authentik.core.models import Token, TokenIntents, User
with transaction.atomic():
    user = User.objects.get(username="openproject-ldap", type="service_account", is_active=True)
    token, created = Token.objects.get_or_create(
        identifier="openproject-ldap-bind",
        defaults={"user": user, "intent": TokenIntents.INTENT_APP_PASSWORD, "key": data["password"],
                  "expiring": False, "description": "Dedicated OpenProject LDAP lookup; HaynesKube/openproject"},
    )
    assert token.user_id == user.pk and token.intent == TokenIntents.INTENT_APP_PASSWORD, "Existing token has unexpected scope"
    assert token.key == data["password"], "Existing token differs; explicit credential rotation required"
    assert not token.expiring, "Existing token has unexpected expiry"
print("OPENPROJECT_RESULT=" + json.dumps({"status": "created" if created else "already_matches", "user": user.username,
                  "intent": "app_password", "secret_values_printed": False}))
'''
command = ["kubectl", "exec", "-n", "network", "deploy/authentik-server", "-c", "server", "-i", "--",
           "ak", "shell", "-c", 'import json,sys; data=json.loads(sys.stdin.readline()); exec(data["program"])']
# pods/exec grants CREATE. Force the supported POST/SPDY transport rather than
# the Python client's GET/WebSocket path, which would require additional RBAC.
env = dict(os.environ, KUBECTL_REMOTE_COMMAND_WEBSOCKETS="false")
response = subprocess.run(command, input=json.dumps({"program": program, "password": password}) + "\n",
                          capture_output=True, text=True, env=env, timeout=90)
assert response.returncode == 0, "Stock Authentik provisioning command failed; no raw remote output returned"
output = response.stdout
results = [line[len("OPENPROJECT_RESULT="):] for line in output.splitlines() if line.startswith("OPENPROJECT_RESULT=")]
assert len(results) == 1, "Stock Authentik provisioning did not confirm success; inspect only redacted diagnostics"
result = json.loads(results[0])
assert result["status"] in ("created", "already_matches") and result["secret_values_printed"] is False
print(json.dumps(result))
