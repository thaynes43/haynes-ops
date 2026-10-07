# dev-env shell defaults — sourced from ~/.bashrc (dev-init wires this up).
# GitOps-managed: kubernetes/main/apps/dev/dev-env/app/resources/bashrc.sh

# Fresh bot token for interactive shells and tmux panes (the refresher sidecar re-mints
# every 40min). NOT enough on its own: Claude Code's Bash tool does not run this file, so
# tool calls get the CLI's startup environment (a stale GH_TOKEN after an hour). `gh` is
# covered by the ~/.local/bin/gh wrapper dev-init writes (reads /creds/gh_token per call,
# haynes-ops#3473); git by its credential helper. Anything else that needs the token
# must read /creds/gh_token itself.
[ -s /creds/gh_token ] && export GH_TOKEN="$(cat /creds/gh_token)"

export PATH="$HOME/.local/bin:$PATH"
