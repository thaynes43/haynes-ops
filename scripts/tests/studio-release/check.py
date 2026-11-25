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
UPSCALING_IMAGE = f"{API_IMAGE}-upscaling"
STALE_MATTING_DIGESTS = {
    "sha256:dd726f79d25eecc15231ad7b9f0ca53c31ae2504666b9abe233ad1cc457a3e6f",
    "sha256:5c431978f3bfe7a48deb062c6b2854ca8b086d1a5d9c600e2db4b1e26d702d72",
    "sha256:" + "0" * 64,
}


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


def published_labels(image, digest):
    """Read and verify the registry manifest and image config at an exact digest."""
    repository = image.removeprefix("ghcr.io/")
    base = f"https://ghcr.io/v2/{repository}"

    def fetch(endpoint, expected, accept="application/json"):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected):
            raise ValueError("invalid OCI image digest")
        request = Request(f"{base}/{endpoint}/{expected}", headers={"Accept": accept})
        request.add_unredirected_header("Authorization", "Bearer " + token)
        with urlopen(request, timeout=15) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024 or "sha256:" + sha256(data).hexdigest() != expected:
            raise ValueError("image manifest or config SHA-256 differs from its pin")
        return json.loads(data)

    with urlopen(f"https://ghcr.io/token?service=ghcr.io&scope=repository:{repository}:pull", timeout=15) as response:
        token = json.loads(response.read(65536))["token"]
    manifest = fetch("manifests", digest, "application/vnd.docker.distribution.manifest.v2+json, "
                                        "application/vnd.oci.image.manifest.v1+json")
    config = fetch("blobs", manifest["config"]["digest"])
    labels = config["config"]["Labels"]
    if ((config["os"], config["architecture"]) != ("linux", "amd64")
            or labels["org.opencontainers.image.source"] != "https://github.com/thaynes43/haynes-studio"):
        raise ValueError("expected a Linux amd64 Studio image")
    return labels


def gibibytes(resources):
    request, limit = resources["request_gib"], resources["limit_gib"]
    if type(request) is not int or type(limit) is not int or not 0 < request <= limit:
        raise ValueError("invalid memory request or limit")
    return f"{request}Gi", f"{limit}Gi"


def published_matting_memory(digest):
    try:
        memory = json.loads(published_labels(MATTING_IMAGE, digest)["io.haynes-studio.matting.resources"])
        if memory["schema"] != "studio.matting-resources/v1":
            raise ValueError("invalid matting profile resource contract")
        return gibibytes(memory)
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise ValueError("published matting image must carry the Studio profile resource contract") from error



# Cilium's default label filter leaves these out of a pod's security identity,
# so a NetworkPolicy peer that requires one of them never matches.
IDENTITY_EXCLUDED_LABELS = (
    "statefulset.kubernetes.io/pod-name", "apps.kubernetes.io/pod-index",
    "batch.kubernetes.io/job-completion-index", "controller-revision-hash",
    "pod-template-hash", "pod-template-generation",
)


def check_policy_selectors(documents):
    def keys(selector):
        selector = selector or {}
        yield from (selector.get("matchLabels") or {})
        yield from (item["key"] for item in selector.get("matchExpressions") or [])

    for policy in (doc for doc in documents if doc["kind"] in {"NetworkPolicy", "CiliumNetworkPolicy"}):
        spec = policy["spec"]
        selectors = [spec.get("podSelector"), spec.get("endpointSelector")]
        for rule in [*(spec.get("ingress") or []), *(spec.get("egress") or [])]:
            selectors += [*(rule.get("toEndpoints") or []), *(rule.get("fromEndpoints") or [])]
            for peer in [*(rule.get("to") or []), *(rule.get("from") or [])]:
                selectors += [peer.get("podSelector"), peer.get("namespaceSelector")]
        used = {key.removeprefix("k8s:") for selector in selectors for key in keys(selector)}
        if bad := sorted(used.intersection(IDENTITY_EXCLUDED_LABELS)):
            raise ValueError(f"{policy['metadata']['name']} selects by {bad[0]}, which Cilium leaves "
                             "out of pod identities; the rule would never match")


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


def check_upscaling_jobs(stage, image, allocation, measuring, policies):
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
        ("compare", 1000, ["studio-upscaling", "compare"],
         ["--input", "input-required", "--input-sha256", "sha256-required", "--allocation", "allocation-required"],
         {"scratch": scratch, "weights": weights, "jobs": jobs_mount,
          "source": {"name": "source", "mountPath": "/studio-source", "subPath": "data/studio", "readOnly": True}},
         {"weights": True, "jobs": False, "source": True}),
    ):
        name = f"studio-upscaling-{role}"
        job_name = name if role == "compare" else f"{name}-{suffix}"
        documents = provision_docs if role == "provision" else render(stage / role)
        job = resource(documents, "Job", job_name)
        spec = job["spec"]
        pod = spec["template"]["spec"]
        label = {"app.kubernetes.io/name": name}
        if (job["metadata"]["namespace"] != "ai" or spec["activeDeadlineSeconds"] != 300
                or spec["backoffLimit"] != 0 or spec["ttlSecondsAfterFinished"] != 86400
                or spec["template"]["metadata"]["labels"] != label
                or pod["restartPolicy"] != "Never" or pod["automountServiceAccountToken"]
                or pod["enableServiceLinks"] or pod.get("serviceAccountName") or pod.get("initContainers")
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


def check_upscaling(path, flux):
    stage = path / "upscaling-pending"
    if not stage.exists():
        return
    pin = yaml.safe_load((stage / "release/release.yaml").read_text())["data"]
    image, allocation = pin["image"], pin["allocation"]
    if not re.fullmatch(r"upscaling-op461-fsrcnn-x2-v1-[0-9]{2}", allocation):
        raise ValueError("invalid create-only upscaling weights allocation")
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
    serving = pin["servingMemoryRequest"], pin["servingMemoryLimit"]
    check_upscaling_jobs(stage, image, allocation, measuring, policies)
    activation = render(stage / "activation")
    check_policy_selectors(activation)
    worker = resource(activation, "Deployment", "studio-worker")
    pod = worker["spec"]["template"]["spec"]
    sidecar = container(worker, "upscaling")
    check_upscaling_container(sidecar, image, ["studio-upscaling", "serve"])
    scratch = {"name": "upscale-scratch", "mountPath": "/upscaling"}
    check_upscaling_mounts(sidecar, pod, {
        "upscale-scratch": scratch,
        "weights": {"name": "weights", "mountPath": "/weights",
                    "subPath": f"data/studio/jobs/{allocation}/weights", "readOnly": True},
    }, {"weights": True})
    if (pod["securityContext"]["fsGroup"] != 10001 or pod["automountServiceAccountToken"]
            or (sidecar["securityContext"]["runAsUser"], sidecar["securityContext"]["runAsGroup"]) != (10001, 10001)
            or scratch not in container(worker, "worker")["volumeMounts"]
            or resource_volume(pod, "upscale-scratch").get("emptyDir") != {"sizeLimit": "256Mi"}
            or sidecar["resources"]["limits"] != {"cpu": "2", "memory": serving[1]}
            or sidecar["resources"]["requests"]["memory"] != serving[0]
            or any(sidecar[key]["exec"]["command"] != ["studio-upscaling", "ready"]
                   for key in ("startupProbe", "readinessProbe", "livenessProbe"))):
        raise ValueError("dormant upscaling sidecar must share bounded scratch and only use local readiness")
    render_worker = resource(render(path.parent / "studio-render/upscaling-pending"), "HelmRelease", "studio-render")
    limits = render_worker["spec"]["values"]["controllers"]["studio-render"]["containers"]["app"]["resources"]["limits"]
    if limits != {"cpu": "18", "memory": "32Gi"}:
        raise ValueError("the dormant upscaling render split must reserve two of the 20 offline CPUs")

    match = re.fullmatch(rf"{re.escape(UPSCALING_IMAGE)}@(sha256:[0-9a-f]{{64}})", image)
    if not match or match[1] == "sha256:" + "0" * 64:
        raise ValueError("set upscaling-pending/release/release.yaml data.image to the published Studio main digest")
    try:
        published = published_labels(UPSCALING_IMAGE, match[1])
        memory = json.loads(published["io.haynes-studio.upscaling.resources"])
        if (memory["schema"] != "studio.upscaling-resources/v1"
                or type(memory["quality_peak_gib"]) is not int or memory["quality_peak_gib"] <= 0
                or not re.fullmatch(r"[a-f0-9]{40}", published["org.opencontainers.image.revision"])):
            raise ValueError("invalid upscaling profile resource contract")
        measured_serving = (("measured-request-required", "measured-limit-required")
                            if memory["serving"] is None else gibibytes(memory["serving"]))
        if measuring != gibibytes(memory["measuring"]) or serving != measured_serving:
            raise ValueError("upscaling release memory differs from the pinned image contract")
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise ValueError(f"published upscaling image resource contract failed verification: {error}") from error


def resource_volume(pod, name):
    return next(volume for volume in pod["volumes"] if volume["name"] == name)


def check(path):
    release = yaml.safe_load((path / "release.yaml").read_text(encoding="utf-8"))
    flux = list(yaml.safe_load_all((path / "ks.yaml").read_text(encoding="utf-8")))
    check_matting(path, flux)
    check_upscaling(path, flux)
    app = render(path / "app")
    api = resource(app, "Deployment", "studio-api")
    mcp = resource(app, "Deployment", "studio-mcp")
    web = resource(app, "Deployment", "studio-web")
    worker = resource(render(path / "worker"), "Deployment", "studio-worker")
    media = render(path / "media-workers")
    voice = resource(media, "Deployment", "studio-voice-worker")
    image = resource(media, "Deployment", "studio-image-worker")
    for stage in (app, media, render(path / "worker")):
        check_policy_selectors(stage)

    digest = image_digest(api, API_IMAGE, name="api")
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
            raise ValueError(f"{workload['metadata']['name']} must use studio-api's digest {digest}")
    web_digest = image_digest(web, API_IMAGE + "-web")
    if digest != release["apiDigest"] or web_digest != release["webDigest"]:
        raise ValueError("studio-api and studio-web must use the published release digest pair")
    if image_digest(api, API_IMAGE + "-web", name="media-worker-routes") != web_digest:
        raise ValueError("The API media-worker listener must use the same nginx image as studio-web")
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
