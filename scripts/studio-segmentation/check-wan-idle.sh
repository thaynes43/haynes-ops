#!/usr/bin/env bash
set -euo pipefail

kubectl -n ai exec deploy/wan-video -c api -- python -B -c '
import sys
from urllib.request import urlopen
with urlopen(sys.argv[1], timeout=5) as response:
    sys.stdout.buffer.write(response.read())
' http://127.0.0.1:8080/v1/ready | jq -es '
  length == 1 and (.[0] | type == "object" and .ready == true and
    has("active_video_job") and .active_video_job == null and
    has("active_foley_job") and .active_foley_job == null)
'
