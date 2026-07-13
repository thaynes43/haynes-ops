# syntax=docker/dockerfile:1
# ghcr.io/thaynes43/dev-env — 24/7 in-cluster agent development environment.
#
# The kitchen-sink workhorse image for the dev-env saga (.agents/sagas/dev-env/):
# agent CLIs (Claude Code, Codex) + the full ops toolchain + code-server + the SDKs
# covering haynesnetwork (Node/TypeScript), haynes-ops (bash/python/k8s), and
# hass-sandbox (Python). Runs as a long-lived pod with a PVC home at /home/dev.
#
# UNLIKE the upgrade-shepherd image this is NOT a contained one-shot runner —
# containment lives in the deployment (operator-tier SA, enumerated egress CNP,
# Authentik-only ingress; saga Hard news #1), not in image minimalism.
#
# Debian/glibc (kubectl/claude glibc-linked); amd64-only (all Talos nodes x86_64).
FROM node:24-slim

# renovate: datasource=github-releases depName=kubernetes/kubernetes
ARG KUBECTL_VERSION=v1.35.5
# renovate: datasource=github-releases depName=fluxcd/flux2
ARG FLUX_VERSION=2.4.0
# renovate: datasource=github-releases depName=cli/cli
ARG GH_VERSION=2.95.0
# renovate: datasource=github-releases depName=helm/helm
ARG HELM_VERSION=3.19.0
# renovate: datasource=github-releases depName=kubernetes-sigs/kustomize extractVersion=^kustomize/v(?<version>.+)$
ARG KUSTOMIZE_VERSION=5.6.0
# renovate: datasource=github-releases depName=getsops/sops
ARG SOPS_VERSION=3.10.2
# renovate: datasource=github-releases depName=FiloSottile/age extractVersion=^v(?<version>.+)$
ARG AGE_VERSION=1.2.1
# renovate: datasource=github-releases depName=mikefarah/yq extractVersion=^v(?<version>.+)$
ARG YQ_VERSION=4.44.3
# renovate: datasource=github-releases depName=go-task/task extractVersion=^v(?<version>.+)$
ARG TASK_VERSION=3.40.0
# renovate: datasource=github-releases depName=opentofu/opentofu extractVersion=^v(?<version>.+)$
ARG OPENTOFU_VERSION=1.9.0
# renovate: datasource=github-releases depName=restic/restic extractVersion=^v(?<version>.+)$
ARG RESTIC_VERSION=0.17.3
# renovate: datasource=github-releases depName=coder/code-server extractVersion=^v(?<version>.+)$
ARG CODE_SERVER_VERSION=4.96.4
# renovate: datasource=github-releases depName=astral-sh/uv
ARG UV_VERSION=0.5.24
# renovate: datasource=npm depName=@anthropic-ai/claude-code
ARG CLAUDE_CODE_VERSION=2.1.207
# renovate: datasource=npm depName=@openai/codex
ARG CODEX_VERSION=0.144.3
# renovate: datasource=npm depName=tsx
ARG TSX_VERSION=4.19.2
# renovate: datasource=npm depName=pnpm
ARG PNPM_VERSION=10.0.0

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Base packages: dev essentials + python3 (hass-sandbox/makejinja) + tmux (agent
# sessions live in tmux) + ripgrep (agents search with rg) + build-essential
# (native npm modules, C extensions).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git openssh-client jq openssl bash \
      tar gzip unzip bzip2 xz-utils \
      tmux ripgrep less procps rsync file make build-essential \
      python3 python3-venv python3-pip \
 && rm -rf /var/lib/apt/lists/*

# kubectl (checksum-verified) — in-cluster via kubernetes.default.svc + the
# operator-tier SA (saga Decision #1).
RUN curl -fsSLo /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && curl -fsSLo /tmp/kubectl.sha256 \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" \
 && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
 && chmod +x /usr/local/bin/kubectl && rm -f /tmp/kubectl.sha256

# flux CLI.
RUN curl -fsSLo /tmp/flux.tar.gz \
      "https://github.com/fluxcd/flux2/releases/download/v${FLUX_VERSION}/flux_${FLUX_VERSION}_linux_amd64.tar.gz" \
 && tar -xzf /tmp/flux.tar.gz -C /usr/local/bin flux \
 && chmod +x /usr/local/bin/flux && rm -f /tmp/flux.tar.gz

# gh CLI.
RUN curl -fsSLo /tmp/gh.tar.gz \
      "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
 && tar -xzf /tmp/gh.tar.gz -C /tmp \
 && install -m 0755 "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh \
 && rm -rf /tmp/gh.tar.gz "/tmp/gh_${GH_VERSION}_linux_amd64"

# helm.
RUN curl -fsSLo /tmp/helm.tar.gz \
      "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz" \
 && tar -xzf /tmp/helm.tar.gz -C /tmp \
 && install -m 0755 /tmp/linux-amd64/helm /usr/local/bin/helm \
 && rm -rf /tmp/helm.tar.gz /tmp/linux-amd64

# kustomize (release tags are kustomize/vX.Y.Z — hence the URL-encoded path).
RUN curl -fsSLo /tmp/kustomize.tar.gz \
      "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" \
 && tar -xzf /tmp/kustomize.tar.gz -C /usr/local/bin kustomize \
 && chmod +x /usr/local/bin/kustomize && rm -f /tmp/kustomize.tar.gz

# sops + age (SOPS_AGE_KEY_FILE workflows, same as the Mac direnv setup).
RUN curl -fsSLo /usr/local/bin/sops \
      "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.amd64" \
 && chmod +x /usr/local/bin/sops \
 && curl -fsSLo /tmp/age.tar.gz \
      "https://github.com/FiloSottile/age/releases/download/v${AGE_VERSION}/age-v${AGE_VERSION}-linux-amd64.tar.gz" \
 && tar -xzf /tmp/age.tar.gz -C /tmp \
 && install -m 0755 /tmp/age/age /usr/local/bin/age \
 && install -m 0755 /tmp/age/age-keygen /usr/local/bin/age-keygen \
 && rm -rf /tmp/age.tar.gz /tmp/age

# yq.
RUN curl -fsSLo /usr/local/bin/yq \
      "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_amd64" \
 && chmod +x /usr/local/bin/yq

# go-task (the repo's Taskfile runner).
RUN curl -fsSLo /tmp/task.tar.gz \
      "https://github.com/go-task/task/releases/download/v${TASK_VERSION}/task_linux_amd64.tar.gz" \
 && tar -xzf /tmp/task.tar.gz -C /usr/local/bin task \
 && chmod +x /usr/local/bin/task && rm -f /tmp/task.tar.gz

# opentofu.
RUN curl -fsSLo /tmp/tofu.zip \
      "https://github.com/opentofu/opentofu/releases/download/v${OPENTOFU_VERSION}/tofu_${OPENTOFU_VERSION}_linux_amd64.zip" \
 && unzip -q /tmp/tofu.zip -d /tmp/tofu \
 && install -m 0755 /tmp/tofu/tofu /usr/local/bin/tofu \
 && rm -rf /tmp/tofu.zip /tmp/tofu

# restic (volsync debugging per the runbooks).
RUN curl -fsSLo /tmp/restic.bz2 \
      "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_amd64.bz2" \
 && bunzip2 /tmp/restic.bz2 \
 && install -m 0755 /tmp/restic /usr/local/bin/restic && rm -f /tmp/restic

# code-server (standalone) — the browser IDE, fronted by traefik-internal +
# Authentik (saga Decision #6); started by the HelmRelease, not baked as ENTRYPOINT.
RUN curl -fsSLo /tmp/code-server.tar.gz \
      "https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/code-server-${CODE_SERVER_VERSION}-linux-amd64.tar.gz" \
 && mkdir -p /usr/local/lib/code-server \
 && tar -xzf /tmp/code-server.tar.gz -C /usr/local/lib/code-server --strip-components=1 \
 && ln -s /usr/local/lib/code-server/bin/code-server /usr/local/bin/code-server \
 && rm -f /tmp/code-server.tar.gz

# uv — modern python env/dependency management (hass-sandbox, makejinja venvs).
RUN curl -fsSLo /tmp/uv.tar.gz \
      "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" \
 && tar -xzf /tmp/uv.tar.gz -C /tmp \
 && install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uv /usr/local/bin/uv \
 && install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/uvx \
 && rm -rf /tmp/uv.tar.gz /tmp/uv-x86_64-unknown-linux-gnu

# Agent CLIs + TS tooling (haynesnetwork runs tsx).
RUN npm install -g \
      "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
      "@openai/codex@${CODEX_VERSION}" \
      "tsx@${TSX_VERSION}" \
      "pnpm@${PNPM_VERSION}" \
 && npm cache clean --force

# github-app-token.sh (single source of truth) — used by the token-refresher
# sidecar/initContainer to mint short-lived ghs_ tokens from the haynes-ops-bot PEM
# (saga Decision #5). The agent containers never see the PEM, only /creds tokens.
WORKDIR /opt/dev-env
COPY scripts/github-app-token.sh ./github-app-token.sh
RUN chmod +x ./github-app-token.sh

# Non-root: rename the base image's uid-1000 'node' user to 'dev' with /home/dev —
# the PVC mounts over /home/dev at runtime (dev-init.sh repopulates config links).
RUN usermod -l dev -d /home/dev node && groupmod -n dev node \
 && mkdir -p /home/dev && chown dev:dev /home/dev

USER dev
WORKDIR /home/dev
ENV HOME=/home/dev \
    CLAUDE_CONFIG_DIR=/home/dev/.claude \
    CODEX_HOME=/home/dev/.codex \
    PATH=/opt/dev-env:/usr/local/bin:/usr/bin:/bin
ENTRYPOINT ["/bin/bash"]
