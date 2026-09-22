# syntax=docker/dockerfile:1
#
# ghcr.io/thaynes43/wyoming-whisper-gpu — a CUDA build of OHF-Voice's
# wyoming-faster-whisper, so Home Assistant's STT can run NVIDIA Parakeet on the
# A2000 next to vexa-whisper instead of faster-whisper `small` on a CPU.
#
# WHY WE BUILD THIS AT ALL. Upstream ships a `Dockerfile.gpu` but deliberately
# does not publish it ("Not published to Docker Hub - build it locally"), and no
# one else publishes an equivalent: `rhasspy/wyoming-whisper` is CPU-only, and
# `linuxserver/faster-whisper:gpu` installs the package with NO extras — no
# onnx-asr, no sherpa — so `guess_stt_library()` silently falls back to
# faster-whisper there. Parakeet on a GPU is only reachable from an image that
# carries the `onnx-asr-gpu` extra, which means this one.
#
# WHY PARAKEET COMES IN THROUGH onnx-asr AND NOT sherpa. `--stt-library auto`
# picks sherpa for English and sherpa's own Parakeet models — but upstream's
# README is explicit that sherpa runs on the CPU *even in the GPU image*:
# sherpa-onnx bundles its own onnxruntime, and two CUDA-enabled onnxruntime
# builds in one process segfault. So the GPU path is `--stt-library onnx-asr`
# with an explicit `--model nemo-parakeet-tdt-0.6b-v2` (that library's own
# default is `gigaam-v2-rnnt`, a Russian model). See the HelmRelease args.
#
# WHY IT IS SLIMMER THAN UPSTREAM'S. Upstream installs
# [zeroconf,transformers,sherpa,onnx-asr-gpu,qwen3-asr-gpu,hass] and lands at
# ~10.7 GB, most of it the CUDA torch wheel that only the `transformers` backend
# uses. We pin one backend, so torch, sherpa and qwen3-asr are dead weight:
# dropping them takes the image to roughly a third of that. faster-whisper comes
# along regardless (it is a core dependency) and is CUDA-capable here, since the
# base supplies the cuDNN 9 that CTranslate2 links against — so switching
# `--model`/`--stt-library` back to a Whisper model needs no rebuild.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04@sha256:ac55d124da4882b497f732d8dfd9a702d5447a5f29d08d56da6f64f0a1eb34bc

# renovate: datasource=pypi depName=wyoming-faster-whisper
ARG WYOMING_FASTER_WHISPER_VERSION=3.8.1
# Renovate rewrites every occurrence of the old version inside the block it
# matched, comments included — never put a version number in a comment BETWEEN
# the annotation and its ARG. See scripts/dev-env/Dockerfile.

# onnxruntime-gpu on PyPI switched from CUDA 12 to CUDA 13 in 1.27.0 (1.26.0 is
# the last CUDA 12 build). Unpinned it installs a wheel that wants
# libcublasLt.so.13, finds only CUDA 12 on this base, and quietly drops back to
# the CPU provider — the transcript still comes out, just ten times slower, with
# nothing in the logs saying why. This bound is upstream's own.
ARG ONNXRUNTIME_GPU_VERSION=">=1.19,<1.27"

# NVIDIA_*: the pod runs `runtimeClassName: nvidia` with NO `nvidia.com/gpu`
# resource request (vexa-whisper holds the node's only unit), so the device
# plugin never injects these and the container would see no GPU without them.
# Same arrangement as our ComfyUI and Ollama images.
#
# HF_HOME/XDG_CACHE_HOME keep the ~2.5 GB Parakeet download on the /data volume
# instead of the container filesystem, so a restart does not re-fetch it. They
# have to be env vars: the libraries read them at import time, before any of our
# arguments are parsed.
#
# HF_HUB_DISABLE_SYMLINKS is not a preference, it is what makes Parakeet load at
# all. huggingface_hub's cache stores every file as a symlink into
# `blobs/<first two hex of its sha>/`, so a model and its external-data sibling
# (`encoder-model.onnx` and `encoder-model.onnx.data`) resolve into DIFFERENT
# blob subdirectories — and onnxruntime, which validates that external data
# cannot resolve outside the model's directory, refuses the session with
# "External data path ... escapes both model directory ... and real model
# directory". Copying instead of symlinking puts the pair back side by side.
# Costs one extra copy of the model on disk. Found the hard way on 3.8.1-1.
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    HF_HOME=/data \
    XDG_CACHE_HOME=/data \
    HF_HUB_DISABLE_SYMLINKS=1 \
    PATH=/venv/bin:$PATH

RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
    && python3 -m venv /venv \
    && /venv/bin/pip install --no-cache-dir -U setuptools wheel \
    && /venv/bin/pip install --no-cache-dir \
        "wyoming-faster-whisper[onnx-asr-gpu]==${WYOMING_FASTER_WHISPER_VERSION}" \
    \
    # faster-whisper depends on onnxruntime (for its bundled Silero VAD), so the
    # CPU package arrives no matter what the extras ask for. Both packages
    # install the same `onnxruntime` module and whichever lands second wins:
    # left alone the CPU build overwrites onnxruntime-gpu's pybind extension and
    # CUDAExecutionProvider disappears — silently, since the CPU provider still
    # loads every model. Reconcile by removing both and reinstalling the GPU one
    # alone; onnxruntime-gpu provides the module faster-whisper imports, so only
    # pip's metadata is left unhappy. This is upstream's fix, verbatim in intent.
    && /venv/bin/pip uninstall -y onnxruntime onnxruntime-gpu \
    && /venv/bin/pip install --no-cache-dir "onnxruntime-gpu${ONNXRUNTIME_GPU_VERSION}" \
    \
    # Fail the build, not the deployment, if the reconcile above ever stops
    # producing a CUDA-capable onnxruntime. get_available_providers() reports
    # what the wheel was compiled with, so it answers on the GPU-less builder.
    && /venv/bin/python3 -c "import onnxruntime as rt; p = rt.get_available_providers(); print('providers:', p); assert 'CUDAExecutionProvider' in p, 'CUDA provider missing'" \
    \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 10300

# No docker_run.sh: that script only exists to supply --uri/--data-dir/--device
# defaults, and the HelmRelease passes all three explicitly.
ENTRYPOINT ["/venv/bin/python3", "-m", "wyoming_faster_whisper"]
CMD ["--uri", "tcp://0.0.0.0:10300", "--data-dir", "/data", "--device", "cuda"]
