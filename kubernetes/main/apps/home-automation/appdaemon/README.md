## AppDaemon config editing from Windows (Cursor)

This AppDaemon deployment includes an **SFTP sidecar** that mounts the same PVC as AppDaemon and exposes it over SSH/SFTP **on port 2222**.

The PVC contents are available at:

- **In the AppDaemon container**: `/conf`
- **In the SFTP sidecar**: `/config/conf`

The intended workflow on Windows is:

- run a local `kubectl port-forward`
- mount `/config/conf` as a Windows drive using SSHFS
- open the mounted drive as a folder in Cursor

---

## Quick start (already set up once on this machine)

### 1) Port-forward the SFTP port

Keep this running in a terminal:

```bash
kubectl -n home-automation port-forward svc/appdaemon 2222:2222
```

### 2) Mount the remote directory as drive `X:`

In PowerShell:

```powershell
net use X: \\sshfs.kr\appdaemon@localhost!2222\config\conf /persistent:no
```

### 3) Open in Cursor

Open folder `X:\` in Cursor.

### Unmount

```powershell
net use X: /delete
```

---

## Full Windows setup (new machine runbook)

### Tools to install

- **kubectl**: needed for port-forwarding to the cluster
- **WinFsp**: required for filesystem mounting
- **SSHFS-Win**: provides the `\\sshfs...` network filesystem provider used by `net use`

---

## SSH key setup

The SFTP sidecar is configured for **public key auth only** (password auth disabled).

### 1) Create (or reuse) an SSH keypair on Windows

If you do not already have a key, generate one:

```powershell
ssh-keygen -t ed25519 -a 64 -C "thaynes@windows"
```

This typically creates:

- Private key: `C:\Users\<you>\.ssh\id_ed25519`
- Public key: `C:\Users\<you>\.ssh\id_ed25519.pub`

### 2) Add your public key to the secret source

In 1Password (the item named **`appdaemon`**) add/update:

- **`SSH_PUBLIC_KEY`**: paste your public key line (starts with `ssh-ed25519 ...`)

Notes:
- If you want multiple Windows machines to connect without reconfiguring, you have two choices:
  - **Reuse the same private key** on each machine (copy `id_ed25519` securely), and keep **one** public key in 1Password; or
  - Put **multiple public keys** into `SSH_PUBLIC_KEY` (one per line). The sidecar will add them to `authorized_keys`.

---

## SSHFS drive mounting details (why we copy the key)

SSHFS-Win’s `\\sshfs.k` / `\\sshfs.kr` key-auth mode expects the private key at:

- `%USERPROFILE%\.ssh\id_rsa`

If your key is `id_ed25519`, the simplest local workaround is to copy it:

```powershell
Copy-Item $env:USERPROFILE\.ssh\id_ed25519 $env:USERPROFILE\.ssh\id_rsa
```

This does **not** change the key itself; it just places it at the filename SSHFS-Win expects.

Then mount the drive using:

```powershell
net use X: \\sshfs.kr\appdaemon@localhost!2222\config\conf /persistent:no
```

### Why `\\sshfs.kr\...`?

- `kr` means: **key auth** + **remote path is absolute (root-relative)**.
- We need this because we are mounting `/config/conf` (not a home-directory relative path).

---

## Common troubleshooting

### “Access denied” or it prompts for a password

- Confirm port-forward is running:

```bash
kubectl -n home-automation port-forward svc/appdaemon 2222:2222
```

- Confirm you used the `kr` prefix and the port:

```powershell
net use X: \\sshfs.kr\appdaemon@localhost!2222\config\conf /persistent:no
```

- Confirm your key exists at the expected path:

```powershell
Test-Path $env:USERPROFILE\.ssh\id_rsa
```

### Host key warnings

The SFTP sidecar stores SSH host keys ephemerally, so after pod restarts your SSH client may warn about host key changes.
This is expected in this setup. Remove/update the old entry in your `known_hosts` if prompted.

---

## Notes on reusing the same key across machines

If you want to go back-and-forth between machines *without changing anything on the cluster*:

- **Best UX**: securely copy your private key (`id_ed25519`) to the other machine and keep using the same public key stored in 1Password.
- **Best separation**: generate a new key per machine and add all public keys to the `SSH_PUBLIC_KEY` field (one per line).

