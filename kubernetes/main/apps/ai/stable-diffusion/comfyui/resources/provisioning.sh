#!/usr/bin/env bash

set -euo pipefail

log() {
  echo "[comfyui-provision] $*"
}

WORKSPACE_ROOT="${WORKSPACE:-/workspace}"
COMFY_ROOT="${WORKSPACE_ROOT}/ComfyUI"
MODELS_ROOT="${COMFY_ROOT}/models"
MANIFEST_PATH="${MODEL_MANIFEST:-/opt/comfyui-provisioning/models.txt}"
FORCE_UPDATE="${PROVISIONING_UPDATE_EXISTING:-false}"
DEPS_DIR="${WORKSPACE_ROOT}/.python-deps"
COMFYUI_VERSION="${COMFYUI_VERSION:-}"

mkdir -p "${MODELS_ROOT}" "${DEPS_DIR}"

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    echo "no-hash-tool"
  fi
}

install_comfyui_requirements() {
  local requirements_file="${COMFY_ROOT}/requirements.txt"
  local marker_file="${DEPS_DIR}/.requirements-hash"
  local venv_pip="/opt/environments/python/comfyui/bin/pip"

  if [[ ! -f "${requirements_file}" ]]; then
    log "No requirements.txt found at ${requirements_file}; skipping."
    return 0
  fi

  if [[ ! -x "${venv_pip}" ]]; then
    log "ComfyUI venv pip not found; skipping requirements install."
    return 0
  fi

  local current_hash
  current_hash="$(sha256_file "${requirements_file}")"

  if [[ -f "${marker_file}" && "$(cat "${marker_file}")" == "${current_hash}" ]]; then
    log "Requirements already installed (hash unchanged); skipping."
    return 0
  fi

  log "Installing ComfyUI requirements.txt to shared deps at ${DEPS_DIR}..."
  "${venv_pip}" install --no-cache-dir --target "${DEPS_DIR}" -r "${requirements_file}"

  echo "${current_hash}" > "${marker_file}"
  log "Requirements installed successfully."
}

# Pin the ComfyUI code to COMFYUI_VERSION. The ai-dock image bakes an old
# ComfyUI; on a fresh openebs-hostpath workspace (post node re-image) it would
# otherwise revert and lose newer nodes (e.g. Qwen-Image-Edit). Seed from the
# baked /opt/ComfyUI (keeps ComfyUI-Manager/essentials) if absent, then checkout.
# Idempotent: no-op when already at COMFYUI_VERSION.
ensure_comfyui_version() {
  if [[ -z "${COMFYUI_VERSION}" ]]; then
    log "COMFYUI_VERSION unset; leaving ComfyUI code as-is."
    return 0
  fi

  if [[ ! -d "${COMFY_ROOT}/.git" ]]; then
    if [[ -d /opt/ComfyUI/.git ]]; then
      log "Fresh workspace: seeding ComfyUI from baked /opt/ComfyUI..."
      cp -a /opt/ComfyUI "${COMFY_ROOT}"
      touch "${COMFY_ROOT}/.move_complete"
    else
      log "No ComfyUI git checkout and no /opt/ComfyUI to seed from; skipping version pin."
      return 0
    fi
  fi

  local current
  current="$(git -C "${COMFY_ROOT}" describe --tags 2>/dev/null || echo unknown)"
  if [[ "${current}" == "${COMFYUI_VERSION}" ]]; then
    log "ComfyUI already at ${COMFYUI_VERSION}."
    return 0
  fi

  log "Pinning ComfyUI ${current} -> ${COMFYUI_VERSION}..."
  git -C "${COMFY_ROOT}" fetch --tags --quiet 2>/dev/null || log "git fetch failed; using local refs."
  if git -C "${COMFY_ROOT}" checkout -f "${COMFYUI_VERSION}" 2>/dev/null; then
    log "ComfyUI pinned to ${COMFYUI_VERSION}."
  else
    log "WARNING: checkout ${COMFYUI_VERSION} failed; leaving ComfyUI as ${current}."
  fi
}

download_file() {
  local url="$1"
  local destination="$2"
  local tmp_file="${destination}.part"

  mkdir -p "$(dirname "${destination}")"
  rm -f "${tmp_file}"

  log "Downloading ${url} -> ${destination}"

  if command -v curl >/dev/null 2>&1; then
    local -a curl_args=(
      --fail
      --location
      --retry 5
      --retry-delay 5
      --show-error
      --silent
      --output "${tmp_file}"
    )

    if [[ "${url}" == *"huggingface.co"* && -n "${HF_TOKEN:-}" ]]; then
      curl_args+=(--header "Authorization: Bearer ${HF_TOKEN}")
    fi

    curl "${curl_args[@]}" "${url}"
  elif command -v wget >/dev/null 2>&1; then
    local -a wget_args=(
      --tries=5
      --waitretry=5
      --quiet
      --output-document="${tmp_file}"
    )

    if [[ "${url}" == *"huggingface.co"* && -n "${HF_TOKEN:-}" ]]; then
      wget_args+=(--header="Authorization: Bearer ${HF_TOKEN}")
    fi

    wget "${wget_args[@]}" "${url}"
  else
    log "No supported download tool found (curl/wget)."
    return 1
  fi

  mv "${tmp_file}" "${destination}"
}

process_model() {
  local relative_path="$1"
  local url="$2"
  local expected_sha="${3:-}"
  local destination="${MODELS_ROOT}/${relative_path}"
  local should_download="true"

  if [[ -s "${destination}" ]]; then
    should_download="false"

    if [[ "${FORCE_UPDATE}" == "true" ]]; then
      log "Updating existing file due to PROVISIONING_UPDATE_EXISTING=true: ${destination}"
      should_download="true"
    elif [[ -n "${expected_sha}" ]]; then
      local current_sha
      if current_sha="$(sha256_file "${destination}")"; then
        if [[ "${current_sha,,}" != "${expected_sha,,}" ]]; then
          log "Checksum mismatch, re-downloading: ${destination}"
          should_download="true"
        else
          log "Checksum matches, keeping existing file: ${destination}"
        fi
      else
        log "Skipping checksum validation for ${destination}; keeping existing file."
      fi
    else
      log "Skipping existing file: ${destination}"
    fi
  fi

  if [[ "${should_download}" == "true" ]]; then
    download_file "${url}" "${destination}"

    if [[ -n "${expected_sha}" ]]; then
      local new_sha
      if new_sha="$(sha256_file "${destination}")"; then
        if [[ "${new_sha,,}" != "${expected_sha,,}" ]]; then
          log "Downloaded file checksum mismatch for ${destination}"
          rm -f "${destination}"
          return 1
        fi
      fi
    fi
  fi
}

# --- ComfyUI version pin (self-heal after an openebs-hostpath wipe) ---
ensure_comfyui_version

# --- Python dependencies ---
install_comfyui_requirements

# --- Model downloads ---
if [[ ! -f "${MANIFEST_PATH}" ]]; then
  log "Model manifest not found at ${MANIFEST_PATH}; skipping model downloads."
else
  log "Using model manifest: ${MANIFEST_PATH}"

  while IFS='|' read -r relative_path url expected_sha; do
    [[ -z "${relative_path// }" ]] && continue
    [[ "${relative_path}" == \#* ]] && continue

    if [[ -z "${url// }" ]]; then
      log "Skipping invalid manifest row (missing URL): ${relative_path}"
      continue
    fi

    process_model "${relative_path}" "${url}" "${expected_sha:-}"
  done < "${MANIFEST_PATH}"
fi

log "Provisioning complete"
