#!/bin/sh
# One Plex hardware-transcode check. POSIX sh + curl, so it runs in the Plex container
# and in the alpine canary image alike.
#
#   probe.sh <session-id> [force]
#
# Where it runs decides how it reaches Plex:
#   - inside a Plex container (kubectl exec): 127.0.0.1:32400, with the token read from
#     Preferences.xml. The token never leaves the pod.
#   - anywhere else, with CANARY_PLEX_URL and CANARY_PLEX_TOKEN set: that server over HTTP
#     (HaynesTower, which is not in the cluster).
#
# Prints exactly one logfmt line that starts with result=. The token is never printed.
#   result=hw      a video transcode decoded AND encoded in hardware
#   result=not-hw  a video transcode ran, but its decode or encode was software
#   result=busy    someone is streaming and no usable video transcode is running: no test
#   result=error   the check could not run (see reason=)
#   mode=synthetic nobody was streaming (or `force`), so it ran a ~20 s test transcode and
#                  stopped it: 4K HDR -> 1080p SDR when the library has one (decode, tone
#                  map, encode), else 4K HEVC -> 1080p, else a 1080p episode -> 720p
#   mode=observed  a real h264/hevc video transcode was already running, and its hardware
#                  flags are the result. Both iGPU generations decode those codecs, so a
#                  software decode there is a fault, not a codec gap.
# Used by the daily canary (canary.sh next to this file) and by scripts/plex-igpu/check.sh.
# Runbook: .agents/runbooks/plex-igpu-transcode.md
S=${1:?usage: probe.sh <session-id> [force]}
FORCE=${2:-}
PMS="/config/Library/Application Support/Plex Media Server"
B=${CANARY_PLEX_URL:-http://127.0.0.1:32400}
T=${CANARY_PLEX_TOKEN:-$(sed -n 's/.*PlexOnlineToken="\([^"]*\)".*/\1/p' "$PMS/Preferences.xml" 2>/dev/null)}
[ -n "$T" ] || { echo "result=error mode=none reason=no-token"; exit 0; }

q() { case "$1" in *\?*) echo '&' ;; *) echo '?' ;; esac; }
get() { curl -fs -m 20 -H 'Accept: application/xml' "$B$1$(q "$1")X-Plex-Token=$T" 2>/dev/null; }
attr() { grep -oE " $1=\"[^\"]*\"" | head -1 | cut -d'"' -f2; }
# $1 = one <TranscodeSession ...> element
verdict() {
  if [ -n "$(echo "$1" | attr transcodeHwDecoding)" ] && [ -n "$(echo "$1" | attr transcodeHwEncoding)" ]; then
    echo hw
  else
    echo not-hw
  fi
}
flags() {
  echo "source=$(echo "$1" | attr sourceVideoCodec) hwRequested=$(echo "$1" | attr transcodeHwRequested)" \
    "hwDecoding=$(echo "$1" | attr transcodeHwDecoding) hwEncoding=$(echo "$1" | attr transcodeHwEncoding)"
}

SESS=$(get /status/sessions) || { echo "result=error mode=none reason=sessions-unreadable"; exit 0; }
N=$(echo "$SESS" | grep -oE '<MediaContainer size="[0-9]+"' | grep -oE '[0-9]+' | head -1)
[ -n "$N" ] || { echo "result=error mode=none reason=sessions-unparsable"; exit 0; }

if [ "$N" != 0 ] && [ "$FORCE" != force ]; then
  OBS=$(echo "$SESS" | grep -oE '<TranscodeSession [^>]*' | grep 'videoDecision="transcode"' |
    grep -E 'sourceVideoCodec="(h264|hevc)"')
  if [ -n "$OBS" ]; then
    # Any real transcode that is not full hardware decides the result.
    BAD=$(echo "$OBS" | while IFS= read -r l; do [ "$(verdict "$l")" = hw ] || echo "$l"; done | head -1)
    PICK=${BAD:-$(echo "$OBS" | head -1)}
    echo "result=$(verdict "$PICK") mode=observed streams=$N transcodes=$(echo "$OBS" | wc -l | tr -d ' ') $(flags "$PICK")"
  else
    echo "result=busy mode=none streams=$N"
  fi
  exit 0
fi

# $1 = library type, $2 = filter; prints the first matching ratingKey of any such library.
pick() {
  for sec in $(get /library/sections | grep -oE "<Directory [^>]*type=\"$1\"[^>]*" | grep -oE ' key="[0-9]+"' | cut -d'"' -f2); do
    k=$(get "/library/sections/$sec/all?$2&X-Plex-Container-Start=0&X-Plex-Container-Size=1" | grep -oE '<Video [^>]*' | attr ratingKey)
    [ -n "$k" ] && { echo "$k"; return; }
  done
}
TEST=4k-hdr RES=1920x1080 KBPS=10000
KEY=$(pick movie "type=1&resolution=4k&hdr=1")
[ -n "$KEY" ] || { TEST=4k-hevc; KEY=$(pick movie "type=1&resolution=4k&videoCodec=hevc"); }
[ -n "$KEY" ] || { TEST=1080p RES=1280x720 KBPS=2000; KEY=$(pick show "type=4&resolution=1080&sort=addedAt:desc"); }
[ -n "$KEY" ] || { echo "result=error mode=synthetic reason=no-test-title"; exit 0; }

C="X-Plex-Client-Identifier=plex-hw-canary&X-Plex-Product=Plex%20Web&X-Plex-Platform=Chrome&X-Plex-Device=Linux&X-Plex-Token=$T"
Q="path=%2Flibrary%2Fmetadata%2F$KEY&mediaIndex=0&partIndex=0&protocol=hls&fastSeek=1&directPlay=0&directStream=0&directStreamAudio=1&videoQuality=100&videoResolution=$RES&maxVideoBitrate=$KBPS&session=$S&$C"
U=$B/video/:/transcode/universal
curl -s -m 20 -o /dev/null "$U/decision?$Q"
# The transcoder starts once a client fetches the variant playlist and a segment.
V=$(curl -s -m 30 "$U/start.m3u8?$Q" | grep -v '^#' | head -1 | tr -d '\r')
case "$V" in session/*) ;; *) echo "result=error mode=synthetic test=$TEST title=$KEY reason=start-failed"; exit 0 ;; esac
SEG=$(curl -s -m 30 "$U/$V$(q "$V")X-Plex-Token=$T" | grep -v '^#' | head -1 | tr -d '\r')
[ -n "$SEG" ] && curl -s -m 30 -o /dev/null "$U/session/$S/base/$SEG$(q "$SEG")X-Plex-Token=$T"
LAST=""
for i in 1 2 3 4; do
  sleep 5
  L=$(get /transcode/sessions | grep -oE "<TranscodeSession [^>]*key=\"$S\"[^>]*|<TranscodeSession key=\"$S\"[^>]*" | head -1)
  [ -n "$L" ] && LAST=$L
done
curl -s -m 20 -o /dev/null "$U/stop?session=$S&$C"
[ -n "$LAST" ] || { echo "result=error mode=synthetic test=$TEST title=$KEY reason=no-session"; exit 0; }
# The tone-map filter is only in the PMS log, which only the in-pod run can read.
TM=unknown
if [ -z "${CANARY_PLEX_URL:-}" ] && [ -r "$PMS/Logs/Plex Media Server.log" ]; then
  TM=$(grep -F "$S" "$PMS/Logs/Plex Media Server.log" | grep -oE 'tonemap_[a-z]+' | head -1)
  TM=${TM:-none}
fi
echo "result=$(verdict "$LAST") mode=synthetic test=$TEST title=$KEY $(flags "$LAST") tonemap=$TM" \
  "speed=$(echo "$LAST" | attr speed | cut -c1-4)"
