# Git Backup

I don't think this is all the way there as my local and global git settings will likely be lost but this is how I've configured `/config` to be a git repo:

## Mount SSH key via secret

I grabbed the contents of `~/.ssh/id_ed25519` from my desktop and created entry in 1Password with the key. Then I pulled it down via:

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/external-secrets.io/externalsecret_v1beta1.json
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: esphome-deploykey
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword-connect
  target:
    name: esphome-deploykey
    creationPolicy: Owner
  data:
    - secretKey: id_ed25519
      remoteRef:
        key: esphome
        property: github_deploy_key
```

Map to somewhere the 1000 user can get the secret:

```yaml
      deploy-key:
        type: secret
        name: esphome-deploykey
        defaultMode: 256
        globalMounts:
          - path: /home/coder/.ssh/id_ed25519
            subPath: id_ed25519
```

## Setup repo in GitHub

> **Repo Private Until I Review for HA API Tokens**

Just created one using default settings (no .gitignore) [here](https://github.com/thaynes43/esphome-config). An esphome centric gitignore was created automattically somehow but I modified it slightly:

`.gitignore`:

```bash
# Gitignore settings for ESPHome
# This is an example and may include too much for your use-case.
# You can modify this file to suit your needs.
/.esphome/
/secrets.yaml
/.vscode/
```

## Setup repo in vscode

```bash
git config --global init.defaultBranch main
git init
git config --global --add safe.directory /config
git config --global user.email "thaynes43@gmail.com"
git config --global user.name "Tom Haynes"
git config --local core.sshCommand "/usr/bin/ssh -i /home/coder/.ssh/id_ed25519"
git remote add origin https://github.com/thaynes43/esphome-config.git
git add .
git commti -m "Initial commit"
git push -u origin main
```

After this I had to authorize github against a code it gave me and my credentials. 

### Note for updating .gitignore

Run `git rm -rf --cached .` if you changed `.gitignore` and you want to remove new exclusions.