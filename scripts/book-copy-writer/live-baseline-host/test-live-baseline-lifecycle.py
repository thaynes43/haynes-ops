#!/usr/bin/env python3
"""Finite UNIT-only private-temp/native-object checks; zero API/PG/NFS operations."""
import copy,hashlib,importlib.util,io,json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).parent
sys.dont_write_bytecode=True
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
ack=load('ack',HERE/'receive-live-baseline-ack.py');host=load('host',HERE/'run-live-byte-baseline.py')
helper=load('checkpoint',HERE/'dependencies/checkpoint-copy-job.py')
class AckTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name);self.raw=b'{"unit_only":true}\n';self.base=self.path/'live-byte-baseline.json';self.base.write_bytes(self.raw);self.base.chmod(0o600)
  self.env={'COPY_BASELINE_DEADLINE_EPOCH':str(time.time()+60),'COPY_PHASE_TOKEN':'a'*32,'COPY_JOB_UID':'11111111-1111-4111-8111-111111111111','COPY_POD_UID':'22222222-2222-4222-8222-222222222222'}
  self.body={'schema':1,'type':'live_baseline_delivery_ack','phase_token':self.env['COPY_PHASE_TOKEN'],'job_uid':self.env['COPY_JOB_UID'],'pod_uid':self.env['COPY_POD_UID'],'baseline_sha256':hashlib.sha256(self.raw).hexdigest()}
 def receive(self,body=None):
  raw=json.dumps(body or self.body).encode()
  with patch.object(sys,'argv',['ack',str(time.time()+60)]):return ack.receive(io.BytesIO(raw),self.env,str(self.path))
 def test_matching_raw_bytes_publish_private_exact_ack(self):
  answer=self.receive();self.assertEqual(answer['baseline_sha256'],self.body['baseline_sha256']);p=self.path/ack.NAME;self.assertEqual(json.loads(p.read_bytes()),self.body);self.assertEqual(p.stat().st_mode&0o777,0o400)
 def test_wrong_owner_refuses_before_publication(self):
  b={**self.body,'job_uid':'33333333-3333-4333-8333-333333333333'}
  with self.assertRaises(ack.Refused):self.receive(b)
  self.assertFalse((self.path/ack.NAME).exists())
 def test_changed_baseline_bytes_refuse(self):
  self.base.write_bytes(self.raw+b' ')
  with self.assertRaises(ack.Refused):self.receive()
 def test_existing_ack_never_overwritten(self):
  p=self.path/ack.NAME;p.write_bytes(b'previous')
  with self.assertRaises(OSError):self.receive()
  self.assertEqual(p.read_bytes(),b'previous')
 def test_symlink_baseline_refuses(self):
  self.base.unlink();self.base.symlink_to(self.path/'elsewhere')
  with self.assertRaises(OSError):self.receive()
 def test_expired_deadline_is_not_extended(self):
  self.env['COPY_BASELINE_DEADLINE_EPOCH']=str(time.time()-1)
  with self.assertRaises(ack.Refused):self.receive()
 def test_duplicate_field_and_oversized_input_refuse(self):
  with patch.object(sys,'argv',['ack',str(time.time()+60)]):
   with self.assertRaises(ack.Refused):ack.receive(io.BytesIO(b'{"schema":1,"schema":1}'),self.env,str(self.path))
   with self.assertRaises(ack.Refused):ack.receive(io.BytesIO(b'x'*4097),self.env,str(self.path))
 def test_hardlinked_baseline_refuses(self):
  os.link(self.base,self.path/'second')
  with self.assertRaises(ack.Refused):self.receive()
class HostTests(unittest.TestCase):
 def setUp(self):
  fixture=json.loads((HERE/'synthetic-baseline-fixture.json').read_bytes());self.b=fixture['baseline'];self.native={'schema':1,'phase_token':'a'*32,'observed_at':'2026-10-09T01:07:06+00:00','module_sha256':host.MODULES,'source_binding':{k:v for k,v in self.b['source_binding'].items() if k!='root_identity6'}}
  self.event={'job_uid':self.b['source_binding']['job_uid'],'pod_uid':self.b['source_binding']['pod_uid'],'production_writes':0,'capture_started_at':self.b['capture_started_at'],'completed_at':self.b['completed_at'],'all_file_count':len(self.b['all_file_fingerprints']),'epub_count':len(self.b['files']),'selected_read_only_measurement':self.b['read_only_stage_measurement']};self.selected={r['path']:r['sha256'] for r in self.b['files']}
 def test_real_producer_fixture_raw_schema_is_accepted(self):host.validate_baseline(self.b,self.event,self.native,self.selected)
 def test_numeric_nanosecond_is_not_accepted(self):
  self.b['all_file_fingerprints'][next(iter(self.b['all_file_fingerprints']))][0][3]=123
  with self.assertRaises(host.Refused):host.validate_baseline(self.b,self.event,self.native,self.selected)
 def test_missing_whole_census_path_refuses(self):
  del self.b['all_file_fingerprints'][self.b['files'][0]['path']]
  with self.assertRaises(host.Refused):host.validate_baseline(self.b,self.event,self.native,self.selected)
 def test_wrong_selected_bytes_or_native_owner_refuse(self):
  bad={k:'f'*64 for k in self.selected}
  with self.assertRaises(host.Refused):host.validate_baseline(self.b,self.event,self.native,bad)
  self.native['source_binding']['pod_uid']='33333333-3333-4333-8333-333333333333'
  with self.assertRaises(host.Refused):host.validate_baseline(self.b,self.event,self.native,self.selected)
 def test_fresh_observation_preserves_source_identity(self):
  current=copy.deepcopy(self.native);current['observed_at']='2026-10-09T01:08:00+00:00';self.assertTrue(host.same_native(current,self.native));current['source_binding']['pod_spec_sha256']='f'*64;self.assertFalse(host.same_native(current,self.native))
 def complete_fixture(self):
  ready=json.loads((HERE/'live-baseline-closed-manifest.json').read_bytes());name=ready['metadata']['name'];phase=ready['metadata']['labels'][host.LABEL];uid='11111111-1111-4111-8111-111111111111';puid='22222222-2222-4222-8222-222222222222';job=copy.deepcopy(ready);job['metadata']['uid']=uid
  labels=job['spec']['template']['metadata']['labels'];labels.update({'batch.kubernetes.io/controller-uid':uid,'controller-uid':uid,'batch.kubernetes.io/job-name':name,'job-name':name});job['spec']['selector']={'matchLabels':{'batch.kubernetes.io/controller-uid':uid}};job['status']={'succeeded':1,'conditions':[{'type':'Complete','status':'True'}]}
  pod={'metadata':{'name':name+'-unit','namespace':'frontend','uid':puid,'labels':copy.deepcopy(labels),'ownerReferences':[{'apiVersion':'batch/v1','kind':'Job','name':name,'uid':uid,'controller':True,'blockOwnerDeletion':True}]},'spec':copy.deepcopy(ready['spec']['template']['spec']),'status':{'phase':'Succeeded','containerStatuses':[{'name':'census','imageID':host.IMAGE,'restartCount':0,'state':{'terminated':{'exitCode':0,'reason':'Completed'}}}]}}
  native={'phase_token':phase,'source_binding':{'job_uid':uid,'pod_uid':puid,'pod_name':pod['metadata']['name'],'pod_spec_sha256':hashlib.sha256(json.dumps(pod['spec'],sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest(),'image_id':host.IMAGE}}
  return ready,job,pod,native
 def test_actual_admission_completion_contract_and_owner_refusal(self):
  r,j,p,n=self.complete_fixture();host.completed(r,j,p,n,helper);p['metadata']['ownerReferences'][0]['uid']='33333333-3333-4333-8333-333333333333'
  with self.assertRaises(host.Refused):host.completed(r,j,p,n,helper)
 def test_nonzero_exit_or_spec_mutation_cannot_claim_complete(self):
  r,j,p,n=self.complete_fixture();p['status']['containerStatuses'][0]['state']['terminated']['exitCode']=2
  with self.assertRaises(host.Refused):host.completed(r,j,p,n,helper)
  r,j,p,n=self.complete_fixture();p['spec']['containers'][0]['env'][0]['value']='changed'
  with self.assertRaises(host.Refused):host.completed(r,j,p,n,helper)
 def test_blocked_stdin_has_original_command_deadline(self):
  r=object.__new__(host.Run);r.end=time.time()+20;r.total=r.end+20
  before=time.monotonic()
  with self.assertRaises(host.Refused):r.command([sys.executable,'-c','import time;time.sleep(5)'],payload=b'x'*(256*1024),seconds=.5)
  self.assertLess(time.monotonic()-before,3)
 def test_ownerless_same_phase_or_controller_pod_refuses_absence(self):
  r=object.__new__(host.Run);r.phase='a'*32;r.uid='11111111-1111-4111-8111-111111111111'
  pod={'metadata':{'name':'unit-orphan','namespace':host.NS,'uid':'22222222-2222-4222-8222-222222222222','labels':{host.LABEL:r.phase},'ownerReferences':[]}}
  with self.assertRaises(host.Refused):r.exact_pods({'kind':'PodList','items':[pod]})
  pod['metadata']['labels']={'batch.kubernetes.io/controller-uid':r.uid}
  with self.assertRaises(host.Refused):r.exact_pods({'kind':'PodList','items':[pod]})
 def test_finished_log_must_bind_same_received_sha_and_owner(self):
  r=object.__new__(host.Run);r.lock=__import__('threading').Lock();r.uid='J';r.pod_uid='P';event={'sha256':'a'*64};r.events=[{'type':'live-baseline-delivered','sha256':'a'*64,'job_uid':'J','pod_uid':'P','production_writes':0}];r.delivered_log(event);r.events[0]['sha256']='b'*64
  with self.assertRaises(host.Refused):r.delivered_log(event)
class InventoryTests(unittest.TestCase):
 def setUp(self):
  self.run=object.__new__(host.Run);self.run.phase='a'*32;self.run.uid='11111111-1111-4111-8111-111111111111'
 def native(self,kind='pods',items=None):
  return {'apiVersion':'v1' if kind=='pods' else 'batch/v1','kind':'PodList' if kind=='pods' else 'JobList','metadata':{'resourceVersion':'123456'},'items':items or []}
 def test_frozen_manifest_pin_and_phase_downward_api_remain_valid(self):
  raw=(HERE/'live-baseline-closed-manifest.json').read_bytes();self.assertEqual(hashlib.sha256(raw).hexdigest(),host.PINS['closed_manifest']);manifest=json.loads(raw)
  self.assertEqual(manifest['metadata']['name'],host.NAME);phase=manifest['metadata']['labels'][host.LABEL];self.assertEqual(manifest['spec']['template']['metadata']['labels'][host.LABEL],phase)
  env=manifest['spec']['template']['spec']['containers'][0]['env'];phase_env=next(item for item in env if item['name']=='COPY_PHASE_TOKEN')
  self.assertEqual(phase_env,{'name':'COPY_PHASE_TOKEN','valueFrom':{'fieldRef':{'apiVersion':'v1','fieldPath':"metadata.labels['"+host.LABEL+"']"}}})
  self.assertFalse(any(item.get('value') and 'valueFrom' in item for item in env))
 def test_exact_unfiltered_raw_endpoints_and_cleanup_budget(self):
  for kind,path in [('pods','/api/v1/namespaces/frontend/pods'),('jobs','/apis/batch/v1/namespaces/frontend/jobs')]:
   with self.subTest(kind=kind),patch.object(self.run,'command',return_value=host.canonical(self.native(kind))) as command:
    self.assertEqual(self.run.inventory(kind,True),self.native(kind));command.assert_called_once_with(['kubectl','get','--raw',path],cleanup=True)
 def test_synthesized_list_wrong_type_and_api_version_refuse(self):
  for bad in [{'kind':'List','apiVersion':'v1','metadata':{'resourceVersion':''},'items':[]},
              {**self.native(),'kind':'JobList'},{**self.native(),'apiVersion':'batch/v1'},
              {**self.native(),'items':{}},[]]:
   with self.subTest(bad=bad),patch.object(self.run,'command',return_value=host.canonical(bad)):
    with self.assertRaises(host.Refused):self.run.inventory('pods')
 def test_missing_resource_version_and_incomplete_inventory_refuse(self):
  for meta in [{},{'resourceVersion':''},{'resourceVersion':123},None,
               {'resourceVersion':'123','continue':'next-page'},
               {'resourceVersion':'123','remainingItemCount':1},
               {'resourceVersion':'123','remainingItemCount':True}]:
   with self.subTest(meta=meta),patch.object(self.run,'command',return_value=host.canonical({**self.native(),'metadata':meta})):
    with self.assertRaises(host.Refused):self.run.inventory('pods')
  good={**self.native(),'metadata':{'resourceVersion':'123','continue':'','remainingItemCount':0}}
  with patch.object(self.run,'command',return_value=host.canonical(good)):self.assertEqual(self.run.inventory('pods'),good)
 def test_wrong_native_item_or_cross_namespace_refuses(self):
  good={'apiVersion':'v1','kind':'Pod','metadata':{'namespace':host.NS}}
  for bad in [{**good,'kind':'Job'},{**good,'apiVersion':'batch/v1'},
              {**good,'metadata':{'namespace':'media'}},None]:
   with self.subTest(bad=bad),patch.object(self.run,'command',return_value=host.canonical(self.native(items=[bad]))):
    with self.assertRaises(host.Refused):self.run.inventory('pods')
  native_item={'metadata':{'namespace':host.NS}}
  with patch.object(self.run,'command',return_value=host.canonical(self.native(items=[native_item]))):self.assertEqual(self.run.inventory('pods')['items'],[native_item])
 def owned_pod(self):
  return {'apiVersion':'v1','kind':'Pod','metadata':{'name':'unit','namespace':host.NS,'uid':'22222222-2222-4222-8222-222222222222','labels':{host.LABEL:self.run.phase,'batch.kubernetes.io/controller-uid':self.run.uid},'ownerReferences':[{'apiVersion':'batch/v1','kind':'Job','name':host.NAME,'uid':self.run.uid,'controller':True,'blockOwnerDeletion':True}]}}
 def test_phase_controller_owner_name_or_uid_union_blocks_false_absence(self):
  good=self.owned_pod();self.assertEqual(self.run.exact_pods(self.native(items=[good])),[good])
  for match in ['phase','controller','owner_name','owner_uid']:
   p=self.owned_pod();p['metadata']['labels']={};p['metadata']['ownerReferences']=[]
   if match=='phase':p['metadata']['labels']={host.LABEL:self.run.phase}
   elif match=='controller':p['metadata']['labels']={'batch.kubernetes.io/controller-uid':self.run.uid}
   else:p['metadata']['ownerReferences']=[{'kind':'Job','name':host.NAME if match=='owner_name' else 'different-name','uid':self.run.uid if match=='owner_uid' else '33333333-3333-4333-8333-333333333333'}]
   with self.subTest(match=match):
    with self.assertRaises(host.Refused):self.run.exact_pods(self.native(items=[p]))
 def test_cleanup_delete_is_foreground_and_uid_preconditioned(self):
  self.run.ready={};self.run.helper=type('Helper',(),{'declared_matches':staticmethod(lambda *args:True)})();self.run.result={};self.run.out=Path('/unit-only')
  job={'apiVersion':'batch/v1','kind':'Job','metadata':{'name':host.NAME,'namespace':host.NS,'uid':self.run.uid,'labels':{host.LABEL:self.run.phase}}}
  initial=self.native('jobs',[job]);empty_jobs=self.native('jobs');empty_pods=self.native('pods')
  self.run.verifier=type('Verifier',(),{'baseline_absent':staticmethod(lambda *args:None)})()
  with patch.object(self.run,'inventory',side_effect=[initial,empty_jobs,empty_pods]),patch.object(self.run,'command') as command,patch.object(self.run,'check'),patch.object(host,'private'):
   self.run.cleanup()
  args,body=command.call_args.args;self.assertEqual(args,['kubectl','delete','--raw',f'/apis/batch/v1/namespaces/{host.NS}/jobs/{host.NAME}','-f','-'])
  self.assertEqual(json.loads(body),{'apiVersion':'v1','kind':'DeleteOptions','propagationPolicy':'Foreground','preconditions':{'uid':self.run.uid}});self.assertTrue(command.call_args.kwargs['cleanup']);self.assertTrue(self.run.result['job_and_all_owned_pods_absent'])
 def test_reused_job_uid_refuses_before_delete(self):
  self.run.ready={};self.run.helper=type('Helper',(),{'declared_matches':staticmethod(lambda *args:True)})();self.run.result={}
  job={'apiVersion':'batch/v1','kind':'Job','metadata':{'name':host.NAME,'namespace':host.NS,'uid':'33333333-3333-4333-8333-333333333333','labels':{host.LABEL:self.run.phase}}}
  with patch.object(self.run,'inventory',return_value=self.native('jobs',[job])),patch.object(self.run,'command') as command:
   with self.assertRaises(host.Refused):self.run.cleanup()
  command.assert_not_called()
if __name__=='__main__':unittest.main()
