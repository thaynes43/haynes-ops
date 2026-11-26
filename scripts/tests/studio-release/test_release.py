"""Exercise the release entry point and the documented Wan idle gates offline."""

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import unittest
from contextlib import ExitStack, redirect_stdout
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml


spec = importlib.util.spec_from_file_location("studio_release_check", Path(__file__).with_name("check.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
STUDIO = guard.STUDIO_PATH
AUTH_SOURCE = b"""
class WorkerRoute(MetadataRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def authenticated(request):
            await run_in_threadpool(worker_access, request)
            return await handler(request)
        return authenticated

def worker_access(request):
    if len(headers) != 1:
        raise unauthenticated()
    if scheme.lower() != 'bearer' or not valid_worker_bearer(token):
        raise unauthenticated()
    worker = identity.get_worker_lane(conn, token)
    if worker is None:
        raise unauthenticated()
    return worker
"""
ROUTES_SOURCE = ("""
from .auth import WorkerRoute
class WorkRoute(WorkerRoute):
    pass
def worker_router():
    router = APIRouter(prefix="/internal/v1/work", route_class=WorkRoute)
""" + "\n".join(f"    @router.post('/{route}')\n    def route_{index}(): pass"
               for index, route in enumerate(guard.MEDIA_ROUTES))).encode()
REVIEWED_MIGRATION = b"reviewed 0013 migration bytes"


class ReleaseFixture:
    def setUp(self):
        contracts = {
            "matting": {"request_gib": 8, "limit_gib": 10},
            "segmentation": {
                "measurement": {"request_gib": 16, "limit_gib": 16},
                "serving": {"request_gib": 4, "limit_gib": 5, "weight_load": "mmap"},
            },
            "upscaling": {"measuring": {"request_gib": 2, "limit_gib": 8}, "quality_peak_gib": 1},
        }
        release = yaml.safe_load((STUDIO / "release.yaml").read_text())
        digests = {guard.API_IMAGE: release["apiDigest"], f"{guard.API_IMAGE}-web": release["webDigest"]}
        self.enterContext(patch.object(guard, "studio_image_digest", side_effect=lambda image, _: digests[image]))
        sources = {
            "api/run_routes.py": ROUTES_SOURCE, "api/auth.py": AUTH_SOURCE,
            "db/migrations/versions/0013_matte_jobs.py": REVIEWED_MIGRATION,
        }
        self.enterContext(patch.object(guard, "studio_source", side_effect=lambda _, path: sources[path]))
        self.enterContext(patch.object(guard, "published_studio_file", side_effect=lambda _, name: name.encode()))
        self.enterContext(patch.object(guard, "MIGRATION_0013_SHA256", sha256(REVIEWED_MIGRATION).hexdigest()))
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


class EntryPointTest(ReleaseFixture, unittest.TestCase):
    def test_check_invokes_each_backend_once(self):
        with ExitStack() as stack:
            output = stack.enter_context(redirect_stdout(io.StringIO()))
            # The committed upscaling pin remains stale until the CR-56 image publishes.
            stack.enter_context(patch.object(guard, "STALE_UPSCALING_DIGESTS", set()))
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

    def test_check_refuses_upscaling_store_drift_from_studio_api(self):
        render = guard.render

        def render_with_different_api_store(path):
            documents = render(path)
            if path == STUDIO / "app":
                api = guard.resource(documents, "Deployment", "studio-api")
                store_root = guard.env(api, "api")["STUDIO_STORE_ROOT"]
                mounts = guard.container(api, "api")["volumeMounts"]
                next(mount for mount in mounts if mount["mountPath"] == store_root)["subPath"] = "data/studio"
            return documents

        with (
            patch.object(guard, "render", side_effect=render_with_different_api_store),
            self.assertRaisesRegex(ValueError, "upscaling store sub-path must match the studio-api Deployment"),
        ):
            guard.check(STUDIO)


class FourthPinMutationTest(ReleaseFixture, unittest.TestCase):
    def check_refuses_rendered_mutation(self, stage, mutate, expected):
        render = guard.render
        changed = render(stage)
        mutate(changed)
        with (
            patch.object(guard, "render", side_effect=lambda path: changed if path == stage else render(path)),
            self.assertRaisesRegex(ValueError, expected),
        ):
            guard.check(STUDIO)

    def test_rendered_api_refuses_the_paused_lane_switch_even_when_set_to_zero(self):
        def add_switch(documents):
            api = guard.resource(documents, "Deployment", "studio-api")
            guard.container(api, "api")["env"].append({"name": "STUDIO_PAUSED_RECONCILE", "value": "0"})

        self.check_refuses_rendered_mutation(STUDIO / "app", add_switch, "STUDIO_PAUSED_RECONCILE must be unset")

    def test_render_lock_provenance_refuses_a_different_target_lock(self):
        lock = (STUDIO.with_name("studio-render") / "app/requirements.txt").read_text()
        revision = yaml.safe_load((STUDIO / "release.yaml").read_text())["revision"]
        other_revision = "a" * 40
        altered = re.sub(r"(?m)^# [0-9a-f]{40} ", f"# {other_revision} ", lock, count=1)

        def files(commit, name):
            if name == "uv.lock" and commit == revision:
                return b"different target lock"
            return name.encode()

        requirements = STUDIO.with_name("studio-render") / "app/requirements.txt"
        with (
            self.override_source(requirements, altered),
            patch.object(guard, "published_studio_file", side_effect=files),
            self.assertRaisesRegex(ValueError, "must export PyAV 18.1.1 from the same Studio release lock"),
        ):
            guard.check(STUDIO)

    def test_active_flux_inventory_keeps_matte_staged(self):
        repo = Path.cwd()
        _, _, _, stages = guard.studio_inventory(repo)
        self.assertEqual({name for name in stages if name.startswith("studio-matte-")}, {guard.MATTE_POLICY_STAGE})
        for name in ("studio", "studio-worker", "studio-media-workers", "studio-media-worker-registration"):
            with self.subTest(stage=name):
                active = guard.flux_build(repo, stages[name])
                self.assertFalse(any("studio-matte-" in doc["metadata"]["name"] for doc in active))
        self.assertEqual(stages["studio-render"]["spec"]["path"], f"./{STUDIO.with_name('studio-render')}/app")

    def test_old_render_pyav_is_refused(self):
        path = STUDIO.with_name("studio-render") / "app/requirements.txt"
        with (
            self.override_source(path, path.read_text().replace("av==18.1.1", "av==18.1.0")),
            self.assertRaisesRegex(ValueError, "offline render must export PyAV 18.1.1"),
        ):
            guard.check(STUDIO)

    def test_registry_digest_cross_check_and_unfixed_migration_refusal(self):
        with (
            patch.object(guard, "studio_image_digest", return_value="sha256:" + "a" * 64),
            self.assertRaisesRegex(ValueError, "digest must match the published Studio revision"),
        ):
            guard.check(STUDIO)
        source = guard.studio_source
        with (
            patch.object(guard, "studio_source",
                         side_effect=lambda revision, name: b"old migration" if name.endswith("0013_matte_jobs.py")
                         else source(revision, name)),
            self.assertRaisesRegex(ValueError, "repin to published Studio main images"),
        ):
            guard.check(STUDIO)

    def test_registry_listener_requires_worker_token_on_every_forwarded_route(self):
        guard.check_listener_auth(ROUTES_SOURCE, AUTH_SOURCE)
        for routes, auth in (
            (ROUTES_SOURCE.replace(b"route_class=WorkRoute", b"route_class=MetadataRoute"), AUTH_SOURCE),
            (ROUTES_SOURCE.replace(b"@router.post('/claim')", b"@router.get('/claim')"), AUTH_SOURCE),
            (ROUTES_SOURCE, AUTH_SOURCE.replace(b"await run_in_threadpool(worker_access, request)", b"pass")),
            (ROUTES_SOURCE, AUTH_SOURCE.replace(b"valid_worker_bearer(token)", b"True")),
            (ROUTES_SOURCE, AUTH_SOURCE.replace(b"identity.get_worker_lane(conn, token)", b"'wrong grant'")),
            (ROUTES_SOURCE.replace(b"/internal/v1/work", b"/other"), AUTH_SOURCE),
            (ROUTES_SOURCE.replace(b"class WorkRoute(WorkerRoute)", b"class WorkRoute(MetadataRoute)"), AUTH_SOURCE),
            (ROUTES_SOURCE.replace(b"from .auth import WorkerRoute", b"from .other import WorkerRoute"), AUTH_SOURCE),
            (ROUTES_SOURCE.replace(b"    pass", b"    def get_route_handler(self): pass", 1), AUTH_SOURCE),
            (ROUTES_SOURCE, AUTH_SOURCE.replace(b"return authenticated", b"return handler")),
        ):
            with self.assertRaisesRegex(ValueError, "every port-8002 route must require a worker token"):
                guard.check_listener_auth(routes, auth)
        with self.assertRaisesRegex(ValueError, "must define worker_access exactly once"):
            guard.check_listener_auth(ROUTES_SOURCE, AUTH_SOURCE + b"\ndef worker_access(request): pass\n")

    def test_entrypoint_refuses_a_listener_without_the_bearer_handler_or_grant_lookup(self):
        for previous, replacement in (
            (b"await run_in_threadpool(worker_access, request)", b"pass"),
            (b"identity.get_worker_lane(conn, token)", b"'wrong grant'"),
        ):
            studio_source = guard.studio_source
            with (
                self.subTest(source=previous),
                patch.object(guard, "studio_source", side_effect=lambda rev, name:
                             AUTH_SOURCE.replace(previous, replacement) if name == "api/auth.py" else studio_source(rev, name)),
                self.assertRaisesRegex(ValueError, "every port-8002 route must require a worker token"),
            ):
                guard.check(STUDIO)

    def test_listener_must_mount_and_read_the_existing_config(self):
        for target, keys, value in (
            ("pod", ("volumes", "media-worker-routes", "configMap", "name"), "wrong-config"),
            ("listener", ("volumeMounts", "media-worker-routes", "mountPath"), "/wrong"),
            ("listener", ("volumeMounts", "media-worker-routes", "readOnly"), False),
            ("listener", ("args", 1), "/wrong/nginx.conf"),
        ):
            def mutate(documents):
                api = guard.resource(documents, "Deployment", "studio-api")
                objects = {"pod": api["spec"]["template"]["spec"], "listener": guard.container(api, "media-worker-routes")}
                change(objects[target], keys, value)

            with self.subTest(field=keys):
                self.check_refuses_rendered_mutation(STUDIO / "app", mutate, "must keep and mount the exact existing media listener configuration")

    def test_listener_keeps_the_existing_routes_and_config_map(self):
        path = STUDIO / "app/media-worker-routes.conf"
        with (
            self.override_source(path, path.read_text().replace("|fail", "|fail|release-matte")),
            self.assertRaisesRegex(ValueError, "port 8002 must allow exactly the five existing media POST routes"),
        ):
            guard.check(STUDIO)

        def change_config(documents):
            guard.resource(documents, "ConfigMap", guard.PREDECESSOR_LISTENER)["data"]["nginx.conf"] += "# changed\n"

        self.check_refuses_rendered_mutation(STUDIO / "app", change_config, "exact existing media listener configuration")

    def test_render_keeps_20_cpus_and_predecessor_requirements(self):
        stage = STUDIO.with_name("studio-render") / "app"

        def change_cpu(documents):
            release = guard.resource(documents, "HelmRelease", "studio-render")
            change(release, ("spec", "values", "controllers", "studio-render", "containers", "app",
                             "resources", "limits", "cpu"), "19")

        def change_previous_config(documents):
            guard.resource(documents, "ConfigMap", "studio-render-requirements")["data"]["requirements.txt"] = "bad"

        self.check_refuses_rendered_mutation(stage, change_cpu, "offline render app must limit CPU to 20")
        self.check_refuses_rendered_mutation(stage, change_previous_config, "keep the byte-identical offline requirements")

    def test_render_turnover_requires_its_config_contents_init_and_recreate(self):
        render_path = STUDIO.with_name("studio-render")
        requirements = (render_path / "app/requirements.txt").read_text()
        render = guard.render
        for target, field, value in (
            ("config", "requirements.txt", "wrong export"),
            ("config", "render-init.py", "wrong init"),
            ("app", "command", ["sleep", "infinity"]),
            ("controller", "strategy", "RollingUpdate"),
            ("volume", "name", "studio-render-requirements"),
        ):
            docs = render(render_path / "app")
            values = guard.resource(docs, "HelmRelease", "studio-render")["spec"]["values"]
            controller = values["controllers"]["studio-render"]
            volume = values["persistence"]["config"]
            config = guard.resource(docs, "ConfigMap", volume["name"])["data"]
            objects = {"controller": controller, "app": controller["containers"]["app"],
                       "volume": volume, "config": config}
            objects[target][field] = value
            with (
                self.subTest(target=target, field=field),
                patch.object(guard, "render", return_value=docs),
                self.assertRaisesRegex(ValueError, "offline replacement must use its own config"),
            ):
                guard.check_render_turnover(STUDIO, requirements)


def change(document, keys, value):
    for key in keys[:-1]:
        document = (next(item for item in document if item["name"] == key)
                    if isinstance(document, list) and isinstance(key, str) else document[key])
    key = keys[-1]
    document[key] = value(document[key]) if callable(value) else value


class RepinTest(unittest.TestCase):
    def test_repin_preserves_the_export_only_when_both_inputs_are_unchanged(self):
        new_revision = "b" * 40
        with TemporaryDirectory(prefix="studio-pin-test-") as directory:
            root = Path(directory)
            path = root / "studio"
            offline = root / "studio-render/app/requirements.txt"
            offline.parent.mkdir(parents=True)
            for changed in (None, "pyproject.toml", "uv.lock"):
                with self.subTest(changed=changed):
                    for name in (*guard.PIN_FILES, "release.yaml"):
                        (path / name).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(STUDIO / name, path / name)
                    offline.write_text("existing rendered export\n")

                    def published_file(revision, name):
                        return b"changed" if (revision, name) == (new_revision, changed) else name.encode()

                    with (
                        patch.object(guard, "published_studio_file", side_effect=published_file),
                        patch.object(guard, "studio_image_digest", side_effect=("sha256:" + "b" * 64, "sha256:" + "c" * 64)),
                        patch.object(guard, "export_render_requirements", return_value="new export\n") as export,
                    ):
                        guard.repin(path, new_revision, "1")
                    self.assertEqual(yaml.safe_load((path / "release.yaml").read_text())["revision"], new_revision)
                    if changed:
                        export.assert_called_once_with(new_revision)
                        self.assertEqual(offline.read_text(), "new export\n")
                    else:
                        export.assert_not_called()
                        self.assertEqual(offline.read_text(), "existing rendered export\n")


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
