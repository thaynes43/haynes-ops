#!/bin/sh
# ---------------------------------------------------------------------------
# mam-update — MyAnonaMouse dynamic-seedbox IP keeper (PLAN-031)
#
# Runs as a SIDECAR in the qBittorrent pod. Because it shares the pod's macvlan
# netns (static-vpn-qbittorrent, VLAN-30), every request it makes egresses the
# SAME Mullvad exit IP that qBittorrent announces from. That is the whole point
# of the sidecar placement (owner ruling Q-02): the IP we register at MAM is
# guaranteed to be the IP MAM sees on our announces.
#
# COMPLIANCE INVARIANTS (mam-rules-scrape.md / research doc §3) — do not weaken:
#   * The ONLY MyAnonaMouse endpoint contacted is dynamicSeedbox.php (documented
#     API). No scraping, no other site automation.
#   * At most ONE dynamicSeedbox.php call per hour (MIN_GAP). Exceeding it returns
#     "Last Change Too Recent" and risks the account.
#   * NEVER register an off-VPN IP. We confirm we are on the Mullvad exit
#     (am.i.mullvad.net/json -> mullvad_exit_ip:true) BEFORE every call; if we
#     cannot confirm it, we skip (fail-closed) — mirrors the pod readiness probe.
#   * MAM rotates the mam_id cookie on use. We keep a curl cookie jar on a subpath
#     of qBittorrent's config PVC (/state) so the rotated cookie survives pod
#     restarts. The env seed (MAM_ID_SEED, from the ExternalSecret) is written into
#     the jar ONCE, on first run only; thereafter the persisted (rotated) cookie
#     is authoritative.
#   * We only call when the exit IP actually changed (or was never registered) —
#     a "No Change" reply otherwise.
# ---------------------------------------------------------------------------
set -u

STATE_DIR="${STATE_DIR:-/state}"
JAR="$STATE_DIR/mam.cookies"          # curl cookie jar (holds the live, rotated mam_id)
IP_CACHE="$STATE_DIR/mam.ip"          # last successfully-registered exit IP
LAST_CALL="$STATE_DIR/mam.last_call"  # epoch of the last dynamicSeedbox.php call (rate-limit guard)
URL="${MAM_SEEDBOX_URL:-https://t.myanonamouse.net/json/dynamicSeedbox.php}"
CHECK_URL="${MAM_CHECK_URL:-https://am.i.mullvad.net/json}"
INTERVAL="${INTERVAL:-3600}"          # loop cadence
MIN_GAP="${MIN_GAP:-3600}"            # hard floor between dynamicSeedbox.php calls

mkdir -p "$STATE_DIR"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) mam-update: $*"; }

log "starting; state=$STATE_DIR endpoint=$URL check=$CHECK_URL interval=${INTERVAL}s min_gap=${MIN_GAP}s"
if [ -s "$JAR" ]; then
  log "cookie jar present — using persisted (rotated) mam_id"
else
  log "no cookie jar yet — will seed it from MAM_ID_SEED on first run"
fi

while true; do
  now=$(date +%s)

  # 1) Confirm we are on the Mullvad exit AND learn the current exit IP, in one call.
  #    am.i.mullvad.net/json -> {"ip":"x.x.x.x", ..., "mullvad_exit_ip":true}
  #    Same host the pod's readiness probe uses, so it is reachable over the tunnel.
  check=$(curl -fsS --max-time 15 "$CHECK_URL" 2>/dev/null)
  cur_ip=$(printf '%s' "$check" | sed -n 's/.*"ip"[[:space:]]*:[[:space:]]*"\([0-9A-Fa-f.:]*\)".*/\1/p')
  on_mullvad=$(printf '%s' "$check" | grep -o '"mullvad_exit_ip"[[:space:]]*:[[:space:]]*true')
  if [ -z "$cur_ip" ] || [ -z "$on_mullvad" ]; then
    log "WARN could not confirm Mullvad exit (VPN down/leaking or check unreachable) — skipping (fail-closed)"
    sleep "$INTERVAL"; continue
  fi

  last_ip=$(cat "$IP_CACHE" 2>/dev/null || true)
  last_call=$(cat "$LAST_CALL" 2>/dev/null || echo 0)
  since=$(( now - last_call ))

  # 2) Nothing to do if the exit IP is unchanged since our last successful registration.
  if [ -n "$last_ip" ] && [ "$cur_ip" = "$last_ip" ]; then
    log "No exit-IP change ($cur_ip); no update needed"
    sleep "$INTERVAL"; continue
  fi

  # 3) Rate-limit guard: never call more than once per MIN_GAP, even on a rapid re-change.
  if [ "$last_call" -ne 0 ] && [ "$since" -lt "$MIN_GAP" ]; then
    wait=$(( MIN_GAP - since ))
    log "exit IP changed (${last_ip:-none} -> $cur_ip) but last call was ${since}s ago (<${MIN_GAP}s) — deferring ${wait}s"
    sleep "$wait"; continue
  fi

  # 4) Seed the jar once (first run), then always drive it from the jar so the
  #    server-rotated mam_id is captured and reused. Netscape cookie-jar format;
  #    far-future expiry so curl persists it (not treated as a session cookie).
  if [ ! -s "$JAR" ]; then
    printf '# Netscape HTTP Cookie File\n.myanonamouse.net\tTRUE\t/\tTRUE\t2147483647\tmam_id\t%s\n' "${MAM_ID_SEED:-}" > "$JAR"
    log "seeded cookie jar from MAM_ID_SEED (first run)"
  fi

  # 5) Register the current exit IP. -b/-c on the same jar loads the current mam_id
  #    and writes back whatever (possibly rotated) cookie the server returns.
  resp=$(curl -fsS --max-time 30 -b "$JAR" -c "$JAR" "$URL" 2>"$STATE_DIR/mam.err")
  rc=$?
  echo "$now" > "$LAST_CALL"   # count EVERY call against the hourly limit (conservative)

  if [ "$rc" -ne 0 ]; then
    log "ERROR curl rc=$rc calling dynamicSeedbox.php: $(cat "$STATE_DIR/mam.err" 2>/dev/null)"
    sleep "$INTERVAL"; continue
  fi

  # 6) Interpret the reply. Update the IP cache only on a confirmed success.
  case "$resp" in
    *Completed*|*completed*)
      echo "$cur_ip" > "$IP_CACHE"
      log "Completed: registered seedbox IP ${last_ip:-none} -> $cur_ip" ;;
    *"No change"*|*"No Change"*|*"no change"*)
      echo "$cur_ip" > "$IP_CACHE"
      log "No Change: MAM already had $cur_ip as the seedbox IP" ;;
    *"Last Change Too Recent"*|*"too recent"*)
      log "WARN MAM rate-limited us (Last Change Too Recent) — will retry next cycle" ;;
    *)
      log "ERROR unexpected dynamicSeedbox.php response: $resp" ;;
  esac

  sleep "$INTERVAL"
done
