#!/usr/bin/env python3
"""Finite scaled owning-host deadlines; fake native lifecycle, no API/PG/NFS."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
HOST = HERE / 'run-live-byte-baseline.py'
spec = importlib.util.spec_from_file_location('finite_live_host', HOST)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

PRELUDE = '''
import importlib.util,json,os,pathlib,signal,subprocess,sys,threading,time
spec=importlib.util.spec_from_file_location('host',sys.argv[1]);h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
out=pathlib.Path(sys.argv[2])
r=h.Run.__new__(h.Run);r.start=time.time();r.end=r.start+.10;r.total=r.start+.40
r.requests=set();r.stream=None;r.thread=None;r.out=out;r.log_error=None
r.result={'baseline_complete':False,'production_library_writes':0,'PG_operations':0,'host_exit_zero_required':True}
signal.signal(signal.SIGALRM,r.hard_stop)
'''


class HostDeadlineCases(unittest.TestCase):
    def subprocess_case(self, body, stdout=subprocess.PIPE):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        before = time.monotonic()
        child = subprocess.Popen([sys.executable, '-B', '-c', PRELUDE + textwrap.dedent(body),
                                  str(HOST), directory.name], stdin=subprocess.DEVNULL,
                                 stdout=stdout, stderr=subprocess.PIPE, start_new_session=True)
        try:
            output, errors = child.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=1)
            self.fail('scaled original cap did not terminate the owning host')
        return child.returncode, output, errors, Path(directory.name), time.monotonic() - before

    def test_collection_alarm_preserves_remaining_original_cleanup_budget(self):
        code, output, errors, directory, elapsed = self.subprocess_case('''
        r.collect=lambda:time.sleep(10)
        def cleanup():
            assert not r.requests
            (out/'cleanup-entered').write_text(str(time.time()))
            r.result['job_and_all_owned_pods_absent']=True
        r.cleanup=cleanup
        result=r.execute();assert result['refused'] and not result['baseline_complete']
        assert r.total==r.start+.40 and time.time()<r.total
        signal.setitimer(signal.ITIMER_REAL,0)
        ''')
        self.assertEqual(code, 0, errors)
        self.assertTrue((directory / 'cleanup-entered').exists())
        self.assertFalse(json.loads((directory / 'actual-receipt.json').read_bytes())['baseline_complete'])
        self.assertLess(elapsed, 2)

    def test_total_alarm_cannot_be_swallowed_by_cleanup(self):
        code, output, errors, directory, elapsed = self.subprocess_case('''
        (out/'earlier-uid-custody').write_text('synthetic-owned-uid')
        r.collect=lambda:None
        r.cleanup=lambda:time.sleep(10)
        r.execute()
        (out/'continued-after-cap').write_text('unsafe')
        ''')
        self.assertEqual(code, 2, errors)
        self.assertFalse((directory / 'continued-after-cap').exists())
        self.assertFalse((directory / 'actual-receipt.json').exists())
        self.assertTrue((directory / 'earlier-uid-custody').exists())
        self.assertIn(b'original_host_total_deadline', output)
        self.assertLess(elapsed, 2)

    def test_stalled_receipt_fsync_cannot_extend_original_cap(self):
        code, output, errors, directory, elapsed = self.subprocess_case('''
        r.collect=lambda:r.result.update(baseline_complete=True)
        r.cleanup=lambda:r.result.update(job_and_all_owned_pods_absent=True)
        def private(path,value):
            (out/'receipt-publication-entered').write_text('started')
            time.sleep(10)
        h.private=private
        r.execute()
        (out/'continued-after-publication').write_text('unsafe')
        ''')
        self.assertEqual(code, 2, errors)
        self.assertTrue((directory / 'receipt-publication-entered').exists())
        self.assertFalse((directory / 'actual-receipt.json').exists())
        self.assertFalse((directory / 'continued-after-publication').exists())
        self.assertLess(elapsed, 2)

    def test_full_stdout_cannot_block_terminal_refusal(self):
        import fcntl
        reader, writer = os.pipe()
        try:
            os.set_blocking(writer, False)
            capacity = fcntl.fcntl(writer, fcntl.F_GETPIPE_SZ)
            self.assertEqual(os.write(writer, b'x' * capacity), capacity)
            os.set_blocking(writer, True)
            code, output, errors, directory, elapsed = self.subprocess_case('''
            r.collect=lambda:None
            r.cleanup=lambda:time.sleep(10)
            r.execute()
            ''', stdout=writer)
            self.assertEqual(code, 2, errors)
            self.assertFalse((directory / 'actual-receipt.json').exists())
            self.assertLess(elapsed, 2)
        finally:
            os.close(writer)
            os.close(reader)

    def test_log_retirement_join_cannot_extend_original_cap(self):
        code, output, errors, directory, elapsed = self.subprocess_case('''
        r.stream=r.launch([sys.executable,'-c','import time;time.sleep(5)'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        class StalledJoin:
            def join(self,timeout):
                assert 0<timeout<=r.total-time.time()+.01
                (out/'bounded-join-entered').write_text(str(timeout))
                time.sleep(10)
            def is_alive(self):return True
        r.thread=StalledJoin()
        r.collect=lambda:None
        r.cleanup=lambda:(out/'unsafe-absence').write_text('unsafe')
        r.execute()
        ''')
        self.assertEqual(code, 2, errors)
        self.assertTrue((directory / 'bounded-join-entered').exists())
        self.assertFalse((directory / 'unsafe-absence').exists())
        self.assertFalse((directory / 'actual-receipt.json').exists())
        self.assertLess(elapsed, 2)

    def test_blocked_request_is_reaped_before_absence(self):
        code, output, errors, directory, elapsed = self.subprocess_case('''
        r.end=r.start+.25;r.total=r.start+.75
        launch=r.launch
        children=[]
        def capture(*a,**kw):
            child=launch(*a,**kw);children.append(child);return child
        r.launch=capture
        r.collect=lambda:r.command([sys.executable,'-c','import time;time.sleep(5)'],payload=b'x'*65536,seconds=.05)
        def cleanup():
            assert not r.requests and len(children)==1 and children[0].returncode is not None
            (out/'reaped-before-absence').write_text(str(children[0].returncode))
            r.result['job_and_all_owned_pods_absent']=True
        r.cleanup=cleanup
        result=r.execute();assert not result['baseline_complete']
        signal.setitimer(signal.ITIMER_REAL,0)
        ''')
        self.assertEqual(code, 0, errors)
        self.assertTrue((directory / 'reaped-before-absence').exists())
        self.assertLess(elapsed, 2)

    def test_deferred_spawn_signal_retains_child_ownership_until_reap(self):
        code, output, errors, directory, elapsed = self.subprocess_case('''
        r.end=r.start+.25;r.total=r.start+.75
        popen=h.subprocess.Popen
        children=[]
        def interrupted_spawn(*a,**kw):
            child=popen(*a,**kw);children.append(child)
            os.kill(os.getpid(),signal.SIGTERM)
            return child
        h.subprocess.Popen=interrupted_spawn
        r.collect=lambda:r.command([sys.executable,'-c','import time;time.sleep(5)'])
        def cleanup():
            assert len(children)==1 and children[0].returncode is not None and not r.requests
            (out/'registered-and-reaped').write_text('proved')
        r.cleanup=cleanup
        result=r.execute();assert result['refused'] and not result['baseline_complete']
        signal.setitimer(signal.ITIMER_REAL,0)
        ''')
        self.assertEqual(code, 0, errors)
        self.assertTrue((directory / 'registered-and-reaped').exists())
        self.assertLess(elapsed, 2)

    def test_unproved_retirement_cannot_admit_native_absence(self):
        run = host.Run.__new__(host.Run)
        run.start = time.time()
        run.end, run.total = run.start + 2, run.start + 3
        run.requests, run.stream, run.thread, run.log_error = {object()}, None, None, None
        run.collect = lambda: None
        run.result = {'baseline_complete': True}
        run.cleanup = mock.Mock()
        run.retire_transports = mock.Mock(side_effect=host.Refused('owned_request_reap_unproved'))
        with tempfile.TemporaryDirectory() as directory:
            run.out = Path(directory)
            try:
                value = run.execute()
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
            self.assertFalse(value['baseline_complete'])
            self.assertFalse(value['cleanup_complete'])
            run.cleanup.assert_not_called()

    def test_live_drain_cannot_deliver_signal_during_spawn_registration(self):
        code, output, errors, directory, elapsed = self.subprocess_case('''
        r.end=r.start+.50;r.total=r.start+1.25
        alive=threading.Event();done=threading.Event();masks=[]
        def drain():
            masks.append(signal.pthread_sigmask(signal.SIG_BLOCK,set()))
            alive.set();done.wait(2)
        previous=signal.pthread_sigmask(signal.SIG_BLOCK,set())
        r.start_drain(drain);assert alive.wait(.25)
        assert signal.pthread_sigmask(signal.SIG_BLOCK,set())==previous
        assert {signal.SIGALRM,signal.SIGTERM,signal.SIGINT}<=masks[0]
        popen=h.subprocess.Popen;children=[]
        def interrupted_spawn(*a,**kw):
            child=popen(*a,**kw);children.append(child)
            os.kill(os.getpid(),signal.SIGTERM)
            time.sleep(.03)
            assert child not in r.requests
            return child
        h.subprocess.Popen=interrupted_spawn
        r.collect=lambda:r.command([sys.executable,'-c','import time;time.sleep(5)'])
        def cleanup():
            assert len(children)==1 and children[0].returncode is not None and not r.requests
            done.set();r.thread.join(.25);assert not r.thread.is_alive()
            (out/'live-thread-registration-proved').write_text('proved')
        r.cleanup=cleanup
        result=r.execute();assert result['refused'] and not result['baseline_complete']
        signal.setitimer(signal.ITIMER_REAL,0)
        ''')
        self.assertEqual(code, 0, errors)
        self.assertTrue((directory / 'live-thread-registration-proved').exists())
        self.assertLess(elapsed, 2)

    def test_pre_execute_contract_and_pin_refusals_keep_internal_code(self):
        for bad_pin, expected in ((False, 'launch_contract_schema'), (True, 'reviewed_helper_pin')):
            with self.subTest(code=expected):
                code, output, errors, directory, elapsed = self.subprocess_case(f'''
                keys=['output_dir','phase_token','closed_manifest','helper','sender','receiver','native_verifier','collector','ack_receiver','selected_scope']
                contract={{k:{{'path':'/unused','sha256':'0'*64}} for k in keys}}
                contract.update(schema={1 if bad_pin else 2},output_dir=str(out/'unused-output'),phase_token='a'*32)
                path=out/'bad-contract.json';path.write_text(json.dumps(contract))
                sys.argv=['host','--contract',str(path),'--root-authorization',h.GO]
                h.main()
                ''')
                self.assertEqual(code, 2, errors)
                events = [json.loads(line) for line in output.splitlines()]
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]['code'], expected)
                self.assertEqual(events[0]['error_class'], 'Refused')
                self.assertNotIn(b'original_host_total_deadline', output)
                self.assertFalse((directory / 'unused-output' / 'actual-receipt.json').exists())
                self.assertLess(elapsed, 2)

    def cleanup_fixture(self, directory):
        run = host.Run.__new__(host.Run)
        run.start, run.total = time.time(), time.time() + 2
        run.phase, run.uid, run.create_attempted = 'a' * 32, None, True
        run.out, run.result = Path(directory), {'baseline_complete': False}
        run.ready = {'metadata': {'name': host.NAME}}
        run.helper = mock.Mock()
        run.helper.declared_matches.return_value = True
        run.verifier = mock.Mock()
        run.command = mock.Mock(return_value=b'{}')
        return run

    def test_lost_create_plus_empty_inventory_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.cleanup_fixture(directory)
            run.inventory = mock.Mock(return_value={'items': []})
            with self.assertRaisesRegex(host.Refused, 'create_transport_commit_unknown'):
                run.cleanup()
            self.assertTrue(run.result['create_outcome_unknown'])
            self.assertNotIn('job_and_all_owned_pods_absent', run.result)
            run.command.assert_not_called()
            self.assertTrue((Path(directory) / 'unknown-create-observed-jobs.json').exists())

    def test_lost_create_requires_actual_uid_custody_before_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.cleanup_fixture(directory)
            job = {'metadata': {'name': host.NAME, 'uid': '11111111-1111-4111-8111-111111111111',
                                'labels': {host.LABEL: run.phase}}}
            run.inventory = mock.Mock(side_effect=[{'kind': 'JobList', 'items': [job]},
                                                  {'kind': 'JobList', 'items': []},
                                                  {'kind': 'PodList', 'items': []}])
            run.cleanup()
            self.assertEqual(run.uid, job['metadata']['uid'])
            self.assertFalse(run.result['create_outcome_unknown'])
            self.assertTrue(run.result['job_and_all_owned_pods_absent'])
            delete = json.loads(run.command.call_args.args[1])
            self.assertEqual(delete['preconditions'], {'uid': run.uid})
            self.assertEqual(delete['propagationPolicy'], 'Foreground')
            self.assertEqual(json.loads((Path(directory) / 'actual-recovered-job.json').read_bytes()), job)
            run.verifier.baseline_absent.assert_called_once()

    def test_same_phase_job_without_owned_create_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.cleanup_fixture(directory)
            run.create_attempted = False
            run.inventory = mock.Mock(return_value={'items': [{'metadata': {'name': host.NAME, 'uid': 'foreign',
                                  'labels': {host.LABEL: run.phase}}}]})
            with self.assertRaisesRegex(host.Refused, 'cleanup_job_reused'):
                run.cleanup()
            run.command.assert_not_called()

    def test_main_final_stdout_and_exit_do_not_wait_on_full_pipe(self):
        import fcntl
        reader, writer = os.pipe()
        try:
            os.set_blocking(writer, False)
            self.assertEqual(os.write(writer, b'x' * fcntl.fcntl(writer, fcntl.F_GETPIPE_SZ)),
                             fcntl.fcntl(writer, fcntl.F_GETPIPE_SZ))
            os.set_blocking(writer, True)
            code, output, errors, directory, elapsed = self.subprocess_case('''
            class FakeRun:
                def __init__(self,c,started):self.requests=set();self.total=started+200
                def execute(self):return {'baseline_complete':True,'job_and_all_owned_pods_absent':True}
                def remaining(self,maximum):assert time.time()<self.total;return min(maximum,self.total-time.time())
            h.Run=FakeRun
            contract={k:None for k in ['output_dir','phase_token','closed_manifest','helper','sender','receiver','native_verifier','collector','ack_receiver','selected_scope']};contract['schema']=1
            path=out/'fake-contract.json';path.write_text(json.dumps(contract))
            sys.argv=['host','--contract',str(path),'--root-authorization',h.GO]
            h.main()
            ''', stdout=writer)
            self.assertEqual(code, 0, errors)
            self.assertLess(elapsed, 2)
        finally:
            os.close(writer)
            os.close(reader)


if __name__ == '__main__':
    unittest.main()
