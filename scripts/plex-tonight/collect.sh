#!/usr/bin/env bash
set -euo pipefail

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'plex-tonight: missing required command: %s\n' "$1" >&2
    exit 1
  }
}

need_cmd kubectl
need_cmd node

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cache_dir="${HOME}/.cache/plex-tonight"
output_path="${cache_dir}/snapshot.json"

umask 077
mkdir -p -- "$cache_dir"
# GNU chmod preserves a directory's inherited setgid bit unless it is cleared
# explicitly. Keep the cache directory at exactly 0700 on this shared PVC.
chmod 0700 -- "$cache_dir"
chmod g-s -- "$cache_dir"
temp_path="$(mktemp "${cache_dir}/.snapshot.XXXXXX")"
trap 'rm -f -- "$temp_path"' EXIT

kubectl exec -i -n frontend deploy/haynesnetwork-main -c app \
  -- node --input-type=module \
  < "${script_dir}/collector.mjs" \
  > "$temp_path"

# Refuse to replace the last good snapshot if transport produced truncated JSON.
if ! node -e '
  try {
    JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8"));
  } catch {
    process.exit(1);
  }
' "$temp_path"; then
  printf 'plex-tonight: collector output was not valid JSON\n' >&2
  exit 1
fi
chmod 600 -- "$temp_path"
mv -f -- "$temp_path" "$output_path"
trap - EXIT

printf '%s\n' "$output_path"
