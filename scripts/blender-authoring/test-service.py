"""Offline real-image smoke: HTTP MCP, artifacts, restart and supervised failure.

Run only in a disposable image container (it replaces the shared scene).
No external network or shared client PVC is used.
"""
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

INSTALL = Path('/opt/blender-authoring')
BASE = 'http://127.0.0.1:8000'
PROMPT = 'Verify standalone Blender authoring with disposable geometry and audio.'


def run(*command, timeout=20, check=True):
    return subprocess.run(command, check=check, timeout=timeout, text=True, capture_output=True)


def start(log):
    process = subprocess.Popen([sys.executable, str(INSTALL / 'service.py')],
                               stdout=log, stderr=log)
    deadline = time.monotonic() + 70
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Service exited during startup: {process.returncode}')
        try:
            if httpx.get(BASE + '/readyz', timeout=3).status_code == 200:
                return process
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    process.terminate()
    process.wait(timeout=20)
    raise RuntimeError('Service startup timed out')


@asynccontextmanager
async def connected():
    async with streamablehttp_client(BASE + '/mcp') as (read, write, _):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=70)) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert names == {'get_scene_info', 'get_object_info', 'execute_blender_code', 'get_viewport_screenshot'}, names
            yield session


async def code(session, source, marker):
    result = await session.call_tool('execute_blender_code', {
        'code': source + '\nprint(' + repr(marker) + ')', 'user_prompt': PROMPT})
    text = '\n'.join(block.text for block in result.content if block.type == 'text')
    assert not result.isError and 'Code executed successfully:' in text and marker in text, text
    return text


async def exercise(downloads):
    async with connected() as session, httpx.AsyncClient(base_url=BASE) as client:
        await code(session, """
import bpy, os
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=2)
obj = bpy.context.active_object
obj.name = 'AuthoringSmokeCube'
material = bpy.data.materials.new('AuthoringSmokeMaterial')
material.use_nodes = True
material.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (0.1, 0.4, 0.8, 1)
obj.data.materials.append(material)
assert bpy.context.preferences.addons['addon'].preferences.telemetry_consent is False
assert not bpy.context.preferences.system.use_online_access
for service in ('polyhaven', 'hyper3d', 'sketchfab', 'polypizza', 'hunyuan3d'):
    assert not getattr(bpy.context.scene, 'blendermcp_use_' + service)
bpy.ops.wm.save_as_mainfile(filepath='/workspace/fixture.blend')
bpy.ops.export_scene.gltf(filepath='/workspace/fixture.glb', export_format='GLB', use_selection=True)
os.symlink('/etc/passwd', '/workspace/escape.glb')
os.symlink('/tmp', '/workspace/escape-dir')
""", 'FIXTURE_SAVED')
        for filename in ('fixture.blend', 'fixture.glb'):
            response = await client.get('/artifacts/' + filename)
            assert response.status_code == 200 and len(response.content) > 100, response
            (downloads / filename).write_bytes(response.content)
        for path in ('escape.glb', 'escape-dir/file.glb', '%2e%2e/etc/passwd', '%2fetc/passwd', '.secret.glb'):
            assert (await client.get('/artifacts/' + path)).status_code == 404, path
        result = await session.call_tool('get_viewport_screenshot', {'max_size': 640, 'user_prompt': PROMPT})
        images = [block for block in result.content if block.type == 'image']
        assert not result.isError and images, result
        png = base64.b64decode(images[0].data, validate=True)
        assert png[:8] == b'\x89PNG\r\n\x1a\n' and len(png) > 1024
        width, height = struct.unpack('>II', png[16:24])
        assert 10 < max(width, height) <= 640
        screenshot = downloads / 'viewport.png'
        screenshot.write_bytes(png)
        decoded = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(screenshot),
                                  '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                                 check=True, capture_output=True, timeout=10).stdout
        assert len(decoded) == width * height * 3
        assert len({decoded[i:i+3] for i in range(0, len(decoded), 3)}) > 16
        await code(session, """
import bpy
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath='/workspace/fixture.glb')
obj = bpy.data.objects['AuthoringSmokeCube']
assert len(obj.data.vertices) >= 8
assert obj.active_material.name.startswith('AuthoringSmokeMaterial')
""", 'GLB_REIMPORTED')
        await code(session, "import bpy; bpy.ops.wm.open_mainfile(filepath='/workspace/fixture.blend')", 'BLEND_REOPENED')
        await code(session, "import bpy; assert bpy.data.objects['AuthoringSmokeCube'].type == 'MESH'", 'REOPEN_VERIFIED')
        edit = asyncio.create_task(code(session, 'import time; time.sleep(3)', 'BUSY_EDIT_DONE'))
        await asyncio.sleep(0.5)
        for endpoint in ('/healthz', '/readyz'):
            response = await client.get(endpoint, timeout=1)
            assert response.status_code == 200, response.text
        await edit
    assert httpx.get(BASE + '/readyz').status_code == 200, 'Client disconnect ended desktop'


def validate_exports(artifacts):
    # Resolve the actual globally installed package, with no npx/network fallback.
    npm_root = run('npm', 'root', '-g').stdout.strip()
    validation = run('node', '-e', r"""
const fs = require('fs');
const validator = require(process.argv[1] + '/gltf-validator');
validator.validateBytes(new Uint8Array(fs.readFileSync(process.argv[2])),
    {uri: 'fixture.glb'}).then(report => {
    console.log(JSON.stringify(report));
    if (report.issues.numErrors !== 0) process.exitCode = 1;
}).catch(error => { console.error(error); process.exitCode = 1; });
""", npm_root, str(artifacts / 'fixture.glb'))
    report = json.loads(validation.stdout)
    assert report['issues']['numErrors'] == 0, report
    (artifacts / 'gltf-validation.json').write_text(json.dumps(report, indent=2) + '\n')
    inspection = run('gltf-transform', 'inspect', str(artifacts / 'fixture.glb'))
    (artifacts / 'gltf-inspect.txt').write_text(inspection.stdout)
    # Check exported mesh/material names independently of Blender's importer.
    data = (artifacts / 'fixture.glb').read_bytes()
    magic, version, size = struct.unpack('<4sII', data[:12])
    assert (magic, version, size) == (b'glTF', 2, len(data))
    length, kind = struct.unpack('<I4s', data[12:20])
    assert kind == b'JSON'
    document = json.loads(data[20:20 + length])
    assert len(document['meshes']) == 1
    assert any(node.get('name') == 'AuthoringSmokeCube' for node in document['nodes'])
    assert any(material.get('name') == 'AuthoringSmokeMaterial' for material in document['materials'])
    cue = artifacts / 'sine-fixture.wav'
    run('ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'sine=frequency=440:duration=0.25:sample_rate=48000', '-c:a', 'pcm_s16le', str(cue))
    metadata = json.loads(run('ffprobe', '-v', 'error', '-show_streams', '-show_format',
                             '-of', 'json', str(cue)).stdout)
    stream = metadata['streams'][0]
    assert stream['codec_name'] == 'pcm_s16le' and stream['sample_rate'] == '48000'
    assert stream['channels'] == 1 and abs(float(metadata['format']['duration']) - 0.25) < 0.01
    assert cue.stat().st_size > 1024
    (artifacts / 'audio-probe.json').write_text(json.dumps(metadata, indent=2) + '\n')


async def verify_restart():
    async with connected() as session:
        await code(session, "import bpy; bpy.ops.wm.open_mainfile(filepath='/workspace/fixture.blend')", 'RESTART_REOPENED')
        await code(session, "import bpy; assert bpy.data.objects['AuthoringSmokeCube'].type == 'MESH'", 'RESTART_VERIFIED')


def main():
    state = Path(f'/tmp/blender-authoring-{os.getuid()}/state.json')
    if state.exists():
        raise RuntimeError('Refusing smoke test over an existing authoring session')
    for name in ('fixture.blend', 'fixture.glb', 'escape.glb', 'escape-dir'):
        path = Path('/workspace') / name
        if path.exists() or path.is_symlink():
            raise RuntimeError(f'Refusing to overwrite existing smoke path: {path}')
    subprocess.run([sys.executable, str(INSTALL / 'test-unit.py')], check=True)
    with tempfile.TemporaryDirectory(prefix='authoring-client-') as directory:
        downloads = Path(directory)
        log_path = downloads / 'service.log'
        process = None
        try:
            with log_path.open('w') as log:
                process = start(log)
                asyncio.run(exercise(downloads))
                validate_exports(downloads)
                process.terminate()
                assert process.wait(timeout=25) == 0
                assert not state.exists()
                process = start(log)
                asyncio.run(verify_restart())
                desktop = json.loads(state.read_text())
                os.kill(desktop['blender']['pid'], signal.SIGKILL)
                assert process.wait(timeout=25) != 0, 'Blender death did not fail the container'
                assert not state.exists()
            print('PASS: HTTP MCP, screenshot pixels, secure artifact downloads, GLB validation/reimport, '
                  '.blend restart/reopen, busy health, supervised Blender failure, FFmpeg audio')
        except BaseException:
            print(log_path.read_text(errors='replace')[-20000:], file=sys.stderr)
            raise
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=25)


if __name__ == '__main__':
    main()
