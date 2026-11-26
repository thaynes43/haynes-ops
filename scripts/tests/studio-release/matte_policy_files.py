"""Identify inventory and workload inputs that require the matte policy guard."""

import argparse
import os
import subprocess
from fnmatch import fnmatchcase

INVENTORY = (
    "kubernetes/main/flux/**",
    *(f"{directory}/{name}"
      for directory in ("kubernetes/main/apps", "kubernetes/main/apps/ai")
      for name in ("kustomization.yaml", "kustomization.yml", "Kustomization")),
)
WORKLOADS = (
    "kubernetes/main/apps/ai/studio*/**",
    "kubernetes/shared/components/common/repos/app-template/**",
    "scripts/tests/studio-release/check.py",
    "scripts/tests/studio-release/install-flux.sh",
    "scripts/tests/studio-release/test_matte_policy.py",
    "scripts/tests/studio-release/matte_policy_files.py",
    ".github/workflows/flux-local.yaml",
)


def checks(base, head):
    # Treat renames as deletion plus addition, so both paths are matched.
    output = subprocess.check_output(["git", "diff", "--no-renames", "--name-only", "-z", base, head, "--"])
    paths = [os.fsdecode(path) for path in output.split(b"\0") if path]
    workloads = any(fnmatchcase(path, pattern) for path in paths for pattern in WORKLOADS)
    inventory = workloads or any(fnmatchcase(path, pattern) for path in paths for pattern in INVENTORY)
    return inventory, workloads


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("head")
    args = parser.parse_args()
    inventory, workloads = checks(args.base, args.head)
    print(f"inventory={str(inventory).lower()}\nworkloads={str(workloads).lower()}")
