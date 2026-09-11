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
ARG KUBECTL_VERSION=v1.37.0
# renovate: datasource=github-releases depName=fluxcd/flux2
ARG FLUX_VERSION=2.9.5
# renovate: datasource=github-releases depName=cli/cli
ARG GH_VERSION=2.100.0
# renovate: datasource=github-releases depName=helm/helm
ARG HELM_VERSION=3.21.4
# renovate: datasource=github-releases depName=kubernetes-sigs/kustomize extractVersion=^kustomize/v(?<version>.+)$
ARG KUSTOMIZE_VERSION=5.8.1
# renovate: datasource=github-releases depName=getsops/sops
ARG SOPS_VERSION=3.13.3
# renovate: datasource=github-releases depName=FiloSottile/age extractVersion=^v(?<version>.+)$
ARG AGE_VERSION=1.3.2
# renovate: datasource=github-releases depName=mikefarah/yq extractVersion=^v(?<version>.+)$
ARG YQ_VERSION=4.53.6
# renovate: datasource=github-releases depName=go-task/task extractVersion=^v(?<version>.+)$
ARG TASK_VERSION=3.53.1
# renovate: datasource=github-releases depName=opentofu/opentofu extractVersion=^v(?<version>.+)$
ARG OPENTOFU_VERSION=1.12.6
# renovate: datasource=github-releases depName=restic/restic extractVersion=^v(?<version>.+)$
ARG RESTIC_VERSION=0.19.1
# talosctl — read-only node triage (dmesg/logs/get) via apid; MUST track the cluster's
# Talos version (nodes run v1.13.3). Same style as kubectl: the `v` lives in the ARG.
# renovate: datasource=github-releases depName=siderolabs/talos
ARG TALOSCTL_VERSION=v1.14.0
# omnictl — read-only Omni machine/cluster status; tracks the self-hosted Omni image
# (ghcr.io/siderolabs/omni). renovate: datasource=github-releases depName=siderolabs/omni
ARG OMNICTL_VERSION=v1.11.0
# renovate: datasource=github-releases depName=coder/code-server extractVersion=^v(?<version>.+)$
ARG CODE_SERVER_VERSION=4.135.0
# renovate: datasource=github-releases depName=astral-sh/uv
ARG UV_VERSION=0.12.10
# renovate: datasource=npm depName=@anthropic-ai/claude-code
# 2.1.251 (2026-08-29): catching up 35 releases of drift. 2.1.217 was pinned by
# hand on 2026-07-22 and never moved again because NOTHING was watching this ARG
# — the `# renovate:` line above it was decoration until customManagers.json5
# grew a Dockerfile manager the same day. The drift was not cosmetic: claude
# resolves the `opus` MODEL ALIAS client-side from a table baked into the binary,
# so 2.1.217 served claude-opus-4-8 for every `--model opus` session (agent-run's
# picker, and the dev-env-ops curation lane after #2663) long after Opus 5 shipped.
# 2.1.263 (2026-09-01): Fable 5.1. `claude-fable-5-1` needs claude-code >=2.1.255 —
# an older CLI REJECTS the id outright ("Claude Code does not support this model";
# no fallback, the session never starts), so this bump is load-bearing for the
# dev-env default model, not cosmetic. 2.1.257 repointed the `fable` alias to 5.1
# and made --effort lift a model's default-effort hold per session; 2.1.263 fixes
# a 2.1.255 launch regression and a remote-session "user messages must have
# non-empty content" failure after a re-sent permission approval.
ARG CLAUDE_CODE_VERSION=2.1.263
# Codex is installed at BOOT as the managed STANDALONE (dev-init.sh), not via npm —
# only the standalone can run `codex remote-control`. This pins that standalone
# version; @openai/codex npm releases track the same numbers as the rust-v* GitHub
# releases the standalone installer pulls, so the npm datasource still tracks it.
# 0.153.4 (2026-09-04): GPT-6-Astra. Codex serves its model catalog PER CLIENT
# VERSION, so on 0.151.0 `gpt-6-astra` does not exist at all — not in the
# agent-run picker, not in the remote-control phone picker. 0.153.1 shipped the
# model hidden; 0.153.4 is the first release that lists it in the picker and makes
# it the bundled default. dev-init installs whatever this pins at boot, so the
# 0.153.4 installed by hand on the PVC on 2026-09-06 reverts on the next pod roll
# until this lands.
# renovate: datasource=npm depName=@openai/codex
ARG CODEX_VERSION=0.153.4
# renovate: datasource=npm depName=tsx
ARG TSX_VERSION=4.23.13
# renovate: datasource=npm depName=pnpm
# 11.26.0: haynesnetwork pins packageManager pnpm@11.26.0 + engines pnpm>=11 (pnpm 10
# hard-refuses to install there). Keep >=11 unless every repo drops the pin.
ARG PNPM_VERSION=11.26.0
# renovate: datasource=npm depName=@playwright/mcp
ARG PLAYWRIGHT_MCP_VERSION=0.0.80
# Blender 4.5 LTS is supported through July 2027. Keep the release series and
# archive version together; verify the official checksum before extraction.
ARG BLENDER_VERSION=4.5.13
ARG BLENDER_SERIES=4.5
# One immutable revision for the server, bundled add-on, and upstream uv.lock.
ARG BLENDER_MCP_REVISION=5f8ddaf6e987c4aa0c3467fcc548838b28f64477
# renovate: datasource=pypi depName=requests
ARG BLENDER_REQUESTS_VERSION=2.34.2
# renovate: datasource=npm depName=@gltf-transform/cli
ARG GLTF_TRANSFORM_VERSION=4.5.0
# Khronos currently publishes the validator under a prerelease version.
ARG GLTF_VALIDATOR_VERSION=2.0.0-dev.3.10

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Base packages: dev essentials + python3 (hass-sandbox/makejinja) + tmux (agent
# sessions live in tmux) + ripgrep (agents search with rg) + build-essential
# (native npm modules, C extensions).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git openssh-client jq openssl bash gettext-base \
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

# talosctl + omnictl (single static binaries, siderolabs releases) — READ-ONLY node
# triage (saga backlog 10). talosctl reads dmesg/logs/machine resources directly from
# each node's apid (tcp/50000); omnictl reads Omni machine/cluster status. Read-only
# is enforced by the CREDENTIAL role at runtime (os:reader talosconfig, Omni Reader
# SA), NOT the binary — the tools themselves are full-featured, but the pod only ever
# holds reader creds (PR B mounts them).
RUN curl -fsSLo /usr/local/bin/talosctl \
      "https://github.com/siderolabs/talos/releases/download/${TALOSCTL_VERSION}/talosctl-linux-amd64" \
 && chmod +x /usr/local/bin/talosctl \
 && curl -fsSLo /usr/local/bin/omnictl \
      "https://github.com/siderolabs/omni/releases/download/${OMNICTL_VERSION}/omnictl-linux-amd64" \
 && chmod +x /usr/local/bin/omnictl

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
      "tsx@${TSX_VERSION}" \
      "pnpm@${PNPM_VERSION}" \
 && npm cache clean --force

# Playwright MCP + a baked chromium (agents do UI/UX testing of the cluster's own
# apps — saga plan 03). Browsers + system deps install at BUILD time to a shared
# path so the runtime pod needs no CDN egress and no root. --with-deps pulls the
# chromium system libraries via apt.
# Install the browser via the MCP's OWN playwright-core CLI so the chromium build
# revision matches what the MCP expects (a bare `npx playwright install` can pull a
# mismatched playwright version).
#
# Browsers are STAGED at /opt/dev-env/ms-playwright, not used in place: at runtime
# PLAYWRIGHT_BROWSERS_PATH points at the PVC (~/.cache/ms-playwright) so a repo whose
# own playwright pins a DIFFERENT chromium revision (haynesnetwork: @playwright/test
# 1.61.x) can `playwright install` it alongside — impossible under
# readOnlyRootFilesystem if the path lived in an image layer. dev-init seeds the PVC
# cache from this staging dir on first boot, so the MCP works with zero downloads.
RUN npm install -g "@playwright/mcp@${PLAYWRIGHT_MCP_VERSION}" \
 && PLAYWRIGHT_BROWSERS_PATH=/opt/dev-env/ms-playwright \
      node "$(npm root -g)/@playwright/mcp/node_modules/playwright-core/cli.js" \
      install --with-deps chromium \
 && chmod -R a+rX /opt/dev-env/ms-playwright \
 && npm cache clean --force && rm -rf /var/lib/apt/lists/*

# github-app-token.sh (single source of truth) — used by the token-refresher
# sidecar/initContainer to mint short-lived ghs_ tokens from the haynes-ops-bot PEM
# (saga Decision #5). The agent containers never see the PEM, only /creds tokens.
WORKDIR /opt/dev-env
COPY scripts/github-app-token.sh ./github-app-token.sh
RUN chmod +x ./github-app-token.sh

# Offline-at-runtime authoring tools for Haynes Quest. Blender uses a graphical
# event loop under Xvfb for MCP commands/screenshots; --background alone cannot
# drive this add-on. Explicit software GL needs no GPU device or extra Service.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      xvfb xauth ffmpeg libgl1-mesa-dri libglx-mesa0 libegl1 \
      libx11-6 libxxf86vm1 libxfixes3 libxi6 libxrender1 libxrandr2 \
      libxkbcommon0 libsm6 libice6 libdbus-1-3 \
 && rm -rf /var/lib/apt/lists/*
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
RUN git init /opt/dev-env/blender-mcp \
 && git -C /opt/dev-env/blender-mcp remote add origin https://github.com/ahujasid/blender-mcp.git \
 && git -C /opt/dev-env/blender-mcp fetch --depth 1 origin "${BLENDER_MCP_REVISION}" \
 && git -C /opt/dev-env/blender-mcp checkout --detach FETCH_HEAD \
 && test "$(git -C /opt/dev-env/blender-mcp rev-parse HEAD)" = "${BLENDER_MCP_REVISION}" \
 && cmp /opt/dev-env/blender-mcp/addon.py /opt/dev-env/blender-mcp/src/blender_mcp/bundled/addon.py \
 && uv sync --project /opt/dev-env/blender-mcp --python /usr/bin/python3 --frozen --no-dev --no-editable \
 && rm -rf /opt/dev-env/blender-mcp/.git \
 && printf '%s\n' "${BLENDER_MCP_REVISION}" > /opt/dev-env/blender-mcp/REVISION \
 && npm install -g "@gltf-transform/cli@${GLTF_TRANSFORM_VERSION}" "gltf-validator@${GLTF_VALIDATOR_VERSION}" \
 && npm cache clean --force
COPY scripts/dev-env/blender-authoring.py scripts/dev-env/blender-bootstrap.py scripts/dev-env/test-blender-authoring.py /opt/dev-env/
RUN chmod 0755 /opt/dev-env/blender-authoring.py \
 && ln -s /opt/dev-env/blender-authoring.py /usr/local/bin/blender-authoring

# Non-root: rename the base image's uid-1000 'node' user to 'dev' with /home/dev —
# the PVC mounts over /home/dev at runtime (dev-init.sh repopulates config links).
RUN usermod -l dev -d /home/dev node && groupmod -n dev node \
 && mkdir -p /home/dev && chown dev:dev /home/dev

USER dev
WORKDIR /home/dev
# CODEX_VERSION rides into the runtime env so dev-init.sh installs the matching
# standalone. ~/.local/bin (the standalone codex launcher + agent-run) leads PATH
# so codex resolves in EVERY context — login shells, tmux panes, and the non-login
# auth-watch probe alike (npm codex is gone; the standalone is the only codex).
ENV HOME=/home/dev \
    CLAUDE_CONFIG_DIR=/home/dev/.claude \
    CODEX_HOME=/home/dev/.codex \
    CODEX_VERSION=${CODEX_VERSION} \
    PLAYWRIGHT_BROWSERS_PATH=/home/dev/.cache/ms-playwright \
    PATH=/home/dev/.local/bin:/opt/dev-env:/usr/local/bin:/usr/bin:/bin
ENTRYPOINT ["/bin/bash"]
