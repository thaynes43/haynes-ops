#!/usr/bin/env python3
"""ghcr.io/thaynes43/comfyui — serve, provision, or self-test.

``serve``      exec ComfyUI with every writable path pointed outside the
               read-only root filesystem.
``provision``  download the models the stored workflows need (see provision.py).
``test``       the CI smoke test: provisioner unit tests, then ComfyUI itself,
               with no network, no GPU and no models.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

COMFY_ROOT = os.environ.get("COMFYUI_ROOT", "/opt/ComfyUI")
HERE = os.path.dirname(os.path.abspath(__file__))

#: Defaults chosen so that every writable path lands on the workspace volume.
#: ComfyUI's own defaults all sit under /opt/ComfyUI, which is read-only here.
DEFAULT_USER_DIR = "/workspace/user"
DEFAULT_INPUT_DIR = "/workspace/input"
DEFAULT_OUTPUT_DIR = "/workspace/output"
DEFAULT_TEMP_DIR = "/workspace/temp"
DEFAULT_PORT = "8188"
DEFAULT_LISTEN = "0.0.0.0"

#: Node classes the stored Qwen-Image-2.1 workflows need.  If an upgrade drops
#: one of these the smoke test fails instead of the pod.
REQUIRED_NODE_CLASSES = (
    "TextEncodeQwenImage21",
    "QwenImage21Cache",
    "TextEncodeQwenImageEditPlus",
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
)


def log(message):
    # type: (str) -> None
    sys.stdout.write("[comfyui-entrypoint] %s\n" % message)
    sys.stdout.flush()


def _ensure_dir(path, what):
    # type: (str, str) -> str
    path = os.path.abspath(path)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise SystemExit(
            "cannot create the %s directory %s: %s — the root filesystem is "
            "read-only, so this path must be a writable volume" % (what, path, exc)
        )
    return path


#: Env vars the Dockerfile points at /tmp so that torch, huggingface_hub and
#: friends never try to cache into the read-only root filesystem.  /tmp is a
#: tmpfs, so these do not exist until something creates them.
CACHE_DIR_ENV_VARS = (
    "HOME",
    "XDG_CACHE_HOME",
    "HF_HOME",
    "TORCH_HOME",
    "TORCHINDUCTOR_CACHE_DIR",
    "TRITON_CACHE_DIR",
    "MPLCONFIGDIR",
)


def prepare_cache_dirs():
    # type: () -> None
    for name in CACHE_DIR_ENV_VARS:
        path = os.environ.get(name)
        if not path:
            continue
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            log("WARNING: could not create %s=%s: %s" % (name, path, exc))


def _temp_flag_value(effective_temp_dir):
    # type: (str) -> str
    """Translate an effective temp directory into ``--temp-directory``'s argument.

    ComfyUI does ``temp_dir = os.path.join(os.path.abspath(args.temp_directory),
    "temp")`` (main.py, v0.37.0) — it *appends* ``temp`` to whatever the flag
    gets.  Every other directory env var here names the directory ComfyUI will
    actually use, so this one does too: the parent is what goes on the command
    line.
    """
    effective = os.path.abspath(effective_temp_dir)
    if os.path.basename(effective) == "temp":
        return os.path.dirname(effective)
    log(
        "COMFYUI_TEMP_DIR=%s does not end in 'temp'; ComfyUI appends it, so the "
        "effective temp directory will be %s/temp" % (effective, effective)
    )
    return effective


def build_serve_argv():
    # type: () -> list
    """Assemble ComfyUI's argv and create every directory it will write to."""
    prepare_cache_dirs()
    port = os.environ.get("COMFYUI_PORT") or DEFAULT_PORT
    listen = os.environ.get("COMFYUI_LISTEN") or DEFAULT_LISTEN

    user_dir = _ensure_dir(
        os.environ.get("COMFYUI_USER_DIR") or DEFAULT_USER_DIR, "user"
    )
    # ComfyUI's LoadAudio/Load3D schemas mkdir inside the input directory while
    # nodes load, so it has to be writable, not merely present.
    input_dir = _ensure_dir(
        os.environ.get("COMFYUI_INPUT_DIR") or DEFAULT_INPUT_DIR, "input"
    )
    output_dir = _ensure_dir(
        os.environ.get("COMFYUI_OUTPUT_DIR") or DEFAULT_OUTPUT_DIR, "output"
    )
    effective_temp = os.path.abspath(
        os.environ.get("COMFYUI_TEMP_DIR") or DEFAULT_TEMP_DIR
    )
    temp_flag = _temp_flag_value(effective_temp)
    # ComfyUI rmtree()s and recreates the temp dir on every boot, so the PARENT
    # must be writable too.
    _ensure_dir(temp_flag, "temp parent")
    _ensure_dir(effective_temp, "temp")

    # The user directory also holds the SQLite asset/user database, its lock
    # file and any alembic backup — ComfyUI derives the database path from
    # --user-directory when --database-url is unset.
    argv = [
        sys.executable,
        os.path.join(COMFY_ROOT, "main.py"),
        "--listen",
        listen,
        "--port",
        str(port),
        "--disable-auto-launch",
        "--user-directory",
        user_dir,
        "--input-directory",
        input_dir,
        "--output-directory",
        output_dir,
        "--temp-directory",
        temp_flag,
    ]

    database_url = os.environ.get("COMFYUI_DATABASE_URL")
    if database_url:
        argv += ["--database-url", database_url]

    extra = os.environ.get("COMFYUI_ARGS") or ""
    if extra.strip():
        argv += shlex.split(extra)

    log("user directory:   %s (also holds comfyui.db)" % user_dir)
    log("input directory:  %s" % input_dir)
    log("output directory: %s" % output_dir)
    log("temp directory:   %s (--temp-directory %s)" % (effective_temp, temp_flag))
    if extra.strip():
        log("extra args:       %s" % extra.strip())
    return argv


def cmd_serve():
    # type: () -> None
    argv = build_serve_argv()
    os.chdir(COMFY_ROOT)
    log("exec: %s" % " ".join(shlex.quote(part) for part in argv))
    os.execv(argv[0], argv)


def cmd_provision(args):
    # type: (list) -> int
    prepare_cache_dirs()
    sys.path.insert(0, HERE)
    import provision

    return provision.main(args)


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------


def _test_dirs(root):
    # type: (str) -> dict
    dirs = {
        "user": os.path.join(root, "user"),
        "input": os.path.join(root, "input"),
        "output": os.path.join(root, "output"),
        "temp": os.path.join(root, "temp"),
    }
    for path in dirs.values():
        os.makedirs(path, exist_ok=True)
    return dirs


def _comfy_argv(dirs, extra):
    # type: (dict, list) -> list
    return [
        sys.executable,
        os.path.join(COMFY_ROOT, "main.py"),
        "--cpu",
        "--disable-auto-launch",
        "--user-directory",
        dirs["user"],
        "--input-directory",
        dirs["input"],
        "--output-directory",
        dirs["output"],
        "--temp-directory",
        os.path.dirname(dirs["temp"]),
    ] + extra


def _run_unit_tests():
    # type: () -> None
    log("running the provisioner unit tests")
    import pytest

    # WORKDIR is /opt/ComfyUI, which ships its own pytest.ini (testpaths =
    # tests tests-unit, addopts = -s, pythonpath = .). Run from our own
    # directory so pytest's rootdir/config discovery cannot pick it up.
    previous = os.getcwd()
    os.chdir(HERE)
    try:
        code = pytest.main(
            ["-q", "-p", "no:cacheprovider", os.path.join(HERE, "test_provision.py")]
        )
    finally:
        os.chdir(previous)
    if code != 0:
        raise SystemExit("provisioner unit tests failed (pytest exit %s)" % code)


def _run_quick_test(dirs):
    # type: (dict) -> None
    log("loading every node with --quick-test-for-ci")
    argv = _comfy_argv(dirs, ["--quick-test-for-ci"])
    result = subprocess.run(argv, cwd=COMFY_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            "ComfyUI --quick-test-for-ci exited %s" % result.returncode
        )


def _http_json(url, timeout=5.0):
    # type: (str, float) -> dict
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _run_server_test(dirs, port=8188, boot_timeout=420.0):
    # type: (dict, int, float) -> None
    """Start the real server and interrogate it.

    ``--quick-test-for-ci`` exits before the temp directory is created and
    before the HTTP bind, so it cannot prove the read-only root filesystem is
    survivable.  This can.
    """
    log("starting ComfyUI on port %d" % port)
    argv = _comfy_argv(dirs, ["--listen", "127.0.0.1", "--port", str(port)])
    process = subprocess.Popen(argv, cwd=COMFY_ROOT)
    base = "http://127.0.0.1:%d" % port
    deadline = time.time() + boot_timeout
    stats = None

    try:
        while time.time() < deadline:
            if process.poll() is not None:
                raise SystemExit(
                    "ComfyUI exited with %s before serving" % process.returncode
                )
            try:
                stats = _http_json(base + "/system_stats")
                break
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(2.0)

        if stats is None:
            raise SystemExit("ComfyUI did not answer /system_stats in %.0fs" % boot_timeout)

        version = (stats.get("system") or {}).get("comfyui_version")
        expected = os.environ.get("COMFYUI_VERSION", "").lstrip("v")
        log("ComfyUI reports version %s" % version)
        if expected and str(version).lstrip("v") != expected:
            raise SystemExit(
                "version mismatch: /system_stats says %r, image was built for %r"
                % (version, expected)
            )

        object_info = _http_json(base + "/object_info", timeout=120.0)
        missing = [name for name in REQUIRED_NODE_CLASSES if name not in object_info]
        if missing:
            raise SystemExit(
                "/object_info is missing required node class(es): %s"
                % ", ".join(missing)
            )
        log(
            "/object_info exposes %d node classes, including all %d required"
            % (len(object_info), len(REQUIRED_NODE_CLASSES))
        )

        # The temp directory is only created after --quick-test-for-ci would
        # have exited, so assert it really landed on the writable volume.
        if not os.path.isdir(dirs["temp"]):
            raise SystemExit("ComfyUI did not create its temp directory at %s" % dirs["temp"])
        if not os.path.isfile(os.path.join(dirs["user"], "comfyui.db")):
            raise SystemExit(
                "ComfyUI did not create its database under the user directory %s"
                % dirs["user"]
            )
        log("temp dir and comfyui.db both landed under the writable volume")
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - belt and braces
            process.kill()
            process.wait(timeout=30)


def cmd_test():
    # type: () -> int
    if not os.path.isfile(os.path.join(HERE, "TEST_IMAGE")):
        raise SystemExit("the 'test' subcommand only exists in the test image")

    prepare_cache_dirs()
    _run_unit_tests()

    root = os.environ.get("COMFYUI_TEST_ROOT") or "/workspace"
    dirs = _test_dirs(root)
    _run_quick_test(dirs)
    _run_server_test(dirs)

    # Nothing may have been written into the image itself.
    for offender in ("temp", "user", "web_custom_versions"):
        path = os.path.join(COMFY_ROOT, offender)
        if os.path.exists(path):
            raise SystemExit(
                "ComfyUI wrote %s into the read-only source tree" % path
            )
    log("all smoke tests passed")
    return 0


def main(argv=None):
    # type: (list) -> int
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv else "serve"
    rest = argv[1:]

    if mode == "serve":
        cmd_serve()  # never returns
        return 0
    if mode == "provision":
        return cmd_provision(rest)
    if mode == "test":
        return cmd_test()
    raise SystemExit("usage: entrypoint.py [serve|provision|test]")


if __name__ == "__main__":
    sys.exit(main())
