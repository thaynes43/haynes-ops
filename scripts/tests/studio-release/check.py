"""Verify the rendered Studio image pins and their Flux Job gates."""

import argparse
import json
import re
import subprocess
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, urlopen

import yaml


STUDIO_PATH = Path("kubernetes/main/apps/ai/studio")
API_IMAGE = "ghcr.io/thaynes43/haynes-studio"
MATTING_IMAGE = f"{API_IMAGE}-matting"
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
)
STALE_MATTING_DIGESTS = {
    "sha256:dd726f79d25eecc15231ad7b9f0ca53c31ae2504666b9abe233ad1cc457a3e6f",
    "sha256:5c431978f3bfe7a48deb062c6b2854ca8b086d1a5d9c600e2db4b1e26d702d72",
    "sha256:" + "0" * 64,
}


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


def published_matting_memory(digest):
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
    return f"{request}Gi", f"{limit}Gi"


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
        spec = policy["spec"] if policy["kind"] in {"NetworkPolicy", "CiliumNetworkPolicy"} else None
        if spec is None:
            continue
        selectors = []
        if policy["kind"] == "NetworkPolicy":
            selectors.append(spec.get("podSelector"))
            for rule in [*(spec.get("ingress") or []), *(spec.get("egress") or [])]:
                for peer in [*(rule.get("to") or []), *(rule.get("from") or [])]:
                    selectors += [peer.get("podSelector"), peer.get("namespaceSelector")]
        else:
            selectors.append(spec.get("endpointSelector"))
            for direction, peers in (("ingress", "fromEndpoints"), ("egress", "toEndpoints")):
                for rule in [*spec.get(direction, []), *spec.get(direction + "Deny", [])]:
                    selectors.extend(rule.get(peers, []))
        used = {key.removeprefix("k8s:") for selector in selectors for key in keys(selector)}
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
        raise ValueError("matting provisioning, smoke, quality trial and worker activation must stay staged")

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
    for overlay, cores in (("app", "20"), ("matting-pending", "18")):
        release = resource(render(render_path / overlay), "HelmRelease", "studio-render")
        render_app = release["spec"]["values"]["controllers"]["studio-render"]["containers"]["app"]
        if render_app["resources"]["limits"] != {"cpu": cores, "memory": "32Gi"}:
            raise ValueError(f"offline render {overlay} must limit CPU to {cores} and memory to 32Gi")


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
            ["studio-segmentation", "smoke", "--load-strategy", "mmap", "--compare-load-strategy", "buffered"],
            {"requests": {"cpu": "250m", "memory": pin["measurementRequest"]},
             "limits": {"cpu": "2", "memory": pin["measurementLimit"]}},
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


def _check_segmentation_activation(path, pending, pin, image, digest, allocation):
    render_path = path.with_name("studio-render")
    base = resource(render(render_path / "app"), "HelmRelease", "studio-render")["spec"]["values"]
    values = resource(render(pending / "activation"), "HelmRelease", "studio-render")["spec"]["values"]
    controller = values["controllers"]["studio-render"]
    sidecar = controller["containers"].pop("segmentation")
    render_app = controller["containers"]["app"]
    if render_app["resources"]["limits"] != {"cpu": "18", "memory": pin["renderMemoryLimit"]}:
        raise ValueError("segmentation activation must reserve two CPU cores and its measured share of 32 GiB")
    render_app["resources"]["limits"] = {"cpu": "20", "memory": "32Gi"}
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
    if values != base:
        raise ValueError("segmentation activation must preserve the base render workload")
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
            or sidecar["resources"] != {
                "requests": {"cpu": "250m", "memory": pin["servingRequest"]},
                "limits": {"cpu": "2", "memory": pin["servingLimit"]},
            }):
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
    if digest == "sha256:" + "0" * 64:
        raise ValueError("set segmentation-pending/release/release.yaml data.image to the published segmentation digest")
    try:
        config = published_config(SEGMENTATION_IMAGE, digest)
        labels = config["config"]["Labels"]
        memory = json.loads(labels["io.haynes-studio.segmentation.resources"])
        if ((config["os"], config["architecture"]) != ("linux", "amd64")
                or labels["org.opencontainers.image.source"] != "https://github.com/thaynes43/haynes-studio"
                or memory["schema"] != "studio.segmentation-resources/v1"):
            raise ValueError("invalid segmentation image resource label")
        minimum_request, minimum_limit = segmentation_memory(memory["measurement"])
        serving = memory["serving"]
        if serving is None:
            expected_serving = ("unmeasured", "unmeasured", "unmeasured")
        else:
            request, limit = segmentation_memory(serving)
            if limit > 31:
                raise ValueError("segmentation serving must leave memory for render")
            expected_serving = f"{request}Gi", f"{limit}Gi", f"{32 - limit}Gi"
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise ValueError("published segmentation image must carry the Studio profile resource contract") from error
    if config["config"].get("User") != "10001:10001":
        raise ValueError("published segmentation image must default to 10001:10001")
    measurement = (pin["measurementRequest"], pin["measurementLimit"])
    if not all(re.fullmatch(r"[1-9][0-9]*Gi", value) for value in measurement):
        raise ValueError("segmentation measurement resources must be positive whole GiB")
    request, limit = (int(value.removesuffix("Gi")) for value in measurement)
    if request < minimum_request or limit < minimum_limit or request > limit:
        raise ValueError("segmentation measurement resources must meet the pinned image's minimums")
    if (pin["servingRequest"], pin["servingLimit"], pin["renderMemoryLimit"]) != expected_serving:
        raise ValueError("segmentation serving resources must match the pinned image's measured contract")


def check(path):
    release = yaml.safe_load((path / "release.yaml").read_text(encoding="utf-8"))
    flux = list(yaml.safe_load_all((path / "ks.yaml").read_text(encoding="utf-8")))
    check_matting(path, flux)
    check_segmentation(path, flux)
    app = render(path / "app")
    api = resource(app, "Deployment", "studio-api")
    mcp = resource(app, "Deployment", "studio-mcp")
    web = resource(app, "Deployment", "studio-web")
    worker_stage = render(path / "worker")
    worker = resource(worker_stage, "Deployment", "studio-worker")
    media = render(path / "media-workers")
    voice = resource(media, "Deployment", "studio-voice-worker")
    image = resource(media, "Deployment", "studio-image-worker")
    for stage in (app, media, worker_stage):
        check_policy_selectors(stage)

    digest, web_digest = release["apiDigest"], release["webDigest"]
    if (image_digest(api, API_IMAGE, name="api") != digest
            or image_digest(web, API_IMAGE + "-web") != web_digest):
        raise ValueError("studio-api and studio-web must use the published release digest pair")
    strategy = api["spec"]["strategy"]
    if strategy["type"] != "RollingUpdate" or strategy["rollingUpdate"]["maxUnavailable"] != 0:
        raise ValueError("studio-api must keep old pods available while the new pods become ready")
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
    if image_digest(api, API_IMAGE + "-web", name="media-worker-routes") != web_digest:
        raise ValueError("The API media-worker listener must use the same nginx image as studio-web")
    for workload, args in ((worker, []), (voice, ["--lane", "voice"]), (image, ["--lane", "image"])):
        spec = container(workload)
        if spec["command"] != ["studio-worker"] or spec.get("args", []) != args:
            raise ValueError(f"{workload['metadata']['name']} must consume its assigned queue lane")
    routes = (path / "app/media-worker-routes.conf").read_text()
    for header in ("X-Forwarded-For", "X-Forwarded-Proto", "X-Forwarded-Host", "X-Real-IP", "Forwarded"):
        if f'proxy_set_header {header} "";' not in routes:
            raise ValueError(f"The API media-worker listener must clear {header} before the loopback API")

    api_env = env(api, "api")
    worker_env = env(worker)
    revision = f"git:{release['revision']}"
    if not re.fullmatch(r"git:[0-9a-f]{40}", revision) or any(
        settings.get("STUDIO_BUILD_REVISION") != revision for settings in (api_env, worker_env)
    ):
        raise ValueError("studio-api and studio-worker must specify the full published git revision")
    if worker_env.get("STUDIO_BUILD_IMAGE_DIGEST") != digest:
        raise ValueError("studio-worker provenance digest must match its image")

    check_packaged_register(app + media, ((api, "api"), (worker, None), (voice, None), (image, None)))
    check_drain(((api, ("api", "media-worker-routes")), (mcp, ("mcp",)), (web, ("nginx",))))
    print(f"Studio release pins and Job health gates agree for {revision} ({digest})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("studio_path", type=Path, nargs="?", default=STUDIO_PATH)
    parser.add_argument("--pin", nargs=2, metavar=("STUDIO_REVISION", "PUBLISH_RUN"),
                        help="verify the published image pair and update all release references")
    args = parser.parse_args()
    try:
        if args.pin:
            repin(args.studio_path, *args.pin)
        check(args.studio_path)
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError, yaml.YAMLError) as error:
        parser.exit(1, f"Studio release check failed: {error}\n")


if __name__ == "__main__":
    main()
