"""Create only the one-time, approved in-cluster LDAP provisioning Job/policy.

Run with KUBECONFIG set, after blueprint + dedicated ExternalSecret are Ready.
No Secret value is read locally. Remove both resources after checking job logs.
"""
import json
from pathlib import Path
import subprocess

name = "openproject-ldap-bind-init"
namespace = "dev"
script = Path(__file__).with_name("provision-ldap-bind.py").read_text()
policy = {"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy", "metadata": {"name": name, "namespace": namespace},
          "spec": {"endpointSelector": {"matchLabels": {"app.kubernetes.io/name": name}}, "ingress": [],
                   "egress": [{"toEntities": ["kube-apiserver"], "toPorts": [{"ports": [{"port": "443", "protocol": "TCP"}, {"port": "6443", "protocol": "TCP"}]}]}]}}
job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "namespace": namespace},
       "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 180, "ttlSecondsAfterFinished": 3600,
                "template": {"metadata": {"labels": {"app.kubernetes.io/name": name}, "annotations": {"k8tz.io/inject": "false"}},
                             "spec": {"serviceAccountName": "dev-env", "automountServiceAccountToken": True, "restartPolicy": "Never",
                                      "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000, "seccompProfile": {"type": "RuntimeDefault"}},
                                      "containers": [{"name": "provision", "image": "ghcr.io/thaynes43/dev-env@sha256:40f91d3cda9f8f45fd8412f4b16c0bc8408e4f094ac11dc1ec0d42a511e3d584",
                                                      "command": ["/usr/bin/python3", "-c", script],
                                                      "env": [{"name": "PYTHONDONTWRITEBYTECODE", "value": "1"}, {"name": "LDAP_BIND_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "openproject-ldap-bind", "key": "password"}}}],
                                                      "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                                                      "resources": {"requests": {"cpu": "25m", "memory": "128Mi"}, "limits": {"cpu": "250m", "memory": "256Mi"}}}]}}}}
for obj in (policy, job):
    subprocess.run(["kubectl", "create", "-f", "-"], input=json.dumps(obj), text=True, check=True)
