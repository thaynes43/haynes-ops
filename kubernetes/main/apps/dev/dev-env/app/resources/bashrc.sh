# dev-env shell defaults — sourced from ~/.bashrc (dev-init wires this up).
# GitOps-managed: kubernetes/main/apps/dev/dev-env/app/resources/bashrc.sh

# Fresh bot token per shell (the refresher sidecar re-mints every 40min; each Bash
# tool call / tmux pane is a new shell, so agents never hold a stale token long).
[ -s /creds/gh_token ] && export GH_TOKEN="$(cat /creds/gh_token)"

export PATH="$HOME/.local/bin:$PATH"
