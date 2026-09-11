"""Network-free security and concurrency regression tests; no Blender required."""
import asyncio
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
import service


class ArtifactTests(unittest.TestCase):
    def test_only_regular_unlinked_assets_below_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'workspace'
            root.mkdir()
            outside = Path(directory) / 'outside.glb'
            outside.write_bytes(b'secret fixture')
            (root / 'asset.glb').write_bytes(b'glTF fixture')
            (root / 'nested').mkdir()
            (root / 'nested' / 'asset.blend').write_bytes(b'BLENDER fixture')
            (root / 'escape.glb').symlink_to(outside)
            (root / 'escape-dir').symlink_to(outside.parent, target_is_directory=True)
            (root / '.secret.glb').write_bytes(b'secret')
            os.link(outside, root / 'hardlink.glb')
            os.mkfifo(root / 'pipe.glb')
            for path in ('../outside.glb', '/asset.glb', './asset.glb', 'escape.glb',
                         'escape-dir/outside.glb', 'hardlink.glb', 'pipe.glb',
                         '.secret.glb', 'nested/../asset.glb', 'asset.py', 'asset.glb/'):
                with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                    service.open_artifact(root, path)
            for path in ('asset.glb', 'nested/asset.blend'):
                stream, size = service.open_artifact(root, path)
                with stream:
                    self.assertEqual(size, len(stream.read()))
            # Rename/symlink replacement cannot change a file already opened safely.
            stream, _ = service.open_artifact(root, 'asset.glb')
            (root / 'asset.glb').unlink()
            (root / 'asset.glb').symlink_to(outside)
            with stream:
                self.assertEqual(stream.read(), b'glTF fixture')


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_edit_keeps_scene_lock_until_worker_finishes(self):
        runtime = service.Runtime()
        started, release, second = threading.Event(), threading.Event(), threading.Event()
        def first():
            started.set()
            release.wait(3)
        task = asyncio.create_task(runtime.serialized(first))
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        next_task = asyncio.create_task(runtime.serialized(second.set))
        await asyncio.sleep(0.05)
        self.assertFalse(second.is_set())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await next_task
        self.assertTrue(second.is_set())

    async def test_busy_readiness_and_process_failure(self):
        with patch.object(service.runtime, 'alive', return_value=True):
            async with service.runtime.lock:
                response = await service.ready(None)
                self.assertEqual(response.status_code, 200)
        with patch.object(service.runtime, 'alive', return_value=False):
            self.assertEqual((await service.ready(None)).status_code, 503)
            self.assertEqual((await service.health(None)).status_code, 503)

    async def test_http_mcp_hosts_and_artifact_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'test.glb').write_bytes(b'glTF test')
            transport = httpx.ASGITransport(app=service.app)
            async with service.mcp.session_manager.run():
                async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:8000') as client:
                    with patch.object(service.desktop, 'WORKSPACE', root):
                        response = await client.get('/artifacts/test.glb')
                        self.assertEqual(response.content, b'glTF test')
                        self.assertEqual(response.headers['content-disposition'], 'attachment')
                        for path in ('%2e%2e/outside.glb', '%2fetc/passwd', '.secret.glb'):
                            self.assertEqual((await client.get('/artifacts/' + path)).status_code, 404)
                    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                        'protocolVersion': '2025-03-26', 'capabilities': {},
                        'clientInfo': {'name': 'unit', 'version': '1'}}}
                    headers = {'accept': 'application/json, text/event-stream'}
                    response = await client.post('/mcp', json=body, headers={**headers, 'host': 'evil.example:8000'})
                    self.assertEqual(response.status_code, 421)
                    response = await client.post('/mcp', json=body, headers={**headers, 'origin': 'https://evil.example'})
                    self.assertEqual(response.status_code, 403)
                    response = await client.post('/mcp', json=body, headers={**headers, 'host': 'blender-authoring.dev.svc.cluster.local:8000'})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()['result']['serverInfo']['name'], 'Haynes Quest Blender')


if __name__ == '__main__':
    unittest.main()
