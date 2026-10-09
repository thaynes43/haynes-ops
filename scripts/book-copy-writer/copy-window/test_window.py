"""Finite public fixtures, fake clocks/APIs and local Git only; no live actions."""
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import window_contract as wc


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watch = load('window_watch', 'copy-recovery-watch.py')
supervisor = load('window_supervisor', 'copy-phase-supervisor.py')
CONTRACT = json.loads((ROOT / 'manifest-contract.json').read_bytes())
REPO = ROOT.parents[2]
NORMAL = wc.blobs(REPO, CONTRACT['normal_source'])
STOP = wc.stop_blobs(NORMAL)


class ContractTests(unittest.TestCase):
    def test_exact_normal_stop_pair(self):
        wc.validate_pair(NORMAL, STOP, CONTRACT)
        self.assertEqual(CONTRACT['clocks'], dict(live=180, live_host_total=200, original_byte_age=300, restore_trigger=170, source_abort=250, restore_reserve=50, service_ceiling=300, publisher_start_age=65, publisher_finish_age=35))
        self.assertIn('fdc358fc', supervisor.INPUT_IMAGE)
        self.assertEqual(supervisor.MODULES['epub_copies.py'], CONTRACT['modules']['epub_copies.py'])

    def test_unrelated_byte_image_strip_or_schedule_drift_refuses(self):
        cases = ((wc.PATHS[2], b'# unrelated comment\n'),
                 (wc.PATHS[3], b'v0.110.4'),
                 (wc.PATHS[0], b'strip'),
                 (wc.PATHS[1], b'schedule'))
        for path, marker in cases:
            altered = dict(STOP)
            altered[path] = marker + altered[path]
            with self.assertRaises(ValueError):
                wc.validate_pair(NORMAL, altered, CONTRACT)

    def test_typed_raw_inventory_and_omitted_child_defaults(self):
        raw = {'kind': 'JobList', 'apiVersion': 'batch/v1', 'metadata': {'resourceVersion': '123'}, 'items': [{'metadata': {'namespace': 'frontend', 'name': 'fresh', 'uid': 'uid'}}]}
        row, = wc.typed_inventory(raw, 'Job', 'batch/v1', 'frontend')
        self.assertEqual((row['kind'], row['apiVersion']), ('Job', 'batch/v1'))
        self.assertNotIn('kind', raw['items'][0])

    def test_invalid_inventory_never_normalized(self):
        good = {'kind': 'PodList', 'apiVersion': 'v1', 'metadata': {'resourceVersion': '123'}, 'items': [{'metadata': {'namespace': 'frontend', 'name': 'fresh', 'uid': 'uid'}}]}
        for mutation in ('list', 'api', 'rv', 'continued', 'remaining', 'wrongchild', 'wrongns', 'duplicate'):
            value = copy.deepcopy(good)
            if mutation == 'list': value['kind'] = 'List'
            if mutation == 'api': value['apiVersion'] = 'batch/v1'
            if mutation == 'rv': value['metadata'].pop('resourceVersion')
            if mutation == 'continued': value['metadata']['continue'] = 'more'
            if mutation == 'remaining': value['metadata']['remainingItemCount'] = 1
            if mutation == 'wrongchild': value['items'][0]['kind'] = 'Job'
            if mutation == 'wrongns': value['items'][0]['metadata']['namespace'] = 'media'
            if mutation == 'duplicate': value['items'] *= 2
            with self.assertRaises(ValueError): wc.typed_inventory(value, 'Pod', 'v1', 'frontend')

    def test_publisher_independent_minimum_and_earliest_expiry(self):
        stamp = lambda n: dt.datetime.fromtimestamp(n, dt.timezone.utc).isoformat()
        scope = dict(read_only=True, publisher_count=8, capture_started_at=stamp(100), captured_at=stamp(132))
        self.assertEqual(supervisor.publisher_writer_deadline(scope, 400, 50, 133), 165)
        self.assertEqual(supervisor.publisher_writer_deadline(scope, 200, 50, 133), 150)
        with self.assertRaises(supervisor.Refused): supervisor.publisher_writer_deadline(scope, 400, 50, 165)
        with self.assertRaises(supervisor.Refused): supervisor.publisher_writer_deadline(scope, 400, 50, 168)


class HeldProofTests(unittest.TestCase):
    def setUp(self):
        normal = 'a' * 40
        parent = {'metadata': {'uid': 'parent', 'namespace': 'flux-system', 'name': 'cluster-apps', 'resourceVersion': '100', 'generation': 1, 'annotations': {'reconcile.fluxcd.io/requestedAt': 'old'}}, 'spec': {'suspend': False}, 'status': {'observedGeneration': 1, 'lastHandledReconcileAt': 'new', 'lastAppliedRevision': 'main@sha1:' + normal, 'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        after = copy.deepcopy(parent); after['metadata']['annotations']['reconcile.fluxcd.io/requestedAt'] = 'new'; after['metadata']['resourceVersion'] = '101'
        self.actual = {key: {'metadata': {'namespace': key[0], 'name': key[1], 'uid': key[1] + '-uid', 'resourceVersion': '123'}, 'spec': {'suspend': True}, 'status': {'lastAppliedRevision': 'main@sha1:' + normal}} for key in wc.SCOPES}
        self.proof = {'schema': 1, 'workloads_normal': True, 'checked_at': '1970-01-01T00:01:40+00:00', 'normal_main_sha': normal, 'parent_before': parent, 'parent_after': after, 'holds_before': list(copy.deepcopy(self.actual).values()), 'holds_after': list(copy.deepcopy(self.actual).values())}

    def test_completed_parent_reconcile_and_all_actual_holds(self):
        wc.validate_holds(self.proof, self.actual, 101)

    def test_parent_failure_or_reset_replaced_uid_and_expiry(self):
        for case in ('pending', 'failed', 'reset', 'replaced', 'future', 'expired'):
            proof, actual = copy.deepcopy(self.proof), copy.deepcopy(self.actual)
            now = 101
            if case == 'pending': proof['parent_after']['status']['lastHandledReconcileAt'] = 'old'
            if case == 'failed': proof['parent_after']['status']['conditions'][0]['status'] = 'False'
            if case == 'reset': actual[wc.SCOPES[0]]['spec']['suspend'] = False
            if case == 'replaced': actual[wc.SCOPES[0]]['metadata']['uid'] = 'replacement'
            if case == 'future': now = 99
            if case == 'expired': now = 700
            with self.assertRaises(ValueError): wc.validate_holds(proof, actual, now)

    def test_native_normal_rejects_stale_generation_terminating_pod_or_wrong_owner(self):
        d={'metadata':{'uid':'deployment','generation':2},'spec':{'replicas':1,'selector':{'matchLabels':{'app':'fixture'}},'template':{'spec':{'containers':[{'name':'app','image':'frozen'}]}}},'status':dict(observedGeneration=2,updatedReplicas=1,readyReplicas=1,availableReplicas=1)}
        rs={'metadata':{'uid':'rs','ownerReferences':[dict(controller=True,kind='Deployment',name='fixture',uid='deployment')]}}
        pod={'metadata':{'namespace':'media','name':'fixture-pod','uid':'pod','labels':{'app':'fixture'},'ownerReferences':[dict(controller=True,kind='ReplicaSet',name='fixture-rs',uid='rs')]},'spec':{'containers':[{'name':'app','image':'frozen'}]},'status':{'phase':'Running','containerStatuses':[dict(name='app',ready=True,state={'running':{}})]}}
        for case in ('pass','generation','terminating','owner','image','unready'):
            deployment,replicaset,native=copy.deepcopy(d),copy.deepcopy(rs),copy.deepcopy(pod)
            if case=='generation':deployment['status']['observedGeneration']=1
            if case=='terminating':native['metadata']['deletionTimestamp']='now'
            if case=='owner':replicaset['metadata']['ownerReferences'][0]['uid']='replacement'
            if case=='image':native['spec']['containers'][0]['image']='changed'
            if case=='unready':native['status']['containerStatuses'][0]['ready']=False
            w=object.__new__(watch.Watchdog)
            w.kube=lambda kind,*args:deployment if kind=='deployment' else replicaset
            w.inventory=lambda *args:[native]
            self.assertEqual(w.deployment_normal('fixture','media',1),case=='pass')


class FakeWatch(watch.Watchdog):
    def __init__(self, root, *, pause='OPEN', inverse='OPEN', window=False, gates=True, normal=True):
        self.args = types.SimpleNamespace(pause='1', restore='2', include_kavita=True, deadline=170, arm_deadline=600, merge_footer='fixture-only', retarget_script='fixture-only', restore_worktree='fixture-only', pause_head='a'*40, restore_branch='agent/fixture')
        self.state = dict(armed_at='1970-01-01T00:00:00+00:00', recover_ks=True, complete=False, inverse_retargeted=True)
        self.state_path = root/'state.json'; self.stop = root/'stop'; self.calls=[]
        self.pause_state, self.inverse_state, self.gates, self.normal = pause, inverse, gates, normal
        self.phase = {'first_service_stop_observed_at':'1970-01-01T00:01:40+00:00'} if window else {}
        self.scopes = list(wc.SCOPES)
        self.save()

    def save(self): self.state_path.write_text(json.dumps(self.state))
    def note(self, message): self.calls.append(('note', message))
    def current_main(self): return 'a'*40
    def restored_main(self, merge_sha): return self.current_main()
    def desired_restored(self, sha): self.calls.append(('normal-git', sha))
    def phase_checkpoint(self): return self.phase
    def runtime_workloads_normal(self): self.calls.append(('native-normal', self.normal)); return self.normal
    def runtime_restored(self, sha): self.calls.append(('terminal-normal', sha)); return self.normal
    def source(self, sha): self.calls.append(('source-exact', sha))
    def cleanup_phase_jobs(self): self.calls.append(('owned-uid-pg-absent',))
    def verify_inverse(self, restore): self.calls.append(('inverse-exact',)); self.state['expected_inverse_head']='b'*40
    def guard_phase_git(self): self.calls.append(('git-phase-exact',))
    def clean_gates(self, restore): return self.gates
    def run(self, argv, **kw): self.calls.append(tuple(argv)); return ''
    def view(self, number): return {'state':self.pause_state if number=='1' else self.inverse_state, 'mergedAt':'1970-01-01T00:00:01+00:00', 'baseRefName':'main', 'mergeCommit':{'oid':'a'*40}}


class WatchRecoveryTests(unittest.TestCase):
    def test_prestage_cancellation_checks_normal_before_any_release(self):
        with tempfile.TemporaryDirectory() as d:
            w=FakeWatch(Path(d)); w.stop.touch(); self.assertTrue(w.tick())
            normal=w.calls.index(('native-normal', True))
            resumes=[i for i,c in enumerate(w.calls) if c[:2]==('flux','resume')]
            self.assertEqual(len(resumes),4); self.assertTrue(all(i>normal for i in resumes))
            self.assertTrue(w.state['complete']); self.assertFalse(w.state['recover_ks'])

    def test_missing_prestage_normal_or_missing_inverse_gates_keeps_holds(self):
        with tempfile.TemporaryDirectory() as d:
            w=FakeWatch(Path(d), normal=False); w.stop.touch()
            with self.assertRaises(RuntimeError): w.tick()
            self.assertFalse(any(c[:2]==('flux','resume') for c in w.calls)); self.assertFalse(w.state['complete'])
        with tempfile.TemporaryDirectory() as d:
            w=FakeWatch(Path(d), pause='MERGED', gates=False); w.stop.touch(); self.assertFalse(w.tick())
            self.assertFalse(any(c[:2]==('flux','resume') or c[:3]==('gh','pr','merge') for c in w.calls))

    def test_poststop_cleanup_source_resume_then_terminal_proof(self):
        with tempfile.TemporaryDirectory() as d:
            w=FakeWatch(Path(d), pause='MERGED', inverse='MERGED', window=True)
            self.assertTrue(w.tick())
            for i,c in enumerate(w.calls):
                if c[:2]==('flux','resume'):
                    self.assertEqual(w.calls[i-1],('owned-uid-pg-absent',)); self.assertEqual(w.calls[i-2][0],'source-exact')
            self.assertEqual(w.calls[-2][0],'terminal-normal')

    def test_actual_stop_170_trigger_and_prestage_600_are_distinct(self):
        with tempfile.TemporaryDirectory() as d:
            w=FakeWatch(Path(d), pause='MERGED', window=True)
            with patch.object(watch,'instant',return_value=dt.datetime.fromtimestamp(269,dt.timezone.utc)): self.assertFalse(w.tick())
            self.assertFalse(any(c[:3]==('gh','pr','merge') for c in w.calls))
            with patch.object(watch,'instant',return_value=dt.datetime.fromtimestamp(270,dt.timezone.utc)): self.assertFalse(w.tick())
            self.assertTrue(any(c[:3]==('gh','pr','merge') for c in w.calls))
        with tempfile.TemporaryDirectory() as d:
            w=FakeWatch(Path(d), pause='MERGED', window=False)
            with patch.object(watch,'instant',return_value=dt.datetime.fromtimestamp(599,dt.timezone.utc)): self.assertFalse(w.tick())
            self.assertFalse(any(c[:3]==('gh','pr','merge') for c in w.calls))

    def test_source_or_cleanup_failure_never_releases_hold(self):
        for name in ('desired_restored','cleanup_phase_jobs','source'):
            with tempfile.TemporaryDirectory() as d:
                w=FakeWatch(Path(d), pause='MERGED', inverse='MERGED', window=True)
                with patch.object(w,name,side_effect=RuntimeError('fixture refusal')):
                    with self.assertRaises(RuntimeError): w.tick()
                self.assertFalse(any(c[:2]==('flux','resume') for c in w.calls)); self.assertFalse(w.state['complete'])


class GitDriftWatchTests(unittest.TestCase):
    def test_completed_recovery_uses_current_main_preserving_unrelated_commit(self):
        with tempfile.TemporaryDirectory() as d:
            w=FakeWatch(Path(d),pause='MERGED',inverse='MERGED',window=True)
            w.args.repo_dir='private-local-fixture';w.state.update(complete=True,expected_restored_sha='a'*40)
            w.current_main=lambda:'c'*40
            w.restored_main=types.MethodType(watch.Watchdog.restored_main,w)
            self.assertTrue(w.tick())
            self.assertIn(('git','-C','private-local-fixture','merge-base','--is-ancestor','a'*40,'c'*40),w.calls)
            self.assertIn(('source-exact','c'*40),w.calls)
            self.assertNotIn(('source-exact','a'*40),w.calls)

    def test_unrelated_main_preserves_authority_but_phase_drift_revokes_cold(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);w=FakeWatch(root,pause='MERGED',window=True)
            w.args.repo_dir='private-local-fixture';w.contract=CONTRACT
            observed=dict(STOP)
            with patch.object(wc,'blobs',return_value=observed):
                watch.Watchdog.guard_phase_git(w)
            self.assertNotIn('copy_authority_revoked_at',w.state)
            observed[wc.PATHS[3]]=observed[wc.PATHS[3]].replace(b'v0.110.5',b'v0.110.6')
            with patch.object(wc,'blobs',return_value=observed):
                with self.assertRaisesRegex(RuntimeError,'COPY revoked'):
                    watch.Watchdog.guard_phase_git(w)
            self.assertTrue(w.stop.exists());first=w.state['copy_authority_revoked_at']
            self.assertIn(('owned-uid-pg-absent',),w.calls)
            self.assertFalse(any(c[:2]==('flux','resume') or c[:3]==('gh','pr','merge') for c in w.calls))
            # Reload the actual durable state as a replacement owner would.
            w.state=json.loads(w.state_path.read_bytes());w.calls=[]
            with patch.object(wc,'blobs',return_value=observed),patch.object(watch,'stamp',return_value='later'):
                with self.assertRaises(RuntimeError):watch.Watchdog.guard_phase_git(w)
            self.assertEqual(w.state['copy_authority_revoked_at'],first)
            self.assertTrue(w.state['recover_ks']);self.assertFalse(w.state['complete'])
            self.assertIn(('owned-uid-pg-absent',),w.calls)
            self.assertFalse(any(c[:2]==('flux','resume') for c in w.calls))


class GitReplayTests(unittest.TestCase):
    def test_actual_copy_aware_script_squash_replay_and_idempotent_second_call(self):
        self.replay_case('plain')

    def test_concurrent_unrelated_main_commit_is_preserved(self):
        self.replay_case('unrelated')

    def test_concurrent_phase_image_change_refuses_without_remote_rewrite(self):
        self.replay_case('image')

    def replay_case(self, drift):
        task_root=Path.home()/'work'; task_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=task_root,prefix='hn-window-finite-') as d:
            root=Path(d); repo=root/'repo'; remote=root/'remote'; repo.mkdir()
            def run(*args, cwd=repo): return subprocess.check_output(list(args),cwd=cwd,text=True,stderr=subprocess.DEVNULL,timeout=10).strip()
            run('git','init','-b','main'); run('git','config','user.email','finite@example.invalid'); run('git','config','user.name','Finite Fixture')
            for path,raw in NORMAL.items():
                target=repo/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(raw)
            run('git','add','.');run('git','commit','-m','normal')
            run('git','init','--bare',str(remote));run('git','remote','add','origin',str(remote));run('git','push','origin','main')
            run('git','checkout','-b','agent/fixture-stop')
            for path,raw in STOP.items(): (repo/path).write_bytes(raw)
            run('git','commit','-am','stop'); stophead=run('git','rev-parse','HEAD')
            run('git','checkout','-b','agent/fixture-inverse')
            for path,raw in NORMAL.items(): (repo/path).write_bytes(raw)
            run('git','commit','-am','inverse');run('git','push','origin','agent/fixture-inverse')
            run('git','checkout','main');run('git','merge','--squash','agent/fixture-stop');run('git','commit','-m','squashed stop');run('git','push','origin','main')
            if drift == 'unrelated':
                (repo/'unrelated-main.txt').write_text('preserve another agent commit\n')
                run('git','add','.');run('git','commit','-m','unrelated main');run('git','push','origin','main')
            elif drift == 'image':
                target=repo/wc.PATHS[3]
                target.write_bytes(target.read_bytes().replace(b'v0.110.5',b'v0.110.6'))
                run('git','commit','-am','independent image upgrade');run('git','push','origin','main')
            original_remote=run('git','rev-parse','refs/remotes/origin/agent/fixture-inverse')
            original_main=run('git','rev-parse','HEAD')
            run('git','checkout','agent/fixture-inverse')
            bin_dir=root/'bin';bin_dir.mkdir(); state=root/'base';state.write_text('agent/fixture-stop')
            fake=bin_dir/'gh'; fake.write_text('''#!/usr/bin/env python3
import json,os,subprocess,sys
from pathlib import Path
r=os.environ['FIXTURE_REPO']; state=Path(os.environ['FIXTURE_BASE']); a=sys.argv[1:]
def git(*v):return subprocess.check_output(['git','-C',r,*v],text=True).strip()
if a[:2]==['pr','edit']:state.write_text('main');sys.exit(0)
if a[:2]==['pr','diff']:print(git('diff','origin/main...HEAD'));sys.exit(0)
if a[:2]!=['pr','view']:sys.exit(2)
if a[2]=='1':x={'state':'MERGED','headRefOid':os.environ['FIXTURE_STOP'],'headRefName':'agent/fixture-stop'}
else:
 rows=git('diff','--numstat','origin/main...HEAD').splitlines();files=[{'path':p,'additions':int(n),'deletions':int(m)} for n,m,p in (s.split(chr(9),2) for s in rows)]
 x={'state':'OPEN','baseRefName':state.read_text(),'headRefOid':git('rev-parse','HEAD'),'additions':sum(f['additions'] for f in files),'deletions':sum(f['deletions'] for f in files),'changedFiles':len(files),'files':files}
print(x[a[a.index('--jq')+1][1:]] if '--jq' in a else json.dumps(x))
''');fake.chmod(0o700)
            env=dict(os.environ,PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],GH_TOKEN='public-finite-dummy',FIXTURE_REPO=str(repo),FIXTURE_BASE=str(state),FIXTURE_STOP=stophead)
            argv=['bash',str(ROOT/'retarget-restore.sh'),str(repo),'1','2',stophead,'agent/fixture-inverse','copy']
            result=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=25)
            if drift == 'image':
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(run('git','--git-dir',str(remote),'rev-parse','refs/heads/agent/fixture-inverse'),original_remote)
                self.assertEqual(run('git','--git-dir',str(remote),'rev-parse','refs/heads/main'),original_main)
                self.assertEqual(state.read_text(),'agent/fixture-stop')
                self.assertIn(b'v0.110.6',wc.blobs(repo,'origin/main')[wc.PATHS[3]])
                return
            self.assertEqual(result.returncode,0,result.stderr)
            if drift == 'unrelated':
                self.assertEqual(run('git','show','HEAD:unrelated-main.txt'),'preserve another agent commit')
            head=run('git','rev-parse','HEAD'); main=run('git','rev-parse','origin/main')
            wc.verify_git_pair(repo,main,head,CONTRACT)
            self.assertEqual(state.read_text(),'main')
            self.assertIn('validate restore against main',run('git','log','-1','--format=%s'))
            subprocess.run(argv,env=env,check=True,capture_output=True,text=True,timeout=10)
            self.assertEqual(head,run('git','rev-parse','HEAD'))
            if drift == 'unrelated':
                run('git','checkout','main');run('git','merge','--squash','agent/fixture-inverse');run('git','commit','-m','squashed inverse')
                restored=run('git','rev-parse','HEAD')
                (repo/'after-recovery.txt').write_text('preserve concurrent post-inverse main\n')
                run('git','add','.');run('git','commit','-m','post-inverse unrelated main');run('git','push','origin','main')
                current=run('git','rev-parse','HEAD')
                w=object.__new__(watch.Watchdog);w.args=types.SimpleNamespace(repo_dir=str(repo))
                w.run=lambda argv,**_:subprocess.check_output(argv,text=True,stderr=subprocess.DEVNULL,timeout=10)
                w.desired_restored=lambda sha:self.assertEqual(wc.blobs(repo,sha),NORMAL)
                self.assertEqual(w.restored_main(restored),current)
                self.assertEqual(run('git','show',current+':unrelated-main.txt'),'preserve another agent commit')
                self.assertEqual(run('git','show',current+':after-recovery.txt'),'preserve concurrent post-inverse main')


class OwnerOutcomeTests(unittest.TestCase):
    def test_exact_main_receipt_request_and_source_event_response_binding(self):
        self.outcome_case('pass')

    def test_response_source_event_hash_mismatch_refuses(self):
        self.outcome_case('hash')

    def test_response_module_or_main_receipt_drift_refuses(self):
        self.outcome_case('module')
        self.outcome_case('receipt')

    def outcome_case(self, case):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);phase='a'*32;key=('frontend','source')
            w=object.__new__(supervisor.Supervisor);w.out=root;w.deadline=time.time()+100
            script=root/'exchange.py';script.write_text('# public fake child transport only\n');script.chmod(0o600)
            completed=root/'main.json';supervisor.private_json(completed,{'job_uid':'main','pod_uid':'main-pod','zero_exit':True})
            library=root/'library.json';supervisor.private_json(library,{'schema':2})
            selection=root/'selection.json';supervisor.private_json(selection,{'entries':[]})
            assembly=root/'assembly.json';supervisor.private_json(assembly,{'proof_files':{'selection.json':{'path':str(selection)},'snapshot.json':{'sha256':'b'*64}}})
            w.status={'phase_token':phase,'selected_count':2,'main_completion_receipt':{'path':str(completed),'sha256':wc.sha(completed.read_bytes())},'library':{'path':str(library)},'assembly_receipt':str(assembly)}
            w.c={'outcome_script':str(script),'outcome_script_sha256':wc.sha(script.read_bytes())}
            w.checkpoint=types.SimpleNamespace(SOURCE=key)
            w.row=lambda _:dict(uid='source-job',ready_manifest={'spec':{'template':{'spec':{'containers':[{'name':'source'}]}}}})
            w.actual_pod=lambda _: {'metadata':{'name':'source-pod','uid':'source-pod-uid'}}
            w.save=lambda:None;w.guard_lease=lambda:None;w.service_fence=lambda:None
            def transport(argv, timeout, output_path=None, input_bytes=None):
                request=json.loads(input_bytes)
                self.assertEqual(input_bytes,json.dumps(request,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()+b'\n')
                self.assertEqual(request['main_receipt_sha256'],wc.sha(completed.read_bytes()))
                self.assertEqual(request['runtime_module_sha256'],{k:CONTRACT['modules'][k] for k in ('epub_copies.py','epub_metadata.py','book_copy_writer.py','bound_census.py')})
                self.assertEqual(request['selected_scope_sha256'],'1754edf94c3735c5c7cf6a78d30e3bea3b110e7b48a77ef1fea6e16e91c82663')
                self.assertLessEqual(timeout,12);self.assertEqual(float(argv[-1]),request['deadline_epoch'])
                final={'schema':2,'read_only':True,'production_writes':0,'phase_token':phase,'job_uid':'source-job','pod_uid':'source-pod-uid','actual_moved_count':2,'every_unapproved_file_unchanged':True}
                response={k:request[k] for k in ('phase_token','job_uid','pod_uid','runtime_module_sha256','selected_scope_sha256','main_receipt_sha256')}
                response.update(schema=1,type='copy_outcome_response',request_sha256=wc.sha(input_bytes),outcome=final,production_writes=0)
                if case=='module':response['runtime_module_sha256']={}
                if case=='receipt':response['main_receipt_sha256']='c'*64
                raw=json.dumps(response,sort_keys=True,separators=(',',':')).encode()+b'\n'
                output_path.write_bytes(raw);output_path.chmod(0o600)
                w.outcome_ready={'response_sha256':wc.sha(raw) if case!='hash' else 'd'*64}
                return str(output_path)
            w.run_monitored=transport
            if case=='pass':
                w.verify_outcome([]);self.assertTrue((root/'actual-copy-outcome.json').exists())
            else:
                with self.assertRaises(supervisor.Refused):w.verify_outcome([])
                self.assertFalse((root/'actual-copy-outcome.json').exists())

    def test_monitored_private_stdin_uses_finite_fd_transport(self):
        with tempfile.TemporaryDirectory() as d:
            w=object.__new__(supervisor.Supervisor);w.out=Path(d)
            w.guard_lease=lambda *_:None;w.save=lambda:None
            raw=b'public finite stdin\n';path=w.run_monitored([sys.executable,'-B','-c','import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'],2,require_lock=False,input_bytes=raw)
            self.assertEqual(Path(path).read_bytes(),raw)


class NativeCleanupTests(unittest.TestCase):
    def fixture(self, root):
        w=FakeWatch(root)
        token='a'*32
        source=('frontend','issue831-copy-source-census-1009-03'); main=('frontend','issue831-copy-selected-1009-03')
        identities=[source,main,('downloads','issue831-ll-source-1009-03'),('media','issue831-kavita-source-1009-03'),('media','issue831-lidarr-source-1009-03')]
        uid='11111111-1111-1111-1111-111111111111'; poduid='22222222-2222-2222-2222-222222222222'
        phase={'schema':1,'phase_token':token,'owned_jobs':[dict(namespace=ns,name=name,uid=uid if (ns,name)==main else None,phase_token=token,writer=(ns,name)==main) for ns,name in identities], 'pg_leases':[]}
        for key in (source,main):
            phase['pg_leases'].append(dict(job_namespace=key[0],job_name=key[1],job_uid=uid if key==main else None,pod_uid=poduid if key==main else None,backend_pid=123 if key==main else None,application_name='issue831-manual-copy-writer' if key==main else 'issue825-duplicate-share-fence-'+token))
        watch.Watchdog.validate_copy_phase(phase)
        w.phase=phase
        job={'metadata':dict(namespace='frontend',name=main[1],uid=uid,labels={'issue825.haynesnetwork/phase':token})}
        pod={'metadata':dict(namespace='frontend',name=main[1]+'-pod',uid=poduid,labels={'issue825.haynesnetwork/phase':token},ownerReferences=[dict(kind='Job',name=main[1],uid=uid)])}
        resources={'Job':[job],'Pod':[pod]}
        def run(argv, **kw):
            w.calls.append(tuple(argv))
            if argv[:3]==['kubectl','get','--raw']:
                kind='Job' if '/apis/batch/' in argv[3] else 'Pod'; ns=argv[3].split('/namespaces/')[1].split('/')[0]
                return json.dumps({'kind':kind+'List','apiVersion':'batch/v1' if kind=='Job' else 'v1','metadata':{'resourceVersion':'123'},'items':[r for r in resources[kind] if r['metadata']['namespace']==ns]})
            if argv[:3]==['kubectl','delete','--raw']:
                options=json.loads(kw['input_text']); self.assertEqual(options['preconditions']['uid'],uid);self.assertEqual(options['propagationPolicy'],'Foreground')
                resources['Job']=[];resources['Pod']=[]
            return ''
        w.run=run
        w.inventory=types.MethodType(watch.Watchdog.inventory,w)
        return w,resources

    def test_actual_typed_inventory_uid_foreground_cleanup_and_union_absence(self):
        with tempfile.TemporaryDirectory() as d:
            w,resources=self.fixture(Path(d));watch.Watchdog.cleanup_phase_jobs(w)
            self.assertEqual(resources,{'Job':[],'Pod':[]})
            self.assertIn('copy_phase_cleanup_verified_at',w.state)
            self.assertTrue(any(c[:3]==('kubectl','delete','--raw') for c in w.calls))

    def test_reused_uid_or_orphan_unregistered_phase_refuses_before_release(self):
        for case in ('uid','unregistered-phase'):
            with tempfile.TemporaryDirectory() as d:
                w,resources=self.fixture(Path(d))
                if case=='uid':resources['Job'][0]['metadata']['uid']='33333333-3333-3333-3333-333333333333'
                else:
                    resources['Job']=[];resources['Pod']=[{'metadata':dict(namespace='frontend',name='unknown-phase-orphan',uid='orphan',labels={'issue825.haynesnetwork/phase':'a'*32})}]
                with self.assertRaises(RuntimeError):watch.Watchdog.cleanup_phase_jobs(w)
                self.assertNotIn('copy_phase_cleanup_verified_at',w.state)
                self.assertFalse(any(c[:3]==('kubectl','delete','--raw') for c in w.calls))


if __name__ == '__main__':
    unittest.main()
