#!/usr/bin/env bash
# pve — Proxmox VE API helper for the dev-env pod (saga dev-env backlog 14).
# Manual: .agents/runbooks/proxmox-access.md (haynes-ops).
#
# Tiers (chosen from the environment, never from files):
#   READ      PVE_TOKEN_ID / PVE_TOKEN_SECRET            prometheus@pve!exporter, PVEAuditor
#   OPERATOR  PVE_OPERATOR_TOKEN_ID / _SECRET             dev-env@pve!operator, VM-scoped
# The operator token is used when present unless --ro is given. Writes need --yes and
# print the resolved request first. Every privilege check is SERVER-SIDE (PVE ACLs) —
# this script only makes the call.
set -euo pipefail

PVE_API_URL="${PVE_API_URL:-https://pvedash.haynesnetwork}"
RO=0; YES=0; RAW=0; ANY=0
# Typo guard (not a security boundary — the token's ACL is PVE's business): `vm` verbs
# only touch the three Talos workers unless --any is given.
WORKERS=(103 108 113)

usage() {
  cat <<'USAGE'
usage: pve [--ro] [--yes] [--raw] [--node <name>] <command>

  ha                          HA resources + quorum/CRM/LRM state (the fence picture)
  nodes                       node status / uptime / load
  guests [--onboot]           every guest: id name type node status (+ onboot per guest)
  vm <id> config              a guest's config
  vm <id> onboot 0|1          set autostart            (operator, --yes)
  vm <id> start|stop|shutdown|reset                     (operator, --yes)
  get <path> [k=v ...]        raw GET  /api2/json<path>
  post|put|delete <path> [k=v ...]                      (--yes)

  --ro     force the read-only token      --yes   confirm a write
  --raw    print the API JSON unformatted --node  talk to <name>.haynesnetwork:8006 directly
  --any    allow `vm` verbs on guests other than the Talos workers (103/108/113)
USAGE
}

log()  { printf '%s\n' "$*" >&2; }
die()  { log "pve: $*"; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing $1"; }
need curl; need jq

# ---- flags ------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ro)   RO=1; shift ;;
    --yes)  YES=1; shift ;;
    --raw)  RAW=1; shift ;;
    --any)  ANY=1; shift ;;
    --node) [[ $# -ge 2 ]] || die "--node needs a name"; PVE_API_URL="https://$2.haynesnetwork:8006"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) break ;;
  esac
done
[[ $# -ge 1 ]] || { usage; exit 1; }

# ---- token ------------------------------------------------------------------
TIER=""
if [[ $RO -eq 0 && -n "${PVE_OPERATOR_TOKEN_ID:-}" && -n "${PVE_OPERATOR_TOKEN_SECRET:-}" ]]; then
  TOKEN="${PVE_OPERATOR_TOKEN_ID}=${PVE_OPERATOR_TOKEN_SECRET}"; TIER="operator"
elif [[ -n "${PVE_TOKEN_ID:-}" && -n "${PVE_TOKEN_SECRET:-}" ]]; then
  TOKEN="${PVE_TOKEN_ID}=${PVE_TOKEN_SECRET}"; TIER="read"
else
  die "no PVE token in the environment (PVE_TOKEN_ID/_SECRET) — backlog 14 PR B not deployed yet? Do not copy one from another pod."
fi

# ---- transport --------------------------------------------------------------
# api METHOD PATH [k=v ...]  → prints the JSON body; non-2xx exits 1 with PVE's message.
api() {
  local m="$1" p="$2"; shift 2
  local args=() kv
  for kv in "$@"; do args+=(--data-urlencode "$kv"); done
  local url="${PVE_API_URL}/api2/json${p}"
  local out code
  if [[ "$m" == "GET" ]]; then
    out="$(curl -sS -k -G "${args[@]}" -H "Authorization: PVEAPIToken=${TOKEN}" -w '\n%{http_code}' "$url")"
  else
    out="$(curl -sS -k -X "$m" "${args[@]}" -H "Authorization: PVEAPIToken=${TOKEN}" -w '\n%{http_code}' "$url")"
  fi
  code="${out##*$'\n'}"; out="${out%$'\n'*}"
  if [[ "$code" != 2* ]]; then
    local msg; msg="$(printf '%s' "$out" | jq -r '.message // .errors // empty' 2>/dev/null || true)"
    [[ -n "$msg" ]] || msg="${out:-(empty body)}"
    log "pve: HTTP $code on $m $p — $msg"
    return 1
  fi
  printf '%s' "$out"
}
write() {  # write METHOD PATH [k=v ...] — the --yes gate
  local m="$1" p="$2"
  [[ $RO -eq 0 ]] || die "refusing $m $p: --ro"
  [[ "$TIER" == "operator" ]] || die "refusing $m $p: only the read token is present (operator token not deployed)"
  log "→ $m ${PVE_API_URL}/api2/json${p} ${*:3}  [tier=$TIER]"
  [[ $YES -eq 1 ]] || die "add --yes to perform this write"
  api "$@"
}
show() { if [[ $RAW -eq 1 ]]; then cat; else jq . ; fi; }
# tbl — align TSV columns (the image has no `column`; written for mawk, not gawk)
tbl() { awk -F'\t' '{ n=split($0,c,"\t"); rows[NR]=$0; for(i=1;i<=n;i++) if(length(c[i])>w[i]) w[i]=length(c[i]) }
  END { for(r=1;r<=NR;r++){ n=split(rows[r],c,"\t"); line=""; for(i=1;i<=n;i++){ line=line c[i]; if(i<n){ pad=w[i]-length(c[i])+2; for(k=0;k<pad;k++) line=line " " } } print line } }'; }

# vm_locate ID → "node type"  (type: qemu|lxc) from /cluster/resources; empty if unknown
vm_locate() {
  local out; out="$(api GET /cluster/resources type=vm)" || return 1
  jq -r --argjson id "$1" '.data[] | select(.vmid==$id) | "\(.node) \(.type)"' <<<"$out"
}

# ---- commands ---------------------------------------------------------------
cmd="$1"; shift
case "$cmd" in
  ha)
    log "# HA resources (tier=$TIER via $PVE_API_URL)"
    api GET /cluster/ha/resources | jq -r '.data[] | [.sid, .state, (.group // "-"), (.comment // "")] | @tsv' \
      | { printf 'SID\tSTATE\tGROUP\tCOMMENT\n'; cat; } | tbl
    log "# manager status"
    api GET /cluster/ha/status/current \
      | jq -r '.data[] | [.type, (.node // "-"), (.sid // "-"), (.status // .state // "-")] | @tsv' \
      | { printf 'TYPE\tNODE\tSID\tSTATUS\n'; cat; } | tbl
    ;;
  nodes)
    api GET /nodes | jq -r '.data[] | [.node, .status, ((.uptime // 0)/86400*10|round/10|tostring)+"d", ((.cpu // 0)*100|round|tostring)+"%", (((.mem // 0)/(.maxmem // 1))*100|round|tostring)+"% mem"] | @tsv' \
      | { printf 'NODE\tSTATUS\tUPTIME\tCPU\tMEM\n'; cat; } | tbl
    ;;
  guests)
    with_onboot=0; [[ "${1:-}" == "--onboot" ]] && with_onboot=1
    rows="$(api GET /cluster/resources type=vm | jq -r '.data[] | [.vmid, .name, .type, .node, .status] | @tsv' | sort -n)"
    if [[ $with_onboot -eq 1 ]]; then
      printf 'ID\tNAME\tTYPE\tNODE\tSTATUS\tONBOOT\n'
      while IFS=$'\t' read -r id name type node status; do
        ob="$(api GET "/nodes/$node/$type/$id/config" | jq -r '.data.onboot // 0')"
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$id" "$name" "$type" "$node" "$status" "$ob"
      done <<<"$rows"
    else
      printf 'ID\tNAME\tTYPE\tNODE\tSTATUS\n'; printf '%s\n' "$rows"
    fi | tbl
    ;;
  vm)
    [[ $# -ge 2 ]] || { usage; exit 1; }
    id="$1"; action="$2"; shift 2
    [[ "$id" =~ ^[0-9]+$ ]] || die "vm id must be numeric"
    if [[ $ANY -eq 0 && " ${WORKERS[*]} " != *" $id "* ]]; then
      die "guest $id is not a Talos worker (${WORKERS[*]}) — pass --any if you really mean it"
    fi
    loc="$(vm_locate "$id")" || die "could not read /cluster/resources (see above)"
    [[ -n "$loc" ]] || die "no guest with id $id"
    read -r node type <<<"$loc"
    case "$action" in
      config)  api GET "/nodes/$node/$type/$id/config" | show ;;
      onboot)  [[ "${1:-}" =~ ^[01]$ ]] || die "onboot wants 0 or 1"
               write PUT "/nodes/$node/$type/$id/config" "onboot=$1" | show ;;
      start|stop|shutdown|reset)
               write POST "/nodes/$node/$type/$id/status/$action" | show ;;
      *) die "unknown vm action '$action'" ;;
    esac
    ;;
  get)    [[ $# -ge 1 ]] || die "get needs a path"; p="$1"; shift; api GET "$p" "$@" | show ;;
  post)   [[ $# -ge 1 ]] || die "post needs a path"; p="$1"; shift; write POST "$p" "$@" | show ;;
  put)    [[ $# -ge 1 ]] || die "put needs a path"; p="$1"; shift; write PUT "$p" "$@" | show ;;
  delete) [[ $# -ge 1 ]] || die "delete needs a path"; p="$1"; shift; write DELETE "$p" "$@" | show ;;
  *) usage; exit 1 ;;
esac
