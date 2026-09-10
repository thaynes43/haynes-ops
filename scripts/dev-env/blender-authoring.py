#!/usr/bin/env python3
"""Lazy, shared Blender desktop for the pod's local stdio MCP clients."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time

INSTALL = Path('/opt/dev-env')
MCP = INSTALL / 'blender-mcp'
RUNTIME = Path(f'/tmp/blender-authoring-{os.getuid()}')
WORKSPACE = Path.home() / '.local/share/blender-authoring'
STATE = RUNTIME / 'state.json'
START_TIMEOUT = 45


def environment():
    env = os.environ.copy()
    for name in ('config', 'cache', 'data', 'candidates', 'blender-config'):
        (WORKSPACE / name).mkdir(parents=True, exist_ok=True)
    # Upstream's opt-in elicitation is separate from its telemetry environment gate.
    # Persist the deployment's opt-out so connecting clients are never prompted.
    consent = WORKSPACE / 'config/blender-mcp/consent_prompt.json'
    consent.parent.mkdir(parents=True, exist_ok=True)
    temporary = consent.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps({'prompt_version': 1, 'action': 'decline',
                                     'consent': False, 'via': 'dev-env-policy'}))
    temporary.replace(consent)
    env.update({
        'XDG_CONFIG_HOME': str(WORKSPACE / 'config'),
        'XDG_CACHE_HOME': str(WORKSPACE / 'cache'),
        'XDG_DATA_HOME': str(WORKSPACE / 'data'),
        'BLENDER_USER_CONFIG': str(WORKSPACE / 'blender-config'),
        'BLENDER_HOST': '127.0.0.1', 'BLENDER_PORT': '9876',
        'LIBGL_ALWAYS_SOFTWARE': '1', 'GALLIUM_DRIVER': 'llvmpipe',
        'BLENDER_MCP_DISABLE_TELEMETRY': '1', 'DISABLE_TELEMETRY': '1',
        'MCP_DISABLE_TELEMETRY': '1', 'DO_NOT_TRACK': '1',
        'PYTHONDONTWRITEBYTECODE': '1', 'UV_OFFLINE': '1',
        'PATH': str(MCP / '.venv/bin') + os.pathsep + env.get('PATH', ''),
    })
    return env


def identity(pid):
    """Linux start time and session/group ID distinguish reused PIDs."""
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return {'pid': pid, 'start': fields[19], 'pgrp': int(fields[2]),
                'session': int(fields[3])}
    except (FileNotFoundError, ProcessLookupError):
        return None


def alive(record):
    return bool(record and identity(record['pid']) == record)


def stop_process(record):
    # pidfd binds the signal to this process even if it exits and its PID is reused.
    if not record:
        return
    try:
        fd = os.pidfd_open(record['pid'])
    except ProcessLookupError:
        return
    try:
        if not alive(record) or record['pgrp'] != record['pid'] or record['session'] != record['pid']:
            return
        signal.pidfd_send_signal(fd, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while alive(record) and time.monotonic() < deadline:
            time.sleep(0.1)
        if alive(record):
            signal.pidfd_send_signal(fd, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        os.close(fd)


def read_state():
    try:
        return json.loads(STATE.read_text())
    except FileNotFoundError:
        return {}


def write_state(state):
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(state))
    temporary.replace(STATE)


def ready(state, timeout=1):
    if not alive(state.get('blender')) or not alive(state.get('xvfb')):
        return False
    try:
        with socket.create_connection(('127.0.0.1', 9876), timeout=timeout) as client:
            client.settimeout(timeout)
            client.sendall(b'{"type":"authoring_health","params":{}}')
            data = b''
            deadline = time.monotonic() + timeout
            while len(data) < 65536 and time.monotonic() < deadline:
                chunk = client.recv(4096)
                if not chunk:
                    return False
                data += chunk
                try:
                    reply = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    continue
                return (reply.get('status') == 'success' and
                        reply.get('result') == {'token': state['token'], 'protocol': 5,
                                               'telemetry': False})
    except (OSError, ValueError, KeyError):
        pass
    return False


@contextlib.contextmanager
def locked():
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    if RUNTIME.is_symlink() or RUNTIME.stat().st_uid != os.getuid():
        raise RuntimeError(f'Unsafe runtime directory: {RUNTIME}')
    RUNTIME.chmod(0o700)
    with (RUNTIME / 'lock').open('a') as lock:
        deadline = time.monotonic() + START_TIMEOUT + 10
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Timed out waiting for another launcher')
                time.sleep(0.1)
        yield


def spawn(command, env, log):
    with (RUNTIME / log).open('ab', buffering=0) as output:
        process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=output, start_new_session=True,
                                   cwd=WORKSPACE / 'candidates')
    record = identity(process.pid)
    if not record:
        raise RuntimeError(f'{command[0]} exited during startup; see {RUNTIME / log}')
    return record


def start(env):
    state = read_state()
    if ready(state):
        return state
    if alive(state.get('blender')):
        raise RuntimeError('Tracked Blender is alive but not ready. Inspect its log; '
                           'stop explicitly before restarting (unsaved scene may exist).')
    if alive(state.get('xvfb')):
        raise RuntimeError('Tracked Xvfb is still alive. Run blender-authoring stop before restarting.')
    # Never adopt or terminate a listener that was not started by this launcher.
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', 9876))
        except OSError as exc:
            raise RuntimeError('Port 9876 is occupied by an untracked listener') from exc
    state = {'token': secrets.token_hex(24)}
    deadline = time.monotonic() + START_TIMEOUT
    try:
        for number in range(90, 120):
            if Path(f'/tmp/.X11-unix/X{number}').exists() or Path(f'/tmp/.X{number}-lock').exists():
                continue
            auth = RUNTIME / 'Xauthority'
            auth.unlink(missing_ok=True)
            auth.touch(mode=0o600)
            subprocess.run(['xauth', '-f', str(auth), 'add', f':{number}', '.',
                            secrets.token_hex(16)], check=True, timeout=3,
                           stdout=subprocess.DEVNULL, stderr=sys.stderr)
            state['display'] = f':{number}'
            env.update(DISPLAY=state['display'], XAUTHORITY=str(auth))
            state['xvfb'] = spawn(['Xvfb', state['display'], '-screen', '0', '1280x720x24',
                                   '-nolisten', 'tcp', '-auth', str(auth), '-noreset'], env, 'xvfb.log')
            write_state(state)
            while alive(state['xvfb']) and time.monotonic() < deadline:
                if Path(f'/tmp/.X11-unix/X{number}').exists():
                    break
                time.sleep(0.1)
            if alive(state['xvfb']) and Path(f'/tmp/.X11-unix/X{number}').exists():
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Xvfb startup timed out')
        else:
            raise RuntimeError('No free virtual display in :90–:119')
        env['BLENDER_AUTHORING_TOKEN'] = state['token']
        state['blender'] = spawn(['/usr/local/bin/blender', '--factory-startup',
                                  '--disable-autoexec', '-noaudio', '--gpu-backend', 'opengl',
                                  '--python', str(INSTALL / 'blender-bootstrap.py')], env, 'blender.log')
        write_state(state)
        while time.monotonic() < deadline:
            if ready(state):
                print(f'Blender ready on {state["display"]}; workspace {WORKSPACE}', file=sys.stderr)
                return state
            if not alive(state['blender']) or not alive(state['xvfb']):
                break
            time.sleep(0.2)
        raise RuntimeError(f'Blender did not become ready within {START_TIMEOUT}s; logs: {RUNTIME}')
    except BaseException:
        stop_process(state.get('blender'))
        stop_process(state.get('xvfb'))
        STATE.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('mcp', 'start', 'status', 'stop'))
    args = parser.parse_args()
    os.umask(0o077)
    env = environment()
    with locked():
        if args.command == 'stop':
            state = read_state()
            stop_process(state.get('blender'))
            stop_process(state.get('xvfb'))
            STATE.unlink(missing_ok=True)
            print('Tracked Blender and Xvfb stopped.', file=sys.stderr)
            return
        if args.command == 'status':
            state = read_state()
            healthy = ready(state)
            print(json.dumps({'ready': healthy, 'blender': state.get('blender'),
                              'display': state.get('display'), 'workspace': str(WORKSPACE),
                              'logs': str(RUNTIME)}))
            raise SystemExit(0 if healthy else 1)
        start(env)
    if args.command == 'mcp':
        # A client disconnect ends only its MCP process, never the shared scene.
        os.execve(str(MCP / '.venv/bin/blender-mcp'), ['blender-mcp'], env)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as error:
        print(f'blender-authoring: {error}', file=sys.stderr)
        raise SystemExit(1)
