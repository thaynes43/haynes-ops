# syntax=docker/dockerfile:1
FROM pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime@sha256:831247999fbf7e08f61b3e39f6d77ee434f38f6f07f769d00db451e853878067 AS runtime
LABEL org.opencontainers.image.source="https://github.com/thaynes43/haynes-ops" \
      org.opencontainers.image.description="Isolated pinned ControlFoley TV2A inference"
WORKDIR /opt/controlfoley-tools
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cu121 -r requirements.txt && python -m pip check
# Keep original source and embedded license notices intact, including Apache-2.0,
# Synchformer and BigVGAN licenses. The adapter is a separate modified inference path.
ADD --checksum=sha256:8fdd3577a2b59c9d31b2c6d76bf6ee359b2db8057bcf62ff7d24c90875b701ae https://codeload.github.com/xiaomi-research/ControlFoley/tar.gz/b6c902888d45d75f022e253e3db29836360a3f82 /tmp/controlfoley.tar.gz
RUN mkdir /opt/controlfoley && tar -xzf /tmp/controlfoley.tar.gz --strip-components=1 -C /opt/controlfoley && rm /tmp/controlfoley.tar.gz
COPY run.py provision.py test_backend.py service.py test_service.py ./
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 CONTROLFOLEY_SOURCE_DIR=/opt/controlfoley
CMD ["python", "/opt/controlfoley-tools/service.py"]
FROM runtime AS test
CMD ["python", "-m", "unittest", "discover", "-s", "/opt/controlfoley-tools", "-p", "test_*.py", "-v"]
