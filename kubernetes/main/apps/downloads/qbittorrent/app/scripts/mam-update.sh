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
#     the jar on first run, and again only when a NEW seed appears while the
#     persisted cookie is dead (see below); otherwise the persisted (rotated)
#     cookie is authoritative.
#   * We call when the exit IP changed (or was never registered) — and, since
#     2026-09-22, once per KEEPALIVE (default daily) even when it has not, to
#     VALIDATE the session: a MAM password change cancels every session, and
#     nothing else surfaces that (qBittorrent kept reporting the tracker as
#     "Working" for 5 weeks with a dead session). A "No Change" reply is the
#     healthy answer; one extra call a day stays far inside the hourly limit.
#
# DEAD-SESSION CONTRACT:
#   * Any {"Success":false,...} reply other than the rate limit (e.g. HTTP 403
#     "No Session Cookie") marks the session dead: the token MAM_SESSION_DEAD is
#     logged on that call AND on every following cycle until a call succeeds, so
#     the Loki rule (lokirule.yaml, severity=critical) keeps paging.
#   * While dead we do NOT retry hourly with the same cookie — the next call waits
#     for KEEPALIVE (daily), unless a NEW seed shows up.
#   * Self-heal: the owner creates a new "dynamic seedbox" session at MAM, stores
#     it as MAM_ID_SEEDBOX (1Password item `myanonamouse`), and restarts this pod.
#     The env seed is read at container start; if its fingerprint differs from the
#     seed we already used, the jar is re-seeded from it and the next call retries
#     after MIN_GAP. An unchanged (dead) seed is never re-tried.
# ---------------------------------------------------------------------------
set -u

STATE_DIR="${STATE_DIR:-/state}"
JAR="$STATE_DIR/mam.cookies"          # curl cookie jar (holds the live, rotated mam_id)
IP_CACHE="$STATE_DIR/mam.ip"          # last successfully-registered exit IP
LAST_CALL="$STATE_DIR/mam.last_call"  # epoch of the last dynamicSeedbox.php call (rate-limit guard)
SEED_USED="$STATE_DIR/mam.seed_used"  # fingerprint (cksum) of the env seed last written into the jar
DEAD_FLAG="$STATE_DIR/mam.dead"       # present while MAM rejects our cookie; holds the last reply
URL="${MAM_SEEDBOX_URL:-https://t.myanonamouse.net/json/dynamicSeedbox.php}"
CHECK_URL="${MAM_CHECK_URL:-https://am.i.mullvad.net/json}"
INTERVAL="${INTERVAL:-3600}"          # loop cadence
MIN_GAP="${MIN_GAP:-3600}"            # hard floor between dynamicSeedbox.php calls
KEEPALIVE="${KEEPALIVE:-86400}"       # validate the session this often even with no IP change

mkdir -p "$STATE_DIR"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) mam-update: $*"; }

# Fingerprint of a secret for equality checks in logs/state without storing it.
fp() { printf '%s' "$1" | cksum | cut -d' ' -f1; }

# Write the env seed into the jar (Netscape cookie-jar format; far-future expiry
# so curl persists it rather than treating it as a session cookie) and remember
# which seed that was.
seed_jar() {
  printf '# Netscape HTTP Cookie File\n.myanonamouse.net\tTRUE\t/\tTRUE\t2147483647\tmam_id\t%s\n' "${MAM_ID_SEED:-}" > "$JAR"
  fp "${MAM_ID_SEED:-}" > "$SEED_USED"
}

log "starting; state=$STATE_DIR endpoint=$URL check=$CHECK_URL interval=${INTERVAL}s min_gap=${MIN_GAP}s keepalive=${KEEPALIVE}s"
if [ -s "$JAR" ]; then
  log "cookie jar present — using persisted (rotated) mam_id"
  # A jar that predates SEED_USED (pre-2026-09-22 state) is attributed to the
  # current env seed, so the self-heal path only fires on a genuinely new seed.
  [ -s "$SEED_USED" ] || fp "${MAM_ID_SEED:-}" > "$SEED_USED"
else
  log "no cookie jar yet — will seed it from MAM_ID_SEED on first run"
fi

# Self-heal on start: the persisted cookie is dead and the operator has supplied a
# different seed (MAM_ID_SEEDBOX rotated in 1Password + this pod restarted).
if [ -e "$DEAD_FLAG" ] && [ -n "${MAM_ID_SEED:-}" ] && [ "$(fp "$MAM_ID_SEED")" != "$(cat "$SEED_USED" 2>/dev/null)" ]; then
  seed_jar
  rm -f "$IP_CACHE"
  log "session was dead and a NEW MAM_ID_SEED is present — re-seeded the cookie jar; will register after MIN_GAP"
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

  # 2) With the exit IP unchanged since our last successful registration there is
  #    nothing to register — but once per KEEPALIVE we still call, to prove the
  #    session is alive. While the session is dead this is also the (daily) retry.
  if [ -n "$last_ip" ] && [ "$cur_ip" = "$last_ip" ]; then
    if [ "$since" -lt "$KEEPALIVE" ]; then
      if [ -e "$DEAD_FLAG" ]; then
        log "MAM_SESSION_DEAD (cached): MAM rejected our mam_id — $(cat "$DEAD_FLAG" 2>/dev/null); next validation in $(( KEEPALIVE - since ))s"
      else
        log "No exit-IP change ($cur_ip); no update needed"
      fi
      sleep "$INTERVAL"; continue
    fi
    log "keepalive: validating the session (last call ${since}s ago)"
  fi

  # 3) Rate-limit guard: never call more than once per MIN_GAP, even on a rapid re-change.
  if [ "$last_call" -ne 0 ] && [ "$since" -lt "$MIN_GAP" ]; then
    wait=$(( MIN_GAP - since ))
    log "exit IP changed (${last_ip:-none} -> $cur_ip) but last call was ${since}s ago (<${MIN_GAP}s) — deferring ${wait}s"
    sleep "$wait"; continue
  fi

  # 4) Seed the jar once (first run), then always drive it from the jar so the
  #    server-rotated mam_id is captured and reused.
  if [ ! -s "$JAR" ]; then
    seed_jar
    log "seeded cookie jar from MAM_ID_SEED (first run)"
  fi

  # 5) Register / validate. -b/-c on the same jar loads the current mam_id and
  #    writes back whatever (possibly rotated) cookie the server returns. No -f:
  #    a dead session answers HTTP 403 with a JSON body we need to read.
  out=$(curl -sS --max-time 30 -b "$JAR" -c "$JAR" -w '\n%{http_code}' "$URL" 2>"$STATE_DIR/mam.err")
  rc=$?
  echo "$now" > "$LAST_CALL"   # count EVERY call against the hourly limit (conservative)

  if [ "$rc" -ne 0 ]; then
    log "ERROR curl rc=$rc calling dynamicSeedbox.php: $(cat "$STATE_DIR/mam.err" 2>/dev/null)"
    sleep "$INTERVAL"; continue
  fi
  code=$(printf '%s' "$out" | tail -n 1)
  resp=$(printf '%s' "$out" | sed '$d')

  # 6) Interpret the reply. Update the IP cache only on a confirmed success.
  case "$resp" in
    *Completed*|*completed*)
      echo "$cur_ip" > "$IP_CACHE"; rm -f "$DEAD_FLAG"
      log "Completed: registered seedbox IP ${last_ip:-none} -> $cur_ip" ;;
    *"No change"*|*"No Change"*|*"no change"*)
      echo "$cur_ip" > "$IP_CACHE"; rm -f "$DEAD_FLAG"
      log "No Change: MAM already had $cur_ip as the seedbox IP (session alive)" ;;
    *"Last Change Too Recent"*|*"too recent"*)
      log "WARN MAM rate-limited us (Last Change Too Recent) — will retry next cycle" ;;
    *'"Success":false'*|*'"Success": false'*)
      # e.g. HTTP 403 {"Success":false,"msg":"No Session Cookie"} — the session is
      # gone (password change, manual removal, expiry). Keep IP_CACHE so the next
      # attempt is the daily keepalive, not an hourly hammer with a dead cookie.
      printf 'http=%s %s' "$code" "$resp" > "$DEAD_FLAG"
      log "MAM_SESSION_DEAD: MAM rejected our mam_id (http=$code): $resp — create a new dynamic-seedbox session at MAM, store it as MAM_ID_SEEDBOX, restart this pod"
      ;;
    *)
      log "ERROR unexpected dynamicSeedbox.php response (http=$code): $resp" ;;
  esac

  sleep "$INTERVAL"
done
