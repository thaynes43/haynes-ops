"""Finite temporary-dir fixtures; no real keys, node writes, CPU burners or load loops."""

import base64
import copy
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

import delivery
import node_install
from public_key import authorized_line, normalize_public


def fixture_public(fill):
    name = b"ssh-ed25519"
    wire = struct.pack(">I", len(name)) + name + struct.pack(">I", 32) + bytes([fill]) * 32
    return b"ssh-ed25519 " + base64.b64encode(wire)


class Guards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.uid, self.gid = os.getuid(), os.getgid()
        # The shared pod's /tmp inherits a setgid group. PVE's audited .ssh does
        # not: make this owned temporary directory match that exact shape.
        os.chown(self.directory, self.uid, self.gid)
        self.directory.chmod(0o700)
        self.original = fixture_public(1) + b" original-access\n"
        self.authorized = self.directory / "authorized_keys"
        self.authorized.write_bytes(self.original)
        self.authorized.chmod(0o600)
        self.public = normalize_public(fixture_public(2) + b" keeper fixture\n")

    def tearDown(self):
        self.temp.cleanup()

    def install(self):
        return node_install.install(self.directory, self.uid, self.gid, self.public)

    def test_preservation_idempotence_and_rollback(self):
        receipt = self.install()
        self.assertTrue(receipt["originalBytesPreserved"])
        self.assertEqual(Path(receipt["backup"]).read_bytes(), self.original)
        self.assertEqual(self.authorized.read_bytes(), self.original + b"\n" + authorized_line(self.public) + b"\n")
        self.assertEqual(self.authorized.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.install()["result"], "already-installed")
        self.assertEqual(node_install.rollback(self.directory, self.uid, self.gid)["result"], "rolled-back")
        self.assertEqual(self.authorized.read_bytes(), self.original)

    def test_later_edit_blocks_install_and_rollback(self):
        self.install()
        changed = self.authorized.read_bytes() + b"# concurrent owner edit\n"
        self.authorized.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "LaterEdit"):
            self.install()
        with self.assertRaisesRegex(ValueError, "LaterEdit"):
            node_install.rollback(self.directory, self.uid, self.gid)
        self.assertEqual(self.authorized.read_bytes(), changed)

    def test_symlink_and_mode_fail_before_authorized_mutation(self):
        self.authorized.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "FileMetadata"):
            self.install()
        self.authorized.chmod(0o600)
        other = self.directory / "existing"
        self.authorized.rename(other)
        self.authorized.symlink_to(other)
        with self.assertRaises(OSError):
            self.install()
        self.assertEqual(other.read_bytes(), self.original)

    def test_existing_same_key_with_other_options_refused(self):
        self.authorized.write_bytes(self.original + self.public + b" broad-key\n")
        with self.assertRaisesRegex(ValueError, "ExistingKeyConflict"):
            self.install()

    def test_prepared_receipt_recovers_before_or_after_atomic_replace(self):
        self.install()
        path = self.directory / node_install.RECEIPT
        receipt = json.loads(path.read_bytes())
        receipt["phase"] = "Prepared"
        path.write_text(json.dumps(receipt))
        self.assertEqual(self.install()["result"], "already-installed")
        receipt["phase"] = "Prepared"
        path.write_text(json.dumps(receipt))
        self.authorized.write_bytes(self.original)
        self.assertEqual(self.install()["result"], "installed")

    def test_invalid_public_wire_and_multiple_lines(self):
        for raw in (b"", b"ssh-ed25519 invalid", fixture_public(2) + b"\n" + fixture_public(3),
                    fixture_public(2) + b"=" * 3, b"restrict," + fixture_public(2)):
            with self.assertRaises(ValueError):
                normalize_public(raw)

    def test_admitted_pod_rejects_credentials_or_injection(self):
        job = delivery.render_job("keeper-node-trust-fixture", "talosw02")
        job["metadata"]["uid"] = "fixture-job-uid"
        pod = copy.deepcopy(job["spec"]["template"])
        pod["metadata"]["ownerReferences"] = [{"uid": "fixture-job-uid", "kind": "Job", "controller": True}]
        pod["spec"]["nodeName"] = "talosw02"
        pod["status"] = {"phase": "Running"}
        delivery.validate_admitted(job, pod)
        for key, value in (("automountServiceAccountToken", True),
                           ("priorityClassName", "household"),
                           ("initContainers", [{"name": "unexpected"}]),
                           ("volumes", [{"name": "private", "secret": {"secretName": "dev-env-keeper-ssh-ca"}}])):
            changed = copy.deepcopy(pod)
            changed["spec"][key] = value
            with self.assertRaises(ValueError):
                delivery.validate_admitted(job, changed)

    def test_refusal_output_is_bounded_and_strictly_allowlisted(self):
        self.assertEqual(delivery.refusal_reason(b'{"result":"refused","reason":"LaterEdit"}'), "LaterEdit")
        for raw in (b'{"result":"refused","reason":"arbitrary output"}', b'not json',
                    b'{"result":"refused","reason":"LaterEdit","extra":"payload"}',
                    b'{"result":"refused","reason":["LaterEdit"]}', b'{}' * 3000):
            self.assertIsNone(delivery.refusal_reason(raw))


if __name__ == "__main__":
    unittest.main()
