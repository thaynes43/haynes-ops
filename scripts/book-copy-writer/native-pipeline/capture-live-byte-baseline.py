#!/usr/bin/env python3
"""Prepared LIVE read-only byte collector; no PG access or library writes.

The native contract and selected stage scope are delivered to private /tmp files
after host proof of the exact live Job/Pod. Runtime creation requires Root GO.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import time

ROOT = '/data/cephfs-hdd/data/media/books/EBooks'
OUTPUT = '/tmp/live-byte-baseline.json'
ACK = '/tmp/live-baseline-delivery-ack.json'
# A future immutable packet must close these collector and entrypoint bytes.
COLLECTOR_SHA256 = '03f6163873b75f2bf0dc6896851b9da568d5e370ef39b56983eed64def2eb5f0'


class Stop(BaseException):
    pass


def best_effort_event(event):
    """Fixed aggregate telemetry only: a full log pipe must not delay exit."""
    descriptor, blocking = None, None
    try:
        raw = json.dumps(event, separators=(',', ':')).encode() + b'\n'
        if len(raw) > 512:
            return
        descriptor = sys.stdout.fileno()
        blocking = os.get_blocking(descriptor)
        os.set_blocking(descriptor, False)
        os.write(descriptor, raw)
    except Exception:
        pass
    finally:
        if descriptor is not None and blocking is not None:
            try:
                os.set_blocking(descriptor, blocking)
            except OSError:
                pass


def await_delivery_ack(environ, binding, baseline_sha256, deadline, collectors, metadata, copies):
    """Exit only after the host retains these exact bytes under the original alarm."""
    expected = {'schema': 1, 'type': 'live_baseline_delivery_ack',
                'phase_token': environ['COPY_PHASE_TOKEN'], 'job_uid': binding['job_uid'],
                'pod_uid': binding['pod_uid'], 'baseline_sha256': baseline_sha256}
    while True:
        collectors.guard(deadline, metadata, lambda: None)
        try:
            with metadata.safe_directory(os.path.dirname(ACK)) as directory:
                raw, info = metadata.read_regular(directory, os.path.basename(ACK), 4096)
                if (info.st_uid != os.getuid() or info.st_gid != os.getgid()
                        or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)):
                    raise metadata.Refused('LIVE acknowledgment must be private and process-owned')
                metadata._same_directory(directory, os.path.dirname(ACK))
        except FileNotFoundError:
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
            continue
        collectors.guard(deadline, metadata, lambda: None)
        value = json.loads(raw, object_pairs_hook=copies.unique_object)
        if not isinstance(value, dict) or type(value.get('schema')) is not int or value != expected:
            raise metadata.Refused('LIVE acknowledgment identity or baseline hash differs')
        return


def run(environ=os.environ):
    # Closed command refuses before importing modules or touching any file/DB.
    if environ.get('COPY_BASELINE_PHASE_READY') != '1':
        best_effort_event({'type': 'live-baseline-refused', 'code': 'phase_not_authorized', 'production_writes': 0})
        return 2
    sys.path.insert(0, '/copy-writer')
    import bound_census_collectors as collectors
    import epub_copies as copies
    import epub_metadata as metadata
    if hashlib.sha256(Path(collectors.__file__).read_bytes()).hexdigest() != COLLECTOR_SHA256:
        raise metadata.Refused('reviewed collector module pin differs')
    expected = json.loads(environ['COPY_BASELINE_MODULE_SHA256_JSON'], object_pairs_hook=copies.unique_object)
    if expected != collectors.module_hashes(metadata, copies):
        raise metadata.Refused('reviewed LIVE module closure differs')
    if os.getuid() != 1000 or os.getgid() != 1000:
        raise metadata.Refused('LIVE process UID/GID differs')
    epoch = float(environ['COPY_BASELINE_DEADLINE_EPOCH'])
    if not time.time() < epoch <= time.time() + 180:
        raise metadata.Refused('LIVE absolute capture deadline differs')
    deadline = time.monotonic() + max(0, epoch - time.time())
    def stopped(*_):
        raise Stop('LIVE collector alarm/signal')
    previous = {s: signal.signal(s, stopped) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)}
    signal.setitimer(signal.ITIMER_REAL, max(0, epoch - time.time()))
    try:
        collectors.report(best_effort_event, 'stage-start', 'native_binding')
        tick = time.monotonic()
        binding = collectors.await_native_binding(environ, ROOT, deadline, metadata, copies, lambda: None)
        selected = collectors.read_private('/tmp/selected-stage-scope.json', 65536, metadata, copies)
        collectors.report(best_effort_event, 'stage-stop', 'native_binding', stage_seconds=time.monotonic() - tick)
        value = collectors.live_baseline(ROOT, deadline, metadata, copies, lambda: None, binding, selected,
                                         observer=best_effort_event)
        raw = collectors.canonical(value) + b'\n'
        if len(raw) > collectors.MAX_PROOF:
            raise metadata.Refused('complete LIVE baseline exceeds transport cap')
        fd = os.open(OUTPUT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        baseline_sha256 = hashlib.sha256(raw).hexdigest()
        print(json.dumps({'type': 'live-baseline-ready', 'sha256': baseline_sha256,
              'capture_started_at': value['capture_started_at'], 'completed_at': value['completed_at'],
              'job_uid': binding['job_uid'], 'pod_uid': binding['pod_uid'],
              'epub_count': len(value['files']), 'all_file_count': len(value['all_file_fingerprints']),
              'selected_read_only_measurement': value['read_only_stage_measurement'], 'production_writes': 0}), flush=True)
        await_delivery_ack(environ, binding, baseline_sha256, deadline, collectors, metadata, copies)
        print(json.dumps({'type': 'live-baseline-delivered', 'sha256': baseline_sha256,
              'job_uid': binding['job_uid'], 'pod_uid': binding['pod_uid'], 'production_writes': 0}), flush=True)
        return 0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def refuse_exit(error):
    """Owning process only: bounded telemetry, no daemon/executor shutdown wait."""
    event = {'type': 'live-baseline-refused', 'code': 'collector_guard_failed',
             'error_class': type(error).__name__[:80], 'production_writes': 0}
    try:
        best_effort_event(event)
    finally:
        os._exit(2)


def main():
    try:
        status = run()
    except BaseException as error:
        refuse_exit(error)
    # Every successful per-file descriptor and artifact/ACK transport is closed.
    # Waiting daemon queue consumers must not introduce interpreter exit waits.
    os._exit(status)


if __name__ == '__main__':
    main()
