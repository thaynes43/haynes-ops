# syntax=docker/dockerfile:1
# ghcr.io/thaynes43/upgrade-shepherd — Tier-4 breaking-change shepherd (phase 4b).
#
# The LLM agent that reads release notes + the committed component playbooks, makes
# the supporting helmrelease/values edits a Renovate tag bump lacks, and OPENS a PR
# (never merges — merge-prevention is via --allowedTools, not this image). Runs as a
# SUSPENDED CronJob, human-summoned. Separate from the minimal gate image.
#
# Containment is environmental (read-only k8s SA, egress CNP, PR-only) + tool
# allowlist. The bot PEM is minted to a ghs_ token in an initContainer and NEVER
# enters this (the LLM) container's env.
#
# Debian/glibc (kubectl/claude glibc-linked); amd64-only (all Talos nodes x86_64).
FROM node:24-slim

# renovate: datasource=github-releases depName=kubernetes/kubernetes
ARG KUBECTL_VERSION=v1.35.5
# renovate: datasource=github-releases depName=fluxcd/flux2
ARG FLUX_VERSION=2.4.0
# renovate: datasource=github-releases depName=cli/cli
ARG GH_VERSION=2.95.0
# renovate: datasource=npm depName=@anthropic-ai/claude-code
ARG CLAUDE_CODE_VERSION=2.1.197

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git jq openssl bash tar gzip \
 && rm -rf /var/lib/apt/lists/*

# kubectl (checksum-verified) — read-only cluster diagnosis.
RUN curl -fsSLo /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && curl -fsSLo /tmp/kubectl.sha256 \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" \
 && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
 && chmod +x /usr/local/bin/kubectl && rm -f /tmp/kubectl.sha256

# flux CLI — Flux CR diagnosis (mode 2). Read-only use.
RUN curl -fsSLo /tmp/flux.tar.gz \
      "https://github.com/fluxcd/flux2/releases/download/v${FLUX_VERSION}/flux_${FLUX_VERSION}_linux_amd64.tar.gz" \
 && tar -xzf /tmp/flux.tar.gz -C /usr/local/bin flux \
 && chmod +x /usr/local/bin/flux && rm -f /tmp/flux.tar.gz

# gh CLI — open PRs as haynes-ops-bot, read release notes (gh release view).
RUN curl -fsSLo /tmp/gh.tar.gz \
      "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
 && tar -xzf /tmp/gh.tar.gz -C /tmp \
 && install -m 0755 "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh \
 && rm -rf /tmp/gh.tar.gz "/tmp/gh_${GH_VERSION}_linux_amd64"

# Claude Code CLI.
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force

# github-app-token.sh (single source of truth) — used ONLY by the initContainer to
# mint the ghs_ token from the PEM. The LLM container never invokes it.
WORKDIR /opt/upgrade-shepherd
COPY scripts/github-app-token.sh ./github-app-token.sh
RUN chmod +x ./github-app-token.sh

# Non-root (node:22-slim ships uid 1000 'node'). --permission-mode/dontAsk needs a
# writable config dir; readOnlyRootFilesystem is satisfied by mounting emptyDirs at
# HOME + CLAUDE_CONFIG_DIR at runtime.
USER node
ENV HOME=/tmp \
    CLAUDE_CONFIG_DIR=/tmp/.claude \
    PATH=/opt/upgrade-shepherd:/usr/local/bin:/usr/bin:/bin
# The run script is mounted from a ConfigMap (auditable per-PR), not baked in.
ENTRYPOINT ["/bin/bash"]
