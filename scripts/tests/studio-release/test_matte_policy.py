"""Verify the standalone matte policy through the rendered Flux inventory."""

import copy
import shutil
import subprocess
import unittest
from fnmatch import fnmatchcase
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from check import (
    FLUX_PRUNE_ANNOTATION,
    MATTE_POLICY,
    MATTE_POLICY_STAGE,
    STUDIO_PATH,
    audit_matte_policy,
    flux_build,
    resource,
    studio_inventory,
)


REPO = Path.cwd()
AI_INVENTORY = STUDIO_PATH.parent / "kustomization.yaml"
FLUX_APPS_INVENTORY = Path("kubernetes/main/flux/apps.yaml")
KUSTOMIZATION_FILES = ("kustomization.yaml", "kustomization.yml", "Kustomization")
SELECTOR = {"app.kubernetes.io/name": "studio-matte-worker"}
STAGE_SPEC = {
    "targetNamespace": "ai",
    "path": f"./{STUDIO_PATH}/matting-pending/worker-policy",
    "sourceRef": {"kind": "GitRepository", "name": "haynes-ops", "namespace": "flux-system"},
    "prune": True,
    "deletionPolicy": "Orphan",
    "interval": "30m",
    "retryInterval": "1m",
    "timeout": "2m",
}
POLICY_SPEC = {
    "podSelector": {"matchLabels": SELECTOR},
    "policyTypes": ["Ingress", "Egress"],
    "ingress": [],
    "egress": [
        {
            "to": [
                {
                    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                    "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                },
            ],
            "ports": [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}],
        },
        {
            "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "studio-api"}}}],
            "ports": [{"port": 8002, "protocol": "TCP"}],
        },
    ],
}


@cache
def stage_definitions(repo):
    _, _, _, stages = studio_inventory(repo)
    if MATTE_POLICY_STAGE not in stages:
        raise ValueError(f"cluster-apps inventory is missing ai/{MATTE_POLICY_STAGE}")
    return stages


@cache
def policy_render(repo):
    return flux_build(repo, stage_definitions(repo)[MATTE_POLICY_STAGE])


def helm_documents(documents, release):
    spec = release["spec"]
    namespace = release["metadata"]["namespace"]
    name = spec.get("releaseName", release["metadata"]["name"])
    if "targetNamespace" in spec:
        namespace = spec["targetNamespace"]
        name = spec.get("releaseName", f"{namespace}-{name}")
    if spec.get("valuesFrom") or spec.get("postRenderers"):
        raise ValueError(f"include all effective Helm values and patches before auditing {name}")
    chart = resource(documents, "OCIRepository", spec["chartRef"]["name"])["spec"]
    output = subprocess.check_output(
        ["helm", "template", name, chart["url"], "--version", chart["ref"]["tag"],
         "--namespace", namespace, "--values", "-"],
        input=yaml.safe_dump(spec["values"]), text=True,
    )
    return [doc for doc in yaml.safe_load_all(output) if doc]


def workload_documents():
    stages = stage_definitions(REPO)
    active_paths = {REPO / stage["spec"]["path"] for stage in stages.values()}
    builds = [flux_build(REPO, stage) for name, stage in stages.items() if name != MATTE_POLICY_STAGE]
    for app in sorted((REPO / STUDIO_PATH.parent).glob("studio*")):
        for manifest in sorted(path for name in KUSTOMIZATION_FILES for path in app.rglob(name)):
            directory = manifest.parent
            config = yaml.safe_load(manifest.read_text())
            if directory not in active_paths and config["kind"] == "Kustomization":
                stage = {
                    "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
                    "kind": "Kustomization",
                    "metadata": {"name": "studio-selector-test", "namespace": "ai"},
                    "spec": {"path": str(directory.relative_to(REPO)), "targetNamespace": "ai"},
                }
                builds.append(flux_build(REPO, stage))

    for documents in builds:
        for document in documents:
            if document["kind"] == "HelmRelease":
                yield from helm_documents(documents, document)
            else:
                yield document


def pod_labels(document):
    spec = document.get("spec", {})
    kind = document["kind"]
    if kind == "Pod":
        return document["metadata"].get("labels", {})
    if kind == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    if kind in {"CronJob", "Job", "DaemonSet", "Deployment", "ReplicaSet", "StatefulSet",
                "ReplicationController"}:
        return spec["template"].get("metadata", {}).get("labels", {})
    return None


class MattePolicyTest(unittest.TestCase):
    def test_effective_stage_owns_only_the_reviewed_network_policy(self):
        stage = stage_definitions(REPO)[MATTE_POLICY_STAGE]
        self.assertEqual(stage["spec"], STAGE_SPEC)
        self.assertEqual(policy_render(REPO), [{
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "annotations": {FLUX_PRUNE_ANNOTATION: "disabled"},
                "labels": {
                    "kustomize.toolkit.fluxcd.io/name": MATTE_POLICY_STAGE,
                    "kustomize.toolkit.fluxcd.io/namespace": "ai",
                },
                "name": MATTE_POLICY,
                "namespace": "ai",
            },
            "spec": POLICY_SPEC,
        }])

    def test_selector_matches_no_rendered_studio_workload(self):
        selector = policy_render(REPO)[0]["spec"]["podSelector"]["matchLabels"]
        names = set()
        for workload in workload_documents():
            labels = pod_labels(workload)
            if labels is None:
                continue
            name = workload["metadata"]["name"]
            names.add(name)
            with self.subTest(kind=workload["kind"], workload=name):
                self.assertFalse(selector.items() <= labels.items())
        self.assertIn("studio-api", names)
        self.assertIn("studio-render", names)
        print(f"Checked the matte selector against {len(names)} distinct Studio workload names")

    def test_parent_inventory_dropping_studio_is_detected(self):
        with TemporaryDirectory(prefix="studio-matte-inventory-") as directory:
            repo = Path(directory)
            shutil.copytree(REPO / "kubernetes", repo / "kubernetes")
            for path in (AI_INVENTORY, FLUX_APPS_INVENTORY):
                with self.subTest(inventory=path):
                    inventory = repo / path
                    original = inventory.read_text()
                    contents = yaml.safe_load(original)
                    if path == AI_INVENTORY:
                        contents["resources"].remove("./studio/ks.yaml")
                    else:
                        contents["spec"]["path"] = "./kubernetes/main/apps/network"
                    inventory.write_text(yaml.safe_dump(contents))
                    try:
                        with self.assertRaisesRegex(ValueError, "cluster-apps inventory is missing ai/studio-matte-policy"):
                            stage_definitions(repo)
                    finally:
                        inventory.write_text(original)

            # An explicit root inventory can replace Flux's generated one.
            inventory = repo / AI_INVENTORY.parent.parent / "kustomization.yml"
            inventory.write_text(yaml.safe_dump({
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": ["network"],
            }))
            with self.assertRaisesRegex(ValueError, "cluster-apps inventory is missing ai/studio-matte-policy"):
                stage_definitions(repo)

    def test_required_aggregate_runs_the_guard_for_parent_inventory_files(self):
        workflow = yaml.safe_load((REPO / ".github/workflows/flux-local.yaml").read_text())
        jobs = workflow["jobs"]
        filter_key = "studio-matte-policy-files"
        step = next(step for step in jobs["filter"]["steps"] if step["id"] == filter_key)
        patterns = step["with"]["patterns"].splitlines()
        inventories = [FLUX_APPS_INVENTORY, *(
            directory / filename
            for directory in (AI_INVENTORY.parent, AI_INVENTORY.parent.parent)
            for filename in KUSTOMIZATION_FILES
        )]
        for inventory in inventories:
            with self.subTest(inventory=inventory):
                self.assertTrue(any(fnmatchcase(str(inventory), pattern) for pattern in patterns))
        self.assertFalse(any(fnmatchcase("kubernetes/main/apps/ai/wan-video/ks.yaml", pattern)
                             for pattern in patterns))
        self.assertEqual(jobs["filter"]["outputs"][filter_key],
                         "${{ steps.studio-matte-policy-files.outputs.changed_files }}")
        self.assertEqual(jobs["studio-matte-policy-guard"]["if"],
                         "${{ needs.filter.outputs.studio-matte-policy-files != '[]' }}")
        self.assertIn("studio-matte-policy-guard", jobs["success"]["needs"])

    def test_read_back_checks_revision_retention_spec_and_pod_absence(self):
        revision = "a" * 40
        applied = f"main@sha1:{revision}"
        stage = copy.deepcopy(stage_definitions(REPO)[MATTE_POLICY_STAGE])
        stage["metadata"]["generation"] = 1
        stage["spec"]["force"] = False
        stage["status"] = {
            "observedGeneration": 1,
            "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 1}],
            "lastAppliedRevision": applied,
            "lastAttemptedRevision": applied,
            "inventory": {"entries": [{"id": f"ai_{MATTE_POLICY}_networking.k8s.io_NetworkPolicy", "v": "v1"}]},
        }
        policy = copy.deepcopy(policy_render(REPO)[0])
        del policy["spec"]["ingress"]
        snapshot = [stage, policy, {"items": []}]
        audit_matte_policy(REPO, revision, snapshot)
        for index, keys, value, message in (
            (0, ("status", "lastAppliedRevision"), "main@sha1:" + "b" * 40, "must be Ready at"),
            (0, ("status", "observedGeneration"), 0, "must be Ready at"),
            (0, ("spec", "deletionPolicy"), "MirrorPrune", "spec.deletionPolicy"),
            (1, ("metadata", "annotations"), {}, "prune protection"),
            (1, ("spec", "ingress"), [{}], "live spec differs"),
            (1, ("spec", "egress"), [{}], "live spec differs"),
            (2, ("items",), [{"metadata": {"name": "terminating-pod"}}], "selects an existing pod"),
        ):
            with self.subTest(keys=keys):
                changed = copy.deepcopy(snapshot)
                target = changed[index]
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
                with self.assertRaisesRegex(ValueError, message):
                    audit_matte_policy(REPO, revision, changed)


if __name__ == "__main__":
    unittest.main()
