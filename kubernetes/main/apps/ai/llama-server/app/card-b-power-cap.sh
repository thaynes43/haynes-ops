#!/bin/sh
# Card B only. No CUDA context, model, benchmark, other GPU or power-limit increase.
# Shared by the startup init container and render-job.sh --cap-only.
# Use bare shell variables: Flux substitutes braced variable syntax in rendered resources.
set -eu
export LC_ALL=C
gpu=GPU-18bf6eab-c76a-26ba-74c8-76093b705b8b

fail() { echo "card-b-power-cap: $*" >&2; exit 1; }
smi() { timeout 15 nvidia-smi -i "$gpu" "$@"; }

[ "$POD_NODE_NAME" = talosw04 ] || fail 'refusing another node'
[ "$NVIDIA_VISIBLE_DEVICES" = "$gpu" ] || fail 'refusing another visible-device selection'

read_state() {
  state=$(smi --query-gpu=uuid,persistence_mode,power.limit,enforced.power.limit --format=csv,noheader,nounits) || fail 'GPU state query failed'
  # Reject missing/extra GPUs, non-finite or non-positive limits, and unknown modes.
  printf '%s\n' "$state" | awk -F, -v uuid="$gpu" '
    { for (i=1; i<=NF; i++) gsub(/^[[:space:]]+|[[:space:]]+$/, "", $i) }
    NF != 4 || $1 != uuid || ($2 != "Enabled" && $2 != "Disabled") ||
      $3 !~ /^[0-9]+([.][0-9]+)?$/ || $4 !~ /^[0-9]+([.][0-9]+)?$/ ||
      $3+0 <= 0 || $4+0 <= 0 { bad=1 }
    END { exit (NR != 1 || bad) }
  ' || fail 'unreadable or unexpected GPU identity/limit'
  mode=$(printf '%s\n' "$state" | cut -d, -f2 | tr -d '[:space:]')
  configured=$(printf '%s\n' "$state" | cut -d, -f3 | tr -d '[:space:]')
  enforced=$(printf '%s\n' "$state" | cut -d, -f4 | tr -d '[:space:]')
  echo "card-b-power-cap: $state"
}

read_state
processes=$(smi --query-compute-apps=pid --format=csv,noheader,nounits) || fail 'cannot verify idle GPU'
[ -z "$processes" ] || fail 'GPU has an active or unreadable compute-process list; no changes made'

# Keep a deliberately lower limit; never turn 150 W into 250 W. An inconsistent
# low configured/high enforced limit is an error, not permission to increase it.
if awk -v p="$configured" 'BEGIN { exit !(p <= 250) }'; then
  awk -v p="$enforced" 'BEGIN { exit !(p <= 250) }' || fail 'configured/enforced limits disagree'
  ceiling=$configured
else
  ceiling=250
fi

if [ "$mode" != Enabled ]; then
  smi -pm 1 || fail 'persistence mode failed; model must remain stopped'
fi
if awk -v p="$configured" 'BEGIN { exit !(p > 250) }'; then
  smi -pl 250 || fail '250 W write failed; model must remain stopped'
fi

read_state
[ "$mode" = Enabled ] || fail 'persistence mode did not remain enabled'
awk -v c="$configured" -v e="$enforced" -v ceiling="$ceiling" '
  BEGIN { exit !(c <= ceiling && e <= ceiling) }
' || fail 'cap read-back failed; model must remain stopped'
echo "card-b-power-cap: verified $configured W configured / $enforced W enforced; no GPU load started"
