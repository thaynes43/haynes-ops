"""Exercise the release entry point and the documented Wan idle gates offline."""

import importlib.util
import io
import json
import os
import re
import subprocess
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml


spec = importlib.util.spec_from_file_location("studio_release_check", Path(__file__).with_name("check.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
STUDIO = guard.STUDIO_PATH


class EntryPointTest(unittest.TestCase):
    def setUp(self):
        contracts = {
            "matting": {"request_gib": 8, "limit_gib": 10},
            "segmentation": {
                "measurement": {"request_gib": 16, "limit_gib": 16},
                "serving": {"request_gib": 4, "limit_gib": 5, "weight_load": "mmap"},
            },
            "upscaling": {"measuring": {"request_gib": 2, "limit_gib": 8}, "quality_peak_gib": 1},
        }
        configs = {}
        for backend, contract in contracts.items():
            configs[f"{guard.API_IMAGE}-{backend}"] = {
                "os": "linux", "architecture": "amd64",
                "config": {"User": "10001:10001", "Cmd": ["studio-segmentation", "serve"], "Labels": {
                    "org.opencontainers.image.source": guard.STUDIO_REPO,
                    "org.opencontainers.image.revision": "a" * 40,
                    "io.haynes-studio.upscaling.comparison": "stored-proxy-frame-v2",
                    f"io.haynes-studio.{backend}.resources": json.dumps({
                        "schema": f"studio.{backend}-resources/v1", **contract,
                    }),
                }},
            }
        self.enterContext(patch.object(guard, "published_config", side_effect=lambda image, digest: configs[image]))
        check_stage = guard.check_segmentation_stage

        def segmentation_stage_with_test_digest(path, flux):
            pin, _ = check_stage(path, flux)
            return pin, "sha256:" + "a" * 64

        # Validate the staged manifests before using a digest for the stub registry.
        # The segmentation suite exercises refusal of the actual stale digests.
        self.enterContext(patch.object(guard, "check_segmentation_stage",
                                       side_effect=segmentation_stage_with_test_digest))

    def override_source(self, path, content):
        read_text = Path.read_text

        def read(candidate, *args, **kwargs):
            return content if candidate == path else read_text(candidate, *args, **kwargs)

        return patch.object(Path, "read_text", read)

    def test_check_invokes_each_backend_once(self):
        with ExitStack() as stack:
            output = stack.enter_context(redirect_stdout(io.StringIO()))
            checks = {
                name: stack.enter_context(patch.object(guard, name, wraps=getattr(guard, name)))
                for name in ("check_matting", "check_segmentation", "check_upscaling")
            }
            guard.check(STUDIO)
            self.assertIn("Studio release pins and Job health gates agree", output.getvalue())
            for name, backend_check in checks.items():
                with self.subTest(backend=name):
                    backend_check.assert_called_once()

    def test_check_propagates_each_backend_refusal(self):
        for backend, allocation, error in (
            ("matting", "invalid", "invalid create-only matting allocation"),
            ("segmentation", "invalid", "invalid segmentation release image or create-only allocation"),
            ("upscaling", "upscaling-op461-fsrcnn-x2-v1-01", "invalid create-only upscaling weights allocation"),
        ):
            release = STUDIO / f"{backend}-pending/release/release.yaml"
            config = yaml.safe_load(release.read_text())
            config["data"]["allocation"] = allocation
            with self.subTest(backend=backend), self.override_source(release, yaml.safe_dump(config)):
                with self.assertRaisesRegex(ValueError, error):
                    guard.check(STUDIO)

    def test_check_refuses_manual_job_stages_in_flux(self):
        path = STUDIO / "ks.yaml"
        flux = path.read_text()
        for backend, error in (
            ("matting", "matting provisioning, smoke, quality trial and worker activation must stay staged"),
            ("segmentation", "Flux may apply only the segmentation policy stage"),
            ("upscaling", "only the upscaling policy stage belongs in Flux"),
        ):
            stage = guard.resource(list(yaml.safe_load_all(flux)), "Kustomization", f"studio-{backend}-policy")
            stage["metadata"]["name"] = f"studio-{backend}-smoke"
            stage["spec"]["path"] = f"./{STUDIO}/{backend}-pending/smoke"
            with self.subTest(backend=backend), self.override_source(path, f"{flux}\n---\n{yaml.safe_dump(stage)}"):
                with self.assertRaisesRegex(ValueError, error):
                    guard.check(STUDIO)


class IdleGateTest(unittest.TestCase):
    def test_runbook_gates_require_ready_video_and_foley_idle(self):
        paths = Path("docs/reference")
        upscaling = (paths / "studio-upscaling-deployment.md").read_text()
        matting = (paths / "studio-matting-deployment.md").read_text()
        matting_gates = [
            block for block in re.findall(r"(?ms)^```bash\n(.*?)^```$", matting) if "wan_idle() {" in block
        ]
        self.assertEqual(len(matting_gates), 2)
        matting_smoke, matting_trial = matting_gates

        def functions(block, *names):
            definitions = []
            for name in names:
                pattern = rf"(?ms)^(?P<indent> *){name}\(\) \{{\n.*?^(?P=indent)\}}$"
                match = re.search(pattern, block)
                self.assertIsNotNone(match, f"missing documented shell function {name}")
                definitions.append(match.group())
            return "\n".join(definitions)

        # Only run the documented helper functions, with the cluster commands stubbed.
        stub = r"""
set -euo pipefail
kubectl() {
  if [[ $1 == top ]]; then
    printf 'talosw01 100m 1%% 100Mi 1%%\n'
  elif [[ $3 == top ]]; then
    printf 'studio-render-pod app 1m 100Mi\n'
  elif [[ $3 == exec ]]; then
    case "${!#}" in
      http://127.0.0.1:8080/v1/ready) printf '%s\n' "$WAN_READY_RESPONSE" ;;
      http://127.0.0.1:8080/health) printf '%s\n' '{"active_job":null}' ;;
      http://127.0.0.1:8188/queue) printf '%s\n' '{"queue_running":[],"queue_pending":[]}' ;;
      *) return 99 ;;
    esac
  else
    return 99
  fi
}
"""
        ready = {"ready": True, "active_video_job": None, "active_foley_job": None}
        cases = (
            ("idle", ready, 0),
            ("Foley active", {**ready, "active_foley_job": "job_foley"}, 1),
            ("video active", {**ready, "active_video_job": "job_video"}, 1),
            ("not ready", {**ready, "ready": False}, 1),
            ("missing readiness", {"active_video_job": None, "active_foley_job": None}, 1),
            ("missing video status", {"ready": True, "active_foley_job": None}, 1),
            ("missing Foley status", {"ready": True, "active_video_job": None}, 1),
        )
        for gate, script in (
            ("upscaling smoke and comparison",
             functions(upscaling, "check", "check_json", "read_endpoint", "idle_gate") + "\nidle_gate"),
            ("matting smoke", functions(matting_smoke, "check_json", "read_endpoint", "wan_idle") + "\nwan_idle"),
            ("matting quality trial", functions(matting_trial, "assert_json", "read_endpoint", "wan_idle") + "\nwan_idle"),
        ):
            for name, payload, status in cases:
                with self.subTest(gate=gate, payload=name):
                    result = subprocess.run(
                        ["bash", "--noprofile", "--norc"], input=f"{stub}\n{script}\n",
                        env={"PATH": os.environ["PATH"], "WAN_READY_RESPONSE": json.dumps(payload)},
                        text=True, capture_output=True, timeout=5,
                    )
                    self.assertEqual(result.returncode, status, result.stderr)


if __name__ == "__main__":
    unittest.main()
