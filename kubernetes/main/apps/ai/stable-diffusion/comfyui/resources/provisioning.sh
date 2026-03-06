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
TMP_DIR="${WORKSPACE_ROOT}/tmp/provisioning"

mkdir -p "${MODELS_ROOT}" "${TMP_DIR}"

ensure_python_dependency() {
  local module_name="$1"
  local package_name="$2"
  local venv_python="/opt/environments/python/comfyui/bin/python"
  local venv_pip="/opt/environments/python/comfyui/bin/pip"
  local dependency_dir="${WORKSPACE_ROOT}/.python-deps"

  if [[ ! -x "${venv_python}" || ! -x "${venv_pip}" ]]; then
    log "ComfyUI virtualenv not found, skipping dependency check for ${module_name}."
    return 0
  fi

  mkdir -p "${dependency_dir}"

  if PYTHONPATH="${dependency_dir}" "${venv_python}" -c "import ${module_name}" >/dev/null 2>&1; then
    log "Python dependency already present in shared deps: ${module_name}"
    return 0
  fi

  log "Installing missing Python dependency to shared deps: ${package_name}"
  "${venv_pip}" install --no-cache-dir --target "${dependency_dir}" "${package_name}"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    log "No SHA256 tool found (sha256sum/shasum); cannot verify checksum."
    return 1
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

if [[ ! -f "${MANIFEST_PATH}" ]]; then
  log "Model manifest not found at ${MANIFEST_PATH}; nothing to provision."
  # Continue startup dependency checks even if no model manifest is provided.
fi

# Some recent ComfyUI builds import SQLAlchemy during startup.
ensure_python_dependency "sqlalchemy" "sqlalchemy"

if [[ -f "${MANIFEST_PATH}" ]]; then
  log "Using model manifest: ${MANIFEST_PATH}"

  while IFS='|' read -r relative_path url expected_sha; do
    # Skip comments and blank lines.
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
