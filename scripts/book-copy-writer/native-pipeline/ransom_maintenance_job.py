"""Distinct Ransom writer with a retired read-only prelude; COPY is unchanged."""
import contextlib
import argparse
import ast
import copy
import datetime as dt
import hashlib
import json
import os
import posixpath
from pathlib import Path
import re
import selectors
import shlex
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace
import urllib.request

# Support the exact isolated host/Job entrypoint without PYTHONPATH. The host
# source and installed image metadata helper must be the already qualified bytes.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
CATALOG_SHA = "02e393a54c941fefef46f349c536b3cb916a48e2d79f88cda00cbd069c8e11b8"
if hashlib.sha256((HERE / "ransom_catalog_maintenance.py").read_bytes()).hexdigest() != CATALOG_SHA:
    raise ValueError("reviewed catalog source bytes differ")
metadata_candidates = [HERE / "epub_metadata.py", Path("/copy-writer/epub_metadata.py")]
if len(HERE.parents) > 2:
    metadata_candidates.append(HERE.parents[2] / "kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert/epub_metadata.py")
metadata_source = next((path for path in metadata_candidates if path.is_file()), None)
if metadata_source is not None:
    if hashlib.sha256(metadata_source.read_bytes()).hexdigest() != "ce3c5a271cb4c94d91f3154240b4cfbc5e0cc57981969fc5c975a0c0f50eb773":
        raise ValueError("qualified metadata source bytes differ")
    sys.path.insert(0, str(metadata_source.parent))
import ransom_catalog_maintenance as catalog

IMAGE = "ghcr.io/thaynes43/book-copy-writer@sha256:628e97b8dbcc83a4d7068484b516b21dde740c4dd130d54f3b030f1ee7a75601"
LIBRARY = "/data/cephfs-hdd/data/media/books/EBooks"
STATE = "/data/cephfs-hdd/data/media/books/.epub-convert"
DB = "/kavita/config/kavita.db"
LABEL = "issue825.haynesnetwork/phase"
PG_NAME = "issue831-manual-copy-writer"
BOOK_NFS = {"server": "gasha01.haynesnetwork", "path": "/hdd-nfs-repl/data/media/books"}


def private(path, sha=None, cap=32 * 1024 * 1024):
    with catalog.metadata.safe_directory(str(Path(path).parent)) as directory:
        raw, info = catalog.metadata.read_regular(directory, Path(path).name, cap)
    catalog.require(info.st_uid == os.getuid() and info.st_nlink == 1 and info.st_mode & 0o777 == 0o600,
                    "private maintenance input identity/mode differs")
    catalog.require(sha is None or hashlib.sha256(raw).hexdigest() == sha, "maintenance source hash differs")
    return raw


def module(name, ref):
    raw = private(ref["path"], ref["sha256"])
    loaded = ModuleType(name)
    loaded.__file__ = ref["path"]
    sys.modules[name] = loaded
    exec(compile(raw, ref["path"], "exec"), loaded.__dict__)
    return loaded


def validate_bootstrap(config):
    refs = config["bootstrap_sources"]
    names = {"ransom_catalog_maintenance.py", "ransom_maintenance_job.py"}
    catalog.require(len(refs) == 2 and {ref["name"] for ref in refs} == names, "exact two bootstrap sources required")
    for ref in refs:
        raw = private(ref["path"], ref["sha256"])
        catalog.require(raw == (HERE / ref["name"]).read_bytes(), "host/Job bootstrap source differs")


def writer_row(phase, config):
    rows = [row for row in phase["owned_jobs"] if row.get("writer") is True]
    catalog.require(len(rows) == 1 and (rows[0]["namespace"], rows[0]["name"]) == ("media", config["job_name"]),
                    "exact singular Ransom writer required")
    return rows[0]


def helper_row(phase, config):
    rows = [row for row in phase["owned_jobs"] if (row["namespace"], row["name"]) == ("media", config["lidarr_helper"]["name"])]
    catalog.require(len(rows) == 1 and rows[0]["writer"] is False, "exact read-only Lidarr helper required")
    return rows[0]


def current_publisher_ref(config):
    ref = config["publisher_proof"]
    if ref is None:
        binding = json.loads(private(config["publisher_proof_binding_output"]))
        catalog.require(set(binding) == {"schema", "phase_token", "scope_sha256", "proof"}
                        and binding["schema"] == 1 and binding["phase_token"] == config["phase_token"]
                        and binding["scope_sha256"] == config["publisher_scope_sha256"]
                        and binding["proof"]["path"] == config["publisher_proof_output"]
                        and re.fullmatch(r"[0-9a-f]{64}", binding["proof"]["sha256"]),
                        "automatic publisher proof binding differs")
        ref = binding["proof"]
    private(ref["path"], ref["sha256"])
    return ref


def one_job_phase(config, core):
    phase = json.loads(private(config["phase_state"]))
    core.validate_state(phase, config["restore_pr"])
    catalog.require(phase["phase_token"] == config["phase_token"] and len(phase["owned_jobs"]) == 2,
                    "maintenance requires exactly one writer and one read-only helper")
    job = writer_row(phase, config)
    helper = helper_row(phase, config)
    ref = config["lidarr_helper"]["source_template"]
    initial = json.loads(private(ref["path"], ref["sha256"]))
    for metadata in (initial["metadata"], initial["spec"]["template"]["metadata"]):
        metadata.setdefault("labels", {})[LABEL] = config["phase_token"]
    catalog.require(helper["initial_manifest"] == initial
                    and (initial["metadata"]["namespace"], initial["metadata"]["name"]) == ("media", config["lidarr_helper"]["name"])
                    and helper["gate_env"] == "LIDARR_CAPTURE_PHASE_READY"
                    and set(helper["mutable_env"]) == {"LIDARR_CAPTURE_PHASE_READY", "LIDARR_CAPTURE_DEADLINE_EPOCH"},
                    "Lidarr immutable template or read-only profile differs")
    source = initial["spec"]["template"]["spec"]
    catalog.require(initial["spec"]["activeDeadlineSeconds"] == 120 and initial["spec"]["backoffLimit"] == 0
                    and source["automountServiceAccountToken"] is False and len(source["containers"]) == 1
                    and source["containers"][0]["image"] == IMAGE
                    and source["volumes"] == [{"name": "config", "persistentVolumeClaim": {"claimName": "lidarr", "readOnly": True}},
                                              {"name": "tmp", "emptyDir": {"sizeLimit": "32Mi"}}]
                    and source["containers"][0]["volumeMounts"] == [{"name": "config", "mountPath": "/source", "readOnly": True},
                                                                    {"name": "tmp", "mountPath": "/tmp"}],
                    "Lidarr helper must retain its exact read-only storage/image/lifetime")
    template = config["job_manifest"]["spec"]["template"]["spec"]
    volumes = {volume["name"]: volume for volume in template["volumes"]}
    expected = job_manifest(config["phase_token"], config["job_name"],
                            template["nodeSelector"]["kubernetes.io/hostname"], config["claim_name"], volumes["books"]["nfs"])
    catalog.require(job["writer"] is True and job["gate_env"] is None and not job["mutable_env"]
                    and job["ready_manifest"] == config["job_manifest"] == expected
                    and job["namespace"] == "media" and job["name"] == config["job_name"],
                    "maintenance checkpoint intent differs")
    # Separate private owner, not an unknown extra field in the generic ledger.
    lease = json.loads(private(config["pg_owner"]))
    catalog.require(set(lease) == {"phase_token", "application_name", "backend_pid", "job_uid", "pod_uid"}
                    and lease["phase_token"] == phase["phase_token"] and lease["application_name"] == PG_NAME,
                    "maintenance PG owner differs")
    if lease["backend_pid"] is not None:
        catalog.require(type(lease["backend_pid"]) is int and lease["backend_pid"] > 0
                        and lease["job_uid"] == job["uid"] and isinstance(lease["pod_uid"], str)
                        and re.fullmatch(r"[0-9a-f-]{36}", lease["pod_uid"]), "maintenance live PG binding differs")
    else:
        catalog.require(lease["job_uid"] is None and lease["pod_uid"] is None, "partial PG binding differs")
    projected = copy.deepcopy(phase)
    projected["pg_leases"] = [lease]
    return projected


def lock_io(state, expected=None, release=False):
    """Exact empty-directory CAS, also executed unchanged in the admitted Sonarr Pod."""
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in state.strip("/").split("/"):
            if part in ("", ".", ".."):raise ValueError("unsafe converter state path")
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory);directory = child
        parent = os.fstat(directory)
        if expected is not None and parent.st_ino != expected["state_inode"]:
            raise ValueError("original converter state directory changed")
        try:child = os.open("lock", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:return {"absent": True, "state_inode": parent.st_ino}
        try:
            info = os.fstat(child)
            identity = {"state_inode": parent.st_ino, "lock_inode": info.st_ino,
                        "lock_uid": info.st_uid, "lock_mode": stat.S_IMODE(info.st_mode)}
            result = dict(identity, absent=False, empty=not os.listdir(child))
            if release:
                if expected != identity or not result["empty"]:raise ValueError("unknown, changed or nonempty converter lock")
                current = os.stat("lock", dir_fd=directory, follow_symlinks=False)
                if (current.st_ino, current.st_uid, stat.S_IMODE(current.st_mode)) != (info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)):
                    raise ValueError("converter lock changed before release")
                os.rmdir("lock", dir_fd=directory)
                if os.path.lexists("/proc/self/fd/" + str(directory) + "/lock"):raise ValueError("converter lock still present")
                result["absent"] = True
            return result
        finally:os.close(child)
    finally:os.close(directory)


def sonarr_lock_program(access, expected, release):
    tools = access["tools"]
    catalog.require(set(tools) == {"sh", "stat", "sha256sum", "id", "rmdir", "find"}
                    and type(access["runtime_uid"]) is int and access["runtime_uid"] >= 0,
                    "Sonarr native capability contract differs")
    for ref in tools.values():
        catalog.require(set(ref) == {"path", "sha256"} and re.fullmatch(r"/[A-Za-z0-9_./-]+", ref["path"])
                        and posixpath.normpath(ref["path"]) == ref["path"] and re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]),
                        "Sonarr native executable identity differs")
    catalog.require(type(release) is bool and (not release or expected is not None), "owned lock release needs exact custody")
    if expected is not None:
        catalog.require(set(expected) == {"state_inode", "lock_inode", "lock_uid", "lock_mode"}
                        and all(type(v) is int and v >= 0 for v in expected.values())
                        and expected["state_inode"] > 0 and expected["lock_inode"] > 0
                        and expected["lock_mode"] <= 0o7777, "owned lock identity differs")
    lines = ["set -efu", "sha=" + shlex.quote(tools["sha256sum"]["path"])]
    for name, ref in tools.items():
        lines += ["checksum=$(\"$sha\" " + shlex.quote(ref["path"]) + ")",
                  '[ "${checksum%% *}" = ' + shlex.quote(ref["sha256"]) + " ] || exit 41"]
        lines.append(name + "_tool=" + shlex.quote(ref["path"]))
    lines += ["wanted_uid=" + str(access["runtime_uid"]), "state=" + shlex.quote(STATE),
              "wanted_state=" + (str(expected["state_inode"]) if expected else "0"),
              "wanted_lock=" + (str(expected["lock_inode"]) if expected else "0"),
              "wanted_lock_uid=" + (str(expected["lock_uid"]) if expected else "0"),
              "wanted_mode=" + (format(expected["lock_mode"], "o") if expected else "0"),
              "release=" + str(int(release)), r'''
[ "$("$id_tool" -u)" = "$wanted_uid" ] || exit 42
[ -w "$state" ] && [ -x "$state" ] || exit 43
saved_ifs=$IFS; IFS=/; set -- $state; IFS=$saved_ifs
prefix=/
for part do
  [ -n "$part" ] || continue
  prefix=${prefix%/}/$part
  [ "$("$stat_tool" -c '%F' "$prefix")" = directory ] || exit 43
done
cd -P "$state"
[ "$(pwd -P)" = "$state" ] || exit 43
state_inode=$("$stat_tool" -c '%i' .)
[ "$("$stat_tool" -c '%i' "$state")" = "$state_inode" ] || exit 43
[ "$wanted_state" = 0 ] || [ "$state_inode" = "$wanted_state" ] || exit 43
if [ ! -e lock ] && [ ! -L lock ]; then
  printf 'state|%s\nabsent|1\n' "$state_inode"; exit 0
fi
[ "$("$stat_tool" -c '%F' lock)" = directory ] || exit 44
identity=$("$stat_tool" -c '%i|%u|%a' lock)
saved_ifs=$IFS; IFS='|'; set -- $identity; IFS=$saved_ifs
lock_inode=$1; lock_uid=$2; lock_mode=$3
entry=$("$find_tool" lock -mindepth 1 -maxdepth 1 -print -quit)
empty=0; [ -n "$entry" ] || empty=1
absent=0
if [ "$release" = 1 ]; then
  [ "$lock_inode" = "$wanted_lock" ] && [ "$lock_uid" = "$wanted_lock_uid" ] && [ "$lock_mode" = "$wanted_mode" ] || exit 44
  [ "$empty" = 1 ] || exit 45
  [ "$("$stat_tool" -c '%F' lock)" = directory ] && [ "$("$stat_tool" -c '%i|%u|%a' lock)" = "$identity" ] || exit 44
  [ "$("$stat_tool" -c '%i' "$state")" = "$state_inode" ] || exit 43
  "$rmdir_tool" -- lock
  [ ! -e lock ] && [ ! -L lock ] || exit 46
  absent=1
fi
[ "$("$stat_tool" -c '%i' "$state")" = "$state_inode" ] || exit 43
printf 'state|%s\nabsent|%s\nlock|%s|%s|%s\nempty|%s\n' "$state_inode" "$absent" "$lock_inode" "$lock_uid" "$lock_mode" "$empty"
''']
    return "\n".join(lines)


def sonarr_lock(config, get, run, expected=None, release=False):
    access = config["converter_lock_access"]
    catalog.require(access["namespace"] == "media" and access["container"] == "app"
                    and access["nfs"] == {"server": BOOK_NFS["server"], "path": "/hdd-nfs-repl"}
                    and access["mount_path"] == "/data/cephfs-hdd", "exact Sonarr NAS route differs")
    pod = get("pod", access["pod_name"], "media")
    states = [s for s in pod.get("status", {}).get("containerStatuses", []) if s.get("name") == "app"]
    catalog.require(pod["metadata"]["uid"] == access["pod_uid"] and pod["spec"] == access["pod_spec"]
                    and not pod["metadata"].get("deletionTimestamp") and pod["spec"]["nodeName"] == access["node_name"]
                    and pod["metadata"].get("labels", {}).get("app.kubernetes.io/name") == "sonarr"
                    and pod.get("status", {}).get("phase") == "Running"
                    and len(states) == 1 and states[0]["name"] == "app" and states[0].get("ready") is True
                    and states[0]["imageID"] == access["image_id"] and states[0]["restartCount"] == access["restart_count"],
                    "admitted Sonarr execution owner differs")
    volumes = [v for v in pod["spec"]["volumes"] if v["name"] == access["volume_name"]]
    containers = [c for c in pod["spec"]["containers"] if c["name"] == "app"]
    mounts = [m for c in containers for m in c["volumeMounts"] if m["name"] == access["volume_name"]]
    catalog.require(len(volumes) == len(mounts) == 1 and volumes[0].get("nfs") == access["nfs"]
                    and mounts[0] in ({"name": access["volume_name"], "mountPath": access["mount_path"]},
                                      {"name": access["volume_name"], "mountPath": access["mount_path"], "readOnly": False}),
                    "Sonarr exact RW NFS mount differs")
    catalog.require(posixpath.join(access["nfs"]["path"], STATE.removeprefix(access["mount_path"] + "/"))
                    == BOOK_NFS["path"] + "/.epub-convert", "NAS logical converter state path differs")
    program = sonarr_lock_program(access, expected, release)
    raw = run(["kubectl", "exec", "-n", "media", access["pod_name"], "-c", "app", "--",
               access["tools"]["sh"]["path"], "-c", program], timeout=10)
    catalog.require(isinstance(raw, str) and len(raw) <= 256, "Sonarr lock scalar output exceeds bound")
    rows = [line.split("|") for line in raw.splitlines()]
    catalog.require(len(rows) in (2, 4) and [r[0] for r in rows] == (["state", "absent"] if len(rows) == 2 else ["state", "absent", "lock", "empty"])
                    and all(re.fullmatch(r"[0-9]+", value) for row in rows for value in row[1:]), "Sonarr lock scalar schema differs")
    catalog.require([len(r) for r in rows] == ([2, 2] if len(rows) == 2 else [2, 2, 4, 2])
                    and rows[1][1] in ("0", "1") and int(rows[0][1]) > 0
                    and (len(rows) == 4 or rows[1][1] == "1"), "Sonarr lock scalar identity differs")
    result = {"state_inode": int(rows[0][1]), "absent": rows[1][1] == "1"}
    if len(rows) == 4:
        catalog.require(rows[3][1] in ("0", "1") and re.fullmatch(r"[0-7]{1,4}", rows[2][3]), "Sonarr lock mode/empty scalar differs")
        result.update(lock_inode=int(rows[2][1]), lock_uid=int(rows[2][2]), lock_mode=int(rows[2][3], 8), empty=rows[3][1] == "1")
    return result


def lock_admission(config, get, run):
    catalog.require(config["owner_approved"] is True and config["root_lock_admission_go"] is True,
                    "pre-Stop lock access admission missing")
    result = sonarr_lock(config, get, run)
    catalog.require(result["absent"] is True, "pre-Stop converter lock exists")
    return {"phase_token": config["phase_token"], "access_sha256": catalog.digest(config["converter_lock_access"]),
            "admitted_epoch": time.time(), "state_inode": result["state_inode"], "lock_absent": True}


def record_lock_custody(config, event, lease):
    custody = event["lock_custody"]
    fields = {"state_inode", "lock_inode", "lock_uid", "lock_mode", "created_after_absent", "nas", "logical_path"}
    admission = json.loads(private(config["converter_lock_admission"]["path"], config["converter_lock_admission"]["sha256"]))
    catalog.require(set(custody) == fields and custody["created_after_absent"] is True
                    and custody["nas"] == BOOK_NFS and custody["logical_path"] == BOOK_NFS["path"] + "/.epub-convert/lock"
                    and custody["state_inode"] == admission["state_inode"] and custody["lock_uid"] == 1000
                    and all(type(custody[k]) is int and custody[k] > 0 for k in ("state_inode", "lock_inode", "lock_mode"))
                    and custody["lock_mode"] <= 0o7777 and custody["lock_mode"] & 0o4000 == 0
                    and event["phase_token"] == config["phase_token"]
                    and lease["backend_pid"] == event["backend_pid"] and lease["phase_token"] == config["phase_token"]
                    and lease["job_uid"] and lease["pod_uid"] == config["pod_uid"], "exact new converter lock custody differs")
    proof = dict(custody, schema=1, phase_token=config["phase_token"], operation_sha256=event["operation_sha256"],
                 backend_pid=lease["backend_pid"], job_uid=lease["job_uid"], pod_uid=lease["pod_uid"])
    output = Path(config["converter_lock_custody_output"])
    catalog.require(output.is_absolute() and os.path.commonpath((str(output), LIBRARY)) != LIBRARY,
                    "lock custody must be retained outside EBooks")
    if os.path.lexists(output):catalog.require(json.loads(private(output)) == proof, "original converter lock custody changed")
    else:
        with catalog.metadata.safe_directory(str(output.parent)) as fd:
            catalog.metadata._write_file(fd, output.name, catalog.canonical(proof))


def accepted_watcher_cache(config, watch, phase):
    path = watch.args.cached_source_receipt
    sha = watch.state.get("cached_source_receipt_sha256")
    catalog.require(path == config["watcher_arguments"]["cached_source_receipt"]
                    and phase["phase_token"] == config["phase_token"]
                    and watch.state["cached_source_owner"]["phase_token"] == phase["phase_token"]
                    and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha),
                    "original watcher has not accepted this phase's exact cache")
    receipt = json.loads(private(path, sha))
    catalog.require(receipt["phase_token"] == phase["phase_token"], "accepted cache phase differs")
    return receipt


def maintenance_watchdog(base, core, config, supervisor, publishers):
    """Only the distinct ledger admission differs; cleanup/restore stay inherited."""
    class MaintenanceWatchdog(base.Watchdog):
        phase_resources_present = base.PhaseResourcesPresent
        def phase_checkpoint(self):
            return one_job_phase(config, core)
        def verify_only_helper(self, phase, pod_uid):
            row = helper_row(phase, config)
            catalog.require(row["uid"] is not None and writer_row(phase, config)["uid"] is None,
                            "read-only prelude requires its exact UID and absent writer")
            names = {r["name"] for r in phase["owned_jobs"]}
            uids = {r["uid"] for r in phase["owned_jobs"] if r["uid"] is not None}
            for namespace in ("frontend", "downloads", "media"):
                for kind in ("Job", "Pod"):
                    for item in self.inventory(kind, namespace):
                        meta = item["metadata"]
                        relevant = (meta.get("labels", {}).get(LABEL) == phase["phase_token"]
                                    or meta["uid"] in uids or kind == "Job" and meta["name"] in names
                                    or any(o.get("kind") == "Job" and (o.get("name") in names or o.get("uid") in uids)
                                           for o in meta.get("ownerReferences", [])))
                        if not relevant:continue
                        catalog.require(namespace == "media" and meta.get("labels", {}).get(LABEL) == phase["phase_token"]
                                        and (kind == "Job" and meta["name"] == row["name"] and meta["uid"] == row["uid"]
                                             or kind == "Pod" and meta["uid"] == pod_uid
                                             and meta.get("ownerReferences") == [{"apiVersion": "batch/v1", "kind": "Job", "name": row["name"],
                                                                                 "uid": row["uid"], "controller": True, "blockOwnerDeletion": True}]),
                                        "unknown or writable resource in read-only prelude union")
            # The full typed union above admits only the separately verified RO
            # Job/Pod; reuse the original primary PG-absence check unchanged.
            super().verify_phase_absent(dict(phase, owned_jobs=[]))
        def verify_phase_absent(self, phase):
            uids = {row["uid"] for row in phase["owned_jobs"] if row["uid"] is not None}
            for namespace in ("frontend", "downloads"):
                for kind in ("Job", "Pod"):
                    for item in self.inventory(kind, namespace):
                        meta = item["metadata"]
                        if (meta.get("labels", {}).get(LABEL) == phase["phase_token"]
                                or any(o.get("kind") == "Job" and o.get("uid") in uids
                                                          for o in meta.get("ownerReferences", []))):
                            raise base.PhaseResourcesPresent("Unexpected maintenance phase resource; retain holds")
            super().verify_phase_absent(phase)  # Original writer/Pod/primary PG absence FIRST.
            def route(expected=None, release=False):return sonarr_lock(config, self.kube, self.run, expected, release)
            observed = route()
            admission = json.loads(private(config["converter_lock_admission"]["path"], config["converter_lock_admission"]["sha256"]))
            catalog.require(admission["phase_token"] == phase["phase_token"]
                            and admission["access_sha256"] == catalog.digest(config["converter_lock_access"])
                            and observed["state_inode"] == admission["state_inode"], "original converter state directory changed")
            if observed["absent"]:return
            proof = json.loads(private(config["converter_lock_custody_output"]))
            operation = json.loads(private(config["bound_operation_output"]))
            lease = json.loads(private(config["pg_owner"]))
            row = writer_row(phase, config)
            catalog.require(proof["phase_token"] == phase["phase_token"] and proof["operation_sha256"] == catalog.digest(operation)
                            and proof["backend_pid"] == lease["backend_pid"] and proof["job_uid"] == row["uid"]
                            and proof["pod_uid"] == lease["pod_uid"] and proof["created_after_absent"] is True
                            and proof["nas"] == BOOK_NFS and proof["logical_path"] == BOOK_NFS["path"] + "/.epub-convert/lock",
                            "converter lock has no exact original owner custody")
            expected = {key: proof[key] for key in ("state_inode", "lock_inode", "lock_uid", "lock_mode")}
            catalog.require(observed == dict(expected, absent=False, empty=True), "converter lock custody or emptiness differs")
            observer = HostAdmission.__new__(HostAdmission)
            observer.c, observer.watch, observer.publishers, observer.supervisor = config, self, publishers, supervisor
            observer.operation = {"original_abort_epoch": supervisor.epoch(self.state["actuation_budget_started_at"]) + 300}
            ref = current_publisher_ref(config)
            observer.status = {"writer_may_mutate": True, "publisher_scope_proof": ref["path"]}
            observer.lock_ready, observer.jobs = True, phase["owned_jobs"]
            def guard(_=True):
                observer.remaining()
                base.Watchdog.verify_phase_absent(self, phase)
                self.verify_cached_stop_holds(self.state["cached_stop_actuation_binding"])
                supervisor.cache.check_live(accepted_watcher_cache(config, self, phase),
                    observer.get, json.loads(private(config["manifest_contract"]["path"], config["manifest_contract"]["sha256"])),
                    phase["phase_token"], deadline=observer.operation["original_abort_epoch"])
            observer.guard_lease = guard
            guard()
            private(config["publisher_scope_hook"]["script"], config["publisher_scope_hook"]["sha256"])
            supervisor.Supervisor.service_fence(observer)
            guard()  # Immediately before the only exact empty-lock release.
            released, absent = route(expected, True), route()
            catalog.require(released["absent"] is True and absent["absent"] is True
                            and released["state_inode"] == absent["state_inode"] == admission["state_inode"],
                            "owned converter lock absence unproved before Normal release")
    return MaintenanceWatchdog


def job_manifest(phase, name, node, claim, nfs):
    """Root must bind fresh actual claim/PV/node/export identities before CREATE."""
    catalog.require(re.fullmatch(r"[0-9a-f]{32}", phase) and re.fullmatch(r"ransom-maintenance-[a-z0-9-]+", name)
                    and isinstance(node, str) and node and isinstance(claim, str) and claim
                    and set(nfs) == {"server", "path"} and all(isinstance(v, str) and v for v in nfs.values()),
                    "maintenance manifest bindings missing")
    catalog.require(nfs == BOOK_NFS, "exact original books NAS export required")
    labels = {LABEL: phase}
    return {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "namespace": "media", "labels": labels},
            "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 250, "template": {
                "metadata": {"labels": labels, "annotations": {"k8tz.io/inject": "false"}},
                "spec": {"nodeSelector": {"kubernetes.io/hostname": node}, "restartPolicy": "Never",
                         "automountServiceAccountToken": False,
                         "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "runAsNonRoot": True},
                         "containers": [{"name": "maintenance", "image": IMAGE,
                                         "command": ["python", "-I", "-c", "import time; time.sleep(250)"],
                                         "resources": {"requests": {"cpu": "25m", "memory": "128Mi"},
                                                       "limits": {"cpu": "500m", "memory": "512Mi"}},
                                         "volumeMounts": [{"name": "config", "mountPath": "/kavita/config"},
                                                          {"name": "books", "mountPath": "/data/cephfs-hdd/data/media/books"},
                                                          {"name": "private", "mountPath": "/maintenance"}]}],
                         "volumes": [{"name": "config", "persistentVolumeClaim": {"claimName": claim}},
                                     {"name": "books", "nfs": nfs},
                                     {"name": "private", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}}]}}}}


@contextlib.contextmanager
def publication_guard(guard, retention, source_hash, candidate_hash):
    """Process-only exact file publication hook, using unchanged metadata CAS."""
    metadata = catalog.metadata
    write, replace, existing_replace = metadata._write_file, os.replace, metadata._replace
    replacement = None
    relative = os.path.relpath(catalog.FILE, LIBRARY)
    stem = metadata.sha256(relative.encode()) + "-" + source_hash
    folder, basename = os.path.split(catalog.FILE)
    allowed = {str(Path(retention)): {"catalog-before.json"},
               STATE + "/backup": {stem + ".epub", stem + ".json"}}
    def checked_write(fd, name, raw, mode=0o600):
        actual = os.readlink("/proc/self/fd/" + str(fd))
        partial = actual == folder and re.fullmatch(r"\." + re.escape(basename) + r"\.strip-[0-9a-f]{16}\.partial", name)
        catalog.require(mode == 0o600 and (name in allowed.get(actual, set()) or partial), "publication escaped exact maintenance paths/mode")
        if partial:
            catalog.require(metadata.sha256(raw) in (source_hash, candidate_hash), "publication bytes differ")
        guard()
        return write(fd, name, raw, mode)
    def checked_replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        catalog.require(src_dir_fd is not None and src_dir_fd == dst_dir_fd
                        and os.readlink("/proc/self/fd/" + str(dst_dir_fd)) == folder and dst == basename
                        and re.fullmatch(r"\." + re.escape(basename) + r"\.strip-[0-9a-f]{16}\.partial", src),
                        "replacement escaped exact Ransom EPUB")
        guard()  # Immediately before the existing atomic publication.
        current, info = metadata.read_regular(dst_dir_fd, dst)
        catalog.require(replacement is not None and current == replacement[0]
                        and metadata._identity(info) == replacement[1], "source changed during host publication guard")
        return replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
    def bound_replace(fd, directory, name, original, info, candidate):
        nonlocal replacement
        catalog.require(directory == folder and name == basename
                        and {metadata.sha256(original), metadata.sha256(candidate)} == {source_hash, candidate_hash},
                        "exact EPUB replacement byte pair differs")
        replacement = (original, metadata._identity(info))
        try:return existing_replace(fd, directory, name, original, info, candidate)
        finally:replacement = None
    metadata._write_file, os.replace = checked_write, checked_replace
    metadata._replace = bound_replace
    try:
        yield
    finally:
        metadata._write_file, os.replace = write, replace
        metadata._replace = existing_replace


def preflight_epub(operation, guard):
    """Refuse already-detectable file/backup mismatches before SQLite mutation."""
    guard()
    with catalog.metadata.safe_directory(str(Path(catalog.FILE).parent)) as fd:
        raw, info = catalog.metadata.read_regular(fd, Path(catalog.FILE).name)
    forward = operation["action"] == "forward"
    expected = operation["original_epub_sha256"] if forward else operation["candidate_epub_sha256"]
    catalog.require(catalog.metadata.sha256(raw) == expected, "EPUB admission source differs")
    if forward:
        checked = catalog.metadata.strip_existing(catalog.FILE, LIBRARY, STATE, 900, dry_run=True,
            expected_sha256=expected, expected_source_identity=catalog.metadata._identity(info), grouping=None)
        catalog.require(checked["result"] == "would_strip" and checked["original_sha256"] == expected
                        and checked["sanitized_sha256"] == operation["candidate_epub_sha256"],
                        "EPUB not settled or admitted strip candidate differs")
    else:
        relative = os.path.relpath(catalog.FILE, LIBRARY)
        manifest = STATE + "/backup/" + catalog.metadata.sha256(relative.encode()) + "-" + operation["original_epub_sha256"] + ".json"
        catalog.require(operation["epub_backup_manifest"] == manifest, "exact Ransom backup manifest differs")
        checked = catalog.metadata.restore_backup(manifest, LIBRARY, STATE, dry_run=True)
        catalog.require(checked["path"] == relative and checked["original_sha256"] == operation["original_epub_sha256"], "Ransom inverse backup differs")
    guard()
    return info


def worker(operation, dsn, ask_host, writer):
    """One original process/connection; no reconnect, cached guard or COPY manifest."""
    catalog.require(operation["library_root"] == LIBRARY and operation["database_path"] == DB
                    and operation["state_directory"] == STATE and operation["owner_approved"] is True
                    and operation["root_runtime_go"] is True and operation["prepared_only"] is False,
                    "maintenance worker authority/scope differs")
    catalog.require(operation["retention_directory"] == STATE + "/ransom-maintenance/" + operation["phase_token"],
                    "exact outside-library retention path differs")
    before = json.loads(private(operation["before"]["path"], operation["before"]["sha256"]))
    os.environ["LIBRARY_HOLD_FOLDERS_JSON"] = json.dumps(operation["process_holds"])
    catalog.require("Daniel Silva/Ransom" in operation["original_holds"]
                    and operation["process_holds"] == [h for h in operation["original_holds"] if h != "Daniel Silva/Ransom"],
                    "one-process hold exclusion differs")
    with writer.PrimaryShareFence(dsn, operation["original_abort_epoch"], phase_token=operation["phase_token"]) as fence:
        lock_custody = None
        def guard():
            fence.health()
            catalog.require(time.time() < operation["original_abort_epoch"], "original worker clock expired")
            ask_host(fence.pid, lock_custody)
            fence.health()
        guard()
        with catalog.metadata.safe_directory(operation["retention_directory"], create=True):pass
        catalog.require(lock_io(STATE)["absent"] is True, "existing converter lock refuses maintenance")
        with writer.converter_lock(STATE), contextlib.closing(sqlite3.connect(DB, isolation_level=None)) as db:
            observed = lock_io(STATE)
            catalog.require(observed["absent"] is False and observed["empty"] is True and observed["lock_uid"] == os.getuid(),
                            "new converter lock identity differs")
            lock_custody = {key: observed[key] for key in ("state_inode", "lock_inode", "lock_uid", "lock_mode")}
            lock_custody.update(created_after_absent=True, nas=BOOK_NFS,
                                logical_path=BOOK_NFS["path"] + "/.epub-convert/lock")
            guard()  # Host retains exact custody before any catalog/EPUB publication.
            db.execute("PRAGMA foreign_keys=ON")
            with publication_guard(guard, operation["retention_directory"], operation["original_epub_sha256"], operation["candidate_epub_sha256"]):
                epub_info = preflight_epub(operation, guard)
                if operation["action"] == "inverse-after-scan":
                    after = json.loads(private(operation["post_scan"]["path"], operation["post_scan"]["sha256"]))
                    result = catalog.invert_after_scan(db, before, after, operation, guard, operation["retention_directory"])
                    guard()
                    result["epub"] = catalog.metadata.restore_backup(operation["epub_backup_manifest"], LIBRARY, STATE)
                elif operation["action"] == "inverse-before-scan":
                    result = catalog.apply(db, before, operation, guard, operation["retention_directory"], inverse=True)
                    guard()
                    result["epub"] = catalog.metadata.restore_backup(operation["epub_backup_manifest"], LIBRARY, STATE)
                else:
                    catalog.require(operation["action"] == "forward", "unknown maintenance action")
                    result = catalog.apply(db, before, operation, guard, operation["retention_directory"])
                    guard()
                    result["epub"] = catalog.metadata.strip_existing(catalog.FILE, LIBRARY, STATE, 900,
                        expected_sha256=operation["original_epub_sha256"], expected_source_identity=catalog.metadata._identity(epub_info), grouping=None)
                    catalog.require(result["epub"]["result"] == "stripped", "EPUB did not publish exact strip")
                guard()
                expected = before if operation["action"].startswith("inverse-") else catalog.expected_after(before)
                catalog.require(catalog.snapshot(db) == expected, "complete protected state changed before worker exit")
                return result


def worker_main():
    sys.path.insert(0, "/copy-writer")
    import book_copy_writer as writer
    catalog.require(len(sys.argv) == 2 and re.fullmatch(r"[0-9a-f]{64}", sys.argv[1]), "admitted worker operation SHA missing")
    operation_sha = sys.argv[1]
    operation = json.loads(private("/maintenance/operation.json", operation_sha))
    # Credential crosses only the existing private stdin stream, never argv/log/files.
    secret = json.loads(sys.stdin.readline(65536))
    catalog.require(set(secret) == {"dsn"} and isinstance(secret["dsn"], str), "private PG input differs")
    count = 0
    def ask_host(pid, lock_custody=None):
        nonlocal count
        count += 1
        catalog.require(count <= 32, "finite maintenance guard count exceeded")
        request = {"event": "guard", "phase_token": operation["phase_token"], "operation_sha256": operation_sha,
                   "sequence": count, "backend_pid": pid, "lock_custody": lock_custody}
        print(json.dumps(request), flush=True)
        # Host process has original timeout; independent Job cleanup revokes on its death.
        ack = json.loads(sys.stdin.readline(4096))
        catalog.require(ack == {"event": "guard-ok", "phase_token": request["phase_token"], "operation_sha256": operation_sha, "sequence": count},
                        "fresh host service acknowledgement differs")
    def expired(*_):
        raise writer.DeadlineExpired("original maintenance abort clock")
    signal.signal(signal.SIGALRM, expired)
    signal.signal(signal.SIGTERM, expired)
    remaining = operation["original_abort_epoch"] - time.time()
    catalog.require(0 < remaining <= 170, "worker original admission clock differs")
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        result = worker(operation, secret["dsn"], ask_host, writer)
        print(json.dumps({"event": "complete", "phase_token": operation["phase_token"], "operation_sha256": operation_sha, "result": result}), flush=True)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


class HostAdmission:
    """Exact one-Job bridge to existing service, publisher and recovery guards."""
    def __init__(self, config, watch, core, pod_guard, supervisor, publisher_guard, *, publisher_pending=False):
        validate_bootstrap(config)
        self.c, self.watch, self.core, self.pod_guard = config, watch, core, pod_guard
        self.publishers, self.supervisor = publisher_guard, supervisor
        self.publisher_pending = publisher_pending and config["publisher_proof"] is None
        catalog.require(self.publisher_pending or config["publisher_proof"] is not None, "full publisher proof required")
        self.status = {"phase_token": config["phase_token"], "writer_may_mutate": not self.publisher_pending}
        if not self.publisher_pending:
            self.status["publisher_scope_proof"] = current_publisher_ref(config)["path"]
        self.lock_ready = True
        self.execution = None
        self.deadline = None
        self.jobs = []
        self.stop = False
        self.operation = json.loads(private(config["operation"]["path"], config["operation"]["sha256"]))
        admission = json.loads(private(config["converter_lock_admission"]["path"], config["converter_lock_admission"]["sha256"]))
        catalog.require(admission["phase_token"] == config["phase_token"] and admission["lock_absent"] is True
                        and admission["access_sha256"] == catalog.digest(config["converter_lock_access"]),
                        "pre-Stop converter access admission differs")
        catalog.require(self.operation["phase_token"] == config["phase_token"]
                        and self.operation["root_runtime_go"] is True and self.operation["owner_approved"] is True
                        and self.operation["prepared_only"] is False
                        and self.operation["action"] in {"forward", "inverse-before-scan", "inverse-after-scan"},
                        "Root/owner runtime binding differs")

    def run(self, argv, timeout=10):
        return self.watch.run(argv, timeout=min(timeout, max(.001, self.remaining())))

    def get(self, kind, name, namespace):
        if not namespace:
            return json.loads(self.run(["kubectl", "get", kind, name, "-o", "json"]))
        return self.watch.kube(kind, name, namespace)

    def list(self, kind, namespace=None):
        kinds = {"pods": "Pod", "pvc": "PersistentVolumeClaim", "pv": "PersistentVolume"}
        argv = ["kubectl", "get", kind] + (["-n", namespace] if namespace else ["-A"] if kind != "pv" else []) + ["-o", "json"]
        return self.supervisor.window.typed_inventory(json.loads(self.run(argv)), kinds[kind], namespace)

    def save(self):
        pass  # Existing service predicate's timestamp is diagnostic, not authority.

    def remaining(self):
        until = self.operation["original_abort_epoch"]
        if until is None:
            state = json.loads(private(self.c["watcher_state"]))
            until = self.supervisor.epoch(state["armed_at"]) + self.watch.args.arm_deadline
        left = until - time.time()
        catalog.require(left > 0, "original maintenance abort reached")
        return left

    def cached_guard(self, holds):
        return self.supervisor.Supervisor.cached_guard(self, holds)

    def review_gate(self, pr):
        return self.supervisor.Supervisor.review_gate(self, pr)

    def activate_and_wait(self):
        """Reuse the frozen activation/review/Stop gates in this finite run."""
        self.guard_lease()
        activation = self.c["watcher_arguments"]["cached_source_activation"]
        catalog.require(self.c["cached_source_activation"] == activation
                        and not Path(activation).exists() and not Path(activation).is_symlink()
                        and self.operation["original_abort_epoch"] is None
                        and self.c["restore_reserve_seconds"] == 130,
                        "maintenance activation already consumed or budget differs")
        state = json.loads(private(self.c["watcher_state"]))
        catalog.require(state.get("armed_ready") is True and state.get("recover_ks") is True
                        and not state.get("complete") and not state.get("copy_authority_revoked_at")
                        and not state.get("actuation_budget_started_at") and not state.get("cached_stop_actuation_started_at")
                        and str(state.get("restore_pr")) == self.c["restore_pr"]
                        and state.get("normal_inverse_merge_sha") == self.c["normal_merge_sha"]
                        and state.get("cached_source_ready_at")
                        and state.get("cached_source_receipt_sha256") == self.c["cached_source_receipt"]["sha256"],
                        "pre-Stop original watcher/cache/Normal custody differs")
        def original_kernel():
            kernel = Path("/proc/" + str(self.c["watcher_pid"]) + "/stat").read_text().rsplit(")", 1)[1].split()
            catalog.require(kernel[0] not in ("Z", "X") and int(kernel[2]) == self.c["watcher_pgid"]
                            and int(kernel[19]) == self.c["watcher_birth"], "pre-Stop original watcher group changed")
        original_kernel()
        phase = one_job_phase(self.c, self.core)
        catalog.require(all(row["uid"] is None for row in phase["owned_jobs"])
                        and all(row["backend_pid"] is None and row["job_uid"] is None and row["pod_uid"] is None
                                for row in phase["pg_leases"]), "activation must precede every helper/writer")
        self.watch.verify_phase_absent(phase)
        self.supervisor.Supervisor.merged_inverse_ready(self)  # Existing exact current review/Git/Normal contract.
        catalog.require(self.status["normal_inverse_merge_sha"] == self.c["normal_merge_sha"], "approved Normal changed")
        receipt, _ = self.cached_guard(True)
        for ns, name in list(self.supervisor.window.SCOPES) + [("flux-system", name) for name in self.supervisor.cache.PARENTS]:
            proof = receipt["parents"][name] if ns == "flux-system" else receipt["holds"][ns + "/" + name]
            catalog.require(state["cached_ks_owners"].get(ns + "/" + name) == {
                "uid": proof["before"]["metadata"]["uid"], "spec": proof["before"]["spec"], "phase_token": self.c["phase_token"]},
                "pre-Stop original hold owner differs")
        for ns, name in (("downloads", "lazylibrarian"), ("media", "kavita")):
            deployment = self.get("deployment", name, ns)
            pods = [p for p in self.list("pods", ns) if all(p["metadata"].get("labels", {}).get(k) == v
                    for k, v in deployment["spec"]["selector"]["matchLabels"].items())]
            catalog.require(deployment["spec"].get("replicas", 1) == 1 and deployment.get("status", {}).get("readyReplicas", 0) == 1
                            and len(pods) == 1 and not pods[0]["metadata"].get("deletionTimestamp"), "services not Normal before activation")
        admission = json.loads(private(self.c["converter_lock_admission"]["path"], self.c["converter_lock_admission"]["sha256"]))
        lock = sonarr_lock(self.c, self.get, self.run)
        catalog.require(0 <= time.time() - admission["admitted_epoch"] <= 300 and lock["absent"] is True
                        and lock["state_inode"] == admission["state_inode"], "pre-Stop converter admission stale or changed")
        self.guard_lease()
        original_kernel()  # Fresh immediately before the one activation publication.
        origin = dt.datetime.now(dt.timezone.utc).isoformat()
        self.operation["original_abort_epoch"] = self.supervisor.epoch(origin) + 170
        self.deadline = self.supervisor.epoch(origin) + 300
        self.status.update(watchdog_armed_at=state["armed_at"], actuation_budget_started_at=origin)
        value = {"schema": 1, "phase_token": self.c["phase_token"],
                 "cached_source_receipt_sha256": self.c["cached_source_receipt"]["sha256"],
                 "normal_inverse_merge_sha": self.c["normal_merge_sha"], "armed_ready": True,
                 "actuation_budget_started_at": origin}
        self.supervisor.cache.activation(value, self.c["phase_token"], self.c["cached_source_receipt"]["sha256"], self.c["normal_merge_sha"])
        self.supervisor.cache.write_private(activation, catalog.canonical(value))
        while True:
            self.guard_lease()
            state = json.loads(private(self.c["watcher_state"]))
            if self.supervisor.Supervisor.cached_stop_gate(self, state):
                self.guard(require_pod=False, read_only_prelude=True)
                return
            time.sleep(.1)

    def guard_lease(self, require_lock=True):
        self.remaining()
        catalog.require(not Path(self.c["watcher_stop"]).exists() and not self.stop
                        and (self.execution is None or self.execution.poll() is None), "maintenance owner revoked")

    def guard(self, require_pod=True, *, read_only_prelude=False):
        catalog.require(not read_only_prelude or self.publisher_pending and not require_pod,
                        "read-only prelude cannot admit a writer")
        catalog.require(read_only_prelude or not self.publisher_pending, "publisher proof missing before writer admission")
        self.guard_lease()
        state = json.loads(private(self.c["watcher_state"]))
        catalog.require(state.get("cached_stop_actuation_complete_at") and not state.get("complete")
                        and not state.get("copy_authority_revoked_at") and state.get("armed_ready") is True,
                        "independent maintenance watcher not active after complete Stop")
        origin = self.supervisor.epoch(state["actuation_budget_started_at"])
        admission = json.loads(private(self.c["converter_lock_admission"]["path"], self.c["converter_lock_admission"]["sha256"]))
        catalog.require(admission["admitted_epoch"] <= origin and origin - admission["admitted_epoch"] <= 300
                        and sonarr_lock(self.c, self.get, self.run)["state_inode"] == admission["state_inode"],
                        "pre-Stop converter route freshness or state identity differs")
        catalog.require(self.operation["original_abort_epoch"] == origin + 170
                        and self.supervisor.epoch(state["armed_at"]) + self.watch.args.arm_deadline > time.time(),
                        "original maintenance clock binding differs")
        kernel = Path("/proc/" + str(self.c["watcher_pid"]) + "/stat").read_text().rsplit(")", 1)[1].split()
        catalog.require(kernel[0] not in ("Z", "X") and int(kernel[2]) == self.c["watcher_pgid"]
                        and int(kernel[19]) == self.c["watcher_birth"], "independent watcher original group changed")
        phase = one_job_phase(self.c, self.core)
        self.watch.state = state  # Read-only context, never a second watchdog owner.
        self.watch.verify_cached_stop_holds(state["cached_stop_actuation_binding"])
        receipt = json.loads(private(self.c["cached_source_receipt"]["path"], self.c["cached_source_receipt"]["sha256"]))
        contract = json.loads(private(self.c["manifest_contract"]["path"], self.c["manifest_contract"]["sha256"]))
        self.supervisor.cache.check_live(receipt, self.get, contract, phase["phase_token"], deadline=self.operation["original_abort_epoch"])
        row = writer_row(phase, self.c)
        if require_pod:
            actual = self.get("job", row["name"], row["namespace"])
            pods = self.watch.owned_job_pods(self.list("pods", "media"), row, row["uid"])
            catalog.require(len(pods) == 1 and pods[0]["metadata"]["uid"] == self.c["pod_uid"], "maintenance Pod custody differs")
            self.pod_guard.verify_owned_pod(row, actual, pods[0], self.c["pod_uid"])
        else:
            catalog.require(row["uid"] is None, "maintenance Job already created; never retry CREATE")
            helper = helper_row(phase, self.c)
            if read_only_prelude and helper["uid"] is not None:
                actual = self.get("job", helper["name"], "media")
                pods = self.watch.owned_job_pods(self.list("pods", "media"), helper, helper["uid"])
                catalog.require(len(pods) == 1, "read-only helper Pod custody differs")
                self.pod_guard.verify_owned_pod(helper, actual, pods[0], pods[0]["metadata"]["uid"])
                self.watch.verify_only_helper(phase, pods[0]["metadata"]["uid"])
            else:self.watch.verify_phase_absent(phase)
        claim = self.get("pvc", self.c["claim_name"], "media")
        pv = self.get("pv", self.c["pv_name"], "")
        catalog.require(claim["metadata"]["uid"] == self.c["claim_uid"] and claim["spec"] == self.c["claim_spec"]
                        and claim.get("status", {}).get("phase") == "Bound" and pv["metadata"]["uid"] == self.c["pv_uid"]
                        and pv["spec"] == self.c["pv_spec"]
                        and claim["spec"].get("volumeName") == self.c["pv_name"]
                        and pv["spec"]["claimRef"]["uid"] == self.c["claim_uid"], "maintenance PVC/PV identity or placement changed")
        self.jobs = phase["owned_jobs"]
        hook = self.c["publisher_scope_hook"]
        private(hook["script"], hook["sha256"])
        self.supervisor.Supervisor.service_fence(self)  # Full existing stopped services + live publisher/storage predicate.
        self.guard_lease()

    def publisher_prelude(self):
        """Existing RO helper → complete capture → exact GC, before any writer."""
        catalog.require(self.publisher_pending, "publisher prelude cannot be replayed")
        self.guard(require_pod=False, read_only_prelude=True)
        phase = one_job_phase(self.c, self.core)
        row = helper_row(phase, self.c)
        catalog.require(row["uid"] is None and row["ready_manifest"] is None, "Lidarr helper already bound; no retry")
        helper = self.c["lidarr_helper"]
        deadline = min(self.operation["original_abort_epoch"], time.time() + 115)
        values = {"LIDARR_CAPTURE_PHASE_READY": "1", "LIDARR_CAPTURE_DEADLINE_EPOCH": f"{deadline:.6f}"}
        with catalog.metadata.safe_directory(str(Path(helper["env_output"]).parent)) as fd:
            catalog.metadata._write_file(fd, Path(helper["env_output"]).name, catalog.canonical(values))
        self.core.process(SimpleNamespace(command="bind", restore_pr=self.c["restore_pr"], namespace="media", name=row["name"],
            initial_manifest_sha256=row["initial_manifest_sha256"], env_file=helper["env_output"], output=helper["ready_output"]), Path(self.c["phase_state"]))
        self.guard(require_pod=False, read_only_prelude=True)
        row = helper_row(one_job_phase(self.c, self.core), self.c)
        result = subprocess.run(["kubectl", "create", "-f", "-", "-o", "json"], input=self.core.encoded(row["ready_manifest"]),
                                capture_output=True, timeout=min(10, self.remaining()))
        catalog.require(result.returncode == 0 and self.core.declared_matches(row["ready_manifest"], json.loads(result.stdout)),
                        "read-only helper CREATE failed; no retry")
        self.core.process(SimpleNamespace(command="observe", restore_pr=self.c["restore_pr"], namespace="media", name=row["name"]), Path(self.c["phase_state"]))
        until = time.monotonic() + min(15, self.remaining())
        while True:
            self.guard_lease()
            row = helper_row(one_job_phase(self.c, self.core), self.c)
            pods = self.watch.owned_job_pods(self.list("pods", "media"), row, row["uid"])
            catalog.require(len(pods) <= 1, "multiple read-only helper Pods")
            if pods and pods[0].get("status", {}).get("phase") == "Running":break
            catalog.require(time.monotonic() < until, "read-only helper original readiness expired")
            time.sleep(.2)
        self.guard(require_pod=False, read_only_prelude=True)
        hook = self.c["publisher_scope_hook"]
        ref = self.c["publisher_config"]
        configured = json.loads(private(ref["path"], ref["sha256"]))
        profile = configured["lidarr_native_helper"]
        catalog.require(profile["checkpoint_helper"] == self.c["sources"]["checkpoint"]["path"]
                        and profile["checkpoint_sha256"] == self.c["sources"]["checkpoint"]["sha256"]
                        and profile["phase_state"] == self.c["phase_state"] and profile["restore_pr"] == self.c["restore_pr"]
                        and profile["job_name"] == row["name"] and profile["namespace"] == "media"
                        and profile["source_template"] == helper["source_template"]["path"]
                        and profile["source_template_sha256"] == helper["source_template"]["sha256"]
                        and profile["capture_program"] == self.c["publisher_sources"]["lidarr-native-paths-capture.py"]["path"]
                        and profile["capture_sha256"] == self.c["publisher_sources"]["lidarr-native-paths-capture.py"]["sha256"],
                        "complete Lidarr collector route differs from exact helper")
        required = {"kapowarr-native-files-capture.py", "lidarr-native-paths-capture.py", "lidarr-native-source-bridge.py",
                    "publisher-config-capture.js", "publisher-local-backing.py", "publisher-path-capture.js", "publisher-path-capture.py",
                    "publisher-path-capture.sh", "publisher-scope-capture.py", "publisher-scope-guard.py", "sab-local-config-capture.py",
                    "approved-normal-write-profiles.json"}
        catalog.require(set(self.c["publisher_sources"]) == required, "complete private publisher closure required")
        for name, item in self.c["publisher_sources"].items():
            catalog.require(Path(item["path"]) == Path(hook["script"]).with_name(name), "publisher sibling path differs")
            private(item["path"], item["sha256"])
        catalog.require(self.c["publisher_sources"]["publisher-scope-guard.py"] == self.c["sources"]["publisher_guard"],
                        "host/collector publisher guards differ")
        collector = module("maintenance_publisher_collector", {"path": hook["script"], "sha256": hook["sha256"]})
        capture = collector.Capture(configured, self.c["publisher_proof_output"], 60)
        original_run = capture.run
        def checked_run(*args, **kwargs):
            self.guard_lease()
            current = json.loads(private(self.c["phase_state"]))
            self.core.validate_state(current, self.c["restore_pr"])
            current["heartbeat"] = self.core.stamp()
            self.core.save(Path(self.c["phase_state"]), current)
            return original_run(*args, **kwargs)
        capture.run = checked_run
        capture.execute()
        self.guard(require_pod=False, read_only_prelude=True)
        raw = private(self.c["publisher_proof_output"])
        ref = {"path": self.c["publisher_proof_output"], "sha256": hashlib.sha256(raw).hexdigest()}
        self.status.update(writer_may_mutate=True, publisher_scope_proof=self.c["publisher_proof_output"])
        self.supervisor.Supervisor.service_fence(self)  # Full proof gate before helper retirement and writer CREATE.
        binding = {"schema": 1, "phase_token": self.c["phase_token"], "scope_sha256": self.c["publisher_scope_sha256"], "proof": ref}
        target = Path(self.c["publisher_proof_binding_output"])
        with catalog.metadata.safe_directory(str(target.parent)) as fd:
            catalog.metadata._write_file(fd, target.name, catalog.canonical(binding))
        current_publisher_ref(self.c)  # Independent watcher consumes this same exclusive binding, never a model hop.
        self.guard_lease()
        self.watch.run(["kubectl", "delete", "--raw", f"/apis/batch/v1/namespaces/media/jobs/{row['name']}", "-f", "-"],
                       timeout=min(10, self.remaining()), input_text=json.dumps({"apiVersion": "v1", "kind": "DeleteOptions",
                       "propagationPolicy": "Foreground", "preconditions": {"uid": row["uid"]}}))
        until = time.monotonic() + min(15, self.remaining())
        while True:
            self.guard_lease()
            try:self.watch.verify_phase_absent(one_job_phase(self.c, self.core));break
            except self.watch.phase_resources_present:
                catalog.require(time.monotonic() < until, "read-only helper foreground GC incomplete")
                time.sleep(.2)
        self.publisher_pending = False
        self.guard(require_pod=False)

    def deliver(self, name, raw):
        """Exact private delivery after complete Job/Pod/custody admission."""
        catalog.require(name in {"ransom_catalog_maintenance.py", "ransom_maintenance_job.py", "before.json", "post-scan.json", "operation.json"}
                        and isinstance(raw, bytes) and 0 < len(raw) <= 32 * 1024 * 1024, "maintenance delivery scope/cap differs")
        if name == "operation.json":
            catalog.require(raw == catalog.canonical(self.operation), "delivered operation differs from exact admitted operation")
        self.guard()
        sha = hashlib.sha256(raw).hexdigest()
        receiver = "import os,sys,hashlib; p=sys.argv[1]; b=sys.stdin.buffer.read(33554433); assert len(b)<=33554432 and hashlib.sha256(b).hexdigest()==sys.argv[2]; f=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600); h=os.fdopen(f,'wb'); h.write(b); h.flush(); os.fsync(h.fileno()); h.close()"
        argv = ["kubectl", "exec", "-i", "-n", "media", self.c["pod_name"], "-c", "maintenance", "--", "python", "-I", "-c", receiver, "/maintenance/" + name, sha]
        result = subprocess.run(argv, input=raw, capture_output=True, timeout=min(10, self.remaining()))
        catalog.require(result.returncode == 0, "owned private delivery failed")
        self.guard()

    def create(self):
        self.guard(require_pod=False)
        ready = self.core.encoded(self.c["job_manifest"])
        result = subprocess.run(["kubectl", "create", "-f", "-", "-o", "json"], input=ready,
                                capture_output=True, timeout=min(10, self.remaining()))
        catalog.require(result.returncode == 0, "maintenance CREATE failed; no retry/adoption")
        actual = json.loads(result.stdout)
        catalog.require(self.core.declared_matches(self.c["job_manifest"], actual), "created maintenance manifest differs")
        arguments = SimpleNamespace(command="observe", namespace="media", name=self.c["job_name"], restore_pr=self.c["restore_pr"])
        self.core.process(arguments, Path(self.c["phase_state"]))
        until = min(time.monotonic() + 15, time.monotonic() + self.remaining())
        while time.monotonic() < until:
            phase = one_job_phase(self.c, self.core)
            row = writer_row(phase, self.c)
            pods = self.watch.owned_job_pods(self.list("pods", "media"), row, row["uid"])
            catalog.require(len(pods) <= 1, "multiple maintenance Pods")
            if pods and pods[0].get("status", {}).get("phase") == "Running":
                self.c["pod_name"], self.c["pod_uid"] = pods[0]["metadata"]["name"], pods[0]["metadata"]["uid"]
                self.guard()
                for ref in self.c["bootstrap_sources"]:
                    catalog.require(ref["name"] in {"ransom_catalog_maintenance.py", "ransom_maintenance_job.py"}, "bootstrap source scope differs")
                    self.deliver(ref["name"], private(ref["path"], ref["sha256"]))
                return {"job_uid": row["uid"], "pod_name": self.c["pod_name"], "pod_uid": self.c["pod_uid"]}
            self.guard_lease();time.sleep(.2)
        raise catalog.Refused("maintenance original Pod readiness expired")

    def inspect(self):
        self.guard()
        # Same pinned component reads a fresh full typed snapshot at stopped-service admission.
        code = "import sys;sys.path[:0]=['/maintenance','/copy-writer'];import ransom_maintenance_job as m;m.inspection_main(" + repr(self.operation["action"]) + ")"
        result = subprocess.run(["kubectl", "exec", "-n", "media", self.c["pod_name"], "-c", "maintenance", "--", "python", "-I", "-B", "-c", code],
                                capture_output=True, timeout=min(60, self.remaining()))
        catalog.require(result.returncode == 0 and len(result.stdout) <= 65536, "stopped current inspection failed")
        self.guard()
        proof = json.loads(result.stdout)
        catalog.require(proof["before_path"] == "/maintenance/current.json", "inspection private path differs")
        catalog.require(proof["schema_sha256"] == self.c["approved_schema_sha256"], "current Native schema differs from reviewed application")
        raw = subprocess.run(["kubectl", "exec", "-n", "media", self.c["pod_name"], "-c", "maintenance", "--", "cat", proof["before_path"]],
                             capture_output=True, timeout=min(60, self.remaining()))
        catalog.require(raw.returncode == 0 and len(raw.stdout) <= 32 * 1024 * 1024
                        and hashlib.sha256(raw.stdout).hexdigest() == proof["before_sha256"], "fresh stopped Native transfer differs")
        self.guard()
        target = Path(self.c["inspection_output"])
        with catalog.metadata.safe_directory(str(target.parent)) as fd:
            catalog.metadata._write_file(fd, target.name, raw.stdout)
        return dict(proof, retained_before_path=str(target))

    def run_automatic(self, dsn):
        """One conditional GO: existing CREATE→inspect→four allowed binds→execute."""
        template = copy.deepcopy(self.operation)
        catalog.require(template["original_abort_epoch"] is None and template["database_device_inode"] is None
                        and template["schema_sha256"] == self.c["approved_schema_sha256"],
                        "automatic operation template/clock/schema differs")
        if template["action"] == "forward":
            catalog.require(template["before"] is None and template["before_sha256"] is None,
                            "forward template already bound; never reuse")
        self.activate_and_wait()  # Original origin is frozen before the first Stop release.
        self.publisher_prelude()
        self.create()
        proof = self.inspect()
        current_raw = private(proof["retained_before_path"], proof["before_sha256"])
        current = json.loads(current_raw)
        if template["action"] == "forward":
            before_raw = current_raw
            self.operation.update(before_sha256=catalog.digest(current),
                                  before={"path": "/maintenance/before.json", "sha256": hashlib.sha256(current_raw).hexdigest()})
        else:
            original_ref = self.c["original_rows"]
            before_raw = private(original_ref["path"], original_ref["sha256"])
            original = json.loads(before_raw)
            catalog.require(template["before"] == {"path": "/maintenance/before.json", "sha256": hashlib.sha256(before_raw).hexdigest()}
                            and template["before_sha256"] == catalog.digest(original), "inverse original baseline binding differs")
            if template["action"] == "inverse-before-scan":
                catalog.require(current == catalog.expected_after(original), "fresh pre-scan after-state drifted")
            else:
                ref = self.c["post_scan_rows"]
                raw = private(ref["path"], ref["sha256"])
                after = json.loads(raw)
                catalog.require(template["post_scan"] == {"path": "/maintenance/post-scan.json", "sha256": hashlib.sha256(raw).hexdigest()}
                                and template["post_scan_after_sha256"] == catalog.digest(after) and current == after,
                                "fresh post-scan after-state drifted")
                catalog.require_scan_delta(catalog.expected_after(original), after, template["native_explicit_values"],
                                           template["scan_started_epoch"], template["scan_finished_epoch"])
                self.deliver("post-scan.json", raw)
        self.operation["database_device_inode"] = proof["database_device_inode"]
        # Exact declared dynamic leaves only; every authority/path/EPUB cell stays pinned.
        neutral = copy.deepcopy(self.operation)
        for key in ("original_abort_epoch", "database_device_inode", "before", "before_sha256"):
            neutral[key] = template[key]
        catalog.require(neutral == template, "automatic bind exceeded declared leaves")
        self.deliver("before.json", before_raw)
        raw = catalog.canonical(self.operation)
        output = Path(self.c["bound_operation_output"])
        with catalog.metadata.safe_directory(str(output.parent)) as fd:
            catalog.metadata._write_file(fd, output.name, raw)
        self.deliver("operation.json", raw)
        return self.execute(dsn)

    def execute(self, dsn):
        """Finite synchronous guard channel; existing watcher owns independent cleanup."""
        catalog.require(self.operation["schema_sha256"] == self.c["approved_schema_sha256"], "worker Native schema admission differs")
        self.guard()
        operation_sha = catalog.digest(self.operation)
        argv = ["kubectl", "exec", "-i", "-n", "media", self.c["pod_name"], "-c", "maintenance", "--", "python", "-I", "-B", "-c",
                "import sys;sys.path[:0]=['/maintenance','/copy-writer'];import ransom_maintenance_job as m;m.worker_main()", operation_sha]
        diagnostic = Path(self.c["execution_stderr"])
        fd = os.open(diagnostic, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        complete, sequence = None, 0
        with os.fdopen(fd, "wb") as stderr:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, start_new_session=True)
            self.execution = process
            process.stdin.write((json.dumps({"dsn": dsn}) + "\n").encode()); process.stdin.flush()
            selector, pending = selectors.DefaultSelector(), bytearray()
            selector.register(process.stdout, selectors.EVENT_READ)
            try:
                while complete is None:
                    self.guard_lease()
                    catalog.require(selector.select(timeout=min(3, self.remaining())), "maintenance guard channel died or timed out")
                    chunk = os.read(process.stdout.fileno(), 4096)
                    catalog.require(chunk, "maintenance worker exited before complete")
                    pending.extend(chunk)
                    catalog.require(len(pending) <= 65536, "maintenance protocol output cap exceeded")
                    while b"\n" in pending:
                        line, _, pending = pending.partition(b"\n")
                        event = json.loads(line)
                        catalog.require(event.get("phase_token") == self.c["phase_token"]
                                        and event.get("operation_sha256") == operation_sha, "worker event phase/operation differs")
                        if event.get("event") == "guard":
                            sequence += 1
                            catalog.require(set(event) == {"event", "phase_token", "operation_sha256", "sequence", "backend_pid", "lock_custody"}
                                            and event["sequence"] == sequence and sequence <= 32
                                            and type(event["backend_pid"]) is int and event["backend_pid"] > 0,
                                            "maintenance guard event differs")
                            lease = {"phase_token": event["phase_token"], "application_name": PG_NAME,
                                     "backend_pid": event["backend_pid"], "job_uid": writer_row(one_job_phase(self.c, self.core), self.c)["uid"], "pod_uid": self.c["pod_uid"]}
                            current = json.loads(private(self.c["pg_owner"]))
                            catalog.require(current["backend_pid"] is None or current == lease, "PG backend changed or reconnected")
                            self.core.save(Path(self.c["pg_owner"]), lease)
                            if event["lock_custody"] is not None:
                                record_lock_custody(self.c, event, lease)
                            self.guard()
                            process.stdin.write((json.dumps({"event": "guard-ok", "phase_token": event["phase_token"], "operation_sha256": operation_sha, "sequence": sequence}) + "\n").encode());process.stdin.flush()
                        else:
                            catalog.require(set(event) == {"event", "phase_token", "operation_sha256", "result"} and event["event"] == "complete"
                                            and complete is None, "unknown or repeated maintenance outcome")
                            complete = event["result"]
                process.stdin.close()
                catalog.require(process.wait(timeout=min(3, self.remaining())) == 0 and not pending,
                                "maintenance process did not close cleanly")
                return complete
            finally:
                selector.close()
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:process.wait(timeout=2)
                    except subprocess.TimeoutExpired:os.killpg(process.pid, signal.SIGKILL);process.wait(timeout=2)
                # Killing kubectl is insufficient: existing watcher deletes exact writer FIRST,
                # proves full Job/Pod union and primary backend absence, then restores Normal.
                Path(self.c["watcher_stop"]).touch(mode=0o600, exist_ok=True)
                self.execution = None


def inspection_main(action="forward"):
    """Read-only production DB handle; no catalog/EPUB operation or PG credential."""
    with contextlib.closing(sqlite3.connect("file:" + DB + "?mode=ro", uri=True, isolation_level=None)) as db:
        db.execute("PRAGMA query_only=ON");db.execute("BEGIN")
        before = catalog.snapshot(db)
        catalog.preconditions(before, time.time())
        catalog.require(action in {"forward", "inverse-before-scan", "inverse-after-scan"}, "inspection action differs")
        if action == "forward":catalog.expected_after(before)
        db.rollback()
    raw = catalog.canonical(before)
    catalog.require(len(raw) <= 32 * 1024 * 1024, "full stopped Native snapshot exceeds private cap")
    path = "/maintenance/current.json"
    with catalog.metadata.safe_directory("/maintenance") as fd:
        catalog.metadata._write_file(fd, "current.json", raw)
    info = os.stat(DB, follow_symlinks=False)
    return print(json.dumps({"schema": 1, "before_path": path, "before_sha256": hashlib.sha256(raw).hexdigest(),
                            "logical_before_sha256": catalog.digest(before), "schema_sha256": catalog.digest(before["schema"]),
                            "database_device_inode": [info.st_dev, info.st_ino]}))


def load_sources(config):
    validate_bootstrap(config)
    refs = config["sources"]
    needed = {"window_contract", "cached_source", "watcher", "checkpoint", "pod_guard", "supervisor", "publisher_guard"}
    catalog.require(set(refs) == needed, "maintenance dependency closure differs")
    for ref in refs.values():private(ref["path"], ref["sha256"])
    original = config["checkpoint_core"]
    catalog.require(original["path"] == str(Path(refs["checkpoint"]["path"]).with_name("checkpoint-owned-job.core.py"))
                    and original["sha256"] == "1ceacf5d487c32b3107ac7db6c166e2384739474505d68640ea7b4921402b622",
                    "explicit original checkpoint dependency differs")
    private(original["path"], original["sha256"])
    sys.path.insert(0, str(Path(refs["watcher"]["path"]).parent))
    module("window_contract", refs["window_contract"])
    module("cached_source", refs["cached_source"])
    return {name: module("maintenance_" + name, refs[name]) for name in ("watcher", "checkpoint", "pod_guard", "supervisor", "publisher_guard")}


def make_watch(config, sources, *, arm=False):
    args = SimpleNamespace(**config["watcher_arguments"])
    catalog.require(args.phase_state == config["phase_state"] and str(args.restore) == config["restore_pr"]
                    and args.state == config["watcher_state"] and args.stop == config["watcher_stop"]
                    and args.include_kavita is True and args.deadline == 170 and 0 < args.arm_deadline <= 1800
                    and args.cached_source_receipt and args.cached_source_activation,
                    "maintenance watcher literal/original clock binding differs")
    klass = maintenance_watchdog(sources["watcher"], sources["checkpoint"], config, sources["supervisor"], sources["publisher_guard"])
    if arm:return klass(args)
    # Reuse existing observer methods without constructor writes/re-arm.
    watch = klass.__new__(klass)
    watch.args, watch.cached = args, True
    watch.state_path, watch.stop, watch.log = Path(args.state), Path(args.stop), Path(args.log)
    watch.state = json.loads(private(args.state))
    watch.contract = json.loads(private(args.manifest_contract))
    watch.scopes = sources["watcher"].CORE_SCOPES + [("media", "kavita")]
    watch.save = lambda: None
    return watch


def native_reader(ref):
    """Use the existing read-only DB/WAL reader without executing its CLI main."""
    tree = ast.parse(private(ref["path"], ref["sha256"]))
    wanted = {"remote", "source_stat", "capture"}
    tree.body = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
                 or isinstance(node, ast.FunctionDef) and node.name in wanted
                 or isinstance(node, ast.Assign) and all(isinstance(target, ast.Name) and target.id in {"FILES", "SOURCE_DIR"} for target in node.targets)]
    catalog.require({node.name for node in tree.body if isinstance(node, ast.FunctionDef)} == wanted,
                    "existing Native capture function set differs")
    scope = {}
    exec(compile(tree, ref["path"], "exec"), scope)
    return scope["capture"]


def verify_native_privacy(directory):
    with catalog.metadata.safe_directory(str(directory)) as fd:
        info = os.fstat(fd)
        catalog.require(info.st_uid == os.getuid() and info.st_mode & 0o777 == 0o700, "Native output directory privacy differs")
        for name in os.listdir(fd):
            catalog.require(name in {"kavita.db", "kavita.db-wal", "kavita.db-shm", "copy-proof.json"}, "unexpected Native capture file")
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            catalog.require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                            and info.st_nlink == 1 and info.st_mode & 0o777 == 0o600,
                            "Native raw capture privacy differs")


def private_native_capture(capture, pod, directory):
    """The existing reader relies on its caller's umask for private raw copies."""
    directory = Path(directory)
    with catalog.metadata.safe_directory(str(directory.parent)):
        catalog.require(not os.path.lexists(directory), "fresh Native output directory required")
    previous = os.umask(0o077)
    try:
        path, receipt = capture(pod, directory)
        verify_native_privacy(directory)
    finally:
        os.umask(previous)
    catalog.require(Path(path) == Path(directory) / "kavita.db", "Native capture path differs")
    return path, receipt


def private_native_snapshot(capture, pod, directory):
    previous = os.umask(0o077)
    try:
        path, receipt = private_native_capture(capture, pod, directory)
        with contextlib.closing(sqlite3.connect("file:" + str(path.resolve()) + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON");db.execute("BEGIN")
            state = catalog.snapshot(db);db.rollback()
        verify_native_privacy(directory)  # Read-only WAL access may create a SHM sidecar.
        return state, receipt
    finally:
        os.umask(previous)


def scan_after_normal(config, watch, capture, token):
    """Existing target admin route, real commit evidence, full fresh preservation."""
    catalog.require(config["owner_approved"] is True and config["root_scan_go"] is True
                    and config["prepared_only"] is False, "target scan admission missing")
    until = config["original_scan_deadline_epoch"]
    catalog.require(0 < until - time.time() <= 300, "original target scan clock invalid")
    watch.desired_restored(config["normal_merge_sha"])
    def normal():
        catalog.require(time.time() < until, "original scan admission expired")
        watch.state = json.loads(private(watch.args.state))
        catalog.require(watch.state.get("complete") is True, "writer-first recovery incomplete")
        watch.verify_phase_absent(watch.phase_checkpoint())
        catalog.require(watch.runtime_restored(config["normal_merge_sha"]), "current Normal scan boundary lost")
        source = watch.kube("gitrepository", "haynes-ops", "flux-system")
        catalog.require(source["spec"].get("suspend", False) is False
                        and source["status"]["artifact"]["revision"] == "main@sha1:" + config["normal_merge_sha"], "Normal Source differs")
        for namespace, name in watch.recovery_scopes():
            ks = watch.kube("kustomization", name, namespace)
            catalog.require(ks["metadata"]["uid"] == watch.state["cached_ks_owners"][namespace + "/" + name]["uid"]
                            and sys.modules["cached_source"].OWNER not in ks["metadata"].get("annotations", {})
                            and ks["status"].get("observedGeneration") == ks["metadata"]["generation"], "Normal controller custody differs")
        pod = watch.kube("pod", config["native_pod_name"], "media")
        states = pod.get("status", {}).get("containerStatuses", [])
        catalog.require(pod["metadata"]["uid"] == config["native_pod_uid"] and not pod["metadata"].get("deletionTimestamp")
                        and pod["spec"] == config["native_pod_spec"] and pod.get("status", {}).get("phase") == "Running"
                        and len(states) == 1 and states[0]["name"] == "app" and states[0]["restartCount"] == 0
                        and states[0].get("ready") is True and "running" in states[0].get("state", {})
                        and states[0]["imageID"].endswith(config["native_image"].split("@", 1)[1]), "native scanner identity changed")
    def files():
        raw = watch.run(["kubectl", "exec", "-n", "media", config["native_pod_name"], "-c", "app", "--",
                         "find", str(Path(catalog.FILE).parent), "-maxdepth", "1", "-type", "f", "-exec", "sha256sum", "{}", "+"], timeout=10)
        rows = sorted(raw.splitlines())
        catalog.require(len(rows) == 5 and all(re.fullmatch(r"[0-9a-f]{64}  " + re.escape(str(Path(catalog.FILE).parent)) + r"/[^/\n]+", line) for line in rows), "target five-file scope differs")
        return rows
    def fresh(directory):
        normal()
        state, receipt = private_native_snapshot(capture, config["native_pod_name"], directory)
        catalog.require(receipt["before"] == receipt["after"] and receipt["readOnlySource"] is True, "Native DB copy unstable")
        normal()
        return state
    original = json.loads(private(config["original_rows"]["path"], config["original_rows"]["sha256"]))
    before, before_files = fresh(config["scan_before_directory"]), files()
    catalog.require(before == catalog.expected_after(original), "protected state drift before target scan; hold retained")
    normal()
    started = time.time()
    request = urllib.request.Request("http://kavita.media.svc.cluster.local:5000/api/Series/scan",
        data=b'{"libraryId":1,"seriesId":1650}', headers={"Content-Type": "application/json", "Authorization": "Bearer " + token}, method="POST")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):raise catalog.Refused("native API redirect refused")
    with urllib.request.build_opener(NoRedirect).open(request, timeout=min(10, until - time.time())) as response:
        catalog.require(response.status == 200, "target scan enqueue failed")
        response.read(65536)
    since = dt.datetime.fromtimestamp(started, dt.timezone.utc).isoformat().replace("+00:00", "Z")
    process = subprocess.Popen(["kubectl", "logs", "--follow", "-n", "media", config["native_pod_name"], "-c", "app", "--since-time=" + since, "--timestamps"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    selector, seen, pending, count = selectors.DefaultSelector(), [], bytearray(), 0
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while len(seen) < 2:
            catalog.require(time.time() < until and process.poll() is None, "target native completion not observed")
            catalog.require(selector.select(timeout=min(3, until - time.time())), "target native completion stream stalled")
            chunk = os.read(process.stdout.fileno(), 4096);catalog.require(chunk, "native log stream closed")
            pending.extend(chunk);count += len(chunk)
            catalog.require(count <= 1024 * 1024 and len(pending) <= 65536, "native scan log cap exceeded")
            while b"\n" in pending:
                line, _, pending = pending.partition(b"\n")
                text = line.decode("utf-8")
                marker = "Beginning file scan on Ransom" if not seen else "Finished series update on Ransom in "
                if marker in text:
                    at = dt.datetime.fromisoformat(text.split(" ", 1)[0].replace("Z", "+00:00")).timestamp()
                    catalog.require(started - 1 <= at <= time.time() + 1 and (not seen or at >= seen[0]), "native start/commit time differs")
                    seen.append(at)
                    if len(seen) == 2:break
    finally:
        selector.close()
        if process.poll() is None:os.killpg(process.pid, signal.SIGTERM)
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:os.killpg(process.pid, signal.SIGKILL);process.wait(timeout=2)
    finished = time.time()
    after = fresh(config["scan_after_directory"])
    catalog.require(files() == before_files, "Native scan changed target file bytes")
    catalog.require_scan_delta(before, after, config["native_explicit_values"], started, finished)
    output = Path(config["post_scan_rows_output"])
    with catalog.metadata.safe_directory(str(output.parent)) as fd:
        catalog.metadata._write_file(fd, output.name, catalog.canonical(after))
    return {"schema": 1, "library_id": 1, "series_id": 1650, "queued": True, "started_epoch": seen[0],
            "committed_epoch": seen[1], "scan_started_epoch": started, "scan_finished_epoch": finished,
            "before_sha256": catalog.digest(before), "after_sha256": catalog.digest(after),
            "protected_native_rows_preserved": True, "target_file_bytes_preserved": True,
            "post_scan_rows_path": str(output), "inverse_runtime_authority": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("lock-admission", "watch", "create", "inspect", "execute", "run", "scan"))
    parser.add_argument("config");parser.add_argument("config_sha256")
    args = parser.parse_args(argv)
    config = json.loads(private(args.config, args.config_sha256))
    sources = load_sources(config)
    if args.command == "lock-admission":
        def run(argv, timeout=10):
            result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            catalog.require(result.returncode == 0, "read-only Sonarr lock admission command refused")
            return result.stdout
        def get(kind, name, namespace):return json.loads(run(["kubectl", "get", kind, name, "-n", namespace, "-o", "json"]))
        result = lock_admission(config, get, run)
        output = Path(config["converter_lock_admission_output"])
        with catalog.metadata.safe_directory(str(output.parent)) as fd:
            catalog.metadata._write_file(fd, output.name, catalog.canonical(result))
        print(json.dumps({"path": str(output), "sha256": catalog.digest(result)}))
        return
    watch = make_watch(config, sources, arm=args.command == "watch")
    if args.command == "watch":watch.loop();return
    if args.command == "scan":
        credential = json.loads(sys.stdin.readline(65536))
        catalog.require(set(credential) == {"token"}, "private admin credential input differs")
        print(json.dumps(scan_after_normal(config, watch, native_reader(config["native_reader"]), credential["token"])))
        return
    try:
        host = HostAdmission(config, watch, sources["checkpoint"], sources["pod_guard"], sources["supervisor"], sources["publisher_guard"], publisher_pending=args.command == "run")
        if args.command == "create":result = host.create()
        elif args.command == "inspect":result = host.inspect()
        else:
            # Existing Hnet Secret may be read by the authorized host pod-exec route;
            # caller sends only this private stdin object, never a credential in argv.
            credentials = json.loads(sys.stdin.readline(65536))
            catalog.require(set(credentials) == {"dsn"}, "private host credential input differs")
            if args.command == "run":result = host.run_automatic(credentials["dsn"])
            else:
                for ref in config["deliveries"]:
                    host.deliver(ref["name"], private(ref["path"], ref["sha256"]))
                result = host.execute(credentials["dsn"])
    except BaseException:
        Path(config["watcher_stop"]).touch(mode=0o600, exist_ok=True)
        raise
    print(json.dumps(result))


if __name__ == "__main__":
    main()
