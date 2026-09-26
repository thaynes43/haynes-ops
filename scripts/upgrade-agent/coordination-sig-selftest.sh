#!/usr/bin/env bash
# Self-test for the regression signature the health gate and shepherd triage share
# (coordination-lib.sh: sig_key_of -> sig_of).
#
# WHY THIS EXISTS
# ---------------
# The sig keys BOTH the gate's re-page throttle (`last_paged`) and triage's
# at-most-once remediation claim (`attempted`). Until #3182 it hashed the whole
# regression list — Flux objects AND the pods listed beside them — so any pod churn
# re-keyed an unchanged incident. 2026-09-25: one failing HelmRelease
# (ai/ollama-prime) paged three times at priority 1 (07:00, 07:30, 08:30Z) under
# three sigs: once because an escalation deleted a co-listed corpse, once because the
# crashlooping pod itself dropped out of the persisted-pods query. It also spawned a
# second remediate run and a duplicate escalation session.
#
# Both ways to be wrong are silent: re-key too eagerly (page storms, duplicate
# sessions) or merge distinct incidents (a new failure deduped into an old one's
# throttle). Every boundary is pinned below.
#
# Rule, same as scripts/work-order-lane-selftest.sh: the functions under test are
# EXTRACTED from the shipped lib, never copy-pasted, so the test cannot drift from
# what the cluster runs.
#
# Usage: scripts/upgrade-agent/coordination-sig-selftest.sh
# Requires: bash, GNU/busybox sed, sha256sum (or shasum/openssl). No cluster access.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="${SRC:-$REPO_ROOT/kubernetes/main/apps/upgrade-agent/health-gate/app/resources/coordination-lib.sh}"
[ -r "$SRC" ] || { echo "FATAL: cannot read $SRC"; exit 1; }

extract() {  # $1=function name — definition line through its closing brace
  awk -v fn="$1" '
    $0 ~ "^" fn "\\(\\)" { inside = 1 }
    inside { print }
    inside && /^}/ { exit }
  ' "$SRC"
}
for fn in sig_of sig_key_of; do
  body="$(extract "$fn")"
  case "$body" in *"$fn()"*) ;; *) echo "FATAL: could not extract $fn() from $SRC"; exit 1 ;; esac
  eval "$body" || { echo "FATAL: extracted $fn() does not parse"; exit 1; }
done

sig() { sig_of "$(sig_key_of "$(printf '%s\n' "$@" | LC_ALL=C sort -u)")"; }   # as gate/triage call it

fails=0
same() {  # $1=name, then two lists separated by --
  local name="$1"; shift; local a=() b=() side=a
  for x in "$@"; do [ "$x" = "--" ] && { side=b; continue; }; [ "$side" = a ] && a+=("$x") || b+=("$x"); done
  local sa sb; sa="$(sig "${a[@]}")"; sb="$(sig "${b[@]}")"
  if [ -n "$sa" ] && [ "$sa" = "$sb" ]; then echo "ok    same: $name ($sa)"; else echo "FAIL  same: $name ($sa vs $sb)"; fails=$((fails+1)); fi
}
differ() {
  local name="$1"; shift; local a=() b=() side=a
  for x in "$@"; do [ "$x" = "--" ] && { side=b; continue; }; [ "$side" = a ] && a+=("$x") || b+=("$x"); done
  local sa sb; sa="$(sig "${a[@]}")"; sb="$(sig "${b[@]}")"
  if [ -n "$sa" ] && [ -n "$sb" ] && [ "$sa" != "$sb" ]; then echo "ok    differ: $name"; else echo "FAIL  differ: $name ($sa vs $sb)"; fails=$((fails+1)); fi
}

HR=flux/HelmRelease/ai/ollama-prime
KS=flux/Kustomization/ai/ollama-prime
OP=pod/ai/ollama-prime-95558898d-6v494
YT=pod/downloads/ytdl-sub-peloton-29837715-qhsm9

# ── the 2026-09-25 incident: all three lists must be ONE sig ──
same   "07:00 -> 07:30Z: co-listed corpse deleted"        "$HR" "$OP" "$YT" -- "$HR" "$OP"
same   "07:30 -> 08:30Z: crashlooping pod dropped out"    "$HR" "$OP"       -- "$HR"
same   "a new replacement pod of the same Deployment"     "$HR" "$OP"       -- "$HR" pod/ai/ollama-prime-7c9d8f6b5-x2kzq
# a flux-only list keys exactly as before the change (backward compatible state)
[ "$(sig "$HR")" = "$(sig_of "$HR")" ] && echo "ok    flux-only list: unchanged sig" || { echo "FAIL  flux-only sig changed"; fails=$((fails+1)); }

# ── distinct Flux incidents stay distinct ──
differ "a second Flux object starts failing"               "$HR" -- "$HR" flux/HelmRelease/media/plex
differ "a different Flux object"                           "$HR" -- flux/HelmRelease/media/plex
differ "HelmRelease alone vs HelmRelease + Kustomization"  "$HR" -- "$HR" "$KS"

# ── pod-only regressions: owner-stable, owner-distinct ──
same   "Deployment pod replaced"            pod/ai/ollama-prime-95558898d-6v494 -- pod/ai/ollama-prime-6f7b9c5d4-bq2lm
same   "CronJob pod of the next run"        "$YT" -- pod/downloads/ytdl-sub-peloton-29838840-dd7jc
same   "DaemonSet pod recreated"            pod/kube-system/cilium-tg8b7 -- pod/kube-system/cilium-w9zhx
differ "two different Deployments"          "$OP" -- pod/observability/dns-canary-5f4cf969d4-l525m
differ "StatefulSet ordinals are distinct"  pod/database/postgres16-7 -- pod/database/postgres16-11
differ "a vowel segment is a real name"     pod/ai/whisper-alpha -- pod/ai/whisper-omega
same   "order does not matter"              "$OP" pod/database/postgres16-7 -- pod/database/postgres16-7 "$OP"

# the normalised forms themselves (what an operator sees if they debug the key)
got="$(sig_key_of "$OP"$'\n'"$YT"$'\n'pod/kube-system/cilium-tg8b7$'\n'pod/database/postgres16-7)"
want=$'pod/ai/ollama-prime\npod/database/postgres16-7\npod/downloads/ytdl-sub-peloton\npod/kube-system/cilium'
[ "$got" = "$want" ] && echo "ok    normalised pod keys" || { echo "FAIL  normalised pod keys:"; printf '%s\n' "$got"; fails=$((fails+1)); }

echo
[ "$fails" -eq 0 ] && { echo "ALL PASS"; exit 0; } || { echo "$fails FAILED"; exit 1; }
