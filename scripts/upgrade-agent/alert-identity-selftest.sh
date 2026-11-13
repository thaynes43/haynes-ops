#!/usr/bin/env bash
# Offline exact-identity contract tests. Extract shipped blocks rather than
# copying writer logic. All kubectl calls use a private synthetic ConfigMap.
# SRC_ROOT may point to an archive of main for negative regression evidence.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python3 "$REPO_ROOT/scripts/upgrade-agent/alert_identity_selftest.py" "${SRC_ROOT:-$REPO_ROOT}"
