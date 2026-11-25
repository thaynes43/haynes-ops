"""Mutation checks for the staged, single-Job matting trial."""

import unittest
from copy import deepcopy

from check import STUDIO_PATH, check_matting_deny_all, check_matting_quality, container, render, resource


class QualityJobTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pending = STUDIO_PATH / "matting-pending"
        cls.script = pending / "quality/trial.py"
        cls.template = render(pending / "quality")
        cls.policy = resource(render(pending / "policy"), "CiliumNetworkPolicy", "studio-matting-quality")
        cls.smoke = resource(render(pending / "smoke"), "Job")
        spec = container(cls.smoke)
        cls.image = spec["image"]
        cls.path = spec["volumeMounts"][1]["subPath"]
        cls.memory = tuple(spec["resources"][kind]["memory"] for kind in ("requests", "limits"))

    def check_job(self, documents):
        check_matting_quality(documents, self.script, self.smoke, self.image, self.path, self.memory)

    def test_render_matches_release(self):
        self.check_job(self.template)
        check_matting_deny_all(self.policy["spec"], "studio-matting-quality")

    def test_refuses_image_drift(self):
        docs = deepcopy(self.template)
        container(docs[0])["image"] = self.image.split("@")[0] + "@sha256:" + "f" * 64
        with self.assertRaisesRegex(ValueError, "selected image"):
            self.check_job(docs)

    def test_refuses_writable_input(self):
        docs = deepcopy(self.template)
        container(docs[0])["volumeMounts"][2]["readOnly"] = False
        with self.assertRaisesRegex(ValueError, "read-only input"):
            self.check_job(docs)

    def test_refuses_second_output_mount(self):
        docs = deepcopy(self.template)
        container(docs[0])["volumeMounts"].append(
            {"name": "jobs", "mountPath": "/another-output", "subPath": "data/studio"})
        with self.assertRaisesRegex(ValueError, "one writable output subPath"):
            self.check_job(docs)

    def test_refuses_entire_jobs_tree_in_main(self):
        docs = deepcopy(self.template)
        container(docs[0])["volumeMounts"][3]["subPath"] = "data/studio/jobs"
        with self.assertRaisesRegex(ValueError, "one writable output subPath"):
            self.check_job(docs)

    def test_refuses_writable_weights(self):
        for at_mount in (True, False):
            with self.subTest(at_mount=at_mount):
                docs = deepcopy(self.template)
                if at_mount:
                    container(docs[0])["volumeMounts"][1]["readOnly"] = False
                else:
                    docs[0]["spec"]["template"]["spec"]["volumes"][1]["nfs"]["readOnly"] = False
                with self.assertRaisesRegex(ValueError, "read-only weights"):
                    self.check_job(docs)

    def test_refuses_main_storage_uid_and_missing_group(self):
        for key, value in (("runAsUser", 1000), ("supplementalGroups", [])):
            with self.subTest(key=key):
                docs = deepcopy(self.template)
                docs[0]["spec"]["template"]["spec"]["securityContext"][key] = value
                with self.assertRaisesRegex(ValueError, "UID 10001 with group 1000"):
                    self.check_job(docs)

    def test_refuses_wrong_reservation_uid_or_gid(self):
        for key in ("runAsUser", "runAsGroup"):
            with self.subTest(key=key):
                docs = deepcopy(self.template)
                init = docs[0]["spec"]["template"]["spec"]["initContainers"][0]
                init["securityContext"][key] = 10001
                with self.assertRaisesRegex(ValueError, "UID/GID 1000 init container"):
                    self.check_job(docs)

    def test_refuses_network_policy_label_change(self):
        docs = deepcopy(self.template)
        docs[0]["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/name"] = "studio-matting-smoke"
        with self.assertRaisesRegex(ValueError, "deny-all network label"):
            self.check_job(docs)

    def test_refuses_network_policy_without_deny_all(self):
        policy = deepcopy(self.policy)
        del policy["spec"]["egressDeny"]
        with self.assertRaisesRegex(ValueError, "dedicated deny-all network policy"):
            check_matting_deny_all(policy["spec"], "studio-matting-quality")

    def test_refuses_day_long_retention(self):
        docs = deepcopy(self.template)
        docs[0]["spec"]["ttlSecondsAfterFinished"] = 86400
        with self.assertRaisesRegex(ValueError, "one-hour TTL"):
            self.check_job(docs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
