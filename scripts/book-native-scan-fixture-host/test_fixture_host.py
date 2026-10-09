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


def node():
    return {"apiVersion": "v1", "kind": "Node", "metadata": {"name": "talosw01", "uid": "44444444-4444-4444-8444-444444444444", "labels": {"topology.kubernetes.io/region": "main", "topology.kubernetes.io/zone": "w"}}, "spec": {"podCIDR": "10.42.3.0/24"}}


def objects():
    manifest = h.manifest(TEMPLATE, PHASE)
    job = copy.deepcopy(manifest)
    job["metadata"]["uid"] = JOBUID
    template = job["spec"]["template"]
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": copy.deepcopy(template["metadata"]), "spec": copy.deepcopy(template["spec"])}
    pod["metadata"].update(name="fixture-pod", namespace="media", uid=PODUID, ownerReferences=[{"kind": "Job", "apiVersion": "batch/v1", "controller": True, "name": job["metadata"]["name"], "uid": JOBUID}])
    pod["metadata"]["labels"]["batch.kubernetes.io/controller-uid"] = JOBUID
    pod["metadata"]["labels"].update(node()["metadata"]["labels"])
    pod["metadata"]["finalizers"] = ["batch.kubernetes.io/job-tracking"]
    pod["metadata"].setdefault("annotations", {})["k8s.v1.cni.cncf.io/network-status"] = json.dumps([{"name": "cilium", "interface": "eth0", "ips": ["10.42.3.129"], "mac": "32:e6:57:96:8f:b0", "default": True, "dns": {}, "gateway": ["10.42.3.185"]}])
    pod["status"] = {"phase": "Running", "podIP": "10.42.3.129", "podIPs": [{"ip": "10.42.3.129"}], "containerStatuses": [{"name": "native-scanner", "restartCount": 0, "imageID": h.IMAGE}]}
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
        h.pod_binding(job, pod, manifest, PHASE, node())
        self.assertEqual(manifest["spec"]["activeDeadlineSeconds"], 180)
        self.assertFalse(manifest["spec"]["template"]["spec"]["enableServiceLinks"])

    def test_new_mount_init_or_network_namespace_refuses(self):
        manifest, job, pod = objects()
        changes = (lambda p: p["spec"].update(initContainers=[{"name": "extra"}]), lambda p: p["spec"].update(hostNetwork=True), lambda p: p["spec"]["volumes"].append({"name": "prod", "persistentVolumeClaim": {"claimName": "kavita"}}), lambda p: p["spec"].update(automountServiceAccountToken=True))
        for change in changes:
            bad = copy.deepcopy(pod)
            change(bad)
            with self.assertRaises(h.Refused):
                h.pod_binding(job, bad, manifest, PHASE, node())

    def test_actual_image_restart_or_owner_conflict_refuses(self):
        manifest, job, pod = objects()
        for change in (lambda p: p["status"]["containerStatuses"][0].update(restartCount=1), lambda p: p["status"]["containerStatuses"][0].update(imageID="different"), lambda p: p["metadata"]["ownerReferences"][0].update(uid=str(uuid.uuid4()))):
            bad = copy.deepcopy(pod)
            change(bad)
            with self.assertRaises(h.Refused):
                h.pod_binding(job, bad, manifest, PHASE, node())

    def test_named_pod_get_requires_exact_typed_list_identity(self):
        _, _, actual = objects()
        listed = copy.deepcopy(actual)
        listed.pop("apiVersion")
        listed.pop("kind")
        with tempfile.TemporaryDirectory() as out:
            fixture = h.Fixture.__new__(h.Fixture)
            fixture.out, fixture.get = Path(out), mock.Mock(return_value=actual)
            self.assertEqual(fixture.named_pod(listed, retain=True), actual)
            self.assertEqual(json.loads(h.read_private(Path(out) / "admission-observed-pod.json")), actual)
            self.assertEqual(fixture.get.call_args.args, ("pod", actual["metadata"]["name"]))
        changes = (lambda p: p["metadata"].update(uid=str(uuid.uuid4())), lambda p: p.update(kind="Node"), lambda p: p.pop("apiVersion"), lambda p: p["metadata"].update(namespace="other"), lambda p: p["metadata"].update(name="reused"))
        for change in changes:
            bad = copy.deepcopy(actual)
            change(bad)
            fixture = h.Fixture.__new__(h.Fixture)
            fixture.get = mock.Mock(return_value=bad)
            with self.assertRaisesRegex(h.Refused, "named_pod_identity_changed"):
                fixture.named_pod(listed)

    def test_generated_pod_metadata_is_verified_and_other_metadata_stays_exact(self):
        manifest, job, pod = objects()
        h.pod_binding(job, pod, manifest, PHASE, node())
        def attachment_change(p, change):
            key = "k8s.v1.cni.cncf.io/network-status"
            value = json.loads(p["metadata"]["annotations"][key])
            change(value)
            p["metadata"]["annotations"][key] = json.dumps(value)
        changes = (
            lambda p: p["metadata"]["finalizers"].append("injected/finalizer"),
            lambda p: p["metadata"]["labels"].update({"topology.kubernetes.io/zone": "other"}),
            lambda p: attachment_change(p, lambda a: a.append(copy.deepcopy(a[0]))),
            lambda p: attachment_change(p, lambda a: a[0]["ips"].append("10.42.3.130")),
            lambda p: attachment_change(p, lambda a: a[0].update(ips=["10.42.3.130"])),
            lambda p: attachment_change(p, lambda a: a[0].update(extra=True)),
            lambda p: attachment_change(p, lambda a: a[0].update(name="secondary")),
            lambda p: attachment_change(p, lambda a: a[0].update(gateway=["10.42.4.1"])),
            lambda p: p["metadata"]["annotations"].update({"injected/annotation": "unexpected"}),
            lambda p: p["metadata"]["annotations"].update({"k8tz.io/inject": "true"}),
            lambda p: p["metadata"]["labels"].update({"injected/label": "unexpected"}),
            lambda p: p["spec"].update(hostNetwork=True),
        )
        for change in changes:
            bad = copy.deepcopy(pod)
            change(bad)
            with self.assertRaises(h.Refused):
                h.pod_binding(job, bad, manifest, PHASE, node())

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

    def test_contextless_native_kubectl_keeps_original_argv_and_deadline(self):
        # The deployed kubectl loses in-cluster fallback when a timeout override
        # is injected. Exercise the real bounded pipe path without any API call.
        real_popen = h.subprocess.Popen
        def local_cli(args, **kwargs):
            return real_popen([sys.executable, "-c", "import json,sys;print(json.dumps(sys.argv[1:]))", *args[1:]], **kwargs)
        with tempfile.TemporaryDirectory() as out, mock.patch.object(h.subprocess, "Popen", side_effect=local_cli):
            raw = h.Native(out, time.time() + 3).call(["kubectl", "get", "pods"], seconds=2)
        self.assertEqual(json.loads(raw), ["get", "pods"])

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

    def test_terminal_zero_requires_current_completed_job_and_pod(self):
        _, job, pod = objects()
        pod["status"]["containerStatuses"][0]["state"] = {"terminated": {"exitCode": 0, "reason": "Completed"}}
        self.assertFalse(h.completed(job, pod))
        job["status"] = {"conditions": [{"type": "Complete", "status": "True"}], "active": 0, "failed": 0, "succeeded": 1}
        pod["status"]["phase"] = "Succeeded"
        self.assertTrue(h.completed(job, pod))
        for change in (lambda j, p: p["status"].update(phase="Running"), lambda j, p: j["status"].update(active=1), lambda j, p: j["status"].update(failed=1), lambda j, p: j["status"].update(succeeded=True), lambda j, p: j["status"]["conditions"].append({"type": "Failed", "status": "True"}), lambda j, p: p["status"]["containerStatuses"][0]["state"]["terminated"].update(reason="Error")):
            bad_job, bad_pod = copy.deepcopy(job), copy.deepcopy(pod)
            change(bad_job, bad_pod)
            with self.assertRaises(h.Refused):
                h.completed(bad_job, bad_pod)

    def test_raw_creation_is_durable_before_admission_refusal(self):
        manifest, job, _ = objects()
        job["spec"]["template"]["spec"]["containers"].append({"name": "injected"})
        with tempfile.TemporaryDirectory() as out:
            fixture = h.Fixture.__new__(h.Fixture)
            fixture.out, fixture.ready, fixture.phase, fixture.name, fixture.uid = Path(out), manifest, PHASE, manifest["metadata"]["name"], None
            fixture.list = mock.Mock(side_effect=[inv("Job"), inv("Pod")])
            fixture.call = mock.Mock(side_effect=[h.canonical(manifest), h.canonical(job)])
            with self.assertRaisesRegex(h.Refused, "native_job_admission_drift"):
                fixture.collect()
            self.assertEqual(fixture.uid, JOBUID)
            self.assertEqual(json.loads(h.read_private(Path(out) / "created-job.json")), job)

    def test_dry_run_refusal_retains_raw_response_without_creation(self):
        manifest, admitted, _ = objects()
        admitted["spec"]["template"]["spec"]["hostNetwork"] = True
        with tempfile.TemporaryDirectory() as out:
            fixture = h.Fixture.__new__(h.Fixture)
            fixture.out, fixture.ready, fixture.phase, fixture.name = Path(out), manifest, PHASE, manifest["metadata"]["name"]
            fixture.list = mock.Mock(side_effect=[inv("Job"), inv("Pod")])
            fixture.call = mock.Mock(return_value=h.canonical(admitted))
            with self.assertRaisesRegex(h.Refused, "native_job_admission_drift"):
                fixture.collect()
            self.assertEqual(json.loads(h.read_private(Path(out) / "server-dry-run.json")), admitted)
            self.assertEqual(fixture.call.call_count, 1)
            self.assertIn("--dry-run=server", fixture.call.call_args.args[0])
            self.assertFalse((Path(out) / "create-intent.json").exists())

    def test_running_admission_refusal_retains_job_and_pod_before_private_input(self):
        manifest, created, original_pod = objects()
        for kind in ("job", "pod"):
            observed, pod = copy.deepcopy(created), copy.deepcopy(original_pod)
            if kind == "job":
                observed["spec"]["template"]["spec"]["hostNetwork"] = True
            else:
                pod["spec"]["initContainers"] = [{"name": "injected"}]
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as out:
                fixture = h.Fixture.__new__(h.Fixture)
                fixture.out, fixture.ready, fixture.phase, fixture.name, fixture.uid = Path(out), manifest, PHASE, manifest["metadata"]["name"], None
                fixture.list = mock.Mock(side_effect=[inv("Job"), inv("Pod"), inv("Pod", [pod])])
                fixture.call = mock.Mock(side_effect=[h.canonical(manifest), h.canonical(created)])
                fixture.get = mock.Mock(side_effect=[observed, pod, node()])
                fixture.network, fixture.upload = mock.Mock(), mock.Mock()
                with self.assertRaisesRegex(h.Refused, f"native_{kind}_admission_drift"):
                    fixture.collect()
                self.assertEqual(json.loads(h.read_private(Path(out) / "admission-observed-job.json")), observed)
                self.assertEqual(json.loads(h.read_private(Path(out) / "admission-observed-pod.json")), pod)
                self.assertEqual(fixture.call.call_count, 2)
                fixture.network.assert_not_called()
                fixture.upload.assert_not_called()

    def test_known_create_uid_cleanup_allows_injected_spec_and_phase(self):
        manifest, job, _ = objects()
        job["spec"]["template"]["spec"]["containers"].append({"name": "injected"})
        job["metadata"]["labels"].pop(h.LABEL)
        with tempfile.TemporaryDirectory() as out:
            h.save_private(Path(out) / "created-job.json", job)
            h.save_private(Path(out) / "execution-retired.json", {"phase": PHASE})
            with mock.patch.object(h.Native, "list", side_effect=[inv("Job", [job]), inv("Job"), inv("Pod")]), mock.patch.object(h.Native, "call", return_value=b"{}") as call:
                h.cleanup_locked({"manifest": manifest, "phase": PHASE, "end": time.time() + 2}, out)
            self.assertEqual(json.loads(call.call_args.args[1])["preconditions"], {"uid": JOBUID})

    def test_unknown_create_recovery_never_adopts_foreign_phase(self):
        manifest, job, _ = objects()
        job["metadata"]["labels"][h.LABEL] = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as out:
            h.save_private(Path(out) / "initial-jobs.json", inv("Job"))
            h.save_private(Path(out) / "create-intent.json", {"phase": PHASE})
            h.save_private(Path(out) / "execution-retired.json", {"phase": PHASE})
            with mock.patch.object(h.Native, "get", return_value=job), mock.patch.object(h.Native, "call") as call:
                with self.assertRaisesRegex(h.Refused, "cleanup_reused_name"):
                    h.cleanup_locked({"manifest": manifest, "phase": PHASE, "end": time.time() + 2}, out)
            call.assert_not_called()

    def test_lost_create_not_found_is_unknown_without_absence_claim(self):
        manifest, _, _ = objects()
        with tempfile.TemporaryDirectory() as out:
            h.save_private(Path(out) / "initial-jobs.json", inv("Job"))
            h.save_private(Path(out) / "create-intent.json", {"phase": PHASE})
            h.save_private(Path(out) / "execution-retired.json", {"phase": PHASE})
            with mock.patch.object(h.Native, "get", side_effect=h.Refused("native_request_refused")), mock.patch.object(h.Native, "cleanup") as cleanup:
                with self.assertRaises(h.Refused):
                    h.cleanup_locked({"manifest": manifest, "phase": PHASE, "end": time.time() + 2}, out)
            cleanup.assert_not_called()
            self.assertFalse((Path(out) / "cleanup-receipt.json").exists())

    def test_actual_admitted_fixture_modules_are_checked_before_private_upload(self):
        with tempfile.TemporaryDirectory() as out:
            fixture = h.Fixture.__new__(h.Fixture)
            paths = h.FIXTURE_MODULES + h.MODULES
            fixture.out = Path(out)
            fixture.approval = {"expectedFixtureModules": {p: "a" * 64 for p in paths}}
            fixture.call = mock.Mock(return_value=("\n".join("a" * 64 + "  " + p for p in paths) + "\n").encode())
            self.assertEqual(fixture.fixture_modules({"metadata": {"name": "synthetic"}}), fixture.approval["expectedFixtureModules"])
            fixture.call.return_value = fixture.call.return_value.replace(b"a" * 64, b"b" * 64, 1)
            with self.assertRaisesRegex(h.Refused, "actual_fixture_modules_changed_before_input"):
                fixture.fixture_modules({"metadata": {"name": "synthetic"}})

    def test_interrupted_custody_stage_recovers_only_current_exact_phase_uid(self):
        manifest, job, _ = objects()
        with tempfile.TemporaryDirectory() as out:
            h.save_private(Path(out) / "created-job.json.pending-interrupted", b'{"metadata":')
            h.save_private(Path(out) / "initial-jobs.json", inv("Job"))
            h.save_private(Path(out) / "create-intent.json", {"phase": PHASE})
            h.save_private(Path(out) / "execution-retired.json", {"phase": PHASE})
            with mock.patch.object(h.Native, "get", return_value=job), mock.patch.object(h.Native, "cleanup") as cleanup:
                h.cleanup_locked({"manifest": manifest, "phase": PHASE, "end": time.time() + 2}, out)
            self.assertEqual(cleanup.call_args.args[2], JOBUID)
            self.assertEqual(json.loads(h.read_private(Path(out) / "recovered-job.json")), job)
            self.assertFalse((Path(out) / "created-job.json").exists())

    def test_cleanup_cannot_prove_absence_before_request_retirement(self):
        manifest, _, _ = objects()
        with tempfile.TemporaryDirectory() as out, mock.patch.object(h.Native, "cleanup") as cleanup:
            with self.assertRaisesRegex(h.Refused, "requests_not_retired"):
                h.cleanup_locked({"manifest": manifest, "phase": PHASE, "end": 0}, out)
            cleanup.assert_not_called()

    def test_watchdog_preserves_valid_main_gc_near_200_cap(self):
        with tempfile.TemporaryDirectory() as out:
            state = {"phase": PHASE, "end": 180, "runnerPid": 876543210, "runnerStartTicks": "bound"}
            path = Path(out) / "state.json"
            h.save_private(path, state)
            h.save_private(Path(out) / "execution-retired.json", {"phase": PHASE})
            clock = [171.0]
            def sleep(_): clock[0] += 9
            def cleanup(*_):
                if clock[0] < 198:
                    raise BlockingIOError("main GC owns lock")
                h.save_private(Path(out) / "cleanup-receipt.json", {"allOwnedJobsPodsAbsent": True})
            with mock.patch.object(h.time, "time", side_effect=lambda: clock[0]), mock.patch.object(h.time, "sleep", side_effect=sleep), mock.patch.object(h, "cleanup_locked", side_effect=cleanup), mock.patch.object(h, "kill_owned_runner") as kill:
                h.watchdog(path)
            kill.assert_not_called()
            self.assertTrue(json.loads(h.read_private(Path(out) / "watchdog-receipt.json"))["allOwnedJobsPodsAbsent"])

    def test_frozen_or_pending_create_at_200_is_unknown_without_api_extension(self):
        with tempfile.TemporaryDirectory() as out:
            state = {"phase": PHASE, "end": 180, "runnerPid": 876543210, "runnerStartTicks": "bound"}
            path = Path(out) / "state.json"
            h.save_private(path, state)
            clock = [179.0]
            def sleep(_): clock[0] += 7
            with mock.patch.object(h.time, "time", side_effect=lambda: clock[0]), mock.patch.object(h.time, "sleep", side_effect=sleep), mock.patch.object(h, "cleanup_locked") as cleanup, mock.patch.object(h, "kill_owned_runner") as kill:
                h.watchdog(path)
            cleanup.assert_not_called()
            kill.assert_called_once_with(state)
            self.assertTrue(json.loads(h.read_private(Path(out) / "watchdog-receipt.json"))["unknown"])

    def test_runner_pid_reuse_never_kills_another_group(self):
        state = {"runnerPid": 876543210, "runnerStartTicks": "bound"}
        proc = "876543210 (fixture) " + " ".join(["0"] * 19 + ["reused"])
        with mock.patch.object(Path, "read_text", return_value=proc), mock.patch.object(h.os, "killpg") as kill:
            h.kill_owned_runner(state)
        kill.assert_not_called()

    def test_killed_but_unreaped_request_cannot_publish_retirement(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = h.subprocess.TimeoutExpired("synthetic", 2)
        with tempfile.TemporaryDirectory() as out:
            native = h.Native(out, time.time() + 3)
            native.phase = PHASE
            with mock.patch.object(h.subprocess, "Popen", return_value=process), mock.patch.object(h.selectors, "DefaultSelector", side_effect=h.Refused("synthetic_request")):
                with self.assertRaises(h.subprocess.TimeoutExpired):
                    native.call(["synthetic"], seconds=2)
            self.assertEqual(native.requests, {process})
            process.kill.assert_called_once()
            with mock.patch.object(h.signal, "setitimer"), self.assertRaisesRegex(h.Refused, "active_request_not_reaped"):
                h.retire_collection(native, out)
            self.assertFalse((Path(out) / "execution-retired.json").exists())

    def test_cleanup_deadline_is_armed_before_atomic_retirement_io(self):
        fixture = type("FakeFixture", (), {"end": 180, "phase": PHASE, "requests": set()})()
        order = []
        def timer(_, seconds):
            self.assertAlmostEqual(seconds, 20.1)
            order.append("200-cap")
        def marker(*_):
            self.assertEqual(order, ["200-cap"])
            order.append("retired")
        with mock.patch.object(h.time, "time", return_value=179.9), mock.patch.object(h.signal, "setitimer", side_effect=timer), mock.patch.object(h, "atomic_private_marker", side_effect=marker):
            h.retire_collection(fixture, "/synthetic")
        self.assertEqual(order, ["200-cap", "retired"])

    def test_retirement_marker_is_complete_and_exclusive_on_publication(self):
        with tempfile.TemporaryDirectory() as out:
            path = Path(out) / "marker.json"
            h.atomic_private_marker(path, {"phase": PHASE})
            self.assertEqual(json.loads(h.read_private(path)), {"phase": PHASE})
            self.assertFalse(path.with_name(path.name + ".pending").exists())
            with self.assertRaises(FileExistsError):
                h.atomic_private_marker(path, {"phase": "other"})
            self.assertEqual(json.loads(h.read_private(path)), {"phase": PHASE})

    def test_signal_after_atomic_rename_leaves_complete_single_link_custody(self):
        real_rename = h.rename_noreplace
        def interrupted_rename(*args, **kwargs):
            real_rename(*args, **kwargs)
            h.os.kill(os.getpid(), h.signal.SIGTERM)
        def stop(*_): raise h.Refused("synthetic_interruption")
        previous_handler = h.signal.signal(h.signal.SIGTERM, stop)
        try:
            with tempfile.TemporaryDirectory() as out:
                path = Path(out) / "created-job.json"
                with mock.patch.object(h, "rename_noreplace", side_effect=interrupted_rename), self.assertRaisesRegex(h.Refused, "synthetic_interruption"):
                    h.atomic_private_marker(path, {"phase": PHASE})
                self.assertEqual(path.stat().st_nlink, 1)
                self.assertEqual(json.loads(h.read_private(path)), {"phase": PHASE})
                self.assertEqual(list(Path(out).glob("*.pending-*")), [])
        finally:
            h.signal.signal(h.signal.SIGTERM, previous_handler)

    def test_exact_native_file_time_binding_truncates_to_100ns_and_uses_ny_zone(self):
        self.assertEqual(schema_codec.file_times(946782245123456799), ["2000-01-01 22:04:05.1234567", "2000-01-02 03:04:05.1234567"])
        pin = {"target": "/candidate.epub", "sha256": "a" * 64, "size": 7}
        row = "946782245|2000-01-01 22:04:05.123456799 -0500|7|1:23"
        raw = (row + "\n" + pin["sha256"] + "  /candidate.epub\n" + row + "\n").encode()
        proof = h.file_timestamp_binding(raw, pin)
        self.assertEqual(proof["LastModifiedUtc"], "text:2000-01-02 03:04:05.1234567")
        with self.assertRaisesRegex(h.Refused, "candidate_file_observation_changed"):
            h.file_timestamp_binding(raw.replace(b"1:23", b"1:24", 1), pin)


if __name__ == "__main__":
    unittest.main()
