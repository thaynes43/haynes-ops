"""Finite local fixtures only: no real API, source hold, Job or library."""
import copy
import datetime as dt
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import unittest
from unittest import mock

import cached_source as cache
import window_contract as wc
import test_window as legacy

PHASE = '11111111-2222-4333-8444-555555555555'
STOP_SHA = 'a' * 40
NORMAL_SHA = 'b' * 40
ROOT = Path(__file__).parent
seal_spec=importlib.util.spec_from_file_location('cached_seal',ROOT/'seal-cached-source.py')
seal=importlib.util.module_from_spec(seal_spec);seal_spec.loader.exec_module(seal)


def native(kind, name, ns='flux-system'):
    return dict(kind=kind, apiVersion={'GitRepository':'source.toolkit.fluxcd.io/v1','Kustomization':'kustomize.toolkit.fluxcd.io/v1','Pod':'v1'}[kind],
        metadata=dict(name=name,namespace=ns,uid=ns+'/'+name,resourceVersion='1',generation=1,annotations={}),spec={},status={})


def archive(blobs=None):
    with io.BytesIO() as output:
        with tarfile.open(fileobj=output,mode='w:') as tar:
            for name, raw in (legacy.STOP if blobs is None else blobs).items():
                member=tarfile.TarInfo('./'+name);member.size=len(raw);tar.addfile(member,io.BytesIO(raw))
        return gzip.compress(output.getvalue(),mtime=0)


def fixture():
    raw=archive()
    source=native('GitRepository','haynes-ops');source['spec']={'interval':'30m','ref':{'branch':'main'},'url':'https://github.com/thaynes43/haynes-ops.git'}
    after=copy.deepcopy(source);after['spec']['suspend']=True;after['metadata'].update(resourceVersion='2',generation=2)
    after['metadata']['annotations']={cache.REQUEST:'source-new',cache.OWNER:PHASE}
    after['status']={'lastHandledReconcileAt':'source-new','observedGeneration':1,
        'artifact':dict(revision='main@sha1:'+STOP_SHA,digest='sha256:'+wc.sha(raw),size=len(raw),
                        url='http://source-controller.flux-system.svc.cluster.local./gitrepository/flux-system/haynes-ops/'+STOP_SHA+'.tar.gz')}
    # The actual service URL may have a trailing DNS dot; normalize fixture only.
    after['status']['artifact']['url']=after['status']['artifact']['url'].replace('cluster.local.','cluster.local')
    pod=native('Pod','source-controller-test');pod['spec']={'nodeName':'worker','containers':[{'name':'manager','image':'ghcr.io/fluxcd/source-controller:v1.9.6'}]}
    pod['status']={'phase':'Running','containerStatuses':[dict(name='manager',ready=True,restartCount=0,imageID='sha256:'+'c'*64,state={'running':{}})]}
    parents, actual_parents, holds, actual_holds={},{},{},{}
    for name in cache.PARENTS:
        before=native('Kustomization',name);before['spec']={'path':'./kubernetes/main/'+name}
        final=copy.deepcopy(before);final['metadata']['resourceVersion']='2';final['metadata']['annotations'][cache.REQUEST]='new-'+name
        final['status']={'lastHandledReconcileAt':'new-'+name,'observedGeneration':1,'lastAppliedRevision':'main@sha1:'+STOP_SHA,'conditions':[dict(type='Ready',status='True')]}
        parents[name]={'before':before,'after':final,'token':'new-'+name};actual_parents[name]=copy.deepcopy(final)
    for ns,name in wc.SCOPES:
        before=native('Kustomization',name,ns);before['spec']={'path':'./'+ns+'/'+name}
        final=copy.deepcopy(before);final['metadata'].update(resourceVersion='2',generation=2)
        final['spec']['suspend']=True;final['metadata']['annotations'][cache.REQUEST]='new-'+name
        # Suspend acknowledgment must not depend on observedGeneration advancing.
        final['status']={'lastHandledReconcileAt':'new-'+name,'observedGeneration':1}
        holds[ns+'/'+name]={'before':before,'after':final,'token':'new-'+name};actual_holds[(ns,name)]=copy.deepcopy(final)
    receipt=dict(schema=1,phase_token=PHASE,pause_pr='3659',restore_pr='3660',restore_head='d'*40,
        stop_main_sha=STOP_SHA,normal_inverse_merge_sha=NORMAL_SHA,source_before=source,source_after=after,
        source_token='source-new',controller_pod=pod,parents=parents,holds=holds)
    return receipt,copy.deepcopy(after),copy.deepcopy(pod),actual_parents,actual_holds,raw


class CachedSourceCases(unittest.TestCase):
    def test_exact_source_and_four_suspend_tokens_without_generation_advance(self):
        r,s,p,parents,holds,raw=fixture()
        cache.validate(r,s,p,parents,holds,raw,legacy.CONTRACT,PHASE)

    def test_stale_inflight_ack_cannot_satisfy_fresh_source_or_application_token(self):
        for target in ('source','hold','parent'):
            r,s,p,parents,holds,raw=fixture()
            if target=='source':s['status']['lastHandledReconcileAt']='old'
            elif target=='hold':holds[wc.SCOPES[0]]['status']['lastHandledReconcileAt']='old'
            else:parents['cluster']['status']['lastHandledReconcileAt']='old'
            with self.assertRaises(ValueError):cache.validate(r,s,p,parents,holds,raw,legacy.CONTRACT,PHASE)
        r,s,p,parents,holds,raw=fixture();r['source_before']['metadata']['annotations'][cache.REQUEST]='source-new'
        with self.assertRaises(ValueError):cache.validate(r,s,p,parents,holds,raw,legacy.CONTRACT,PHASE)
        r,s,p,parents,holds,raw=fixture();r['source_before']['metadata']['resourceVersion']=s['metadata']['resourceVersion']
        with self.assertRaises(ValueError):cache.validate(r,s,p,parents,holds,raw,legacy.CONTRACT,PHASE)

    def test_sealer_owning_alarm_bounds_nested_read_only_work(self):
        at=time.monotonic()
        with self.assertRaises(TimeoutError):
            with seal.wall_guard(.02):time.sleep(.2)
        self.assertLess(time.monotonic()-at,.5)

    def test_cache_read_owning_alarm_bounds_native_child_and_nested_deadline(self):
        r,*_=fixture();children=[];at=time.monotonic()
        def get(*_):
            child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(5)'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            children.append(child)
            try:child.communicate()
            finally:
                if child.poll() is None:child.kill()
                child.communicate(timeout=1)
        with self.assertRaises(TimeoutError):
            with cache.wall_guard(.02):cache.check_live(r,get,legacy.CONTRACT,PHASE)
        self.assertLess(time.monotonic()-at,.5);self.assertIsNotNone(children[0].poll())

    def test_cache_alarm_does_not_shorten_retarget_or_flux_restoration_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory)
            result=subprocess.CompletedProcess([],0,'','')
            with mock.patch.object(Path,'read_text',return_value='public-fixture-token'),mock.patch.object(legacy.watch.subprocess,'run',return_value=result) as run:
                w.run(['bash','retarget-restore.sh'],timeout=120)
                self.assertEqual(run.call_args.kwargs['timeout'],120)
                w.run(['flux','reconcile','source','git','haynes-ops','--timeout=30s'])
                self.assertEqual(run.call_args.kwargs['timeout'],45)

    def test_parent_hold_source_controller_and_artifact_drift_refuse(self):
        for target in ('sourceuid','resume','owner','controlleruid','restart','parent','hold','bytes'):
            r,s,p,parents,holds,raw=fixture()
            if target=='sourceuid':s['metadata']['uid']='replacement'
            elif target=='resume':s['spec']['suspend']=False
            elif target=='owner':s['metadata']['annotations'][cache.OWNER]='other'
            elif target=='controlleruid':p['metadata']['uid']='replacement'
            elif target=='restart':p['status']['containerStatuses'][0]['restartCount']=1
            elif target=='parent':parents['cluster']['status']['lastAppliedRevision']='main@sha1:'+NORMAL_SHA
            elif target=='hold':holds[wc.SCOPES[0]]['spec']['suspend']=False
            else:raw=raw[:-1]+b'!'
            with self.assertRaises(ValueError):cache.validate(r,s,p,parents,holds,raw,legacy.CONTRACT,PHASE)

    def test_active_mode_preserves_historical_hold_proof_after_authorized_release(self):
        r,s,p,parents,holds,raw=fixture()
        cache.validate(r,s,p,parents,{},raw,legacy.CONTRACT,PHASE,False)
        r['holds'][wc.SCOPES[0][0]+'/'+wc.SCOPES[0][1]]['after']['spec']['suspend']=False
        with self.assertRaises(ValueError):cache.validate(r,s,p,parents,{},raw,legacy.CONTRACT,PHASE,False)

    def test_artifact_exact_manifest_and_compression_caps(self):
        r,s,*_=fixture()
        changed=dict(legacy.STOP);changed[wc.PATHS[0]]+=b'# drift\n';raw=archive(changed)
        s['status']['artifact'].update(size=len(raw),digest='sha256:'+wc.sha(raw))
        with self.assertRaises(ValueError):cache.artifact_bytes(s,raw,legacy.CONTRACT,STOP_SHA)
        r,s,p,parents,holds,raw=fixture()
        with mock.patch.object(cache,'MAX_EXPANDED',32),self.assertRaises(ValueError):cache.artifact_bytes(s,raw,legacy.CONTRACT,STOP_SHA)

    def test_bracketed_artifact_read_catches_controller_eviction_during_fetch(self):
        r,s,p,parents,holds,raw=fixture();calls={'pod':0}
        def get(kind,name,ns):
            if kind=='gitrepository':return copy.deepcopy(s)
            if kind=='pod':
                calls['pod']+=1;value=copy.deepcopy(p)
                if calls['pod']==2:value['metadata']['uid']='recreated'
                return value
            return copy.deepcopy(parents[name] if ns=='flux-system' else holds[(ns,name)])
        with mock.patch.object(cache,'fetch',return_value=raw),self.assertRaises(ValueError):cache.check_live(r,get,legacy.CONTRACT,PHASE)

    def test_bounded_fetch_retires_blocked_child_and_caps_unknown_length_body(self):
        r,s,*_=fixture();real_popen=subprocess.Popen;children=[]
        def child(argv,**kw):
            script='import time;time.sleep(5)' if '--blocked' in scenario else 'import sys;sys.stdout.buffer.write(b"x"*64)'
            process=real_popen([sys.executable,'-c',script],**kw);children.append(process);return process
        scenario=['--blocked'];at=time.monotonic()
        with mock.patch.object(cache.subprocess,'Popen',side_effect=child),self.assertRaises(ValueError):cache.fetch(s,time.time()+.05)
        self.assertLess(time.monotonic()-at,.75);self.assertIsNotNone(children[-1].poll())
        scenario=[]
        with mock.patch.object(cache.subprocess,'Popen',side_effect=child),mock.patch.object(cache,'MAX_ARTIFACT',32),self.assertRaises(ValueError):cache.fetch(s)
        self.assertIsNotNone(children[-1].poll())

    def test_atomic_private_activation_no_replace_and_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'active.json';cache.write_private(target,b'{}\n')
            self.assertEqual(cache.read_private(target),b'{}\n');self.assertEqual(target.stat().st_nlink,1)
            with self.assertRaises(OSError):cache.write_private(target,b'overwrite')
            self.assertEqual(cache.read_private(target),b'{}\n')
            target.chmod(0o644)
            with self.assertRaises(ValueError):cache.read_private(target)
            fifo=Path(directory)/'fifo';os.mkfifo(fifo,0o600)
            with self.assertRaises(ValueError):cache.read_private(fifo)

    def test_source_release_is_exact_uid_rv_phase_and_never_adopts_foreign_hold(self):
        r,s,*_=fixture();patch=cache.release_patch(s,s['metadata']['uid'],PHASE)
        self.assertEqual(patch[0],dict(op='test',path='/metadata/uid',value=s['metadata']['uid']))
        self.assertEqual(patch[1]['value'],s['metadata']['resourceVersion'])
        self.assertEqual(patch[-1],dict(op='replace',path='/spec/suspend',value=False))
        with self.assertRaises(ValueError):cache.release_patch(s,'other',PHASE)
        with self.assertRaises(ValueError):cache.release_patch(s,s['metadata']['uid'],'other')

    def test_current_normal_goal_honors_same_six_upgrade_without_blob_rollback(self):
        newer=dict(legacy.NORMAL);newer[wc.PATHS[3]]=newer[wc.PATHS[3]].replace(b'v0.110.5',b'v0.110.6')
        newer[wc.PATHS[2]]=newer[wc.PATHS[2]].replace(b'      lazylibrarian:\n',b'      lazylibrarian:\n        replicas: 2\n',1)
        goal=cache.normal_goal(newer)
        self.assertEqual(goal['deployments'][('downloads','lazylibrarian')]['replicas'],2)
        self.assertTrue(goal['deployments'][('frontend','haynesnetwork-main')]['images'][0][1].endswith(':v0.110.6'))
        with self.assertRaises(ValueError):cache.normal_goal(legacy.STOP)

    def test_bound_activation_cannot_switch_phase_cache_or_inverse(self):
        value=dict(schema=1,phase_token=PHASE,cached_source_receipt_sha256='c'*64,normal_inverse_merge_sha=NORMAL_SHA,armed_ready=True,actuation_budget_started_at='1970-01-01T00:00:10+00:00')
        cache.activation(value,PHASE,'c'*64,NORMAL_SHA)
        for field in ('phase_token','cached_source_receipt_sha256','normal_inverse_merge_sha','armed_ready'):
            changed=dict(value);changed[field]='wrong'
            with self.assertRaises(ValueError):cache.activation(changed,PHASE,'c'*64,NORMAL_SHA)
        changed=dict(value);changed.pop('actuation_budget_started_at')
        with self.assertRaises(ValueError):cache.activation(changed,PHASE,'c'*64,NORMAL_SHA)

    def watcher(self,directory):
        w=object.__new__(legacy.watch.Watchdog);w.cached=True;w.normal_goal=None
        w.args=types.SimpleNamespace(pause='3659',restore='3660',cached_source_receipt=str(Path(directory)/'cache.json'),
            cached_source_activation=str(Path(directory)/'active.json'),deadline=170,arm_deadline=600)
        w.state={'armed_at':'1970-01-01T00:00:00+00:00','cached_source_owner':{'uid':'flux-system/haynes-ops','spec':fixture()[0]['source_before']['spec'],'phase_token':PHASE}}
        w.state_path=Path(directory)/'state.json';w.stop=Path(directory)/'stop';w.save=lambda:None
        w.contract=legacy.CONTRACT;w.phase_checkpoint=lambda:dict(phase_token=PHASE)
        w.stop_actuated=lambda:False
        w.restored_main=lambda merge:NORMAL_SHA;w.events=[]
        w.cleanup_phase_jobs=lambda:w.events.append('writers-pg-absent')
        w.recover_cluster=lambda sha:w.events.append('restore-latest-normal')
        return w

    def test_inverse_already_merged_waits_without_active_ci_or_immediate_restoration(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);receipt=fixture()[0];raw=json.dumps(receipt).encode();cache.write_private(w.args.cached_source_receipt,raw)
            at=dt.datetime.fromtimestamp(100,dt.timezone.utc)
            with mock.patch.object(legacy.watch,'instant',return_value=at),mock.patch.object(cache,'check_live') as check:
                self.assertFalse(w.tick_cached({},dict(mergeCommit={'oid':NORMAL_SHA})))
            self.assertEqual(w.events,[]);self.assertTrue(check.call_args.kwargs['holds']);self.assertIn('cached_source_ready_at',w.state)

    def test_cache_loss_revokes_and_restores_original_clock_on_cold_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);cache.write_private(w.args.cached_source_receipt,json.dumps(fixture()[0]).encode())
            with mock.patch.object(legacy.watch,'instant',return_value=dt.datetime.fromtimestamp(100,dt.timezone.utc)),mock.patch.object(cache,'check_live',side_effect=ValueError('evicted')):
                self.assertTrue(w.tick_cached({},dict(mergeCommit={'oid':NORMAL_SHA})))
            self.assertEqual(w.events,['writers-pg-absent','restore-latest-normal']);self.assertTrue(w.stop.exists())
            revoked=w.state['copy_authority_revoked_at'];armed=w.state['armed_at']
            with mock.patch.object(legacy.watch,'instant',return_value=dt.datetime.fromtimestamp(150,dt.timezone.utc)):
                self.assertTrue(w.tick_cached({},dict(mergeCommit={'oid':NORMAL_SHA})))
            self.assertEqual((w.state['copy_authority_revoked_at'],w.state['armed_at']),(revoked,armed))

    def test_restore_trigger_uses_original_actual_stop_and_never_restarts_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);started='1970-01-01T00:00:10+00:00'
            w.phase_checkpoint=lambda:dict(phase_token=PHASE,first_service_stop_observed_at=started)
            with mock.patch.object(legacy.watch,'instant',return_value=dt.datetime.fromtimestamp(180,dt.timezone.utc)):
                self.assertTrue(w.tick_cached({},dict(mergeCommit={'oid':NORMAL_SHA})))
            self.assertEqual(w.state['window_started_at'],started)
            self.assertEqual(w.events,['writers-pg-absent','restore-latest-normal'])

    def test_writer_pg_absence_and_normal_proof_precede_source_and_app_release(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);w.scopes=list(wc.SCOPES)
            w.phase_checkpoint=lambda:dict(first_service_stop_observed_at='1970-01-01T00:00:10+00:00')
            w.stop_actuated=lambda:True;w.desired_restored=lambda sha:w.events.append('normal-git')
            w.source=lambda sha:w.events.append('source-normal')
            w.run=lambda argv:w.events.append('app-'+argv[1]);w.runtime_restored=lambda sha:True;w.note=lambda text:None
            legacy.watch.Watchdog.recover_cluster(w,NORMAL_SHA)
            self.assertEqual(w.events[:3],['writers-pg-absent','normal-git','source-normal'])
            self.assertLess(w.events.index('writers-pg-absent'),w.events.index('app-resume'))

    def test_crash_after_partial_release_is_bounded_by_durable_pre_release_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);w.state['normal_inverse_verified_at']='1970-01-01T00:00:01+00:00';w.state['normal_inverse_merge_sha']=NORMAL_SHA
            raw=json.dumps(fixture()[0]).encode();cache.write_private(w.args.cached_source_receipt,raw)
            active=dict(schema=1,phase_token=PHASE,cached_source_receipt_sha256=wc.sha(raw),normal_inverse_merge_sha=NORMAL_SHA,
                        armed_ready=True,actuation_budget_started_at='1970-01-01T00:00:10+00:00')
            cache.write_private(w.args.cached_source_activation,json.dumps(active).encode())
            w.view=lambda *_:self.fail('active recovery must not query GitHub advisory/CI')
            with mock.patch.object(legacy.watch,'instant',return_value=dt.datetime.fromtimestamp(181,dt.timezone.utc)):
                self.assertTrue(w.tick())
            self.assertEqual(w.events,['writers-pg-absent','restore-latest-normal'])
            self.assertNotIn('window_started_at',w.state)
            self.assertEqual(w.state['actuation_budget_started_at'],active['actuation_budget_started_at'])

    def test_partial_stop_without_durable_origin_refuses_and_restores_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);raw=json.dumps(fixture()[0]).encode();cache.write_private(w.args.cached_source_receipt,raw)
            active=dict(schema=1,phase_token=PHASE,cached_source_receipt_sha256=wc.sha(raw),normal_inverse_merge_sha=NORMAL_SHA,armed_ready=True)
            cache.write_private(w.args.cached_source_activation,json.dumps(active).encode());w.stop_actuated=lambda:True
            with mock.patch.object(legacy.watch,'instant',return_value=dt.datetime.fromtimestamp(20,dt.timezone.utc)):
                self.assertTrue(w.tick_cached({},dict(mergeCommit={'oid':NORMAL_SHA})))
            self.assertEqual(w.events,['writers-pg-absent','restore-latest-normal']);self.assertNotIn('window_started_at',w.state)
            self.assertTrue(w.state['actual_stop_origin_unknown'])

    def test_due_recovery_revokes_writers_before_git_availability_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);w.state.update(normal_inverse_verified_at='accepted',normal_inverse_merge_sha=NORMAL_SHA)
            w.phase_checkpoint=lambda:dict(phase_token=PHASE,first_service_stop_observed_at='1970-01-01T00:00:10+00:00')
            w.restored_main=lambda *_:(_ for _ in ()).throw(TimeoutError('unavailable'))
            w.view=lambda *_:self.fail('accepted active phase must not query advisory')
            with mock.patch.object(legacy.watch,'instant',return_value=dt.datetime.fromtimestamp(180,dt.timezone.utc)),self.assertRaises(TimeoutError):w.tick()
            self.assertEqual(w.events,['writers-pg-absent']);self.assertTrue(w.stop.exists());self.assertFalse(w.state['complete'])

    def test_actual_converter_strip_or_ransom_hold_drift_prevents_terminal_normal(self):
        w=object.__new__(legacy.watch.Watchdog);w.cached=True;w.normal_goal=cache.normal_goal(legacy.NORMAL)
        converter=wc.yaml.safe_load(legacy.NORMAL[wc.PATHS[0]])
        def get(kind,name,ns):
            if kind=='helmrelease':return dict(metadata={'generation':1},spec={'values':w.normal_goal['helm_values'][(ns,name)]},status={'observedGeneration':1,'conditions':[dict(type='Ready',status='True')]})
            if name=='lazylibrarian-epub-convert':return converter
            images=w.normal_goal['cron_images'].get(name,[])
            return dict(spec={'suspend':False,'jobTemplate':{'spec':{'template':{'spec':{'containers':[dict(image=i) for i in images]}}}}})
        w.kube=get;w.deployment_normal=lambda *_:True
        self.assertTrue(w.runtime_current_normal())
        env=converter['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['env']
        strip=next(e for e in env if e['name']=='STRIP_SERIES_METADATA');strip['value']='1'
        self.assertFalse(w.runtime_current_normal());strip['value']='0'
        hold=next(e for e in env if e['name']=='LIBRARY_HOLD_FOLDERS_JSON');hold['value']='[]'
        self.assertFalse(w.runtime_current_normal())

    def test_accidental_staging_stop_revokes_before_open_inverse_queries_and_retains_holds(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);w.stop_actuated=lambda:True
            w.view=lambda *_:self.fail('unclocked Stop must not wait retarget/CI/advisory')
            def merged_metadata(argv,**kwargs):
                self.assertEqual(w.events,['writers-pg-absent'])
                self.assertEqual(argv[-1],'state,mergeCommit');self.assertEqual(kwargs['timeout'],5)
                return json.dumps({'state':'OPEN'})
            w.run=merged_metadata
            with self.assertRaises(RuntimeError):w.tick()
            self.assertTrue(w.state['actual_stop_origin_unknown']);self.assertTrue(w.stop.exists())
            self.assertNotIn('window_started_at',w.state);self.assertEqual(w.events,['writers-pg-absent'])
            first=w.state['copy_authority_revoked_at'];w.run=lambda *_args,**_kw:json.dumps({'state':'MERGED','mergeCommit':{'oid':NORMAL_SHA}})
            self.assertTrue(w.tick());self.assertEqual(w.state['copy_authority_revoked_at'],first)
            self.assertEqual(w.events[-1],'restore-latest-normal')

    def test_cold_origin_binds_immutable_budget_before_commands_and_takes_earliest(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);raw=json.dumps(fixture()[0]).encode();cache.write_private(w.args.cached_source_receipt,raw)
            active=dict(schema=1,phase_token=PHASE,cached_source_receipt_sha256=wc.sha(raw),normal_inverse_merge_sha=NORMAL_SHA,
                        armed_ready=True,actuation_budget_started_at='1970-01-01T00:01:40+00:00')
            cache.write_private(w.args.cached_source_activation,json.dumps(active).encode())
            w.phase_checkpoint=lambda:dict(phase_token=PHASE,window_started_at='1970-01-01T00:03:20+00:00')
            w.run=lambda *_a,**_k:self.fail('origin binding must precede all native/Git commands')
            with mock.patch.object(legacy.watch,'instant',return_value=dt.datetime.fromtimestamp(250,dt.timezone.utc)):
                self.assertEqual(w.original_service_origin(),100)
                cold=self.watcher(directory);cold.state=copy.deepcopy(w.state);cold.phase_checkpoint=w.phase_checkpoint;cold.run=w.run
                self.assertEqual(cold.original_service_origin(),100)
            self.assertEqual(cold.state['window_started_at'],'1970-01-01T00:03:20+00:00')

    def test_individually_valid_commands_cumulatively_hit_original_ceiling_and_reap(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);origin=time.time()-299.85;original=dt.datetime.fromtimestamp(origin,dt.timezone.utc).isoformat()
            w.state['window_started_at']=original;w.original_service_origin=lambda:origin
            children=[];real_popen=subprocess.Popen
            def child(*args,**kwargs):
                value=real_popen(*args,**kwargs);children.append(value);return value
            def work():
                legacy.watch.Watchdog.run(w,[sys.executable,'-c','import time;time.sleep(.01)'],timeout=45)
                legacy.watch.Watchdog.run(w,[sys.executable,'-c','import time;time.sleep(5)'],timeout=45)
                self.fail('no command may pass original ceiling')
            w.tick_body=work;at=time.monotonic()
            with mock.patch.object(Path,'read_text',return_value='public-fixture-token'),mock.patch.object(subprocess,'Popen',side_effect=child),self.assertRaises(TimeoutError):w.tick()
            self.assertLess(time.monotonic()-at,.75);self.assertEqual(len(children),2);self.assertTrue(all(p.poll() is not None for p in children))
            self.assertIn('service_ceiling_missed_at',w.state);self.assertFalse(w.state['complete']);self.assertTrue(w.stop.exists())
            self.assertEqual(w.state['window_started_at'],original)

    def test_service_control_inside_cached_proof_cannot_enter_exception_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);raw=json.dumps(fixture()[0]).encode();cache.write_private(w.args.cached_source_receipt,raw)
            w.state.update(normal_inverse_verified_at='accepted',normal_inverse_merge_sha=NORMAL_SHA)
            origin=time.time()-299.9;w.state['window_started_at']=dt.datetime.fromtimestamp(origin,dt.timezone.utc).isoformat()
            children=[];real_popen=subprocess.Popen
            def child(*args,**kwargs):
                value=real_popen(*args,**kwargs);children.append(value);return value
            def get(*_):
                legacy.watch.Watchdog.run(w,[sys.executable,'-c','import time;time.sleep(5)'],timeout=45)
                self.fail('blocked child must be retired')
            w.kube=get
            def proof():
                with mock.patch.object(legacy.watch,'instant',return_value=dt.datetime.fromtimestamp(origin+100,dt.timezone.utc)):
                    return w.tick_cached({},dict(mergeCommit={'oid':NORMAL_SHA}))
            with mock.patch.object(Path,'read_text',return_value='public-fixture-token'),mock.patch.object(subprocess,'Popen',side_effect=child),self.assertRaises(TimeoutError):w.service_budget(origin,proof)
            self.assertEqual(w.events,[]);self.assertEqual(len(children),1);self.assertIsNotNone(children[0].poll())
            self.assertIn('service_ceiling_missed_at',w.state);self.assertTrue(w.stop.exists())

    def test_expired_cold_window_runs_only_safety_normal_proof_and_never_becomes_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);original='1970-01-01T00:00:10+00:00'
            w.state.update(window_started_at=original,normal_inverse_merge_sha=NORMAL_SHA)
            w.original_service_origin=lambda:self.fail('persisted expiry must cap commands before new clock proof')
            w.scopes=list(wc.SCOPES);w.phase_checkpoint=lambda:dict(phase_token=PHASE,window_started_at=original)
            w.stop_actuated=lambda:True;w.desired_restored=lambda _:w.events.append('normal-git')
            w.source=lambda _:w.events.append('normal-source')
            def run(argv,**_):
                self.assertEqual(argv[0],'flux');self.assertIn(argv[1],('resume','reconcile'));w.events.append('restore-app')
            w.run=run;w.runtime_restored=lambda _:True;w.note=lambda _:None
            w.recover_cluster=types.MethodType(legacy.watch.Watchdog.recover_cluster,w)
            with mock.patch.object(legacy.watch,'instant',return_value=dt.datetime.fromtimestamp(400,dt.timezone.utc)):self.assertTrue(w.tick())
            self.assertFalse(w.state['complete']);self.assertTrue(w.state['safety_recovery_complete'])
            self.assertLess(w.events.index('writers-pg-absent'),w.events.index('normal-source'))
            self.assertLess(w.events.index('normal-git'),w.events.index('normal-source'))
            missed=w.state['service_ceiling_missed_at'];revoked=w.state['copy_authority_revoked_at']
            cold=self.watcher(directory);cold.state=copy.deepcopy(w.state);cold.scopes=w.scopes;cold.phase_checkpoint=w.phase_checkpoint
            cold.original_service_origin=w.original_service_origin;cold.stop_actuated=w.stop_actuated;cold.desired_restored=w.desired_restored
            cold.source=w.source;cold.run=w.run;cold.runtime_restored=w.runtime_restored;cold.note=w.note;cold.events=w.events
            cold.recover_cluster=types.MethodType(legacy.watch.Watchdog.recover_cluster,cold)
            with mock.patch.object(legacy.watch,'instant',return_value=dt.datetime.fromtimestamp(450,dt.timezone.utc)):self.assertTrue(cold.tick())
            self.assertEqual((cold.state['window_started_at'],cold.state['service_ceiling_missed_at'],cold.state['copy_authority_revoked_at']),(original,missed,revoked))
            self.assertFalse(cold.state['complete'])

    def test_safety_recovery_with_unavailable_normal_never_releases_source_or_apps(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);w.state.update(window_started_at='1970-01-01T00:00:10+00:00',normal_inverse_merge_sha=NORMAL_SHA)
            w.restored_main=lambda _:(_ for _ in ()).throw(TimeoutError('unavailable'))
            with mock.patch.object(legacy.watch,'instant',return_value=dt.datetime.fromtimestamp(400,dt.timezone.utc)),self.assertRaises(TimeoutError):w.tick()
            self.assertEqual(w.events,['writers-pg-absent']);self.assertFalse(w.state['complete']);self.assertIn('service_ceiling_missed_at',w.state)

    def test_late_cold_recheck_preserves_historical_success_before_original_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            w=self.watcher(directory);original='1970-01-01T00:00:10+00:00';completed='1970-01-01T00:03:00+00:00'
            w.state.update(window_started_at=original,normal_inverse_merge_sha=NORMAL_SHA,complete=True,completed_at=completed)
            w.scopes=list(wc.SCOPES);w.phase_checkpoint=lambda:dict(phase_token=PHASE,window_started_at=original)
            w.stop_actuated=lambda:False;w.desired_restored=lambda _:w.events.append('normal-git')
            w.runtime_still_normal=lambda:True;w.source=lambda _:w.events.append('normal-source')
            w.run=lambda argv,**_:w.events.append('restore-app') if argv[0]=='flux' else self.fail('no producer/replay')
            w.runtime_restored=lambda _:True;w.note=lambda _:None
            w.recover_cluster=types.MethodType(legacy.watch.Watchdog.recover_cluster,w)
            with mock.patch.object(legacy.watch,'instant',return_value=dt.datetime.fromtimestamp(400,dt.timezone.utc)):self.assertTrue(w.tick())
            self.assertTrue(w.state['complete']);self.assertEqual(w.state['completed_at'],completed)
            self.assertNotIn('service_ceiling_missed_at',w.state);self.assertIn('copy_authority_revoked_at',w.state)
            self.assertEqual(w.state['window_started_at'],original)


if __name__=='__main__':unittest.main()
