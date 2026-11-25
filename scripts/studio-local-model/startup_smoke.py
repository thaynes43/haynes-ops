"""Run the built image's actual serve entry point on a Docker CPU runner."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from textwrap import dedent

from startup_check import wait_for_listener


PENDING = Path(__file__).resolve().parents[2] / "kubernetes/main/apps/ai/wan-video/local-model-pending"


def fixture(root):
    root.chmod(0o755)  # The container runs as UID 1000.
    profile = dedent((PENDING / "activation/profile.yaml").read_text().split("  local-model.json: |\n", 1)[1])
    (root / "local-model.json").write_text(profile)
    pins = json.loads((PENDING / "weights/weights.json").read_text())
    directory = root / "weights" / pins["model"] / pins["revision"]
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps(pins))
    for pin in pins["files"]:
        # Startup compares the provisioning manifest and file types and sizes.
        # Sparse placeholders exercise that check without model contents.
        with (directory / pin["name"]).open("wb") as weight:
            weight.truncate(pin["bytes"])
    return json.loads(profile)["gpu_uuid"]


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=30).strip()


def smoke(image, root, pins, reason):
    container = docker(
        "run", "--detach", "--runtime", "runc", "--read-only", "--network", "none",
        "--cpus", "2", "--memory", "1g",
        "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--mount", f"type=bind,source={root},target=/fixture,readonly",
        "--tmpfs", "/workspace:rw,uid=1000,gid=1000,mode=0700",
        "--env", f"NVIDIA_VISIBLE_DEVICES={pins[0]}", "--env", f"CUDA_VISIBLE_DEVICES={pins[1]}",
        "--env", "NVIDIA_DRIVER_CAPABILITIES=compute,utility", "--env", "OMP_NUM_THREADS=2",
        "--env", "OPENBLAS_NUM_THREADS=1",
        image, "serve", "--profile", "/fixture/local-model.json", "--weights-root", "/fixture/weights",
    )
    try:
        with subprocess.Popen(["docker", "logs", "--follow", container], stdout=subprocess.PIPE,
                              bufsize=0) as logs:
            try:
                startup = wait_for_listener(logs.stdout, "local-model-listening")
            finally:
                logs.terminate()
                logs.wait(timeout=5)
        print(startup, flush=True)
        docker("exec", container, "python3", "-m", "studio_local_backend", "ready")
        residency = json.loads(docker(
            "exec", container, "python3", "-c",
            'from pathlib import Path; import json, product_local as local; '
            'print(json.dumps(local.exchange(Path("/workspace"), {"action": "residency"})))',
        ))
        expected_environment = dict(zip(("NVIDIA_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES"), pins))
        if (startup["pins"] != expected_environment or startup["startup_error"] != reason
                or residency.get("startup_error") != reason or residency["resident"] is not False
                or residency["receipt"] is not None):
            raise RuntimeError(f"Unexpected serve startup disposition: {startup}, {residency}")
    finally:
        docker("rm", "--force", container)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        card = fixture(root)
        smoke(sys.argv[1], root, (card, card), None)
        smoke(sys.argv[1], root, (card, "0"), "gpu_pin_mismatch")
