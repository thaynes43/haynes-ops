#!/usr/bin/env bash
# Staged adapter: refuses all production connections until this exact shared
# source revision has an operator-approved activation marker. No secret in argv.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
expected="$(sha256sum /opt/dev-env/config/claude/mcp.json | cut -d' ' -f1)"
approval="$HOME/.config/cursor/dev-env-mcp-approved.sha256"
if [ ! -f "$approval" ] || [ "$(cat "$approval")" != "$expected" ]; then
  echo 'cursor-mcp: activation pending for this source revision' >&2
  exit 1
fi
exec uv run --no-project --python /usr/bin/python3 --with-requirements "$HERE/cursor-mcp-requirements.txt" \
  python "$HERE/cursor-mcp.py" "$@"
