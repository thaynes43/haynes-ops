"""immich-machine-learning VRAM guard (haynes-ops).

Why this file exists: Immich ML v3.2.4 builds its CUDA sessions with fixed
provider options (machine-learning/immich_ml/sessions/ort.py:134-135,
{"arena_extend_strategy": "kSameAsRequested", "device_id": ...}) and no
environment variable reaches them, so nothing in Immich's own settings can
bound how much GPU memory ONNX Runtime takes. Measured on talosm05's shared
A2000 (2026-10-06), OCR alone, one request at a time, grew the ML process from
2.0 to 4.1 GB in 90 minutes, and all three queues together filled the card
(11.9 of 12.3 GB, about 150 CUDA out-of-memory errors in 10 minutes).

The cause is ONNX Runtime's CUDA memory arena, which never gives memory back
to the card. Each session keeps every block it ever allocated, so its size is
its high-water mark. OCR input shapes change with every image and every line of
text, so blocks of new sizes keep being added (2.0 -> 4.1 GB above). With the
default of one request thread per CPU (20 on talosm05), each inference in
flight also holds its own working memory at the same time.

Python imports `sitecustomize` at start-up from any directory on PYTHONPATH
(the HelmRelease puts this one first). This module hooks the first import of
`onnxruntime` and changes two public onnxruntime calls. Immich's code is not
touched.
  * InferenceSession(...): for CUDAExecutionProvider only, it adds
    gpu_mem_limit=<IMMICH_ML_CUDA_MEM_LIMIT_MB> MiB, the hard cap on that
    session's arena. It is added with setdefault, so if Immich ever sets the
    option itself, Immich's value wins.
  * Session.run(...): on a CUDA session it adds the run option
    memory.enable_memory_arena_shrinkage=gpu:<device>. ONNX Runtime then frees
    every arena block that is no longer in use at the end of each run, even a
    failed run (onnxruntime 1.26.0 inference_session.cc:3226-3232, 3347-3349;
    BFCArena::Shrink, bfc_arena.cc:488). Model weights stay; a run's working
    memory goes back to the card.

  * Session.run(...) on a CUDA session also takes one process-wide lock, so
    only one model runs on the GPU at a time whatever
    MACHINE_LEARNING_REQUEST_THREADS says, and then, with
    IMMICH_ML_GPU_DUTY_CYCLE=d below 1, sleeps (1/d - 1) times as long as the
    run took before it lets the next run start. ML then keeps the GPU busy at
    most a fraction d of the time, and the voice models that share the card
    (ollama-assist02) get the rest. Measured 2026-10-06: without it, a busy
    ML made ollama-assist02's answers 1.7x slower.

So the process holds every loaded model's weights, plus one run's working
memory up to gpu_mem_limit, plus the CUDA context. A run that would go past
gpu_mem_limit fails in ONNX Runtime ("Failed to allocate memory") instead of
taking memory from the other processes on the card. The ImmichMlCudaOutOfMemory
alert counts those lines.

Every line it logs starts with "immich-ml-vram-guard". A failure to apply the
guard logs "immich-ml-vram-guard ERROR" (alerted), and ML then runs unguarded.
Runbook: .agents/runbooks/immich-ml-backlog.md, section "The VRAM guard".
"""

import importlib.abc
import importlib.util
import os
import sys
import threading
import time

_TAG = "immich-ml-vram-guard"
_CUDA = "CUDAExecutionProvider"
_SHRINK_KEY = "memory.enable_memory_arena_shrinkage"


def _log(level, message):
    # stderr reaches the container log (and Loki) without touching Immich's
    # logging setup, which is configured after this module runs.
    sys.stderr.write(f"{_TAG} {level}: {message}\n")
    sys.stderr.flush()


_GPU_LOCK = threading.Lock()
_MAX_PAUSE_S = 30.0


def _duty_cycle():
    """IMMICH_ML_GPU_DUTY_CYCLE as a float in (0, 1]; 1 (the default) means no pause."""
    try:
        duty = float(os.environ.get("IMMICH_ML_GPU_DUTY_CYCLE", "1") or "1")
    except ValueError:
        _log("ERROR", "IMMICH_ML_GPU_DUTY_CYCLE is not a number; using 1 (no pause)")
        return 1.0
    return duty if 0 < duty <= 1 else 1.0


def pause_after(run_seconds, duty):
    """How long to wait after a run of run_seconds so the GPU is busy at most `duty` of the time."""
    if duty >= 1:
        return 0.0
    return min(run_seconds * (1 / duty - 1), _MAX_PAUSE_S)


def _cuda_options():
    limit_mb = int(os.environ.get("IMMICH_ML_CUDA_MEM_LIMIT_MB", "0") or "0")
    return {"gpu_mem_limit": str(limit_mb * 1024 * 1024)} if limit_mb > 0 else {}


def guard_provider_options(providers, provider_options):
    """Return (providers, provider_options, cuda_device) with the CUDA options added.

    Accepts every form InferenceSession accepts: provider names with a parallel
    provider_options list, or (name, options) tuples. Never mutates the
    caller's lists or dicts. cuda_device is None when CUDA is not requested.
    """
    if not providers:
        return providers, provider_options, None
    extra = _cuda_options()
    providers = list(providers)
    provider_options = list(provider_options) if provider_options is not None else None
    cuda_device = None
    for i, provider in enumerate(providers):
        if isinstance(provider, tuple):
            name, options = provider
            if name == _CUDA:
                merged = {**extra, **dict(options)}
                providers[i] = (name, merged)
                cuda_device = str(merged.get("device_id", "0"))
        elif provider == _CUDA:
            if provider_options is None:
                provider_options = [{} for _ in providers]
            merged = {**extra, **dict(provider_options[i])}
            provider_options[i] = merged
            cuda_device = str(merged.get("device_id", "0"))
    return providers, provider_options, cuda_device


def _patch(ort):
    from onnxruntime.capi import onnxruntime_inference_collection as oic

    session_init = oic.InferenceSession.__init__
    session_run = oic.Session.run

    def __init__(self, path_or_bytes, sess_options=None, providers=None, provider_options=None, **kwargs):
        providers, provider_options, cuda_device = guard_provider_options(providers, provider_options)
        session_init(self, path_or_bytes, sess_options, providers, provider_options, **kwargs)
        # Only shrink when CUDA really got the session (it can fall back to CPU).
        if cuda_device is not None and _CUDA in self.get_providers():
            self._vram_guard_device = f"gpu:{cuda_device}"
            name = path_or_bytes if isinstance(path_or_bytes, (str, os.PathLike)) else "<bytes>"
            _log("INFO", f"guarding CUDA session {os.fspath(name)}")

    duty = _duty_cycle()

    def run(self, output_names, input_feed, run_options=None):
        device = getattr(self, "_vram_guard_device", None)
        if device is None:
            return session_run(self, output_names, input_feed, run_options)
        if run_options is None:
            run_options = ort.RunOptions()
        run_options.add_run_config_entry(_SHRINK_KEY, device)
        with _GPU_LOCK:
            start = time.monotonic()
            try:
                return session_run(self, output_names, input_feed, run_options)
            finally:
                pause = pause_after(time.monotonic() - start, duty)
                if pause > 0:
                    time.sleep(pause)

    oic.InferenceSession.__init__ = __init__
    oic.Session.run = run
    limit = _cuda_options().get("gpu_mem_limit")
    _log(
        "INFO",
        f"active (onnxruntime {ort.__version__}): CUDA sessions get "
        f"gpu_mem_limit={int(limit) // 1048576 if limit else 'none'} MiB, "
        f"an arena shrink after every run, one run at a time, GPU duty cycle {duty:g}",
    )


class _OnnxruntimeHook(importlib.abc.MetaPathFinder):
    """Patch onnxruntime right after its first import, in whichever process imports it."""

    def find_spec(self, name, path=None, target=None):
        if name != "onnxruntime":
            return None
        sys.meta_path.remove(self)  # one shot; the normal finders do the lookup
        spec = importlib.util.find_spec(name)
        if spec is None or spec.loader is None:
            _log("ERROR", "onnxruntime not found; ML runs unguarded")
            return spec
        exec_module = spec.loader.exec_module

        def exec_and_patch(module):
            exec_module(module)
            try:
                _patch(module)
            except Exception as error:  # never stop ML from starting
                _log("ERROR", f"could not patch onnxruntime, ML runs unguarded: {error!r}")

        spec.loader.exec_module = exec_and_patch
        return spec


if os.environ.get("IMMICH_ML_VRAM_GUARD", "true").lower() != "false":
    sys.meta_path.insert(0, _OnnxruntimeHook())
