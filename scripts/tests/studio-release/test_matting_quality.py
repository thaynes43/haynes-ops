"""Mutation checks for the staged, single-Job matting trial."""

import unittest
from copy import deepcopy

from check import STUDIO_PATH, check_matting_quality, container, render, resource


class QualityJobTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pending = STUDIO_PATH / "matting-pending"
        cls.script = pending / "quality/trial.py"
        cls.template = render(pending / "quality")
        cls.smoke = resource(render(pending / "smoke"), "Job")
        spec = container(cls.smoke)
        cls.image = spec["image"]
        cls.path = spec["volumeMounts"][1]["subPath"]
        cls.memory = tuple(spec["resources"][kind]["memory"] for kind in ("requests", "limits"))

    def check_job(self, documents):
        check_matting_quality(documents, self.script, self.smoke, self.image, self.path, self.memory)

    def test_render_matches_release(self):
        self.check_job(self.template)

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
        with self.assertRaisesRegex(ValueError, "only the Studio jobs output share"):
            self.check_job(docs)

    def test_refuses_network_policy_label_change(self):
        docs = deepcopy(self.template)
        docs[0]["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/name"] = "trial"
        with self.assertRaisesRegex(ValueError, "deny-all network label"):
            self.check_job(docs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
