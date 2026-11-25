# Source from the runbook's standalone Bash blocks after enabling errexit.
gate=Setup
trap 'status=$?; if [[ -n $gate ]] && (( status != 0 )); then
    printf "%s failed (exit %d)\n" "$gate" "$status" >&2
fi' EXIT

gate_passed() {
    printf '%s passed\n' "$gate" >&2
}

pvc_uid_matches() {
    local expected actual
    expected=$(cat "$1") || return
    actual=$(kubectl -n ai get pvc wan-video -o jsonpath='{.metadata.uid}') || return
    [[ -n $expected && -n $actual && $expected == "$actual" ]]
}
