#!/usr/bin/env python3
"""One explicitly approved LIVE baseline Job. No PG, scan, settings or library write."""
import argparse,copy,datetime as dt,hashlib,importlib.util,json,os,selectors,signal,stat,subprocess,sys,threading,time,uuid,re
from pathlib import Path
IMAGE='ghcr.io/thaynes43/book-copy-writer@sha256:fdc358fcce883a198a710f9415a95e5200b39499a26ab540a9863043a8b2f86c'
MODULES={'epub_copies.py':'b79389c89bd5a57b4737ad693d2c209bc513e818343064ec04c8f0fcfb24a7da','epub_metadata.py':'ce3c5a271cb4c94d91f3154240b4cfbc5e0cc57981969fc5c975a0c0f50eb773'}
LABEL='issue825.haynesnetwork/phase';NAME='issue831-live-byte-baseline-1009-03';NS='frontend';GO='ACTUAL_PARENT_BOUND_LIVE_BASELINE_RO_GO'
PINS={'helper':'c0894d655c677a5f3ba41911d0efb6f43310dc63f451530bba9f402d328046e0','sender':'675d47a66657445846f6f5cdc6646e640daf17d97914eb2b0abda02d756cce82','receiver':'fbf7998738652db4023721731526b343faff4b2d880d24e811a63a95eaab2d3e','native_verifier':'67f40c064babee41cc7faba1b7b9541a0ffe65d4d0f137507248fdf5c9e8108f','collector':'6e758e34db12fb82d4d6050c460d17a815d5b608c511a1d7bc9f94886342a61a','selected_scope':'1754edf94c3735c5c7cf6a78d30e3bea3b110e7b48a77ef1fea6e16e91c82663','ack_receiver':'de30ca5463b6564146f4fe71c79a6502487941ccfc09d7967054abeceff421f3','closed_manifest':'40d743c36e4b0b4dede4d596678a5a17fb0818eadc497323af7c57efd7f1c11b'}
class Refused(RuntimeError):pass

def require(ok,code):
 if not ok:raise Refused(code)
def stamp():return dt.datetime.now(dt.timezone.utc).isoformat()
def unique(rows):
 v={}
 for k,x in rows:
  require(k not in v,'duplicate_json_key');v[k]=x
 return v
def decode(raw):return json.loads(raw,object_pairs_hook=unique)
def same_native(a,b):return {k:v for k,v in a.items() if k!='observed_at'}=={k:v for k,v in b.items() if k!='observed_at'}
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def private(path,raw):
 raw=raw if isinstance(raw,bytes) else canonical(raw)+b'\n';fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
def pinned(entry,cap=1024*1024):
 require(isinstance(entry,dict) and set(entry)=={'path','sha256'} and Path(entry['path']).is_absolute(),'artifact_binding')
 fd=os.open(entry['path'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 with os.fdopen(fd,'rb') as f:
  a=os.fstat(f.fileno());require(stat.S_ISREG(a.st_mode) and a.st_nlink==1 and 0<a.st_size<=cap,'artifact_type_or_cap');raw=f.read(cap+1);b=os.fstat(f.fileno());z=os.stat(entry['path'],follow_symlinks=False)
  fields=('st_dev','st_ino','st_size','st_mode','st_uid','st_gid','st_mtime_ns','st_ctime_ns','st_nlink');require(len(raw)==a.st_size and all(getattr(a,k)==getattr(b,k)==getattr(z,k) for k in fields) and hashlib.sha256(raw).hexdigest()==entry['sha256'],'artifact_changed')
 return raw
def load(name,entry):
 raw=pinned(entry);spec=importlib.util.spec_from_loader(name,loader=None,origin=entry['path']);m=importlib.util.module_from_spec(spec);m.__file__=entry['path'];exec(compile(raw,entry['path'],'exec'),m.__dict__);return m

def completed(ready,job,pod,native,helper):
 # Only this exact read-only LIVE profile may complete here. Do not widen the
 # checkpoint helper's separate completed-MAIN-only lease verification.
 jm,pm=job['metadata'],pod['metadata'];expected=native['source_binding'];status=pod['status']['containerStatuses'];conditions=job.get('status',{}).get('conditions',[])
 require(jm.get('uid')==expected['job_uid'] and (jm.get('namespace'),jm.get('name'))==(NS,NAME) and jm.get('labels',{}).get(LABEL)==native['phase_token'] and not jm.get('deletionTimestamp') and helper.declared_matches(ready,job),'completed_job_identity')
 require(pm.get('uid')==expected['pod_uid'] and pm.get('name')==expected['pod_name'] and pm.get('namespace')==NS and pm.get('labels',{}).get(LABEL)==native['phase_token'] and pm.get('labels',{}).get('batch.kubernetes.io/controller-uid')==expected['job_uid'] and not pm.get('deletionTimestamp') and pm.get('ownerReferences')==[{'apiVersion':'batch/v1','kind':'Job','name':NAME,'uid':expected['job_uid'],'controller':True,'blockOwnerDeletion':True}],'completed_pod_identity')
 require(hashlib.sha256(json.dumps(pod['spec'],sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()==expected['pod_spec_sha256'] and len(status)==1 and status[0]['name']==ready['spec']['template']['spec']['containers'][0]['name'] and status[0]['restartCount']==0 and status[0]['imageID'].removeprefix('docker-pullable://')==expected['image_id'],'completed_spec_image')
 term=status[0].get('state',{}).get('terminated',{})
 require(pod['status']['phase']=='Succeeded' and set(status[0]['state'])=={'terminated'} and type(term.get('exitCode')) is int and term['exitCode']==0 and term.get('reason')=='Completed' and job.get('status',{}).get('active',0)==0 and job.get('status',{}).get('succeeded')==1 and any(r.get('type')=='Complete' and r.get('status')=='True' for r in conditions) and not any(r.get('type')=='Failed' and r.get('status')=='True' for r in conditions),'live_not_complete')

def validate_baseline(value,event,native,selected):
 require(value.get('schema')==1 and value.get('kind')=='live_byte_baseline' and value.get('complete') is True and value.get('stable_before_after') is True and value.get('read_only') is True and value.get('production_writes')==0 and value.get('module_sha256')==MODULES,'baseline_complete_schema')
 binding=value.get('source_binding',{});require(set(binding)==set(native['source_binding'])|{'root_identity6'} and all(binding[k]==v for k,v in native['source_binding'].items()),'baseline_native_binding')
 require(event.get('job_uid')==binding['job_uid'] and event.get('pod_uid')==binding['pod_uid'] and event.get('production_writes')==0 and event.get('capture_started_at')==value['capture_started_at'] and event.get('completed_at')==value['completed_at'] and event.get('all_file_count')==len(value['all_file_fingerprints']) and event.get('epub_count')==len(value['files']) and event.get('selected_read_only_measurement')==value['read_only_stage_measurement'],'baseline_ready_binding')
 fps=value.get('all_file_fingerprints');require(isinstance(fps,dict) and 0<len(fps)<=10000,'complete_fingerprint_map')
 for path,fp in fps.items():
  require(isinstance(path,str) and path and '\\' not in path and all(part not in ('','.','..') for part in path.split('/')) and all(ord(c)>=32 for c in path),'fingerprint_path')
  require(isinstance(fp,list) and len(fp)==4 and isinstance(fp[0],list) and len(fp[0])==6,'fingerprint_fields')
  require(all(isinstance(v,str) and re.fullmatch('0|[1-9][0-9]*',v) and int(v)<=2**64-1 for v in fp[0]+fp[1:]) and int(fp[0][5])>=1,'decimal_fingerprint')
 require(isinstance(value.get('files'),list) and len(value['files'])<=10000,'complete_epub_rows')
 rows={r['path']:r for r in value['files']};require(len(rows)==len(value['files']) and all(rows.get(p,{}).get('sha256')==sha for p,sha in selected.items()),'selected_scope_changed')
 require(set(rows)=={p for p in fps if not any(x.startswith('.') for x in p.split('/')) and p.lower().endswith('.epub')} and all(r.get('source_identity')==fps[p][0] and isinstance(r.get('sha256'),str) and re.fullmatch('[0-9a-f]{64}',r['sha256']) and isinstance(r.get('packages'),list) and r['packages'] and all(isinstance(o.get('raw_opf_sha256'),str) and re.fullmatch('[0-9a-f]{64}',o['raw_opf_sha256']) for o in r['packages']) for p,r in rows.items()),'complete_epub_fingerprint_binding')
 clocks=[value['capture_started_at'],value['completed_at'],value['read_only_stage_measurement']['started_at'],value['read_only_stage_measurement']['selected_started_at'],value['read_only_stage_measurement']['completed_at']]
 epochs=[dt.datetime.fromisoformat(x.replace('Z','+00:00')).timestamp() for x in clocks];require(epochs==sorted(epochs),'baseline_clock_order')
 require(value['read_only_stage_measurement']['selected_distinct_count']==len(selected) and value['read_only_stage_measurement']['production_writes']==0,'measurement_scope')

class Run:
 def __init__(self,c,started_at=None):
  self.start=time.time() if started_at is None else started_at;self.c=c;self.out=Path(c['output_dir']);self.out.mkdir(mode=0o700,exist_ok=False);self.end=self.start+180;self.total=self.start+200;self.uid=None;self.pod_uid=None;self.native=None;self.stream=None;self.events=[];self.log_error=None;self.lock=threading.Lock();self.result={'schema':1,'started_at':stamp(),'phase_token':c['phase_token'],'job_uid':None,'pod_uid':None,'baseline_complete':False,'production_library_writes':0,'PG_operations':0}
  require(re.fullmatch('[0-9a-f]{32}',c['phase_token']) is not None,'phase_token')
  for name,sha in PINS.items():
   if name!='closed_manifest':require(c[name].get('sha256')==sha,'reviewed_'+name+'_pin')
   pinned(c[name])
  require(Path(c['receiver']['path'])==Path(c['sender']['path']).parent/'receive-private-inputs.py' and Path(c['collector']['path'])==Path(c['native_verifier']['path']).parent/'bound_census_collectors.py','reviewed_sibling_closure')
  self.helper=load('baseline_helper',c['helper'])
  collector=load('bound_census_collectors',c['collector']);old=sys.modules.get('bound_census_collectors');sys.modules['bound_census_collectors']=collector
  try:self.verifier=load('baseline_native_verifier',c['native_verifier'])
  finally:
   if old is None:sys.modules.pop('bound_census_collectors',None)
   else:sys.modules['bound_census_collectors']=old
  self.ready=decode(pinned(c['closed_manifest'],2*1024*1024));self.phase=c['phase_token'];self.receiver=pinned(c['receiver'],131000);self.ack=pinned(c['ack_receiver'],131000)
  require(self.phase!='0'*32,'fresh_live_phase_required')
  reviewed=copy.deepcopy(self.ready)
  for meta in (reviewed['metadata'],reviewed['spec']['template']['metadata']):
   require(meta.get('labels',{}).get(LABEL)==self.phase,'closed_live_phase_identity')
   meta['labels'][LABEL]='0'*32
  require(hashlib.sha256(canonical(reviewed)+b'\n').hexdigest()==PINS['closed_manifest'],'closed_live_template_changed')
 def check(self,cleanup=False):require(time.time()<(self.total if cleanup else self.end),'host_absolute_deadline')
 def command(self,args,payload=None,cap=2*1024*1024,seconds=5,cleanup=False):
  self.check(cleanup);limit=min(self.total if cleanup else self.end,time.time()+seconds);p=subprocess.Popen(args,stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
  try:
   require(payload is None or len(payload)<=2*1024*1024,'native_stdin_cap')
   raw=bytearray();total=0;position=0
   with selectors.DefaultSelector() as selector:
    selector.register(p.stdout,selectors.EVENT_READ,'out');selector.register(p.stderr,selectors.EVENT_READ,'err')
    if payload is not None:
     os.set_blocking(p.stdin.fileno(),False);selector.register(p.stdin,selectors.EVENT_WRITE,'in')
    while selector.get_map():
     require(time.time()<limit,'native_command_deadline')
     for key,_ in selector.select(min(.05,max(.001,limit-time.time()))):
      if key.data=='in':
       if position<len(payload):
        try:position+=os.write(key.fileobj.fileno(),payload[position:position+4096])
        except BlockingIOError:continue
        except BrokenPipeError:raise Refused('native_stdin_refused')
       if position==len(payload):selector.unregister(key.fileobj);key.fileobj.close();p.stdin=None
       continue
      chunk=os.read(key.fileobj.fileno(),65536)
      if not chunk:selector.unregister(key.fileobj);continue
      total+=len(chunk);require(total<=cap,'native_output_cap')
      if key.data=='out':raw.extend(chunk)
   require(time.time()<limit and p.wait(timeout=max(.001,limit-time.time()))==0,'native_command_refused');return bytes(raw)
  finally:
   if p.poll() is None:os.killpg(p.pid,signal.SIGKILL)
   p.wait(timeout=2)
   for f in (p.stdin,p.stdout,p.stderr):
    if f is not None:f.close()
 def get(self,kind,name,cleanup=False):return json.loads(self.command(['kubectl','get',kind,name,'-n',NS,'-o','json'],cleanup=cleanup))
 def inventory(self,kind,cleanup=False):
  # kubectl get -o json synthesizes a generic List and discards native list
  # metadata. Read the complete, unfiltered namespace API object instead.
  endpoints={'jobs':(f'/apis/batch/v1/namespaces/{NS}/jobs','batch/v1','JobList','Job'),
             'pods':(f'/api/v1/namespaces/{NS}/pods','v1','PodList','Pod')}
  require(kind in endpoints,'inventory_resource')
  path,api,list_kind,item_kind=endpoints[kind]
  value=decode(self.command(['kubectl','get','--raw',path],cleanup=cleanup))
  require(isinstance(value,dict) and value.get('apiVersion')==api and value.get('kind')==list_kind
          and isinstance(value.get('items'),list),'complete_native_inventory')
  meta=value.get('metadata')
  require(isinstance(meta,dict) and isinstance(meta.get('resourceVersion'),str) and bool(meta['resourceVersion'])
          and meta.get('continue','')=='' and ('remainingItemCount' not in meta or type(meta['remainingItemCount']) is int and meta['remainingItemCount']==0),'complete_native_inventory_metadata')
  # Native typed lists may omit each item's redundant type/version fields.
  require(all(isinstance(item,dict) and item.get('kind',item_kind)==item_kind and item.get('apiVersion',api)==api
              and isinstance(item.get('metadata'),dict) and item['metadata'].get('namespace')==NS for item in value['items']),'native_inventory_item')
  # Preserve the typed envelope's identity for comparisons of individual objects.
  for item in value['items']:
   item.setdefault('kind',item_kind);item.setdefault('apiVersion',api)
  return value
 def exact_pods(self,pods):
  require(pods.get('kind')=='PodList' and isinstance(pods.get('items'),list),'complete_pod_inventory')
  found=[]
  for p in pods['items']:
   m=p['metadata'];owners=m.get('ownerReferences',[])
   flags=(m.get('labels',{}).get(LABEL)==self.phase or self.uid is not None and m.get('labels',{}).get('batch.kubernetes.io/controller-uid')==self.uid or any(o.get('kind')=='Job' and (o.get('name')==NAME or self.uid is not None and o.get('uid')==self.uid) for o in owners))
   if not flags:continue
   require(m.get('namespace')==NS and m.get('labels',{}).get(LABEL)==self.phase and len(owners)==1 and owners[0].get('kind')=='Job' and owners[0].get('apiVersion')=='batch/v1' and owners[0].get('controller') is True and owners[0].get('name')==NAME and self.uid in (None,owners[0].get('uid')) and m.get('labels',{}).get('batch.kubernetes.io/controller-uid')==owners[0].get('uid'),'owned_pod_reused');found.append(p)
  return found
 def native_now(self):
  job=self.get('job',NAME);pods=self.exact_pods(self.inventory('pods'));require(len(pods)==1,'exact_one_owned_pod');pod=pods[0]
  native=self.verifier.bind(self.ready,job,pod,self.phase,MODULES,stamp(),self.helper,self.uid,pod['metadata']['uid'])
  if self.native:require(same_native(native,self.native),'native_source_changed')
  return job,pod,native
 def logs(self,pod):
  fd=os.open(self.out/'actual-log.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);self.stream=subprocess.Popen(['kubectl','logs','--follow','-n',NS,pod['metadata']['name'],'-c','census'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
  def drain():
   try:
    total=0;pending=bytearray()
    with os.fdopen(fd,'wb',buffering=0) as output,selectors.DefaultSelector() as sel:
     sel.register(self.stream.stdout,selectors.EVENT_READ,'out');sel.register(self.stream.stderr,selectors.EVENT_READ,'err')
     while sel.get_map():
      for key,_ in sel.select(.1):
       chunk=os.read(key.fileobj.fileno(),65536)
       if not chunk:sel.unregister(key.fileobj);continue
       total+=len(chunk);require(total<=1024*1024,'log_cap')
       if key.data=='out':
        output.write(chunk);os.fsync(output.fileno());pending.extend(chunk)
        require(len(pending)<=65536,'log_line_cap')
        while b'\n' in pending:
         line,_,pending=pending.partition(b'\n');event=json.loads(line)
         with self.lock:self.events.append(event)
     require(not pending and self.stream.wait(timeout=1)==0,'log_transport_refused')
   except BaseException:self.log_error='bounded_live_log_refused'
  self.thread=threading.Thread(target=drain,daemon=True);self.thread.start()
 def collect(self):
  spec=self.ready;require(spec['metadata']['name']==NAME and spec['metadata']['namespace']==NS and spec['metadata']['labels'][LABEL]==self.phase and spec['spec']['template']['metadata']['labels'][LABEL]==self.phase,'closed_manifest_identity')
  c=spec['spec']['template']['spec']['containers'][0];env={e['name']:e for e in c['env']};require(c['image']==IMAGE and env['COPY_BASELINE_PHASE_READY'].get('value')=='0' and env['COPY_BASELINE_DEADLINE_EPOCH'].get('value')=='0' and env['COPY_BASELINE_MODULE_SHA256_JSON'].get('value')=='{}' and spec['spec']['activeDeadlineSeconds']==180 and spec['spec']['backoffLimit']==0,'closed_live_profile')
  env['COPY_BASELINE_PHASE_READY']['value']='1';env['COPY_BASELINE_DEADLINE_EPOCH']['value']=str(int(self.end));env['COPY_BASELINE_MODULE_SHA256_JSON']['value']=canonical(MODULES).decode();private(self.out/'ready-manifest.json',spec)
  jobs=self.inventory('jobs');pods=self.inventory('pods');require(not any(j['metadata']['name']==NAME for j in jobs['items']) and not self.exact_pods(pods),'initial_owned_name_absence')
  admitted=json.loads(self.command(['kubectl','create','--dry-run=server','-f','-','-o','json'],canonical(spec)));require(self.helper.declared_matches(spec,admitted),'server_admission_differs');private(self.out/'server-dry-run.json',admitted)
  created=json.loads(self.command(['kubectl','create','-f','-','-o','json'],canonical(spec)));self.uid=created['metadata']['uid'];self.result['job_uid']=self.uid;private(self.out/'actual-created-job.json',created)
  while True:
   self.check();job=self.get('job',NAME);pods=self.exact_pods(self.inventory('pods'));require(not job.get('status',{}).get('failed') and len(pods)<=1,'live_job_failed')
   if pods and pods[0]['status']['phase']=='Running':break
   time.sleep(.2)
  job,pod,self.native=self.native_now();self.pod_uid=pod['metadata']['uid'];self.result['pod_uid']=self.pod_uid;private(self.out/'actual-running-job.json',job);private(self.out/'actual-running-pod.json',pod);private(self.out/'native-source-binding.json',self.native);self.logs(pod)
  artifact=lambda path:{'path':str(path),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}
  native_path=self.out/'native-source-binding.json';ready_path=self.out/'ready-manifest.json'
  contract={'schema':1,'mode':'LIVE','manifest':artifact(ready_path),'helper':self.c['helper'],'native_verifier':self.c['native_verifier'],'receiver':self.c['receiver'],'native_binding':artifact(native_path),'selected_scope':self.c['selected_scope'],'phase_state':None,'source_fence':None,'namespace':NS,'job_name':NAME,'job_uid':self.uid,'pod_name':pod['metadata']['name'],'pod_uid':self.pod_uid,'phase_token':self.phase,'deadline_epoch':str(int(self.end)),'restore_pr':None,'phase_identity_sha256':None,'image':IMAGE};private(self.out/'input-delivery-contract.json',contract)
  pinned(self.c['sender'])
  self.command(['nice','-n','19','python3',self.c['sender']['path'],'--contract',str(self.out/'input-delivery-contract.json'),'--receipt',str(self.out/'input-delivery-receipt.jsonl')],seconds=12)
  while True:
   self.check();require(self.log_error is None,'live_log_refused')
   with self.lock:events=list(self.events)
   require(not any(e.get('type')=='live-baseline-refused' for e in events),'live_producer_refused');found=[e for e in events if e.get('type')=='live-baseline-ready']
   if found:require(len(found)==1,'ready_event_repeated');event=found[0];break
   job=self.get('job',NAME);require(not job.get('status',{}).get('failed'),'live_job_failed');time.sleep(.2)
  job,pod,native_before=self.native_now();private(self.out/'native-before-fetch.json',native_before);private(self.out/'actual-before-fetch-pod.json',pod)
  program="import os,signal,stat,sys,time;signal.signal(signal.SIGALRM,lambda *_:sys.exit(2));signal.setitimer(signal.ITIMER_REAL,max(.001,float(sys.argv[1])-time.time()));fd=os.open('/tmp/live-byte-baseline.json',os.O_RDONLY|os.O_NOFOLLOW);a=os.fstat(fd);assert stat.S_ISREG(a.st_mode) and a.st_nlink==1 and 0<a.st_size<=33554432;f=os.fdopen(fd,'rb');raw=f.read(33554433);b=os.fstat(f.fileno());z=os.stat('/tmp/live-byte-baseline.json',follow_symlinks=False);fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_nlink','st_mode','st_uid','st_gid');assert len(raw)==a.st_size and all(getattr(a,k)==getattr(b,k)==getattr(z,k) for k in fields);sys.stdout.buffer.write(raw)"
  end=min(self.end,time.time()+10);raw=self.command(['kubectl','exec','-n',NS,pod['metadata']['name'],'-c','census','--','nice','-n','19','python','-B','-c',program,str(end)],cap=32*1024*1024,seconds=10);require(hashlib.sha256(raw).hexdigest()==event['sha256'],'baseline_raw_sha');value=decode(raw);selected=decode(pinned(self.c['selected_scope'],65536));validate_baseline(value,event,self.native,selected);private(self.out/'live-byte-baseline.json',raw)
  job,pod,native_after=self.native_now();require(same_native(native_after,native_before),'native_changed_during_fetch');private(self.out/'native-after-fetch.json',native_after);private(self.out/'actual-after-fetch-pod.json',pod)
  ack={'schema':1,'type':'live_baseline_delivery_ack','phase_token':self.phase,'job_uid':self.uid,'pod_uid':self.pod_uid,'baseline_sha256':event['sha256']};private(self.out/'delivery-ack.json',ack)
  end=min(self.end,time.time()+5);answer=json.loads(self.command(['kubectl','exec','-i','-n',NS,pod['metadata']['name'],'-c','census','--','nice','-n','19','python','-B','-c',self.ack.decode(),str(end)],payload=canonical(ack),seconds=5));require(answer=={**{k:v for k,v in ack.items() if k!='type'},'type':'live_baseline_ack_ready','production_writes':0},'ack_receipt_differs');private(self.out/'ack-receiver-receipt.json',answer)
  while True:
   self.check();job=self.get('job',NAME);pods=self.exact_pods(self.inventory('pods'));require(len(pods)==1 and not job.get('status',{}).get('failed'),'completion_owner_or_failure')
   if any(e.get('type')=='Complete' and e.get('status')=='True' for e in job.get('status',{}).get('conditions',[])):break
   time.sleep(.2)
  completed(self.ready,job,pods[0],self.native,self.helper)
  self.thread.join(timeout=min(2,max(.001,self.end-time.time())));require(not self.thread.is_alive() and self.log_error is None,'finished_log_transport');self.delivered_log(event);private(self.out/'actual-complete-job.json',job);private(self.out/'actual-complete-pod.json',pods[0]);self.result.update(baseline_complete=True,baseline=artifact(self.out/'live-byte-baseline.json'),original_byte_started_at=value['capture_started_at'],original_byte_completed_at=value['completed_at'],read_only_stage_measurement=value['read_only_stage_measurement'],actual_complete_at=stamp())
 def cleanup(self):
  # Actual UID preconditions only; a UID-null create failure may discover its
  # exact phase+manifest Job before deletion, never substitute historical IDs.
  jobs=self.inventory('jobs',True);found=[j for j in jobs['items'] if j['metadata']['name']==NAME]
  require(len(found)<=1,'cleanup_job_duplicate')
  if found:
   j=found[0];require(j['metadata'].get('labels',{}).get(LABEL)==self.phase and self.uid in (None,j['metadata']['uid']) and self.helper.declared_matches(self.ready,j),'cleanup_job_reused');self.uid=j['metadata']['uid'];self.result['job_uid']=self.uid
   self.command(['kubectl','delete','--raw',f'/apis/batch/v1/namespaces/{NS}/jobs/{NAME}','-f','-'],canonical({'apiVersion':'v1','kind':'DeleteOptions','propagationPolicy':'Foreground','preconditions':{'uid':self.uid}}),cleanup=True)
  else:
   for p in self.exact_pods(self.inventory('pods',True)):
    self.command(['kubectl','delete','--raw',f"/api/v1/namespaces/{NS}/pods/{p['metadata']['name']}",'-f','-'],canonical({'apiVersion':'v1','kind':'DeleteOptions','propagationPolicy':'Foreground','preconditions':{'uid':p['metadata']['uid']}}),cleanup=True)
  while True:
   self.check(True);jobs=self.inventory('jobs',True);pods=self.inventory('pods',True)
   if not any(j['metadata']['name']==NAME for j in jobs['items']) and not self.exact_pods(pods):break
   time.sleep(.2)
  if self.uid is not None:self.verifier.baseline_absent(jobs,pods,NS,NAME,self.uid,self.phase)
  private(self.out/'actual-cleanup-jobs.json',jobs);private(self.out/'actual-cleanup-pods.json',pods);self.result.update(job_and_all_owned_pods_absent=True,cleanup_verified_at=stamp())
 def delivered_log(self,event):
  with self.lock:events=list(self.events)
  matches=[e for e in events if e.get('type')=='live-baseline-delivered']
  require(len(matches)==1 and matches[0]=={'type':'live-baseline-delivered','sha256':event['sha256'],'job_uid':self.uid,'pod_uid':self.pod_uid,'production_writes':0},'delivered_owned_log')
 def execute(self):
  old={}
  def stop(*_):raise Refused('host_alarm_or_signal')
  try:
   for sig in (signal.SIGALRM,signal.SIGTERM,signal.SIGINT):old[sig]=signal.signal(sig,stop)
   signal.setitimer(signal.ITIMER_REAL,self.total-time.time());self.collect()
  except BaseException as e:self.result.update(refused=True,error_class=type(e).__name__,error_code=str(e) if isinstance(e,Refused) else None,baseline_complete=False)
  finally:
   try:self.cleanup()
   except BaseException as e:self.result.update(cleanup_complete=False,cleanup_error_class=type(e).__name__)
   if self.stream is not None:
    if self.stream.poll() is None:os.killpg(self.stream.pid,signal.SIGTERM)
    try:self.stream.wait(timeout=2)
    except subprocess.TimeoutExpired:os.killpg(self.stream.pid,signal.SIGKILL);self.stream.wait(timeout=2)
    self.thread.join(timeout=1)
   if self.log_error is not None and self.result.get('baseline_complete'):self.result.update(baseline_complete=False,refused=True,error_code=self.log_error)
   self.result.update(finished_at=stamp(),actual_seconds=time.time()-self.start);private(self.out/'actual-receipt.json',self.result)
   signal.setitimer(signal.ITIMER_REAL,0)
   for sig,handler in old.items():signal.signal(sig,handler)
  return self.result

def main():
 p=argparse.ArgumentParser();p.add_argument('--contract',required=True);p.add_argument('--root-authorization');a=p.parse_args()
 if a.root_authorization!=GO:print(json.dumps({'prepared_only':True,'production_actions':0}));return
 started=time.time();previous={}
 def stop(*_):raise Refused('original_host_absolute_deadline_or_signal')
 try:
  for sig in (signal.SIGALRM,signal.SIGTERM,signal.SIGINT):previous[sig]=signal.signal(sig,stop)
  signal.setitimer(signal.ITIMER_REAL,200)
  c=decode(Path(a.contract).read_bytes());require(set(c)=={'schema','output_dir','phase_token','closed_manifest','helper','sender','receiver','native_verifier','collector','ack_receiver','selected_scope'} and c['schema']==1,'launch_contract_schema');result=Run(c,started).execute();print(json.dumps({k:result.get(k) for k in ('baseline_complete','refused','job_uid','pod_uid','job_and_all_owned_pods_absent','actual_seconds')}));raise SystemExit(0 if result.get('baseline_complete') and result.get('job_and_all_owned_pods_absent') else 2)
 finally:
  signal.setitimer(signal.ITIMER_REAL,0)
  for sig,handler in previous.items():signal.signal(sig,handler)
if __name__=='__main__':main()
