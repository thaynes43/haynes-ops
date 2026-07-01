# syntax=docker/dockerfile:1
# ghcr.io/thaynes43/upgrade-agent — Tier-4 health-gate toolbox (phase 4a).
#
# DELIBERATELY MINIMAL: kubectl + jq + curl + bash only. No node, no claude-code,
# no gh, no git, no openssl. The always-on health gate physically CANNOT run an LLM
# or push to the repo even if the pod is compromised — the strongest containment for
# an unattended workload. The phase-4b shepherd (LLM + PR authorship) will get its
# OWN, separate, fatter image; it is not built here.
#
# Debian/glibc (kubectl is glibc-linked); amd64-only (all Talos nodes are x86_64).
FROM debian:bookworm-slim

# renovate: datasource=github-releases depName=kubernetes/kubernetes
ARG KUBECTL_VERSION=v1.35.5

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl jq bash \
 && curl -fsSLo /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && curl -fsSLo /tmp/kubectl.sha256 \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" \
 && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
 && chmod +x /usr/local/bin/kubectl \
 && rm -rf /tmp/kubectl.sha256 /var/lib/apt/lists/*

# Run as nobody. The gate reads the cluster via the mounted SA token (world-readable
# projected volume) and needs no filesystem writes beyond the emptyDir /tmp (HOME).
USER 65534:65534
ENV HOME=/tmp
# 4a overrides this with `command:` to run the ConfigMap-mounted gate.sh.
ENTRYPOINT ["/bin/bash"]
