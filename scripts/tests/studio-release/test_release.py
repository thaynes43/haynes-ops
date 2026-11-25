"""Exercise the release entry point and the documented Wan idle gates offline."""

import importlib.util
import io
import json
import os
import re
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
render_cluster_inventory = guard.render_cluster_inventory
repository_cluster_policies = guard.repository_cluster_policies
STUDIO = guard.STUDIO_PATH
MATTE = STUDIO / guard.MATTE_ACTIVATION
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
        matting = yaml.safe_load((STUDIO / "matting-pending/release/release.yaml").read_text())["data"]
        release = yaml.safe_load((STUDIO / "release.yaml").read_text())
        digests = {guard.API_IMAGE: release["apiDigest"], f"{guard.API_IMAGE}-web": release["webDigest"]}
        self.enterContext(patch.object(guard, "studio_image_digest", side_effect=lambda image, _: digests[image]))
        sources = {
            "api/run_routes.py": ROUTES_SOURCE, "api/auth.py": AUTH_SOURCE,
            "db/migrations/versions/0013_matte_jobs.py": REVIEWED_MIGRATION,
        }
        self.enterContext(patch.object(guard, "studio_source", side_effect=lambda _, path: sources[path]))
        self.enterContext(patch.object(guard, "MIGRATION_0013_SHA256", sha256(REVIEWED_MIGRATION).hexdigest()))
        configs = {}
        for backend, contract in contracts.items():
            configs[f"{guard.API_IMAGE}-{backend}"] = {
                "os": "linux", "architecture": "amd64",
                "config": {"User": "10001:10001", "Cmd": ["studio-segmentation", "serve"], "Labels": {
                    "org.opencontainers.image.source": guard.STUDIO_REPO,
                    "org.opencontainers.image.revision": matting["revision"] if backend == "matting" else "a" * 40,
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

        def fixture_inventory(_):
            docs = [{"kind": "Namespace", "metadata": {"name": "ai"}}]
            for stage in (STUDIO / "app", STUDIO / "worker", STUDIO / "media-workers", MATTE):
                for doc in guard.render(stage):
                    if doc["kind"] in guard.NAMESPACE_POLICY_KINDS:
                        docs.append({**doc, "metadata": {"namespace": "ai", **doc["metadata"]}})
            return docs

        self.enterContext(patch.object(guard, "render_cluster_inventory", side_effect=fixture_inventory))
        self.enterContext(patch.object(guard, "repository_cluster_policies", return_value=[]))

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
            ("matting", "matting provisioning, smoke and quality trial must stay staged"),
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
            elif path == STUDIO / guard.MATTE_ACTIVATION:
                matte = guard.resource(documents, "Deployment", guard.MATTE_WORKER)
                mounts = guard.container(matte, "worker")["volumeMounts"]
                next(mount for mount in mounts if mount["name"] == "store")["subPath"] = "data/studio"
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

    def test_wrong_matte_worker_digest_is_refused(self):
        def mutate(stage):
            worker = guard.resource(stage, "Deployment", guard.MATTE_WORKER)
            guard.container(worker, "worker")["image"] = f"{guard.API_IMAGE}@sha256:" + "0" * 64

        self.check_refuses_rendered_mutation(
            STUDIO / guard.MATTE_ACTIVATION, mutate, "matte worker and registration Job must use the Studio release digest",
        )

    def test_listener_missing_release_route_is_refused(self):
        path = STUDIO / "app/media-worker-routes.conf"
        with (
            self.override_source(path, path.read_text().replace("|release-matte", "")),
            self.assertRaisesRegex(ValueError, "port 8002 must allow exactly the six media POST routes"),
        ):
            guard.check(STUDIO)

    def test_matting_sidecar_on_run_worker_is_refused(self):
        def mutate(stage):
            worker = guard.resource(stage, "Deployment", "studio-worker")
            matte = guard.resource(guard.render(STUDIO / guard.MATTE_ACTIVATION), "Deployment", guard.MATTE_WORKER)
            worker["spec"]["template"]["spec"]["containers"].append(guard.container(matte, "matting"))

        self.check_refuses_rendered_mutation(
            STUDIO / "worker", mutate, "matting sidecar belongs only on the matte worker pod",
        )

    def test_dropped_label_selector_is_refused(self):
        def mutate(stage):
            policy = guard.resource(stage, "NetworkPolicy", guard.MATTE_WORKER)
            policy["spec"]["egress"][1]["to"][0]["podSelector"]["matchLabels"]["pod-template-hash"] = "ignored"

        self.check_refuses_rendered_mutation(
            STUDIO / guard.MATTE_ACTIVATION, mutate, "Cilium leaves out of pod identities",
        )

    def test_old_render_pyav_is_refused(self):
        path = STUDIO.with_name("studio-render") / "app/requirements.txt"
        with (
            self.override_source(path, path.read_text().replace("av==18.1.1", "av==18.1.0")),
            self.assertRaisesRegex(ValueError, "offline render must export PyAV 18.1.1"),
        ):
            guard.check(STUDIO)

    def test_stage_wiring_and_sidecar_metadata(self):
        path = STUDIO / "ks.yaml"
        for stage, field, value, error in (
            (guard.MATTE_WORKER, "dependsOn", [{"name": "studio"}], "matte deployment must wait"),
            (guard.MATTE_WORKER, "path", "./wrong", "matte deployment must wait"),
            (guard.MATTE_WORKER, "wait", False, "matte deployment must wait"),
            (guard.MATTE_WORKER, "prune", False, "matte deployment must wait"),
            ("studio-matting-policy", "path", "./wrong", "matting policy must use the policy path"),
            ("studio-matting-policy", "prune", False, "matting policy must use the policy path"),
            (f"{guard.MATTE_WORKER}-registration", "dependsOn", [{"name": "studio-migration"}],
             "matte registration must wait"),
            (f"{guard.MATTE_WORKER}-registration", "path", "./wrong", "matte registration must wait"),
        ):
            flux = list(yaml.safe_load_all(path.read_text()))
            guard.resource(flux, "Kustomization", stage)["spec"][field] = value
            with self.subTest(stage=stage, field=field), self.override_source(path, yaml.safe_dump_all(flux)):
                with self.assertRaisesRegex(ValueError, error):
                    guard.check(STUDIO)
        path = STUDIO / "matting-pending/release/release.yaml"
        for field, value in (("revision", "b" * 40), ("profileSha256", "wrong")):
            pin = yaml.safe_load(path.read_text())
            pin["data"][field] = value
            with self.subTest(field=field), self.override_source(path, yaml.safe_dump(pin)):
                with self.assertRaisesRegex(ValueError, "published sidecar revision and provisioned profile hash"):
                    guard.check(STUDIO)

    def test_worker_and_sidecar_runtime_contract(self):
        work_error = "matte worker must use its lane, worker listener"
        mount_error = "matte worker must share only scratch"
        token_error = "matte token must be mounted only in the worker"
        for name, keys, value, error in (
            ("worker", ("command",), ["studio-worker"], work_error),
            ("worker", ("args",), ["studio-worker", "--lane", "image"], work_error),
            ("worker", ("env", "STUDIO_API_URL", "value"), "http://studio-api:8000", work_error),
            ("worker", ("env", "STUDIO_MATTING_REVISION", "value"), "b" * 40, work_error),
            ("worker", ("env", "STUDIO_MATTING_PROFILE_SHA256", "value"), "b" * 64, work_error),
            ("worker", ("startupProbe", "exec", "command"), ["true"], "300-second progress marker"),
            ("worker", ("livenessProbe", "exec", "command"), ["true"], "300-second progress marker"),
            ("worker", ("volumeMounts", "matte-scratch", "mountPath"), "/wrong", mount_error),
            ("worker", ("volumeMounts", "store", "subPath"), "data/studio", mount_error),
            ("worker", ("env", "STUDIO_STORE_ROOT", "value"), "/wrong", mount_error),
            ("worker", ("volumeMounts",), lambda mounts: [*mounts, {"name": "weights", "mountPath": "/weights"}],
             mount_error),
            ("pod", ("volumes", "store", "nfs", "path"), "/wrong", mount_error),
            ("worker", ("volumeMounts", "worker-token", "readOnly"), False, token_error),
            ("pod", ("volumes", "worker-token", "secret", "secretName"), "other", token_error),
            ("pod", ("securityContext", "fsGroup"), 1000, mount_error),
            ("pod", ("nodeSelector", "kubernetes.io/hostname"), "talosw02", mount_error),
            ("pod", ("automountServiceAccountToken",), True, token_error),
            ("pod", ("shareProcessNamespace",), True, token_error),
            ("deployment", ("strategy", "type"), "RollingUpdate", mount_error),
            ("matting", ("command",), ["studio-matting", "ready"], "must serve as 10001:10001"),
            ("matting", ("securityContext", "runAsUser"), 1000, "must serve as 10001:10001"),
            ("matting", ("securityContext", "readOnlyRootFilesystem"), False, "no credentials, read-only root"),
            ("matting", ("envFrom",), [{"secretRef": {"name": "studio-matte-worker-token"}}], "no credentials"),
            ("matting", ("livenessProbe", "exec", "command"), ["true"], "Unix HTTP readiness"),
            ("matting", ("startupProbe", "exec", "command"), ["true"], "Unix HTTP readiness"),
            ("matting", ("readinessProbe", "exec", "command", 3),
             lambda value: value.replace("200 or", "200 and"), "exact profile hash"),
            ("matting", ("volumeMounts", "weights", "readOnly"), False, "read-only pinned weights"),
            ("matting", ("lifecycle", "preStop", "sleep", "seconds"), 0, "preStop sleep"),
            ("worker", ("lifecycle", "preStop", "sleep", "seconds"), 0, "preStop sleep"),
            ("account", ("automountServiceAccountToken",), True, token_error),
        ):
            def mutate(stage):
                worker = guard.resource(stage, "Deployment", guard.MATTE_WORKER)
                objects = {"deployment": worker["spec"], "pod": worker["spec"]["template"]["spec"],
                           "account": guard.resource(stage, "ServiceAccount", guard.MATTE_WORKER),
                           "worker": guard.container(worker, "worker"), "matting": guard.container(worker, "matting")}
                change(objects[name], keys, value)

            with self.subTest(container=name, field=keys):
                self.check_refuses_rendered_mutation(MATTE, mutate, error)

        def mount_token_in_sidecar(stage):
            worker = guard.resource(stage, "Deployment", guard.MATTE_WORKER)
            guard.container(worker, "matting")["volumeMounts"].append({
                "name": "worker-token", "mountPath": "/etc/worker", "readOnly": True,
            })

        self.check_refuses_rendered_mutation(MATTE, mount_token_in_sidecar, token_error)

    def test_worker_token_sources_and_sidecar_environment(self):
        unset = {"secretKeyRef": {"name": "studio-matte-worker-token", "key": "STUDIO_WORKER_TOKEN"}}
        for component, keys, value in (
            ("worker", ("env",), lambda env: [*env, {"name": "STUDIO_WORKER_TOKEN", "value": "synthetic"}]),
            ("worker", ("env",), lambda env: [*env, {"name": "EXTRA", "valueFrom": unset}]),
            ("worker", ("envFrom",), [{"secretRef": {"name": "studio-matte-worker-token"}}]),
            ("worker", ("volumeMounts", "worker-token", "mountPath"), "/wrong"),
            ("matting", ("env",), lambda env: [*env, {"name": "EXTRA", "value": "synthetic"}]),
        ):
            def mutate(documents):
                deployment = guard.resource(documents, "Deployment", guard.MATTE_WORKER)
                change(guard.container(deployment, component), keys, value)

            with self.subTest(container=component, field=keys, value=value):
                self.check_refuses_rendered_mutation(MATTE, mutate, "matte token must be mounted only in the worker")

    def test_worker_identity_host_network_and_exact_limits(self):
        for component, keys, value, message in (
            ("pod", ("securityContext", "runAsUser"), 1001, "must run as UID and GID 1000"),
            ("pod", ("securityContext", "runAsGroup"), 10001, "must run as UID and GID 1000"),
            ("worker", ("securityContext", "runAsUser"), 10001, "must run as UID and GID 1000"),
            ("pod", ("serviceAccountName",), "default", "no service-account token"),
            ("pod", ("hostPID",), True, "separate PID"),
            ("pod", ("hostNetwork",), True, "matte worker must share only scratch"),
            ("worker", ("resources", "limits", "cpu"), "1200m", "matte worker must share only scratch"),
            ("worker", ("resources", "limits", "memory"), "2Gi", "matte worker must share only scratch"),
            ("deployment", ("replicas",), 2, "matte worker must share only scratch"),
        ):
            def mutate(documents):
                deployment = guard.resource(documents, "Deployment", guard.MATTE_WORKER)
                target = {"pod": deployment["spec"]["template"]["spec"],
                          "worker": guard.container(deployment, "worker"), "deployment": deployment["spec"]}
                change(target[component], keys, value)

            with self.subTest(component=component, field=keys):
                self.check_refuses_rendered_mutation(MATTE, mutate, message)

    def test_readiness_probe_requires_the_reviewed_socket_setup_and_profile_binding(self):
        pin = yaml.safe_load((STUDIO / "matting-pending/release/release.yaml").read_text())["data"]["profileSha256"]
        for keys, value in (
            (("readinessProbe", "exec", "command", 1), "-I"),
            (("readinessProbe", "exec", "command", 3), lambda program: program.replace("/v1/ready", "/v1/progress")),
            (("readinessProbe", "exec", "command", 3), lambda program: program.replace("raise SystemExit", "result = ")),
            (("env", "STUDIO_MATTING_PROFILE_SHA256", "value"), "wrong-profile"),
        ):
            with self.subTest(field=keys, value=value):
                sidecar = guard.container(guard.resource(guard.render(MATTE), "Deployment", guard.MATTE_WORKER), "matting")
                change(sidecar, keys, value)
                with self.assertRaisesRegex(ValueError, "exact profile hash"):
                    guard.check_matte_readiness(sidecar, pin)

    def test_effective_network_and_label_coverage(self):
        app = STUDIO / "app"
        for stage, kind, name, keys, value, error in (
            (MATTE, "NetworkPolicy", guard.MATTE_WORKER, ("spec", "egress"),
             lambda rules: [*rules, {}], "matte worker effective egress"),
            (MATTE, "NetworkPolicy", guard.MATTE_WORKER, ("spec", "ingress"),
             [{}], "matte worker must refuse pod ingress"),
            (MATTE, "Deployment", guard.MATTE_WORKER, ("spec", "template", "metadata", "labels", "app.kubernetes.io/name"),
             "different", "pod labels must match"),
            (MATTE, "NetworkPolicy", guard.MATTE_WORKER, ("spec", "podSelector", "matchLabels", "app.kubernetes.io/name"),
             "different", "selector must cover the rendered pod labels"),
            (app, "NetworkPolicy", "studio-api-ingress", ("spec", "ingress"),
             lambda rules: [*rules, {}], "Studio API effective ingress"),
            (app, "NetworkPolicy", "studio-api-ingress", ("spec", "ingress", 1, "from"),
             [], "Studio API effective ingress"),
            (app, "NetworkPolicy", "studio-api-ingress", ("spec", "podSelector", "matchLabels", "app.kubernetes.io/name"),
             "different", "selector must cover the rendered pod labels"),
        ):
            def mutate(documents):
                change(guard.resource(documents, kind, name), keys, value)

            with self.subTest(stage=stage, kind=kind, field=keys):
                self.check_refuses_rendered_mutation(stage, mutate, error)

        def second_policy(documents):
            documents.append({"kind": "NetworkPolicy", "metadata": {"name": "bypass-from-all"},
                              "spec": {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "studio-api"}},
                                       "ingress": [{}]}})

        self.check_refuses_rendered_mutation(app, second_policy, "Studio API effective ingress")

    def test_effective_policies_include_another_rendered_flux_kustomization(self):
        self.check_effective_policy_in_flux_stage(sibling=False)

    def test_effective_policies_include_active_siblings_of_cluster_apps(self):
        self.check_effective_policy_in_flux_stage(sibling=True)

    def check_effective_policy_in_flux_stage(self, *, sibling):
        repo = Path(self.enterContext(TemporaryDirectory(prefix="op967-policy-inventory-")))
        cluster = repo / "kubernetes/main"
        (cluster / "flux/config").mkdir(parents=True)
        source = {"kind": "GitRepository", "name": "haynes-ops", "namespace": "flux-system"}

        def flux(name, path, **extra):
            return {"apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization",
                    "metadata": {"name": name, "namespace": "flux-system"},
                    "spec": {"path": f"./kubernetes/main/{path}", "sourceRef": source, **extra}}

        (cluster / "flux/config/cluster.yaml").write_text(yaml.safe_dump(flux("cluster", "flux")))
        (cluster / "flux/apps.yaml").write_text(yaml.safe_dump(flux("cluster-apps", "apps")))
        (cluster / "apps").mkdir()
        path = "policy" if sibling else "apps/other-stage/app"
        other = cluster / path
        other.mkdir(parents=True)
        if sibling:
            stage = cluster / "flux/other-stage.yaml"
            (cluster / "apps/placeholder.yaml").write_text(yaml.safe_dump({
                "apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "other"},
            }))
        else:
            stage = other.parent / "ks.yaml"
            (cluster / "apps/kustomization.yaml").write_text(yaml.safe_dump({
                "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
                "resources": ["other-stage/ks.yaml"],
            }))
        for namespace, should_refuse in (("elsewhere", False), ("ai", True)):
            with self.subTest(target_namespace=namespace):
                stage.write_text(yaml.safe_dump(flux("other-stage", path, targetNamespace=namespace)))
                (other / "extra-policy.yaml").write_text(yaml.safe_dump({
                    "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                    "metadata": {"name": "other-stage-unrestricted"}, "spec": {
                        "podSelector": {"matchExpressions": [{"key": "app.kubernetes.io/name", "operator": "In",
                                                             "values": [guard.MATTE_WORKER]}]},
                        "egress": [{}],
                    },
                }))
                fixture_inventory = guard.render_cluster_inventory
                with patch.object(guard, "render_cluster_inventory", side_effect=lambda root:
                                  fixture_inventory(root) + render_cluster_inventory(repo)):
                    if should_refuse:
                        with self.assertRaisesRegex(ValueError, "matte worker effective egress"):
                            guard.check(STUDIO)
                    else:
                        guard.check(STUDIO)

    def test_effective_egress_applies_empty_kubernetes_policy_types_default(self):
        def unrestricted(documents):
            documents.append({
                "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                "metadata": {"name": "empty-policy-types"}, "spec": {
                    "podSelector": {"matchLabels": {"app.kubernetes.io/name": guard.MATTE_WORKER}},
                    "policyTypes": [], "egress": [{}],
                },
            })

        self.check_refuses_rendered_mutation(MATTE, unrestricted, "matte worker effective egress")

    def test_cilium_policy_specs_rules_and_scope(self):
        key = "k8s:app.kubernetes.io/name"
        label = {"matchExpressions": [{"key": key, "operator": "In", "values": [guard.MATTE_WORKER]}]}
        for kind, namespace, field, rule, error in (
            ("CiliumNetworkPolicy", "ai", "spec", {"egress": [{}]}, "egress"),
            ("CiliumNetworkPolicy", "ai", "specs", {"ingressDeny": [{}]}, "ingress"),
            ("CiliumNetworkPolicy", "elsewhere", "spec", {"egress": [{}]}, None),
            (guard.CLUSTER_POLICY_KIND, None, "specs", {"egressDeny": [{}]}, "egress"),
            (guard.CLUSTER_POLICY_KIND, None, "spec", {"ingress": [{}]}, "ingress"),
        ):
            spec = {"endpointSelector": label, **rule}
            metadata = {"name": "other-policy"}
            if namespace is not None:
                metadata["namespace"] = namespace
            policy = {"kind": kind, "metadata": metadata, field: spec if field == "spec" else [spec]}
            inventory = guard.render_cluster_inventory
            with (
                self.subTest(kind=kind, namespace=namespace, field=field, rule=rule),
                patch.object(guard, "render_cluster_inventory", side_effect=lambda root: [*inventory(root), policy]),
            ):
                if error:
                    with self.assertRaisesRegex(ValueError, rf"unexpected Cilium policy other-policy.*{error}"):
                        guard.check(STUDIO)
                else:
                    guard.check(STUDIO)

    def test_repository_clusterwide_policy_outside_the_app_inventory_is_checked(self):
        repo = Path(self.enterContext(TemporaryDirectory(prefix="op967-clusterwide-policy-")))
        folder = repo / "kubernetes/staged"
        folder.mkdir(parents=True)
        (folder / "example.json").write_text(json.dumps({
            "kind": guard.CLUSTER_POLICY_KIND, "metadata": {"name": "future-bypass"},
            "spec": {"endpointSelector": {"matchLabels": {"any:app.kubernetes.io/name": guard.MATTE_WORKER}},
                     "egress": [{}]},
        }))
        with (
            patch.object(guard, "repository_cluster_policies", side_effect=lambda _: repository_cluster_policies(repo)),
            self.assertRaisesRegex(ValueError, "unexpected Cilium policy future-bypass"),
        ):
            guard.check(STUDIO)

    def test_policy_selector_expressions_cover_matching_and_nonmatching_pods(self):
        labels = {"app.kubernetes.io/name": guard.MATTE_WORKER, "example": "present"}
        for op, key, values, matches in (
            ("In", "example", ["present"], True), ("In", "example", ["elsewhere"], False),
            ("NotIn", "example", ["elsewhere"], True), ("NotIn", "example", ["present"], False),
            ("NotIn", "missing", ["present"], True),
            ("Exists", "example", [], True), ("Exists", "missing", [], False),
            ("DoesNotExist", "missing", [], True), ("DoesNotExist", "example", [], False),
        ):
            with self.subTest(operator=op, key=key, values=values):
                selector = {"matchExpressions": [{"key": key, "operator": op, "values": values}]}
                self.assertEqual(guard.selector_matches(selector, labels), matches)

    def test_job_registration_and_projection_contract(self):
        reg = STUDIO / "matte-worker-registration"
        projection = STUDIO / "media-worker-registration"
        for stage, kind, name, keys, value, error in (
            (reg, "Job", None, ("spec", "template", "spec", "containers", "register", "command"), ["true"],
             "matte registration must bind"),
            (reg, "Job", None, ("spec", "template", "spec", "serviceAccountName"), "default",
             "matte registration must bind"),
            (reg, "Job", None, ("spec", "template", "spec", "automountServiceAccountToken"), True,
             "matte registration must bind"),
            (reg, "Job", None, ("spec", "template", "spec", "containers", "register", "args", 5), "voice",
             "matte registration must bind"),
            (reg, "Job", None, ("spec", "template", "spec", "volumes", "token", "secret", "secretName"), "wrong",
             "matte registration must bind"),
            (reg, "Job", None, ("spec", "template", "spec", "containers", "register", "volumeMounts", "token", "readOnly"),
             False, "matte registration must bind"),
            (projection, "Password", f"{guard.MATTE_WORKER}-token", ("spec", "length"), 48,
             "independent|own stable generated token"),
            (projection, "ExternalSecret", f"{guard.MATTE_WORKER}-token", ("spec", "refreshPolicy"), "Periodic",
             "own stable generated token"),
            (projection, "ExternalSecret", f"{guard.MATTE_WORKER}-token", ("spec", "target", "creationPolicy"), "Owner",
             "own stable generated token"),
        ):
            def mutate(documents):
                change(guard.resource(documents, kind, name), keys, value)

            with self.subTest(field=keys):
                self.check_refuses_rendered_mutation(stage, mutate, error)

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
            (ROUTES_SOURCE.replace(b"@router.post('/release-matte')", b"@router.get('/release-matte')"), AUTH_SOURCE),
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

    def test_listener_must_mount_and_read_the_new_config(self):
        for target, keys, value in (
            ("pod", ("volumes", "media-worker-routes", "configMap", "name"), guard.PREDECESSOR_LISTENER),
            ("listener", ("volumeMounts", "media-worker-routes", "mountPath"), "/wrong"),
            ("listener", ("volumeMounts", "media-worker-routes", "readOnly"), False),
            ("listener", ("args", 1), "/wrong/nginx.conf"),
        ):
            def mutate(documents):
                api = guard.resource(documents, "Deployment", "studio-api")
                objects = {"pod": api["spec"]["template"]["spec"], "listener": guard.container(api, "media-worker-routes")}
                change(objects[target], keys, value)

            with self.subTest(field=keys):
                self.check_refuses_rendered_mutation(STUDIO / "app", mutate, "must mount the new rendered listener allowlist")

    def test_listener_predecessor_map_keeps_identity_content_and_prune_rule(self):
        for keys, value in (
            (("data", "nginx.conf"), "changed"),
            (("metadata", "annotations", "kustomize.toolkit.fluxcd.io/prune"), None),
        ):
            def mutate(documents):
                change(guard.resource(documents, "ConfigMap", guard.PREDECESSOR_LISTENER), keys, value)

            with self.subTest(field=keys):
                self.check_refuses_rendered_mutation(
                    STUDIO / "app", mutate, "keep the exact predecessor listener ConfigMap",
                )

    def test_render_split_and_equal_rollout_paths(self):
        render = STUDIO.with_name("studio-render")
        compose = render / "matting-pending"

        def revert_cpu(documents):
            release = guard.resource(documents, "HelmRelease", "studio-render")
            change(release, ("spec", "values", "controllers", "studio-render", "containers", "app",
                             "resources", "limits", "cpu"), "20")

        self.check_refuses_rendered_mutation(compose, revert_cpu, "both live Flux paths")

        def old_render_changed(documents):
            guard.resource(documents, "ConfigMap", "studio-render-requirements")["data"]["requirements.txt"] = "bad"

        changed = guard.render(compose)
        old_render_changed(changed)
        render_docs = guard.render
        with (
            patch.object(guard, "render", side_effect=lambda path: changed if path in {compose, render / "app"}
                         else render_docs(path)),
            self.assertRaisesRegex(ValueError, "keep the byte-identical offline requirements"),
        ):
            guard.check(STUDIO)

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
