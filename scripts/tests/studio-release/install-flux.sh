#!/usr/bin/env bash
# Install the pinned Flux CLI for the offline Studio release checks.
set -euo pipefail

mkdir "$RUNNER_TEMP/studio-flux"
cd "$RUNNER_TEMP/studio-flux"
curl --fail --silent --show-error --location \
  https://github.com/fluxcd/flux2/releases/download/v2.10.0/flux_2.10.0_linux_amd64.tar.gz \
  --output flux.tar.gz
printf '%s\n' '208dd7012c04fedeed5b2d0b6e63df3b15b1c34a381527811685c098bc9f8659  flux.tar.gz' | sha256sum --check
tar -xzf flux.tar.gz flux
printf '%s\n' "$PWD" >> "$GITHUB_PATH"
