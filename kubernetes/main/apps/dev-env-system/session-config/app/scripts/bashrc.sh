# dev-env v2 session shell defaults. agentd makes ~/.bashrc source this file
# (DESIGN-001 D-40). GitOps-managed: haynes-ops
# kubernetes/main/apps/dev-env-system/session-config/app/scripts/bashrc.sh.
#
# No GH_TOKEN export, unlike v1 (D-49). The keeper re-mints /creds/gh_token every 40
# minutes and each token lasts 60. agentd's gh wrapper (~/.local/bin/gh) and git's
# credential helper read the file at every call (D-42). A token exported here would be
# copied into every process started from this shell, and die an hour later.

# ~/.local/bin first, so agentd's gh wrapper wins over the image's gh. A login shell's
# /etc/profile can reset PATH, so prepend it again here (a repeat entry is harmless).
export PATH="$HOME/.local/bin:$PATH"
