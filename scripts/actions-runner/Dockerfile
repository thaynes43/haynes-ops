# syntax=docker/dockerfile:1
# ghcr.io/thaynes43/actions-runner: ephemeral ARC jobs in private repositories.
# Upstream supplies Ubuntu 24.04, runner UID 1001, action Node runtimes,
# Docker CLI 29.8.2, Buildx, and the ARC container hooks. Preserve that layout.
FROM ghcr.io/actions/actions-runner:2.338.0@sha256:4ffadc0002b2581327e06101fc8c06cd189232baf79fe561fac9caeb76f5e807

LABEL org.opencontainers.image.source="https://github.com/thaynes43/haynes-ops" \
      org.opencontainers.image.description="Ephemeral ARC runner tools for private repositories"

USER root

# SDK versions remain the workflows' responsibility (setup-node, setup-go,
# setup-dotnet and setup-gcloud). Supply their native prerequisites, the tools
# formerly provided by GitHub-hosted Ubuntu, and Chromium's system libraries.
# Playwright 1.63.0's Ubuntu 24.04 tools/chromium package list:
# https://github.com/microsoft/playwright/blob/v1.63.0/packages/playwright-core/src/server/registry/nativeDeps.ts
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential gh pipx postgresql-client python3 python3-venv zstd \
      libicu74 libssl3t64 libkrb5-3 zlib1g \
      xvfb fonts-noto-color-emoji fonts-unifont libfontconfig1 libfreetype6 \
      xfonts-cyrillic xfonts-scalable fonts-liberation fonts-ipafont-gothic \
      fonts-wqy-zenhei fonts-tlwg-loma-otf fonts-freefont-ttf \
      libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 \
      libcairo2 libcups2t64 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0t64 \
      libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 \
      libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
 && rm -rf /var/lib/apt/lists/*

ENV PATH="/home/runner/.local/bin:${PATH}"

USER runner
