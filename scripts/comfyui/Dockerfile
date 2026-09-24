# syntax=docker/dockerfile:1
#
# ghcr.io/thaynes43/comfyui — our own ComfyUI image.
#
# Replaces ghcr.io/ai-dock/comfyui (dead upstream: last pushed 2024-11, Python
# 3.10, needs privileged: true). That image was kept usable by an init script
# that git-checked-out a newer ComfyUI tag over the baked code and pip-installed
# ~5.7 GB of dependencies onto the PVC, read back through PYTHONPATH. The
# layering broke the v0.37.0 upgrade: `pip install --target` leaves the previous
# versions' *.dist-info behind, importlib.metadata then reported a stale
# tokenizers, and ComfyUI refused to start. Here the code and its dependencies
# are baked, immutable and versioned with the image; only models and user data
# live on volumes.
#
# Python 3.13 — ComfyUI's own recommendation, and the newest interpreter for
# which EVERY pinned dependency ships an explicit cp313 manylinux x86_64 wheel:
# comfy-kitchen 0.2.35, comfy-aimdo 0.5.5, comfy-angle, av, tokenizers,
# safetensors, blake3, sentencepiece, scipy, numpy, Pillow and the torch stack
# all have one. 3.14 also resolves, but only by leaning on abi3 forward
# compatibility for six of those, and torchvision 0.27.0 declares
# `requires-python != 3.14.1` — a base image landing on that exact patch would
# fail the install with a confusing "no matching distribution". 3.12 works and
# buys nothing.
FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2 AS runtime

# renovate: datasource=github-releases depName=Comfy-Org/ComfyUI
ARG COMFYUI_VERSION=v0.37.0
# Renovate rewrites EVERY occurrence of the old version inside the block it
# matched, comments included — so never put a version number in a comment
# BETWEEN the annotation and its ARG. See scripts/dev-env/Dockerfile.

# Caches: ComfyUI core writes nothing to $HOME, but transformers,
# huggingface_hub and torch sit on its startup import path and do. Everything
# mutable is aimed at /tmp, which is a tmpfs/emptyDir, never the read-only root.
#
# NVIDIA_*: the pod runs runtimeClassName: nvidia with NO nvidia.com/gpu
# resource request, so the device plugin never injects these. Without them the
# container sees no GPU unless it is privileged — which is exactly what this
# image exists to stop needing. Same arrangement as the Ollama image.
ENV COMFYUI_ROOT=/opt/ComfyUI \
    COMFYUI_VERSION=${COMFYUI_VERSION} \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DO_NOT_TRACK=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HOME=/tmp/comfy-home \
    TMPDIR=/tmp \
    XDG_CACHE_HOME=/tmp/comfy-cache \
    HF_HOME=/tmp/comfy-cache/huggingface \
    TORCH_HOME=/tmp/comfy-cache/torch \
    TORCHINDUCTOR_CACHE_DIR=/tmp/comfy-cache/inductor \
    TRITON_CACHE_DIR=/tmp/comfy-cache/triton \
    MPLCONFIGDIR=/tmp/comfy-cache/matplotlib \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

# git is a BUILD dependency only, so it is installed, used and purged in one
# layer (purging in a later layer would leave it in the image).
#
# No libgl1/libglib2.0-0: nothing on ComfyUI's startup import path dlopens
# desktop OpenGL. comfy_extras/nodes_glsl.py preloads the ANGLE libraries
# bundled inside the comfy-angle wheel before importing PyOpenGL, and
# PyAV/Pillow ship their own native libraries. A node that did need one would
# fail soft (ComfyUI catches per-node import errors), which is why the smoke
# test asserts the node classes the stored workflows use are actually present.
#
# The three `test` guards are load-bearing, not decoration:
#   input/         folder_paths.py makedirs it at IMPORT time, before
#                  --input-directory is applied; that one write cannot be
#                  redirected by any flag. The shipped example.png makes the
#                  os.path.exists guard short-circuit.
#   custom_nodes/  main.py and nodes.py both os.listdir it with no try/except.
#                  It holds only what ComfyUI itself ships (the websocket image
#                  save node); no ComfyUI-Manager, no third-party nodes.
#   user/, temp/   must NOT exist. A shipped user/comfyui.db would make
#                  app/database/db.py os.replace it in place on first boot —
#                  into the read-only tree.
RUN mkdir -p /tmp/comfy-home /tmp/comfy-cache \
 && apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git \
 && git clone --depth 1 --branch "${COMFYUI_VERSION}" \
      https://github.com/Comfy-Org/ComfyUI.git "${COMFYUI_ROOT}" \
 && rm -rf "${COMFYUI_ROOT}/.git" \
           "${COMFYUI_ROOT}/.github" \
           "${COMFYUI_ROOT}/.ci" \
           "${COMFYUI_ROOT}/tests" \
           "${COMFYUI_ROOT}/tests-unit" \
 && test -d "${COMFYUI_ROOT}/input" \
 && test -d "${COMFYUI_ROOT}/custom_nodes" \
 && test ! -e "${COMFYUI_ROOT}/user" \
 && test ! -e "${COMFYUI_ROOT}/temp" \
 && apt-get purge -y --auto-remove git \
 && rm -rf /var/lib/apt/lists/*

COPY constraints.txt /tmp/comfyui-build/

# The torch stack first, then ComfyUI's requirements under the same pins so a
# transitive dependency cannot drag in a different torch. ComfyUI leaves torch,
# torchvision and torchaudio unpinned upstream.
RUN pip install --no-cache-dir --disable-pip-version-check \
      -r /tmp/comfyui-build/constraints.txt \
 && pip install --no-cache-dir --disable-pip-version-check \
      -c /tmp/comfyui-build/constraints.txt \
      -r "${COMFYUI_ROOT}/requirements.txt" \
 && rm -rf /tmp/comfyui-build /root/.cache

# Default-PyPI torch is a CUDA build (CUDA 13.0, via the nvidia-*-cu13
# dependency set), but a resolver change or an index override could silently
# substitute the CPU-only wheel, and that failure would only surface on the node
# as "no GPU detected". Fail the build instead.
RUN python -c "import torch, sys; cuda = torch.version.cuda; print('torch', torch.__version__, 'cuda', cuda); sys.exit(0 if cuda else 'FATAL: torch wheel is a CPU-only build')"

# Precompile while the tree is still writable. At runtime
# PYTHONDONTWRITEBYTECODE=1 stops Python from trying (and silently failing) to
# write bytecode into the read-only tree on every cold start.
RUN python -m compileall -q -j 0 "${COMFYUI_ROOT}" >/dev/null 2>&1 || true

COPY entrypoint.py provision.py /opt/comfyui-tools/

# uid/gid 1000 to match the workspace PVC and the NFS shares. Never root.
# /config is where the workflow ConfigMap and the optional models.yaml mount.
RUN groupadd --gid 1000 comfy \
 && useradd --uid 1000 --gid 1000 --home-dir /workspace --shell /usr/sbin/nologin comfy \
 && mkdir -p /workspace /config /tmp/comfy-home /tmp/comfy-cache \
 && chown -R 1000:1000 /workspace /tmp/comfy-home /tmp/comfy-cache \
 && chmod 0755 /opt/comfyui-tools/entrypoint.py /opt/comfyui-tools/provision.py \
 && test ! -e /opt/comfyui-tools/TEST_IMAGE

USER 1000:1000
WORKDIR /opt/ComfyUI
EXPOSE 8188
ENTRYPOINT ["python", "/opt/comfyui-tools/entrypoint.py"]
CMD ["serve"]

# ---------------------------------------------------------------------------
# test — the CI smoke image. Never published.
# ---------------------------------------------------------------------------
FROM runtime AS test
USER root
# Declared here, with its default, because ARG defaults do NOT cross stages —
# a bare `ARG PYTEST_VERSION` would expand to the empty string.
# renovate: datasource=pypi depName=pytest
ARG PYTEST_VERSION=9.1.1
RUN pip install --no-cache-dir --disable-pip-version-check "pytest==${PYTEST_VERSION}"
COPY test_provision.py /opt/comfyui-tools/
# testdata/deployed/{workflows,api-workflows} are the app's REAL workflow JSONs,
# staged into the build context by .github/workflows/comfyui-build.yml so the
# parser is tested against the files actually deployed rather than against a
# copy that can drift. COMFYUI_TEST_STRICT_FIXTURES=1 turns a missing fixture
# into a failure instead of a skip.
COPY testdata /opt/comfyui-tools/testdata
RUN touch /opt/comfyui-tools/TEST_IMAGE \
 && chmod 0444 /opt/comfyui-tools/TEST_IMAGE \
 && chown -R root:root /opt/comfyui-tools
USER 1000:1000
