#!/usr/bin/env bash
# Notification identity only. Never modifies rescue, ownership or lifecycle.
# Sourced by respond.sh. Requires jq; no network, model or cluster commands.
sig_of() { printf '%s' "$1" | sha256sum | grep -oE '[0-9a-f]{64}' | head -1 | cut -c1-12; }
rescue_identity_qualified() {
  jq -e '
    .labels as $l | $l.alertname == "DevEnvRescueFailed"
    and ($l.session_uid // "" | test("^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"))
    and ($l.reason // "" | test("^[A-Za-z][A-Za-z0-9]{0,63}$"))
    and ($l.session_generation // "" | test("^[1-9][0-9]*$"))
    and ($l.risk_key // "" | test("^([a-f0-9]{64}|unclassified)$"))
    and ($l.pending_delete == "true" or $l.pending_delete == "false")
  ' >/dev/null 2>&1 <<< "$1"
}

rescue_incident_owned() {
  rescue_identity_qualified "$1" || return 1
  jq -e '
    .labels as $l
    | ($l.reason == "NotProven" or $l.reason == "BootstrapInterrupted")
      and ($l.risk_key | test("^[a-f0-9]{64}$"))
      and ($l.owner_uid // "" | test("^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"))
  ' >/dev/null 2>&1 <<< "$1"
}

incident_claim_key() {
  local identity
  if rescue_identity_qualified "$1"; then
    # Explicit closed material facts supplement fingerprint+startsAt. No retry
    # timestamp, free-text annotation or native-availability claim enters a key.
    identity="$(jq -c '[.fingerprint,.startsAt,.labels.session_uid,.labels.session_generation,.labels.reason,.labels.risk_key,.labels.pending_delete]' <<< "$1")" || return 1
  else
    identity="$(jq -r '(.fingerprint // "") + "|" + (.startsAt // "")' <<< "$1")" || return 1
  fi
  sig_of "$identity"
}

incident_age_eligible() {
  # A persistent qualified rescue can change material risk without resolving.
  # Its distinct durable key, rather than an old startsAt/max-age, governs that
  # new diagnosis. Other alerts retain the existing age/spend policy.
  rescue_identity_qualified "$1" && return 0
  TZ=UTC jq -e --argjson now "$2" --argjson maxage "$3" '
    (((.startsAt | sub("\\.[0-9]+";"") | fromdateiso8601?) // $now) > ($now - $maxage))
  ' >/dev/null 2>&1 <<< "$1"
}
