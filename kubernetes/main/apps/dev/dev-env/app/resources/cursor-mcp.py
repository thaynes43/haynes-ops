#!/usr/bin/env python3
"""Local stdio adapter using the official MCP SDK's Streamable HTTP transport.

Preserves JSON-RPC IDs/messages and forwards notifications and server requests.
No listener, OAuth flow, dashboard secret or credential in command arguments.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys

SOURCE = Path('/opt/dev-env/config/claude/mcp.json')
APPROVAL = Path.home() / '.config/cursor/dev-env-mcp-approved.sha256'


def expand(value, environment):
    if isinstance(value, str):
        def replace(match):
            if not environment.get(match[1]):
                raise ValueError('required environment variable is absent')
            return environment[match[1]]
        return re.sub(r'\$\{([A-Z][A-Z0-9_]*)\}', replace, value)
    if isinstance(value, dict):
        return {key: expand(item, environment) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item, environment) for item in value]
    return value


def approved_spec(name, source=SOURCE, approval=APPROVAL):
    data = source.read_bytes()
    if not approval.is_file() or approval.read_text().strip() != hashlib.sha256(data).hexdigest():
        raise ValueError('MCP activation is pending for this source revision')
    return json.loads(data)['mcpServers'][name]


async def relay(url, headers):
    import anyio
    import httpx2
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server.stdio import stdio_server
    from mcp.shared.message import ClientMessageMetadata, SessionMessage

    # SDK follows only same-origin redirects. No env proxy or ambient netrc auth.
    async with httpx2.AsyncClient(headers=headers, trust_env=False,
                                 timeout=httpx2.Timeout(30, read=300)) as client:
        async with streamable_http_client(url, http_client=client) as (remote_read, remote_write):
            async with stdio_server() as (local_read, local_write):
                state = {'initialize_id': None, 'version': None}
                async with local_write, anyio.create_task_group() as group:
                    async def to_remote():
                        async for item in local_read:
                            if isinstance(item, Exception):
                                raise ValueError('invalid input frame')
                            frame = item.message.model_dump(by_alias=True, exclude_none=True)
                            if frame.get('method') == 'initialize':
                                state['initialize_id'] = frame.get('id')
                                state['version'] = None
                            extra = {'MCP-Protocol-Version': state['version']} if state['version'] else {}
                            await remote_write.send(SessionMessage(item.message, ClientMessageMetadata(headers=extra)))
                        group.cancel_scope.cancel()

                    async def to_local():
                        async for item in remote_read:
                            if isinstance(item, Exception):
                                raise ValueError('remote transport failed')
                            frame = item.message.model_dump(by_alias=True, exclude_none=True)
                            if frame.get('id') == state['initialize_id'] and 'result' in frame:
                                state['version'] = frame['result'].get('protocolVersion', state['version'])
                            await local_write.send(SessionMessage(item.message))
                        group.cancel_scope.cancel()

                    group.start_soon(to_remote)
                    group.start_soon(to_local)


def main():
    # Transport errors can contain a secret URL path. Never log exception text,
    # HTTP details, headers, tracebacks or the expanded configuration.
    logging.disable(logging.CRITICAL)
    if len(sys.argv) != 2:
        raise ValueError('one registered server name is required')
    spec = expand(approved_spec(sys.argv[1]), os.environ)
    if 'command' in spec:
        os.execvpe(spec['command'], [spec['command'], *spec.get('args', [])],
                   {**os.environ, **spec.get('env', {})})
    if spec.get('type') != 'http':
        raise ValueError('unsupported source transport')
    import anyio
    anyio.run(relay, spec['url'], spec.get('headers', {}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'cursor-mcp: unavailable ({type(error).__name__}); inspect registration and approval locally', file=sys.stderr)
        sys.exit(1)
