#!/usr/bin/env bash
# Story 848. All script children use an empty environment and stub-first PATH;
# the Python harness never inherits the pod's cluster login or model credentials.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec python3 -I -B "$ROOT/scripts/upgrade-agent/test_responder_claim.py" "$@"
