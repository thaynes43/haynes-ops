"""Finite fake-native/clock fixtures only; no production API or library access."""
import copy
import datetime as dt
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import types
import unittest
from unittest import mock
import cached_source as cache
import test_cached_source as fixtures

spec=importlib.util.spec_from_file_location('normal_rehearsal',Path(__file__).with_name('normal-rehearsal.py'))
normal=importlib.util.module_from_spec(spec);spec.loader.exec_module(normal)


class NormalCases(unittest.TestCase):
    def fake(self,directory):
        directory=Path(directory);runtime=directory/'runtime';runtime.mkdir(mode=0o700)
        receipt,source,controller,parents,apps,_=fixtures.fixture();raw=fixtures.archive(fixtures.legacy.NORMAL)
        controller['metadata']['labels']={'app':'source-controller'}
        source=copy.deepcopy(receipt['source_before']);source['status']={'artifact':dict(revision='main@sha1:'+fixtures.NORMAL_SHA,
            size=len(raw),digest='sha256:'+fixtures.wc.sha(raw),url='http://source-controller.flux-system.svc.cluster.local./gitrepository/flux-system/haynes-ops/normal.tar.gz')}
        rows={('kustomization',name,'flux-system'):copy.deepcopy(receipt['parents'][name]['before']) for name in cache.PARENTS}
        rows.update({('kustomization',name,ns):copy.deepcopy(receipt['holds'][ns+'/'+name]['before']) for ns,name in fixtures.wc.SCOPES})
        rows[('gitrepository','haynes-ops','flux-system')]=source;rows[('pod',controller['metadata']['name'],'flux-system')]=controller
        owners={ns+'/'+name:dict(uid=row['metadata']['uid'],spec=copy.deepcopy(row['spec']),phase_token=fixtures.PHASE)
            for (kind,name,ns),row in rows.items() if kind=='kustomization'}
        state={'pre_pause_main_sha':fixtures.NORMAL_SHA,'cached_ks_owners':owners,'cached_source_owner':dict(uid=source['metadata']['uid'],spec=copy.deepcopy(source['spec']),phase_token=fixtures.PHASE)}
        packet={'directory':str(directory),'phase_token':fixtures.PHASE,'repo_dir':'unused'}
        ready=runtime/'ready.json';cache.write_private(ready,b'{}\n')
        calls=[]
        def run(argv,**kwargs):
            calls.append(argv)
            if argv[:3]==['kubectl','get','pods']:return json.dumps({'items':[controller]})
            self.assertEqual(argv[:2],['kubectl','patch']);kind,name=argv[2:4];ns=argv[argv.index('-n')+1];row=rows[(kind,name,ns)]
            patch=json.loads(argv[-1]);self.assertEqual(patch[0]['value'],row['metadata']['uid']);self.assertEqual(patch[1]['value'],row['metadata']['resourceVersion'])
            row['metadata'].update(resourceVersion='2',generation=2,annotations=patch[2]['value']);row['spec']['suspend']=True
            row['status']['lastHandledReconcileAt']=row['metadata']['annotations'][cache.REQUEST]
            return '{}'
        observer=types.SimpleNamespace(state=None,save=None,stop=None,log=None,current_main=lambda:fixtures.NORMAL_SHA,
            desired_restored=lambda _:None,runtime_workloads_normal=lambda:True,runtime_still_normal=lambda:True,
            cleanup_phase_jobs=lambda:calls.append(['owned-union-and-pg-absent']),kube=lambda kind,name,ns:copy.deepcopy(rows[(kind,name,ns)]),
            inventory=lambda kind,ns:[copy.deepcopy(controller)],run=run)
        return packet,ready,state,observer,raw,calls

    def test_complete_fake_six_hold_source_normal_proof_always_cancels(self):
        with tempfile.TemporaryDirectory() as directory:
            packet,ready,state,w,raw,calls=self.fake(directory)
            with (mock.patch.object(normal,'assert_watch',return_value=state),mock.patch.object(normal,'watcher',return_value=(w,cache,fixtures.wc,fixtures.legacy.watch)) as factory,
                 mock.patch.object(normal,'register_exercise'),mock.patch.object(cache,'fetch',return_value=raw),mock.patch.object(fixtures.wc,'blobs',return_value=fixtures.legacy.NORMAL),mock.patch.object(normal,'event')):
                normal.exercise(packet,str(ready))
            self.assertEqual(factory.call_args.kwargs,{'initialize':False})
            patches=[a for a in calls if a[:2]==['kubectl','patch']]
            self.assertEqual([a[3] for a in patches],['cluster','cluster-apps',*[name for _,name in normal.APPS],'haynes-ops'])
            self.assertEqual(calls[0],['owned-union-and-pg-absent']);self.assertTrue((Path(directory)/'runtime'/'cancel').exists())
            proof=json.loads(cache.read_private(Path(directory)/'runtime'/'normal-proof.json'))
            self.assertFalse(proof['stop_applied']);self.assertFalse(proof['copy_authorized']);self.assertEqual(len(proof['holds']),6)

    def test_lost_hold_reply_or_pg_refusal_always_cancels_without_false_proof(self):
        for stage in ('before-hold','after-hold'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as directory:
                packet,ready,state,w,raw,calls=self.fake(directory)
                if stage=='before-hold':w.cleanup_phase_jobs=lambda:(_ for _ in ()).throw(RuntimeError('unknown PG owner'))
                else:
                    run=w.run
                    def lost(argv,**kwargs):run(argv,**kwargs);raise TimeoutError('lost hold response')
                    w.run=lost
                with mock.patch.object(normal,'assert_watch',return_value=state),mock.patch.object(normal,'register_exercise'),mock.patch.object(normal,'watcher',return_value=(w,cache,fixtures.wc,fixtures.legacy.watch)),self.assertRaises(Exception):normal.exercise(packet,str(ready))
                self.assertTrue((Path(directory)/'runtime'/'cancel').exists());self.assertFalse((Path(directory)/'runtime'/'normal-proof.json').exists())
                if stage=='before-hold':self.assertEqual(calls,[])

    def test_retired_prior_phase_permits_new_owned_hold_without_erasing_other_annotations(self):
        _,source,_,_,_,_=fixtures.fixture();source['spec']['suspend']=False
        source['metadata']['annotations']['another-controller']='keep'
        with self.assertRaises(ValueError):normal.hold_patch(source,{'uid':source['metadata']['uid'],'spec':source['spec']},'new-phase','fresh-token','GitRepository','haynes-ops','flux-system')
        source['metadata']['annotations'].pop(cache.OWNER)
        patch=normal.hold_patch(source,{'uid':source['metadata']['uid'],'spec':source['spec']},'new-phase','fresh-token','GitRepository','haynes-ops','flux-system')
        self.assertEqual(patch[2]['value']['another-controller'],'keep');self.assertEqual(patch[2]['value'][cache.OWNER],'new-phase')

    def test_watch_pid_reuse_refuses_before_any_hold(self):
        packet={'directory':'/private','phase_token':fixtures.PHASE};ready={'packet_sha256':normal.sha(normal.canonical(packet)),
            'phase_token':fixtures.PHASE,'pid':os.getpid()+100,'start_ticks':'old','state':'/private/runtime/watch-state.json'}
        with mock.patch.object(normal,'start_ticks',return_value='replacement'),self.assertRaises(ValueError):normal.assert_watch(ready,packet)

    def test_frozen_packet_go_mismatch_refuses_before_importing_or_executing_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'packet.json';cache.write_private(path,b'{"schema":1}\n')
            with mock.patch.object(normal,'modules',side_effect=AssertionError('imports forbidden')),self.assertRaises(ValueError):normal.load_packet(path,'0'*64)

    def test_bounded_nonblocking_aggregate_preserves_stdout_flags_when_pipe_full(self):
        read,write=os.pipe()
        try:
            capacity=fcntl.fcntl(write,fcntl.F_GETPIPE_SZ);os.write(write,b'x'*capacity)
            with mock.patch.object(normal.sys,'stdout',types.SimpleNamespace(fileno=lambda:write)):normal.event({'type':'ready'})
            self.assertTrue(os.get_blocking(write))
        finally:os.close(read);os.close(write)

    def test_cold_recovery_keeps_original_rehearsal_budget_and_never_copy_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700)
            packet={'directory':directory,'repo_dir':'unused','binaries':{}}
            w,_,_,_=normal.watcher(packet,initialize=False);w.state={'armed_at':'1970-01-01T00:00:01+00:00',
                'normal_rehearsal_restore_started_at':'1970-01-01T00:00:10+00:00'};w.save=lambda:None
            w.cleanup_phase_jobs=lambda:None;w.current_main=lambda:fixtures.NORMAL_SHA;w.recover_cluster=lambda _:None
            w.fence_hold_requests=lambda:None
            with mock.patch.object(normal.time,'time',return_value=200),mock.patch.object(normal,'retire_exercise'):self.assertTrue(w.tick())
            self.assertEqual(w.state['normal_rehearsal_restore_started_at'],'1970-01-01T00:00:10+00:00')
            self.assertFalse(w.state['normal_rehearsal_recovered_within_budget']);self.assertFalse(w.state['copy_runtime_authorized'])
            self.assertNotIn('window_started_at',w.state);self.assertNotIn('actuation_budget_started_at',w.state)

    def test_cancel_closes_registration_before_any_process_or_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'phase_token':fixtures.PHASE}
            with mock.patch.object(normal,'assert_watch') as check,mock.patch.object(normal.os,'setsid') as session:
                with self.assertRaisesRegex(ValueError,'registration closed'):normal.register_exercise(packet,{})
                check.assert_not_called();session.assert_not_called()
            self.assertFalse((runtime/'exercise-owner.json').exists())

    def test_exact_exercise_group_retired_before_recovery_and_pid_reuse_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'phase_token':fixtures.PHASE}
            child=subprocess.Popen([sys.executable,'-B','-c',
                'import subprocess,sys,time,pathlib;request=subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(10)"]);pathlib.Path(sys.argv[1]).write_text(str(request.pid));time.sleep(10)',str(runtime/'request-pid')],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            try:
                until=time.monotonic()+2
                while not (runtime/'request-pid').exists():
                    self.assertLess(time.monotonic(),until);time.sleep(.02)
                request_pid=int((runtime/'request-pid').read_text())
                self.assertIn(request_pid,normal.group_members(child.pid))
                owner={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                    'phase_token':fixtures.PHASE,'pid':child.pid,'start_ticks':normal.start_ticks(child.pid),'pgid':child.pid}
                cache.write_private(runtime/'exercise-owner.json',normal.canonical(owner))
                with mock.patch.object(normal,'start_ticks',return_value='replaced'),mock.patch.object(normal.os,'killpg') as kill:
                    with self.assertRaisesRegex(ValueError,'PID replaced'):normal.retire_exercise(packet)
                    kill.assert_not_called()
                normal.retire_exercise(packet);self.assertEqual(child.wait(timeout=1),-9)
                self.assertEqual(normal.group_members(child.pid),{})
            finally:
                if child.poll() is None:child.kill()
                child.wait(timeout=1)

    def test_recovery_orders_authority_retirement_and_barriers_before_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'repo_dir':'unused','binaries':{}}
            w,_,_,_=normal.watcher(packet,initialize=False);w.state={'armed_at':'1970-01-01T00:00:01+00:00'};w.save=lambda:None
            calls=[];w.fence_hold_requests=lambda:calls.append('RV barriers')
            w.cleanup_phase_jobs=lambda:calls.append('writer/PG absence');w.current_main=lambda:fixtures.NORMAL_SHA
            w.recover_cluster=lambda _:calls.append('restore/release')
            with mock.patch.object(normal,'retire_exercise',side_effect=lambda _:calls.append('request group retired')):
                self.assertTrue(w.tick())
            self.assertEqual(calls,['request group retired','RV barriers','writer/PG absence','restore/release'])

    def test_seven_rv_barriers_reject_delayed_hold_and_unknown_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            packet,ready,state,observer,_,_=self.fake(directory)
            packet['binaries']={};w,_,_,_=normal.watcher(packet,initialize=False);w.state=state;w.save=lambda:None
            w.kube=observer.kube
            kinds=[('GitRepository','haynes-ops','flux-system')]+[('Kustomization',n,ns) for ns,n in normal.PARENTS+normal.APPS]
            rows={(kind.lower(),name,ns):observer.kube(kind.lower(),name,ns) for kind,name,ns in kinds}
            old=w.kube('kustomization','cluster','flux-system')
            delayed=normal.hold_patch(old,state['cached_ks_owners']['flux-system/cluster'],fixtures.PHASE,'delayed','Kustomization','cluster','flux-system')
            def run(argv,**kw):
                kind,name=argv[2:4];ns=argv[argv.index('-n')+1];row=rows[(kind,name,ns)];patch=json.loads(argv[-1])
                self.assertEqual(patch[0]['value'],row['metadata']['uid']);self.assertEqual(patch[1]['value'],row['metadata']['resourceVersion'])
                self.assertEqual(patch[2]['value'],row['spec']);row['metadata']['resourceVersion']='barrier'
                row['metadata']['annotations']=patch[3]['value'];return '{}'
            w.kube=lambda kind,name,ns:copy.deepcopy(rows[(kind,name,ns)]);w.run=mock.Mock(side_effect=run)
            w.fence_hold_requests();self.assertEqual(w.run.call_count,7)
            self.assertNotEqual(delayed[1]['value'],rows[('kustomization','cluster','flux-system')]['metadata']['resourceVersion'])
            def submit_delayed():
                current=rows[('kustomization','cluster','flux-system')]
                if delayed[1]['value']!=current['metadata']['resourceVersion']:raise ValueError('server RV test refused')
                current['spec']['suspend']=True
            with self.assertRaisesRegex(ValueError,'server RV test refused'):submit_delayed()
            self.assertFalse(rows[('kustomization','cluster','flux-system')]['spec'].get('suspend',False))
            rows[('gitrepository','haynes-ops','flux-system')]['metadata']['annotations'][cache.OWNER]='foreign'
            w.run.reset_mock()
            with self.assertRaisesRegex(ValueError,'foreign phase'):w.fence_hold_requests()
            w.run.assert_not_called()

    def test_historically_in_budget_normal_cold_recheck_does_not_invent_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700)
            packet={'directory':directory,'repo_dir':'unused','binaries':{}}
            w,_,_,_=normal.watcher(packet,initialize=False);w.state={'normal_only_rehearsal_complete':True,
                'normal_rehearsal_restore_started_at':'1970-01-01T00:00:10+00:00','normal_rehearsal_recovered_within_budget':True}
            w.save=lambda:None;w.cleanup_phase_jobs=lambda:None;w.current_main=lambda:fixtures.NORMAL_SHA;w.recover_cluster=lambda _:None
            with mock.patch.object(normal.time,'time',return_value=200):self.assertTrue(w.tick())
            self.assertTrue(w.state['normal_rehearsal_recovered_within_budget']);self.assertNotIn('normal_rehearsal_recovery_budget_missed_at',w.state)
            self.assertEqual(w.state['normal_rehearsal_restore_started_at'],'1970-01-01T00:00:10+00:00')


if __name__=='__main__':unittest.main()
