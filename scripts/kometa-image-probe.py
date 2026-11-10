"""Render a bounded, secret-free worker Job to check a proposed Kometa image.

Run locally with --image and --node; apply the JSON output as an ephemeral Job.
The embedded probe never loads config.yml, starts Kometa or contacts Plex or any
provider. On the exact image it checks that the retired IMDb dataset patch still
fails closed, that the image's own CLI parser accepts each production job's
arguments, and that a forked worker holds the shared lock until tini's group
termination releases it. It cannot prove that Kometa itself starts: the first
real run's "Finished Run" line does that.
"""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile


def probe():
    namespace = {"__name__": "hnet_probe_launcher", "__file__": "/tmp/serial-run.py"}
    exec(LAUNCHER_SOURCE, namespace)
    source = Path("/modules/imdb.py").read_bytes()
    print("image_imdb_sha256", hashlib.sha256(source).hexdigest(), flush=True)
    # Retired 2026-11-10: nothing sets HNET_IMDB_RATINGS_SOURCE=dataset. If it were
    # set by accident, the launcher's hash guard must refuse an upgraded image.
    try:
        namespace["patched_imdb_source"](source)
    except RuntimeError as error:
        assert "Unsupported Kometa IMDb source" in str(error), error
    else:
        raise AssertionError("the retired dataset patch accepts this image: it is the reviewed v2.5.2/v2.5.3 imdb.py, not an upgrade")
    print("retired_dataset_patch_fails_closed: PASS", flush=True)

    # Exercise the actual image's parser without importing/running the application.
    actual = ast.parse(Path("/kometa.py").read_text())
    parser_nodes = []
    for node in actual.body:
        if isinstance(node, ast.Assign):
            names = {n.id for target in node.targets for n in ast.walk(target) if isinstance(n, ast.Name)}
            if names & {"arguments", "parser", "args"}:
                parser_nodes.append(node)
                if "unknown" in names:
                    break
        elif isinstance(node, ast.For) and isinstance(node.iter, ast.Call):
            func = node.iter.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "arguments":
                parser_nodes.append(node)
    parser_code = compile(ast.Module(body=parser_nodes, type_ignores=[]), "/kometa.py", "exec")
    for job_args in JOB_ARGS:
        cli = {"argparse": argparse}
        sys.argv = ["/kometa.py", *job_args]
        exec(parser_code, cli)
        assert not cli["unknown"], job_args
        for flag in job_args:
            assert getattr(cli["args"], flag.removeprefix("--").replace("-", "_")) is True, flag
    print("actual_image_cli_flags", json.dumps(JOB_ARGS), "PASS", flush=True)

    operations_args = next(job_args for job_args in JOB_ARGS if "--operations-only" in job_args)
    pool_context = next(n for n in actual.body if isinstance(n, ast.FunctionDef) and n.name == "_process_pool_context")
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        root = Path(directory)
        stub = root / "kometa.py"
        stub.write_text("import multiprocessing,os,sys\nfrom pathlib import Path\nfrom concurrent.futures import ProcessPoolExecutor\n"
                        + ast.unparse(pool_context) + f'''
def worker():
    assert sys.argv[1:] == {operations_args!r}
    assert any(os.readlink(fd).endswith("run.lock") for fd in Path("/proc/self/fd").iterdir() if fd.exists())
    print("WORKER_READY", flush=True)
    multiprocessing.Event().wait()
if __name__ == "__main__":
    with ProcessPoolExecutor(max_workers=1, mp_context=_process_pool_context()) as pool:
        pool.submit(worker).result()
''')
        launcher = root / "serial-run.py"
        launcher.write_text(LAUNCHER_SOURCE)
        runner = root / "runner.py"
        runner.write_text("import runpy\n"
                          f"m=runpy.run_path({str(launcher)!r})\n"
                          f"m['main'].__globals__['KOMETA_PATH']=m['Path']({str(stub)!r})\n"
                          f"m['main'].__globals__['LOCK_PATH']=m['Path']({str(root / 'run.lock')!r})\n"
                          "m['main']()\n")
        # Production mode: the launcher takes the lock, then execs unmodified Kometa.
        env = {**os.environ, "HNET_IMDB_RATINGS_SOURCE": "upstream"}
        proc = subprocess.Popen([*TINI_COMMAND[:-1], str(runner), *operations_args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, bufsize=0, start_new_session=True)
        try:
            ready = False
            for _ in range(6):
                assert select.select([proc.stdout], [], [], 5)[0], "worker startup timeout"
                line = proc.stdout.readline()
                if b"WORKER_READY" in line:
                    ready = True
                    break
                if not line:
                    raise AssertionError(proc.stderr.read().decode())
            assert ready
            acquire = "import fcntl,sys; fp=open(sys.argv[1],'a'); fcntl.flock(fp,fcntl.LOCK_EX|fcntl.LOCK_NB)"
            check = subprocess.run([sys.executable, "-c", acquire, str(root / "run.lock")], capture_output=True, timeout=5)
            assert check.returncode != 0 and b"BlockingIOError" in check.stderr
            proc.terminate()
            proc.wait(timeout=8)
            check = subprocess.run([sys.executable, "-c", acquire, str(root / "run.lock")], capture_output=True, timeout=5)
            assert check.returncode == 0, "orphan worker retained lock after tini termination"
            print("actual_image_worker_lock_and_tini_group_termination: PASS", flush=True)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()
    print("kometa_image_probe: PASS", flush=True)


def render_job():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--node", required=True, help="Idle worker node; never a control-plane node")
    parser.add_argument("--name", default="kometa-image-probe")
    args = parser.parse_args()
    launcher = Path(__file__).resolve().parents[1] / "kubernetes/main/apps/media/kometa/app/config/serial-run.py"
    release = launcher.parents[1] / "helmrelease.yaml"
    lines = [line.strip() for line in release.read_text().splitlines()]
    command_line = next(line for line in lines if line.startswith("command: ["))
    tini_command = json.loads(command_line.removeprefix("command: "))
    job_args = [json.loads(line.removeprefix("args: ")) for line in lines if line.startswith("args: [")]
    assert len(job_args) == 3 and sum("--operations-only" in a for a in job_args) == 1, job_args
    code = (f"LAUNCHER_SOURCE={launcher.read_text()!r}\nTINI_COMMAND={tini_command!r}\nJOB_ARGS={job_args!r}\n"
            + Path(__file__).read_text() + "\nprobe()\n")
    print(json.dumps({"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": args.name, "namespace": "media"},
          "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 180, "ttlSecondsAfterFinished": 300,
          "template": {"spec": {"restartPolicy": "Never", "automountServiceAccountToken": False,
          "nodeSelector": {"kubernetes.io/hostname": args.node},
          "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000,
                              "seccompProfile": {"type": "RuntimeDefault"}},
          "containers": [{"name": "probe", "image": args.image, "command": ["/.venv/bin/python3", "-c", code],
          "resources": {"requests": {"cpu": "100m", "memory": "512Mi"}, "limits": {"cpu": "500m", "memory": "1Gi"}},
          "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
          "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}]}], "volumes": [{"name": "tmp", "emptyDir": {}}]}}}}))


if __name__ == "__main__" and "LAUNCHER_SOURCE" not in globals():
    render_job()
