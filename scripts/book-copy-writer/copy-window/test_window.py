"""Finite public fixtures, fake clocks/APIs and local Git only; no live actions."""
import copy
import datetime as dt
import hashlib
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
    def test_favorable_verdict_does_not_resolve_advisory_findings(self):
        self.assertTrue(wc.clean_advisory('Verdict: Looks good. No findings.'))
        for body in ('Verdict: Looks good', 'Verdict: Looks good\nMEDIUM: unsafe gate',
                     'No findings.\n[P2] unsafe gate', 'No findings. Needs changes'):
            self.assertFalse(wc.clean_advisory(body))
        stamp='1970-01-01T00:00:02+00:00'
        checks=[dict(name=name,status='COMPLETED',conclusion='SUCCESS',startedAt=stamp) for name in ('Flux Local - Success','Diff Scope - Success','Claude Review (advisory)')]
        pr=dict(statusCheckRollup=checks,commits=[dict(committedDate='1970-01-01T00:00:01+00:00')],comments=[dict(author={'login':'claude[bot]'},createdAt=stamp,updatedAt=stamp,body='Verdict: Looks good\nMEDIUM: unsafe gate')])
        self.assertFalse(watch.Watchdog.clean_gates(pr));self.assertFalse(supervisor.clean_gates(pr))
        pr['comments'][0]['body']='No findings.'
        self.assertTrue(watch.Watchdog.clean_gates(pr));self.assertTrue(supervisor.clean_gates(pr))

    def test_independent_disposition_binds_actual_failure_and_keeps_other_gates(self):
        stamp='1970-01-01T00:00:02+00:00'
        bindings=dict(source_commit='a'*40,stop_pr='3689',stop_head='b'*40,
                      inverse_pr='3690',inverse_head='c'*40,phase_token='d'*32)
        failed=dict(name='Claude Review (advisory)',status='COMPLETED',conclusion='FAILURE',
                    detailsUrl='https://github.com/example/actions/runs/1',startedAt=stamp,completedAt=stamp)
        pr=dict(headRefOid=bindings['inverse_head'],commits=[dict(committedDate='1970-01-01T00:00:01+00:00')],comments=[],
                statusCheckRollup=[dict(name=name,status='COMPLETED',conclusion='SUCCESS') for name in ('Flux Local - Success','Diff Scope - Success')]+[failed])
        disposition=dict(schema=1,prepared_only=False,explicitRootApproval=True,bindings=bindings,
                         failure_class='startup_before_review',advisory=failed)
        for gate in (supervisor.clean_gates,watch.Watchdog.clean_gates):
            self.assertFalse(gate(pr))
            self.assertTrue(gate(pr,disposition,bindings))
            for case in ('pending','unknown','stale','head','phase','other_failure','finding','duplicate'):
                current,approved,expected=copy.deepcopy(pr),copy.deepcopy(disposition),copy.deepcopy(bindings)
                if case=='pending':current['statusCheckRollup'][-1]['status']='IN_PROGRESS'
                if case=='unknown':approved['failure_class']='unknown'
                if case=='stale':current['commits'][-1]['committedDate']='1970-01-01T00:00:03+00:00'
                if case=='head':current['headRefOid']='e'*40
                if case=='phase':expected['phase_token']='e'*32
                if case=='other_failure':current['statusCheckRollup'].append(dict(name='another check',status='COMPLETED',conclusion='FAILURE'))
                if case=='finding':current['comments']=[dict(author={'login':'claude'},createdAt=stamp,body='MEDIUM: unresolved finding')]
                if case=='duplicate':current['statusCheckRollup'].append(copy.deepcopy(failed))
                self.assertFalse(gate(current,approved,expected),case)

    def test_private_review_requires_root_independence_and_exact_source(self):
        bindings=dict(source_commit='a'*40,stop_pr='3689',stop_head='b'*40,
                      inverse_pr='3690',inverse_head='c'*40,phase_token='d'*32)
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            def save(name,value):
                raw=json.dumps(value,sort_keys=True).encode();p=directory/name;p.write_bytes(raw);p.chmod(0o600)
                return dict(path=str(p),sha256=hashlib.sha256(raw).hexdigest())
            review=dict(schema=1,bindings=bindings,provider='codex',decision='PASS',unresolved_findings=[],
                        prepared_by='preparer',reviewed_by='independent reviewer',failure_class='startup_before_review',
                        advisory={},source_files={name:wc.sha((ROOT/name).read_bytes()) for name in wc.REVIEW_SOURCES})
            disposition=dict(schema=1,prepared_only=False,explicitRootApproval=True,bindings=bindings,
                             failure_class='startup_before_review',advisory={},independent_review=save('review.json',review))
            def git_source(repo,*argv):
                self.assertEqual(argv[0],'show')
                self.assertTrue(argv[1].startswith(bindings['source_commit']+':'))
                return (ROOT/argv[1].split('/')[-1]).read_bytes()
            with patch.object(wc,'git',side_effect=git_source):
                entry=save('disposition.json',disposition)
                self.assertEqual(wc.load_review_disposition(entry,bindings,REPO,watch.cache.read_private),disposition)
                for case in ('closed','unapproved','same_reviewer','finding','source','operation','review_drift','commit_drift'):
                    changed,peer=copy.deepcopy(disposition),copy.deepcopy(review)
                    if case=='closed':changed['prepared_only']=True
                    if case=='unapproved':changed['explicitRootApproval']=False
                    if case=='same_reviewer':peer['reviewed_by']=peer['prepared_by']
                    if case=='finding':peer['unresolved_findings']=['unresolved']
                    if case=='source':peer['source_files']['cached_source.py']='0'*64
                    if case=='operation':peer['bindings']['inverse_head']='e'*40
                    changed['independent_review']=save('review.json',peer)
                    candidate=save('disposition.json',changed)
                    if case=='review_drift':(directory/'review.json').write_text('{}')
                    with self.assertRaises(ValueError,msg=case):
                        if case=='commit_drift':
                            with patch.object(wc,'git',return_value=b'changed'):
                                wc.load_review_disposition(candidate,bindings,REPO,watch.cache.read_private)
                        else:wc.load_review_disposition(candidate,bindings,REPO,watch.cache.read_private)

    def test_byte_activation_age_boundary_and_delayed_arm_refusal(self):
        def stamp(at):
            return supervisor.dt.datetime.fromtimestamp(at, supervisor.dt.timezone.utc).isoformat()
        baseline=dict(schema=1,kind='live_byte_baseline',complete=True,capture_started_at=stamp(0),completed_at=stamp(240))
        supervisor.byte_activation_admission(baseline,300)
        for at,value in ((300.001,baseline),(239,baseline),(300,dict(baseline,complete=False)),(300,dict(baseline,completed_at=stamp(-1)))):
            with self.assertRaises(supervisor.Refused):supervisor.byte_activation_admission(value,at)
        for elapsed in (300,300.001):
            with self.subTest(elapsed=elapsed), tempfile.TemporaryDirectory() as directory:
                clock={'at':260};activation=Path(directory)/'activation'
                def artifact(name,value):
                    path=Path(directory)/name;raw=json.dumps(value).encode();path.write_bytes(raw);path.chmod(0o600)
                    return dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest())
                phase='a'*32;merge='b'*40;held={'before':{'metadata':{'uid':'owned'},'spec':{'owned':'normal'}}}
                proof=dict(normal_main_sha=merge,holds={ns+'/'+name:held for ns,name in supervisor.SCOPES},parents={name:held for name in supervisor.cache.PARENTS})
                cached=artifact('cache',proof);contract=artifact('contract',{'manifests':{}})
                owners={ns+'/'+name:dict(uid='owned',spec={'owned':'normal'},phase_token=phase)
                        for ns,name in supervisor.SCOPES+[('flux-system',name) for name in supervisor.cache.PARENTS]}
                watch=dict(armed_ready=True,complete=False,recover_ks=True,restore_pr='1',scopes=supervisor.SCOPES,
                           cached_source_receipt_sha256=cached['sha256'],cached_source_ready_at=stamp(0),
                           normal_inverse_merge_sha=merge,armed_at=stamp(0),cached_ks_owners=owners)
                watchfile=artifact('watch',watch)['path']
                x=supervisor.Supervisor.__new__(supervisor.Supervisor)
                x.c=dict(watchdog_state=watchfile,watchdog_stop=str(Path(directory)/'stop'),restore_pr='1',
                         cached_source_receipt=cached,cached_source_activation=str(activation),manifest_contract=contract,
                         live_byte_baseline=artifact('baseline',baseline),arm_deadline_seconds=1800,repo_dir='fake')
                x.status=dict(phase_token=phase,normal_inverse_merge_sha=merge);x.save=lambda:None;x.inverse_green=lambda:None
                x.get=lambda kind,name,ns: ({'spec':{'replicas':1,'selector':{'matchLabels':{'app':'live'}}},'status':{'readyReplicas':1}}
                    if kind=='deployment' else {'metadata':{'uid':'pvc'}} if kind=='pvc' else {'spec':{'suspend':True}})
                x.list=lambda *_:[{'metadata':{'labels':{'app':'live'}},'spec':{'nodeName':'fake'}}]
                def delayed_check(*_,**__):clock['at']=elapsed
                with patch.object(supervisor.time,'time',side_effect=lambda:clock['at']), \
                     patch.object(supervisor,'now',side_effect=lambda:stamp(clock['at'])), \
                     patch.object(supervisor.cache,'check_live',side_effect=delayed_check), \
                     patch.object(supervisor.window,'blobs',return_value={}),patch.object(supervisor.window,'PATHS',[]):
                    if elapsed>300:
                        with self.assertRaisesRegex(supervisor.Refused,'original byte activation admission'):x.arm()
                        self.assertFalse(activation.exists());self.assertNotIn('actuation_budget_started_at',x.status)
                    else:
                        x.arm();actual=json.loads(activation.read_bytes())
                        self.assertEqual(actual['actuation_budget_started_at'],stamp(300))
                        self.assertEqual(baseline['capture_started_at'],stamp(0))

    def test_exact_normal_stop_pair(self):
        wc.validate_pair(NORMAL, STOP, CONTRACT)
        self.assertEqual(CONTRACT['clocks'], dict(live=240, live_host_total=260, original_byte_age=600, activation_admission_age=300, restore_trigger=170, source_abort=170, restore_reserve=130, service_ceiling=300, publisher_start_age=65, publisher_finish_age=35))
        self.assertEqual(supervisor.INPUT_IMAGE, CONTRACT['image'])
        self.assertEqual(CONTRACT['image'], 'ghcr.io/thaynes43/book-copy-writer@sha256:628e97b8dbcc83a4d7068484b516b21dde740c4dd130d54f3b030f1ee7a75601')
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
    def verify_recovery_absence(self): self.calls.append(('final-owned-uid-pg-absent',))
    def verify_inverse(self, restore): self.calls.append(('inverse-exact',)); self.state['expected_inverse_head']='b'*40
    def guard_phase_git(self,pause=None,restore=None): self.calls.append(('git-phase-exact',))
    def clean_gates(self, restore): return self.gates
    def run(self, argv, **kw): self.calls.append(tuple(argv)); return ''
    def view(self, number): return {'state':self.pause_state if number=='1' else self.inverse_state, 'mergedAt':'1970-01-01T00:00:01+00:00', 'baseRefName':'main', 'mergeCommit':{'oid':'a'*40}}


class WatchRecoveryTests(unittest.TestCase):
    def test_supervisor_staging_1800_preserves_fixed_service_and_artifact_gates(self):
        fields='repo_dir manifest_contract hold_receipt restore_pr restore_head watchdog_state watchdog_stop evidence_dir copy_phase_state copy_checkpoint_helper copy_checkpoint_helper_sha256 cached_source_receipt cached_source_activation readonly_capture_jobs copy_job arm_deadline_seconds max_service_absence_seconds restore_reserve_seconds publisher_guard publisher_guard_sha256 publisher_scope_sha256 publisher_scope_hook publisher_config publisher_config_sha256 assembly_script assembly_script_sha256 delivery_script delivery_script_sha256 outcome_script outcome_script_sha256 kavita_exporters_dir kavita_exporter_sha256 live_byte_baseline source_private_input selection_approval census_holds ll_sql_sha256'.split()
        config=dict.fromkeys(fields,None)
        config.update(arm_deadline_seconds=600,max_service_absence_seconds=300,
                      restore_reserve_seconds=130,restore_head='invalid-exact-head')
        for limit,cached in ((600,None),(1800,dict(path='fixture-cache',sha256='a'*64))):
            config.update(arm_deadline_seconds=limit,cached_source_receipt=cached)
            with self.assertRaisesRegex(supervisor.Refused,'exact inverse and scope hashes required'):
                supervisor.validate(config)
        for changed in (dict(arm_deadline_seconds=1801),dict(arm_deadline_seconds=0),
                        dict(arm_deadline_seconds=601,cached_source_receipt=None),
                        dict(arm_deadline_seconds=1800,cached_source_receipt=None),
                        dict(arm_deadline_seconds=1800,max_service_absence_seconds=301),
                        dict(arm_deadline_seconds=1800,restore_reserve_seconds=131)):
            with self.assertRaisesRegex(supervisor.Refused,'phase300/abort170/reserve130 are fixed'):
                supervisor.validate(dict(config,**changed))

    def test_cli_staging_default_600_and_explicit_1800_keep_restore_170(self):
        argv=['watch','--arm','--pause','1','--restore','2','--pause-head','a'*40,
              '--restore-worktree','fixture','--restore-branch','agent/fixture',
              '--include-kavita','--phase-state','fixture-phase','--state','fixture-state',
              '--stop','fixture-stop','--log','fixture-log','--merge-footer','fixture-footer']
        cached=['--cached-source-receipt','fixture-cache','--cached-source-activation','fixture-activation']
        for extra,expected in (([],600),(['--arm-deadline','600'],600),(['--arm-deadline','1800']+cached,1800)):
            with patch.object(sys,'argv',argv+extra),patch.object(watch,'Watchdog') as watchdog:
                watch.main()
                args=watchdog.call_args.args[0]
                self.assertEqual(args.arm_deadline,expected);self.assertEqual(args.deadline,170)
                watchdog.return_value.loop.assert_called_once_with()
        for extra in (['--arm-deadline','1801']+cached,['--arm-deadline','0'],['--arm-deadline','-1'],
                      ['--arm-deadline','601'],['--arm-deadline','1800'],
                      ['--arm-deadline','1800','--cached-source-receipt','fixture-cache'],
                      ['--arm-deadline','1800','--deadline','171']+cached):
            with patch.object(sys,'argv',argv+extra),patch.object(sys,'stderr'),patch.object(watch,'Watchdog') as watchdog:
                with self.assertRaises(SystemExit) as error:watch.main()
                self.assertEqual(error.exception.code,2);watchdog.assert_not_called()

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
    def raced_inverse(self,root):
        w=FakeWatch(root,pause='MERGED');w.cached=True;w.args.repo_dir='private-local-fixture'
        w.contract=CONTRACT;w.args.pause_head='a'*40;w.current_main=lambda:'c'*40
        pause=dict(state='MERGED',headRefOid='a'*40,mergeCommit={'oid':'a'*40},mergedAt='fixture')
        old=dict(state='OPEN',baseRefName='main',headRefOid='b'*40)
        fresh=dict(number=2,state='MERGED',baseRefName='main',headRefOid='b'*40,mergeCommit={'oid':'c'*40})
        w.view=lambda number:pause if number=='1' else old
        w.guard_phase_git=types.MethodType(watch.Watchdog.guard_phase_git,w)
        w.stop_actuated=lambda:False
        def run(argv,**kwargs):
            w.calls.append(tuple(argv))
            return json.dumps(fresh) if argv[:3]==['gh','pr','view'] else ''
        w.run=run
        return w,pause,old,fresh

    def test_stale_inverse_view_routes_verified_normal_merge_to_cached_stage(self):
        with tempfile.TemporaryDirectory() as d:
            w,pause,old,fresh=self.raced_inverse(Path(d));original=dict(w.state)
            w.tick_cached=lambda p,r:w.calls.append(('cached-stage',p,r)) or False
            with patch.object(wc,'blobs',side_effect=lambda repo,sha:STOP if sha=='a'*40 else NORMAL),patch.object(wc,'verify_git_pair') as pair:
                self.assertFalse(w.tick_body())
            pair.assert_called_once_with('private-local-fixture','a'*40,'b'*40,CONTRACT,'inverse')
            self.assertIn(('git','-C','private-local-fixture','merge-base','--is-ancestor','a'*40,'b'*40),w.calls)
            self.assertIn(('git','-C','private-local-fixture','merge-base','--is-ancestor','a'*40,'c'*40),w.calls)
            self.assertIn(('git','-C','private-local-fixture','merge-base','--is-ancestor','c'*40,'c'*40),w.calls)
            self.assertIn(('cached-stage',pause,fresh),w.calls)
            self.assertEqual({k:w.state[k] for k in original},original);self.assertEqual(w.state['pause_merged_at'],'fixture')
            self.assertNotIn('copy_authority_revoked_at',w.state);self.assertFalse(w.stop.exists())
            self.assertNotIn(('owned-uid-pg-absent',),w.calls)

    def test_raced_inverse_missing_identity_ancestry_or_normal_proof_still_revokes(self):
        for case in ('head','number','base','unmerged','merge','stop_head','ancestry','merged_blob','unexpected_main'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as d:
                w,pause,old,fresh=self.raced_inverse(Path(d));calls=w.run;merged=dict(NORMAL);current=dict(NORMAL)
                if case=='head':fresh['headRefOid']='d'*40
                if case=='number':fresh['number']=3
                if case=='base':fresh['baseRefName']='other'
                if case=='unmerged':fresh['state']='OPEN'
                if case=='merge':fresh['mergeCommit']=None
                if case=='stop_head':pause['headRefOid']='d'*40
                if case=='merged_blob':fresh['mergeCommit']['oid']='d'*40;merged[wc.PATHS[0]]+=b'\n'
                if case=='unexpected_main':current[wc.PATHS[0]]+=b'\n'
                def run(argv,**kwargs):
                    if case=='ancestry' and '--is-ancestor' in argv:raise RuntimeError('fixture lost ancestry')
                    return calls(argv,**kwargs)
                w.run=run
                w.tick_cached=lambda *args:self.fail('unproved inverse reached cached staging')
                def blobs(repo,sha):return STOP if sha=='a'*40 else merged if sha=='d'*40 else current
                with patch.object(wc,'blobs',side_effect=blobs),patch.object(wc,'verify_git_pair'):
                    with self.assertRaisesRegex(RuntimeError,'COPY revoked'):w.tick_body()
                self.assertTrue(w.stop.exists());self.assertTrue(w.state['recover_ks']);self.assertFalse(w.state['complete'])
                self.assertIn(('owned-uid-pg-absent',),w.calls)
                if case=='unexpected_main':self.assertFalse(any(c[:3]==('gh','pr','view') for c in w.calls))

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
            w=object.__new__(supervisor.Supervisor);w.out=root;w.deadline=time.time()+300
            script=root/'exchange.py';script.write_text('# public fake child transport only\n');script.chmod(0o600)
            completed=root/'main.json';supervisor.private_json(completed,{'job_uid':'main','pod_uid':'main-pod','zero_exit':True})
            library=root/'library.json';supervisor.private_json(library,{'schema':2})
            selection=root/'selection.json';supervisor.private_json(selection,{'entries':[]})
            assembly=root/'assembly.json';supervisor.private_json(assembly,{'proof_files':{'selection.json':{'path':str(selection)},'snapshot.json':{'sha256':'b'*64}}})
            w.status={'phase_token':phase,'selected_count':2,'main_completion_receipt':{'path':str(completed),'sha256':wc.sha(completed.read_bytes())},'library':{'path':str(library)},'assembly_receipt':str(assembly)}
            w.c={'outcome_script':str(script),'outcome_script_sha256':wc.sha(script.read_bytes()),'restore_reserve_seconds':130}
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
                self.assertLessEqual(request['deadline_epoch'],w.deadline-130)
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


class PublisherOverlapTests(unittest.TestCase):
    def fixture(self, root):
        w=object.__new__(supervisor.Supervisor);w.out=root;w.publisher_capture=None;w.lock_ready=True
        w.status={'phase_token':'a'*32,'first_service_stop_observed_at':'1970-01-01T00:01:40+00:00'};w.jobs=[];events=[];children=[]
        keys=[('downloads','issue831-ll-source-1009-03'),('media','issue831-kavita-source-1009-03')]
        state={'heartbeat':'initial','owned_jobs':[dict(key=list(k),uid=None) for k in keys],
               'pg_leases':[dict(backend_pid=42)]}
        script=root/'collector.py';script.write_text('def storage_inventory(*args):return {}\n')
        paths=[]
        for ns,name in keys:
            p=root/(name+'.json');p.write_text(json.dumps({'metadata':{'namespace':ns,'name':name}}));paths.append(str(p))
        w.c={'readonly_capture_jobs':paths,'publisher_scope_hook':{'script':str(script),'sha256':wc.sha(script.read_bytes())},
             'publisher_config':'public-fixture.json','publisher_scope_sha256':'b'*64}
        w.checkpoint=types.SimpleNamespace(validate_job=lambda job,phase:(job['metadata']['namespace'],job['metadata']['name']))
        w.ledger=lambda:(copy.deepcopy(state),'unused')
        w.service_fence=lambda:None;w.guard_lease=lambda *_:w.check_publisher_capture()
        w.save=lambda:state.update(heartbeat='current')
        w.bind_reader=lambda path:json.loads(Path(path).read_bytes())
        def create(job):
            key=[job['metadata']['namespace'],job['metadata']['name']]
            uid=key[1]+'-uid';next(r for r in state['owned_jobs'] if r['key']==key)['uid']=uid
            w.jobs.append(dict(namespace=key[0],name=key[1],uid=uid));events.append(('uid',tuple(key)))
        w.create_job=create;w.list=lambda _:[]
        def verify(*args):
            self.assertEqual(args[3],{r['uid'] for r in state['owned_jobs']})
            events.append(('proof',len(children)));return {}
        w.publishers=types.SimpleNamespace(verify=verify,scope_digest=lambda _:'b'*64)
        def collect(key):
            self.assertIsNotNone(w.publisher_capture);w.check_publisher_capture();events.append(('payload',key))
            if key==keys[-1]:children[-1].code=0
            return key
        w.capture_vendor=collect;w.wait_source_census=lambda:None
        w.retire_vendor_readers=lambda *args:events.append(('native_gc',))
        class Child:
            pid=4321
            def __init__(self,code):self.code=code;self.term_timeout=False;self.signals=[]
            def poll(self):return self.code
            def terminate(self):self.signals.append(supervisor.signal.SIGTERM)
            def kill(self):self.signals.append(supervisor.signal.SIGKILL)
            def wait(self,timeout):
                if self.term_timeout:self.term_timeout=False;raise subprocess.TimeoutExpired('public-fixture',timeout)
                events.append(('reaped',));self.code=0 if self.code is None else self.code;return self.code
        def spawn(argv,**kwargs):
            self.assertTrue(all(r['uid'] for r in state['owned_jobs']))
            self.assertFalse(kwargs['start_new_session']);self.assertEqual(kwargs['stdin'],subprocess.DEVNULL)
            destination=Path(argv[argv.index('--output')+1]);destination.write_text('{"publishers":[]}')
            child=Child(None if not children else 0);children.append(child);events.append(('started',len(children)));return child
        return w,state,events,children,spawn

    def test_uid_barrier_both_full_proofs_and_native_gc_order(self):
        with tempfile.TemporaryDirectory() as d:
            w,state,events,children,spawn=self.fixture(Path(d))
            with patch.object(supervisor.subprocess,'Popen',side_effect=spawn):
                ll,kv=w.collect_readonly_sources();w.capture_publishers()
            kinds=[r[0] for r in events]
            self.assertEqual(kinds,['uid','uid','started','payload','payload','reaped','proof','native_gc','started','reaped','proof'])
            self.assertEqual(len(children),2);self.assertIsNone(w.publisher_capture)
            self.assertTrue(all(r['uid'] for r in state['owned_jobs']))
            self.assertEqual(state['pg_leases'],[dict(backend_pid=42)])

    def test_nonheartbeat_uid_or_pg_ledger_change_refuses_and_reaps(self):
        for field in ('uid','backend_pid'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as d:
                w,state,events,children,spawn=self.fixture(Path(d))
                def collect(key):
                    if field=='uid':state['owned_jobs'][0]['uid']='foreign-uid'
                    else:state['pg_leases'][0]['backend_pid']=99
                    w.check_publisher_capture()
                w.capture_vendor=collect
                with patch.object(supervisor.subprocess,'Popen',side_effect=spawn),patch.object(supervisor.os,'killpg',side_effect=AssertionError('publisher must not signal an unowned group')):
                    with self.assertRaisesRegex(supervisor.Refused,'phase identity changed'):w.collect_readonly_sources()
                self.assertEqual(children[0].signals,[supervisor.signal.SIGTERM]);self.assertEqual(events[-1],('reaped',))
                self.assertFalse(any(r[0] in ('proof','native_gc') for r in events));self.assertIsNone(w.publisher_capture)

    def test_failed_expired_or_native_refused_child_is_reaped(self):
        for case in ('child_failed','expired','native_failed','term_timeout'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as d:
                w,state,events,children,spawn=self.fixture(Path(d))
                def collect(key):
                    if case=='native_failed':raise supervisor.Refused('stopped vendor source refused')
                    if case=='child_failed':children[-1].code=7
                    else:
                        w.publisher_capture['expires']=0
                        if case=='term_timeout':children[-1].term_timeout=True
                    w.check_publisher_capture()
                w.capture_vendor=collect
                with patch.object(supervisor.subprocess,'Popen',side_effect=spawn),patch.object(supervisor.os,'killpg',side_effect=AssertionError('publisher must not signal an unowned group')):
                    with self.assertRaises(supervisor.Refused):w.collect_readonly_sources()
                self.assertEqual(events[-1],('reaped',));self.assertIsNone(w.publisher_capture)
                self.assertFalse(any(r[0] in ('proof','native_gc') for r in events))
                expected=[] if case=='child_failed' else [supervisor.signal.SIGTERM]
                if case=='term_timeout':expected.append(supervisor.signal.SIGKILL)
                self.assertEqual(children[0].signals,expected)


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

    def test_empty_union_skips_duplicate_intent_reads_but_repeats_complete_pg_proof(self):
        with tempfile.TemporaryDirectory() as d:
            w,resources=self.fixture(Path(d));resources.update(Job=[],Pod=[])
            watch.Watchdog.cleanup_phase_jobs(w)
            self.assertEqual(sum(c[:3]==('kubectl','get','--raw') for c in w.calls),6)
            self.assertEqual(sum(c[:2]==('kubectl','exec') for c in w.calls),1)
            self.assertFalse(any(c[:2]==('kubectl','delete') for c in w.calls))
            w.calls.clear();watch.Watchdog.cleanup_phase_jobs(w)
            self.assertEqual(sum(c[:3]==('kubectl','get','--raw') for c in w.calls),6)
            self.assertEqual(sum(c[:2]==('kubectl','exec') for c in w.calls),1)

    def test_unknown_inventory_or_pg_never_enters_cleanup_fallback(self):
        for case in ('malformed','pg'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as d:
                w,resources=self.fixture(Path(d));resources.update(Job=[],Pod=[]);original=w.run
                def run(argv,**kw):
                    if case=='malformed' and argv[:3]==['kubectl','get','--raw']:return '{}'
                    if case=='pg' and argv[:2]==['kubectl','exec']:raise RuntimeError('PG proof unknown')
                    return original(argv,**kw)
                w.run=run
                with self.assertRaises((RuntimeError,ValueError)):watch.Watchdog.cleanup_phase_jobs(w)
                self.assertNotIn('copy_phase_cleanup_verified_at',w.state)
                self.assertFalse(any(c[:2]==('kubectl','delete') for c in w.calls))

    def test_late_phase_pod_blocks_next_release_or_final_completion(self):
        for stage in ('next-release','final'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as d:
                w,resources=self.fixture(Path(d));resources.update(Job=[],Pod=[])
                w.cleanup_phase_jobs=types.MethodType(watch.Watchdog.cleanup_phase_jobs,w)
                w.verify_recovery_absence=types.MethodType(watch.Watchdog.verify_recovery_absence,w)
                def late():
                    resources['Pod']=[{'metadata':dict(namespace='media',name='late-orphan',uid='late',labels={'issue825.haynesnetwork/phase':'a'*32})}]
                released=[];reconciled=[]
                def release(ns,name):
                    released.append((ns,name))
                    if stage=='next-release':late()
                def reconcile(ns,name,sha):
                    reconciled.append((ns,name))
                    if stage=='final' and len(reconciled)==len(w.scopes):late()
                w.release_ks=release;w.reconcile_ks=reconcile
                with self.assertRaisesRegex(watch.PhaseResourcesPresent,'Pod union'):w.recover_cluster('a'*40)
                self.assertEqual(len(released),1 if stage=='next-release' else len(w.scopes))
                self.assertFalse(w.state['complete'])
                self.assertFalse(any(c[0]=='terminal-normal' for c in w.calls))

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
