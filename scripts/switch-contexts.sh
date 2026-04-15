#!/usr/bin/env bash
set -o errexit
set -o pipefail

TARGET="$1"

if [[ -z "${TARGET}" ]]; then
    echo "Usage: $(basename "$0") [edge|ops]"
    exit 1
fi

case "${TARGET}" in
    edge)
        CONTEXT="haynes-edge"
        ;;
    ops)
        CONTEXT="haynes-ops"
        ;;
    *)
        echo "Invalid argument: ${TARGET}. Use 'edge' or 'ops'."
        exit 1
        ;;
esac

# Switch contexts for omnictl, talosctl, kubectl
if command -v omnictl >/dev/null 2>&1; then
    echo "Switching omnictl context to '${CONTEXT}'..."
    omnictl config context "${CONTEXT}"
else
    echo "omnictl not found; skipping." >&2
fi

if command -v talosctl >/dev/null 2>&1; then
    echo "Switching talosctl context to '${CONTEXT}'..."
    talosctl config context "${CONTEXT}"
else
    echo "talosctl not found; skipping." >&2
fi

if command -v kubectl >/dev/null 2>&1; then
    echo "Switching kubectl context to '${CONTEXT}'..."
    kubectl config use-context "${CONTEXT}"
else
    echo "kubectl not found; skipping." >&2
fi

# Output current context details
echo "=== Omnictl contexts (current marked with *) ==="
if command -v omnictl >/dev/null 2>&1; then
    omnictl config contexts
else
    echo "omnictl not found"
fi

echo "=== Talosctl contexts (current marked with *) ==="
if command -v talosctl >/dev/null 2>&1; then
    talosctl config contexts
else
    echo "talosctl not found"
fi

echo "=== Kubectl contexts (current marked with *) ==="
if command -v kubectl >/dev/null 2>&1; then
    kubectl config get-contexts
else
    echo "kubectl not found"
fi


