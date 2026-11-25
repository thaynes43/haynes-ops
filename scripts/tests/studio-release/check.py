"""Verify the rendered Studio image pins and their Flux Job gates."""

import argparse
import re
import subprocess
from pathlib import Path

import yaml


STUDIO_PATH = Path("kubernetes/main/apps/ai/studio")
API_IMAGE = "ghcr.io/thaynes43/haynes-studio"


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


def container(workload):
    (container,) = workload["spec"]["template"]["spec"]["containers"]
    return container


def image_digest(workload, repository):
    image = container(workload)["image"]
    match = re.fullmatch(rf"{re.escape(repository)}@(sha256:[0-9a-f]{{64}})", image)
    if not match:
        raise ValueError(f"{workload['metadata']['name']} must pin a {repository} digest")
    return match[1]


def check_job(job, flux, prefix, digest):
    name = job["metadata"]["name"]
    stem = f"{prefix}-{digest.removeprefix('sha256:')[:12]}"
    if not re.fullmatch(rf"{stem}(?:-r[1-9][0-9]*)?", name):
        raise ValueError(f"{name} must be named for the release digest ({stem})")
    checks = [check for check in flux["spec"]["healthChecks"] if check["kind"] == "Job"]
    if checks != [{"apiVersion": "batch/v1", "kind": "Job", "name": name, "namespace": "ai"}]:
        raise ValueError(f"{flux['metadata']['name']} must check Job ai/{name}")


def env(workload):
    return {entry["name"]: entry.get("value") for entry in container(workload)["env"]}


def check_packaged_register(app, deployments):
    for document in app:
        if document and document["kind"] == "ConfigMap" and document["metadata"]["name"].startswith(
            "studio-model-licences-"
        ):
            raise ValueError("Studio ships the licence register in the image")
    for deployment in deployments:
        volumes = deployment["spec"]["template"]["spec"]["volumes"]
        if "STUDIO_MODEL_REGISTER" in env(deployment) or any(
            volume.get("configMap", {}).get("name", "").startswith("studio-model-licences-")
            for volume in volumes
        ):
            raise ValueError(f"{deployment['metadata']['name']} must use the packaged licence register")


def check(path):
    release = yaml.safe_load((path / "release.yaml").read_text(encoding="utf-8"))
    flux = list(yaml.safe_load_all((path / "ks.yaml").read_text(encoding="utf-8")))
    app = render(path / "app")
    api = resource(app, "Deployment", "studio-api")
    mcp = resource(app, "Deployment", "studio-mcp")
    web = resource(app, "Deployment", "studio-web")
    worker = resource(render(path / "worker"), "Deployment", "studio-worker")
    migration = resource(render(path / "migration"), "Job")
    registration = resource(render(path / "worker-registration"), "Job")

    digest = image_digest(api, API_IMAGE)
    for workload in (mcp, worker, migration, registration):
        if image_digest(workload, API_IMAGE) != digest:
            raise ValueError(f"{workload['metadata']['name']} must use studio-api's digest {digest}")
    if digest != release["apiDigest"] or image_digest(web, API_IMAGE + "-web") != release["webDigest"]:
        raise ValueError("studio-api and studio-web must use the published release digest pair")

    api_env = env(api)
    worker_env = env(worker)
    revision = f"git:{release['revision']}"
    if not re.fullmatch(r"git:[0-9a-f]{40}", revision) or any(
        settings.get("STUDIO_BUILD_REVISION") != revision for settings in (api_env, worker_env)
    ):
        raise ValueError("studio-api and studio-worker must specify the full published git revision")
    if worker_env.get("STUDIO_BUILD_IMAGE_DIGEST") != digest:
        raise ValueError("studio-worker provenance digest must match its image")

    check_job(migration, resource(flux, "Kustomization", "studio-migration"), "studio-migrate", digest)
    check_job(registration, resource(flux, "Kustomization", "studio-worker-registration"),
              "studio-register-worker", digest)
    check_packaged_register(app, (api, worker))
    print(f"Studio release pins and Job health gates agree for {revision} ({digest})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("studio_path", type=Path, nargs="?", default=STUDIO_PATH)
    args = parser.parse_args()
    try:
        check(args.studio_path)
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError, yaml.YAMLError) as error:
        parser.exit(1, f"Studio release check failed: {error}\n")


if __name__ == "__main__":
    main()
