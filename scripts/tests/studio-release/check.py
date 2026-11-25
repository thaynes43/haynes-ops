"""Verify the rendered Studio image pins and their Flux Job gates."""

import argparse
import json
from hashlib import sha256
import re
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

import yaml


STUDIO_PATH = Path("kubernetes/main/apps/ai/studio")
API_IMAGE = "ghcr.io/thaynes43/haynes-studio"
MATTING_IMAGE = f"{API_IMAGE}-matting"
PRE_CR38_IMAGE = "sha256:dd726f79d25eecc15231ad7b9f0ca53c31ae2504666b9abe233ad1cc457a3e6f"


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


def check_model_licences(app, api, worker, digest):
    name = f"studio-model-licences-{digest.removeprefix('sha256:')[:12]}"
    config = resource(app, "ConfigMap", name)
    if config.get("immutable") is not True:
        raise ValueError(f"{name} must be immutable")
    annotations = config["metadata"].get("annotations", {})
    if annotations.get("kustomize.toolkit.fluxcd.io/prune") != "disabled":
        raise ValueError(f"{name} must be exempt from Flux pruning")
    for deployment in (api, worker):
        volumes = deployment["spec"]["template"]["spec"]["volumes"]
        register, = [volume for volume in volumes if volume["name"] == "model-licences"]
        if register["configMap"]["name"] != name:
            raise ValueError(f"{deployment['metadata']['name']} must mount {name}")


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


def published_matting_memory(digest):
    """Read the profile resource contract from the exact published image config."""
    repository = MATTING_IMAGE.removeprefix("ghcr.io/")
    base = f"https://ghcr.io/v2/{repository}"

    def fetch(endpoint, expected, accept="application/json"):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected):
            raise ValueError("invalid OCI digest in matting image")
        request = Request(f"{base}/{endpoint}/{expected}", headers={"Accept": accept})
        request.add_unredirected_header("Authorization", "Bearer " + token)
        with urlopen(request, timeout=15) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024 or "sha256:" + sha256(data).hexdigest() != expected:
            raise ValueError("matting image manifest or config SHA-256 differs from its pin")
        return json.loads(data)

    try:
        with urlopen(f"https://ghcr.io/token?service=ghcr.io&scope=repository:{repository}:pull", timeout=15) as response:
            token = json.loads(response.read(65536))["token"]
        manifest = fetch("manifests", digest, "application/vnd.docker.distribution.manifest.v2+json, "
                                            "application/vnd.oci.image.manifest.v1+json")
        config = fetch("blobs", manifest["config"]["digest"])
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
    return f"{request}Gi", f"{limit}Gi"


def check_matting(path, flux):
    pending = path / "matting-pending"
    if not pending.exists():
        return
    pin = yaml.safe_load((pending / "release/release.yaml").read_text())["data"]
    image = pin["image"]
    allocation = pin["allocation"]
    match = re.fullmatch(rf"{re.escape(MATTING_IMAGE)}@(sha256:[0-9a-f]{{64}})", image)
    if not match or match[1] in {PRE_CR38_IMAGE, "sha256:" + "0" * 64}:
        raise ValueError("set matting-pending/release/release.yaml data.image to the published matting digest")
    memory = published_matting_memory(match[1])
    if (pin["memoryRequest"], pin["memoryLimit"]) != memory:
        raise ValueError("matting release request and limit must match the published Studio profile")
    if not re.fullmatch(r"matting-op459-birefnet-lite-v1-[0-9]{2}", allocation):
        raise ValueError("invalid create-only matting allocation")

    policy_stage = resource(flux, "Kustomization", "studio-matting-policy")
    policy_path = f"./{STUDIO_PATH}/matting-pending/policy"
    if policy_stage["spec"]["path"] != policy_path or not policy_stage["spec"]["prune"]:
        raise ValueError("the Studio matting policy is the only applied matting stage and must prune")
    if any(
        "matting-pending" in stage["spec"]["path"] and stage is not policy_stage
        for stage in flux if stage["kind"] == "Kustomization"
    ):
        raise ValueError("matting provisioning, smoke and worker activation must stay staged")

    policy = render(pending / "policy")
    if {doc["kind"] for doc in policy} != {"CiliumNetworkPolicy", "ConfigMap"}:
        raise ValueError("the applied matting stage may include only policies and provisioner code")
    download = resource(policy, "CiliumNetworkPolicy", "studio-matting-provision")["spec"]
    smoke_egress = resource(policy, "CiliumNetworkPolicy", "studio-matting-smoke")["spec"]
    allow = [{"matchName": "huggingface.co"}, {"matchName": "us.aws.cdn.hf.co"}]
    dns, https = download["egress"]
    if (dns != {
        "toEndpoints": [{"matchLabels": {
            "k8s:io.kubernetes.pod.namespace": "kube-system", "k8s:k8s-app": "kube-dns",
        }}],
        "toPorts": [{"ports": [{"port": "53", "protocol": "ANY"}], "rules": {"dns": allow}}],
    } or https["toFQDNs"] != allow
            or https["toPorts"] != [{"ports": [{"port": "443", "protocol": "TCP"}]}]
            or smoke_egress.get("egressDeny") != [{"toEntities": ["all"]}]
            or "egress" in smoke_egress):
        raise ValueError("matting egress must be pinned DNS/HTTPS for provisioning and denied for smoke")
    for spec, name in ((download, "studio-matting-provision"), (smoke_egress, "studio-matting-smoke")):
        if (spec["endpointSelector"]["matchLabels"] != {"app.kubernetes.io/name": name}
                or spec["ingressDeny"] != [{"fromEntities": ["all"]}]):
            raise ValueError(f"wrong network selector or ingress rule for {name}")

    jobs = {}
    weight_path = f"data/studio/jobs/{allocation}/weights"
    for name, uid in (("provision", 1000), ("smoke", 10001)):
        stage = render(pending / name)
        name_suffix = allocation.rsplit("-", 1)[1]
        attempt = "-r3" if name == "smoke" else ""
        job = resource(stage, "Job", f"studio-matting-{name}-{name_suffix}{attempt}")
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
    if (container(smoke)["command"] != ["studio-matting", "smoke", "--no-cpu-arena"]
            or pod["securityContext"]["fsGroup"] != 10001):
        raise ValueError("the smoke Job must measure the CPU arena change with writable private scratch")
    check_matting_mounts(container(smoke), pod, weight_path, "scratch", memory)

    stage = render(pending / "activation")
    worker = resource(stage, "Deployment", "studio-worker")
    worker_pod = worker["spec"]["template"]["spec"]
    active = {c["name"]: c for c in worker_pod["containers"]}
    base = resource(render(path / "worker"), "Deployment", "studio-worker")
    old_worker = container(base)
    live_worker = active["worker"]
    new_mount, = [mount for mount in live_worker["volumeMounts"] if mount not in old_worker["volumeMounts"]]
    if (set(active) != {"worker", "matting"} or new_mount != {"name": "matte-scratch", "mountPath": "/matting"}
            or [m for m in live_worker["volumeMounts"] if m != new_mount] != old_worker["volumeMounts"]
            or {key: val for key, val in live_worker.items() if key != "volumeMounts"}
            != {key: val for key, val in old_worker.items() if key != "volumeMounts"}
            or worker_pod["securityContext"]["fsGroup"] != 10001
            or worker_pod["automountServiceAccountToken"] or old_worker["resources"]["limits"]["cpu"] != "4"):
        raise ValueError("the staged sidecar must preserve the existing worker and share only scratch")
    sidecar = active["matting"]
    check_matting_container(sidecar, image)
    check_matting_mounts(sidecar, worker_pod, weight_path, "matte-scratch", memory)
    if (sidecar["command"] != ["studio-matting", "serve"]
            or sidecar["securityContext"]["runAsUser"] != 10001
            or sidecar["securityContext"]["runAsGroup"] != 10001):
        raise ValueError("the matting sidecar must use 10001:10001")
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        if sidecar[probe]["exec"]["command"] != ["studio-matting", "ready"]:
            raise ValueError("the matting sidecar probes must use Unix HTTP readiness")

    render_path = path.with_name("studio-render")
    for overlay, cores, limit in (("app", "20", "32Gi"), ("matting-pending", "18", "16Gi")):
        release = resource(render(render_path / overlay), "HelmRelease", "studio-render")
        render_app = release["spec"]["values"]["controllers"]["studio-render"]["containers"]["app"]
        if render_app["resources"]["limits"] != {"cpu": cores, "memory": limit}:
            raise ValueError("the dormant matting split must stage offline render at 18 CPUs and 16Gi")


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


def check(path):
    release = yaml.safe_load((path / "release.yaml").read_text(encoding="utf-8"))
    flux = list(yaml.safe_load_all((path / "ks.yaml").read_text(encoding="utf-8")))
    check_matting(path, flux)
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
    check_model_licences(app, api, worker, digest)
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
