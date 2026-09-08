#!/usr/bin/env bash
# mcp-json-to-codex-toml — render claude's mcp.json (the ONE GitOps MCP source,
# config/claude/mcp.json) as codex `[mcp_servers.*]` TOML on stdout. dev-init
# appends the result to the GitOps config.toml copy at boot, so both agents see
# the same servers and nobody maintains a second list. Mapping (claude → codex):
#   {command, args, env}          → command, args, env (literals) + env_vars (by name)
#   {type: http, url, headers}    → url, bearer_token_env_var / env_http_headers /
#                                   http_headers
#   {type: sse}                   → SKIPPED: codex speaks streamable HTTP only (its
#                                   `url` key); the server must offer /mcp instead.
# Placeholders: a value that is EXACTLY "${VAR}" becomes a by-NAME reference
# (env_vars / env_http_headers / bearer_token_env_var — codex reads the variable
# at connect time, so no secret is written to config.toml); "${VAR}" embedded in
# a longer string (a URL path, a non-Bearer header prefix) is expanded from this
# process's env, exactly as claude's registration does. Codex forwards only a
# core env to stdio servers, so every stdio server also gets FORWARD_VARS below
# (harmless when unset; PLAYWRIGHT_BROWSERS_PATH is the load-bearing one — the
# image points it at the PVC browser cache).
set -uo pipefail
src="${1:-/opt/dev-env/config/claude/mcp.json}"
FORWARD_VARS='["HOME","PATH","LANG","TERM","TMPDIR","XDG_CACHE_HOME","XDG_CONFIG_HOME","XDG_DATA_HOME","XDG_RUNTIME_DIR","PLAYWRIGHT_BROWSERS_PATH","UV_CACHE_DIR"]'

jq -r --argjson fwd "$FORWARD_VARS" '
  def q: @json;                                   # JSON string == TOML basic string here
  def key: if test("^[A-Za-z0-9_-]+$") then . else q end;
  def isvar: type == "string" and test("^\\$\\{[A-Za-z_][A-Za-z0-9_]*\\}$");
  def varname: capture("^\\$\\{(?<v>[A-Za-z_][A-Za-z0-9_]*)\\}$").v;
  def inline(m): "{ " + (m | to_entries | map("\(.key|key) = \(.value|q)") | join(", ")) + " }";

  .mcpServers | to_entries[] | .key as $name | .value as $s
  | ($s.type // (if $s.command then "stdio" else "http" end)) as $type
  | if $type == "sse" then
      "# \($name): type=sse in mcp.json — codex has no SSE client (streamable HTTP only), skipped"
    elif $type == "stdio" then
      ( ($s.env // {}) | to_entries ) as $env
      | ($env | map(select(.value | isvar)) | map(.value | varname)) as $byname
      | ($env | map(select(.value | isvar | not)) | from_entries) as $lit
      | [ "[mcp_servers.\($name|key)]",
          "command = \($s.command|q)",
          (if (($s.args // []) | length) > 0 then "args = [\($s.args | map(q) | join(", "))]" else empty end),
          (if ($lit | length) > 0 then "env = \(inline($lit))" else empty end),
          "env_vars = [\((($byname + $fwd) | unique | map(q)) | join(", "))]",
          "startup_timeout_sec = 60"
        ] | join("\n")
    else
      ($s.headers // {}) as $h
      | ($h | to_entries | map(select(.key | ascii_downcase == "authorization")) | first) as $auth
      | (if $auth and ($auth.value | test("^Bearer \\$\\{[A-Za-z_][A-Za-z0-9_]*\\}$"))
           then ($auth.value | ltrimstr("Bearer ") | varname) else null end) as $bearer
      | ($h | to_entries | map(select(($bearer != null and (.key | ascii_downcase) == "authorization") | not))) as $rest
      | ($rest | map(select(.value | isvar)) | map({key, value: (.value | varname)}) | from_entries) as $envh
      | ($rest | map(select(.value | isvar | not)) | from_entries) as $lith
      | [ "[mcp_servers.\($name|key)]",
          "url = \($s.url|q)",
          (if $bearer then "bearer_token_env_var = \($bearer|q)" else empty end),
          (if ($envh | length) > 0 then "env_http_headers = \(inline($envh))" else empty end),
          (if ($lith | length) > 0 then "http_headers = \(inline($lith))" else empty end)
        ] | join("\n")
    end
  | . + "\n"
' "$src" | envsubst
