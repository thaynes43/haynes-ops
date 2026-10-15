"""One-time Job program: run in-cluster with the existing dev-env SA.

LDAP_BIND_PASSWORD comes only from the dedicated ExternalSecret. Existing
pods/exec permission sends it over the verified Kubernetes API to stock ak shell.
No new RBAC, human password, source patch, argv credential or credential output.
Run after blueprint/ESO sync. Existing mismatched tokens fail closed.
"""
import json
import os
from kubernetes import client, config
from kubernetes.stream import stream

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
config.load_incluster_config()
api = client.CoreV1Api()
deployment = client.AppsV1Api().read_namespaced_deployment("authentik-server", "network")
selector = ",".join(k + "=" + v for k, v in deployment.spec.selector.match_labels.items())
pods = api.list_namespaced_pod("network", label_selector=selector).items
ready = [p for p in pods if p.status.phase == "Running" and any(c.type == "Ready" and c.status == "True" for c in p.status.conditions or [])]
assert ready, "No ready Authentik server"
ready.sort(key=lambda p: p.metadata.name)
command = ["ak", "shell", "-c", 'import json,sys; data=json.loads(sys.stdin.readline()); exec(data["program"])']
ws = stream(api.connect_get_namespaced_pod_exec, ready[0].metadata.name, "network", container="server",
            command=command, stderr=True, stdin=True, stdout=True, tty=False, _preload_content=False)
ws.write_stdin(json.dumps({"program": program, "password": password}) + "\n")
output = ""
for _ in range(45):
    if not ws.is_open():
        break
    ws.update(timeout=1)
    if ws.peek_stdout():
        output += ws.read_stdout()
    if ws.peek_stderr():
        ws.read_stderr()  # Never echo arbitrary remote logs or exception data.
ws.close()
results = [line[len("OPENPROJECT_RESULT="):] for line in output.splitlines() if line.startswith("OPENPROJECT_RESULT=")]
assert len(results) == 1, "Stock Authentik provisioning did not confirm success; inspect only redacted diagnostics"
result = json.loads(results[0])
assert result["status"] in ("created", "already_matches") and result["secret_values_printed"] is False
print(json.dumps(result))
