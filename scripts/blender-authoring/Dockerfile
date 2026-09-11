# syntax=docker/dockerfile:1
# Dedicated amd64 authoring worker. Build context: scripts/blender-authoring/.
# The same Debian/Node base and verified authoring pins as dev-env, without its
# agent credentials, cluster tools, startup scripts or mounted home directory.
FROM node:24-slim
ARG BLENDER_VERSION=4.5.13
ARG BLENDER_SERIES=4.5
ARG BLENDER_MCP_REVISION=5f8ddaf6e987c4aa0c3467fcc548838b28f64477
ARG BLENDER_REQUESTS_VERSION=2.34.2
ARG UV_VERSION=0.12.10
ARG GLTF_TRANSFORM_VERSION=4.5.0
ARG GLTF_VALIDATOR_VERSION=2.0.0-dev.3.10
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git xz-utils python3 python3-venv \
      xvfb xauth ffmpeg libgl1-mesa-dri libglx-mesa0 libegl1 \
      libx11-6 libxxf86vm1 libxfixes3 libxi6 libxrender1 libxrandr2 \
      libxkbcommon0 libsm6 libice6 libdbus-1-3 \
 && rm -rf /var/lib/apt/lists/*
RUN curl --retry 3 -fsSLo /tmp/uv.tar.gz \
      "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" \
 && tar -xzf /tmp/uv.tar.gz -C /tmp \
 && install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uv /usr/local/bin/uv \
 && rm -rf /tmp/uv.tar.gz /tmp/uv-x86_64-unknown-linux-gnu
RUN curl --retry 3 -fsSLo /tmp/blender.tar.xz \
      "https://download.blender.org/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
 && curl --retry 3 -fsSLo /tmp/blender.sha256 \
      "https://download.blender.org/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}.sha256" \
 && awk -v file="blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
      '$2 == file || $2 == "*" file {print $1 "  /tmp/blender.tar.xz"; found=1} END {if (!found) exit 1}' \
      /tmp/blender.sha256 | sha256sum -c - \
 && mkdir -p /opt/blender \
 && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 \
 && ln -s /opt/blender/blender /usr/local/bin/blender \
 && rm -f /tmp/blender.tar.xz /tmp/blender.sha256 \
 && /opt/blender/${BLENDER_SERIES}/python/bin/python3.11 -m ensurepip \
 && /opt/blender/${BLENDER_SERIES}/python/bin/python3.11 -m pip install --no-cache-dir \
      "requests==${BLENDER_REQUESTS_VERSION}" \
 && blender --version
RUN git init /opt/blender-authoring/blender-mcp \
 && git -C /opt/blender-authoring/blender-mcp remote add origin https://github.com/ahujasid/blender-mcp.git \
 && git -C /opt/blender-authoring/blender-mcp fetch --depth 1 origin "${BLENDER_MCP_REVISION}" \
 && git -C /opt/blender-authoring/blender-mcp checkout --detach FETCH_HEAD \
 && test "$(git -C /opt/blender-authoring/blender-mcp rev-parse HEAD)" = "${BLENDER_MCP_REVISION}" \
 && cmp /opt/blender-authoring/blender-mcp/addon.py /opt/blender-authoring/blender-mcp/src/blender_mcp/bundled/addon.py \
 && uv sync --project /opt/blender-authoring/blender-mcp --python /usr/bin/python3 --frozen --no-dev --no-editable \
 && rm -rf /opt/blender-authoring/blender-mcp/.git /root/.cache \
 && printf '%s\n' "${BLENDER_MCP_REVISION}" > /opt/blender-authoring/blender-mcp/REVISION \
 && npm install -g "@gltf-transform/cli@${GLTF_TRANSFORM_VERSION}" "gltf-validator@${GLTF_VALIDATOR_VERSION}" \
 && npm cache clean --force \
 && apt-get purge -y git curl \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
COPY desktop.py bootstrap.py service.py test-service.py test-unit.py /opt/blender-authoring/
RUN mkdir -p /workspace && chown 1000:1000 /workspace
ENV HOME=/tmp/blender-home \
    PYTHONDONTWRITEBYTECODE=1 \
    BLENDER_MCP_DISABLE_TELEMETRY=1 DISABLE_TELEMETRY=1 MCP_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 \
    UV_OFFLINE=1 \
    PATH=/opt/blender-authoring/blender-mcp/.venv/bin:/usr/local/bin:/usr/bin:/bin
USER 1000:1000
WORKDIR /workspace
EXPOSE 8000
ENTRYPOINT ["/opt/blender-authoring/blender-mcp/.venv/bin/python", "/opt/blender-authoring/service.py"]
