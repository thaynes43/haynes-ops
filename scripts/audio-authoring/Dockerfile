# syntax=docker/dockerfile:1
FROM python:3.11-slim AS runtime

ARG STABLE_AUDIO_REVISION=779434a908193105335fd8d833418603625b2859
ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/tmp/audio-home \
    HF_HOME=/tmp/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_DISABLE_XET=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    DO_NOT_TRACK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates ffmpeg git libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt weights-revision.patch /tmp/audio-build/
RUN python -m venv /opt/audio-authoring/venv \
 && /opt/audio-authoring/venv/bin/pip install --no-cache-dir --disable-pip-version-check \
      -r /tmp/audio-build/requirements.txt \
 && git init /opt/audio-authoring/stable-audio-3 \
 && git -C /opt/audio-authoring/stable-audio-3 remote add origin https://github.com/Stability-AI/stable-audio-3.git \
 && git -C /opt/audio-authoring/stable-audio-3 fetch --depth 1 origin "${STABLE_AUDIO_REVISION}" \
 && git -C /opt/audio-authoring/stable-audio-3 checkout --detach FETCH_HEAD \
 && test "$(git -C /opt/audio-authoring/stable-audio-3 rev-parse HEAD)" = "${STABLE_AUDIO_REVISION}" \
 && git -C /opt/audio-authoring/stable-audio-3 apply /tmp/audio-build/weights-revision.patch \
 && rm -rf /opt/audio-authoring/stable-audio-3/optimized/tflite/models/tflite \
 && ln -s /models/tflite /opt/audio-authoring/stable-audio-3/optimized/tflite/models/tflite \
 && rm -rf /opt/audio-authoring/stable-audio-3/.git \
            /opt/audio-authoring/stable-audio-3/optimized/mlx \
            /opt/audio-authoring/stable-audio-3/optimized/tensorRT \
            /opt/audio-authoring/stable-audio-3/optimized/tflite/build \
            /opt/audio-authoring/stable-audio-3/optimized/tflite/sa3-gradio \
            /opt/audio-authoring/stable-audio-3/optimized/tflite/requirements-gradio.txt \
            /opt/audio-authoring/stable-audio-3/optimized/tflite/scripts/sa3_gradio.py \
            /root/.cache /tmp/audio-build \
 && apt-get purge -y git \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

COPY entrypoint.py prefetch.py service.py /opt/audio-authoring/
RUN mkdir -p /models /workspace /tmp/audio-home /tmp/huggingface \
 && chown -R 1000:1000 /models /workspace /tmp/audio-home /tmp/huggingface \
 && chmod 0755 /opt/audio-authoring/entrypoint.py /opt/audio-authoring/prefetch.py \
 && test ! -e /opt/audio-authoring/TEST_IMAGE

ENV PATH=/opt/audio-authoring/venv/bin:/usr/local/bin:/usr/bin:/bin
USER 1000:1000
WORKDIR /workspace
EXPOSE 8000
ENTRYPOINT ["python", "/opt/audio-authoring/entrypoint.py"]
CMD ["serve"]

FROM runtime AS test
USER root
COPY fixture_backend.py test_service.py /opt/audio-authoring/
RUN touch /opt/audio-authoring/TEST_IMAGE \
 && chmod 0444 /opt/audio-authoring/TEST_IMAGE \
 && chown root:root /opt/audio-authoring/TEST_IMAGE /opt/audio-authoring/fixture_backend.py /opt/audio-authoring/test_service.py
USER 1000:1000
