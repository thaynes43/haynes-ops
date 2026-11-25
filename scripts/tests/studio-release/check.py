"""Verify the rendered Studio image pins and their Flux Job gates."""

import argparse
from hashlib import sha256
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


def check_receipt(path, documents, job):
    volumes = job["spec"]["template"]["spec"]["volumes"]
    receipt_volume, = [volume for volume in volumes if volume["name"] == "receipt"]
    config = resource(documents, "ConfigMap", receipt_volume["configMap"]["name"])
    source_bytes = (path / "receipt.json").read_bytes()
    mounted_bytes = config["data"]["receipt.json"].encode("utf-8")
    expected = env(job)["STUDIO_IMPORT_RECEIPT_SHA256"]
    if source_bytes != mounted_bytes or sha256(mounted_bytes).hexdigest() != expected:
        raise ValueError(f"{job['metadata']['name']} receipt SHA-256 or rendered bytes differ")


def check_archive_jobs(path, flux, digest):
    stages = (
        ("import-one-more-meeting", "studio-import-one-more-meeting", {"studio-migration", "studio-archive-access"}),
        ("import-winter-menu", "studio-import-winter-menu", {"studio-migration", "studio-import-one-more-meeting"}),
        ("playback-registration", "studio-register-playback", {"studio", "studio-import-winter-menu"}),
        ("playback-proof", "studio-verify-playback", {"studio-playback-registration"}),
    )
    resource(flux, "Kustomization", "studio-archive-access")
    for folder, prefix, dependencies in stages:
        stage = resource(flux, "Kustomization", f"studio-{folder}")
        spec = stage["spec"]
        if spec["path"] != f"./{STUDIO_PATH}/{folder}":
            raise ValueError(f"{stage['metadata']['name']} points at the wrong Job directory")
        if {item["name"] for item in spec["dependsOn"]} != dependencies:
            raise ValueError(f"{stage['metadata']['name']} has the wrong dependencies")
        documents = render(path / folder)
        job = resource(documents, "Job")
        if image_digest(job, API_IMAGE) != digest:
            raise ValueError(f"{job['metadata']['name']} must use studio-api's digest {digest}")
        check_job(job, stage, prefix, digest)
        if folder.startswith("import-"):
            check_receipt(path / folder, documents, job)


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
    check_archive_jobs(path, flux, digest)
    print(f"Studio release pins, Job health gates and archive receipts agree for {revision} ({digest})")


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
