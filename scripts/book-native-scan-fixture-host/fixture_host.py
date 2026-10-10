"""Closed private native fixture transport; preparation is not execution authority."""
import argparse
import copy
import ctypes
import errno
import datetime as dt
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import schema_codec

NS = "media"
LABEL = "book-native-fixture"
APP = "book-native-scan-fixture"
IMAGE = "ghcr.io/thaynes43/book-native-scan-fixture@sha256:53dfa0670e616e9b6845ccb2dc8f8dbf40cc3e28a7367d0b0ce105d2a6a196b4"
NATIVE = "sha256:ca6af7a18d7124d014702983c2364e485294f808c1552e9555f2595b7cda7982"
GATE = "set -eu; umask 077; while [ ! -f /fixture-input/approved-packet.json ]; do sleep 1; done; exec nice -n 19 /fixture/NativeScannerFixture --prepared-private-fixture"
UPLOAD = "set -eu; umask 077; p=$1; h=$2; [ ! -e \"$p\" ]; [ ! -L \"$p\" ]; [ ! -e \"$p.partial\" ]; [ ! -L \"$p.partial\" ]; mkdir -p -- \"$(dirname -- \"$p\")\"; cat > \"$p.partial\"; chmod 600 \"$p.partial\"; [ \"$(sha256sum -- \"$p.partial\" | cut -d ' ' -f 1)\" = \"$h\" ]; sync -f \"$p.partial\"; mv -n -- \"$p.partial\" \"$p\"; [ ! -e \"$p.partial\" ]; [ ! -L \"$p\" ]; [ \"$(sha256sum -- \"$p\" | cut -d ' ' -f 1)\" = \"$h\" ]; sync -f \"$(dirname -- \"$p\")\"; printf '%s\\n' \"$h\""
FIXTURE_MODULES = ["/fixture/NativeScannerFixture", "/fixture/NativeScannerFixture.dll", "/fixture/libcoreclr.so", "/fixture/libhostfxr.so", "/fixture/System.Private.CoreLib.dll"]
MODULES = ["/kavita/" + n + ".dll" for n in ("Kavita.Server", "Kavita.API", "Kavita.Models", "Kavita.Services", "Kavita.Database", "Microsoft.Data.Sqlite", "Microsoft.EntityFrameworkCore", "Microsoft.EntityFrameworkCore.Relational")]
PROOFS = {"/kavita/config/proof-" + n + ".json" for n in ("before", "after-build", "after-bind", "after", "inverse")}
DIAGNOSTIC = "/kavita/config/proof-diagnostic.json"


class Refused(Exception):
    pass


def require(value, code):
    if not value:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def stamp(epoch=None):
    return dt.datetime.fromtimestamp(time.time() if epoch is None else epoch, dt.timezone.utc).isoformat()


def epoch(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def read_private(path, limit=32 * 1024 * 1024):
    path = Path(path)
    require(path.is_absolute(), "private_path_absolute")
    for parent in (path, *path.parents):
        require(not parent.is_symlink(), "private_symlink")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_mode & 0o077 == 0 and 0 < before.st_size <= limit, "private_mode_size")
        with os.fdopen(os.dup(fd), "rb") as file:
            raw = file.read(limit + 1)
        after = os.fstat(fd)
        named = path.stat(follow_symlinks=False)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_nlink")
        require(len(raw) == before.st_size and all(getattr(before, k) == getattr(after, k) == getattr(named, k) for k in fields), "private_changed")
        return raw
    finally:
        os.close(fd)


def save_private(path, raw):
    path = Path(path)
    raw = raw if isinstance(raw, bytes) else canonical(raw)
    require(path.parent.is_dir() and path.parent.stat().st_mode & 0o077 == 0 and not path.parent.is_symlink(), "private_directory")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(os.dup(fd), "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
    finally:
        os.close(fd)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def rename_noreplace(directory, source, destination):
    # Same Linux no-replace publication primitive as native SOURCE outcome_mailbox.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    require(rename is not None, "atomic_noreplace_unsupported")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(directory, source.encode(), directory, destination.encode(), 1) != 0:
        if ctypes.get_errno() == errno.EEXIST:
            raise FileExistsError(destination)
        raise Refused("atomic_noreplace_refused")


def atomic_private_marker(path, value):
    path = Path(path)
    pending = path.with_name(path.name + ".pending-" + str(uuid.uuid4()))
    save_private(pending, value)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    source = os.open(pending, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(source)
        named = os.stat(pending.name, dir_fd=directory, follow_symlinks=False)
        fields = ("st_dev", "st_ino", "st_size", "st_mode", "st_nlink", "st_mtime_ns")
        require(before.st_nlink == 1 and all(getattr(before, k) == getattr(named, k) for k in fields), "atomic_stage_changed")
        rename_noreplace(directory, pending.name, path.name)
        # File bytes were already fsynced. The final entry is atomic/no-replace
        # and never has a second link, even across SIGKILL or a pod crash.
        os.fsync(directory)
        after = os.fstat(source)
        named = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        require(all(getattr(before, k) == getattr(after, k) == getattr(named, k) for k in fields), "atomic_final_changed")
    finally:
        os.close(source)
        os.close(directory)


def retire_collection(fixture, out, original_execution_end=None):
    # The original collection alarm must not interrupt a completed collect while
    # its retirement marker is being fsynced. Keep the same absolute 200s cap.
    execution_end = fixture.end if original_execution_end is None else original_execution_end
    signal.setitimer(signal.ITIMER_REAL, max(.001, execution_end + 20 - time.time()))
    require(not fixture.requests, "active_request_not_reaped")
    atomic_private_marker(Path(out) / "execution-retired.json", {"phase": fixture.phase, "retiredAt": stamp(), "runnerPid": os.getpid()})


def inventory(value, kind):
    api, envelope = ("batch/v1", "JobList") if kind == "Job" else ("v1", "PodList")
    require(value.get("apiVersion") == api and value.get("kind") == envelope and isinstance(value.get("items"), list), "raw_inventory_type")
    metadata = value.get("metadata", {})
    require(bool(metadata.get("resourceVersion")) and metadata.get("continue", "") == "" and metadata.get("remainingItemCount", 0) == 0, "raw_inventory_partial")
    for item in value["items"]:
        require(item.get("kind", kind) == kind and item.get("apiVersion", api) == api and item.get("metadata", {}).get("namespace") == NS, "raw_inventory_item")
    return value


def owned_pods(pods, name, phase, uid):
    result = []
    for pod in inventory(pods, "Pod")["items"]:
        m = pod["metadata"]
        owners = m.get("ownerReferences", [])
        labels = m.get("labels", {})
        candidate = labels.get(LABEL) == phase or uid is not None and labels.get("batch.kubernetes.io/controller-uid") == uid or any(o.get("kind") == "Job" and (o.get("name") == name or uid is not None and o.get("uid") == uid) for o in owners)
        if not candidate:
            continue
        require(labels.get(LABEL) == phase and len(owners) == 1 and owners[0].get("kind") == "Job" and owners[0].get("apiVersion") == "batch/v1" and owners[0].get("controller") is True and owners[0].get("name") == name and uid in (None, owners[0].get("uid")) and labels.get("batch.kubernetes.io/controller-uid") == owners[0].get("uid"), "owned_pod_conflict")
        result.append(pod)
    return result


def file_timestamp_binding(raw, pin):
    lines = raw.decode("ascii").splitlines()
    require(len(lines) == 3 and lines[0] == lines[2] and lines[1].split()[0] == pin["sha256"], "candidate_file_observation_changed")
    values = lines[0].split("|")
    require(len(values) == 4 and re.fullmatch("[0-9]+", values[0]) and re.fullmatch("[0-9]+", values[2]) and int(values[2]) == pin["size"] and re.fullmatch("[0-9]+:[0-9]+", values[3]), "candidate_file_stat_shape")
    fractional = re.search(r"\.([0-9]{9}) [+-][0-9]{4}$", values[1])
    require(fractional is not None, "candidate_file_time_precision")
    mtime_ns = int(values[0]) * 1_000_000_000 + int(fractional[1])
    local, utc = schema_codec.file_times(mtime_ns)
    return {"Path": pin["target"], "Sha256": pin["sha256"], "Size": pin["size"], "MtimeNanoseconds": mtime_ns, "Inode": values[3], "LastModified": "text:" + local, "LastModifiedUtc": "text:" + utc, "ObservedAt": stamp()}


def created_uid(expected, actual):
    metadata = actual.get("metadata", {})
    require(actual.get("apiVersion") == "batch/v1" and actual.get("kind") == "Job" and metadata.get("namespace") == NS and metadata.get("name") == expected["metadata"]["name"] and isinstance(metadata.get("uid"), str) and bool(metadata["uid"]), "raw_created_identity")
    return metadata["uid"]


def cleanup_pods(pods, name, phase, uid):
    """Known controller UID proves ownership even when admission changed labels."""
    result = []
    for pod in inventory(pods, "Pod")["items"]:
        m = pod["metadata"]
        owners = m.get("ownerReferences", [])
        labels = m.get("labels", {})
        candidate = labels.get(LABEL) == phase or uid is not None and labels.get("batch.kubernetes.io/controller-uid") == uid or any(o.get("kind") == "Job" and (o.get("name") == name or uid is not None and o.get("uid") == uid) for o in owners)
        if not candidate:
            continue
        require(uid is not None and len(owners) == 1 and owners[0].get("kind") == "Job" and owners[0].get("apiVersion") == "batch/v1" and owners[0].get("controller") is True and owners[0].get("name") == name and owners[0].get("uid") == uid and labels.get("batch.kubernetes.io/controller-uid") in (None, uid), "cleanup_pod_conflict")
        result.append(pod)
    return result


def manifest(template, phase):
    require(str(uuid.UUID(phase)) == phase, "phase_uuid")
    value = copy.deepcopy(template["job"])
    value["metadata"]["name"] = "ransom-native-fixture-" + phase[:8]
    value["metadata"]["labels"][LABEL] = phase
    value["spec"]["template"]["metadata"]["labels"][LABEL] = phase
    value["spec"]["podReplacementPolicy"] = "Failed"
    spec = value["spec"]["template"]["spec"]
    spec["enableServiceLinks"] = False
    spec["containers"][0]["image"] = IMAGE
    spec["containers"][0]["command"] = ["sh", "-c", GATE]
    spec["containers"][0]["args"] = []
    return value


def declared(expected, actual, pod=False):
    """Permit only known native defaults and Job controller labels."""
    expected = copy.deepcopy(expected)
    actual = copy.deepcopy(actual)
    for value in (expected, actual):
        value.pop("status", None)
        m = value["metadata"]
        native_uid = m.get("uid")
        native_name = m.get("name")
        for key in ("uid", "resourceVersion", "generation", "creationTimestamp", "managedFields", "ownerReferences", "generateName", "deletionTimestamp", "deletionGracePeriodSeconds"):
            m.pop(key, None)
        for label in list(m.get("labels", {})):
            if label in ("batch.kubernetes.io/controller-uid", "batch.kubernetes.io/job-name", "controller-uid", "job-name"):
                del m["labels"][label]
        if not pod:
            spec = value["spec"]
            for key, default in (("completionMode", "NonIndexed"), ("suspend", False), ("manualSelector", False)):
                if spec.get(key) == default:
                    spec.pop(key)
            selector = spec.pop("selector", None)
            require(selector is None or selector == {"matchLabels": {"batch.kubernetes.io/controller-uid": native_uid}}, "native_job_selector")
            value["spec"]["template"]["metadata"].pop("creationTimestamp", None)
            labels = value["spec"]["template"]["metadata"].get("labels", {})
            for key in ("batch.kubernetes.io/controller-uid", "controller-uid", "batch.kubernetes.io/job-name", "job-name"):
                if key in labels:
                    require(labels[key] == (native_uid if key.endswith("controller-uid") else native_name), "native_template_controller_label")
                    del labels[key]
            ps = value["spec"]["template"]["spec"]
        else:
            ps = value["spec"]
        for key, default in (("dnsPolicy", "ClusterFirst"), ("schedulerName", "default-scheduler"), ("serviceAccount", "default"), ("serviceAccountName", "default"), ("hostNetwork", False), ("hostPID", False), ("hostIPC", False), ("priority", 0), ("preemptionPolicy", "PreemptLowerPriority")):
            if ps.get(key) == default:
                ps.pop(key)
        # Native default NotReady/Unreachable tolerations do not grant mounts/network.
        ts = ps.get("tolerations", [])
        default_ts = [{"key": k, "operator": "Exists", "effect": "NoExecute", "tolerationSeconds": 300} for k in ("node.kubernetes.io/not-ready", "node.kubernetes.io/unreachable")]
        if ts and sorted(ts, key=lambda t: t.get("key", "")) == default_ts:
            ps.pop("tolerations")
        for container in ps.get("containers", []):
            if container.get("args") == []:
                container.pop("args")
            for key, default in (("terminationMessagePath", "/dev/termination-log"), ("terminationMessagePolicy", "File")):
                if container.get(key) == default:
                    container.pop(key)
            for entry in container.get("env", []):
                field = entry.get("valueFrom", {}).get("fieldRef", {})
                if field.get("apiVersion") == "v1":
                    field.pop("apiVersion")
    require(expected == actual, "native_pod_admission_drift" if pod else "native_job_admission_drift")


def verified_generated_pod_metadata(pod, node, allow_removed_finalizer=False):
    """Remove only observed native metadata after proving its exact provenance."""
    require(node.get("apiVersion") == "v1" and node.get("kind") == "Node" and node.get("metadata", {}).get("name") == pod["spec"]["nodeName"] == "talosw01" and bool(node["metadata"].get("uid")), "actual_node_identity")
    value = copy.deepcopy(pod)
    metadata = value["metadata"]
    finalizers = metadata.get("finalizers", [])
    require(finalizers == ["batch.kubernetes.io/job-tracking"] or allow_removed_finalizer and finalizers == [], "native_pod_finalizers")
    metadata.pop("finalizers", None)
    for label in ("topology.kubernetes.io/region", "topology.kubernetes.io/zone"):
        native = node["metadata"].get("labels", {}).get(label)
        require(isinstance(native, str) and bool(native) and metadata.get("labels", {}).get(label) == native, "native_pod_topology")
        metadata["labels"].pop(label)
    annotations = metadata.get("annotations", {})
    encoded = annotations.get("k8s.v1.cni.cncf.io/network-status")
    require(isinstance(encoded, str), "native_cni_annotation_missing")
    attachment = json.loads(encoded)
    require(isinstance(attachment, list) and len(attachment) == 1 and isinstance(attachment[0], dict), "native_cni_attachment_count")
    cni = attachment[0]
    require(set(cni) == {"name", "interface", "ips", "mac", "default", "dns", "gateway"}, "native_cni_attachment_keys")
    pod_ip = pod.get("status", {}).get("podIP")
    require(cni["name"] == "cilium" and cni["interface"] == "eth0" and cni["default"] is True and cni["dns"] == {} and cni["ips"] == [pod_ip] and pod.get("status", {}).get("podIPs") == [{"ip": pod_ip}], "native_cni_attachment_identity")
    require(isinstance(cni["mac"], str) and re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", cni["mac"]) and isinstance(cni["gateway"], list) and len(cni["gateway"]) == 1, "native_cni_address_shape")
    subnet = ipaddress.ip_network(node["spec"]["podCIDR"])
    require(ipaddress.ip_address(pod_ip) in subnet and ipaddress.ip_address(cni["gateway"][0]) in subnet, "native_cni_node_subnet")
    annotations.pop("k8s.v1.cni.cncf.io/network-status")
    if not annotations:
        metadata.pop("annotations")
    return value


def pod_binding(job, pod, expected, phase, node, after_ack=False):
    declared(expected, job)
    require(job["metadata"].get("labels", {}).get(LABEL) == phase, "job_phase")
    owned_pods({"apiVersion": "v1", "kind": "PodList", "metadata": {"resourceVersion": "checked"}, "items": [pod]}, expected["metadata"]["name"], phase, job["metadata"]["uid"])
    want = {"apiVersion": "v1", "kind": "Pod", "metadata": copy.deepcopy(expected["spec"]["template"]["metadata"]), "spec": copy.deepcopy(expected["spec"]["template"]["spec"])}
    want["metadata"]["name"] = pod["metadata"]["name"]
    want["metadata"]["namespace"] = NS
    # The Job controller owns this marker independently of our private ACK.
    # Its absence is native terminal metadata only, never proof/exec authority.
    removed = pod["metadata"].get("finalizers", []) == []
    if removed:
        states = pod.get("status", {}).get("containerStatuses", [])
        require(pod.get("status", {}).get("phase") == "Succeeded" and len(states) == 1 and bool(states[0].get("state", {}).get("terminated")), "native_terminal_finalizer_removed")
        # Validates the native Completed/exit0/restarts0 and failed-Job guards.
        # Complete may still be pending; it remains mandatory for final success.
        completed(job, pod)
    declared(want, verified_generated_pod_metadata(pod, node, removed), True)
    statuses = pod.get("status", {}).get("containerStatuses", [])
    require(len(statuses) == 1 and statuses[0]["name"] == "native-scanner" and statuses[0].get("restartCount") == 0 and statuses[0].get("imageID", "").endswith(IMAGE.split("@", 1)[1]), "actual_image_or_restart")


def completed(job, pod):
    status = job.get("status", {})
    conditions = status.get("conditions", [])
    require(type(status.get("failed", 0)) is int and status.get("failed", 0) == 0 and not any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions), "actual_job_failed")
    states = pod.get("status", {}).get("containerStatuses", [])
    require(len(states) == 1 and states[0].get("restartCount") == 0, "terminal_restart_count")
    terminated = states[0].get("state", {}).get("terminated")
    if terminated:
        require(type(terminated.get("exitCode")) is int and terminated["exitCode"] == 0 and terminated.get("reason") == "Completed", "child_terminal_failure")
    if not any(c.get("type") == "Complete" and c.get("status") == "True" for c in conditions):
        return False
    require(pod.get("status", {}).get("phase") == "Succeeded" and terminated is not None and type(status.get("active", 0)) is int and status.get("active", 0) == 0 and type(status.get("succeeded")) is int and status["succeeded"] == 1, "actual_job_pod_completion")
    return True


def realized_deny(cnp, cep, native, pod_uid, phase):
    require(cnp.get("spec") == {"endpointSelector": {"matchLabels": {"app.kubernetes.io/name": APP}}, "ingressDeny": [{"fromEntities": ["all"]}], "egressDeny": [{"toEntities": ["all"]}]}, "purpose_policy_changed")
    require(any(c.get("type") == "Valid" and c.get("status") == "True" for c in cnp.get("status", {}).get("conditions", [])), "purpose_policy_invalid")
    require(any(o.get("kind") == "Pod" and o.get("uid") == pod_uid for o in cep.get("metadata", {}).get("ownerReferences", [])), "endpoint_pod_uid")
    require(len(native) == 1 and native[0].get("id") == cep.get("status", {}).get("id"), "native_endpoint_id")
    status = native[0].get("status", {})
    require(status.get("state") == "ready", "endpoint_not_ready")
    policy = status.get("policy", {})
    current, wanted = policy.get("realized", {}), policy.get("spec", {})
    require(current == wanted and current.get("policy-enabled") == "both" and 0 in current.get("denied-ingress-identities", []) and 0 in current.get("denied-egress-identities", []) and current.get("policy-revision", 0) > 0, "actual_deny_not_realized")
    return {"schema": 1, "Phase": phase, "PodUid": pod_uid, "observedAt": stamp(), "cnpSha256": sha(canonical(cnp)), "endpointSha256": sha(canonical(cep)), "nativeEndpointSha256": sha(canonical(native)), "denyAllRealized": True}


def bpf_deny(entries):
    # Cilium v1.20.2 policymap.go: bit 0 denotes Deny, static wildcard
    # prefix length is 40; direction 0/1 means ingress/egress. Inspect native
    # JSON rather than infer a denial from the installed policy definition.
    require(isinstance(entries, list) and 2 <= len(entries) <= 256, "native_bpf_scope")
    for direction in (0, 1):
        wildcard = [e for e in entries if e.get("Key") == {"Prefixlen": 40, "Identity": 0, "TrafficDirection": direction, "Nexthdr": 0, "DestPortNetwork": 0}]
        require(len(wildcard) == 1 and type(wildcard[0].get("Flags")) is int and wildcard[0]["Flags"] & 1 == 1, "native_bpf_wildcard_deny")
    require(all(type(e.get("Flags")) is int and e["Flags"] & 1 == 1 for e in entries), "native_bpf_allow_present")


def validate_receipt(raw, event, phase, job_uid, pod_uid):
    require(event.get("Phase") == phase and event.get("JobUid") == job_uid and event.get("PodUid") == pod_uid, "ready_event_identity")
    require(sha(raw) == event["receiptSha256"], "receipt_hash")
    value = json.loads(raw)
    require(value.get("schema") == 1 and value.get("Phase") == phase and value.get("JobUid") == job_uid and value.get("PodUid") == pod_uid and value.get("productionAuthorization") is False and value.get("outcome") in ("passed-private-proof", "unknown"), "receipt_identity")
    pins = value.get("proofFiles", [])
    require(1 <= len(pins) <= 6 and len({p["Path"] for p in pins}) == len(pins) and all(p["Path"] in PROOFS | {DIAGNOSTIC} and re.fullmatch("[0-9a-f]{64}", p["Sha256"]) for p in pins) and pins == event["proofFiles"], "receipt_scope")
    require(value["outcome"] != "passed-private-proof" or {p["Path"] for p in pins} == PROOFS, "passed_proof_incomplete")
    return value


def validate_diagnostic(raw, harness_sha):
    require(len(raw) <= 4096, "diagnostic_size")
    value = json.loads(raw)
    require(type(value) is dict and set(value) == {"Schema", "Stage", "Kind", "HarnessSha256", "OwnedCallsites"} and type(value["Schema"]) is int and value["Schema"] == 1 and value["HarnessSha256"] == harness_sha
            and isinstance(value["Stage"], str) and value["Stage"] in {"admission", "native-build", "native-projection", "native-scan", "native-cleanup-predicate", "state-readback", "private-inverse"}
            and isinstance(value["Kind"], str) and re.fullmatch("[A-Za-z][A-Za-z0-9]{0,95}", value["Kind"]), "diagnostic_identity")
    calls = value["OwnedCallsites"]
    require(isinstance(calls, list) and 1 <= len(calls) <= 8 and all(type(c) is dict and set(c) == {"MethodToken", "IlOffset"}
            and type(c["MethodToken"]) is int and 0x06000001 <= c["MethodToken"] <= 0x06ffffff
            and type(c["IlOffset"]) is int and -1 <= c["IlOffset"] <= 1048576 for c in calls)
            and len({(c["MethodToken"], c["IlOffset"]) for c in calls}) == len(calls), "diagnostic_owned_scope")
    return value


class Native:
    def __init__(self, out, end):
        self.out, self.end = Path(out), end
        self.requests = set()

    def call(self, args, payload=None, limit=1024 * 1024, seconds=10):
        until = min(self.end, time.time() + seconds)
        require(until > time.time(), "original_deadline")
        with tempfile.TemporaryFile() as source:
            if payload is not None:
                source.write(payload)
                source.seek(0)
            require(time.time() < until, "native_request_deadline")
            process = None
            output = bytearray()
            total = 0
            try:
                # A pending deadline signal after registration still enters the
                # outer cleanup finally, so it kills/reaps the owned request.
                previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM, signal.SIGTERM, signal.SIGINT})
                try:
                    process = subprocess.Popen(args, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    self.requests.add(process)
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
                with selectors.DefaultSelector() as select:
                    select.register(process.stdout, selectors.EVENT_READ, True)
                    select.register(process.stderr, selectors.EVENT_READ, False)
                    while select.get_map():
                        require(time.time() < until, "native_request_deadline")
                        for key, _ in select.select(min(.1, max(.001, until - time.time()))):
                            chunk = os.read(key.fileobj.fileno(), 65536)
                            if not chunk:
                                select.unregister(key.fileobj)
                                continue
                            total += len(chunk)
                            require(total <= limit, "native_output_cap")
                            if key.data:
                                output.extend(chunk)
                    require(process.wait(timeout=max(.001, until - time.time())) == 0, "native_request_refused")
                return bytes(output)
            finally:
                if process is not None:
                    try:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=2)
                    finally:
                        if process.poll() is not None:
                            self.requests.discard(process)
                        process.stdout.close()
                        process.stderr.close()

    def get(self, resource, name):
        return json.loads(self.call(["kubectl", "get", resource, name, "-n", NS, "-o", "json"]))

    def list(self, kind):
        url = f"/apis/batch/v1/namespaces/{NS}/jobs" if kind == "Job" else f"/api/v1/namespaces/{NS}/pods"
        return inventory(json.loads(self.call(["kubectl", "get", "--raw", url], limit=16 * 1024 * 1024)), kind)

    def upload(self, pod_name, path, raw):
        require(path in PROOFS | {"/fixture-input/approved-packet.json", "/fixture-input/evidence-ack.json", "/fixture-input/source-proof.json", "/fixture-input/network-deny-proof.json", "/fixture-input/original.db", "/fixture-input/original.epub", "/kavita/config/kavita.db", "/kavita/config/appsettings.json"} or path.startswith("/data/cephfs-hdd/data/media/books/EBooks/") and path.endswith(".epub") and ".." not in path, "upload_path")
        result = self.call(["kubectl", "exec", "-i", "-n", NS, pod_name, "-c", "native-scanner", "--", "sh", "-c", UPLOAD, "fixture-upload", path, sha(raw)], raw, seconds=15)
        require(result.strip().decode() == sha(raw), "upload_ack")

    def cleanup(self, expected, phase, uid):
        name = expected["metadata"]["name"]
        jobs = self.list("Job")
        found = [j for j in jobs["items"] if j["metadata"]["name"] == name or j["metadata"].get("labels", {}).get(LABEL) == phase or uid is not None and j["metadata"].get("uid") == uid]
        require(len(found) <= 1, "cleanup_conflicting_jobs")
        if found:
            job = found[0]
            require(job["metadata"]["name"] == name and job["metadata"].get("namespace") == NS and (job["metadata"].get("labels", {}).get(LABEL) == phase if uid is None else job["metadata"].get("uid") == uid), "cleanup_reused_name")
            uid = job["metadata"]["uid"]
            options = {"apiVersion": "v1", "kind": "DeleteOptions", "propagationPolicy": "Foreground", "preconditions": {"uid": uid}}
            self.call(["kubectl", "delete", "--raw", f"/apis/batch/v1/namespaces/{NS}/jobs/{name}", "-f", "-"], canonical(options))
        else:
            for pod in cleanup_pods(self.list("Pod"), name, phase, uid):
                options = {"apiVersion": "v1", "kind": "DeleteOptions", "propagationPolicy": "Foreground", "preconditions": {"uid": pod["metadata"]["uid"]}}
                self.call(["kubectl", "delete", "--raw", f"/api/v1/namespaces/{NS}/pods/{pod['metadata']['name']}", "-f", "-"], canonical(options))
        while True:
            jobs, pods = self.list("Job"), self.list("Pod")
            require(not any(j["metadata"]["name"] == name and uid is not None and j["metadata"]["uid"] != uid for j in jobs["items"]), "cleanup_name_reused")
            leftovers = [j for j in jobs["items"] if j["metadata"]["name"] == name or j["metadata"].get("labels", {}).get(LABEL) == phase or uid is not None and j["metadata"].get("uid") == uid]
            if not leftovers and not cleanup_pods(pods, name, phase, uid):
                require(time.time() < self.end, "cleanup_original_cap")
                save_private(self.out / "cleanup-jobs.json", jobs)
                save_private(self.out / "cleanup-pods.json", pods)
                save_private(self.out / "cleanup-receipt.json", {"phase": phase, "jobUid": uid, "observedAt": stamp(), "allOwnedJobsPodsAbsent": True})
                return
            time.sleep(.2)


def validate_approval(value, now):
    require(value.get("schema") == 1 and value.get("explicitRootFixtureApproval") is True and value.get("runtimeApproval") is True and value.get("productionWriterApproval") is False, "exact_root_fixture_go")
    phase = value.get("phase", "")
    require(str(uuid.UUID(phase)) == phase and epoch(value["approvedAt"]) <= now < epoch(value["startBefore"]) <= epoch(value["approvedAt"]) + 300, "root_go_expired")
    require(value.get("image") == IMAGE and re.fullmatch("[0-9a-f]{64}", value.get("manifestSha256", "")), "signed_image_manifest")
    source_pins = value.get("sourcePins", [])
    require(len(source_pins) >= 4 and len({p["path"] for p in source_pins}) == len(source_pins) and any(Path(p["path"]).resolve() == Path(__file__).resolve() for p in source_pins), "approved_host_closure")
    for pin in source_pins:
        path = Path(pin["path"])
        require(path.is_absolute() and not path.is_symlink() and path.stat().st_size <= 1024 * 1024 and sha(path.read_bytes()) == pin["sha256"], "approved_source_changed")
    required_paths = {Path(__file__).resolve(), Path(schema_codec.__file__).resolve(), Path(value["templatePath"]).resolve()}
    require(required_paths <= {Path(p["path"]).resolve() for p in source_pins}, "approved_import_template_closure")
    require(value["expectedLiveNative"]["ImageTag"] == "0.9.0.2" and value["expectedLiveNative"]["ImageDigest"] == NATIVE and value["expectedLiveNative"]["TimeZone"] == "America/New_York" and set(value["expectedLiveNative"]["Modules"]) == set(MODULES), "native_source_tuple")
    fixture_modules = value.get("expectedFixtureModules", {})
    require(set(fixture_modules) == set(FIXTURE_MODULES + MODULES) and all(re.fullmatch("[0-9a-f]{64}", v) for v in fixture_modules.values()) and fixture_modules["/fixture/NativeScannerFixture.dll"] == value["fixturePacket"]["HarnessSha256"] and all(fixture_modules[p] == value["expectedLiveNative"]["Modules"][p] for p in MODULES), "reviewed_admitted_module_closure")
    uploads = value.get("uploads", [])
    require(len(uploads) == 5 and len({p["target"] for p in uploads}) == 5 and {p["target"] for p in uploads} == {"/fixture-input/original.db", "/fixture-input/original.epub", "/kavita/config/kavita.db", "/kavita/config/appsettings.json", value["fixturePacket"]["TargetFilePath"]}, "private_upload_scope")
    for pin in uploads:
        require(sha(read_private(pin["path"])) == pin["sha256"], "approved_private_input_changed")
    require(value.get("nativeFileTimestampBinding") is None, "obsolete_file_timestamp_policy")
    file_rules = [r for r in value["fixturePacket"]["ScanAllowances"] if r["Table"] == "MangaFile" and r["Field"] in ("LastModified", "LastModifiedUtc")]
    require(len(file_rules) == 2 and {r["Field"] for r in file_rules} == {"LastModified", "LastModifiedUtc"} and all(r["Id"] == 3570 and r["After"] is None and r["ScanClock"] is True for r in file_rules), "reviewed_scan_timestamp_slots")
    # Explicit exact private packet values are reviewed, not inferred from results.
    require(value["fixturePacket"].get("ExplicitRootFixtureApproval") is False and value["fixturePacket"]["Target"] == {"Library": 1, "Series": 1650, "Volume": 1800, "Chapter": 3358, "File": 3570}, "reviewed_private_target")
    require(value["fixturePacket"]["ImageDigest"] == IMAGE.split("@", 1)[1] and re.fullmatch("[0-9a-f]{64}", value["fixturePacket"]["ExpectedSchemaSha256"]), "private_native_schema")


class Fixture(Native):
    def __init__(self, approval, out):
        self.approval = approval
        self.start = time.time()
        super().__init__(out, self.start + 180)
        self.original_end = self.end
        self.phase = approval["phase"]
        self.ready = manifest(json.loads(Path(approval["templatePath"]).read_text()), self.phase)
        require(sha(canonical(self.ready)) == approval["manifestSha256"], "reviewed_manifest_changed")
        self.name = self.ready["metadata"]["name"]
        self.uid = self.pod_uid = None

    def bind_job_clock(self, job):
        require(job["metadata"]["uid"] == self.uid, "job_clock_uid_changed")
        self.job_started = epoch(job["status"]["startTime"])
        self.end = min(self.original_end, self.job_started + 180)
        require(self.job_started <= time.time() < self.end <= self.original_end, "original_job_clock")
        signal.setitimer(signal.ITIMER_REAL, max(.001, self.end - time.time()))
        save_private(self.out / "actual-execution-clock.json", {"originalHostExecutionEnd": stamp(self.original_end), "jobStartedAt": stamp(self.job_started), "collectionExpiresAt": stamp(self.end), "originalHostCleanupEnd": stamp(self.original_end + 20)})

    def approved_packet(self, source_raw, network_raw, observed):
        packet = copy.deepcopy(self.approval["fixturePacket"])
        # File observation proves upload custody only; native hooks set scan clocks.
        packet.update(ExplicitRootFixtureApproval=True, Phase=self.phase, JobUid=self.uid, PodUid=self.pod_uid, Node="talosw01", JobStartedAt=stamp(self.job_started), ExpiresAt=stamp(self.end), PrivateSourceProofSha256=sha(source_raw), NetworkDenyProofSha256=sha(network_raw))
        packet["Inputs"] = [{"Path": p["target"], "Sha256": p["sha256"]} for p in self.approval["uploads"]] + [{"Path": "/fixture-input/source-proof.json", "Sha256": sha(source_raw)}, {"Path": "/fixture-input/network-deny-proof.json", "Sha256": sha(network_raw)}]
        return packet

    def native_now(self):
        expected = self.approval["expectedLiveNative"]
        name = self.approval["nativePodName"]
        before = self.get("pod", name)
        require(before["metadata"]["uid"] == self.approval["nativePodUid"] and before["spec"]["nodeName"] == "talosw01", "native_pod_changed")
        statuses = [s for s in before["status"]["containerStatuses"] if s["name"] == "app"]
        require(len(statuses) == 1 and statuses[0].get("ready") is True and statuses[0].get("restartCount") == self.approval["nativeRestartCount"] and statuses[0]["imageID"].endswith(NATIVE) and before["spec"]["containers"][0]["image"].endswith(":" + expected["ImageTag"]), "native_live_image")
        program = "set -eu; sha256sum " + " ".join(MODULES) + "; printf '%s\\n' \"$TZ\"; date +%:z; sha256sum /usr/share/zoneinfo/America/New_York"
        raw = self.call(["kubectl", "exec", "-n", NS, name, "-c", "app", "--", "sh", "-c", program])
        lines = raw.decode().splitlines()
        require(len(lines) == 11 and lines[8] == expected["TimeZone"] and lines[10].split()[0] == self.approval["nativeZoneFileSha256"], "native_live_timezone")
        modules = dict((line.split()[1], line.split()[0]) for line in lines[:8])
        require(modules == expected["Modules"], "native_live_modules")
        observed = stamp()
        require(epoch(observed) >= self.job_started, "native_before_original_job_clock")
        after = self.get("pod", name)
        require(after["metadata"]["uid"] == before["metadata"]["uid"] and after["status"]["containerStatuses"] == before["status"]["containerStatuses"] and after["spec"] == before["spec"], "native_changed_during_read")
        offset = lines[9]
        require(re.fullmatch("[+-][0-9]{2}:[0-9]{2}", offset), "native_offset_shape")
        result = {"ObservedAt": observed, "PodUid": before["metadata"]["uid"], "ImageTag": expected["ImageTag"], "ImageDigest": NATIVE, "TimeZone": lines[8], "ActualUtcOffset": ("-" if offset[0] == "-" else "") + offset[1:] + ":00", "Modules": [{"Path": path, "Sha256": modules[path]} for path in MODULES]}
        return result

    def binding(self, after_ack=False):
        job = self.get("job", self.name)
        pod = node = None
        try:
            require(job["metadata"]["uid"] == self.uid, "job_uid_changed")
            pods = owned_pods(self.list("Pod"), self.name, self.phase, self.uid)
            require(len(pods) == 1 and pods[0]["metadata"]["uid"] == self.pod_uid, "fixture_pod_changed")
            pod = self.named_pod(pods[0])
            node = self.fixture_node(pod)
            pod_binding(job, pod, self.ready, self.phase, node, after_ack)
        except Refused:
            # Save observed responses, not a reconstructed terminal premise.
            # Publication stays within the original collection/cleanup clocks.
            for name, observed in (("job", job), ("pod", pod), ("node", node)):
                if observed is not None:
                    atomic_private_marker(self.out / ("refused-binding-" + name + ".json"), observed)
            raise
        return job, pod

    def named_pod(self, listed, retain=False):
        pod = self.get("pod", listed["metadata"]["name"])
        if retain:
            save_private(self.out / "admission-observed-pod.json", pod)
        require(pod.get("apiVersion") == "v1" and pod.get("kind") == "Pod" and pod.get("metadata", {}).get("namespace") == NS and isinstance(pod["metadata"].get("uid"), str) and bool(pod["metadata"]["uid"]) and all(pod["metadata"].get(key) == listed["metadata"].get(key) for key in ("namespace", "name", "uid")), "named_pod_identity_changed")
        return pod

    def fixture_node(self, pod, retain=False):
        require(pod["spec"]["nodeName"] == self.ready["spec"]["template"]["spec"]["nodeName"], "fixture_node_changed")
        node = self.get("node", pod["spec"]["nodeName"])
        if retain:
            save_private(self.out / "admission-observed-node.json", node)
        return node

    def network(self, pod, prefix="before"):
        cnp = self.get("ciliumnetworkpolicy", "book-native-scan-fixture")
        require(cnp["metadata"]["uid"] == self.approval["cnpUid"], "installed_policy_replaced")
        cep = self.get("ciliumendpoint", pod["metadata"]["name"])
        agent = json.loads(self.call(["kubectl", "get", "pod", self.approval["ciliumAgentName"], "-n", "kube-system", "-o", "json"]))
        require(agent["metadata"]["uid"] == self.approval["ciliumAgentUid"] and agent["spec"]["nodeName"] == "talosw01", "native_agent_changed")
        eid = str(cep["status"]["id"])
        require(eid.isdigit(), "native_endpoint_id_shape")
        args = ["kubectl", "exec", "-n", "kube-system", self.approval["ciliumAgentName"], "-c", "cilium-agent", "--", "cilium-dbg"]
        native = json.loads(self.call(args + ["endpoint", "get", eid, "-o", "json"]))
        proof = realized_deny(cnp, cep, native, self.pod_uid, self.phase)
        bpf = self.call(args + ["bpf", "policy", "get", eid, "-o", "json"])
        bpf_deny(json.loads(bpf))
        proof["nativeBpfSha256"] = sha(bpf)
        for name, value in (("actual-cnp.json", cnp), ("actual-cep.json", cep), ("actual-native-endpoint.json", native), ("actual-native-bpf.txt", bpf)):
            save_private(self.out / (prefix + "-" + name), value)
        return proof

    def fixture_modules(self, pod):
        expected = self.approval["expectedFixtureModules"]
        raw = self.call(["kubectl", "exec", "-n", NS, pod["metadata"]["name"], "-c", "native-scanner", "--", "sha256sum", *FIXTURE_MODULES, *MODULES])
        lines = raw.decode("ascii").splitlines()
        require(len(lines) == len(expected), "actual_fixture_module_count")
        actual = dict((line.split()[1], line.split()[0]) for line in lines)
        require(actual == expected, "actual_fixture_modules_changed_before_input")
        save_private(self.out / "actual-fixture-module-pins.json", actual)
        return actual

    def collect(self):
        jobs, pods = self.list("Job"), self.list("Pod")
        require(not any(j["metadata"]["name"] == self.name or j["metadata"].get("labels", {}).get(LABEL) == self.phase for j in jobs["items"]) and not owned_pods(pods, self.name, self.phase, None), "initial_owned_absence")
        save_private(self.out / "initial-jobs.json", jobs)
        save_private(self.out / "initial-pods.json", pods)
        admitted = json.loads(self.call(["kubectl", "create", "--dry-run=server", "-f", "-", "-o", "json"], canonical(self.ready)))
        save_private(self.out / "server-dry-run.json", admitted)
        declared(self.ready, admitted)
        save_private(self.out / "create-intent.json", {"phase": self.phase, "name": self.name, "startedAt": stamp()})
        created_raw = self.call(["kubectl", "create", "-f", "-", "-o", "json"], canonical(self.ready))
        atomic_private_marker(self.out / "created-job.json", created_raw)
        created = json.loads(created_raw)
        self.uid = created_uid(self.ready, created)
        declared(self.ready, created)
        while True:
            job = self.get("job", self.name)
            require(job["metadata"]["uid"] == self.uid and not job.get("status", {}).get("failed"), "fixture_failed")
            found = owned_pods(self.list("Pod"), self.name, self.phase, self.uid)
            require(len(found) <= 1, "fixture_multiple_pods")
            if found and found[0].get("status", {}).get("phase") == "Running":
                self.pod_uid = found[0]["metadata"]["uid"]
                pod = found[0]
                break
            time.sleep(.2)
        save_private(self.out / "admission-observed-job.json", job)
        save_private(self.out / "admission-observed-pod-list.json", pod)
        pod = self.named_pod(pod, retain=True)
        node = self.fixture_node(pod, retain=True)
        pod_binding(job, pod, self.ready, self.phase, node)
        self.bind_job_clock(job)
        save_private(self.out / "running-job.json", job)
        save_private(self.out / "running-pod.json", pod)
        network = self.network(pod)
        native = self.native_now()
        source = copy.deepcopy(self.approval["sourceProof"])
        require("LiveNative" not in source, "old_live_proof_present")
        source["LiveNative"] = native
        source["AdmittedFixtureModules"] = self.fixture_modules(pod)
        for pin in self.approval["uploads"]:
            raw = read_private(pin["path"])
            require(sha(raw) == pin["sha256"], "private_input_changed_at_send")
            self.binding()
            self.upload(pod["metadata"]["name"], pin["target"], raw)
        candidate = next(p for p in self.approval["uploads"] if p["target"] == self.approval["fixturePacket"]["TargetFilePath"])
        candidate = {**candidate, "size": len(read_private(candidate["path"]))}
        program = 'set -eu; stat -c "%Y|%y|%s|%d:%i" -- "$1"; sha256sum -- "$1"; stat -c "%Y|%y|%s|%d:%i" -- "$1"'
        observed = file_timestamp_binding(self.call(["kubectl", "exec", "-n", NS, pod["metadata"]["name"], "-c", "native-scanner", "--", "sh", "-c", program, "fixture-stat", candidate["target"]]), candidate)
        source["CandidateFileTimestamp"] = observed
        save_private(self.out / "actual-candidate-file-timestamp.json", observed)
        source_raw, network_raw = canonical(source), canonical(network)
        packet = self.approved_packet(source_raw, network_raw, observed)
        save_private(self.out / "actual-source-proof.json", source_raw)
        save_private(self.out / "actual-network-proof.json", network_raw)
        save_private(self.out / "actual-approved-packet.json", packet)
        self.upload(pod["metadata"]["name"], "/fixture-input/source-proof.json", source_raw)
        self.upload(pod["metadata"]["name"], "/fixture-input/network-deny-proof.json", network_raw)
        self.binding()
        require(self.native_now()["Modules"] == native["Modules"], "native_changed_before_packet")
        self.network(pod, "before-packet")
        self.upload(pod["metadata"]["name"], "/fixture-input/approved-packet.json", canonical(packet))
        event = None
        while event is None:
            raw = self.call(["kubectl", "logs", "-n", NS, pod["metadata"]["name"], "-c", "native-scanner"], seconds=5)
            events = [json.loads(line) for line in raw.splitlines() if line.strip()]
            require(not any(e.get("result") == "REFUSED" for e in events), "native_fixture_refused")
            ready = [e for e in events if e.get("result") == "PRIVATE_PROOF_READY"]
            require(len(ready) <= 1, "proof_ready_repeated")
            if ready:
                event = ready[0]
                save_private(self.out / "ready-event.json", event)
                break
            self.binding()
            time.sleep(.2)
        receipt = self.call(["kubectl", "exec", "-n", NS, pod["metadata"]["name"], "-c", "native-scanner", "--", "cat", "/kavita/config/proof-receipt.json"])
        value = validate_receipt(receipt, event, self.phase, self.uid, self.pod_uid)
        for pin in value["proofFiles"]:
            raw = self.call(["kubectl", "exec", "-n", NS, pod["metadata"]["name"], "-c", "native-scanner", "--", "cat", pin["Path"]], limit=32 * 1024 * 1024)
            require(sha(raw) == pin["Sha256"], "private_proof_copy_hash")
            save_private(self.out / Path(pin["Path"]).name, raw)
            if pin["Path"] == DIAGNOSTIC:
                validate_diagnostic(raw, self.approval["fixturePacket"]["HarnessSha256"])
        save_private(self.out / "proof-receipt.json", receipt)
        _, current = self.binding()
        self.native_now()
        self.network(current, "after-proof")
        ack = {"Phase": self.phase, "JobUid": self.uid, "PodUid": self.pod_uid, "ReceiptSha256": sha(receipt), "DurablyCopied": True}
        save_private(self.out / "evidence-ack.json", ack)
        self.upload(pod["metadata"]["name"], "/fixture-input/evidence-ack.json", canonical(ack))
        while True:
            job, current = self.binding(after_ack=True)
            if completed(job, current):
                break
            time.sleep(.2)
        final_raw = self.call(["kubectl", "logs", "-n", NS, pod["metadata"]["name"], "-c", "native-scanner"])
        final = [json.loads(line) for line in final_raw.splitlines() if line.strip()]
        passes = [e for e in final if e.get("result") == "PASS_ACTUAL_NATIVE_SCANNER_PRIVATE_FIXTURE"]
        require(len(passes) == 1 and passes[0].get("Phase") == self.phase and passes[0].get("JobUid") == self.uid and passes[0].get("PodUid") == self.pod_uid and passes[0].get("hostApplicationStarted") is False and passes[0].get("savedAndCurationRowsExact") is True and passes[0].get("cleanupRemoved") == 0 and passes[0].get("productionAuthorization") is False, "actual_native_pass_missing")
        save_private(self.out / "actual-native-pass.json", passes[0])
        return passes[0]


def cleanup_locked(state, out):
    lock = os.open(Path(out) / "cleanup.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (Path(out) / "cleanup-receipt.json").exists():
            return
        retired = Path(out) / "execution-retired.json"
        require(retired.exists() and json.loads(read_private(retired))["phase"] == state["phase"], "requests_not_retired")
        uid = None
        created = Path(out) / "created-job.json"
        if created.exists():
            uid = created_uid(state["manifest"], json.loads(read_private(created, 1024 * 1024)))
        # Lost CREATE transport can recover only after its own durable intent and
        # full initial absence, never by adopting an arbitrary matching name.
        elif (Path(out) / "create-intent.json").exists():
            initial = inventory(json.loads(read_private(Path(out) / "initial-jobs.json", 16 * 1024 * 1024)), "Job")
            name = state["manifest"]["metadata"]["name"]
            require(not any(j["metadata"]["name"] == name or j["metadata"].get("labels", {}).get(LABEL) == state["phase"] for j in initial["items"]), "recovery_initial_absence")
            recovered_path = Path(out) / "recovered-job.json"
            if recovered_path.exists():
                recovered = json.loads(read_private(recovered_path, 1024 * 1024))
            else:
                recovered = Native(out, state["end"] + 20).get("job", name)
                require(recovered.get("metadata", {}).get("labels", {}).get(LABEL) == state["phase"], "cleanup_reused_name")
                created_uid(state["manifest"], recovered)
                atomic_private_marker(recovered_path, recovered)
            uid = created_uid(state["manifest"], recovered)
            # A lost creation response plus NotFound is still unknown: an API
            # commit may be late. Never turn that observation into absence.
        else:
            require(not any(j["metadata"]["name"] == state["manifest"]["metadata"]["name"] or j["metadata"].get("labels", {}).get(LABEL) == state["phase"] for j in Native(out, state["end"] + 20).list("Job")["items"]), "unowned_creation_without_intent")
        Native(out, state["end"] + 20).cleanup(state["manifest"], state["phase"], uid)
    finally:
        os.close(lock)


def kill_owned_runner(state):
    pid = state["runnerPid"]
    try:
        ticks = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        if ticks == state["runnerStartTicks"] and os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, FileNotFoundError):
        pass


def watchdog(path):
    state = json.loads(read_private(path, 1024 * 1024))
    out = Path(path).parent
    cap = state["end"] + 20
    last_error = None
    while time.time() < cap:
        if (out / "cleanup-receipt.json").exists():
            save_private(out / "watchdog-receipt.json", {"phase": state["phase"], "allOwnedJobsPodsAbsent": True, "observedAt": stamp()})
            return
        if time.time() >= state["end"] and (out / "execution-retired.json").exists():
            try:
                cleanup_locked(state, out)
            except Exception as error:
                last_error = type(error).__name__
        time.sleep(max(0, min(.2, cap - time.time())))
    if (out / "cleanup-receipt.json").exists():
        save_private(out / "watchdog-receipt.json", {"phase": state["phase"], "allOwnedJobsPodsAbsent": True, "observedAt": stamp()})
        return
    # Never begin cleanup with zero budget. No 220s extension. A main host that
    # has not retired its request process cannot establish absence at all.
    kill_owned_runner(state)
    save_private(out / "watchdog-receipt.json", {"phase": state["phase"], "unknown": True, "allOwnedJobsPodsAbsent": False, "lastCleanupKind": last_error, "observedAt": stamp()})


def run(approval_path, out, approved_sha):
    raw = read_private(approval_path, 1024 * 1024)
    require(re.fullmatch("[0-9a-f]{64}", approved_sha or "") and sha(raw) == approved_sha, "exact_reviewed_approval_sha")
    approval = json.loads(raw)
    validate_approval(approval, time.time())
    # Never place another shared-shell/agent process in the watchdog kill group.
    if os.getpgrp() != os.getpid():
        os.setsid()
    require(os.getpgrp() == os.getpid(), "dedicated_host_process_group")
    out = Path(out)
    out.mkdir(mode=0o700)
    fixture = Fixture(approval, out)
    state = {"phase": fixture.phase, "manifest": fixture.ready, "end": fixture.end, "runnerPid": os.getpid(), "runnerStartTicks": Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()[19]}
    save_private(out / "watchdog-state.json", state)
    frozen_source = Path(__file__).read_bytes()
    own_pin = next(p for p in approval["sourcePins"] if Path(p["path"]).resolve() == Path(__file__).resolve())
    require(sha(frozen_source) == own_pin["sha256"], "host_source_changed_before_watchdog")
    save_private(out / "frozen-host.py", frozen_source)
    codec_source = Path(schema_codec.__file__).read_bytes()
    codec_pin = next(p for p in approval["sourcePins"] if Path(p["path"]).resolve() == Path(schema_codec.__file__).resolve())
    require(sha(codec_source) == codec_pin["sha256"], "codec_changed_before_watchdog")
    save_private(out / "schema_codec.py", codec_source)
    watcher = subprocess.Popen([sys.executable, "-B", str(out / "frozen-host.py"), "--watchdog", str(out / "watchdog-state.json")], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    save_private(out / "watchdog-process.json", {"pid": watcher.pid, "observedAt": stamp(), "originalEnd": stamp(fixture.end)})
    def stop(*_):
        raise Refused("host_original_deadline_or_interruption")
    handlers = {s: signal.signal(s, stop) for s in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT)}
    signal.setitimer(signal.ITIMER_REAL, max(.001, fixture.end - time.time()))
    result = {"phase": fixture.phase, "productionAuthorization": False, "unknown": True}
    try:
        result["nativeProof"] = fixture.collect()
        result["unknown"] = False
    except BaseException as error:
        result.update(kind=type(error).__name__, code=str(error) if isinstance(error, Refused) else None)
    finally:
        # collect() has fully unwound Native.call, which kills and reaps its
        # active subprocess in finally; no collection method is called again.
        retire_collection(fixture, out, state["end"])
        try:
            cleanup_locked(state, out)
            require((out / "cleanup-receipt.json").exists(), "cleanup_unproved")
            result["allOwnedJobsPodsAbsent"] = True
        except BaseException as error:
            result.update(unknown=True, cleanupKind=type(error).__name__)
        save_private(out / "host-receipt.json", result)
        signal.setitimer(signal.ITIMER_REAL, 0)
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        # The independent watcher observes durable cleanup, otherwise retains its
        # original deadline. Never kill it merely because the main host stopped.
        if result.get("allOwnedJobsPodsAbsent"):
            try:
                watcher.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(watcher.pid, signal.SIGTERM)
                watcher.wait(timeout=2)
    print(json.dumps({k: v for k, v in result.items() if k != "nativeProof"}))
    return 2 if result["unknown"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-private-approval", type=Path)
    parser.add_argument("--run-approved", type=Path)
    parser.add_argument("--private-output", type=Path)
    parser.add_argument("--approval-sha256")
    parser.add_argument("--watchdog", type=Path)
    args = parser.parse_args()
    if args.watchdog:
        return watchdog(args.watchdog)
    if args.run_approved:
        require(args.private_output is not None, "private_output_missing")
        return run(args.run_approved, args.private_output, args.approval_sha256)
    require(args.validate_private_approval is not None, "runtime_not_authorized")
    approval = json.loads(read_private(args.validate_private_approval, 1024 * 1024))
    require(approval.get("preparedOnly") is True and approval.get("runtimeApproval") is False, "preparation_wrapper_only")
    # The executable runtime binding is deliberately separate from preparation.
    # No caller can turn this validating entry point into a Job create operation.
    print(json.dumps({"result": "PASS_PREPARATION_ONLY", "approvalSha256": sha(canonical(approval)), "productionWrites": 0, "jobsCreated": 0}))


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as error:
        print(json.dumps({"result": "REFUSED", "kind": type(error).__name__, "code": str(error) if isinstance(error, Refused) else None, "outcome": "unknown", "productionAuthorization": False}))
        sys.exit(2)
