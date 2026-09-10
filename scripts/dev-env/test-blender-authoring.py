#!/opt/dev-env/blender-mcp/.venv/bin/python
"""Offline image smoke test; creates disposable geometry/audio, never game assets.

Run with the baked MCP venv Python. Requires no running authoring instance.
Artifacts remain in ~/.local/share/blender-authoring/candidates/smoke-*.
"""
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

LAUNCHER = '/usr/local/bin/blender-authoring'
RUNTIME = Path(f'/tmp/blender-authoring-{os.getuid()}')
WORKSPACE = Path.home() / '.local/share/blender-authoring'
PROMPT = 'Verify the local authoring tools using disposable geometry and an audio fixture.'


def run(*command, timeout=20, check=True):
    return subprocess.run(command, check=check, timeout=timeout,
                          text=True, capture_output=True)


def status():
    result = run(LAUNCHER, 'status', check=False)
    assert result.returncode in (0, 1), result.stderr
    return json.loads(result.stdout)


@asynccontextmanager
async def connected(log):
    params = StdioServerParameters(command=LAUNCHER, args=['mcp'], env=dict(os.environ))
    async with stdio_client(params, errlog=log) as streams:
        async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=55)) as session:
            result = await session.initialize()
            assert result.serverInfo.name, result
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {'get_scene_info', 'execute_blender_code', 'get_viewport_screenshot'} <= names, names
            yield session


async def code(session, source, marker):
    result = await session.call_tool('execute_blender_code', {
        'code': source + '\nprint(' + repr(marker) + ')', 'user_prompt': PROMPT,
    })
    text = '\n'.join(block.text for block in result.content if block.type == 'text')
    assert not result.isError and 'Code executed successfully:' in text and marker in text, text
    return text


async def exercise(artifacts, log):
    blend = artifacts / 'fixture.blend'
    glb = artifacts / 'fixture.glb'
    screenshot = artifacts / 'viewport.png'
    async with connected(log) as session:
        initial = status()
        assert initial['ready'], initial
        # Repeated starts must retain exactly the same process and scene.
        starts = [subprocess.Popen([LAUNCHER, 'start'], stdout=log, stderr=log) for _ in range(2)]
        for process in starts:
            assert process.wait(timeout=55) == 0
        assert status()['blender'] == initial['blender']
        await code(session, """
import bpy
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=2)
obj = bpy.context.active_object
obj.name = 'AuthoringSmokeCube'
obj.data.name = 'AuthoringSmokeMesh'
material = bpy.data.materials.new('AuthoringSmokeMaterial')
material.use_nodes = True
material.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (0.1, 0.4, 0.8, 1)
obj.data.materials.append(material)
assert obj.type == 'MESH' and len(obj.data.vertices) == 8
assert obj.active_material.name == 'AuthoringSmokeMaterial'
assert bpy.context.preferences.addons['blender_authoring_addon'].preferences.telemetry_consent is False
assert not bpy.context.preferences.system.use_online_access
for service in ('polyhaven', 'hyper3d', 'sketchfab', 'polypizza', 'hunyuan3d'):
    assert not getattr(bpy.context.scene, 'blendermcp_use_' + service)
""", 'CREATED_FIXTURE')
        info = await session.call_tool('get_scene_info', {'user_prompt': PROMPT})
        assert not info.isError and 'AuthoringSmokeCube' in str(info.content), info
        result = await session.call_tool('get_viewport_screenshot', {'max_size': 640, 'user_prompt': PROMPT})
        images = [block for block in result.content if block.type == 'image']
        assert not result.isError and images, result
        screenshot.write_bytes(base64.b64decode(images[0].data, validate=True))
        png = screenshot.read_bytes()
        assert png[:8] == b'\x89PNG\r\n\x1a\n' and len(png) > 1024
        width, height = struct.unpack('>II', png[16:24])
        assert width > 10 and height > 10 and max(width, height) <= 640, (width, height)
        decoded = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(screenshot),
                                  '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                                 check=True, capture_output=True, timeout=10).stdout
        assert len(decoded) == width * height * 3
        assert len({decoded[i:i + 3] for i in range(0, len(decoded), 3)}) > 16, 'Blank viewport screenshot'
        await code(session, f"""
import bpy
bpy.ops.wm.save_as_mainfile(filepath={str(blend)!r})
bpy.ops.export_scene.gltf(filepath={str(glb)!r}, export_format='GLB', use_selection=True)
""", 'SAVED_EXPORTED_FIXTURE')
        assert blend.stat().st_size > 1024 and glb.stat().st_size > 100
        await code(session, f"""
import bpy
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath={str(glb)!r})
meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
assert len(meshes) == 1, [obj.name for obj in meshes]
assert meshes[0].name == 'AuthoringSmokeCube'
assert len(meshes[0].data.vertices) >= 8
assert meshes[0].active_material.name.startswith('AuthoringSmokeMaterial')
""", 'GLB_ROUNDTRIP_OK')
        await code(session, f"import bpy\nbpy.ops.wm.open_mainfile(filepath={str(blend)!r})", 'REOPENED_BLEND')
        await code(session, """
import bpy
obj = bpy.data.objects['AuthoringSmokeCube']
assert obj.type == 'MESH' and len(obj.data.vertices) == 8
assert obj.active_material.name == 'AuthoringSmokeMaterial'
assert bpy.context.scene.blendermcp_server_running
assert not bpy.context.scene.blendermcp_use_polyhaven
""", 'BLEND_ROUNDTRIP_OK')
    assert status()['ready'], 'MCP disconnect stopped the shared Blender scene'
    run(LAUNCHER, 'stop')
    assert not status()['ready']
    async with connected(log) as session:
        restarted = status()
        assert restarted['ready'] and restarted['blender'] != initial['blender']
        await code(session, f"import bpy\nbpy.ops.wm.open_mainfile(filepath={str(blend)!r})", 'RESTART_REOPENED')
        await code(session, "import bpy\nassert bpy.data.objects['AuthoringSmokeCube'].type == 'MESH'", 'RECONNECTED_OK')


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


def main():
    # Explicitly refuse to disturb a live/shared work session, including unhealthy ones.
    if (RUNTIME / 'state.json').exists():
        raise RuntimeError('Authoring state exists; stop the shared session explicitly before smoke testing')
    (WORKSPACE / 'candidates').mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(prefix='smoke-', dir=WORKSPACE / 'candidates'))
    try:
        with (artifacts / 'mcp.log').open('w') as log:
            async def bounded():
                async with asyncio.timeout(150):
                    await exercise(artifacts, log)
            asyncio.run(bounded())
        validate_exports(artifacts)
        print(f'PASS: MCP, screenshot, .blend/GLB roundtrip, Khronos validation, restart, audio. Artifacts: {artifacts}')
    except BaseException:
        for path in (RUNTIME / 'xvfb.log', RUNTIME / 'blender.log', artifacts / 'mcp.log'):
            if path.exists():
                print(f'--- {path} (last 12 KiB) ---\n{path.read_text(errors="replace")[-12288:]}', file=sys.stderr)
        raise
    finally:
        result = run(LAUNCHER, 'stop', check=False)
        if result.returncode:
            print(result.stderr, file=sys.stderr)


if __name__ == '__main__':
    main()
