#!/usr/bin/env bash
# OP278: public pin + broker-delivered tmpfs IdentityFile; no key contents read here.
set -euo pipefail
if [[ $# != 1 || ! $1 =~ ^grant-[a-zA-Z0-9-]+$ ]]; then
  echo 'Usage: check.sh grant-ID (Active hw-ssh credential lease)' >&2
  exit 2
fi
trust="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/known_hosts"
expected='SHA256:tEwBOujUwtwGmK1vZ8tYn93ojOoP9olb/da5WamdmCc'
[[ $(wc -l < "$trust") == 1 ]] || { echo 'Unexpected public trust records' >&2; exit 1; }
actual=$(ssh-keygen -E sha256 -lf "$trust")
[[ $actual == "256 $expected pikvm.haynesnetwork (ED25519)" ]] || {
  echo 'Public pin artifact differs from approved provenance' >&2; exit 1;
}
: "${XDG_RUNTIME_DIR:?broker tmpfs runtime directory required}"
key="$XDG_RUNTIME_DIR/dev-env-hwssh/$1/key"
[[ -f $key && ! -L $key && $(stat -f -c %T -- "$key") == tmpfs ]] || {
  echo 'Broker credential file unavailable in tmpfs' >&2; exit 1;
}
# -F /dev/null disables user/system SSH config, including ProxyCommand/ProxyJump.
# HostKeyAlias binds the fixed numeric destination to the established public record.
# OpenSSH verifies the server host key before sending public-key authentication.
exec timeout --signal=TERM --kill-after=5s 30s ssh -F /dev/null -T -p 22 \
  -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
  -o StrictHostKeyChecking=yes -o HostKeyAlgorithms=ssh-ed25519 \
  -o HostKeyAlias=pikvm.haynesnetwork -o CheckHostIP=no \
  -o "UserKnownHostsFile=$trust" -o GlobalKnownHostsFile=/dev/null \
  -o KnownHostsCommand=none -o VerifyHostKeyDNS=no -o UpdateHostKeys=no \
  -o IdentitiesOnly=yes -o IdentityAgent=none -o "IdentityFile=$key" \
  -o CertificateFile=none -o PreferredAuthentications=publickey \
  -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
  -o ForwardAgent=no -o ForwardX11=no -o ClearAllForwardings=yes \
  -o ControlMaster=no -o ControlPath=none -o ControlPersist=no \
  root@192.168.40.33 'hostname; id; date -u'
