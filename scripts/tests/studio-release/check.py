"""Verify the rendered Studio image pins and their Flux Job gates."""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tarfile
from datetime import datetime
from fractions import Fraction
from functools import cache
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.request import Request, urlopen

import yaml


STUDIO_PATH = Path("kubernetes/main/apps/ai/studio")
API_IMAGE = "ghcr.io/thaynes43/haynes-studio"
MATTING_IMAGE = f"{API_IMAGE}-matting"
UPSCALING_IMAGE = f"{API_IMAGE}-upscaling"
UPSCALING_COMPARISON_LABEL = "io.haynes-studio.upscaling.comparison"
SEGMENTATION_IMAGE = f"{API_IMAGE}-segmentation"
STUDIO_REPO = "https://github.com/thaynes43/haynes-studio"
MANIFEST_TYPES = (
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)
PIN_FILES = (
    "ks.yaml", "app/api.yaml", "app/mcp.yaml", "app/web.yaml", "migration/job.yaml",
    "worker/deployment.yaml", "worker-registration/job.yaml", "media-workers/image.yaml",
    "media-workers/voice.yaml", "media-worker-registration/image-job.yaml",
    "media-worker-registration/voice-job.yaml",
    "matting-pending/activation/worker/deployment.yaml", "matte-worker-registration/job.yaml",
)
RENDER_EXPORT = (
    "uv", "export", "--locked", "--no-default-groups", "--no-emit-project",
    "--no-annotate", "--no-header", "--no-hashes", "--python", "3.12",
)
MATTE_WORKER = "studio-matte-worker"
MATTE_ACTIVATION = "matting-pending/activation"
MATTE_POLICY_STAGE = "studio-matte-policy"
MATTE_POLICY_PATH = "matting-pending/worker-policy"
MATTE_COMMAND = ["python", "-B", "/etc/studio/matte-start.py"]
MATTE_PROGRESS = "/run/studio/worker-progress"
MATTE_TOKEN_MOUNT = "/run/secrets/studio-worker"
MEDIA_ROUTES = ("claim", "heartbeat", "publish", "complete", "fail", "release-matte")
PREDECESSOR_LISTENER = "studio-media-worker-routes-cbf48cht27"
PREDECESSOR_LISTENER_SHA256 = "f8b500a868d689f51ee5a0239a6208580d25790a36511b1a2b3069c973b736f1"
PREDECESSOR_RENDER_SHA256 = "51d95b693bf4a07a2f5818d5e8e9143d889b763fe5bc7d4442a62988efd5c4be"
MIGRATION_0013_SHA256 = "6e68c22881ed4129b9557402accaee5a054eb490bb3fc58b3d04b399019d71be"
NAMESPACE_POLICY_KINDS = {"NetworkPolicy", "CiliumNetworkPolicy"}
CLUSTER_POLICY_KIND = "CiliumClusterwideNetworkPolicy"
FLUX_ROOT = Path("kubernetes/main/flux/config/cluster.yaml")
MATTE_STAGES = {"studio-matte-worker", "studio-matte-worker-registration"}
# Flux Kustomization v1 CRD defaults. A field explicitly set in Git must match.
FLUX_SPEC_DEFAULTS = {"force": False}
STALE_MATTING_DIGESTS = {
    "sha256:dd726f79d25eecc15231ad7b9f0ca53c31ae2504666b9abe233ad1cc457a3e6f",
    "sha256:5c431978f3bfe7a48deb062c6b2854ca8b086d1a5d9c600e2db4b1e26d702d72",
    "sha256:" + "0" * 64,
}
STALE_SEGMENTATION_DIGESTS = {
    "sha256:58b037970810333c5ca1350d7acc32e113e06494ab49b69c184c0a8a93458e53",
    "sha256:05de65b703506a4591887e3063dde04a03aa142f28bacde5b3101a495b6a53c0",
    "sha256:3261efedf5d52baa4c53aa0ce6fc8f538b40cb0675fc2dc7a0c2ca74fd8184be",
    "sha256:644c34c52905c00bb6044c1d0f0479eb1b150d9979f6508eea8631e796b45d0e",
    "sha256:" + "0" * 64,
}
STALE_UPSCALING_DIGESTS = {
    "sha256:577f9763a172164ca355de3afc335241deeb3c49ffcb103ba9ebcb1535a1c2ab",
}
GIB = 1024**3
DECIMAL_SI = {"n": -9, "u": -6, "m": -3, "": 0, "k": 3, "M": 6, "G": 9, "T": 12, "P": 15, "E": 18}
BINARY_SI = {f"{unit}i": 1024**power for power, unit in enumerate("KMGTPE", start=1)}


def quantity_units(value, units_per_base, name):
    """Parse a Kubernetes quantity into whole millicores or bytes."""
    text = str(value)
    match = re.fullmatch(r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))([A-Za-z]+|[eE][+-]?[0-9]+)?", text)
    if match is not None and len(text) <= 100:
        magnitude, suffix = match.groups()
        suffix = suffix or ""
        exponent = DECIMAL_SI.get(suffix)
        if re.fullmatch(r"[eE][+-]?[0-9]+", suffix):
            exponent = int(suffix[1:])
        if suffix in BINARY_SI:
            scale = BINARY_SI[suffix]
        elif exponent is not None and -100 <= exponent <= 100:
            scale = Fraction(10) ** exponent
        else:
            raise ValueError(f"invalid Kubernetes {name} limit {value!r}")
        units = Fraction(magnitude) * scale * units_per_base
        if units > 0 and units.denominator == 1:
            return units.numerator
    raise ValueError(f"invalid Kubernetes {name} limit {value!r}")


def resource_limits(spec):
    limits = spec["resources"]["limits"]
    return quantity_units(limits["cpu"], 1000, "CPU"), quantity_units(limits["memory"], 1, "memory")


def studio_image_digest(image, revision):
    """Verify a published tag, immutable manifest and image config using anonymous pull."""
    repository = image.removeprefix("ghcr.io/")
    base = f"https://ghcr.io/v2/{repository}"
    with urlopen(f"https://ghcr.io/token?service=ghcr.io&scope=repository:{repository}:pull",
                 timeout=15) as response:
        token = json.loads(response.read(65536))["token"]

    def fetch(endpoint, ref, accept="application/json"):
        request = Request(f"{base}/{endpoint}/{ref}", headers={"Accept": accept})
        request.add_unredirected_header("Authorization", "Bearer " + token)
        with urlopen(request, timeout=15) as response:
            data = response.read(1024 * 1024 + 1)
            reported_digest = response.headers.get("Docker-Content-Digest")
        digest = "sha256:" + sha256(data).hexdigest()
        if len(data) > 1024 * 1024 or (reported_digest and reported_digest != digest):
            raise ValueError(f"{image} {endpoint}/{ref} failed digest verification")
        return data, digest

    tag = f"sha-{revision}"
    manifest, digest = fetch("manifests", tag, MANIFEST_TYPES)
    if fetch("manifests", digest, MANIFEST_TYPES) != (manifest, digest):
        raise ValueError(f"{image} manifest at {digest} differs from {tag}")
    config_digest = json.loads(manifest)["config"]["digest"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", config_digest):
        raise ValueError(f"{image} has an invalid config digest")
    config, actual = fetch("blobs", config_digest)
    if actual != config_digest:
        raise ValueError(f"{image} config does not match {config_digest}")
    config = json.loads(config)
    labels = config["config"]["Labels"]
    if ((config["os"], config["architecture"]) != ("linux", "amd64")
            or labels.get("org.opencontainers.image.revision") != revision
            or labels.get("org.opencontainers.image.source") != STUDIO_REPO):
        raise ValueError(f"{image} must be a Studio linux/amd64 image built from {revision}")
    print(f"Verified {image}:{tag} @ {digest} (linux/amd64, revision {revision})")
    return digest


def studio_source(revision, name):
    url = f"https://raw.githubusercontent.com/thaynes43/haynes-studio/{revision}/src/haynes_studio/{name}"
    with urlopen(url, timeout=15) as response:
        data = response.read(256 * 1024 + 1)
    if len(data) > 256 * 1024:
        raise ValueError(f"published Studio source is too large: {name}")
    return data


def check_release_migration(revision):
    migration = studio_source(revision, "db/migrations/versions/0013_matte_jobs.py")
    if sha256(migration).hexdigest() != MIGRATION_0013_SHA256:
        raise ValueError("OP967 must repin to published Studio main images carrying the reviewed 0013 lock fix")


def check_listener_auth(routes, auth):
    route_module = ast.parse(routes)
    auth_module = ast.parse(auth)

    def one(parent, node_type, name):
        nodes = [item for item in parent.body if isinstance(item, node_type) and item.name == name]
        if len(nodes) != 1:
            raise ValueError(f"port 8002 token authentication must define {name} exactly once")
        return nodes[0]

    route_class = one(route_module, ast.ClassDef, "WorkRoute")
    router = one(route_module, ast.FunctionDef, "worker_router")
    worker_route = one(auth_module, ast.ClassDef, "WorkerRoute")
    get_handler = one(worker_route, ast.FunctionDef, "get_route_handler")
    authenticated = one(get_handler, ast.AsyncFunctionDef, "authenticated")
    worker_access = one(auth_module, ast.FunctionDef, "worker_access")

    router_calls = [node for node in ast.walk(router) if isinstance(node, ast.Call)
                    and ast.unparse(node.func) == "APIRouter"]
    options = ({item.arg: ast.unparse(item.value) for item in router_calls[0].keywords}
               if len(router_calls) == 1 else {})
    mounted = [
        node.args[0].value for node in ast.walk(router)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "router.post"
        and node.args and isinstance(node.args[0], ast.Constant)
    ]
    refused = {
        ast.unparse(node.test) for node in worker_access.body
        if isinstance(node, ast.If) and [ast.unparse(item) for item in node.body] == ["raise unauthenticated()"]
    }
    if ([ast.unparse(base) for base in route_class.bases] != ["WorkerRoute"]
            or any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "get_route_handler"
                   for node in route_class.body)
            or not any(isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == "auth"
                       and any(alias.name == "WorkerRoute" and alias.asname is None for alias in node.names)
                       for node in route_module.body)
            or options.get("route_class") != "WorkRoute"
            or options.get("prefix") != "'/internal/v1/work'"
            or any(mounted.count(f"/{path}") != 1 for path in MEDIA_ROUTES)
            or [ast.unparse(node) for node in authenticated.body] != [
                "await run_in_threadpool(worker_access, request)", "return await handler(request)",
            ]
            or not isinstance(get_handler.body[-1], ast.Return)
            or ast.unparse(get_handler.body[-1]) != "return authenticated"
            or not {"len(headers) != 1", "scheme.lower() != 'bearer' or not valid_worker_bearer(token)",
                    "worker is None"}.issubset(refused)
            or "identity.get_worker_lane(conn, token)" not in {
                ast.unparse(node) for node in ast.walk(worker_access) if isinstance(node, ast.Call)
            }):
        raise ValueError("every port-8002 route must require a worker token before parsing the request")


def export_render_requirements(revision):
    """Export the offline worker requirements from the selected Studio lock."""
    source = f"https://raw.githubusercontent.com/thaynes43/haynes-studio/{revision}"
    with TemporaryDirectory(prefix="studio-render-lock-") as directory:
        for name in ("pyproject.toml", "uv.lock"):
            with urlopen(f"{source}/{name}", timeout=15) as response:
                Path(directory, name).write_bytes(response.read(4 * 1024 * 1024 + 1))
        requirements = subprocess.check_output(RENDER_EXPORT, cwd=directory, text=True)
        uv = subprocess.check_output(["uv", "--version"], text=True).split()[1]
    return (
        "# Runtime dependencies from thaynes43/haynes-studio uv.lock at\n"
        f"# {revision} (Python 3.12, linux x86_64).\n"
        f"# Generated with uv {uv} from that checkout (project and all dev groups omitted):\n"
        f"# {' '.join(RENDER_EXPORT)}\n{requirements}"
    )


def repin(path, revision, publish_run):
    """Update the release record and its literal Kubernetes references together."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(r"[0-9]+", publish_run):
        raise ValueError("pinning requires the full Studio revision and numeric Studio images run ID")
    release_path = path / "release.yaml"
    previous = yaml.safe_load(release_path.read_text(encoding="utf-8"))
    if not (re.fullmatch(r"[0-9a-f]{40}", previous["revision"])
            and all(re.fullmatch(r"sha256:[0-9a-f]{64}", previous[key])
                    for key in ("apiDigest", "webDigest"))):
        raise ValueError("release.yaml must contain a full revision and the existing image digests")
    api = studio_image_digest(API_IMAGE, revision)
    web = studio_image_digest(f"{API_IMAGE}-web", revision)

    # Replace complete digests before matching bare Job suffixes. A versioned
    # retry suffix belongs to the old Job and is reset on a new digest.
    old_suffix = previous["apiDigest"].removeprefix("sha256:")[:12]
    new_suffix = api.removeprefix("sha256:")[:12]
    updates = {}
    for name in PIN_FILES:
        file = path / name
        content = file.read_text(encoding="utf-8")
        updated = content.replace(previous["revision"], revision)
        updated = updated.replace(previous["apiDigest"], api).replace(previous["webDigest"], web)
        if old_suffix != new_suffix:
            updated = re.sub(rf"(?<=-){old_suffix}(?:-r[1-9][0-9]*)?(?=\s|$)", new_suffix, updated)
        updates[file] = updated
    updates[release_path] = (
        f'# Successful "Studio images" publish from haynes-studio main, run {publish_run}.\n'
        f"revision: {revision}\napiDigest: {api}\nwebDigest: {web}\n"
    )
    updates[path.with_name("studio-render") / "app/requirements.txt"] = export_render_requirements(revision)
    for file, content in updates.items():
        file.write_text(content, encoding="utf-8")


def resource(documents, kind, name=None):
    matches = [
        document for document in documents
        if document and document["kind"] == kind
        and (name is None or document["metadata"]["name"] == name)
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one {kind} {name or ''}, found {len(matches)}")
    return matches[0]


def render(path):
    output = subprocess.check_output(["kustomize", "build", str(path)], text=True)
    return list(yaml.safe_load_all(output))


def flux_build(repo, stage):
    with TemporaryDirectory(prefix="studio-flux-stage-") as directory:
        manifest = Path(directory) / "stage.yaml"
        manifest.write_text(yaml.safe_dump(stage))
        output = subprocess.check_output(
            ["flux", "build", "kustomization", stage["metadata"]["name"],
             "--namespace", stage["metadata"]["namespace"],
             "--path", stage["spec"]["path"], "--kustomization-file", str(manifest),
             "--dry-run", "--in-memory-build"],
            cwd=repo, env={**os.environ, "GOMAXPROCS": "2"}, text=True,
        )
    return [doc for doc in yaml.load_all(output, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) if doc]


def studio_inventory(repo):
    bootstrap = list(yaml.safe_load_all((repo / FLUX_ROOT).read_text()))
    source = resource(bootstrap, "GitRepository", "haynes-ops")
    cluster = resource(bootstrap, "Kustomization", "cluster")
    inventory = resource(flux_build(repo, cluster), "Kustomization", "cluster-apps")
    stages = {
        doc["metadata"]["name"]: doc for doc in flux_build(repo, inventory)
        if doc["kind"] == "Kustomization" and doc["metadata"].get("namespace") == "ai"
        and re.match(r"^studio(?:-|$)", doc["metadata"]["name"])
    }
    return source, cluster, inventory, stages


def flux_reference(stage):
    source = stage["spec"]["sourceRef"]
    return source["kind"], source.get("namespace") or stage["metadata"]["namespace"], source["name"]


def flux_ready(stage):
    status = stage.get("status", {})
    generation = stage["metadata"]["generation"]
    return (not stage["metadata"].get("deletionTimestamp") and not stage["spec"].get("suspend")
            and status.get("observedGeneration") == generation
            and any(condition["type"] == "Ready" and condition["status"] == "True"
                    and condition.get("observedGeneration") == generation
                    for condition in status.get("conditions", [])))


def check_live_flux_spec(name, live, reviewed):
    def compare(actual, expected, field):
        if isinstance(actual, dict) and isinstance(expected, dict):
            for key in sorted(actual.keys() | expected.keys()):
                child = f"{field}.{key}"
                if key not in actual or key not in expected:
                    raise ValueError(f"different pin: live {name} {child} differs from the selected merge")
                compare(actual[key], expected[key], child)
        elif json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
            raise ValueError(f"different pin: live {name} {field} differs from the selected merge")

    implicit = {key: value for key, value in FLUX_SPEC_DEFAULTS.items() if key in live}
    compare(live, {**implicit, **reviewed}, "spec")


def audit_flux(expected, mode, snapshot):
    """Return 0 when ready, 2 while stages converge, or 3 before source pickup."""
    source, inventory, stages = snapshot

    def git(*args):
        return subprocess.check_output(["git", *args], text=True).strip()

    @cache
    def revision(value):
        match = re.fullmatch(r"main@sha1:([0-9a-f]{40})", value)
        if not match:
            raise ValueError(f"unexpected Flux revision {value}")
        commit = match[1]
        if subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], check=False).returncode:
            raise ValueError(f"observed Flux revision {commit} is missing after fetching main")
        return commit

    def ancestor(older, newer):
        return subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], check=False).returncode == 0

    @cache
    def admitted(commit):
        if ancestor(expected, commit):
            if not ancestor(commit, "refs/remotes/origin/main"):
                raise ValueError(f"Flux revision {commit} is outside fetched main")
            return True
        if ancestor(commit, expected):
            return False
        raise ValueError(f"Flux revision {commit} is outside the selected merge history")

    artifact = revision(source.get("status", {}).get("artifact", {}).get("revision", ""))
    if not admitted(artifact):
        print(f"Pending Flux source pickup: {artifact}")
        return 3

    with TemporaryDirectory(prefix="studio-pin-audit-") as directory:
        @cache
        def inputs(commit):
            repo = Path(directory) / commit
            repo.mkdir()
            archive = repo / "source.tar"
            subprocess.run(["git", "archive", "--output", str(archive), commit, "kubernetes"], check=True)
            with tarfile.open(archive) as files:
                files.extractall(repo, filter="data")
            git_source, cluster, parent, definitions = studio_inventory(repo)
            for name, stage in {"cluster": cluster, "cluster-apps": parent, **definitions}.items():
                if flux_reference(stage) != ("GitRepository", "flux-system", "haynes-ops"):
                    raise ValueError(f"different pin at {commit}: {name} source")
            return repo, (git_source["spec"], cluster["spec"], parent["spec"]), definitions

        pinned_repo, pinned_inputs, expected_stages = inputs(expected)
        matte = MATTE_STAGES.intersection(expected_stages)
        if matte != (MATTE_STAGES if mode == "release" else set()):
            raise ValueError(f"selected {mode} revision has unexpected matte stages")
        if MATTE_POLICY_STAGE not in expected_stages:
            raise ValueError(f"selected {mode} revision must retain {MATTE_POLICY_STAGE}")
        if {"timeout": "60s", **source["spec"]} != {"timeout": "60s", **pinned_inputs[0]}:
            raise ValueError("different pin: live GitRepository spec differs from the selected merge")

        @cache
        def tree(commit, path):
            return git("rev-parse", "--verify", f"{commit}:{path.removeprefix('./')}")

        @cache
        def rendered_pin(name):
            return flux_build(pinned_repo, expected_stages[name])

        @cache
        def compare(commit):
            if commit == expected:
                return
            repo, observed_inputs, observed_stages = inputs(commit)
            # The shared inventory can gain or patch other stages. Only its
            # own source/path and the resulting Studio definitions matter.
            if observed_inputs[0] != pinned_inputs[0] or any(
                observed["path"] != pinned["path"]
                for observed, pinned in zip(observed_inputs[1:], pinned_inputs[1:], strict=True)
            ):
                raise ValueError(f"different pin at {commit}: Git source or Flux inventory inputs changed")
            for name in sorted(expected_stages.keys() | observed_stages.keys()):
                stage = expected_stages.get(name)
                observed = observed_stages.get(name)
                if stage != observed or (
                    tree(commit, stage["spec"]["path"]) != tree(expected, stage["spec"]["path"])
                    or flux_build(repo, observed) != rendered_pin(name)
                ):
                    raise ValueError(f"different pin at {commit}: {name} inputs changed")

        compare(artifact)

        def applied(stage, spec):
            name = stage["metadata"]["name"]
            status = stage.get("status", {})
            at_pin = False
            for field in ("lastAttemptedRevision", "lastAppliedRevision"):
                if value := status.get(field):
                    commit = revision(value)
                    if admitted(commit):
                        compare(commit)
                        if field == "lastAppliedRevision":
                            at_pin = True
            if at_pin:
                check_live_flux_spec(name, stage["spec"], spec)
            ready = at_pin and flux_ready(stage)
            print(f"Flux {name}: applied {status.get('lastAppliedRevision', '-')}, ready at pin {ready}")
            return ready

        inventory_ready = applied(inventory, pinned_inputs[2])
        live = {stage["metadata"]["name"]: stage for stage in stages["items"]}
        if inventory_ready and (missing := expected_stages.keys() - live.keys()):
            raise ValueError(f"ready Flux inventory is missing Studio stages: {', '.join(sorted(missing))}")
        ready = [applied(live[name], stage["spec"]) for name, stage in expected_stages.items() if name in live]
        extra = any(re.match(r"^studio(?:-|$)", name) and name not in expected_stages for name in live)
        return 0 if inventory_ready and all(ready) and not extra else 2


def render_cluster_inventory(repo):
    """Render the complete active Flux tree rooted at the cluster Kustomization."""
    bootstrap = repo / FLUX_ROOT
    cluster = resource(list(yaml.safe_load_all(bootstrap.read_text())), "Kustomization", "cluster")
    flux_env = {**os.environ, "GOMAXPROCS": "2"}
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

    with TemporaryDirectory(prefix="studio-flux-inventory-") as directory:
        temporary = Path(directory)
        manifest = temporary / "flux-stage.yaml"
        manifest.write_text(yaml.safe_dump(cluster))
        command = [
            "flux", "build", "kustomization", cluster["metadata"]["name"],
            "--path", cluster["spec"]["path"], "--kustomization-file", str(manifest),
            "--dry-run", "--in-memory-build",
        ]
        # The root also declares the Flux controllers' OCI source. Materialize
        # that source so its sibling Kustomization is built with its patches.
        roots = subprocess.check_output(command, cwd=repo, env=flux_env, text=True)
        root_documents = [doc for doc in yaml.load_all(roots, Loader=loader) if doc]
        oci_sources = {
            (doc["metadata"]["namespace"], doc["metadata"]["name"]): doc["spec"]
            for doc in root_documents if doc["kind"] == "OCIRepository"
        }
        local_sources = {"GitRepository/flux-system/haynes-ops": "."}
        for stage in root_documents:
            if (stage["kind"] != "Kustomization"
                    or not stage["apiVersion"].startswith("kustomize.toolkit.fluxcd.io/")):
                continue
            ref = stage["spec"]["sourceRef"]
            if ref["kind"] != "OCIRepository":
                continue
            namespace = ref.get("namespace", stage["metadata"]["namespace"])
            name = ref["name"]
            key = f"OCIRepository/{namespace}/{name}"
            if key in local_sources:
                continue
            source = oci_sources[(namespace, name)]
            pin = source["ref"]
            if digest := pin.get("digest"):
                artifact = f"{source['url']}@{digest}"
            else:
                artifact = f"{source['url']}:{pin['tag']}"
            destination = temporary / f"source-{len(local_sources)}"
            destination.mkdir()
            subprocess.run(["flux", "pull", "artifact", artifact, "--output", str(destination)],
                           check=True, capture_output=True, env=flux_env, text=True)
            local_sources[key] = str(destination)
        mappings = ",".join(f"{key}={path}" for key, path in local_sources.items())
        output = subprocess.check_output(
            [*command, "--recursive", "--local-sources", mappings], cwd=repo, env=flux_env, text=True,
        )
    return [doc for doc in yaml.load_all(output, Loader=loader) if doc]


def repository_cluster_policies(repo):
    """Also include clusterwide policy definitions outside the active app inventory."""
    for file in (repo / "kubernetes").rglob("*"):
        if file.suffix not in {".yaml", ".yml", ".json"} or not file.is_file():
            continue
        text = file.read_text()
        if CLUSTER_POLICY_KIND in text:
            yield from (doc for doc in yaml.safe_load_all(text)
                        if isinstance(doc, dict) and doc.get("kind") == CLUSTER_POLICY_KIND)


def container(workload, name=None):
    containers = workload["spec"]["template"]["spec"]["containers"]
    (selected,) = [item for item in containers if name is None or item["name"] == name]
    return selected


def image_digest(workload, repository, *, name=None):
    image = container(workload, name)["image"]
    match = re.fullmatch(rf"{re.escape(repository)}@(sha256:[0-9a-f]{{64}})", image)
    if not match:
        raise ValueError(f"{workload['metadata']['name']} must pin a {repository} digest")
    return match[1]


def check_jobs(documents, flux, prefixes, digest):
    names = sorted(document["metadata"]["name"] for document in documents
                   if document and document["kind"] == "Job")
    stems = sorted(f"{prefix}-{digest.removeprefix('sha256:')[:12]}" for prefix in prefixes)
    if len(names) != len(stems) or any(
        not re.fullmatch(rf"{stem}(?:-r[1-9][0-9]*)?", name) for name, stem in zip(names, stems)
    ):
        raise ValueError(f"{flux['metadata']['name']} Job names {names} must match release stems {stems}")
    checks = [check for check in flux["spec"]["healthChecks"] if check["kind"] == "Job"]
    expected = [{"apiVersion": "batch/v1", "kind": "Job", "name": name, "namespace": "ai"} for name in names]
    if len(checks) != len(expected) or any(check not in checks for check in expected):
        raise ValueError(f"{flux['metadata']['name']} must health-check exactly these Jobs in ai: {names}")
    return [resource(documents, "Job", name) for name in names]


def env(workload, name=None):
    return {entry["name"]: entry.get("value") for entry in container(workload, name)["env"]}


def check_drain(deployments):
    """Serving containers keep serving briefly while the ingress drops a terminating pod."""
    for deployment, names in deployments:
        pod = deployment["spec"]["template"]["spec"]
        grace = pod.get("terminationGracePeriodSeconds", 30)
        for name in names:
            spec = next(item for item in pod["containers"] if item["name"] == name)
            seconds = spec.get("lifecycle", {}).get("preStop", {}).get("sleep", {}).get("seconds")
            if type(seconds) is not int or not 5 <= seconds <= grace - 10:
                raise ValueError(f"{deployment['metadata']['name']}/{name} needs a preStop sleep of at least "
                                 "5 seconds that leaves 10 seconds of the termination grace period")


def check_packaged_register(app, deployments):
    for document in app:
        if document and document["kind"] == "ConfigMap" and document["metadata"]["name"].startswith(
            "studio-model-licences-"
        ):
            raise ValueError("Studio ships the licence register in the image")
    for deployment, container_name in deployments:
        volumes = deployment["spec"]["template"]["spec"]["volumes"]
        if "STUDIO_MODEL_REGISTER" in env(deployment, container_name) or any(
            volume.get("configMap", {}).get("name", "").startswith("studio-model-licences-")
            for volume in volumes
        ):
            raise ValueError(f"{deployment['metadata']['name']} must use the packaged licence register")


def published_config(image, digest):
    """Read the digest-verified OCI config from the public Studio registry."""
    repository = image.removeprefix("ghcr.io/")
    base = f"https://ghcr.io/v2/{repository}"

    def fetch(endpoint, expected, accept="application/json"):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected):
            raise ValueError("invalid Studio OCI digest")
        request = Request(f"{base}/{endpoint}/{expected}", headers={"Accept": accept})
        request.add_unredirected_header("Authorization", "Bearer " + token)
        with urlopen(request, timeout=15) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024 or "sha256:" + sha256(data).hexdigest() != expected:
            raise ValueError("Studio image manifest or config SHA-256 differs from its pin")
        return json.loads(data)

    with urlopen(f"https://ghcr.io/token?service=ghcr.io&scope=repository:{repository}:pull", timeout=15) as response:
        token = json.loads(response.read(65536))["token"]
    manifest = fetch("manifests", digest, "application/vnd.docker.distribution.manifest.v2+json, "
                                         "application/vnd.oci.image.manifest.v1+json")
    return fetch("blobs", manifest["config"]["digest"])


def published_matting_contract(digest):
    try:
        config = published_config(MATTING_IMAGE, digest)
        label = config["config"]["Labels"]["io.haynes-studio.matting.resources"]
        memory = json.loads(label)
        request, limit = memory["request_gib"], memory["limit_gib"]
        if (memory["schema"] != "studio.matting-resources/v1"
                or type(request) is not int or type(limit) is not int or not 0 < request <= limit
                or (config["os"], config["architecture"]) != ("linux", "amd64")
                or config["config"]["Labels"]["org.opencontainers.image.source"]
                != "https://github.com/thaynes43/haynes-studio"):
            raise ValueError("invalid matting profile resource contract")
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise ValueError("published matting image must carry the Studio profile resource contract") from error
    return (f"{request}Gi", f"{limit}Gi"), config["config"]["Labels"]["org.opencontainers.image.revision"]


# Cilium's default label filter leaves these out of a pod's security identity,
# so a NetworkPolicy peer that requires one of them never matches.
IDENTITY_EXCLUDED_LABELS = (
    "statefulset.kubernetes.io/pod-name", "apps.kubernetes.io/pod-index",
    "batch.kubernetes.io/job-completion-index", "controller-revision-hash",
    "pod-template-hash", "pod-template-generation",
)


def check_policy_selectors(documents, *, app_labels_only=False):
    def keys(selector):
        selector = selector or {}
        yield from (selector.get("matchLabels") or {})
        yield from (item["key"] for item in selector.get("matchExpressions") or [])

    for policy in documents:
        selectors = []
        if policy["kind"] == "NetworkPolicy":
            spec = policy["spec"]
            selectors.append(spec.get("podSelector"))
            for rule in [*(spec.get("ingress") or []), *(spec.get("egress") or [])]:
                for peer in [*(rule.get("to") or []), *(rule.get("from") or [])]:
                    selectors += [peer.get("podSelector"), peer.get("namespaceSelector")]
        elif policy["kind"] in {"CiliumNetworkPolicy", CLUSTER_POLICY_KIND}:
            for spec in [policy.get("spec"), *(policy.get("specs") or [])]:
                if spec:
                    selectors.append(spec.get("endpointSelector"))
                    for direction, peers in (("ingress", "fromEndpoints"), ("egress", "toEndpoints")):
                        for rule in [*(spec.get(direction) or []), *(spec.get(direction + "Deny") or [])]:
                            selectors.extend(rule.get(peers, []))
        used = {key.removeprefix("k8s:").removeprefix("any:") for selector in selectors for key in keys(selector)}
        if bad := sorted(used.intersection(IDENTITY_EXCLUDED_LABELS)):
            raise ValueError(f"{policy['metadata']['name']} selects by {bad[0]}, which Cilium leaves "
                             "out of pod identities; the rule would never match")
        if app_labels_only and any(
            key != "io.kubernetes.pod.namespace" and not key.startswith("app.kubernetes.io/") for key in used
        ):
            raise ValueError(f"{policy['metadata']['name']} must select pods by app.kubernetes.io labels")


def check_matting(path, flux):
    pending = path / "matting-pending"
    if not pending.exists():
        return
    pin = yaml.safe_load((pending / "release/release.yaml").read_text())["data"]
    image = pin["image"]
    allocation = pin["allocation"]
    match = re.fullmatch(rf"{re.escape(MATTING_IMAGE)}@(sha256:[0-9a-f]{{64}})", image)
    if not match or match[1] in STALE_MATTING_DIGESTS:
        raise ValueError("set matting-pending/release/release.yaml data.image to the published matting digest")
    memory, revision = published_matting_contract(match[1])
    if (pin["memoryRequest"], pin["memoryLimit"]) != memory:
        raise ValueError("matting release request and limit must match the published Studio profile")
    if not re.fullmatch(r"matting-op459-birefnet-lite-v1-[0-9]{2}", allocation):
        raise ValueError("invalid create-only matting allocation")

    if (pin["revision"] != revision or not re.fullmatch(r"[0-9a-f]{40}", revision)
            or not re.fullmatch(r"[0-9a-f]{64}", pin["profileSha256"])):
        raise ValueError("matting must use the published sidecar revision and provisioned profile hash")

    worker_stage = resource(flux, "Kustomization", MATTE_WORKER)
    stage_spec = worker_stage["spec"]
    if (stage_spec["path"] != f"./{STUDIO_PATH}/{MATTE_ACTIVATION}" or not stage_spec["wait"]
            or not stage_spec["prune"] or stage_spec["dependsOn"] != [
                {"name": "studio"}, {"name": "studio-render"}, {"name": MATTE_POLICY_STAGE},
                {"name": "studio-media-worker-registration"},
            ]):
        raise ValueError("matte deployment must wait for the API, offline render split, matte policy and token projection")
    matte_policy_stage = resource(flux, "Kustomization", MATTE_POLICY_STAGE)
    if matte_policy_stage["spec"]["path"] != f"./{STUDIO_PATH}/{MATTE_POLICY_PATH}":
        raise ValueError("the matte worker policy must have its own prerequisite path")
    policy_stage = resource(flux, "Kustomization", "studio-matting-policy")
    policy_path = f"./{STUDIO_PATH}/matting-pending/policy"
    if policy_stage["spec"]["path"] != policy_path or not policy_stage["spec"]["prune"]:
        raise ValueError("the Studio matting policy must use the policy path and prune")
    if any(
        "matting-pending" in stage["spec"]["path"] and stage not in (policy_stage, matte_policy_stage, worker_stage)
        for stage in flux if stage["kind"] == "Kustomization"
    ):
        raise ValueError("matting provisioning, smoke and quality trial must stay staged")

    policy = render(pending / "policy")
    if {doc["kind"] for doc in policy} != {"CiliumNetworkPolicy", "ConfigMap"}:
        raise ValueError("the applied matting stage may include only policies and provisioner code")
    download = resource(policy, "CiliumNetworkPolicy", "studio-matting-provision")["spec"]
    for name in ("studio-matting-smoke", "studio-matting-quality"):
        check_matting_deny_all(resource(policy, "CiliumNetworkPolicy", name)["spec"], name)
    allow = [{"matchName": "huggingface.co"}, {"matchName": "us.aws.cdn.hf.co"}]
    dns, https = download["egress"]
    if (dns != {
        "toEndpoints": [{"matchLabels": {
            "k8s:io.kubernetes.pod.namespace": "kube-system", "k8s:k8s-app": "kube-dns",
        }}],
        "toPorts": [{"ports": [{"port": "53", "protocol": "ANY"}], "rules": {"dns": allow}}],
    } or https["toFQDNs"] != allow
            or https["toPorts"] != [{"ports": [{"port": "443", "protocol": "TCP"}]}]):
        raise ValueError("matting provisioning egress must be pinned DNS/HTTPS")
    if (download["endpointSelector"]["matchLabels"] != {"app.kubernetes.io/name": "studio-matting-provision"}
            or download["ingressDeny"] != [{"fromEntities": ["all"]}]):
        raise ValueError("wrong network selector or ingress rule for studio-matting-provision")

    jobs = {}
    weight_path = f"data/studio/jobs/{allocation}/weights"
    name_suffix = allocation.rsplit("-", 1)[1]
    for name, uid in (("provision", 1000), ("smoke", 10001)):
        stage = render(pending / name)
        job = resource(stage, "Job", f"studio-matting-{name}-{name_suffix}")
        pod = job["spec"]["template"]["spec"]
        if (job["spec"]["activeDeadlineSeconds"] != 300 or job["spec"]["backoffLimit"] != 0
                or job["spec"]["ttlSecondsAfterFinished"] != 86400
                or pod["nodeSelector"] != {"kubernetes.io/hostname": "talosw01"}
                or pod["priorityClassName"] != "dev-env-agent"
                or pod["restartPolicy"] != "Never" or pod["automountServiceAccountToken"]
                or pod.get("initContainers") or pod.get("hostNetwork")
                or job["spec"]["template"]["metadata"]["labels"] != {
                    "app.kubernetes.io/name": f"studio-matting-{name}",
                }
                or pod["securityContext"]["runAsUser"] != uid
                or pod["securityContext"]["runAsGroup"] != uid
                or not pod["securityContext"]["runAsNonRoot"]
                or pod["securityContext"]["seccompProfile"] != {"type": "RuntimeDefault"}):
            raise ValueError(f"unsafe matting {name} Job envelope")
        check_matting_container(container(job), image)
        jobs[name] = (job, pod)

    provision, pod = jobs["provision"]
    provision_mounts = container(provision)["volumeMounts"]
    if (env(provision)["STUDIO_MATTING_ALLOCATION"] != allocation
            or container(provision)["command"] != ["python", "-B", "/config/provision.py"]
            or provision_mounts != [
                {"name": "media", "mountPath": "/jobs", "subPath": "data/studio/jobs"},
                {"name": "config", "mountPath": "/config", "readOnly": True},
            ]
            or len(pod["volumes"]) != 2 or "nfs" not in pod["volumes"][0]
            or pod["volumes"][1]["configMap"]["name"] != resource(policy, "ConfigMap")["metadata"]["name"]):
        raise ValueError("the provisioner may write only inside the Studio jobs allocation")

    smoke, pod = jobs["smoke"]
    if (container(smoke)["command"] != ["studio-matting", "smoke"]
            or pod["securityContext"]["fsGroup"] != 10001):
        raise ValueError("the smoke Job must run the serving defaults with writable private scratch")
    check_matting_mounts(container(smoke), pod, weight_path, "scratch", memory)

    quality = render(pending / "quality")
    check_matting_quality(quality, pending / "quality/trial.py", smoke, image, weight_path, memory)

    return pin, memory


def check_matte_token(stage, pod, worker, sidecar):
    token_name = f"{MATTE_WORKER}-token"
    if ([item for item in worker["volumeMounts"] if item["name"] == "worker-token"] != [
            {"name": "worker-token", "mountPath": MATTE_TOKEN_MOUNT, "readOnly": True},
        ] or [volume for volume in pod["volumes"] if volume.get("secret") or volume.get("projected")] != [{
            "name": "worker-token",
            "secret": {"secretName": token_name, "defaultMode": 0o440,
                       "items": [{"key": "STUDIO_WORKER_TOKEN", "path": "token"}]},
        }]
            or any(item["name"] == "STUDIO_WORKER_TOKEN" or item.get("valueFrom", {}).get("secretKeyRef")
                   for item in worker["env"])
            or worker.get("envFrom")
            or any(item["name"] == "worker-token" for item in sidecar["volumeMounts"])
            or {item["name"] for item in sidecar["env"]} != {
                "STUDIO_MATTING_IMAGE", "STUDIO_MATTING_PROFILE_SHA256",
            }
            or pod.get("automountServiceAccountToken") is not False
            or pod.get("serviceAccountName") != MATTE_WORKER
            or resource(stage, "ServiceAccount", MATTE_WORKER).get("automountServiceAccountToken") is not False
            or any(pod.get(key) for key in ("shareProcessNamespace", "hostPID", "initContainers", "ephemeralContainers"))):
        raise ValueError("matte token must be mounted only in the worker with separate PID and no service-account token")
    pod_identity = pod["securityContext"]
    worker_identity = worker["securityContext"]
    for field in ("runAsUser", "runAsGroup"):
        if pod_identity.get(field) != 1000 or worker_identity.get(field, 1000) != 1000:
            raise ValueError("matte worker must run as UID and GID 1000")


def check_matte_readiness(sidecar, profile):
    command = sidecar["readinessProbe"]["exec"]["command"]
    message = "matting readiness must check the exact profile hash on the shared Unix socket"
    if (len(command) != 4 or command[:3] != ["python", "-B", "-c"]
            or next((item.get("value") for item in sidecar["env"]
                     if item["name"] == "STUDIO_MATTING_PROFILE_SHA256"), None) != profile):
        raise ValueError(message)
    # Evaluate only the final Boolean expression. Require the reviewed imports,
    # socket call and permitted comparison terms before evaluating anything.
    statements = ast.parse(command[3]).body
    setup = [
        "import os",
        "from pathlib import Path",
        "from haynes_studio.matting.service import local_call",
        "code, ready = local_call(Path('/matting/matte.sock'), 'GET', '/v1/ready', timeout=2)",
    ]
    if len(statements) != 5 or [ast.unparse(item) for item in statements[:4]] != setup:
        raise ValueError(message)
    ending = statements[-1]
    if (not isinstance(ending, ast.Raise) or not isinstance(ending.exc, ast.Call)
            or ast.unparse(ending.exc.func) != "SystemExit" or len(ending.exc.args) != 1
            or ending.exc.keywords or ending.cause):
        raise ValueError(message)
    convert = ending.exc.args[0]
    if (not isinstance(convert, ast.Call) or ast.unparse(convert.func) != "int"
            or len(convert.args) != 1 or convert.keywords):
        raise ValueError(message)
    expression = convert.args[0]
    if not isinstance(expression, ast.BoolOp) or {ast.unparse(term) for term in expression.values} != {
        "code != 200", "ready.get('profile_sha256') != os.environ['STUDIO_MATTING_PROFILE_SHA256']",
    }:
        raise ValueError(message)
    predicate = compile(ast.Expression(expression), "<matting readiness probe>", "eval")
    os_stub = SimpleNamespace(environ={"STUDIO_MATTING_PROFILE_SHA256": profile})
    for code, observed, refused in ((200, profile, False), (200, "wrong", True), (503, profile, True)):
        inputs = {"code": code, "ready": {"profile_sha256": observed}, "os": os_stub}
        if eval(predicate, {"__builtins__": {}}, inputs) != refused:
            raise ValueError(message)


def check_matte_worker(stage, api, run, pin, memory):
    worker = resource(stage, "Deployment", MATTE_WORKER)
    pod = worker["spec"]["template"]["spec"]
    if (len(run["spec"]["template"]["spec"]["containers"]) != 1
            or {item["name"] for item in pod["containers"]} != {"worker", "matting"}
            or len(pod["containers"]) != 2):
        raise ValueError("the matting sidecar belongs only on the matte worker pod")
    worker_spec = container(worker, "worker")
    sidecar = container(worker, "matting")
    settings = env(worker, "worker")
    check_matte_token(stage, pod, worker_spec, sidecar)
    if (worker_spec["command"] != MATTE_COMMAND or worker_spec["args"] != ["studio-worker", "--lane", "matte"]
            or settings["STUDIO_API_URL"] != "http://studio-api.ai.svc.cluster.local:8002"
            or settings["STUDIO_WORKER_PROGRESS_FILE"] != MATTE_PROGRESS
            or {key: settings[f"STUDIO_MATTING_{key}"] for key in ("IMAGE", "REVISION", "PROFILE_SHA256")}
            != {"IMAGE": pin["image"], "REVISION": pin["revision"], "PROFILE_SHA256": pin["profileSha256"]}):
        raise ValueError("matte worker must use its lane, worker listener and the admitted sidecar pins")
    probes = ("startupProbe", "livenessProbe")
    for probe in probes:
        if worker_spec[probe]["exec"]["command"] != [
            "studio-worker", "--progress-file", MATTE_PROGRESS, "--check-progress", "300",
        ]:
            raise ValueError("matte worker probes must check the 300-second progress marker")
    api_root = env(api, "api")["STUDIO_STORE_ROOT"]
    api_store = next(m for m in container(api, "api")["volumeMounts"] if m["mountPath"] == api_root)
    mounts = {item["name"]: item for item in worker_spec["volumeMounts"]}
    if (mounts["matte-scratch"] != {"name": "matte-scratch", "mountPath": "/matting"}
            or mounts["store"] != {"name": "store", "mountPath": api_root, "subPath": api_store["subPath"]}
            or settings["STUDIO_STORE_ROOT"] != api_root or "weights" in mounts
            or resource_volume(pod, "store")["nfs"] != resource_volume(
                api["spec"]["template"]["spec"], api_store["name"])["nfs"]
            or pod["securityContext"]["fsGroup"] != 10001
            or pod.get("hostNetwork")
            or pod["nodeSelector"] != {"kubernetes.io/hostname": "talosw01"}
            or resource_limits(worker_spec) != (1000, GIB)
            or worker["spec"]["replicas"] != 1 or worker["spec"]["strategy"]["type"] != "Recreate"):
        raise ValueError("matte worker must share only scratch with matting and write to the API store")
    check_matting_container(sidecar, pin["image"])
    check_matting_mounts(sidecar, pod, f"data/studio/jobs/{pin['allocation']}/weights", "matte-scratch", memory)
    if (sidecar["command"] != ["studio-matting", "serve"]
            or sidecar["securityContext"]["runAsUser"] != 10001
            or sidecar["securityContext"]["runAsGroup"] != 10001):
        raise ValueError("the matting sidecar must serve as 10001:10001")
    for probe in probes:
        if sidecar[probe]["exec"]["command"] != ["studio-matting", "ready"]:
            raise ValueError("matting health probes must use Unix HTTP readiness")
    check_matte_readiness(sidecar, pin["profileSha256"])
    return worker


def selector_matches(selector, labels):
    if any(labels.get(key) != value
           for key, value in selector.get("matchLabels", {}).items()):
        return False
    for expression in selector.get("matchExpressions", []):
        key, op = expression["key"], expression["operator"]
        values = expression.get("values", [])
        if not {
            "In": labels.get(key) in values, "NotIn": labels.get(key) not in values,
            "Exists": key in labels, "DoesNotExist": key not in labels,
        }.get(op, False):
            return False
    return True


def cilium_labels(pod, namespace_labels, cluster_name):
    labels = {key: value for key, value in pod["metadata"]["labels"].items()
              if key not in IDENTITY_EXCLUDED_LABELS}
    labels["io.kubernetes.pod.namespace"] = "ai"
    labels["io.cilium.k8s.policy.serviceaccount"] = pod["spec"].get("serviceAccountName", "default")
    labels["io.cilium.k8s.policy.cluster"] = cluster_name
    labels.update({f"io.cilium.k8s.namespace.labels.{key}": value for key, value in namespace_labels.items()})
    return {f"{source}{key}": value for key, value in labels.items() for source in ("", "k8s:", "any:")}


def selected_policies(documents, pod, endpoint_labels):
    pod_labels = pod["metadata"]["labels"]
    for document in documents:
        kind = document["kind"]
        if kind in NAMESPACE_POLICY_KINDS and document["metadata"].get("namespace") != "ai":
            continue
        if kind == "NetworkPolicy":
            spec = document["spec"]
            if selector_matches(spec["podSelector"], pod_labels):
                yield document, spec
        elif kind in {"CiliumNetworkPolicy", CLUSTER_POLICY_KIND}:
            for spec in [document.get("spec"), *(document.get("specs") or [])]:
                if spec and "endpointSelector" in spec and selector_matches(spec["endpointSelector"], endpoint_labels):
                    yield document, spec


def check_matte_policies(documents, api, matte, api_identity, matte_identity):
    def policy_rules(workload, identity, policy_name, direction):
        expected_selected = False
        rules = []
        for document, spec in selected_policies(documents, workload, identity):
            name = document["metadata"]["name"]
            if document["kind"] == "NetworkPolicy":
                # Kubernetes defaults both an omitted and an empty policyTypes list.
                types = spec.get("policyTypes") or ["Ingress", *(["Egress"] if spec.get("egress") else [])]
                if direction.capitalize() in types:
                    expected_selected = expected_selected or name == policy_name
                    rules.extend(spec.get(direction) or [])
            elif spec.get(direction) or spec.get(direction + "Deny"):
                # The reviewed allowlist is expressed entirely in NetworkPolicy.
                # A matching Cilium rule can add to it or override it with a deny.
                raise ValueError(f"unexpected Cilium policy {name} for {policy_name} {direction}")
        if not expected_selected:
            raise ValueError(f"{policy_name} {direction} selector must cover the rendered pod labels")
        return {json.dumps(rule, sort_keys=True) for rule in rules}

    def peer(**match):
        return {"podSelector": {"matchLabels": match}}

    def rule(direction, peers, ports):
        return {direction: peers, "ports": [{"port": port, "protocol": protocol} for port, protocol in ports]}

    def expect_rules(workload, identity, name, direction, expected, message):
        if policy_rules(workload, identity, name, direction) != {json.dumps(item, sort_keys=True) for item in expected}:
            raise ValueError(message)

    app_label = "app.kubernetes.io/name"
    media_names = ["studio-voice-worker", "studio-image-worker", MATTE_WORKER]
    dns = {**peer(**{"k8s-app": "kube-dns"}),
           "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}
    listener = peer(**{app_label: "studio-api"})
    expect_rules(matte, matte_identity, MATTE_WORKER, "egress", [
        rule("to", [dns], [(53, "UDP"), (53, "TCP")]),
        rule("to", [listener], [(8002, "TCP")]),
    ], "matte worker effective egress must be DNS and the Studio port-8002 listener only")
    expect_rules(matte, matte_identity, MATTE_WORKER, "ingress", [], "matte worker must refuse pod ingress")
    traefik = {**peer(**{"app.kubernetes.io/instance": "traefik-internal-network"}),
               "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "network"}}}
    media = {"podSelector": {"matchExpressions": [{"key": app_label, "operator": "In", "values": media_names}]}}
    expect_rules(api, api_identity, "studio-api-ingress", "ingress", [
        rule("from", [traefik, peer(**{app_label: "studio-mcp"}), peer(**{app_label: "studio-worker"})], [(8000, "TCP")]),
        rule("from", [media], [(8002, "TCP")]),
    ], "Studio API effective ingress must admit only its reviewed callers and the three media worker labels")


def check_matte_network(documents, api, matte, voice, image, run, mcp, cluster_name):
    app_label = "app.kubernetes.io/name"
    workload_names = ("studio-api", "studio-voice-worker", "studio-image-worker", MATTE_WORKER,
                      "studio-worker", "studio-mcp")
    for workload, name in zip((api, voice, image, matte, run, mcp), workload_names, strict=True):
        if workload["spec"]["template"]["metadata"]["labels"].get(app_label) != name:
            raise ValueError(f"{name} pod labels must match its rendered NetworkPolicy selectors")
    namespace = resource(documents, "Namespace", "ai")
    policies = [doc for doc in documents if doc["kind"] == CLUSTER_POLICY_KIND or (
        doc["kind"] in NAMESPACE_POLICY_KINDS and doc["metadata"].get("namespace") == "ai"
    )]
    check_policy_selectors(policies)
    labels = {"kubernetes.io/metadata.name": "ai", **namespace["metadata"].get("labels", {})}
    api_pod, matte_pod = api["spec"]["template"], matte["spec"]["template"]
    check_matte_policies(policies, api_pod, matte_pod,
                         cilium_labels(api_pod, labels, cluster_name), cilium_labels(matte_pod, labels, cluster_name))


def check_effective_policies(path):
    repo = path.resolve().parents[len(STUDIO_PATH.parts) - 1]
    documents = render_cluster_inventory(repo) + list(repository_cluster_policies(repo))
    deployments = [
        doc for doc in documents
        if doc["kind"] == "Deployment" and doc["metadata"].get("namespace") == "ai"
    ]
    names = ("studio-api", MATTE_WORKER, "studio-voice-worker", "studio-image-worker",
             "studio-worker", "studio-mcp")
    workloads = [resource(deployments, "Deployment", name) for name in names]
    cilium = yaml.safe_load((repo / "kubernetes/main/apps/kube-system/cilium/app/helm-values.yaml").read_text())
    check_matte_network(documents, *workloads, cilium["cluster"]["name"])


def check_installed_policies(data, *, stage_repo=None):
    namespaced, clusterwide, cilium, *workloads = data
    if cilium["data"]["enable-policy"] not in {"default", "always"}:
        raise ValueError("installed Cilium does not enforce network policy")
    policies = [doc for doc in namespaced["items"] if doc["metadata"]["namespace"] == "ai"]
    policies.extend(clusterwide["items"])
    if stage_repo is not None:
        (namespace,) = workloads
        _, _, _, stages = studio_inventory(stage_repo)
        api_stage = flux_build(stage_repo, stages["studio"])
        matte_stage = flux_build(stage_repo, stages[MATTE_WORKER])
        policy_stage = flux_build(stage_repo, stages[MATTE_POLICY_STAGE])
        api = resource(api_stage, "Deployment", "studio-api")["spec"]["template"]
        matte = resource(matte_stage, "Deployment", MATTE_WORKER)["spec"]["template"]
        replacements = {
            doc["metadata"]["name"]: doc for doc in (
                resource(api_stage, "NetworkPolicy", "studio-api-ingress"),
                resource(policy_stage, "NetworkPolicy", MATTE_WORKER),
            )
        }
        policies = [doc for doc in policies if
                    doc["kind"] != "NetworkPolicy" or doc["metadata"]["name"] not in replacements]
        policies.extend(replacements.values())
        labels = namespace["metadata"]["labels"]
        cluster = cilium["data"]["cluster-name"]
        api_identity = cilium_labels(api, labels, cluster)
        matte_identity = cilium_labels(matte, labels, cluster)
        check_policy_selectors(policies)
        for name, pod, identity in (("studio-api", api, api_identity), (MATTE_WORKER, matte, matte_identity)):
            matches = sorted({f"{doc['kind']}/{doc['metadata']['name']}"
                              for doc, _ in selected_policies(policies, pod, identity)})
            print(f"Projected {name} matching policies: {', '.join(matches)}")
        check_matte_policies(policies, api, matte, api_identity, matte_identity)
        return
    endpoints, api, matte = workloads
    if len(api["items"]) != 2 or len(matte["items"]) != 1:
        raise ValueError("expected two installed API pods and one matte worker pod for the policy read")
    check_policy_selectors(policies)
    endpoint_by_name = {ep["metadata"]["name"]: ep for ep in endpoints["items"]}
    identities = {}
    for pod in api["items"] + matte["items"]:
        name = pod["metadata"]["name"]
        endpoint = endpoint_by_name.get(name, {})
        if not any(ref["kind"] == "Pod" and ref["uid"] == pod["metadata"]["uid"]
                   for ref in endpoint.get("metadata", {}).get("ownerReferences", [])):
            raise ValueError(f"missing installed Cilium endpoint for pod {name}")
        labels = endpoint["status"]["identity"]["labels"]
        if not labels:
            raise ValueError(f"missing installed Cilium identity labels for pod {name}")
        exact = {}
        for label in labels:
            key, _, value = label.partition("=")
            exact[key] = value
        generic = {key.split(":", 1)[1]: value for key, value in exact.items() if ":" in key}
        identity = {**generic, **{f"any:{key}": value for key, value in generic.items()}, **exact}
        identities[name] = identity
        matches = sorted({f"{doc['kind']}/{doc['metadata']['name']}"
                          for doc, _ in selected_policies(policies, pod, identity)})
        print(f"{name} matching policies: {', '.join(matches)}")
    matte_pod = matte["items"][0]
    matte_policy = resource(policies, "NetworkPolicy", MATTE_WORKER)
    created = datetime.fromisoformat(matte_policy["metadata"]["creationTimestamp"])
    started = datetime.fromisoformat(matte_pod["status"]["startTime"])
    if created >= started:
        raise ValueError(f"{MATTE_WORKER} NetworkPolicy creation must precede the matte pod start")
    print(f"{MATTE_WORKER} NetworkPolicy created {created.isoformat()}, pod started {started.isoformat()}")
    for pod in api["items"]:
        check_matte_policies(policies, pod, matte_pod, identities[pod["metadata"]["name"]],
                             identities[matte_pod["metadata"]["name"]])


def check_matting_deny_all(spec, name):
    if spec != {
        "endpointSelector": {"matchLabels": {"app.kubernetes.io/name": name}},
        "ingressDeny": [{"fromEntities": ["all"]}],
        "egressDeny": [{"toEntities": ["all"]}],
    }:
        raise ValueError(f"{name} needs a dedicated deny-all network policy")


def check_matting_container(spec, image):
    settings = {entry["name"]: entry.get("value") for entry in spec["env"]}
    security = spec["securityContext"]
    if (spec["image"] != image or settings["STUDIO_MATTING_IMAGE"] != image
            or any(entry.get("valueFrom") for entry in spec["env"])
            or spec.get("envFrom") or spec.get("ports")
            or spec["securityContext"].get("privileged")
            or security["allowPrivilegeEscalation"] or not security["readOnlyRootFilesystem"]
            or security["capabilities"] != {"drop": ["ALL"]}
            or any("gpu" in resource for resource in spec["resources"]["limits"])):
        raise ValueError("matting must use the selected image, no credentials, read-only root and CPU only")


def check_matting_mounts(spec, pod, subpath, scratch_name, memory):
    volumes = {item["name"]: item for item in pod["volumes"]}
    mounts = {item["name"]: item for item in spec["volumeMounts"]}
    request, limit = memory
    if (mounts != {
            scratch_name: {"name": scratch_name, "mountPath": "/matting"},
            "weights": {"name": "weights", "mountPath": "/weights", "subPath": subpath, "readOnly": True},
        } or volumes[scratch_name]["emptyDir"]["sizeLimit"] != "256Mi"
            or not volumes["weights"]["nfs"]["readOnly"]
            or spec["resources"]["limits"] != {"cpu": "2", "memory": limit}
            or spec["resources"]["requests"]["memory"] != request):
        raise ValueError(f"matting needs 2 CPUs, {request} requested and {limit} limit, a private emptyDir "
                         "and read-only pinned weights")


def check_matting_quality(documents, script, smoke, image, weight_path, memory):
    if len(documents) != 1:
        raise ValueError("matting quality must create exactly one Job and carry its script inline")
    job = resource(documents, "Job", "studio-matting-quality-template")
    spec = job["spec"]
    expected_spec = {key: value for key, value in smoke["spec"].items() if key != "template"}
    expected_spec["ttlSecondsAfterFinished"] = 3600
    if (job["apiVersion"] != "batch/v1" or job["metadata"].get("namespace") != "ai"
            or {key: value for key, value in spec.items() if key != "template"} != expected_spec):
        raise ValueError("matting quality needs a bounded Job envelope with a one-hour TTL")
    pod = spec["template"]["spec"]
    smoke_pod = smoke["spec"]["template"]["spec"]
    fields = {"containers", "initContainers", "volumes"}
    expected_pod = {key: value for key, value in smoke_pod.items() if key not in fields}
    expected_pod["securityContext"] = {
        **smoke_pod["securityContext"], "supplementalGroups": [1000],
    }
    if (spec["template"]["metadata"] != {"labels": {"app.kubernetes.io/name": "studio-matting-quality"}}
            or {key: value for key, value in pod.items() if key not in fields} != expected_pod):
        raise ValueError("matting quality needs its own deny-all network label and UID 10001 with group 1000")

    sandbox = container(smoke)["securityContext"]
    if pod.get("initContainers") != [{
        "name": "reserve-output",
        "image": image,
        "command": ["python", "-B", "-c"],
        "args": [script.with_name("reserve.py").read_text(encoding="utf-8")],
        "resources": {"requests": {"cpu": "10m", "memory": "16Mi"},
                      "limits": {"cpu": "100m", "memory": "64Mi"}},
        "securityContext": {**sandbox, "runAsUser": 1000, "runAsGroup": 1000},
        "volumeMounts": [{"name": "jobs", "mountPath": "/jobs", "subPath": "data/studio/jobs"}],
    }]:
        raise ValueError("matting quality needs exactly one UID/GID 1000 init container for the output reservation")
    workload = container(job)
    check_matting_container(workload, image)
    if (set(workload) != {"name", "image", "command", "args", "env", "resources", "securityContext",
                         "volumeMounts"} or workload["name"] != "matting"
            or workload["securityContext"] != sandbox
            or workload["env"] != [{"name": "STUDIO_MATTING_IMAGE", "value": image}]
            or workload["command"] != ["python", "-B", "-c"]
            or workload["args"] != [script.read_text(encoding="utf-8")]
            or workload["resources"] != {
                "requests": {"cpu": "2", "memory": memory[0]},
                "limits": {"cpu": "2", "memory": memory[1]},
            }):
        raise ValueError("matting quality must run the reviewed script and pinned CPU/memory profile")

    scratch, weights = smoke_pod["volumes"]
    share = {"server": "gasha01.haynesnetwork", "path": "/hdd-nfs-repl"}
    if workload["volumeMounts"] != [
        {"name": "scratch", "mountPath": "/matting"},
        {"name": "weights", "mountPath": "/weights", "subPath": weight_path, "readOnly": True},
        {"name": "input", "mountPath": "/input", "readOnly": True,
         "subPath": "private-input-directory-replaced-by-runbook"},
        {"name": "jobs", "mountPath": "/output",
         "subPath": "data/studio/jobs/output-directory-replaced-by-runbook"},
    ] or weights["nfs"] != {**share, "readOnly": True} or pod["volumes"] != [
        scratch, weights, {"name": "input", "nfs": weights["nfs"]}, {"name": "jobs", "nfs": share},
    ]:
        raise ValueError("matting quality needs pinned read-only weights, one read-only input and only "
                         "one writable output subPath")


def segmentation_memory(memory):
    request, limit = memory["request_gib"], memory["limit_gib"]
    if type(request) is not int or type(limit) is not int or not 0 < request <= limit:
        raise ValueError("invalid segmentation image memory contract")
    return request, limit


def _check_segmentation_policy(policy):
    check_policy_selectors(policy, app_labels_only=True)
    if sorted(doc["kind"] for doc in policy) != ["CiliumNetworkPolicy", "CiliumNetworkPolicy", "ConfigMap"]:
        raise ValueError("the segmentation policy stage may include only two policies and the provisioner")
    download = resource(policy, "CiliumNetworkPolicy", "studio-segmentation-provision")["spec"]
    smoke = resource(policy, "CiliumNetworkPolicy", "studio-segmentation-smoke")["spec"]
    hosts = [{"matchName": "huggingface.co"}, {"matchName": "us.aws.cdn.hf.co"}]
    for name, spec, egress in (
        ("provision", download, {"egress": [
            {
                "toEndpoints": [{"matchLabels": {
                    "k8s:io.kubernetes.pod.namespace": "kube-system", "k8s:app.kubernetes.io/name": "coredns",
                }}],
                "toPorts": [{"ports": [{"port": "53", "protocol": "ANY"}], "rules": {"dns": hosts}}],
            },
            {"toFQDNs": hosts, "toPorts": [{"ports": [{"port": "443", "protocol": "TCP"}]}]},
        ]}),
        ("smoke", smoke, {"egressDeny": [{"toEntities": ["all"]}]}),
    ):
        if spec != {
            "endpointSelector": {"matchLabels": {"app.kubernetes.io/name": f"studio-segmentation-{name}"}},
            "ingressDeny": [{"fromEntities": ["all"]}], **egress,
        }:
            raise ValueError(f"segmentation {name} has an unexpected network rule")


def _check_segmentation_jobs(pending, policy, pin, image, allocation):
    nfs = {"server": "gasha01.haynesnetwork", "path": "/hdd-nfs-repl"}
    hardening = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}
    pod_security = {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}}
    weight_path = f"data/studio/jobs/{allocation}/weights"
    suffix = allocation.rsplit("-", 1)[1]
    settings = {"STUDIO_SEGMENTATION_IMAGE": image}
    for stage, deadline, security, arguments, resources, mounts, volumes, expected_env in (
        (
            "provision", 600, {**pod_security, "runAsUser": 1000, "runAsGroup": 1000},
            ["python", "-B", "/config/provision.py"],
            {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "500m", "memory": "1Gi"}},
            [
                {"name": "media", "mountPath": "/jobs", "subPath": "data/studio/jobs"},
                {"name": "config", "mountPath": "/config", "readOnly": True},
            ],
            [{"name": "media", "nfs": nfs}, {"name": "config", "configMap": {
                "name": resource(policy, "ConfigMap")["metadata"]["name"],
            }}],
            {**settings, "STUDIO_SEGMENTATION_ALLOCATION": allocation},
        ),
        (
            "smoke", 900, {**pod_security, "runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001},
            ["studio-segmentation", "smoke"],
            {"requests": {"cpu": "2", "memory": pin["servingRequest"]},
             "limits": {"cpu": "2", "memory": pin["servingLimit"]}},
            [
                {"name": "scratch", "mountPath": "/segmentation"},
                {"name": "weights", "mountPath": "/opt/studio-models", "subPath": weight_path, "readOnly": True},
            ],
            [{"name": "scratch", "emptyDir": {"sizeLimit": "256Mi"}},
             {"name": "weights", "nfs": {**nfs, "readOnly": True}}],
            settings,
        ),
    ):
        job = resource(render(pending / stage), "Job", f"studio-segmentation-{stage}-{suffix}")
        spec = job["spec"]
        pod = spec["template"]["spec"]
        container_spec = container(job)
        if (job["metadata"]["namespace"] != "ai" or spec["activeDeadlineSeconds"] != deadline
                or spec["backoffLimit"] != 0 or spec["ttlSecondsAfterFinished"] != 86400
                or spec["template"]["metadata"]["labels"] != {"app.kubernetes.io/name": f"studio-segmentation-{stage}"}
                or pod != {
                    "automountServiceAccountToken": False, "enableServiceLinks": False, "restartPolicy": "Never",
                    "nodeSelector": {"kubernetes.io/hostname": "talosw01"}, "priorityClassName": "dev-env-agent",
                    "securityContext": security, "containers": [container_spec], "volumes": volumes,
                }
                or container_spec != {
                    "name": "segmentation", "command": arguments, "image": image, "resources": resources,
                    "securityContext": hardening, "volumeMounts": mounts,
                    "env": [{"name": name, "value": value} for name, value in expected_env.items()],
                }):
            raise ValueError(f"unsafe segmentation {stage} Job envelope")


def _check_render_overlays(path, pending):
    apps_path = path.parent.parent.resolve()
    render_path = path.with_name("studio-render").resolve()
    activation = (pending / "activation").resolve()
    approved = {render_path / "app", render_path / "matting-pending", activation, activation / "render"}
    # Track both render replacements and any Kustomization that brings one in as a base.
    owners = set()
    references = {}
    for candidate in apps_path.rglob("*"):
        if candidate.name not in {"kustomization.yaml", "kustomization.yml", "Kustomization"}:
            continue
        config = yaml.safe_load(candidate.read_text())
        directory = candidate.parent.resolve()
        dependencies = {
            (directory / item).resolve()
            for key in ("resources", "bases", "components") for item in config.get(key, [])
        }
        references[directory] = dependencies
        if directory.is_relative_to(render_path):
            owners.add(directory)
            continue
        for dependency in dependencies:
            if dependency.is_file():
                source = dependency.read_text()
                if "HelmRelease" in source and "studio-render" in source and any(
                    isinstance(document, dict) and document.get("kind") == "HelmRelease"
                    and document.get("metadata", {}).get("name") == "studio-render"
                    for document in yaml.safe_load_all(source)
                ):
                    owners.add(directory)
    while extra := {directory for directory, deps in references.items() if deps & owners} - owners:
        owners.update(extra)
    if unknown := owners - approved:
        names = ", ".join(str(directory.relative_to(apps_path)) for directory in sorted(unknown))
        raise ValueError(f"unknown studio-render overlay outside the composed set: {names}")
    if (render_path / "matting-pending" not in references.get(activation / "render", set())
            or activation / "render" not in references.get(activation, set())):
        raise ValueError("segmentation activation must build on the matting render overlay")
    live = yaml.safe_load((render_path / "ks.yaml").read_text())
    if (live["spec"]["path"] != f"./{STUDIO_PATH.with_name('studio-render')}/matting-pending"
            or live["spec"].get("wait") is not True):
        raise ValueError("the live studio-render Flux stage must wait on the matting-only overlay")


def _check_render_budget(name, containers, memory_gib):
    cpu, memory = map(sum, zip(*(resource_limits(spec) for spec in containers), strict=True))
    if cpu > 24000 or memory > memory_gib * GIB:
        raise ValueError(f"{name} exceeds the talosw01 budget of 24 CPU or {memory_gib} GiB")


def _check_segmentation_activation(path, pending, pin, image, digest, allocation):
    _check_render_overlays(path, pending)
    render_path = path.with_name("studio-render")
    base = resource(render(render_path / "app"), "HelmRelease", "studio-render")["spec"]["values"]
    matting = resource(render(render_path / "matting-pending"), "HelmRelease", "studio-render")["spec"]["values"]
    values = resource(render(pending / "activation"), "HelmRelease", "studio-render")["spec"]["values"]
    controller = values["controllers"]["studio-render"]
    matting_containers = matting["controllers"]["studio-render"]["containers"]
    stage = render(path / "matting-pending/activation")
    workers = resource(stage, "Deployment", MATTE_WORKER)["spec"]["template"]["spec"]["containers"]
    run = resource(render(path / "worker"), "Deployment", "studio-worker")
    run_spec = container(run, "worker")
    if {spec["name"] for spec in workers} != {"worker", "matting"} or len(workers) != 2:
        raise ValueError("segmentation requires the matte worker and its matting companion")
    base_app = base["controllers"]["studio-render"]["containers"]["app"]
    if resource_limits(run_spec) != (4000, 8 * GIB):
        raise ValueError("run worker must keep its separate 4-CPU and 8-GiB allocation")
    _check_render_budget("base without matte", [base_app, run_spec], 40)
    _check_render_budget("matting alone", [*matting_containers.values(), run_spec, *workers], 51)
    _check_render_budget("matting with segmentation", [*controller["containers"].values(), run_spec, *workers], 56)
    matting_app = matting_containers["app"]
    render_app = controller["containers"]["app"]
    if (resource_limits(base_app) != (17000, 32 * GIB)
            or resource_limits(matting_app) != (17000, 32 * GIB)
            or resource_limits(render_app) != (15000, 32 * GIB)):
        raise ValueError("offline render must keep 32 GiB with 17 CPUs on either live path and 15 with segmentation")
    if controller["pod"].pop("terminationGracePeriodSeconds", None) != 150:
        raise ValueError("segmentation activation needs a 150 s pod grace period for drain and recovery")
    sidecar = controller["containers"].pop("segmentation")
    matting_app["resources"]["limits"] = base_app["resources"]["limits"]
    nfs = values["persistence"].pop("segmentation-weights")
    scratch = values["persistence"].pop("segmentation-scratch")
    mount = {"path": "/segmentation"}
    if nfs != {
        "type": "nfs", "server": "gasha01.haynesnetwork", "path": "/hdd-nfs-repl",
        "advancedMounts": {"studio-render": {"segmentation": [{
            "path": "/opt/studio-models", "subPath": f"data/studio/jobs/{allocation}/weights", "readOnly": True,
        }]}},
    } or scratch != {
        "type": "emptyDir", "sizeLimit": "256Mi",
        "advancedMounts": {"studio-render": {"app": [mount], "segmentation": [mount]}},
    }:
        raise ValueError("segmentation activation needs read-only weights and a dedicated shared scratch")
    for name in ("studio", "config", "tmp"):
        volume = values["persistence"][name]
        volume["globalMounts"] = volume.pop("advancedMounts")["studio-render"]["app"]
    render_app["resources"]["limits"] = matting_app["resources"]["limits"]
    if values != matting or matting != base:
        raise ValueError("both render activation stages must preserve the base render workload")
    if values["defaultPodOptions"]["securityContext"]["fsGroup"] != 1000:
        raise ValueError("segmentation activation needs fsGroup 1000 for shared scratch and socket access")
    if (sidecar["image"] != {"repository": SEGMENTATION_IMAGE, "tag": f"pinned@{digest}"}
            or sidecar["env"] != {"STUDIO_SEGMENTATION_IMAGE": image}
            or sidecar["command"] != ["studio-segmentation", "serve"]
            or sidecar.get("ports")
            or sidecar["securityContext"] != {
                "runAsUser": 10001, "runAsGroup": 10001, "runAsNonRoot": True, "allowPrivilegeEscalation": False,
                "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]},
            }
            or sidecar["resources"]["requests"] != {"cpu": "250m", "memory": pin["servingRequest"]}
            or resource_limits(sidecar) != (2000, quantity_units(pin["servingLimit"], 1, "memory"))):
        raise ValueError("unsafe segmentation serving container")
    for probe in ("startup", "readiness", "liveness"):
        config = sidecar["probes"][probe]
        if not (config["enabled"] and config["custom"]
                and config["spec"]["exec"] == {"command": ["studio-segmentation", "ready"]}):
            raise ValueError(f"segmentation {probe} must use Unix HTTP readiness")


def check_segmentation_stage(path, flux):
    pending = path / "segmentation-pending"
    pin = yaml.safe_load((pending / "release/release.yaml").read_text())["data"]
    image = pin["image"]
    match = re.fullmatch(rf"{re.escape(SEGMENTATION_IMAGE)}@(sha256:[a-f0-9]{{64}})", image)
    if not match or not re.fullmatch(r"segmentation-op460-dino-sam-v1-[0-9]{2}", pin["allocation"]):
        raise ValueError("invalid segmentation release image or create-only allocation")
    if (pin["servingRequest"], pin["servingLimit"], pin["renderMemoryLimit"]) != ("4Gi", "5Gi", "32Gi"):
        raise ValueError("segmentation serving request, limit and render limit must be 4 GiB, 5 GiB and 32 GiB")
    digest = match[1]
    policy_stage = resource(flux, "Kustomization", "studio-segmentation-policy")
    if (policy_stage["spec"]["path"] != f"./{STUDIO_PATH}/segmentation-pending/policy"
            or not policy_stage["spec"]["prune"]
            or any("segmentation-pending" in stage["spec"]["path"] and stage is not policy_stage
                   for stage in flux if stage["kind"] == "Kustomization")):
        raise ValueError("Flux may apply only the segmentation policy stage")
    policy = render(pending / "policy")
    _check_segmentation_policy(policy)
    _check_segmentation_jobs(pending, policy, pin, image, pin["allocation"])
    _check_segmentation_activation(path, pending, pin, image, digest, pin["allocation"])
    return pin, digest


def check_segmentation(path, flux):
    if not (path / "segmentation-pending").exists():
        return
    pin, digest = check_segmentation_stage(path, flux)
    if digest in STALE_SEGMENTATION_DIGESTS:
        raise ValueError("set segmentation-pending/release/release.yaml data.image to the published segmentation digest")
    try:
        config = published_config(SEGMENTATION_IMAGE, digest)
        labels = config["config"]["Labels"]
        memory = json.loads(labels["io.haynes-studio.segmentation.resources"])
        if ((config["os"], config["architecture"]) != ("linux", "amd64")
                or labels["org.opencontainers.image.source"] != "https://github.com/thaynes43/haynes-studio"
                or memory["schema"] != "studio.segmentation-resources/v1"):
            raise ValueError("invalid segmentation image resource label")
        segmentation_memory(memory["measurement"])
        serving = memory["serving"]
        request, limit = segmentation_memory(serving)
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise ValueError("published segmentation image must carry the Studio profile resource contract") from error
    if config["config"].get("User") != "10001:10001":
        raise ValueError("published segmentation image must default to 10001:10001")
    if (pin["servingRequest"], pin["servingLimit"]) != (f"{request}Gi", f"{limit}Gi"):
        raise ValueError("segmentation serving resources must match the pinned image's measured contract")
    if serving.get("weight_load") != "mmap":
        raise ValueError("the published segmentation image must pin memory-mapped serving")
    if config["config"].get("Cmd") != ["studio-segmentation", "serve"]:
        raise ValueError("the published segmentation image must default to serving")


def published_labels(image, digest):
    config = published_config(image, digest)
    labels = config["config"]["Labels"]
    if ((config["os"], config["architecture"]) != ("linux", "amd64")
            or labels["org.opencontainers.image.source"] != STUDIO_REPO):
        raise ValueError("expected a Linux amd64 Studio image")
    return labels


def gibibytes(resources):
    request, limit = resources["request_gib"], resources["limit_gib"]
    if type(request) is not int or type(limit) is not int or not 0 < request <= limit:
        raise ValueError("invalid memory request or limit")
    return f"{request}Gi", f"{limit}Gi"


def check_upscaling_container(spec, image, command):
    settings = {entry["name"]: entry.get("value") for entry in spec["env"]}
    if (spec["image"] != image or settings.get("STUDIO_UPSCALING_IMAGE") != image
            or any(entry.get("valueFrom") for entry in spec["env"])
            or any(spec.get(key) for key in ("envFrom", "ports", "volumeDevices"))
            or spec["command"] != command
            or any("gpu" in name for name in spec["resources"]["limits"])):
        raise ValueError("upscaling must use the release image and named CPU command without credentials or ports")
    security = spec["securityContext"]
    if (security.get("privileged") or security["allowPrivilegeEscalation"]
            or not security["readOnlyRootFilesystem"] or security["capabilities"] != {"drop": ["ALL"]}):
        raise ValueError("upscaling containers must be unprivileged with read-only roots")


def check_upscaling_mounts(spec, pod, mounts, nfs_modes):
    actual_mounts = {item["name"]: item for item in spec["volumeMounts"]}
    volumes = {item["name"]: item for item in pod["volumes"]}
    if actual_mounts != mounts:
        raise ValueError("upscaling mount paths or access modes differ from the release")
    for name, read_only in nfs_modes.items():
        nfs = volumes[name]["nfs"]
        if (nfs["server"] != "gasha01.haynesnetwork" or nfs["path"] != "/hdd-nfs-repl"
                or nfs.get("readOnly", False) != read_only):
            raise ValueError("upscaling must use the Studio NFS export with the declared access mode")
    if "scratch" in mounts and volumes["scratch"].get("emptyDir") != {"sizeLimit": "256Mi"}:
        raise ValueError("upscaling scratch needs a 256Mi emptyDir")


def check_upscaling_jobs(stage, image, allocation, measuring, policies, store_sub_path):
    suffix = allocation.rsplit("-", 1)[1]
    weights_path = f"data/studio/jobs/{allocation}/weights"
    weights = {"name": "weights", "mountPath": "/weights", "subPath": weights_path, "readOnly": True}
    scratch = {"name": "scratch", "mountPath": "/upscaling"}
    jobs_mount = {"name": "jobs", "mountPath": "/jobs", "subPath": "data/studio/jobs"}
    provision_docs = render(stage / "provision")
    for role, uid, command, args, mounts, nfs in (
        ("provision", 1000, ["python", "-B", "/config/provision.py"], None,
         {"media": {**jobs_mount, "name": "media"}, "config": {"name": "config", "mountPath": "/config", "readOnly": True}},
         {"media": False}),
        ("smoke", 10001, ["studio-upscaling", "smoke"], ["--allocator", "compare"],
         {"scratch": scratch, "weights": weights}, {"weights": True}),
        ("compare", 10001, ["studio-upscaling", "compare"],
         ["--proxy", "proxy-locator-required", "--frame-index", "frame-index-required"],
         {"scratch": scratch, "weights": weights,
          "jobs": {"name": "jobs", "mountPath": "/output",
                   "subPath": "data/studio/jobs/output-directory-replaced-by-runbook"},
          "source": {"name": "source", "mountPath": "/store/source-shard",
                     "subPath": "private-input-directory-replaced-by-runbook", "readOnly": True},
          "master": {"name": "master", "mountPath": "/store/master-shard",
                     "subPath": "private-master-directory-replaced-by-runbook", "readOnly": True}},
         {"weights": True, "jobs": False, "source": True, "master": True}),
    ):
        name = f"studio-upscaling-{role}"
        comparing = role == "compare"
        documents = provision_docs if role == "provision" else render(stage / role)
        job = resource(documents, "Job", f"{name}-{suffix}")
        spec = job["spec"]
        pod = spec["template"]["spec"]
        label = {"app.kubernetes.io/name": name}
        if (job["metadata"]["namespace"] != "ai" or spec["activeDeadlineSeconds"] != 300
                or spec["backoffLimit"] != 0 or spec["ttlSecondsAfterFinished"] != (3600 if comparing else 86400)
                or spec["template"]["metadata"]["labels"] != label
                or pod["restartPolicy"] != "Never" or pod["automountServiceAccountToken"]
                or pod["enableServiceLinks"] or pod.get("serviceAccountName")
                or (not comparing and pod.get("initContainers"))
                or pod.get("hostNetwork") or pod.get("hostPID") or pod.get("hostIPC")
                or pod["nodeSelector"] != {"kubernetes.io/hostname": "talosw01"}
                or pod["priorityClassName"] != "dev-env-agent"):
            raise ValueError(f"unsafe {name} Job envelope")
        security = pod["securityContext"]
        if (not security["runAsNonRoot"] or (security["runAsUser"], security["runAsGroup"]) != (uid, uid)
                or security["seccompProfile"] != {"type": "RuntimeDefault"}):
            raise ValueError(f"unsafe {name} Job identity")
        c = container(job)
        check_upscaling_container(c, image, command)
        check_upscaling_mounts(c, pod, mounts, nfs)
        if comparing:
            if job["metadata"].get("annotations", {}).get("studio.haynesops.com/store-sub-path") != store_sub_path:
                raise ValueError("comparison Job store sub-path must match the upscaling release")
            sandbox = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                       "capabilities": {"drop": ["ALL"]}}
            if (security != {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
                             "fsGroup": 10001, "supplementalGroups": [1000],
                             "seccompProfile": {"type": "RuntimeDefault"}}
                    or c["securityContext"] != sandbox or len(pod["containers"]) != 1):
                raise ValueError("comparison must run only UID/GID 10001 with supplemental group 1000")
            if pod.get("initContainers") != [{
                "name": "reserve-output",
                "image": image,
                "command": ["python", "-B", "-c"],
                "args": [(stage / "compare/reserve.py").read_text(encoding="utf-8")],
                "resources": {"requests": {"cpu": "10m", "memory": "16Mi"},
                              "limits": {"cpu": "100m", "memory": "64Mi"}},
                "securityContext": {**sandbox, "runAsUser": 1000, "runAsGroup": 1000},
                "volumeMounts": [jobs_mount],
            }]:
                raise ValueError("comparison requires one UID/GID 1000 init container for output reservation")
            if (len(documents) != 1 or len(c["volumeMounts"]) != len(mounts)
                    or len(pod["volumes"]) != len(mounts)):
                raise ValueError("comparison requires one Job and only the declared volumes and mounts")
        if c.get("args") != args or {volume["name"] for volume in pod["volumes"]} != set(mounts):
            raise ValueError(f"unexpected {name} arguments or volumes")
        request, limit = measuring
        resources = c["resources"]
        if role == "provision":
            config_name = resource(policies, "ConfigMap")["metadata"]["name"]
            if (resources["limits"] != {"cpu": "500m", "memory": "1Gi"}
                    or env(job)["STUDIO_UPSCALING_ALLOCATION"] != allocation
                    or next(v for v in pod["volumes"] if v["name"] == "config")["configMap"]["name"] != config_name):
                raise ValueError("upscaling provisioning must use the Flux script and the 1Gi write bound")
        elif resources["limits"] != {"cpu": "2", "memory": limit} or resources["requests"]["memory"] != request:
            raise ValueError("upscaling Jobs must match the measuring memory and two-CPU ceiling")


def check_upscaling(path, flux, api):
    stage = path / "upscaling-pending"
    if not stage.exists():
        return
    pin = yaml.safe_load((stage / "release/release.yaml").read_text())["data"]
    image, allocation = pin["image"], pin["allocation"]
    store_sub_path = pin["storeSubPath"]
    if not re.fullmatch(r"upscaling-op461-fsrcnn-x2-v1-(?:0[2-9]|[1-9][0-9])", allocation):
        raise ValueError("invalid create-only upscaling weights allocation")
    store_root = env(api, "api").get("STUDIO_STORE_ROOT")
    store_mounts = [mount for mount in container(api, "api")["volumeMounts"] if mount["mountPath"] == store_root]
    if len(store_mounts) != 1 or store_mounts[0].get("subPath") != store_sub_path:
        raise ValueError("upscaling store sub-path must match the studio-api Deployment store mount")
    api_nfs = resource_volume(api["spec"]["template"]["spec"], store_mounts[0]["name"]).get("nfs", {})
    if (api_nfs.get("server"), api_nfs.get("path")) != ("gasha01.haynesnetwork", "/hdd-nfs-repl"):
        raise ValueError("upscaling and the studio-api store must use the same Studio NFS export")
    policy_stage = resource(flux, "Kustomization", "studio-upscaling-policy")
    if (policy_stage["spec"]["path"] != f"./{STUDIO_PATH}/upscaling-pending/policy"
            or not policy_stage["spec"]["prune"] or any(
                "upscaling-pending" in item["spec"]["path"] and item is not policy_stage
                for item in flux if item["kind"] == "Kustomization"
            )):
        raise ValueError("only the upscaling policy stage belongs in Flux")
    policies = render(stage / "policy")
    check_policy_selectors(policies)
    if sorted(doc["kind"] for doc in policies) != ["CiliumNetworkPolicy"] * 3 + ["ConfigMap"]:
        raise ValueError("upscaling policy stage must contain only targeted policies and the provisioner script")
    for role in ("provision", "smoke", "compare"):
        name = f"studio-upscaling-{role}"
        policy = resource(policies, "CiliumNetworkPolicy", name)["spec"]
        expected = {"endpointSelector": {"matchLabels": {"app.kubernetes.io/name": name}},
                    "ingressDeny": [{"fromEntities": ["all"]}]}
        if role == "provision":
            host = [{"matchName": "raw.githubusercontent.com"}]
            expected["egress"] = [
                {"toEndpoints": [{"matchLabels": {"k8s:io.kubernetes.pod.namespace": "kube-system",
                                                  "k8s:app.kubernetes.io/instance": "coredns"}}],
                 "toPorts": [{"ports": [{"port": "53", "protocol": "ANY"}], "rules": {"dns": host}}]},
                {"toFQDNs": host, "toPorts": [{"ports": [{"port": "443", "protocol": "TCP"}]}]},
            ]
        else:
            expected["egressDeny"] = [{"toEntities": ["all"]}]
        if policy != expected:
            raise ValueError(f"unexpected traffic or identity labels for {name}")

    measuring = pin["memoryRequest"], pin["memoryLimit"]
    check_upscaling_jobs(stage, image, allocation, measuring, policies, store_sub_path)

    match = re.fullmatch(rf"{re.escape(UPSCALING_IMAGE)}@(sha256:[0-9a-f]{{64}})", image)
    if not match or match[1] == "sha256:" + "0" * 64:
        raise ValueError("set upscaling-pending/release/release.yaml data.image to the published Studio main digest")
    if match[1] in STALE_UPSCALING_DIGESTS:
        raise ValueError("stale upscaling image digest: set upscaling-pending/release/release.yaml data.image "
                         "to the published CR-56 digest from Studio main")
    try:
        published = published_labels(UPSCALING_IMAGE, match[1])
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise ValueError(f"published upscaling image failed verification: {error}") from error
    if published.get(UPSCALING_COMPARISON_LABEL) != "stored-proxy-frame-v2":
        raise ValueError("published upscaling image must support the stored-proxy frame comparison and probe")
    try:
        memory = json.loads(published["io.haynes-studio.upscaling.resources"])
        if (memory["schema"] != "studio.upscaling-resources/v1"
                or type(memory["quality_peak_gib"]) is not int or memory["quality_peak_gib"] <= 0
                or not re.fullmatch(r"[a-f0-9]{40}", published["org.opencontainers.image.revision"])):
            raise ValueError("invalid upscaling profile resource contract")
        if measuring != gibibytes(memory["measuring"]):
            raise ValueError("upscaling release memory differs from the pinned image contract")
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise ValueError(f"published upscaling image resource contract failed verification: {error}") from error


def resource_volume(pod, name):
    return next(volume for volume in pod["volumes"] if volume["name"] == name)


def check_render_turnover(path, requirements):
    render_path = path.with_name("studio-render")
    base = render(render_path / "app")
    if render(render_path / "matting-pending") != base:
        raise ValueError("both live Flux paths must render the same offline workload")
    previous = resource(base, "ConfigMap", "studio-render-requirements")["data"]["requirements.txt"]
    if sha256(previous.encode()).hexdigest() != PREDECESSOR_RENDER_SHA256:
        raise ValueError("keep the byte-identical offline requirements for the predecessor pod")
    release = resource(base, "HelmRelease", "studio-render")
    values = release["spec"]["values"]
    controller = values["controllers"]["studio-render"]
    app = controller["containers"]["app"]
    config_name = values["persistence"]["config"]["name"]
    current = resource(base, "ConfigMap", config_name)["data"]
    if (config_name == "studio-render-requirements" or current["requirements.txt"] != requirements
            or current["render-init.py"] != (render_path / "app/render-init.py").read_text()
            or app["command"] != ["/usr/local/bin/python", "-B", "/config/render-init.py"]
            or controller["strategy"] != "Recreate"):
        raise ValueError("offline replacement must use its own config, forwarding init and Recreate strategy")


def check(path):
    release = yaml.safe_load((path / "release.yaml").read_text(encoding="utf-8"))
    flux = list(yaml.safe_load_all((path / "ks.yaml").read_text(encoding="utf-8")))
    matting_pin, matting_memory = check_matting(path, flux)
    app = render(path / "app")
    api = resource(app, "Deployment", "studio-api")
    mcp = resource(app, "Deployment", "studio-mcp")
    web = resource(app, "Deployment", "studio-web")
    worker_stage = render(path / "worker")
    worker = resource(worker_stage, "Deployment", "studio-worker")
    media = render(path / "media-workers")
    voice = resource(media, "Deployment", "studio-voice-worker")
    image = resource(media, "Deployment", "studio-image-worker")
    matte_stage = render(path / MATTE_ACTIVATION)
    matte = check_matte_worker(matte_stage, api, worker, matting_pin, matting_memory)
    check_effective_policies(path)
    digest, web_digest = release["apiDigest"], release["webDigest"]
    if (image_digest(api, API_IMAGE, name="api") != digest
            or image_digest(web, API_IMAGE + "-web") != web_digest):
        raise ValueError("studio-api and studio-web must use the published release digest pair")
    strategy = api["spec"]["strategy"]
    if strategy["type"] != "RollingUpdate" or strategy["rollingUpdate"]["maxUnavailable"] != 0:
        raise ValueError("studio-api must keep old pods available while the new pods become ready")
    matte_registration = resource(flux, "Kustomization", f"{MATTE_WORKER}-registration")
    if (matte_registration["spec"]["dependsOn"] != [{"name": "studio-migration"}, {"name": MATTE_WORKER}]
            or matte_registration["spec"]["path"] != f"./{STUDIO_PATH}/matte-worker-registration"):
        raise ValueError("matte registration must wait for the schema and exact sidecar readiness")
    matte_jobs = check_jobs(render(path / "matte-worker-registration"), matte_registration,
                            ("studio-register-matte-worker",), digest)
    register = container(matte_jobs[0])
    register_pod = matte_jobs[0]["spec"]["template"]["spec"]
    if (register["command"] != ["studio-api-admin"] or register["args"] != [
        "worker-token", "register", "--worker-id", MATTE_WORKER, "--lane", "matte",
        "--token-file", "/run/studio-credentials/token",
    ] or resource_volume(register_pod, "token")["secret"] != {
        "secretName": f"{MATTE_WORKER}-token", "defaultMode": 0o440,
        "items": [{"key": "STUDIO_WORKER_TOKEN", "path": "token"}],
    } or {"name": "token", "mountPath": "/run/studio-credentials", "readOnly": True} not in register["volumeMounts"]
            or register_pod["automountServiceAccountToken"] is not False
            or register_pod["serviceAccountName"] != "studio-media-worker-registration"):
        raise ValueError("matte registration must bind its projected token to the matte lane")
    credentials = render(path / "media-worker-registration")
    secret_name = f"{MATTE_WORKER}-token"
    password = resource(credentials, "Password", secret_name)["spec"]
    projection = resource(credentials, "ExternalSecret", secret_name)["spec"]
    if (password["length"] != 64 or password["symbols"] != 0 or password["secretKeys"] != ["STUDIO_WORKER_TOKEN"]
            or projection["refreshPolicy"] != "CreatedOnce"
            or projection["target"] != {"name": secret_name, "creationPolicy": "Orphan"}
            or projection["dataFrom"] != [{"sourceRef": {"generatorRef": {
                "apiVersion": "generators.external-secrets.io/v1alpha1", "kind": "Password", "name": secret_name,
            }}}]):
        raise ValueError("matte worker needs its own stable generated token")
    jobs = [
        *check_jobs(render(path / "migration"), resource(flux, "Kustomization", "studio-migration"),
                    ("studio-migrate",), digest),
        *check_jobs(render(path / "worker-registration"), resource(flux, "Kustomization", "studio-worker-registration"),
                    ("studio-register-worker",), digest),
        *check_jobs(render(path / "media-worker-registration"),
                    resource(flux, "Kustomization", "studio-media-worker-registration"),
                    ("studio-register-voice-worker", "studio-register-image-worker"), digest),
    ]
    for workload in (mcp, worker, voice, image, *jobs):
        if image_digest(workload, API_IMAGE) != digest:
            raise ValueError(f"{workload['metadata']['name']} must use the Studio release digest {digest}")
    if image_digest(matte, API_IMAGE, name="worker") != digest or image_digest(matte_jobs[0], API_IMAGE) != digest:
        raise ValueError("the matte worker and registration Job must use the Studio release digest")
    if image_digest(api, API_IMAGE + "-web", name="media-worker-routes") != web_digest:
        raise ValueError("The API media-worker listener must use the same nginx image as studio-web")
    for workload, args in ((worker, []), (voice, ["--lane", "voice"]), (image, ["--lane", "image"])):
        spec = container(workload)
        if spec["command"] != ["studio-worker"] or spec.get("args", []) != args:
            raise ValueError(f"{workload['metadata']['name']} must consume its assigned queue lane")
    previous_listener = resource(app, "ConfigMap", PREDECESSOR_LISTENER)
    if (previous_listener["metadata"].get("annotations", {}).get("kustomize.toolkit.fluxcd.io/prune") != "disabled"
            or sha256(previous_listener["data"]["nginx.conf"].encode()).hexdigest() != PREDECESSOR_LISTENER_SHA256):
        raise ValueError("keep the exact predecessor listener ConfigMap through verified turnover")
    routes = (path / "app/media-worker-routes.conf").read_text()
    locations = re.findall(r"(?m)^\s*location\s+([^\n{]+?)\s*\{", routes)
    allowlist = "~ ^/internal/v1/work/(" + "|".join(MEDIA_ROUTES) + ")$"
    if (locations != [allowlist, "/"]
            or "if ($request_method != POST) { return 403; }" not in routes
            or "proxy_pass http://127.0.0.1:8000$uri;" not in routes
            or not re.search(r"location / \{\s*return 403;\s*\}", routes)):
        raise ValueError("port 8002 must allow exactly the six media POST routes including release-matte")
    for header in ("X-Forwarded-For", "X-Forwarded-Proto", "X-Forwarded-Host", "X-Real-IP", "Forwarded"):
        if f'proxy_set_header {header} "";' not in routes:
            raise ValueError(f"The API media-worker listener must clear {header} before the loopback API")

    api_pod = api["spec"]["template"]["spec"]
    listener_config = resource_volume(api_pod, "media-worker-routes")["configMap"]["name"]
    listener = container(api, "media-worker-routes")
    if (resource(app, "ConfigMap", listener_config)["data"]["nginx.conf"] != routes
            or {"name": "media-worker-routes", "mountPath": "/etc/studio-worker-routes", "readOnly": True}
            not in listener["volumeMounts"]
            or listener["args"] != ["-c", "/etc/studio-worker-routes/nginx.conf", "-g", "daemon off;"]):
        raise ValueError("the API must mount the new rendered listener allowlist")
    check_listener_auth(studio_source(release["revision"], "api/run_routes.py"),
                        studio_source(release["revision"], "api/auth.py"))

    api_env = env(api, "api")
    worker_env = env(worker)
    revision = f"git:{release['revision']}"
    if not re.fullmatch(r"git:[0-9a-f]{40}", revision) or any(
        settings.get("STUDIO_BUILD_REVISION") != revision for settings in (api_env, worker_env)
    ):
        raise ValueError("studio-api and studio-worker must specify the full published git revision")
    if worker_env.get("STUDIO_BUILD_IMAGE_DIGEST") != digest:
        raise ValueError("studio-worker provenance digest must match its image")

    offline_lock = (path.with_name("studio-render") / "app/requirements.txt").read_text()
    requirements = offline_lock.splitlines()
    if ([line for line in requirements if line.startswith("av==")] != ["av==18.1.1"]
            or f"# {release['revision']} (Python 3.12, linux x86_64)." not in requirements):
        raise ValueError("offline render must export PyAV 18.1.1 from the same Studio release lock")
    check_render_turnover(path, offline_lock)
    check_packaged_register(app + media + matte_stage,
                            ((api, "api"), (worker, None), (voice, None), (image, None), (matte, "worker")))
    check_drain(((api, ("api", "media-worker-routes")), (mcp, ("mcp",)), (web, ("nginx",)),
                 (matte, ("worker", "matting"))))
    for repository, expected in ((API_IMAGE, digest), (API_IMAGE + "-web", web_digest)):
        if studio_image_digest(repository, release["revision"]) != expected:
            raise ValueError(f"{repository} digest must match the published Studio revision")
    check_segmentation(path, flux)
    check_upscaling(path, flux, api)
    check_release_migration(release["revision"])
    print(f"Studio release pins and Job health gates agree for {revision} ({digest})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("studio_path", type=Path, nargs="?", default=STUDIO_PATH)
    parser.add_argument("--pin", nargs=2, metavar=("STUDIO_REVISION", "PUBLISH_RUN"),
                        help="verify the published image pair and update all release references")
    parser.add_argument("--effective-policies", action="store_true",
                        help="verify Studio policy from the complete active Flux inventory")
    parser.add_argument("--installed-policies", choices=("preflight", "postflight"),
                        help="audit installed policy JSON on stdin against the projected or real matte pod")
    parser.add_argument("--audit-flux", nargs=2, metavar=("MODE", "OPS_REVISION"),
                        help="compare applied Flux inputs against the selected merge")
    args = parser.parse_args()
    try:
        if args.effective_policies:
            check_effective_policies(args.studio_path)
            print("Studio effective network policies match the reviewed rules")
            return
        if args.installed_policies:
            repo = args.studio_path.resolve().parents[len(STUDIO_PATH.parts) - 1]
            check_installed_policies(json.load(sys.stdin),
                                     stage_repo=repo if args.installed_policies == "preflight" else None)
            return
        if args.audit_flux:
            mode, revision = args.audit_flux
            if mode not in {"release", "rollback"} or not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise ValueError("Flux comparison requires a release or rollback and its full merged revision")
            raise SystemExit(audit_flux(revision, mode, json.load(sys.stdin)))
        if args.pin:
            repin(args.studio_path, *args.pin)
        check(args.studio_path)
    except (KeyError, OSError, SyntaxError, ValueError, subprocess.CalledProcessError, yaml.YAMLError) as error:
        parser.exit(1, f"Studio release check failed: {error}\n")


if __name__ == "__main__":
    main()
