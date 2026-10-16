#!/usr/bin/env python3
"""Offline fixtures only: no production MCP, account login, model call or cluster."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / 'kubernetes/main/apps/dev/dev-env/app/resources'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


config = module('cursor_config', SCRIPTS / 'cursor-config.py')
adapter = module('cursor_mcp', SCRIPTS / 'cursor-mcp.py')


class LifecycleTests(unittest.TestCase):
    def prepare(self, root):
        home, commands = root / 'home with spaces', root / 'bin'
        home.mkdir(); commands.mkdir()
        for name, body in {
            'uname': 'case "$1" in -s) echo Linux;; -m) echo x86_64;; esac',
            'flock': 'exit 0',  # Process locking is exercised with real flock on Linux below.
            'curl': 'echo curl >> "$HOME/calls"; exit 22',
            'tar': 'echo unexpected-tar >> "$HOME/calls"; exit 1',
        }.items():
            path = commands / name
            path.write_text('#!/bin/sh\n' + body + '\n'); path.chmod(0o755)
        return home, commands, {**os.environ, 'HOME': str(home), 'PATH': str(commands) + ':' + os.environ['PATH']}

    def test_failed_download_keeps_previous_install_and_does_not_extract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); home, _, env = self.prepare(root)
            bins = home / '.local/bin'; bins.mkdir(parents=True)
            (bins / 'agent').symlink_to(home / '.local/share/cursor-agent/versions/previous/cursor-agent')
            result = subprocess.run(['bash', str(SCRIPTS / 'cursor-worker.sh'), 'install'], env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((home / 'calls').read_text(), 'curl\n')
            self.assertIn('/previous/', os.readlink(bins / 'agent'))
            self.assertEqual(list((home / '.local/share/cursor-agent/versions').glob('.install.*')), [])

    def test_existing_pin_is_idempotent_and_never_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            home, _, env = self.prepare(Path(directory))
            binary = home / '.local/share/cursor-agent/versions/2026.10.14-0f9edde/cursor-agent'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\necho 2026.10.14-0f9edde\n'); binary.chmod(0o755)
            for _ in range(2):
                subprocess.run(['bash', str(SCRIPTS / 'cursor-worker.sh'), 'install'], env=env, check=True, capture_output=True)
            self.assertEqual((home / '.local/bin/agent').resolve(), binary.resolve())
            self.assertFalse((home / 'calls').exists())

    def test_up_preserves_existing_pilot_session(self):
        with tempfile.TemporaryDirectory() as directory:
            home, commands, env = self.prepare(Path(directory))
            tmux = commands / 'tmux'
            tmux.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOME/calls"\n[ "$1" = has-session ]\n')
            tmux.chmod(0o755)
            subprocess.run(['bash', str(SCRIPTS / 'cursor-worker.sh'), 'up'], env=env, check=True, capture_output=True)
            self.assertEqual((home / 'calls').read_text(), 'has-session -t =cursor-worker-v1\n')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'real Linux flock fixture runs in CI')
    def test_install_refuses_held_supervisor_lock(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            home, commands, env = self.prepare(Path(directory))
            (commands / 'flock').unlink()
            state = home / '.local/state/dev-env-cursor'; state.mkdir(parents=True)
            with (state / 'worker.lock').open('w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = subprocess.run(['bash', str(SCRIPTS / 'cursor-worker.sh'), 'install'], env=env, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((home / 'calls').exists())


class SupervisorTests(unittest.TestCase):
    """`run` with a fake pinned CLI: no download, login, network or real worker."""

    def prepare(self, root, worker_body, logged_in=True):
        home, env = LifecycleTests.prepare(self, root)[0::2]
        binary = home / '.local/share/cursor-agent/versions/2026.10.14-0f9edde/cursor-agent'
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\n'
                          'case "$1" in\n'
                          '  --version) echo 2026.10.14-0f9edde;;\n'
                          '  status) if [ -f "$HOME/auth-ok" ]; then echo \'{"isAuthenticated": true}\'; else exit 1; fi;;\n'
                          '  worker) echo worker-start >> "$HOME/calls"\n' + worker_body + '\n;;\n'
                          'esac\n')
        binary.chmod(0o755)
        workspace = home / 'cursor/haynes-ops'
        workspace.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
        if logged_in:
            (home / '.config/cursor').mkdir(parents=True)
            (home / '.config/cursor/auth.json').write_text('synthetic-fixture')
            (home / 'auth-ok').write_text('')
        env.update({'CURSOR_WORKER_BACKOFF_SECONDS': '0', 'DEV_ENV_CLAUDE_CONFIG': str(SCRIPTS / 'config/claude')})
        return home, env

    def run_supervisor(self, env, timeout=30):
        return subprocess.run(['bash', str(SCRIPTS / 'cursor-worker.sh'), 'run'], env=env,
                              capture_output=True, text=True, timeout=timeout)

    def starts(self, home):
        calls = home / 'calls'
        return calls.read_text().splitlines().count('worker-start') if calls.exists() else 0

    def test_short_exits_and_failed_auth_checks_trip_breaker_after_five(self):
        with tempfile.TemporaryDirectory() as directory:
            # The first exit also makes the status check fail: a transient auth error
            # must consume the retry budget, not end recovery permanently.
            home, env = self.prepare(Path(directory), 'rm -f "$HOME/auth-ok"; exit 3')
            result = self.run_supervisor(env)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(self.starts(home), 5)
            self.assertIn('authentication check failed', result.stdout)
            self.assertIn('circuit breaker open', result.stdout)
            self.assertFalse((home / 'calls').read_text().count('curl'))

    def test_disabled_marker_stops_loop_without_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            home, env = self.prepare(Path(directory),
                                     'touch "$HOME/.local/state/dev-env-cursor/disabled"; exit 0')
            result = self.run_supervisor(env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.starts(home), 1)
            self.assertIn('disabled by persistent operator marker', result.stdout)
            # Already disabled: a later boot does not start a worker at all.
            self.assertEqual(self.run_supervisor(env).returncode, 0)
            self.assertEqual(self.starts(home), 1)

    def test_log_is_copy_truncated_before_each_start(self):
        with tempfile.TemporaryDirectory() as directory:
            home, env = self.prepare(Path(directory),
                                     'touch "$HOME/.local/state/dev-env-cursor/disabled"; exit 0')
            env['CURSOR_WORKER_LOG_MAX_BYTES'] = '50'
            state = home / '.local/state/dev-env-cursor'; state.mkdir(parents=True)
            (state / 'worker.log').write_text('x' * 100)
            self.assertEqual(self.run_supervisor(env).returncode, 0)
            self.assertEqual((state / 'worker.log.1').read_text(), 'x' * 100)
            self.assertEqual((state / 'worker.log').read_text(), '')

    def test_failed_status_at_boot_still_starts_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            home, env = self.prepare(Path(directory),
                                     'touch "$HOME/.local/state/dev-env-cursor/disabled"; exit 0')
            (home / 'auth-ok').unlink()
            result = self.run_supervisor(env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.starts(home), 1)
            self.assertIn('authentication check failed; starting', result.stdout)

    def test_logged_out_never_starts_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            home, env = self.prepare(Path(directory), 'exit 0', logged_in=False)
            result = self.run_supervisor(env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.starts(home), 0)
            self.assertIn('browser login required', result.stdout)

    def test_dev_env_profile_preserves_separate_identity_and_root(self):
        with tempfile.TemporaryDirectory() as directory:
            home, env = self.prepare(Path(directory),
                                     'printf "%s\\n" "$@" > "$HOME/worker-args"; '
                                     'touch "$HOME/.local/state/cursor-worker-dev-env/disabled"; exit 0')
            workspace = home / 'repos/dev-env'
            workspace.mkdir(parents=True)
            subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
            result = subprocess.run(['bash', str(SCRIPTS / 'cursor-worker.sh'), 'run', 'dev-env'],
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((home / 'worker-args').read_text().splitlines(),
                             ['worker', '--name', 'dev-env', '--worker-dir', str(workspace),
                              '--data-dir', str(home / '.local/share/cursor-agent-dev-env'),
                              '--idle-release-timeout', '0', 'start'])
            self.assertFalse((home / '.local/state/dev-env-cursor/disabled').exists())
            self.assertTrue((workspace / '.cursor/dev-env-parity.json').exists())

    def test_missing_dev_env_checkout_never_starts_or_clones(self):
        with tempfile.TemporaryDirectory() as directory:
            home, env = self.prepare(Path(directory), 'exit 0')
            result = subprocess.run(['bash', str(SCRIPTS / 'cursor-worker.sh'), 'run', 'dev-env'],
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.starts(home), 0)
            self.assertIn('dev-env checkout missing; no automatic clone', result.stdout)
            self.assertFalse((home / 'repos/dev-env').exists())

    def test_logout_during_recovery_stops_without_breaker(self):
        with tempfile.TemporaryDirectory() as directory:
            home, env = self.prepare(Path(directory), 'rm -f "$HOME/.config/cursor/auth.json"; exit 1')
            result = self.run_supervisor(env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.starts(home), 1)
            self.assertIn('human browser login required', result.stdout)

    def test_term_stops_worker_promptly(self):
        with tempfile.TemporaryDirectory() as directory:
            home, env = self.prepare(Path(directory), 'echo $$ > "$HOME/worker.pid"; exec sleep 60')
            process = subprocess.Popen(['bash', str(SCRIPTS / 'cursor-worker.sh'), 'run'], env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                pid_file = home / 'worker.pid'
                for _ in range(200):
                    if pid_file.exists() and pid_file.read_text().strip():
                        break
                    threading.Event().wait(0.05)
                self.assertTrue(pid_file.exists(), 'fake worker never started')
                process.terminate()
                self.assertEqual(process.wait(timeout=5), 0)
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(pid_file.read_text()), 0)
            finally:
                if process.poll() is None: process.kill(); process.wait()
                process.stdout.close(); process.stderr.close()


class ConfigurationTests(unittest.TestCase):
    def test_render_has_exact_names_no_values_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            subprocess.run(['git', 'init', '-q', directory], check=True)
            (workspace / 'AGENTS.md').write_text('existing repository instructions\n')
            source = SCRIPTS / 'config/claude'
            first = config.render(workspace, source)
            paths = [p for p in (workspace / '.cursor').rglob('*') if p.is_file()]
            contents = {p: p.read_bytes() for p in paths}
            exclude = (workspace / '.git/info/exclude').read_bytes()
            self.assertEqual(first, config.render(workspace, source))
            self.assertEqual(contents, {p: p.read_bytes() for p in paths})
            self.assertEqual(exclude, (workspace / '.git/info/exclude').read_bytes())
            self.assertEqual((workspace / 'AGENTS.md').read_text(), 'existing repository instructions\n')
            pending = json.loads((workspace / '.cursor/dev-env-mcp.pending.json').read_text())
            original = json.loads((source / 'mcp.json').read_text())['mcpServers']
            self.assertEqual(set(pending['mcpServers']), set(original))
            self.assertEqual(len(original), 10)
            self.assertTrue(all('command' in row and 'url' not in row for row in pending['mcpServers'].values()))
            self.assertFalse((workspace / '.cursor/mcp.json').exists())
            common = (source / 'CLAUDE.md').read_text()
            self.assertTrue((workspace / '.cursor/rules/dev-env-shared.mdc').read_text().endswith(common))

    def test_unmanaged_rule_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rules = root / '.cursor/rules/dev-env-shared.mdc'
            rules.parent.mkdir(parents=True)
            rules.write_text('user rules')
            with self.assertRaises(ValueError):
                config.render(root, SCRIPTS / 'config/claude')
            self.assertEqual(rules.read_text(), 'user rules')

    def test_approval_binds_exact_source_before_secret_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            source, approval = Path(directory) / 'mcp.json', Path(directory) / 'approval'
            source.write_text('{"mcpServers":{"fixture":{"url":"http://localhost/${FIXTURE_TOKEN}"}}}')
            with self.assertRaises(ValueError):
                adapter.approved_spec('fixture', source, approval)
            approval.write_text(hashlib.sha256(source.read_bytes()).hexdigest())
            spec = adapter.approved_spec('fixture', source, approval)
            self.assertIn('${FIXTURE_TOKEN}', spec['url'])
            self.assertEqual(adapter.expand(spec, {'FIXTURE_TOKEN': 'fake'}), {'url': 'http://localhost/fake'})
            with self.assertRaises(ValueError):
                adapter.expand(spec, {})
            source.write_text(source.read_text() + '\n')
            with self.assertRaises(ValueError):
                adapter.approved_spec('fixture', source, approval)


class TransportTests(unittest.TestCase):
    def test_stdio_http_session_schema_pagination_errors_and_eof(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_):
                pass
            def do_GET(self):
                self.send_response(405); self.send_header('Content-Length', '0'); self.end_headers()
            def do_DELETE(self):
                seen.append(('DELETE', dict(self.headers), None))
                self.send_response(200); self.send_header('Content-Length', '0'); self.end_headers()
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append(('POST', dict(self.headers), request))
                if 'id' not in request:
                    self.send_response(202); self.send_header('Content-Length', '0'); self.end_headers(); return
                method = request['method']
                if method == 'initialize':
                    result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}},
                              'serverInfo': {'name': 'fixture', 'version': '1'}, 'instructions': 'fixture instruction'}
                elif method == 'tools/list':
                    result = {'tools': [{'name': 'fixture_read', 'inputSchema': {'type': 'object', 'properties': {'x': {'type': 'integer'}}, 'required': ['x']}}]}
                    if not request.get('params', {}).get('cursor'):
                        result['nextCursor'] = 'page-2'
                else:
                    result = None
                response = {'jsonrpc': '2.0', 'id': request['id']}
                response.update({'result': result} if result is not None else {'error': {'code': -32601, 'message': 'fixture unknown method'}})
                body = json.dumps(response).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                if method == 'initialize': self.send_header('Mcp-Session-Id', 'fixture-session')
                self.end_headers(); self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        code = ('import runpy,anyio,logging; logging.disable(logging.CRITICAL); '
                f'm=runpy.run_path({str(SCRIPTS / "cursor-mcp.py")!r}); '
                f'anyio.run(m["relay"], "http://127.0.0.1:{server.server_port}/mcp", {{"Authorization":"Bearer synthetic-only"}})')
        process = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def send(frame):
            process.stdin.write(json.dumps(frame).encode() + b'\n'); process.stdin.flush()
        def receive():
            ready, _, _ = select.select([process.stdout], [], [], 10)
            self.assertTrue(ready, 'adapter did not respond in 10 seconds')
            return json.loads(process.stdout.readline())
        try:
            send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'fixture', 'version': '1'}}})
            self.assertEqual(receive()['result']['instructions'], 'fixture instruction')
            send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            send({'jsonrpc': '2.0', 'id': 'catalog', 'method': 'tools/list', 'params': {}})
            first = receive()
            self.assertEqual(first['id'], 'catalog')
            self.assertEqual(first['result']['nextCursor'], 'page-2')
            send({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list', 'params': {'cursor': 'page-2'}})
            self.assertEqual(receive()['result']['tools'], first['result']['tools'])
            send({'jsonrpc': '2.0', 'id': 4, 'method': 'fixture/unknown', 'params': {}})
            self.assertEqual(receive()['error']['code'], -32601)
            process.stdin.close()
            process.wait(timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr.read().decode())
            self.assertTrue(any(row[0] == 'DELETE' for row in seen))
            catalog = [row for row in seen if row[2] and row[2].get('method') == 'tools/list']
            self.assertEqual(catalog[1][2]['params'], {'cursor': 'page-2'})
            headers = {k.lower(): v for k, v in catalog[0][1].items()}
            self.assertEqual(headers['authorization'], 'Bearer synthetic-only')
            self.assertEqual(headers['mcp-session-id'], 'fixture-session')
            self.assertEqual(headers['mcp-protocol-version'], '2025-03-26')
        finally:
            if process.poll() is None: process.kill(); process.wait()
            process.stdout.close(); process.stderr.close()
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
