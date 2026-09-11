#!/opt/blender-authoring/blender-mcp/.venv/bin/python
"""One private authoring worker: native HTTP MCP, Blender desktop and artifacts."""
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import stat
import tempfile

import desktop
from blender_mcp.server import BlenderConnection
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse, StreamingResponse
import uvicorn

logger = logging.getLogger('blender-authoring')
# Upstream INFO includes full Python command parameters (possibly reference-image
# bytes). Retain connection errors without copying authoring inputs into logs.
logging.getLogger('BlenderMCPServer').setLevel(logging.WARNING)
ARTIFACT_TYPES = {
    '.blend': 'application/octet-stream', '.glb': 'model/gltf-binary',
    '.gltf': 'model/gltf+json', '.bin': 'application/octet-stream',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.webp': 'image/webp', '.wav': 'audio/wav', '.ogg': 'audio/ogg',
    '.mp3': 'audio/mpeg', '.flac': 'audio/flac', '.mp4': 'video/mp4',
    '.json': 'application/json',
}


def open_artifact(root, relative):
    """Resolve via directory descriptors; never follow symlinks, including races."""
    parts = relative.split('/')
    if not parts or any(not p or p.startswith('.') or '\\' in p or '\x00' in p for p in parts):
        raise ValueError('Invalid artifact path')
    if Path(parts[-1]).suffix.lower() not in ARTIFACT_TYPES:
        raise ValueError('Unsupported artifact type')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise ValueError('Artifact must be a regular file with one link')
        return os.fdopen(fd, 'rb'), info.st_size
    finally:
        os.close(directory)


class Runtime:
    def __init__(self):
        self.state = {}
        self.lock = asyncio.Lock()
        self.server = None
        self.failed = False

    def alive(self):
        return all(desktop.alive(self.state.get(name)) for name in ('blender', 'xvfb'))

    async def serialized(self, function):
        # Cancellation must not release the lock while a Blender edit still runs.
        async with self.lock:
            task = asyncio.create_task(asyncio.to_thread(function))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    async def command(self, kind, params=None):
        def invoke():
            if not self.alive():
                raise RuntimeError('Blender desktop is unavailable')
            connection = BlenderConnection(host='127.0.0.1', port=9876)
            try:
                result = connection.send_command(kind, params)
                if isinstance(result, dict) and 'error' in result:
                    raise RuntimeError(result['error'])
                return result
            finally:
                connection.disconnect()
        return await self.serialized(invoke)

    async def monitor(self):
        while True:
            await asyncio.sleep(1)
            if not self.alive():
                self.failed = True
                logger.error('Blender or Xvfb exited; terminating the authoring worker')
                self.server.should_exit = True
                return


runtime = Runtime()
mcp = FastMCP('Haynes Quest Blender', host='0.0.0.0', port=8000,
              stateless_http=True, json_response=True,
              transport_security=TransportSecuritySettings(
                  enable_dns_rebinding_protection=True,
                  allowed_hosts=['127.0.0.1:*', 'localhost:*', 'blender-authoring:*',
                                 'blender-authoring.dev:*', 'blender-authoring.dev.svc:*',
                                 'blender-authoring.dev.svc.cluster.local:*'],
                  allowed_origins=[]),
              instructions='One author/work order at a time; all clients share one scene. '
              'Save .blend checkpoints and exports explicitly under /workspace. '
              'Download saved files with GET /artifacts/<relative-path>. '
              'Restarts discard unsaved edits. No optional external asset APIs are enabled.')


@mcp.tool()
async def get_scene_info(user_prompt: str = '') -> str:
    """Inspect the shared Blender scene."""
    return json.dumps(await runtime.command('get_scene_info'), indent=2)


@mcp.tool()
async def get_object_info(object_name: str, user_prompt: str = '') -> str:
    """Inspect a named object in the shared scene."""
    return json.dumps(await runtime.command('get_object_info', {'name': object_name}), indent=2)


@mcp.tool()
async def execute_blender_code(code: str, user_prompt: str = '') -> str:
    """Run Python in Blender; save deliverables under /workspace before disconnecting."""
    result = await runtime.command('execute_code', {'code': code})
    return f"Code executed successfully: {result.get('result', '')}"


@mcp.tool()
async def get_viewport_screenshot(max_size: int = 1000, user_prompt: str = '') -> Image:
    """Return the rendered viewport as an MCP image, without a shared client filesystem."""
    if not 16 <= max_size <= 2048:
        raise ValueError('max_size must be between 16 and 2048')
    # Unique path permits screenshot reads outside the serialized socket call.
    with tempfile.TemporaryDirectory(prefix='blender-screenshot-') as directory:
        path = Path(directory) / 'viewport.png'
        await runtime.command('get_viewport_screenshot', {
            'max_size': max_size, 'filepath': str(path), 'format': 'png'})
        return Image(data=path.read_bytes(), format='png')


@mcp.custom_route('/healthz', methods=['GET'])
async def health(_request):
    healthy = runtime.alive() and not runtime.failed
    return JSONResponse({'healthy': healthy}, status_code=200 if healthy else 503)


@mcp.custom_route('/readyz', methods=['GET'])
async def ready(_request):
    healthy = runtime.alive() and not runtime.failed
    # A render legitimately blocks Blender's event loop. Check responsiveness only
    # when idle; otherwise keep readiness while the supervised process is alive.
    if healthy and not runtime.lock.locked():
        healthy = await runtime.serialized(lambda: desktop.ready(runtime.state, timeout=2))
    return JSONResponse({'ready': healthy, 'busy': runtime.lock.locked()},
                        status_code=200 if healthy else 503)


@mcp.custom_route('/artifacts/{relative:path}', methods=['GET'])
async def artifact(request):
    relative = request.path_params['relative']
    try:
        stream, size = open_artifact(desktop.WORKSPACE, relative)
    except (OSError, ValueError):
        return JSONResponse({'error': 'Artifact not found'}, status_code=404)

    async def chunks():
        try:
            while chunk := await asyncio.to_thread(stream.read, 1024 * 1024):
                yield chunk
        finally:
            stream.close()
    return StreamingResponse(chunks(), media_type=ARTIFACT_TYPES[Path(relative).suffix.lower()],
                             headers={'Content-Length': str(size), 'Cache-Control': 'no-store',
                                      'Content-Disposition': 'attachment',
                                      'X-Content-Type-Options': 'nosniff'})


app = mcp.streamable_http_app()
http_lifespan = app.router.lifespan_context


@asynccontextmanager
async def lifespan(application):
    os.umask(0o077)
    try:
        with desktop.locked():
            runtime.state = await asyncio.to_thread(desktop.start, desktop.environment())
        watcher = asyncio.create_task(runtime.monitor())
        try:
            async with http_lifespan(application):
                yield
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
    finally:
        # SIGTERM is a shutdown, not a save operation. Saved workspace files persist.
        for name in ('blender', 'xvfb'):
            await asyncio.to_thread(desktop.stop_process, runtime.state.get(name))
        desktop.STATE.unlink(missing_ok=True)


app.router.lifespan_context = lifespan


def main():
    runtime.server = uvicorn.Server(uvicorn.Config(app, host='0.0.0.0', port=8000,
                                                  timeout_graceful_shutdown=15))
    runtime.server.run()
    raise SystemExit(1 if runtime.failed or not runtime.server.started else 0)


if __name__ == '__main__':
    main()
