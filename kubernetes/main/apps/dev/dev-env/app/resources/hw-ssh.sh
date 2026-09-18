#!/usr/bin/env bash
# hw-ssh — SSH to the non-Talos hardware from inside dev-env (2026-09-17, backlog 14
# "SSH tier"). One ed25519 key for all of it, materialised by dev-init from the pod
# env (HW_SSH_PRIVATE_KEY_B64 ← 1Password `dev-env`) to ~/.ssh/dev-env-hw.
#
#   Proxmox nodes  → user `dev-env`, key-only, sudo allowlist (see runbook) — root-
#                    equivalent in practice via `sudo qm`, same tier Tom ruled for the
#                    API operator token (2026-09-09, Q-1).
#   HaynesTower    → root (Unraid has no other SSH user); key in /boot/config/ssh/root.pubkeys.
#
# usage: hw-ssh list
#        hw-ssh <host> [command...]         # no command = interactive shell
#        hw-ssh pve-all <command...>        # same command on all five PVE nodes
#        hw-ssh --raw <user@fqdn> [cmd...]  # anything else the CNP allows on :22
#
# Rules (runbook .agents/runbooks/proxmox-access.md): read-first; `declare-activity`
# before anything disruptive; no node reboots or Unraid array stop/start from here.
set -uo pipefail

KEY="${HW_SSH_KEY:-$HOME/.ssh/dev-env-hw}"
KNOWN="$HOME/.ssh/known_hosts_hw"
DOMAIN=haynesnetwork
PVE_NODES=(haynesintelligence twin-top twin-bottom pve04 pve-filet02)

die() { printf 'hw-ssh: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '11,15p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

target() {  # host alias → user@fqdn
  case "$1" in
    haynesintelligence|twin-top|twin-bottom|pve04|pve-filet02) printf 'dev-env@%s.%s' "$1" "$DOMAIN" ;;
    haynestower|unraid|tower) printf 'root@haynestower.%s' "$DOMAIN" ;;
    *) return 1 ;;
  esac
}

run() {  # user@fqdn [cmd...]
  local dest="$1"; shift
  [[ -r "$KEY" ]] || die "no key at $KEY — HW_SSH_PRIVATE_KEY_B64 not in the pod env yet? (1Password dev-env field + pod bounce; see runbook)"
  exec_or_run ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode="$([[ $# -eq 0 ]] && echo no || echo yes)" \
    -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$KNOWN" -o ConnectTimeout=10 \
    -o LogLevel=ERROR "$dest" "$@"
}
exec_or_run() { if [[ -n "${HW_SSH_NOEXEC:-}" ]]; then "$@"; else exec "$@"; fi; }

[[ $# -ge 1 ]] || usage 1
case "$1" in
  -h|--help|help) usage ;;
  list)
    for n in "${PVE_NODES[@]}"; do printf '%-20s %s\n' "$n" "$(target "$n")"; done
    printf '%-20s %s\n' haynestower "$(target haynestower)"
    ;;
  pve-all)
    shift; [[ $# -ge 1 ]] || die "pve-all needs a command"
    rc=0
    for n in "${PVE_NODES[@]}"; do
      printf '===== %s =====\n' "$n"
      HW_SSH_NOEXEC=1 run "$(target "$n")" "$@" || { rc=$?; printf 'hw-ssh: %s exited %s\n' "$n" "$rc" >&2; }
    done
    exit "$rc"
    ;;
  --raw)
    shift; [[ $# -ge 1 ]] || die "--raw needs user@host"
    dest="$1"; shift; run "$dest" "$@"
    ;;
  *)
    dest="$(target "$1")" || die "unknown host '$1' — try: hw-ssh list"
    shift; run "$dest" "$@"
    ;;
esac
