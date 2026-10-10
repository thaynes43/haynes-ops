"""Unexecuted source candidate for a finite synthetic NFS capsule.

This module is an unexecuted storage source candidate. Its default __main__
only validates the companion command-budget specification. Explicit execution
requires reviewed orchestration plus the original telemetry/admission/lifecycle
gates; it has not been authorized or run in this unit.
The fixed native-Git reservations need source review; they are not an OS quota.
"""
import dataclasses
import json
import os
import re
import resource
import stat
import subprocess
import time
import fcntl
import errno
import io
import zipfile
import signal
import functools
import argparse
from pathlib import Path

SPEC = Path(__file__).with_name('budget.json')
SCRATCH = Path('/scratch')
GIT = '/usr/bin/git'
FILE_MAX = 16_384
ROLE_GENERATIONS = 384
TOTAL_BYTES = 12 * 1024 * 1024
TOTAL_ENTRIES = 1024
PILOT_PATTERN = re.compile(r'nfs-synth-[0-9a-f]{32}\Z')
TOP_LEVEL = {'repo', 'peer', 'wt-a', 'wt-b', 'control', 'copy', 'archive'}


class CapsuleUnknown(RuntimeError):
    """Stop the route; never retry, remount, or claim storage acceptance."""

    def __init__(self, code, private=None):
        super().__init__(code)
        self.private = private or {}  # final supervisor writes only to a 0600 receipt


def metadata_deadline(function):
    """10s userspace deadline; an uninterruptible kernel call remains Unknown."""
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        started = time.monotonic()
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        def expired(_signum, _frame):
            raise CapsuleUnknown('metadata userspace deadline exceeded')
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, min(10, previous_timer[0]) if previous_timer[0] else 10)
        try:
            return function(*args, **kwargs)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer[0]:
                remaining = previous_timer[0] - (time.monotonic() - started)
                if remaining <= 0:
                    raise CapsuleUnknown('enclosing metadata deadline exceeded')
                signal.setitimer(signal.ITIMER_REAL, remaining, previous_timer[1])
    return wrapped


@dataclasses.dataclass
class Reservation:
    used: int = 0

    def reserve(self, generations):
        if not 0 < generations <= ROLE_GENERATIONS:
            raise CapsuleUnknown('invalid fixed reservation')
        if self.used + generations > ROLE_GENERATIONS:
            raise CapsuleUnknown('role output reservation exhausted')
        self.used += generations


def apply_file_limit():
    """Must precede every producer; inherited by all native Git children."""
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_MAX, FILE_MAX))
    if resource.getrlimit(resource.RLIMIT_FSIZE) != (FILE_MAX, FILE_MAX):
        raise CapsuleUnknown('file limit not established')


def require_file_limit():
    if resource.getrlimit(resource.RLIMIT_FSIZE) != (FILE_MAX, FILE_MAX):
        raise CapsuleUnknown('inherited hard file limit missing')


@metadata_deadline
def validate_root(export, pilot_name, create=False):
    """Inspect/create exactly one unique pilot; never list the export root."""
    if os.getuid() != 1000 or os.getgid() != 1000:
        raise CapsuleUnknown('expected non-root identity missing')
    if not PILOT_PATTERN.fullmatch(pilot_name):
        raise CapsuleUnknown('pilot identity invalid')
    export_fd = os.open(export, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if create:
            # EEXIST is a refusal, never evidence that an old fixture is ours.
            os.mkdir(pilot_name, 0o700, dir_fd=export_fd)
        fd = os.open(pilot_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                     dir_fd=export_fd)
        st = os.fstat(fd)
        if st.st_uid != 1000 or st.st_gid != 1000 or stat.S_IMODE(st.st_mode) != 0o700:
            os.close(fd)
            raise CapsuleUnknown('pilot containment/ownership unconfirmed')
        return Path(export) / pilot_name, fd
    finally:
        os.close(export_fd)


@metadata_deadline
def checked_tree(root, root_fd):
    """No-follow tripwire for only the unique pilot, not aggregate enforcement."""
    expected = os.fstat(root_fd)
    now = os.lstat(root)
    if (now.st_dev, now.st_ino) != (expected.st_dev, expected.st_ino):
        raise CapsuleUnknown('pilot identity changed')
    entries = 0
    regular_bytes = 0
    todo = [(os.dup(root_fd), ())]
    try:
      while todo:
        directory_fd, parent_parts = todo.pop()
        try:
          with os.scandir(directory_fd) as scan:
            for entry in scan:
                entries += 1
                if entries > TOTAL_ENTRIES:
                    raise CapsuleUnknown('entry cap exceeded')
                parts = parent_parts + (entry.name,)
                if parts[0] not in TOP_LEVEL:
                    raise CapsuleUnknown('unexpected pilot parent')
                st = entry.stat(follow_symlinks=False)
                if st.st_uid != 1000 or st.st_gid != 1000:
                    raise CapsuleUnknown('fixture entry ownership changed')
                if stat.S_ISDIR(st.st_mode):
                    fd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=directory_fd)
                    opened = os.fstat(fd)
                    if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
                        os.close(fd)
                        raise CapsuleUnknown('fixture directory identity changed')
                    todo.append((fd, parts))
                elif stat.S_ISREG(st.st_mode):
                    if st.st_nlink != 1:
                        raise CapsuleUnknown('fixture file unexpectedly aliases another inode')
                    if st.st_size > FILE_MAX:
                        raise CapsuleUnknown('regular-file cap exceeded')
                    regular_bytes += st.st_size
                    if regular_bytes > TOTAL_BYTES:
                        raise CapsuleUnknown('regular-byte cap exceeded')
                else:
                    raise CapsuleUnknown('unexpected non-regular fixture entry')
        finally:
            os.close(directory_fd)
    finally:
        for fd, _ in todo:
            os.close(fd)
    return {'entries': entries, 'regular_bytes': regular_bytes}


def fixed_git_environment():
    # No inherited credential/config/provider variables reach Git.
    spec = json.loads(SPEC.read_text())
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(SCRATCH),
           'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'TMPDIR': str(SCRATCH)}
    env.update(spec['git_environment'])
    return env


def verify_toolchain():
    spec = json.loads(SPEC.read_text())
    result = subprocess.run([GIT, '--version'], env=fixed_git_environment(),
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=3)
    import hashlib
    if result.returncode or result.stdout.decode().strip() != spec['toolchain']['git_version']:
        raise CapsuleUnknown('reviewed Git version differs')
    if hashlib.sha256(Path(GIT).read_bytes()).hexdigest() != spec['toolchain']['git_binary_sha256']:
        raise CapsuleUnknown('reviewed Git binary differs')


class FixedGitWriter:
    def __init__(self, root, root_fd, role, reservation):
        require_file_limit()
        if role not in ('A', 'B'):
            raise CapsuleUnknown('unknown role')
        self.root = root
        self.root_fd = root_fd
        self.reservation = reservation
        spec = json.loads(SPEC.read_text())
        self.plan = {op['operation']: op for op in spec['fixed_git_writer_plan_' + role]}
        self.config = [c.replace('/scratch', str(SCRATCH)) for c in spec['all_git_prefix_config']]
        self.completed = set()

    def run_once(self, operation):
        require_file_limit()
        if operation not in self.plan or operation in self.completed:
            raise CapsuleUnknown('unlisted or repeated Git writer')
        op = self.plan[operation]
        checked_tree(self.root, self.root_fd)
        self.reservation.reserve(op['reserved_generations'])
        # Consume before spawn: failure/lost acknowledgement never authorizes retry.
        self.completed.add(operation)
        prefix = ['timeout', '--signal=TERM', '--kill-after=2s', '15s', GIT]
        for value in self.config:
            prefix += ['-c', value]
        started = time.monotonic()
        child = subprocess.Popen(prefix + [a.replace('/scratch', str(SCRATCH)) for a in op['args']], cwd=self.root,
                                 env=fixed_git_environment(), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 start_new_session=True)
        try:
            stdout, stderr = child.communicate(timeout=17)
        except subprocess.TimeoutExpired as error:
            # A hard NFS syscall may outlive TERM/KILL. No next writer or cleanup.
            # The Job supervisor must capture actual termination/D-state privately.
            raise CapsuleUnknown('native command termination unconfirmed', {
                'operation': operation, 'owned_process_id': child.pid,
                'elapsed_seconds': time.monotonic() - started,
                'stdout_partial': (error.output or b'')[:1024].decode(errors='replace'),
                'stderr_partial': (error.stderr or b'')[:1024].decode(errors='replace')}) from None
        elapsed = time.monotonic() - started
        expected_failure = op.get('expected_failure', False)
        expected_ref_lock = (expected_failure and child.returncode != 0
                             and b'refs/heads/pilot.lock' in stderr and b'File exists' in stderr)
        if (expected_failure and not expected_ref_lock) or (not expected_failure and child.returncode != 0):
            raise CapsuleUnknown('fixed native Git writer failed', {
                'operation': operation, 'exit': child.returncode,
                'elapsed_seconds': elapsed,
                'stdout': stdout[:1024].decode(errors='replace'),
                'stderr': stderr[:1024].decode(errors='replace')})
        tree = checked_tree(self.root, self.root_fd)
        return {'operation': operation, 'exit': child.returncode,
                'elapsed_seconds': elapsed, 'output_bytes': len(stdout) + len(stderr),
                **tree}


@metadata_deadline
def known_parent_fd(root_fd, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise CapsuleUnknown('unsafe fixed path')
    if path.parts[0] not in TOP_LEVEL:
        raise CapsuleUnknown('unknown fixed parent')
    fd = os.dup(root_fd)
    try:
        for name in path.parts[:-1]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd, path.name
    except BaseException:
        os.close(fd)
        raise


@metadata_deadline
def fixed_read(root_fd, relative):
    fd, name = known_parent_fd(root_fd, relative)
    try:
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            st = os.fstat(file_fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > FILE_MAX:
                raise CapsuleUnknown('fixed read was not a bounded regular file')
            data = os.read(file_fd, FILE_MAX + 1)
            if len(data) > FILE_MAX:
                raise CapsuleUnknown('fixed read exceeded file cap')
            return data
        finally:
            os.close(file_fd)
    finally:
        os.close(fd)


@metadata_deadline
def fixed_mkdir(root, root_fd, relative, reservation):
    reservation.reserve(1)
    parent, name = known_parent_fd(root_fd, relative)
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    finally:
        os.close(parent)
    checked_tree(root, root_fd)


@metadata_deadline
def fixed_unlink(root_fd, relative, directory=False):
    parent, name = known_parent_fd(root_fd, relative)
    try:
        if directory:
            os.rmdir(name, dir_fd=parent)
        else:
            os.unlink(name, dir_fd=parent)
    finally:
        os.close(parent)


@metadata_deadline
def fixed_rw_open(root_fd, relative):
    parent, name = known_parent_fd(root_fd, relative)
    try:
        fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise CapsuleUnknown('lock was not a regular fixture file')
        return fd
    finally:
        os.close(parent)


@metadata_deadline
def fixed_flock(fd, flags):
    fcntl.flock(fd, flags)


@metadata_deadline
def fixed_rename(root_fd, relative_old, relative_new):
    old_parent, old_name = known_parent_fd(root_fd, relative_old)
    new_parent, new_name = known_parent_fd(root_fd, relative_new)
    try:
        os.rename(old_name, new_name, src_dir_fd=old_parent, dst_dir_fd=new_parent)
        os.fsync(new_parent)
    finally:
        os.close(old_parent); os.close(new_parent)


@metadata_deadline
def fixed_mkdir_denied(root_fd, reservation):
    reservation.reserve(1)
    parent, name = known_parent_fd(root_fd, 'control/mutex')
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            return
        raise CapsuleUnknown('peer mkdir lock was not excluded')
    finally:
        os.close(parent)


@metadata_deadline
def fixed_replace(root, root_fd, relative, payload, reservation, suffix):
    # Both temporary output and replacement count. One fixed suffix per operation.
    temporary = relative + '.new-' + suffix
    reserved_python_write(root, root_fd, temporary, payload, reservation)
    reservation.reserve(1)
    parent, name = known_parent_fd(root_fd, relative)
    try:
        os.rename(Path(temporary).name, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
    checked_tree(root, root_fd)


class FiniteCandidateRunner:
    """Source-only two-role functional candidate; no telemetry acceptance claim.

    Launch must be wrapped by the reviewed 145s helper/180s Job supervisors.
    Native commands have15s+2s userspace bounds. A kernel hard block can exceed
    every userspace timer, so termination must be proved before later cleanup.
    """
    def __init__(self, export, pilot, role):
        apply_file_limit()
        if role not in ('A', 'B'):
            raise CapsuleUnknown('invalid role')
        os.umask(0o077)
        verify_toolchain()
        self.role = role
        self.started = time.monotonic()
        self.deadline = self.started + 145
        self.budget = Reservation()
        for directory in (SCRATCH / 'empty-hooks', SCRATCH / 'empty-template'):
            # Scratch must be a fresh memory EmptyDir in the reviewed Pod source.
            os.mkdir(directory, 0o700)
        if role == 'A':
            self.root, self.fd = validate_root(export, pilot, create=True)
        else:
            for attempt in range(120):
                try:
                    self.root, self.fd = validate_root(export, pilot, create=False)
                    break
                except FileNotFoundError:
                    if attempt == 119 or time.monotonic() >= self.deadline:
                        raise CapsuleUnknown('new pilot rendezvous timed out') from None
                    time.sleep(0.5)
        self.git = FixedGitWriter(self.root, self.fd, role, self.budget)
        self.executed = set()
        self.receipts = []

    def once(self, name):
        if name in self.executed or time.monotonic() >= self.deadline:
            raise CapsuleUnknown('duplicate operation or helper deadline')
        self.executed.add(name)

    def write(self, relative, payload):
        self.once('write:' + relative)
        reserved_python_write(self.root, self.fd, relative, payload, self.budget)

    def marker(self, name):
        self.once('marker:' + name)
        fixed_replace(self.root, self.fd, 'control/' + name,
                      name.encode('ascii') + b'\n', self.budget,
                      'marker-' + self.role)

    def wait(self, name):
        self.once('wait:' + name)
        # Initial start skew gets at most60s inside the unchanged145s deadline.
        # Other handoffs get15s; observations never retry any producer.
        observations = 120 if name in {'seed', 'peer-ready'} else 30
        for _ in range(observations):
            if time.monotonic() >= self.deadline:
                raise CapsuleUnknown('helper deadline')
            try:
                if fixed_read(self.fd, 'control/' + name) == name.encode('ascii') + b'\n':
                    return
                raise CapsuleUnknown('marker contents contradicted')
            except FileNotFoundError:
                time.sleep(0.5)
        raise CapsuleUnknown('fixed rendezvous timed out')

    def command(self, operation):
        self.once('git:' + operation)
        self.receipts.append(self.git.run_once(operation))

    def readonly_status(self, directory):
        self.once('status:' + directory)
        if directory not in {'repo', 'peer', 'wt-a', 'wt-b'}:
            raise CapsuleUnknown('unknown status target')
        prefix = ['timeout', '--signal=TERM', '--kill-after=2s', '15s', GIT]
        for config in self.git.config:
            prefix += ['-c', config]
        child = subprocess.Popen(prefix + ['-C', directory, 'status', '--porcelain=v1', '--untracked-files=all'],
                                 cwd=self.root, env=fixed_git_environment(), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            stdout, stderr = child.communicate(timeout=17)
        except subprocess.TimeoutExpired as error:
            raise CapsuleUnknown('status termination unconfirmed', {
                'owned_process_id': child.pid, 'stdout': (error.output or b'')[:1024].decode(errors='replace'),
                'stderr': (error.stderr or b'')[:1024].decode(errors='replace')}) from None
        if child.returncode:
            raise CapsuleUnknown('fixed status failed', {'exit': child.returncode,
                'stderr': stderr[:1024].decode(errors='replace')})
        expected = b'M  f1\n' if directory == 'wt-a' else b''
        if stdout != expected:
            raise CapsuleUnknown('fixed status or staged WIP differs', {'directory': directory})
        self.receipts.append({'operation': 'status:' + directory, 'exact_expected_status': True})
        checked_tree(self.root, self.fd)

    def verify_peer_ref(self):
        self.once('verify-peer-ref')
        refs = []
        for directory, revision in (('repo', 'HEAD'), ('peer', 'refs/remotes/origin/pilot')):
            command = ['timeout', '--signal=TERM', '--kill-after=2s', '15s', GIT]
            for config in self.git.config:
                command += ['-c', config]
            child = subprocess.Popen(command + ['-C', directory, 'rev-parse', '--verify', revision],
                                     cwd=self.root, env=fixed_git_environment(), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            try:
                stdout, stderr = child.communicate(timeout=17)
            except subprocess.TimeoutExpired as error:
                raise CapsuleUnknown('ref-read termination unconfirmed', {
                    'owned_process_id': child.pid, 'stdout': (error.output or b'')[:1024].decode(errors='replace'),
                    'stderr': (error.stderr or b'')[:1024].decode(errors='replace')}) from None
            if child.returncode or not re.fullmatch(b'[0-9a-f]{40}\\n', stdout):
                raise CapsuleUnknown('fixed ref-read failed', {'exit': child.returncode,
                    'stderr': stderr[:1024].decode(errors='replace')})
            refs.append(stdout)
        if refs[0] != refs[1]:
            raise CapsuleUnknown('peer fetched ref is stale')
        self.receipts.append({'operation': 'verify-peer-ref', 'matching': True})

    def verify_disjoint_workspaces(self):
        self.once('verify-disjoint-inodes')
        records = []
        for relative in ('repo/f1', 'peer/f1', 'wt-a/f1', 'wt-b/f1'):
            parent, name = known_parent_fd(self.fd, relative)
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    st = os.fstat(fd)
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or (st.st_uid, st.st_gid) != (1000,1000):
                        raise CapsuleUnknown('workspace inode identity is unsafe')
                    records.append((st.st_dev, st.st_ino))
                finally:
                    os.close(fd)
            finally:
                os.close(parent)
        if len(set(records)) != 4:
            raise CapsuleUnknown('task workspaces unexpectedly share a file inode')
        self.receipts.append({'operation':'verify-disjoint-inodes','distinct_files':4})

    def run_A(self):
        self.once('role:A')
        fixed_mkdir(self.root, self.fd, 'control', self.budget)
        self.command('init')
        for n in range(8):
            self.write('repo/f' + str(n), bytes([65 + n]) * 512)
        self.command('seed_add'); self.command('seed_commit'); self.marker('seed')
        self.wait('peer-ready')
        for n in (1, 2):
            self.once('revision:' + str(n))
            fixed_replace(self.root, self.fd, 'repo/f0', bytes([80 + n]) * 512,
                          self.budget, 'revision-' + str(n))
            self.command('revision_' + str(n) + '_add')
            self.command('revision_' + str(n) + '_commit')
        self.command('worktree_A'); self.command('worktree_B')
        fixed_replace(self.root, self.fd, 'wt-a/f1', b'W' * 512, self.budget, 'wip')
        self.command('staged_WIP'); self.marker('latest'); self.wait('peer-fetched')
        self.verify_peer_ref()
        self.verify_disjoint_workspaces()
        self.readonly_status('repo'); self.readonly_status('peer'); self.readonly_status('wt-a')
        fixed_mkdir(self.root, self.fd, 'control/mutex', self.budget)
        self.write('repo/.git/refs/heads/pilot.lock', b'fixed-owned-lock\n')
        self.write('control/flock', b'fixed-lock\n')
        lock_fd = fixed_rw_open(self.fd, 'control/flock')
        try:
            fixed_flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.write('control/flock-inode', str(os.fstat(lock_fd).st_ino).encode() + b'\n')
            self.marker('locks-held'); self.wait('locks-denied')
            fixed_unlink(self.fd, 'repo/.git/refs/heads/pilot.lock')
            fixed_unlink(self.fd, 'control/mutex', directory=True)
            fixed_flock(lock_fd, fcntl.LOCK_UN)
            self.marker('locks-released'); self.wait('locks-acquired')
        finally:
            os.close(lock_fd)
        self.archive_roundtrip()
        self.marker('A-done')

    def run_B(self):
        self.once('role:B')
        self.wait('seed'); self.command('clone'); self.command('checkout'); self.marker('peer-ready')
        self.wait('latest'); self.command('fetch'); self.marker('peer-fetched')
        self.verify_peer_ref()
        self.verify_disjoint_workspaces()
        self.readonly_status('peer'); self.readonly_status('repo'); self.readonly_status('wt-b')
        self.wait('locks-held')
        fixed_mkdir_denied(self.fd, self.budget)
        self.command('ref_lock_probe')
        lock_fd = fixed_rw_open(self.fd, 'control/flock')
        try:
            if fixed_read(self.fd, 'control/flock-inode') != str(os.fstat(lock_fd).st_ino).encode() + b'\n':
                raise CapsuleUnknown('peer did not open the same common lock inode')
            try:
                fixed_flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
            else:
                raise CapsuleUnknown('peer flock was not excluded')
            self.marker('locks-denied'); self.wait('locks-released')
            fixed_flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fixed_flock(lock_fd, fcntl.LOCK_UN)
            fixed_mkdir(self.root, self.fd, 'control/mutex', self.budget)
            fixed_unlink(self.fd, 'control/mutex', directory=True)
            self.write('repo/.git/refs/heads/pilot.lock', b'fixed-peer-owned-lock\n')
            fixed_unlink(self.fd, 'repo/.git/refs/heads/pilot.lock')
            self.marker('locks-acquired')
        finally:
            os.close(lock_fd)
        self.wait('A-done'); self.marker('B-done')

    def archive_roundtrip(self):
        self.once('archive')
        for directory in ('copy', 'archive', 'copy/restore'):
            fixed_mkdir(self.root, self.fd, directory, self.budget)
        expected = {}
        for n in range(8):
            name = 'f' + str(n); data = fixed_read(self.fd, 'wt-a/' + name)
            expected_data = (b'R' if n == 0 else b'W' if n == 1 else bytes([65 + n])) * 512
            if data != expected_data:
                raise CapsuleUnknown('fixed WIP or committed payload changed')
            self.write('copy/' + name + '.tmp', data)
            self.budget.reserve(1)
            fixed_rename(self.fd, 'copy/' + name + '.tmp', 'copy/' + name)
            expected[name] = data
        expected['index'] = fixed_read(self.fd, 'repo/.git/worktrees/wt-a/index')
        expected['HEAD'] = fixed_read(self.fd, 'repo/.git/worktrees/wt-a/HEAD')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
            for name, data in expected.items():
                archive.writestr(name, data)
        self.write('archive/wip.zip', output.getvalue())
        with zipfile.ZipFile(io.BytesIO(fixed_read(self.fd, 'archive/wip.zip'))) as archive:
            if set(archive.namelist()) != set(expected):
                raise CapsuleUnknown('archive member set changed')
            for name, original in expected.items():
                data = archive.read(name)
                if data != original:
                    raise CapsuleUnknown('archive bytes changed')
                self.write('copy/restore/' + name, data)
        if fixed_read(self.fd, 'repo/.git/worktrees/wt-a/index') != expected['index'] or fixed_read(self.fd, 'repo/.git/worktrees/wt-a/HEAD') != expected['HEAD']:
            raise CapsuleUnknown('live WIP index/HEAD changed')
        checked_tree(self.root, self.fd)

    def run(self):
        try:
            (self.run_A if self.role == 'A' else self.run_B)()
            if time.monotonic() >= self.deadline:
                raise CapsuleUnknown('helper completion exceeded deadline')
            return {'role': self.role, 'functional_candidate_completed': True,
                    'elapsed_seconds': time.monotonic() - self.started,
                    'reserved_generations': self.budget.used,
                    'tree': checked_tree(self.root, self.fd),
                    'storage_acceptance': False, 'receipts': self.receipts}
        finally:
            os.close(self.fd)


@metadata_deadline
def reserved_python_write(root, root_fd, relative, payload, reservation):
    """One known new regular file only; callers may not supply household paths."""
    require_file_limit()
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise CapsuleUnknown('unsafe fixture path')
    if path.parts[0] not in TOP_LEVEL or len(payload) > FILE_MAX:
        raise CapsuleUnknown('unlisted parent or oversized payload')
    checked_tree(root, root_fd)
    reservation.reserve(1)
    parent_fd = os.dup(root_fd)
    try:
        for name in path.parts[:-1]:
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent_fd)
        try:
            written = 0
            while written < len(payload):
                # Finite <=16KiB write completion, never a burner or storage load loop.
                n = os.write(fd, payload[written:])
                if n <= 0:
                    raise CapsuleUnknown('write did not advance')
                written += n
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    return checked_tree(root, root_fd)


def validate_spec_only():
    spec = json.loads(SPEC.read_text())
    fixed = sum(op['reserved_generations'] for role in ('A', 'B')
                for op in spec['fixed_git_writer_plan_' + role])
    fixed += spec['python_writer_reservations']['total']
    assert fixed == 576
    assert fixed + spec['accounting']['variant_margin_generations'] == 768
    assert 768 * FILE_MAX == TOTAL_BYTES < 16 * 1024 * 1024
    assert TOTAL_ENTRIES < 2048
    for role in ('A', 'B'):
        ops = spec['fixed_git_writer_plan_' + role]
        assert len({op['operation'] for op in ops}) == len(ops)
        assert sum(op['reserved_generations'] for op in ops) + 64 <= ROLE_GENERATIONS
    return {'source_arithmetic_valid': True, 'storage_executed': False,
            'trial_orchestrator_source_present': True,
            'storage_validation_or_acceptance': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Unexecuted finite NFS source candidate')
    parser.add_argument('--execute-candidate', action='store_true')
    parser.add_argument('--pilot')
    parser.add_argument('--role', choices=('A', 'B'))
    args = parser.parse_args()
    if not args.execute_candidate:
        print(json.dumps(validate_spec_only()))
    elif not args.pilot or not args.role:
        parser.error('execution needs the reviewed immutable pilot and role')
    else:
        try:
            print(json.dumps(FiniteCandidateRunner('/discovery', args.pilot, args.role).run()))
        except BaseException as error:
            private = {'classification': type(error).__name__,
                       'code': str(error)[:512], 'details': getattr(error, 'private', {})}
            encoded = json.dumps(private).encode()[:FILE_MAX]
            fd = os.open(SCRATCH / 'capsule-error.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.write(fd, encoded)
            finally:
                os.close(fd)
            print(json.dumps({'functional_candidate_completed': False,
                              'storage_acceptance': False, 'classification': type(error).__name__}))
            raise SystemExit(1) from None
