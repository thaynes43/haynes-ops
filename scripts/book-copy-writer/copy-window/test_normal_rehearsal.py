"""Finite fake-native/clock fixtures only; no production API or library access."""
import copy
import datetime as dt
import fcntl
import importlib.util
import json
import os
import signal
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
            w.cleanup_phase_jobs=lambda:None;w.current_main=lambda:fixtures.NORMAL_SHA
            def recovered(_):w.state.update(complete=True,completed_at='1970-01-01T00:03:20+00:00')
            w.recover_cluster=recovered
            w.fence_hold_requests=lambda:None
            with mock.patch.object(normal.time,'time',return_value=200),mock.patch.object(normal,'retire_exercise'):self.assertTrue(w.tick())
            self.assertEqual(w.state['normal_rehearsal_restore_started_at'],'1970-01-01T00:00:10+00:00')
            self.assertFalse(w.state['normal_rehearsal_recovered_within_budget']);self.assertFalse(w.state['copy_runtime_authorized'])
            self.assertFalse(w.state['complete']);self.assertTrue(w.state['safety_recovery_complete'])
            self.assertEqual(w.state['safety_recovery_completed_at'],'1970-01-01T00:03:20+00:00')
            with mock.patch.object(normal.time,'time',return_value=300):self.assertTrue(w.tick())
            self.assertFalse(w.state['complete']);self.assertFalse(w.state['normal_rehearsal_recovered_within_budget'])
            self.assertEqual(w.state['safety_recovery_completed_at'],'1970-01-01T00:03:20+00:00')
            self.assertNotIn('window_started_at',w.state);self.assertNotIn('actuation_budget_started_at',w.state)

    def test_normal_miss_never_persists_generic_complete_even_before_helper_or_failed_cold_check(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700)
            packet={'directory':directory,'repo_dir':'unused','binaries':{}}
            w,_,_,watch=normal.watcher(packet,initialize=False)
            origin='1970-01-01T00:00:10+00:00';miss='1970-01-01T00:01:00+00:00'
            w.state={'complete':False,'normal_rehearsal_restore_started_at':origin,
                     'normal_rehearsal_recovery_budget_missed_at':miss,
                     'normal_rehearsal_recovered_within_budget':True,'copy_runtime_authorized':True}
            saved=[];w.save=lambda:saved.append(copy.deepcopy(w.state))
            w.cleanup_phase_jobs=lambda:None;w.desired_restored=lambda _:None
            w.phase_checkpoint=lambda:{'phase_token':fixtures.PHASE};w.stop_actuated=lambda:False
            w.runtime_still_normal=lambda:True;w.source=lambda _:None;w.release_ks=lambda *_:None
            w.run=lambda *_:None;w.runtime_restored=lambda _:True;w.verify_recovery_absence=lambda:None;w.reconcile_ks=lambda *_:None;w.retire_hold_annotations=lambda:None;w.note=lambda _:None
            first='1970-01-01T00:03:20+00:00';later='1970-01-01T00:04:20+00:00'
            with mock.patch.object(watch,'stamp',return_value=first):w.recover_cluster(fixtures.NORMAL_SHA)
            self.assertTrue(w.state['safety_recovery_complete']);self.assertFalse(w.state['complete'])
            self.assertTrue(saved);self.assertTrue(all(row.get('complete') is False for row in saved))
            with mock.patch.object(watch,'stamp',return_value=later):w.recover_cluster(fixtures.NORMAL_SHA)
            self.assertEqual(w.state['safety_recovery_completed_at'],first)
            self.assertEqual(w.state['safety_recovery_reverified_at'],later)
            # The actual historical state had generic completed_at but no
            # safety timestamp; cold correction must keep that first proof.
            w.state.pop('safety_recovery_completed_at');w.state.update(complete=True,completed_at=first)
            with mock.patch.object(watch,'stamp',return_value=later):w.recover_cluster(fixtures.NORMAL_SHA)
            self.assertEqual(w.state['safety_recovery_completed_at'],first)
            self.assertFalse(w.state['complete'])
            w.runtime_restored=lambda _:False
            with self.assertRaisesRegex(RuntimeError,'waiting for app/KS convergence'):w.recover_cluster(fixtures.NORMAL_SHA)
            self.assertTrue(all(row.get('complete') is False for row in saved))
            w.state['complete']=True
            w.cleanup_phase_jobs=lambda:(_ for _ in ()).throw(RuntimeError('cleanup unknown'))
            with self.assertRaisesRegex(RuntimeError,'cleanup unknown'):w.recover_cluster(fixtures.NORMAL_SHA)
            self.assertFalse(saved[-1]['complete'])
            w.current_main=lambda:fixtures.NORMAL_SHA
            w.state.update(complete=True,normal_only_rehearsal_complete=True)
            with self.assertRaisesRegex(RuntimeError,'cleanup unknown'):w.tick()
            self.assertFalse(saved[-1]['complete'])
            self.assertTrue(all(row.get('normal_rehearsal_recovered_within_budget') is False and row.get('copy_runtime_authorized') is False for row in saved))
            self.assertEqual((w.state['normal_rehearsal_restore_started_at'],w.state['normal_rehearsal_recovery_budget_missed_at']),(origin,miss))

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
            def restore(_):w.cleanup_phase_jobs();calls.append('restore/release')
            w.recover_cluster=restore
            with mock.patch.object(normal,'retire_exercise',side_effect=lambda _:calls.append('request group retired')):
                self.assertTrue(w.tick())
            self.assertEqual(calls,['request group retired','RV barriers','writer/PG absence','restore/release'])

    def test_empty_original_group_with_reused_pid_persists_cold_retirement(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'phase_token':fixtures.PHASE}
            owner={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'pid':os.getpid()+100000,'start_ticks':'original','pgid':os.getpid()+100000}
            cache.write_private(runtime/'exercise-owner.json',normal.canonical(owner))
            with mock.patch.object(normal,'start_ticks',return_value='replacement'),mock.patch.object(normal,'group_members',return_value={}) as group,mock.patch.object(normal.os,'killpg') as kill:
                normal.retire_exercise(packet);group.assert_called_once_with(owner['pgid']);kill.assert_not_called()
            proof=normal.read_private(runtime/'exercise-retired.json')
            with mock.patch.object(normal,'start_ticks',side_effect=AssertionError('retired identity must not be readopted')),mock.patch.object(normal,'group_members',side_effect=AssertionError('new group is not owned')),mock.patch.object(normal.os,'killpg') as kill:
                normal.retire_exercise(packet);kill.assert_not_called()
            self.assertEqual(normal.read_private(runtime/'exercise-retired.json'),proof)

    def test_retirement_receipt_wrong_owner_binding_refuses_without_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'phase_token':fixtures.PHASE};pid=os.getpid()+100000
            owner={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'pid':pid,'start_ticks':'original','pgid':pid}
            cache.write_private(runtime/'exercise-owner.json',normal.canonical(owner))
            wrong={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'owner_sha256':'0'*64,'original_group':pid,'retired_at':'private-proof'}
            wrong['group_members']={}
            wrong['basis']='empty_original_group'
            cache.write_private(runtime/'exercise-retired.json',normal.canonical(wrong))
            with mock.patch.object(normal.os,'killpg') as kill,self.assertRaisesRegex(ValueError,'receipt changed'):
                normal.retire_exercise(packet)
            kill.assert_not_called()

    def test_reused_pid_leads_foreign_group_original_is_retired_without_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'phase_token':fixtures.PHASE};pid=os.getpid()+100000
            owner={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'pid':pid,'start_ticks':'original','pgid':pid}
            cache.write_private(runtime/'exercise-owner.json',normal.canonical(owner))
            with mock.patch.object(normal,'start_ticks',return_value='replacement'),mock.patch.object(normal,'group_members',return_value={pid:'replacement'}),mock.patch.object(normal.os,'killpg') as kill:
                normal.retire_exercise(packet);kill.assert_not_called()
            proof=json.loads(normal.read_private(runtime/'exercise-retired.json'))
            self.assertEqual(proof['basis'],'replacement_pid');self.assertEqual(proof['group_members'],{})

    def test_prior_captured_missing_leader_retires_original_orphan_group(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'phase_token':fixtures.PHASE};pid=os.getpid()+100000
            owner={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'pid':pid,'start_ticks':'original','pgid':pid}
            cache.write_private(runtime/'exercise-owner.json',normal.canonical(owner))
            custody={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'owner_sha256':normal.sha(normal.canonical(owner)),
                'original_group':pid,'members':{str(pid):'original',str(pid+1):'owned-descendant'}}
            cache.write_private(runtime/'exercise-members.json',normal.canonical(custody))
            with mock.patch.object(normal,'start_ticks',side_effect=FileNotFoundError),mock.patch.object(normal,'group_members',side_effect=[{pid+1:'owned-descendant'},{pid+1:'owned-descendant'},{}]),mock.patch.object(normal,'stop_member',return_value=True),mock.patch.object(normal,'signal_member') as retire:
                normal.retire_exercise(packet);retire.assert_called_once_with(pid+1,'owned-descendant',pid)
            proof=json.loads(normal.read_private(runtime/'exercise-retired.json'))
            self.assertEqual(proof['basis'],'retired_owned_group');self.assertEqual(proof['group_members'],{})

    def test_replacement_group_with_exited_leader_has_no_original_custody(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'phase_token':fixtures.PHASE};pid=os.getpid()+100000
            owner={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'pid':pid,'start_ticks':'original','pgid':pid}
            cache.write_private(runtime/'exercise-owner.json',normal.canonical(owner))
            with mock.patch.object(normal,'start_ticks',side_effect=FileNotFoundError),mock.patch.object(normal,'group_members',return_value={pid+1:'foreign-orphan'}),mock.patch.object(normal,'signal_member') as retire:
                with self.assertRaisesRegex(ValueError,'member custody unproved'):normal.retire_exercise(packet)
                retire.assert_not_called()
            self.assertFalse((runtime/'exercise-members.json').exists());self.assertFalse((runtime/'exercise-retired.json').exists())

    def test_live_new_member_persists_custody_before_signal_and_cold_orphan_retirement(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'phase_token':fixtures.PHASE};pid=os.getpid()+100000
            owner={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'pid':pid,'start_ticks':'original','pgid':pid}
            cache.write_private(runtime/'exercise-owner.json',normal.canonical(owner))
            def interrupted(member,birth,group):
                proofs=list(runtime.glob('exercise-member-*.json'));self.assertEqual(len(proofs),1)
                self.assertEqual(json.loads(normal.read_private(proofs[0]))['pid'],pid+1)
                self.assertEqual((member,birth,group),(pid+1,'late-child',pid))
                raise ValueError('interrupted after descendant retirement')
            with mock.patch.object(normal,'start_ticks',return_value='original'),mock.patch.object(normal,'group_members',side_effect=[{pid:'original'},{pid:'original',pid+1:'late-child'}]),mock.patch.object(normal,'stop_member',return_value=True),mock.patch.object(normal,'member_state',return_value='T'),mock.patch.object(normal,'signal_member',side_effect=interrupted):
                with self.assertRaisesRegex(ValueError,'interrupted'):normal.retire_exercise(packet)
            original=normal.read_private(runtime/'exercise-members.json')
            with mock.patch.object(normal,'start_ticks',side_effect=FileNotFoundError),mock.patch.object(normal,'group_members',side_effect=[{pid+1:'late-child'},{pid+1:'late-child'},{}]),mock.patch.object(normal,'stop_member',return_value=True),mock.patch.object(normal,'signal_member') as retire:
                normal.retire_exercise(packet);retire.assert_called_once_with(pid+1,'late-child',pid)
            self.assertEqual(normal.read_private(runtime/'exercise-members.json'),original)
            self.assertEqual(json.loads(normal.read_private(runtime/'exercise-retired.json'))['basis'],'retired_owned_group')

    def test_missing_leader_unknown_new_member_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'phase_token':fixtures.PHASE};pid=os.getpid()+100000
            owner={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'pid':pid,'start_ticks':'original','pgid':pid}
            cache.write_private(runtime/'exercise-owner.json',normal.canonical(owner))
            custody={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'owner_sha256':normal.sha(normal.canonical(owner)),
                'original_group':pid,'members':{str(pid):'original'}}
            cache.write_private(runtime/'exercise-members.json',normal.canonical(custody))
            with mock.patch.object(normal,'start_ticks',side_effect=FileNotFoundError),mock.patch.object(normal,'group_members',return_value={pid+1:'unknown'}),mock.patch.object(normal,'signal_member') as retire:
                with self.assertRaisesRegex(ValueError,'new member custody unproved'):normal.retire_exercise(packet)
                retire.assert_not_called()
            self.assertEqual(list(runtime.glob('exercise-member-*.json')),[])
            self.assertFalse((runtime/'exercise-retired.json').exists())

    def test_live_member_supplement_cannot_exceed_original_identity_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'phase_token':fixtures.PHASE};pid=os.getpid()+100000
            owner={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'pid':pid,'start_ticks':'original','pgid':pid}
            cache.write_private(runtime/'exercise-owner.json',normal.canonical(owner))
            custody={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'owner_sha256':normal.sha(normal.canonical(owner)),
                'original_group':pid,'members':{str(pid):'original'}|{str(pid+i):'old' for i in range(1,64)}}
            cache.write_private(runtime/'exercise-members.json',normal.canonical(custody))
            with mock.patch.object(normal,'start_ticks',return_value='original'),mock.patch.object(normal,'group_members',return_value={pid:'original',pid+64:'new'}),mock.patch.object(normal,'stop_member',return_value=True),mock.patch.object(normal,'member_state',return_value='T'),mock.patch.object(normal,'signal_member') as retire:
                with self.assertRaisesRegex(ValueError,'captured member identity cap'):normal.retire_exercise(packet)
                retire.assert_not_called()
            self.assertEqual(list(runtime.glob('exercise-member-*.json')),[])

    def test_stopped_leader_retains_custody_through_descendant_fork_and_is_killed_last(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'phase_token':fixtures.PHASE};pid=os.getpid()+100000
            owner={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'pid':pid,'start_ticks':'original','pgid':pid}
            cache.write_private(runtime/'exercise-owner.json',normal.canonical(owner))
            calls=[];alive=True
            def birth(_):
                if alive:return 'original'
                raise FileNotFoundError
            def stop(member,ticks,group,until):
                self.assertTrue((runtime/'exercise-members.json').exists())
                calls.append(('stop',member));return True
            def kill(member,ticks,group):
                nonlocal alive
                calls.append(('kill',member))
                if member==pid:alive=False
            snapshots=[{pid:'original'},{pid:'original',pid+1:'child'},
                {pid:'original',pid+2:'late-grandchild'},{pid:'original',pid+2:'late-grandchild'},
                {pid:'original'},{pid:'original'},{}]
            with mock.patch.object(normal,'start_ticks',side_effect=birth),mock.patch.object(normal,'group_members',side_effect=snapshots),mock.patch.object(normal,'stop_member',side_effect=stop),mock.patch.object(normal,'member_state',return_value='T'),mock.patch.object(normal,'signal_member',side_effect=kill):
                normal.retire_exercise(packet)
            self.assertEqual(calls,[('stop',pid),('stop',pid+1),('kill',pid+1),('stop',pid+2),('kill',pid+2),('kill',pid)])
            self.assertEqual(len(list(runtime.glob('exercise-member-*.json'))),2)
            self.assertEqual(json.loads(normal.read_private(runtime/'exercise-retired.json'))['group_members'],{})

    def test_stop_requires_actual_state_acknowledgement_and_original_deadline(self):
        with mock.patch.object(normal,'signal_member') as send,mock.patch.object(normal,'member_state',side_effect=['S','T']),mock.patch.object(normal.time,'monotonic',return_value=1):
            self.assertTrue(normal.stop_member(12345,'original',12345,2))
            send.assert_called_once_with(12345,'original',12345,signal.SIGSTOP)
        with mock.patch.object(normal,'signal_member') as send,mock.patch.object(normal,'member_state',return_value='S'),mock.patch.object(normal.time,'monotonic',return_value=2):
            with self.assertRaisesRegex(ValueError,'stop dispatch deadline'):normal.stop_member(12345,'original',12345,2)
            send.assert_not_called()

    def test_resumed_original_leader_refuses_before_descendant_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'phase_token':fixtures.PHASE};pid=os.getpid()+100000
            owner={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'pid':pid,'start_ticks':'original','pgid':pid}
            cache.write_private(runtime/'exercise-owner.json',normal.canonical(owner))
            with mock.patch.object(normal,'start_ticks',return_value='original'),mock.patch.object(normal,'group_members',return_value={pid:'original',pid+1:'child'}),mock.patch.object(normal,'stop_member',return_value=True),mock.patch.object(normal,'member_state',return_value='S'),mock.patch.object(normal,'signal_member') as kill:
                with self.assertRaisesRegex(ValueError,'leader resumed'):normal.retire_exercise(packet)
                kill.assert_not_called()
            self.assertFalse((runtime/'exercise-retired.json').exists())

    def test_member_supplement_wrong_owner_refuses_before_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime';runtime.mkdir(mode=0o700);(runtime/'cancel').touch(mode=0o600)
            packet={'directory':directory,'phase_token':fixtures.PHASE};pid=os.getpid()+100000
            owner={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'pid':pid,'start_ticks':'original','pgid':pid}
            cache.write_private(runtime/'exercise-owner.json',normal.canonical(owner))
            binding={'schema':1,'packet_sha256':normal.sha(normal.canonical(packet)),
                'phase_token':fixtures.PHASE,'owner_sha256':normal.sha(normal.canonical(owner)),'original_group':pid}
            cache.write_private(runtime/'exercise-members.json',normal.canonical(binding|{'members':{str(pid):'original'}}))
            bad=binding|{'owner_sha256':'0'*64,'pid':pid+1,'start_ticks':'child'};raw=normal.canonical(bad)
            cache.write_private(runtime/('exercise-member-'+normal.sha(raw)+'.json'),raw)
            with mock.patch.object(normal,'start_ticks',side_effect=FileNotFoundError),mock.patch.object(normal,'group_members',return_value={pid+1:'child'}),mock.patch.object(normal,'signal_member') as retire:
                with self.assertRaisesRegex(ValueError,'supplement changed'):normal.retire_exercise(packet)
                retire.assert_not_called()

    def test_final_signal_uses_captured_pidfd_when_numeric_pid_is_replaced(self):
        pid,fd=12345,99;numeric={'birth':'original'};handles={fd:'original'};signaled=[]
        fields=['S','1',str(pid)]+['0']*16+['original']
        class FakePath:
            def __init__(self,value):self.value=str(value)
            def __truediv__(self,value):return FakePath(self.value+'/'+str(value))
            def read_text(self):
                if self.value.endswith('/stat'):return str(pid)+' (exercise) '+' '.join(fields)
                return 'Pid:\t'+str(pid)+'\n'
        def send(handle,sig):
            numeric['birth']='replacement';signaled.append((handles[handle],sig))
        with mock.patch.object(normal,'Path',FakePath),mock.patch.object(normal.os,'pidfd_open',return_value=fd),mock.patch.object(normal.os,'close'),mock.patch.object(normal.signal,'pidfd_send_signal',side_effect=send),mock.patch.object(normal.os,'killpg') as group:
            normal.signal_member(pid,'original',pid);group.assert_not_called()
        self.assertEqual(numeric['birth'],'replacement');self.assertEqual(signaled,[('original',9)])

    def test_replacement_before_pidfd_validation_is_never_signaled(self):
        pid,fd=12345,99;fields=['S','1',str(pid)]+['0']*16+['replacement']
        class FakePath:
            def __init__(self,value):self.value=str(value)
            def __truediv__(self,value):return self
            def read_text(self):return str(pid)+' (replacement) '+' '.join(fields)
        with mock.patch.object(normal,'Path',FakePath),mock.patch.object(normal.os,'pidfd_open',return_value=fd),mock.patch.object(normal.os,'close') as close,mock.patch.object(normal.signal,'pidfd_send_signal') as send:
            normal.signal_member(pid,'original',pid);send.assert_not_called();close.assert_called_once_with(fd)

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

    def test_normal_harness_inherits_actual_generic_recovery_and_all_release_barriers(self):
        with tempfile.TemporaryDirectory() as directory:
            packet,_,state,observer,_,_=self.fake(directory)
            w,_,_,base=normal.watcher(packet,initialize=False);w.state=state;w.save=lambda:None
            for method in ('recover_cluster','cleanup_phase_jobs','verify_recovery_absence','source','reconcile_ks','retire_hold_annotations'):
                self.assertIs(getattr(type(w),method),getattr(base.Watchdog,method))
            rows={(kind,name,ns):observer.kube(kind,name,ns) for kind,name,ns in
                  [('gitrepository','haynes-ops','flux-system')]+[('kustomization',n,ns) for ns,n in normal.PARENTS+normal.APPS]}
            for row in rows.values():
                row['spec']['suspend']=False
                row['status']['conditions']=[dict(type='Ready',status='True')]
                row['status']['lastAppliedRevision']='main@sha1:'+fixtures.NORMAL_SHA
            w.kube=lambda kind,name,ns:copy.deepcopy(rows[(kind,name,ns)])
            calls=[];w.run=lambda argv,**kw:calls.append(tuple(argv[:3]))
            w.phase_checkpoint=lambda:dict(owned_jobs=[dict(namespace=ns,name=n,uid=None,phase_token=fixtures.PHASE) for ns,n in normal.JOBS],
                phase_token=fixtures.PHASE,pg_leases=[dict(application_name=n,backend_pid=None) for _,n in normal.JOBS[:2]])
            w.inventory=lambda kind,ns:calls.append(('inventory',kind,ns)) or []
            w.desired_restored=lambda _:None;w.stop_actuated=lambda:False;w.runtime_still_normal=lambda:True
            w.runtime_restored=lambda _:True;w.retire_hold_annotations=lambda:calls.append(('retire',));w.note=lambda _:None
            w.recover_cluster(fixtures.NORMAL_SHA)
            self.assertTrue(w.state['complete'])
            self.assertEqual(sum(c[0]=='inventory' for c in calls),48)
            self.assertEqual(sum(c[:2]==('kubectl','exec') for c in calls),8)
            self.assertEqual(sum(c[:3]==('flux','reconcile','source') for c in calls),1)
            self.assertEqual(sum(c[:3]==('flux','reconcile','kustomization') for c in calls),6)
            self.assertEqual(calls[-1],('retire',))
            # Unknown final union never publishes completion or retires owners.
            w.state['complete']=False;counter=0
            def unknown(kind,ns):
                nonlocal counter
                counter+=1
                if counter==43:raise RuntimeError('final union unknown')
                return []
            w.inventory=unknown;calls.clear()
            with self.assertRaisesRegex(RuntimeError,'final union unknown'):w.recover_cluster(fixtures.NORMAL_SHA)
            self.assertFalse(w.state['complete']);self.assertNotIn(('retire',),calls)

    def test_normal_absence_refuses_phase_pod_or_pg_without_deleting(self):
        with tempfile.TemporaryDirectory() as directory:
            packet,_,state,_,_,_=self.fake(directory)
            w,_,_,_=normal.watcher(packet,initialize=False);w.state=state;w.save=lambda:None
            w.phase_checkpoint=lambda:dict(owned_jobs=[dict(namespace=ns,name=n,uid=None,phase_token=fixtures.PHASE) for ns,n in normal.JOBS],
                phase_token=fixtures.PHASE,pg_leases=[dict(application_name=n,backend_pid=None) for _,n in normal.JOBS[:2]])
            w.run=mock.Mock()
            w.inventory=lambda kind,ns:[dict(metadata=dict(name='unregistered',uid='unknown',labels={'issue825.haynesnetwork/phase':fixtures.PHASE}))] if kind=='Pod' else []
            with self.assertRaisesRegex(RuntimeError,'Pod union'):w.cleanup_phase_jobs()
            w.run.assert_not_called()
            w.inventory=lambda *_:[];w.run.side_effect=RuntimeError('PG absence unknown')
            with self.assertRaisesRegex(RuntimeError,'PG absence unknown'):w.cleanup_phase_jobs()
            self.assertEqual(w.run.call_args.args[0][:2],['kubectl','exec'])

    def test_normal_source_reuse_still_refuses_uid_spec_phase_revision_or_ready_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            packet,_,state,observer,_,_=self.fake(directory)
            w,_,_,_=normal.watcher(packet,initialize=False);w.state=state;w.recovery_source_reconciled=True
            original=observer.kube('gitrepository','haynes-ops','flux-system');original['spec']['suspend']=False
            original['status']['conditions']=[dict(type='Ready',status='True')]
            w.run=mock.Mock();w.kube=lambda *_:copy.deepcopy(original)
            w.source(fixtures.NORMAL_SHA);w.run.assert_not_called()
            variants=[]
            row=copy.deepcopy(original);row['metadata']['uid']='replacement';variants.append(row)
            row=copy.deepcopy(original);row['spec']['interval']='1s';variants.append(row)
            row=copy.deepcopy(original);row['metadata']['annotations'][cache.OWNER]='foreign';variants.append(row)
            row=copy.deepcopy(original);row['status']['artifact']['revision']='main@sha1:'+'0'*40;variants.append(row)
            row=copy.deepcopy(original);row['status']['conditions']=[];variants.append(row)
            for row in variants:
                w.kube=lambda *_:copy.deepcopy(row)
                with self.assertRaises((RuntimeError,ValueError)):w.source(fixtures.NORMAL_SHA)
            w.run.assert_not_called()

    def test_normal_controller_reconciles_stale_but_refuses_foreign_or_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            packet,_,state,observer,_,_=self.fake(directory)
            w,_,_,_=normal.watcher(packet,initialize=False);w.state=state
            row=observer.kube('kustomization','cluster','flux-system');row['spec']['suspend']=False
            row['status']['lastAppliedRevision']='main@sha1:'+'0'*40
            w.kube=lambda *_:copy.deepcopy(row);w.run=mock.Mock()
            w.reconcile_ks('flux-system','cluster',fixtures.NORMAL_SHA)
            self.assertEqual(w.run.call_args.args[0][:4],['flux','reconcile','kustomization','cluster'])
            w.run.reset_mock();row['metadata']['annotations'][cache.OWNER]='foreign'
            with self.assertRaisesRegex(ValueError,'foreign phase'):w.reconcile_ks('flux-system','cluster',fixtures.NORMAL_SHA)
            row['metadata']['annotations'][cache.OWNER]=fixtures.PHASE;row['metadata']['uid']='replacement'
            with self.assertRaises(ValueError):w.reconcile_ks('flux-system','cluster',fixtures.NORMAL_SHA)
            w.run.assert_not_called()

    def test_command_timings_are_bounded_aggregate_only_and_persist_failed_call(self):
        with tempfile.TemporaryDirectory() as directory:
            packet={'directory':directory,'repo_dir':'unused','binaries':{'kubectl':{'path':'/test/kubectl'}}}
            (Path(directory)/'runtime').mkdir(mode=0o700)
            w,_,_,watch=normal.watcher(packet,initialize=False);w.state={'normal_rehearsal_restore_started_at':'original'}
            w.save=mock.Mock()
            with mock.patch.object(watch.Watchdog,'run',return_value='private output'),mock.patch.object(normal.time,'monotonic',side_effect=[10,10.25]):
                self.assertEqual(w.run(['kubectl','exec','private SQL']),'private output')
            with mock.patch.object(watch.Watchdog,'run',side_effect=watch.ServiceCeiling()),mock.patch.object(normal.time,'monotonic',side_effect=[20,20.5]):
                with self.assertRaises(watch.ServiceCeiling):w.run(['kubectl','exec','private secret'])
            self.assertEqual(w.state['normal_rehearsal_call_timings'],{'recovery/kubectl/exec':dict(count=2,seconds=.75,maximum_seconds=.5,failures=1)})
            self.assertEqual(w.state['normal_rehearsal_restore_started_at'],'original');self.assertEqual(w.save.call_count,2)
            self.assertNotIn('private',json.dumps(w.state))

    def test_flux_timings_bind_only_fixed_owned_scopes_including_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            packet={'directory':directory,'repo_dir':'unused','binaries':{'flux':{'path':'/test/flux'}}}
            (Path(directory)/'runtime').mkdir(mode=0o700)
            w,_,_,watch=normal.watcher(packet,initialize=False);w.state={'normal_rehearsal_restore_started_at':'original'};w.save=mock.Mock()
            commands=[['flux','reconcile','source','git','haynes-ops','-n','flux-system','--timeout=30s'],
                ['flux','reconcile','kustomization','cluster','-n','flux-system','--timeout=30s'],
                ['flux','reconcile','kustomization','private-secret','-n','private-namespace']]
            with mock.patch.object(watch.Watchdog,'run',side_effect=['private output',watch.ServiceCeiling(),'private output']),mock.patch.object(normal.time,'monotonic',side_effect=[10,10.25,20,20.5,30,30.75]):
                w.run(commands[0])
                with self.assertRaises(watch.ServiceCeiling):w.run(commands[1])
                w.run(commands[2])
            timings=w.state['normal_rehearsal_call_timings']
            self.assertEqual(set(timings),{'recovery/flux/reconcile/source/flux-system/haynes-ops',
                'recovery/flux/reconcile/kustomization/flux-system/cluster','recovery/flux/reconcile'})
            self.assertEqual(timings['recovery/flux/reconcile/kustomization/flux-system/cluster'],dict(count=1,seconds=.5,maximum_seconds=.5,failures=1))
            self.assertNotIn('private',json.dumps(w.state));self.assertEqual(w.save.call_count,3)

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
