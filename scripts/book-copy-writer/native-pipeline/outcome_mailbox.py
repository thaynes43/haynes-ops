"""One private outcome request, executed only by the SOURCE connection owner."""
import contextlib
import hashlib
import json
import os
import signal
import stat
import time

REQUEST = 'copy-outcome-request.json'
RESPONSE = 'copy-outcome-response.json'
CAP_REQUEST = 32 * 1024 * 1024
CAP_RESPONSE = 16 * 1024 * 1024
SCOPE_SHA256 = '1754edf94c3735c5c7cf6a78d30e3bea3b110e7b48a77ef1fea6e16e91c82663'
KEEPER = 'Orson Scott Card/Pathfinder/Pathfinder - Orson Scott Card.epub'
MODULES = {
    'epub_copies.py': 'b79389c89bd5a57b4737ad693d2c209bc513e818343064ec04c8f0fcfb24a7da',
    'epub_metadata.py': 'ce3c5a271cb4c94d91f3154240b4cfbc5e0cc57981969fc5c975a0c0f50eb773',
    'book_copy_writer.py': 'fbfec65f4af933da6ec9aca90536501e4514cebfb8e378584e3085a993493880',
    'bound_census.py': '47c62c82277e5511d5011845ff0600acd0f1d97212077f41b31fba40775065f3',
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode() + b'\n'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@contextlib.contextmanager
def bounded_owner_alarm(epoch, metadata):
    started = time.monotonic()
    previous = signal.getitimer(signal.ITIMER_REAL)
    remaining = epoch - time.time()
    if not 0 < remaining <= 12.01:
        raise metadata.Refused('outcome deadline differs')
    if previous[0]:
        remaining = min(remaining, previous[0])
    # The owning SOURCE signal handlers remain installed, including their Stop.
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if previous[0]:
            signal.setitimer(signal.ITIMER_REAL, max(.000001, previous[0] - (time.monotonic() - started)), previous[1])


class SourceOutcome:
    def __init__(self, verifier, controller, metadata, copies, environ, source_epoch, directory='/tmp', emit=lambda _: None):
        self.verifier, self.controller = verifier, controller
        self.metadata, self.copies = metadata, copies
        self.environ, self.source_epoch = dict(environ), source_epoch
        self.directory, self.emit = directory, emit
        self.completed = False

    def read_request(self):
        with self.metadata.safe_directory(self.directory) as directory:
            raw, info = self.metadata.read_regular(directory, REQUEST, CAP_REQUEST)
            if (info.st_uid != os.getuid() or info.st_gid != os.getgid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o400):
                raise self.metadata.Refused('outcome request is not immutable private owner input')
            self.metadata._same_directory(directory, self.directory)
            return raw, (self.metadata._identity(info), info.st_mode, info.st_uid, info.st_gid)

    def validate(self, request):
        required = {'schema', 'type', 'phase_token', 'job_uid', 'pod_uid', 'runtime_module_sha256',
                    'selected_scope_sha256', 'main_receipt_sha256', 'deadline_epoch', 'payload'}
        if (not isinstance(request, dict) or set(request) != required or type(request['schema']) is not int
                or request['schema'] != 1 or request['type'] != 'copy_outcome_request'
                or request['runtime_module_sha256'] != MODULES or request['selected_scope_sha256'] != SCOPE_SHA256):
            raise self.metadata.Refused('outcome request scope or module schema differs')
        for key, env in [('phase_token', 'COPY_PHASE_TOKEN'), ('job_uid', 'COPY_JOB_UID'), ('pod_uid', 'COPY_POD_UID')]:
            if request[key] != self.environ[env]:
                raise self.metadata.Refused('outcome request SOURCE owner differs')
        receipt = request['main_receipt_sha256']
        if not isinstance(receipt, str) or len(receipt) != 64 or any(c not in '0123456789abcdef' for c in receipt):
            raise self.metadata.Refused('outcome MAIN receipt binding differs')
        epoch = request['deadline_epoch']
        if type(epoch) not in (int, float) or not time.time() < epoch <= min(self.source_epoch, time.time() + 12):
            raise self.metadata.Refused('outcome request original deadline differs')
        payload = request['payload']
        if not isinstance(payload, dict):
            raise self.metadata.Refused('outcome payload differs')
        entries = payload.get('selection', {}).get('entries')
        if not isinstance(entries, list) or len(entries) != 2:
            raise self.metadata.Refused('outcome selection is not the approved two extras')
        scope = {}
        for entry in entries:
            if not isinstance(entry, dict) or entry.get('keeper') != KEEPER or entry.get('path') in scope or entry.get('path') == KEEPER:
                raise self.metadata.Refused('outcome approved keeper or distinct extra differs')
            scope[entry['path']] = entry.get('sha256')
            if KEEPER in scope and scope[KEEPER] != entry.get('keeper_sha256'):
                raise self.metadata.Refused('outcome keeper hashes disagree')
            scope[KEEPER] = entry.get('keeper_sha256')
        if sha(canonical(scope)) != SCOPE_SHA256:
            raise self.metadata.Refused('outcome exact three byte paths differ')
        return epoch

    def process_pending(self):
        self.controller.scan_guard()
        if self.completed:
            return False
        try:
            with self.metadata.safe_directory(self.directory) as directory:
                os.stat(REQUEST, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return False
        with bounded_owner_alarm(min(self.source_epoch, time.time() + 12), self.metadata):
            # An immediate rollback/re-BEGIN may still have INTRANS and a recent
            # local guard clock. Reprove the once-bound transaction before work.
            self.controller.health()
            raw, identity = self.read_request()
            request = json.loads(raw, object_pairs_hook=self.copies.unique_object)
            epoch = self.validate(request)
            current = signal.getitimer(signal.ITIMER_REAL)[0]
            remaining = epoch - time.time()
            if current <= 0 or remaining <= 0:
                raise self.metadata.Refused('outcome original alarm expired')
            signal.setitimer(signal.ITIMER_REAL, min(current, remaining))
            deadline = time.monotonic() + max(0, epoch - time.time())
            outcome = self.verifier.collect(request['payload'], deadline, self.controller.health,
                                            self.controller.scan_guard, self.environ)
            self.controller.scan_guard()
            if self.read_request() != (raw, identity) or not time.time() < epoch:
                raise self.metadata.Refused('outcome request changed or expired')
            response = {'schema': 1, 'type': 'copy_outcome_response', 'phase_token': request['phase_token'],
                        'job_uid': request['job_uid'], 'pod_uid': request['pod_uid'],
                        'request_sha256': sha(raw), 'main_receipt_sha256': request['main_receipt_sha256'],
                        'runtime_module_sha256': MODULES, 'selected_scope_sha256': SCOPE_SHA256,
                        'outcome': outcome, 'production_writes': 0}
            encoded = canonical(response)
            if len(encoded) > CAP_RESPONSE:
                raise self.metadata.Refused('outcome private response exceeds unchanged cap')
            with self.metadata.safe_directory(self.directory) as directory:
                fd = os.open(RESPONSE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(fd, 'wb') as output:
                    output.write(encoded); output.flush(); os.fsync(output.fileno())
                self.metadata._same_directory(directory, self.directory)
            self.completed = True
            self.emit({'type': 'copy-outcome-ready', 'phase_token': request['phase_token'],
                       'job_uid': request['job_uid'], 'pod_uid': request['pod_uid'],
                       'request_sha256': sha(raw), 'response_sha256': sha(encoded), 'production_writes': 0})
            return True
