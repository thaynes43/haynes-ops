"""Render a bounded, secret-free worker Job to check the guarded Kometa image.

Run locally with --image and --node; apply the JSON output as an ephemeral Job.
The embedded probe never loads config.yml or contacts Plex. It only downloads the
official ratings dataset into emptyDir and exercises a blocking stub worker.
"""

import argparse
import ast
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time


def probe():
    namespace = {"__name__": "hnet_probe_launcher", "__file__": "/tmp/serial-run.py"}
    exec(LAUNCHER_SOURCE, namespace)
    namespace["patched_imdb_source"](Path("/modules/imdb.py").read_bytes())
    print("image_source_guard: PASS", flush=True)

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
    cli = {"argparse": argparse}
    sys.argv = ["/kometa.py", "--run", "--operations-only", "--timings"]
    exec(compile(ast.Module(body=parser_nodes, type_ignores=[]), "/kometa.py", "exec"), cli)
    assert cli["args"].run and cli["args"].operations_only and cli["args"].timings
    assert not cli["unknown"]
    print("actual_image_cli_flags: PASS", flush=True)

    namespace["sys"].meta_path.insert(0, namespace["DatasetIMDbFinder"]())
    from modules import util

    class QuietLogger:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    util.logger = QuietLogger()
    from modules.imdb import IMDb
    from modules.request import Requests

    client = Requests("probe", "", "master", None)
    original_stream = client.get_stream
    downloads = []

    def stream(url, path, info="Item"):
        assert url == "https://datasets.imdbws.com/title.ratings.tsv.gz", "unexpected network endpoint"
        downloads.append(url)
        return original_stream(url, path, info)

    def unexpected(*args, **kwargs):
        raise AssertionError("per-title service call")

    client.get_stream = stream
    client.get = unexpected
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        imdb = IMDb(client, None, directory)
        started = time.monotonic()
        assert imdb.get_rating(None) is None and not downloads
        movie = float(imdb.get_rating("tt0111161"))
        show = float(imdb.get_rating("tt0944947"))
        assert 0 <= movie <= 10 and 0 <= show <= 10
        assert imdb.get_rating("tt999999999999") is None and len(downloads) == 1
        assert not list(Path(directory).iterdir())
        print("daily_dataset_probe", json.dumps({"seconds": round(time.monotonic() - started, 3),
              "downloads": len(downloads), "rows": len(imdb._ratings), "movie": movie, "show": show,
              "missing_id": True, "temp_cleanup": True}), flush=True)

    pool_context = next(n for n in actual.body if isinstance(n, ast.FunctionDef) and n.name == "_process_pool_context")
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        root = Path(directory)
        stub = root / "kometa.py"
        stub.write_text("import multiprocessing,os,sys\nfrom pathlib import Path\nfrom concurrent.futures import ProcessPoolExecutor\n"
                        "from modules.imdb import IMDb\n" + ast.unparse(pool_context) + '''
imdb = IMDb(None, None, "/tmp")
imdb._ratings = {"movie": "8.1", "show": "7.3"}
def unexpected(*args):
    raise AssertionError("per-title service call")
imdb._service_title = unexpected
def worker():
    assert sys.argv[1:] == ["--run", "--operations-only", "--timings"]
    assert any(os.readlink(fd).endswith("run.lock") for fd in Path("/proc/self/fd").iterdir() if fd.exists())
    assert imdb.get_rating("movie") == "8.1" and imdb.get_rating("show") == "7.3"
    print("WORKER_PATCH_READY", flush=True)
    multiprocessing.Event().wait()
if __name__ == "__main__":
    with ProcessPoolExecutor(max_workers=1, mp_context=_process_pool_context()) as pool:
        pool.submit(worker).result()
''')
        launcher = root / "serial-run.py"
        launcher.write_text(LAUNCHER_SOURCE)
        runner = root / "runner.py"
        runner.write_text("import runpy,sys\nsys.path.insert(0,'/')\n"
                          f"m=runpy.run_path({str(launcher)!r})\n"
                          f"m['main'].__globals__['KOMETA_PATH']=m['Path']({str(stub)!r})\n"
                          f"m['main'].__globals__['LOCK_PATH']=m['Path']({str(root / 'run.lock')!r})\n"
                          "m['main']()\n")
        env = {**os.environ, "HNET_IMDB_RATINGS_SOURCE": "dataset"}
        proc = subprocess.Popen([*TINI_COMMAND[:-1], str(runner), "--run", "--operations-only", "--timings"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, bufsize=0, start_new_session=True)
        try:
            ready = False
            for _ in range(6):
                assert select.select([proc.stdout], [], [], 5)[0], "worker startup timeout"
                line = proc.stdout.readline()
                if b"WORKER_PATCH_READY" in line:
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
            print("actual_image_runpy_worker_pickling_and_patch: PASS", flush=True)
            print("actual_image_worker_lock_and_tini_group_termination: PASS", flush=True)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()


def render_job():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--node", required=True, help="Idle worker node; never a control-plane node")
    parser.add_argument("--name", default="kometa-image-probe")
    args = parser.parse_args()
    launcher = Path(__file__).resolve().parents[1] / "kubernetes/main/apps/media/kometa/app/config/serial-run.py"
    release = launcher.parents[1] / "helmrelease.yaml"
    command_line = next(line.strip() for line in release.read_text().splitlines() if line.strip().startswith("command: ["))
    tini_command = json.loads(command_line.removeprefix("command: "))
    code = f"LAUNCHER_SOURCE={launcher.read_text()!r}\nTINI_COMMAND={tini_command!r}\n" + Path(__file__).read_text() + "\nprobe()\n"
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
