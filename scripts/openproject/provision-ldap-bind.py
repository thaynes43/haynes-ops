"""Run through stock `ak shell` in authentik-server, after blueprint/ESO sync.

No credential is accepted on argv or printed. The Kubernetes client reads only
this application's new bind Secret. Existing mismatched tokens fail closed.
"""
import base64
import json
from django.db import transaction
from authentik.core.models import Token, TokenIntents, User
from kubernetes import client, config

config.load_incluster_config()
secret = client.CoreV1Api().read_namespaced_secret("openproject-ldap-bind", "network")
password = base64.b64decode(secret.data["password"]).decode()
assert len(password) == 64 and all(c in "0123456789abcdefABCDEF" for c in password), "Unexpected dedicated password format"
with transaction.atomic():
    user = User.objects.get(username="openproject-ldap", type="service_account", is_active=True)
    token, created = Token.objects.get_or_create(
        identifier="openproject-ldap-bind",
        defaults={"user": user, "intent": TokenIntents.INTENT_APP_PASSWORD, "key": password,
                  "expiring": False, "description": "Dedicated OpenProject LDAP lookup; HaynesKube/openproject"},
    )
    assert token.user_id == user.pk and token.intent == TokenIntents.INTENT_APP_PASSWORD, "Existing token has unexpected scope"
    assert token.key == password, "Existing token differs; explicit credential rotation required"
    assert not token.expiring, "Existing token has unexpected expiry"
print(json.dumps({"status": "created" if created else "already_matches", "user": user.username,
                  "intent": "app_password", "secret_values_printed": False}))
