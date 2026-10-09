import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock
import uuid
import schema_codec

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("fixture_host", HERE / "fixture_host.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
TEMPLATE = json.loads((HERE.parent / "book-native-scan-fixture/prepared-runtime.json").read_bytes())
PHASE = "11111111-1111-4111-8111-111111111111"
JOBUID = "22222222-2222-4222-8222-222222222222"
PODUID = "33333333-3333-4333-8333-333333333333"


def objects():
    manifest = h.manifest(TEMPLATE, PHASE)
    job = copy.deepcopy(manifest)
    job["metadata"]["uid"] = JOBUID
    template = job["spec"]["template"]
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": copy.deepcopy(template["metadata"]), "spec": copy.deepcopy(template["spec"])}
    pod["metadata"].update(name="fixture-pod", namespace="media", uid=PODUID, ownerReferences=[{"kind": "Job", "apiVersion": "batch/v1", "controller": True, "name": job["metadata"]["name"], "uid": JOBUID}])
    pod["metadata"]["labels"]["batch.kubernetes.io/controller-uid"] = JOBUID
    pod["status"] = {"phase": "Running", "containerStatuses": [{"name": "native-scanner", "restartCount": 0, "imageID": h.IMAGE}]}
    return manifest, job, pod


def inv(kind, items=()):
    return {"apiVersion": "batch/v1" if kind == "Job" else "v1", "kind": kind + "List", "metadata": {"resourceVersion": "123"}, "items": list(items)}


class HostTests(unittest.TestCase):
    def test_actual_typed_inventory_may_omit_child_type(self):
        _, job, _ = objects()
        job.pop("kind")
        job.pop("apiVersion")
        self.assertEqual(h.inventory(inv("Job", [job]), "Job")["items"], [job])
        for bad in ({**inv("Job"), "kind": "List"}, {**inv("Job"), "metadata": {"resourceVersion": "1", "continue": "next"}}):
            with self.assertRaises(h.Refused):
                h.inventory(bad, "Job")

    def test_sealed_manifest_and_known_admission_defaults(self):
        manifest, job, pod = objects()
        job["spec"].update(completionMode="NonIndexed", suspend=False, manualSelector=False, selector={"matchLabels": {"batch.kubernetes.io/controller-uid": JOBUID}})
        pod["spec"].update(dnsPolicy="ClusterFirst", schedulerName="default-scheduler", serviceAccountName="default", serviceAccount="default")
        pod["spec"]["containers"][0].update(terminationMessagePath="/dev/termination-log", terminationMessagePolicy="File")
        h.pod_binding(job, pod, manifest, PHASE)
        self.assertEqual(manifest["spec"]["activeDeadlineSeconds"], 180)
        self.assertFalse(manifest["spec"]["template"]["spec"]["enableServiceLinks"])

    def test_new_mount_init_or_network_namespace_refuses(self):
        manifest, job, pod = objects()
        changes = (lambda p: p["spec"].update(initContainers=[{"name": "extra"}]), lambda p: p["spec"].update(hostNetwork=True), lambda p: p["spec"]["volumes"].append({"name": "prod", "persistentVolumeClaim": {"claimName": "kavita"}}), lambda p: p["spec"].update(automountServiceAccountToken=True))
        for change in changes:
            bad = copy.deepcopy(pod)
            change(bad)
            with self.assertRaises(h.Refused):
                h.pod_binding(job, bad, manifest, PHASE)

    def test_actual_image_restart_or_owner_conflict_refuses(self):
        manifest, job, pod = objects()
        for change in (lambda p: p["status"]["containerStatuses"][0].update(restartCount=1), lambda p: p["status"]["containerStatuses"][0].update(imageID="different"), lambda p: p["metadata"]["ownerReferences"][0].update(uid=str(uuid.uuid4()))):
            bad = copy.deepcopy(pod)
            change(bad)
            with self.assertRaises(h.Refused):
                h.pod_binding(job, bad, manifest, PHASE)

    def test_union_finds_owner_only_pod_with_missing_phase(self):
        manifest, _, pod = objects()
        pod["metadata"]["labels"].pop(h.LABEL)
        with self.assertRaisesRegex(h.Refused, "owned_pod_conflict"):
            h.owned_pods(inv("Pod", [pod]), manifest["metadata"]["name"], PHASE, JOBUID)

    def test_native_bpf_deny_has_both_wildcards_no_allow(self):
        rows = [{"Flags": 1, "Key": {"Prefixlen": 40, "Identity": 0, "TrafficDirection": direction, "Nexthdr": 0, "DestPortNetwork": 0}} for direction in (0, 1)]
        h.bpf_deny(rows)
        for bad in (rows[:1], [{**rows[0], "Flags": 0}, rows[1]], rows + [{"Flags": 0, "Key": {"Identity": 11}}]):
            with self.assertRaises(h.Refused):
                h.bpf_deny(bad)

    def test_installed_policy_does_not_replace_realized_endpoint_proof(self):
        cnp = {"spec": TEMPLATE["networkDeny"]["spec"], "status": {"conditions": [{"type": "Valid", "status": "True"}]}}
        cep = {"metadata": {"ownerReferences": [{"kind": "Pod", "uid": PODUID}]}, "status": {"id": 44}}
        realized = {"policy-enabled": "both", "denied-ingress-identities": [0], "denied-egress-identities": [0], "policy-revision": 4}
        endpoint = [{"id": 44, "status": {"state": "ready", "policy": {"spec": realized, "realized": realized}}}]
        self.assertTrue(h.realized_deny(cnp, cep, endpoint, PODUID, PHASE)["denyAllRealized"])
        for change in (lambda e: e[0]["status"]["policy"].update(realized={**realized, "policy-enabled": "none"}), lambda e: e[0].update(id=45)):
            bad = copy.deepcopy(endpoint)
            change(bad)
            with self.assertRaises(h.Refused):
                h.realized_deny(cnp, cep, bad, PODUID, PHASE)

    def test_receipt_requires_complete_pass_and_exact_identity(self):
        pins = [{"Path": p, "Sha256": "a" * 64} for p in sorted(h.PROOFS)]
        receipt = {"schema": 1, "Phase": PHASE, "JobUid": JOBUID, "PodUid": PODUID, "outcome": "passed-private-proof", "proofFiles": pins, "productionAuthorization": False}
        raw = h.canonical(receipt)
        event = {"Phase": PHASE, "JobUid": JOBUID, "PodUid": PODUID, "receiptSha256": h.sha(raw), "proofFiles": pins}
        h.validate_receipt(raw, event, PHASE, JOBUID, PODUID)
        for bad in ({**event, "receiptSha256": "b" * 64}, {**event, "PodUid": str(uuid.uuid4())}):
            with self.assertRaises(h.Refused):
                h.validate_receipt(raw, bad, PHASE, JOBUID, PODUID)
        receipt["proofFiles"] = pins[:1]
        raw = h.canonical(receipt)
        with self.assertRaisesRegex(h.Refused, "passed_proof_incomplete"):
            h.validate_receipt(raw, {**event, "proofFiles": pins[:1], "receiptSha256": h.sha(raw)}, PHASE, JOBUID, PODUID)
        receipt["outcome"] = "unknown"
        raw = h.canonical(receipt)
        h.validate_receipt(raw, {**event, "proofFiles": pins[:1], "receiptSha256": h.sha(raw)}, PHASE, JOBUID, PODUID)

    def test_private_copy_is_exclusive_fsynced_hash_verified_and_symlink_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proof.json"
            h.save_private(path, b"synthetic-private-proof")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(h.read_private(path), b"synthetic-private-proof")
            with self.assertRaises(FileExistsError):
                h.save_private(path, b"overwrite")
            alias = Path(directory) / "alias"
            alias.symlink_to(path)
            with self.assertRaises(h.Refused):
                h.read_private(alias)

    def test_cleanup_uses_uid_and_requires_complete_union_absence(self):
        manifest, job, pod = objects()
        with tempfile.TemporaryDirectory() as out:
            native = h.Native(out, time.time() + 5)
            native.list = mock.Mock(side_effect=[inv("Job", [job]), inv("Job"), inv("Pod")])
            native.call = mock.Mock(return_value=b"{}")
            native.cleanup(manifest, PHASE, JOBUID)
            args, payload = native.call.call_args.args
            self.assertEqual(json.loads(payload)["preconditions"], {"uid": JOBUID})
            self.assertIn("--raw", args)
            self.assertTrue((Path(out) / "cleanup-jobs.json").exists())
            self.assertTrue((Path(out) / "cleanup-pods.json").exists())
            self.assertTrue(json.loads(h.read_private(Path(out) / "cleanup-receipt.json"))["allOwnedJobsPodsAbsent"])

    def test_cleanup_reused_name_never_deletes(self):
        manifest, job, _ = objects()
        job["metadata"]["uid"] = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as out:
            native = h.Native(out, time.time() + 5)
            native.list = mock.Mock(return_value=inv("Job", [job]))
            native.call = mock.Mock()
            with self.assertRaises(h.Refused):
                native.cleanup(manifest, PHASE, JOBUID)
            native.call.assert_not_called()

    def test_orphan_owner_only_pod_cleanup_is_uid_scoped(self):
        manifest, _, pod = objects()
        with tempfile.TemporaryDirectory() as out:
            native = h.Native(out, time.time() + 5)
            native.list = mock.Mock(side_effect=[inv("Job"), inv("Pod", [pod]), inv("Job"), inv("Pod")])
            native.call = mock.Mock(return_value=b"{}")
            native.cleanup(manifest, PHASE, JOBUID)
            self.assertEqual(json.loads(native.call.call_args.args[1])["preconditions"], {"uid": PODUID})

    def test_native_request_bounds_oversized_output_without_load_loop(self):
        with tempfile.TemporaryDirectory() as out:
            native = h.Native(out, time.time() + 3)
            with self.assertRaisesRegex(h.Refused, "native_output_cap"):
                native.call([sys.executable, "-c", "import sys; sys.stdout.write('x'*4096)"], limit=1024, seconds=2)

    def test_prepared_or_production_writer_go_is_refused_before_any_runtime(self):
        for value in ({"schema": 1, "runtimeApproval": False}, {"schema": 1, "explicitRootFixtureApproval": True, "runtimeApproval": True, "productionWriterApproval": True}):
            with self.assertRaisesRegex(h.Refused, "exact_root_fixture_go"):
                h.validate_approval(value, time.time())

    def test_packet_launch_gate_precedes_native_builder(self):
        manifest = h.manifest(TEMPLATE, PHASE)
        self.assertEqual(manifest["spec"]["template"]["spec"]["containers"][0]["command"], ["sh", "-c", h.GATE])
        self.assertNotIn("Host.Start", h.GATE)
        self.assertLess(h.GATE.index("approved-packet.json"), h.GATE.index("exec nice"))

    def test_native_schema_codec_refuses_unreviewed_unicode_or_trigger(self):
        self.assertEqual(schema_codec.quote("<&+>`"), '"\\u003C\\u0026\\u002B\\u003E\\u0060"')
        self.assertEqual(schema_codec.quote('"\''), '"\\u0022\\u0027"')
        with self.assertRaises(ValueError):
            schema_codec.quote("non-ascii-é")
        with self.assertRaises(ValueError):
            schema_codec.schema_sha([("trigger", "name", "table", "sql")])

    def test_watchdog_kills_only_exact_owned_group_before_cleanup(self):
        with tempfile.TemporaryDirectory() as out:
            state = {"phase": PHASE, "end": 0, "runnerPid": 876543210, "runnerStartTicks": "bound"}
            path = Path(out) / "state.json"
            h.save_private(path, state)
            proc = "876543210 (fixture) " + " ".join(["0"] * 19 + ["bound"])
            actions = []
            with mock.patch.object(Path, "read_text", return_value=proc), mock.patch.object(h.os, "getpgid", return_value=876543210), mock.patch.object(h.os, "killpg", side_effect=lambda *_: actions.append("kill")), mock.patch.object(h, "cleanup_locked", side_effect=lambda *_: actions.append("cleanup")):
                h.watchdog(path)
            self.assertEqual(actions, ["kill", "cleanup"])
            reused = proc.replace("bound", "reused")
            with mock.patch.object(Path, "read_text", return_value=reused), mock.patch.object(h.os, "killpg") as kill, mock.patch.object(h, "cleanup_locked") as cleanup:
                h.watchdog(path)
            kill.assert_not_called()
            cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
