"""Render a bounded public-only Job, then install via an undisplayed stdout pipe."""

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

from node_install import REFUSAL_REASONS

HERE = Path(__file__).resolve().parent
NAMESPACE = "dev-env-system"
SECRET = "dev-env-keeper-node-trust-public"
MOUNT = "/run/keeper-node-trust-public/public-key"
IMAGE = "ghcr.io/thaynes43/dev-env:2.9.1@sha256:a975de7dbc40c6f33048a38a327a2db2b9698a7615f5b2b9897abbb1d04df0cf"
NODES = ("haynesintelligence", "twin-top", "twin-bottom", "pve04", "pve-filet02")


def public_source():
    return (HERE / "public_key.py").read_text()


def reader_source():
    return public_source() + f"""
import hashlib, json, sys, time
try:
    with open({MOUNT!r}, 'rb') as source:
        public = normalize_public(source.read(LIMIT + 1))
    public_digest = hashlib.sha256(public).hexdigest()
    if not sys.argv[1:]:
        print(json.dumps({{'publicKeyValidated': True, 'publicSHA256': public_digest}}), flush=True)
        time.sleep(150)
    elif sys.argv[1:] == ['--emit', public_digest]:
        sys.stdout.buffer.write(public + b'\\n')
    else:
        raise ValueError('PublicGenerationChangedOrOperation')
except Exception:
    print('public-key-validation-failed', file=sys.stderr)
    sys.exit(1)
"""


def node_source():
    return ("import sys, types\n"
            "public_key = types.ModuleType('public_key')\n"
            f"exec({public_source()!r}, public_key.__dict__)\n"
            "sys.modules['public_key'] = public_key\n"
            + (HERE / "node_install.py").read_text())


def render_job(name, worker):
    if not name.startswith("keeper-node-trust-") or not worker.startswith("talosw"):
        raise ValueError("JobNameOrWorker")
    annotations = {"k8tz.io/inject": "false"}
    return {"apiVersion": "batch/v1", "kind": "Job",
            "metadata": {"name": name, "namespace": NAMESPACE, "annotations": annotations},
            "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 180,
                     "ttlSecondsAfterFinished": 300,
                     "template": {"metadata": {"annotations": annotations},
                                  "spec": {"restartPolicy": "Never", "serviceAccountName": "default",
                                           "priorityClassName": "dev-env-agent",
                                           "automountServiceAccountToken": False,
                                           "nodeSelector": {"kubernetes.io/hostname": worker},
                                           "affinity": {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
                                               "nodeSelectorTerms": [{"matchExpressions": [{"key": "node-role.kubernetes.io/control-plane", "operator": "DoesNotExist"}]}]}}},
                                           "securityContext": {"runAsNonRoot": True, "runAsUser": 1000,
                                                               "runAsGroup": 1000, "seccompProfile": {"type": "RuntimeDefault"}},
                                           "containers": [{"name": "delivery", "image": IMAGE,
                                                           "command": ["python3", "-B", "-c", reader_source()],
                                                           "resources": {"requests": {"cpu": "25m", "memory": "64Mi"},
                                                                         "limits": {"cpu": "100m", "memory": "128Mi"}},
                                                           "securityContext": {"allowPrivilegeEscalation": False,
                                                                               "readOnlyRootFilesystem": True,
                                                                               "capabilities": {"drop": ["ALL"]}},
                                                           "volumeMounts": [{"name": "public", "mountPath": "/run/keeper-node-trust-public", "readOnly": True}]}],
                                           "volumes": [{"name": "public", "secret": {"secretName": SECRET,
                                                        "defaultMode": 0o444, "items": [{"key": "public-key", "path": "public-key"}]}}]}}}}


def query(kind, name):
    result = subprocess.run(["kubectl", "get", kind, name, "-n", NAMESPACE, "-o", "json"],
                            capture_output=True, timeout=15, check=True)
    return json.loads(result.stdout)


def validate_admitted(job, pod):
    expected = render_job(job["metadata"]["name"], pod["spec"]["nodeName"])
    wanted = expected["spec"]["template"]["spec"]
    spec = pod["spec"]
    if (job["metadata"].get("annotations", {}).get("k8tz.io/inject") != "false"
            or pod["metadata"].get("annotations", {}).get("k8tz.io/inject") != "false"
            or spec.get("automountServiceAccountToken") is not False
            or spec.get("serviceAccountName") != "default"
            or spec.get("priorityClassName") != "dev-env-agent"
            or spec.get("initContainers") or spec.get("ephemeralContainers")
            or len(spec.get("containers", [])) != 1
            or spec.get("volumes") != wanted["volumes"]
            or spec.get("nodeSelector") != wanted["nodeSelector"]
            or spec.get("affinity") != wanted["affinity"]
            or spec.get("securityContext") != wanted["securityContext"]
            or spec.get("hostNetwork") or spec.get("hostPID") or spec.get("hostIPC")
            or job["spec"].get("activeDeadlineSeconds") != 180
            or job["spec"].get("backoffLimit") != 0
            or not any(ref.get("uid") == job["metadata"]["uid"] and ref.get("kind") == "Job"
                       and ref.get("controller") for ref in pod["metadata"].get("ownerReferences", []))):
        raise ValueError("AdmittedPodGuard")
    container = spec["containers"][0]
    for field in ("name", "image", "command", "resources", "securityContext", "volumeMounts"):
        if container.get(field) != wanted["containers"][0][field]:
            raise ValueError("AdmittedContainerGuard")
    if container.get("env") or container.get("envFrom") or container.get("args"):
        raise ValueError("AdmittedEnvironmentGuard")
    if pod.get("status", {}).get("phase") != "Running":
        raise ValueError("PodNotRunning")


def refusal_reason(raw):
    if len(raw) > 4096:
        return None
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    if (not isinstance(result, dict) or set(result) != {"result", "reason"}
            or result["result"] != "refused" or not isinstance(result["reason"], str)
            or result["reason"] not in REFUSAL_REASONS):
        return None
    return result["reason"]


def install_nodes(job_name, pod_name):
    job, pod = query("job", job_name), query("pod", pod_name)
    validate_admitted(job, pod)
    logs = subprocess.run(["kubectl", "logs", "-n", NAMESPACE, pod_name, "-c", "delivery"],
                          capture_output=True, timeout=10, check=True)
    validated = json.loads(logs.stdout)
    if (set(validated) != {"publicKeyValidated", "publicSHA256"}
            or validated["publicKeyValidated"] is not True
            or not isinstance(validated["publicSHA256"], str)
            or len(validated["publicSHA256"]) != 64
            or any(c not in "0123456789abcdef" for c in validated["publicSHA256"])):
        raise ValueError("ReaderValidationReceipt")
    source = node_source()
    print(json.dumps({"admittedPublicOnlyJob": True, "nodeInstallerSHA256": hashlib.sha256(source.encode()).hexdigest()}), flush=True)
    deadline = time.monotonic() + 120
    for node in NODES:
        if deadline - time.monotonic() < 30:
            raise ValueError("DeliveryBudget")
        current_pod = query("pod", pod_name)
        if current_pod["metadata"]["uid"] != pod["metadata"]["uid"]:
            raise ValueError("DeliveryPodReplaced")
        validate_admitted(job, current_pod)
        # Public material passes directly between pipe descriptors. Neither the
        # model nor this Python process reads it, stores it, or places it in argv.
        producer = subprocess.Popen(["kubectl", "exec", "-n", NAMESPACE, pod_name, "-c", "delivery",
                                     "--", "python3", "-B", "-c", reader_source(),
                                     "--emit", validated["publicSHA256"]],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            remote = shlex.join(["python3", "-B", "-c", source, "install"])
            consumer = subprocess.run(["hw-ssh", node, remote], stdin=producer.stdout,
                                      capture_output=True, timeout=20)
            producer.stdout.close()
            producer.wait(timeout=5)
            if consumer.returncode or producer.returncode:
                reason = refusal_reason(consumer.stdout)
                if reason is not None:
                    print(json.dumps({"node": node, "refused": reason}), flush=True)
                raise ValueError("PublicTransferOrInstallFailed")
            receipt = json.loads(consumer.stdout)
            if (set(receipt) != {"result", "originalBytesPreserved", "backup"}
                    or receipt["result"] not in {"installed", "already-installed"}
                    or receipt["originalBytesPreserved"] is not True
                    or not isinstance(receipt["backup"], str)
                    or not receipt["backup"].startswith("/home/dev-env/.ssh/.authorized_keys.before-keeper-ca.")):
                raise ValueError("InstallerReceipt")
            account = subprocess.run(["hw-ssh", node, "id -un"], capture_output=True, timeout=10, check=True)
            if account.stdout.strip() != b"dev-env":
                raise ValueError("StandingAccountChanged")
            subprocess.run(["hw-ssh", node, "sudo -n /usr/bin/pvesh get /version --output-format json"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
            print(json.dumps({"node": node, **receipt, "existingAccess": "passed"}, sort_keys=True), flush=True)
        finally:
            if producer.poll() is None:
                producer.terminate()
                try:
                    producer.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    producer.kill()
                    producer.wait(timeout=5)
            producer.stdout.close()
            producer.stderr.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    render = commands.add_parser("render-job")
    render.add_argument("--name", required=True)
    render.add_argument("--worker", required=True)
    install = commands.add_parser("install")
    install.add_argument("--job", required=True)
    install.add_argument("--pod", required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--node", choices=NODES, required=True)
    args = parser.parse_args()
    if args.operation == "render-job":
        print(json.dumps(render_job(args.name, args.worker)))
    elif args.operation == "install":
        install_nodes(args.job, args.pod)
    else:
        remote = shlex.join(["python3", "-B", "-c", node_source(), "rollback"])
        result = subprocess.run(["hw-ssh", args.node, remote], capture_output=True, timeout=25, check=True)
        receipt = json.loads(result.stdout)
        if receipt.get("result") not in {"rolled-back", "already-rolled-back"}:
            raise ValueError("RollbackReceipt")
        print(json.dumps({"node": args.node, **receipt}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("keeper-node-trust: operation failed; stop and inspect metadata/receipts", file=sys.stderr)
        sys.exit(1)
