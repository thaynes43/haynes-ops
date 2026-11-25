FROM ghcr.io/ggml-org/llama.cpp:server-cuda12-b12679@sha256:fbd1c7239e4a14fdb973aa114add9ab7057f10ff632f6147890ff1843a607f3b AS llama
FROM docker.io/library/python:3.12.16-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64 AS python
FROM docker.io/library/gcc:14-bookworm@sha256:5e927c284bf55a7dc796262e311a0703344f62f41f5621eb56843111b1d37e15 AS binding

WORKDIR /build
RUN curl --fail --silent --show-error --location --proto '=https' \
      https://codeload.github.com/ggml-org/llama.cpp/tar.gz/af5c58347fdbc2cd19e65b278e81c8f528f8a175 \
      --output llama.tar.gz \
    && echo 'b17f07c9bef5b3a1b5368f445c99d2bde6265164d2a30866a616cf9f6ebe02f3  llama.tar.gz' | sha256sum --check \
    && mkdir source && tar -xzf llama.tar.gz -C source --strip-components=1
COPY --from=llama /app/libllama.so* /app/libllama-common.so* /app/libggml.so* /app/libggml-base.so* /app/libmtmd.so* /build/libs/
COPY scripts/studio-local-model/backend.cpp /build/backend.cpp
RUN g++ -std=c++17 -fPIC -shared -O2 -Wall -Wextra -Wno-unused-function \
    -Isource/common -Isource/include -Isource/ggml/include -Isource/tools/mtmd -Isource/vendor/stb \
    backend.cpp -L/build/libs -Wl,--no-undefined,--allow-shlib-undefined \
    -lllama-common -lmtmd -lllama -lggml -lggml-base -o libstudio_local_backend.so

FROM llama AS runtime
LABEL org.opencontainers.image.source="https://github.com/thaynes43/haynes-ops"
LABEL org.opencontainers.image.title="Haynes Studio local model"
COPY --from=python /usr/local/bin/python3* /usr/local/bin/
COPY --from=python /usr/local/lib/libpython3.12.so* /usr/local/lib/
COPY --from=python /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=python /usr/local/lib/libpython3.so /usr/local/lib/
ENV LD_LIBRARY_PATH=/app:/usr/local/lib:${LD_LIBRARY_PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CUDA_CACHE_DISABLE=1
WORKDIR /opt/studio-local-model
COPY --from=binding /build/libstudio_local_backend.so /build/source/models/templates/Qwen3.5-4B.jinja /build/source/LICENSE ./
COPY kubernetes/main/apps/ai/wan-video/app/*.py ./
COPY scripts/studio-local-model/startup_check.py ./
COPY kubernetes/main/apps/ai/wan-video/local-model-pending/weights/weights.json ./
USER 1000:1000
HEALTHCHECK NONE
ENTRYPOINT ["python3", "-m", "studio_local_backend"]
CMD ["serve"]
