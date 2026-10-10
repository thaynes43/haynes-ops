#!/usr/bin/env python3
"""Prepared #825 maintenance supervisor. No runtime action without --execute.

Root stages a reviewed inverse while services are live, then resumes the pause.
This process observes the first stop and owns five exact bounded Jobs. SOURCE
PID1 owns evidence-only primary PG locks; published MAIN PID1 owns its own locks.
The host owns no PG session. Complete sources and final publisher scope precede
assembly and immutable MAIN deadline binding. No active MAIN overlaps refresh.
A separate pre-armed watchdog owns Git/source/Flux recovery, including parent death.
"""
import argparse
import collections
import copy
import contextlib
import fcntl
import io
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
import stat
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import tempfile
import time
import uuid
import window_contract as window
import cached_source as cache

SCOPES=[('frontend','haynesnetwork'),('media','libretto'),('downloads','lazylibrarian'),('media','kavita')]
CRONS=[('downloads','lazylibrarian-epub-convert'),('downloads','lazylibrarian-library-scan')]+[
 ('frontend','haynesnetwork-'+n) for n in ['sync-books','sync-books-collections','sync-format-pairing','sync-goodreads']]
INPUT_IMAGE='ghcr.io/thaynes43/book-copy-writer@sha256:628e97b8dbcc83a4d7068484b516b21dde740c4dd130d54f3b030f1ee7a75601'
MODULES={'epub_copies.py':'b79389c89bd5a57b4737ad693d2c209bc513e818343064ec04c8f0fcfb24a7da','epub_metadata.py':'ce3c5a271cb4c94d91f3154240b4cfbc5e0cc57981969fc5c975a0c0f50eb773'}
ROOT='/data/cephfs-hdd/data/media/books/EBooks'
STATE='/data/cephfs-hdd/data/media/books/.epub-convert'
REPO='thaynes43/haynes-ops'
class Refused(RuntimeError):pass
class NotStopped(Refused):pass

def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def epoch(value):return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
def terminal_success(row):
 if row.get('conclusion') not in ['SUCCESS','SKIPPED','NEUTRAL']:return False
 if row.get('status')=='COMPLETED':return True
 try:return isinstance(row.get('completedAt'),str) and bool(row['completedAt']) and epoch(row['completedAt'])>0
 except (ValueError,TypeError):return False
def private_json(path,value,replace=False):
 p=Path(path);tmp=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp')
 descriptor=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  with os.fdopen(descriptor,'w') as output:output.write(json.dumps(value,indent=2)+'\n');output.flush();os.fsync(output.fileno())
  if replace:os.replace(tmp,p)
  else:os.link(tmp,p,follow_symlinks=False)
  directory=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  try:os.fsync(directory)
  finally:os.close(directory)
 finally:tmp.unlink(missing_ok=True)
def bounded_text(value,limit):
 raw=value[:limit].encode('utf-8',errors='replace');return raw[:limit].decode('utf-8',errors='ignore'),len(value)>limit or len(raw)>limit
def exception_diagnostic(error,publisher_refused=None):
 # Only our fixed refusal messages and the exact pinned publisher exception
 # type retain text. Unknown subprocess exceptions may contain credentials.
 known=isinstance(error,Refused) or (publisher_refused is not None and type(error) is publisher_refused)
 message,truncated=bounded_text(str(error),4096) if known else (None,False)
 frames=collections.deque(maxlen=16);count=0;tb=error.__traceback__
 while tb is not None:
  frame=tb.tb_frame;frames.append({'file':bounded_text(frame.f_code.co_filename,512)[0],'function':bounded_text(frame.f_code.co_name,128)[0],'line':tb.tb_lineno});tb=tb.tb_next;count+=1
 return {'schema':1,'exception_class':bounded_text(type(error).__name__,128)[0],
  'exception_module':bounded_text(type(error).__module__,256)[0],'known_refusal':known,
  'private_message':message,'message_truncated':truncated,'frames':list(frames),
  'frames_truncated':count>16,'locals_retained':False}
VENDOR_PREDICATES=set(['capture_consistency_refused', 'capture_deadline_expired', 'capture_deadline_invalid', 'capture_empty_ll_inventory', 'capture_fence_abort_clock', 'capture_fence_backend', 'capture_fence_identity', 'capture_fence_node', 'capture_fence_schema', 'capture_fence_stale', 'capture_fence_start', 'capture_fence_tables', 'capture_fence_time', 'capture_identity_missing', 'capture_kind_unknown', 'capture_phase_closed', 'capture_release_identity', 'capture_release_unsafe', 'capture_source_byte_cap', 'capture_source_changed', 'capture_source_changed_before_release', 'capture_source_missing_or_journal', 'capture_source_unsafe'])
VENDOR_CODES=set(['ERR_ASSERTION', 'absolute_deadline', 'capture_guard_failed', 'ENOENT', 'EEXIST', 'EACCES', 'EPERM', 'ENOSPC', 'ERR_SQLITE_ERROR', 'SQLITE_ERROR', 'MODULE_NOT_FOUND', 'ERR_MODULE_NOT_FOUND', 'ERR_UNKNOWN_BUILTIN_MODULE', 'ERR_DLOPEN_FAILED'])
VENDOR_ERROR_CLASSES=set(['AssertionError', 'Error', 'TypeError', 'RangeError', 'SyntaxError'])
def vendor_refusal_event(event,key,job_uid,pod_uid,phase_token):
 # Only fixed reviewed enums survive. Raw assertion text, values and locals do not.
 code=event.get('code') if isinstance(event.get('code'),str) and event.get('code') in VENDOR_CODES else 'other'
 predicate=event.get('predicate') if code=='ERR_ASSERTION' and isinstance(event.get('predicate'),str) and event.get('predicate') in VENDOR_PREDICATES else None
 kind='ll' if key[0]=='downloads' else 'kavita'
 return {'type':'capture-refused','code':code,'predicate':predicate,
  'error_class':event.get('error_class') if isinstance(event.get('error_class'),str) and event.get('error_class') in VENDOR_ERROR_CLASSES else 'unknown',
  'kind':kind,'namespace':key[0],'job_name':key[1],'job_uid':job_uid,'pod_uid':pod_uid,'phase_token':phase_token,
  'event_identity_verified':(event.get('kind'),event.get('job_uid'),event.get('pod_uid'),event.get('phase_token'))==(kind,job_uid,pod_uid,phase_token)}
def publisher_writer_deadline(scope,phase_deadline,reserve,at):
 if scope.get('read_only') is not True or scope.get('publisher_count')!=8:raise Refused('writer needs the complete eight-publisher scope')
 start,finish=epoch(scope['capture_started_at']),epoch(scope['captured_at'])
 if not start<=finish<=at or at-start>65 or at-finish>35:raise Refused('publisher proof is future, inverted or expired')
 deadline=math.floor(min(phase_deadline-reserve,start+65,finish+35))
 if deadline<=at:raise Refused('no fresh publisher writer lease remains')
 return deadline
def fresh(value,age=300):
 delta=time.time()-epoch(value)
 if delta < -5 or delta > age:raise Refused('source/health timestamp is future or expired')
def byte_activation_admission(baseline,at):
 if (type(baseline.get('schema')) is not int or baseline['schema']!=1
     or baseline.get('kind')!='live_byte_baseline' or baseline.get('complete') is not True):raise Refused('complete pinned LIVE baseline required before activation')
 start,finish=epoch(baseline['capture_started_at']),epoch(baseline['completed_at'])
 if not start<=finish<=at or not 0<=at-start<=300:raise Refused('original byte activation admission expired or future')
def prospective_moves(report):
 return sum(not row['keeper'] and not row['protected_reasons']
            for group in report['groups'] for row in group['copies'])
def clean_gates(pr,review=None,bindings=None):
 checks=pr.get('statusCheckRollup',[]);names={r.get('name'):r for r in checks}
 independent=window.independent_advisory(review,pr,bindings or {})
 for name in ['Flux Local - Success','Diff Scope - Success']:
  if not terminal_success(names.get(name,{})) or names[name].get('conclusion')!='SUCCESS':return False
 if not checks or any(not terminal_success(r) and not (independent and r.get('name')=='Claude Review (advisory)') for r in checks):return False
 if independent:return True
 if not terminal_success(names.get('Claude Review (advisory)',{})) or names['Claude Review (advisory)'].get('conclusion')!='SUCCESS':return False
 at=epoch(pr['commits'][-1]['committedDate'])
 if not names['Claude Review (advisory)'].get('startedAt') or epoch(names['Claude Review (advisory)']['startedAt'])<at:return False
 comments=[r for r in pr['comments'] if r.get('author',{}).get('login','').lower() in ('claude','claude[bot]') and epoch(r.get('updatedAt') or r['createdAt'])>=at]
 if not comments:return False
 body=max(comments,key=lambda r:epoch(r.get('updatedAt') or r['createdAt']))['body']
 return window.clean_advisory(body)

def pinned_artifact(entry,cap):
 if not isinstance(entry,dict) or set(entry)!={'path','sha256'} or not Path(entry['path']).is_absolute() or not re.fullmatch('[0-9a-f]{64}',entry['sha256']):raise Refused('exact private artifact descriptor required')
 fd=os.open(entry['path'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 with os.fdopen(fd,'rb') as source:
  before=os.fstat(source.fileno());fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_size','st_mtime_ns','st_ctime_ns','st_nlink')
  if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_mode&0o077 or not 0<before.st_size<=cap:raise Refused('private artifact type/mode/link/byte cap differs')
  raw=source.read(cap+1);after=os.fstat(source.fileno());current=os.stat(entry['path'],follow_symlinks=False)
  if len(raw)!=before.st_size or any(getattr(before,k)!=getattr(after,k) or getattr(before,k)!=getattr(current,k) for k in fields) or hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise Refused('private artifact changed before sealing')
 return raw
def native_reader_identity(key,row,pod):
 status=pod['status']['containerStatuses'];spec=pod['spec']
 if len(status)!=1 or len(spec['containers'])!=1 or pod['metadata']['uid'] is None:raise Refused('exact reader native identity missing')
 return {'namespace':key[0],'job_name':key[1],'job_uid':row['uid'],'pod_name':pod['metadata']['name'],'pod_uid':pod['metadata']['uid'],'pod_spec_sha256':hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest(),'image':spec['containers'][0]['image'],'image_id':status[0]['imageID'],'restarts':status[0]['restartCount']}
def seal_vendor_payload(key,result,row,pod_uid,phase,sql_sha):
 proof=json.loads(pinned_artifact(result['proof'],4*1024*1024));kind='ll' if key[0]=='downloads' else 'kavita';base='lazylibrarian.db' if kind=='ll' else 'kavita.db'
 if (proof.get('schema')!=1 or proof.get('kind')!=kind or proof.get('phase_token')!=phase or proof.get('job_uid')!=row['uid'] or proof.get('pod_uid')!=pod_uid or proof.get('readOnlySource') is not True or proof.get('sourceWrites')!=0 or not proof.get('before') or proof['before']!=proof.get('after') or epoch(proof['captureStartedAt'])>epoch(proof['capturedAt'])):raise Refused('complete stable vendor producer proof missing before cleanup')
 entries=proof.get('files');files=result.get('files')
 if (not isinstance(entries,list) or not isinstance(files,dict) or len({e['name'] for e in entries})!=len(entries) or {e['name'] for e in entries}!=set(files) or base not in files or set(files)-{base,base+'-wal',base+'-shm'}):raise Refused('complete vendor DB/WAL/SHM scope missing before cleanup')
 for e in entries:
  raw=pinned_artifact(files[e['name']],512*1024*1024)
  if len(raw)!=e['size'] or hashlib.sha256(raw).hexdigest()!=e['sha256']:raise Refused('vendor copied bytes differ from producer proof')
 if kind=='ll':
  sql=json.loads(pinned_artifact(result['sql'],32*1024*1024))
  if (sql.get('queryName')!='LL_BOOKS_SQL' or sql.get('querySha256')!=sql_sha or sql.get('phase')!=phase or sql.get('sourceWrites')!=0 or sql.get('readOnly') is not True or not isinstance(sql.get('llBooks'),list) or not sql['llBooks'] or sql.get('sourceFingerprintBefore')!=proof['before'] or sql.get('sourceFingerprintAfter')!=proof['after']):raise Refused('complete LL SQL proof missing before cleanup')
 else:
  if set(result.get('raw_exports',{}))!={'reading-state-all.json','dependencies.json','saved-metadata-locks.json'}:raise Refused('complete raw Kavita exports missing before cleanup')
  for entry in result['raw_exports'].values():json.loads(pinned_artifact(entry,32*1024*1024))
 return {'schema':1,'namespace':key[0],'job_name':key[1],'job_uid':row['uid'],'pod_uid':pod_uid,'phase_token':phase,'complete_copied_payload':True,'payload':result,'source_writes':0}

class Supervisor:
 def __init__(self,config):
  self.c=config;self.out=Path(config['evidence_dir']);self.out.mkdir(mode=0o700,parents=True,exist_ok=False)
  self.path=self.out/'supervisor.json';self.status={'prepared_only':False,'phase':'arming','heartbeat':now(),
   'restore_pr':config['restore_pr'],'restore_head':config['restore_head'],'owned_jobs':[],'writer_job':None,'complete':False}
  helper_path=Path(config['copy_checkpoint_helper'])
  if hashlib.sha256(helper_path.read_bytes()).hexdigest()!=config['copy_checkpoint_helper_sha256']:raise Refused('copy checkpoint helper changed')
  helper_spec=importlib.util.spec_from_file_location('copy_checkpoint',helper_path)
  self.checkpoint=importlib.util.module_from_spec(helper_spec);helper_spec.loader.exec_module(self.checkpoint)
  ledger,_sha=self.checkpoint.read_json(Path(config['copy_phase_state']));self.checkpoint.validate_state(ledger,config['restore_pr']);self.checkpoint.validate_leases(ledger,require_all=True)
  if ledger['complete'] or any(row['uid'] is not None for row in ledger['owned_jobs']):raise Refused('copy phase must be fresh with all intents uncreated')
  self.status['phase_token']=ledger['phase_token']
  self.status['heartbeat_required']=True
  self.jobs=[{'namespace':r['namespace'],'name':r['name'],'uid':None,'phase_token':ledger['phase_token'],'writer':r['writer']} for r in ledger['owned_jobs']]
  self.status['owned_jobs']=self.jobs
  self.lock=None;self.events=queue.Queue();self.health_at=None;self.lock_pid=None;self.deadline=None;self.lock_ready=False
  self.last_heartbeat=0;self.ledger_frozen=False;self.source_pod=None;self.source_ready=False;self.app_ready_sha=None
  publisher_path=Path(config['publisher_guard'])
  if hashlib.sha256(publisher_path.read_bytes()).hexdigest()!=config['publisher_guard_sha256']:raise Refused('publisher guard module changed')
  module=importlib.util.spec_from_file_location('publisher_guard',publisher_path)
  self.publishers=importlib.util.module_from_spec(module);module.loader.exec_module(self.publishers)
  self.stop=False;self.last_guard=0;self.last_cluster_guard=0;self.hook=None
  self.main_stream=None;self.main_log_error=None;self.main_log_thread=None;self.main_prefix_lock=threading.Lock();self.main_prefix_bytes=0
  signal.signal(signal.SIGTERM,self.stop_signal);signal.signal(signal.SIGINT,self.stop_signal)
  self.save()
 def stop_signal(self,*unused):self.stop=True
 def ledger(self):
  state,sha=self.checkpoint.read_json(Path(self.c['copy_phase_state']));self.checkpoint.validate_state(state,self.c['restore_pr']);self.checkpoint.validate_leases(state,require_all=True);return state,sha
 def heartbeat(self):
  if self.deadline is None or self.ledger_frozen or time.monotonic()-self.last_heartbeat<1:return
  path=Path(self.c['copy_phase_state']);fd=os.open(path.with_suffix(path.suffix+'.lock'),os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'w') as lock:
   try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:return
   state,_=self.ledger()
   if 'window_started_at' not in state or state['complete']:raise Refused('heartbeat needs the current started COPY phase')
   state['heartbeat']=now();self.checkpoint.validate_state(state,self.c['restore_pr']);self.checkpoint.save(path,state)
  self.last_heartbeat=time.monotonic()
 def save(self):
  self.heartbeat();self.status['heartbeat']=now();private_json(self.path,self.status,replace=True)
 def helper(self,*argv):
  result=self.run_monitored(['nice','-n','19','python3',self.c['copy_checkpoint_helper'],'--phase-state',self.c['copy_phase_state'],'--restore-pr',str(self.c['restore_pr']),*argv],10,require_lock=self.lock_ready)
  return result
 def row(self,key):
  state,_=self.ledger();return next(r for r in state['owned_jobs'] if (r['namespace'],r['name'])==key)
 def bind(self,key,values):
  self.guard_lease(self.lock_ready);row=self.row(key)
  if row['uid'] is not None or row['ready_manifest'] is not None:raise Refused('intent already created or bound; never rerun')
  source=self.out/(key[1]+'-env.json');ready=self.out/(key[1]+'-ready.json');private_json(source,values)
  self.helper('bind','--namespace',key[0],'--name',key[1],'--initial-manifest-sha256',row['initial_manifest_sha256'],'--env-file',str(source),'--output',str(ready))
  return ready
 def run(self,argv,timeout=15,input_text=None):
  guarded=(self.deadline is not None and self.status.get('phase') not in ('aborting','restoring','restore_requested','restored_verified'))
  if guarded:
   self.guard_lease(self.lock_ready)
   timeout=min(timeout,5,max(.01,self.deadline-int(self.c['restore_reserve_seconds'])-time.time()))
  env=dict(os.environ)
  if argv[0]=='gh':env['GH_TOKEN']=Path('/creds/gh_token').read_text().strip()
  result=subprocess.run(argv,env=env,input=input_text,text=True,capture_output=True,timeout=timeout)
  if guarded:self.guard_lease(self.lock_ready)
  if result.returncode:raise Refused(f'{argv[0]} {argv[1]} failed with exit {result.returncode}')
  return result.stdout
 def get(self,kind,name,ns):return json.loads(self.run(['kubectl','get',kind,name,'-n',ns,'-o','json']))
 def list(self,kind,ns=None):
  types={'pods':('Pod','pods'),'pvc':('PersistentVolumeClaim','persistentvolumeclaims'),'pv':('PersistentVolume','persistentvolumes')}
  if kind not in types:raise Refused('unsupported authoritative inventory kind')
  typed,resource=types[kind];endpoint='/api/v1/'+(f'namespaces/{ns}/' if ns else '')+resource
  return window.typed_inventory(json.loads(self.run(['kubectl','get','--raw',endpoint])),typed,'v1',ns)
 def review_gate(self,pr):
  if not self.c.get('review_disposition'):return clean_gates(pr)
  operation=self.c['review_operation']
  bindings=dict(operation,inverse_pr=str(self.c['restore_pr']),inverse_head=self.c['restore_head'],phase_token=self.status['phase_token'])
  pause=json.loads(self.run(['gh','pr','view',operation['stop_pr'],'--repo',REPO,'--json','headRefOid']))
  if pause['headRefOid']!=operation['stop_head']:raise Refused('reviewed Stop head changed')
  value=window.load_review_disposition(self.c['review_disposition'],bindings,self.c['repo_dir'],cache.read_private)
  self.status['independent_review_disposition_sha256']=self.c['review_disposition']['sha256'];self.save()
  return clean_gates(pr,value,bindings)
 def inverse_green(self):
  if self.c.get('cached_source_receipt'):
   return self.merged_inverse_ready()
  pr=json.loads(self.run(['gh','pr','view',str(self.c['restore_pr']),'--repo',REPO,'--json',
   'state,baseRefName,headRefOid,statusCheckRollup,comments,commits,files']))
  pages=json.loads(self.run(['gh','api','--paginate','--slurp',f"repos/{REPO}/issues/{self.c['restore_pr']}/comments?per_page=100"]))
  pr['comments']=[{'author':{'login':r['user']['login']},'body':r['body'],'createdAt':r['created_at'],'updatedAt':r['updated_at']} for page in pages for r in page]
  if pr['state']!='OPEN' or pr['baseRefName']!='main' or pr['headRefOid']!=self.c['restore_head'] or not self.review_gate(pr):
   raise Refused('exact main inverse/current checks/normal advisory are not green')
  expected={
   'kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert-cronjob.yaml':(1,1),
   'kubernetes/main/apps/frontend/haynesnetwork/app/helmrelease.yaml':(4,4),
   'kubernetes/main/apps/media/libretto/app/helmrelease.yaml':(1,1),
   'kubernetes/main/apps/downloads/lazylibrarian/app/library-scan-cronjob.yaml':(1,1),
   'kubernetes/main/apps/downloads/lazylibrarian/app/helmrelease.yaml':(0,1),
   'kubernetes/main/apps/media/kavita/app/helmrelease.yaml':(0,1)}
  if {r['path']:(r['additions'],r['deletions']) for r in pr['files']}!=expected:raise Refused('inverse is not exact +7/-9 across six manifests')
  patch=self.run(['gh','pr','diff',str(self.c['restore_pr']),'--repo',REPO]).splitlines()
  plus=collections.Counter(r[1:].strip() for r in patch if r.startswith('+') and not r.startswith('+++'))
  minus=collections.Counter(r[1:].strip() for r in patch if r.startswith('-') and not r.startswith('---'))
  url='http://lazylibrarian.downloads.svc.cluster.local:5299'
  if plus!=collections.Counter(['suspend: false']*6+['LAZYLIBRARIAN_URL: '+url]) or minus!=collections.Counter(['suspend: true']*6+['LAZYLIBRARIAN_URL: ""','replicas: 0','replicas: 0']):raise Refused('inverse contains an unexpected field')
  self.run(['git','-C',self.c['repo_dir'],'fetch','origin','main:refs/remotes/origin/main'])
  self.run(['git','-C',self.c['repo_dir'],'fetch','origin',f"pull/{self.c['restore_pr']}/head"])
  base=self.run(['git','-C',self.c['repo_dir'],'rev-parse','origin/main']).strip()
  contract=json.loads(pinned_artifact(self.c['manifest_contract'],1024*1024))
  window.verify_git_pair(self.c['repo_dir'],base,self.c['restore_head'],contract,'inverse')
  self.status['inverse_green_checked_at']=now();self.save()
 def cached_guard(self,holds):
  raw=pinned_artifact(self.c['cached_source_receipt'],1024*1024)
  receipt=json.loads(raw)
  contract=json.loads(pinned_artifact(self.c['manifest_contract'],1024*1024))
  deadline=None if self.deadline is None else self.deadline-int(self.c['restore_reserve_seconds'])
  cache.check_live(receipt,self.get,contract,self.status['phase_token'],holds=holds,deadline=deadline)
  return receipt,hashlib.sha256(raw).hexdigest()
 def merged_inverse_ready(self):
  receipt,_sha=self.cached_guard(True)
  pr=json.loads(self.run(['gh','pr','view',str(self.c['restore_pr']),'--repo',REPO,'--json',
   'state,baseRefName,headRefOid,mergeCommit,statusCheckRollup,comments,commits,files']))
  pages=json.loads(self.run(['gh','api','--paginate','--slurp',f"repos/{REPO}/issues/{self.c['restore_pr']}/comments?per_page=100"]))
  pr['comments']=[{'author':{'login':r['user']['login']},'body':r['body'],'createdAt':r['created_at'],'updatedAt':r['updated_at']} for page in pages for r in page]
  merge=receipt.get('normal_inverse_merge_sha')
  if (pr['state']!='MERGED' or pr['baseRefName']!='main' or pr['headRefOid']!=self.c['restore_head']
      or pr.get('mergeCommit',{}).get('oid')!=merge or str(receipt.get('restore_pr'))!=str(self.c['restore_pr'])
      or not self.review_gate(pr)):raise Refused('reviewed Normal inverse must already be merged before Stop')
  self.run(['git','-C',self.c['repo_dir'],'fetch','origin','main:refs/remotes/origin/main'])
  current=self.run(['git','-C',self.c['repo_dir'],'rev-parse','origin/main']).strip()
  self.run(['git','-C',self.c['repo_dir'],'merge-base','--is-ancestor',merge,current])
  contract=json.loads(pinned_artifact(self.c['manifest_contract'],1024*1024))
  parent=self.run(['git','-C',self.c['repo_dir'],'rev-parse',merge+'^']).strip()
  window.verify_git_pair(self.c['repo_dir'],parent,merge,contract,'inverse')
  window.validate_pair(window.blobs(self.c['repo_dir'],merge),window.blobs(self.c['repo_dir'],receipt['stop_main_sha']),contract)
  cache.normal_goal(window.blobs(self.c['repo_dir'],current))
  self.status.update(inverse_green_checked_at=now(),normal_inverse_merge_sha=merge);self.save()
 def arm(self):
  self.inverse_green()
  watch=json.loads(Path(self.c['watchdog_state']).read_text())
  if not watch.get('armed_ready') or watch.get('complete') or not watch.get('recover_ks') or str(watch['restore_pr'])!=str(self.c['restore_pr']) or sorted(map(tuple,watch['scopes']))!=sorted(SCOPES):raise Refused('four-app recovery watchdog is not pre-armed')
  if Path(self.c['watchdog_stop']).exists():raise Refused('watchdog already has a stop request')
  if watch.get('copy_authority_revoked_at'):raise Refused('watchdog revoked this phase; fresh packet/phase required')
  if self.c.get('cached_source_receipt'):
   if (watch.get('cached_source_receipt_sha256')!=self.c['cached_source_receipt']['sha256']
       or not watch.get('cached_source_ready_at')
       or watch.get('normal_inverse_merge_sha')!=self.status.get('normal_inverse_merge_sha')):
    raise Refused('watchdog has not accepted the same sealed cached source/Normal inverse')
  if time.time()-epoch(watch['armed_at'])>=int(self.c['arm_deadline_seconds']):raise Refused('pre-stage watchdog arm deadline already expired')
  for ns,name in [('downloads','lazylibrarian'),('media','kavita')]:
   d=self.get('deployment',name,ns)
   if d['spec'].get('replicas',1)!=1 or d.get('status',{}).get('readyReplicas',0)!=1:raise Refused('services must be live while inverse is validated')
   selector=d['spec']['selector']['matchLabels']
   pods=[p for p in self.list('pods',ns) if all(p['metadata'].get('labels',{}).get(k)==v for k,v in selector.items())]
   if len(pods)!=1 or pods[0]['metadata'].get('deletionTimestamp'):raise Refused('capture placement must bind one stable live app pod')
   pvc=self.get('pvc',name,ns)
   self.status.setdefault('capture_placement',{})[ns]={'claim':name,'claim_uid':pvc['metadata']['uid'],'node':pods[0]['spec']['nodeName']}
  for ns,name in SCOPES:
   if self.get('kustomization',name,ns)['spec'].get('suspend') is not True:raise Refused('all four KS must be held while workloads are live')
  if self.c.get('cached_source_receipt'):
   proof=json.loads(pinned_artifact(self.c['cached_source_receipt'],1024*1024))
   contract=json.loads(pinned_artifact(self.c['manifest_contract'],1024*1024))
   cache.check_live(proof,self.get,contract,self.status['phase_token'],holds=True)
   owners=watch.get('cached_ks_owners',{})
   for ns,name in SCOPES+[('flux-system',n) for n in cache.PARENTS]:
    hold=proof['parents'][name] if ns=='flux-system' else proof['holds'][ns+'/'+name]
    if owners.get(ns+'/'+name)!={'uid':hold['before']['metadata']['uid'],'spec':hold['before']['spec'],'phase_token':self.status['phase_token']}:raise Refused('watchdog does not own same exact six hold identities')
  else:
   proof=json.loads(pinned_artifact(self.c['hold_receipt'],1024*1024))
   window.validate_holds(proof,{(ns,name):self.get('kustomization',name,ns) for ns,name in SCOPES},time.time())
  normal=window.blobs(self.c['repo_dir'],proof['normal_main_sha'])
  contract=json.loads(pinned_artifact(self.c['manifest_contract'],1024*1024))
  for path in window.PATHS:
   if window.sha(normal[path])!=contract['manifests'][path]['normal_sha256']:raise Refused('held preflight normal main differs')
  self.status.update(phase='armed_while_live',armed_ready=True,watchdog_armed_at=watch['armed_at']);self.save()
  if self.c.get('cached_source_receipt'):
   baseline=json.loads(pinned_artifact(self.c['live_byte_baseline'],32*1024*1024))
   activation_at=now();byte_activation_admission(baseline,epoch(activation_at))
   self.status['actuation_budget_started_at']=activation_at;self.save()
   cache.write_private(self.c['cached_source_activation'],(json.dumps({'schema':1,
    'phase_token':self.status['phase_token'],'cached_source_receipt_sha256':self.c['cached_source_receipt']['sha256'],
    'normal_inverse_merge_sha':self.status['normal_inverse_merge_sha'],'armed_ready':True,
    'actuation_budget_started_at':self.status['actuation_budget_started_at']},sort_keys=True)+'\n').encode())
 def cached_stop_gate(self,watch):
  if (watch.get('complete') or not watch.get('recover_ks') or not watch.get('armed_ready')
      or watch.get('armed_at')!=self.status['watchdog_armed_at']
      or str(watch.get('restore_pr'))!=str(self.c['restore_pr'])
      or watch.get('normal_inverse_merge_sha')!=self.status['normal_inverse_merge_sha']
      or watch.get('copy_authority_revoked_at')):raise Refused('pending Stop watcher custody lost')
  remaining=epoch(self.status['actuation_budget_started_at'])+170-time.time()
  if remaining<=0:raise Refused('pre-release conservative restoration trigger reached')
  with cache.wall_guard(remaining):
   receipt,_sha=self.cached_guard(False)  # Source and both parents, while apps may be released.
   if not watch.get('cached_stop_actuation_complete_at'):return False
   binding=watch.get('cached_stop_actuation_binding',{})
   if binding!={'phase_token':self.status['phase_token'],'cached_source_receipt_sha256':self.c['cached_source_receipt']['sha256'],
       'activation_sha256':window.sha(cache.read_private(self.c['cached_source_activation'])),'stop_main_sha':receipt['stop_main_sha']}:raise Refused('completed Stop actuation binding changed')
   holds=watch.get('cached_stop_holds',{})
   if set(holds)!=set(receipt['holds']):raise Refused('completed Stop rehold scope changed')
   for key,proof in holds.items():
    old=receipt['holds'][key]['before'];before=proof['before']
    if before['metadata']['uid']!=old['metadata']['uid'] or dict(before['spec'],suspend=False)!=dict(old['spec'],suspend=False):raise Refused('completed Stop rehold original identity/spec changed')
   # Preserve the sealed receipt; bind fresh reholds to original identities in memory.
   checked=dict(receipt,holds=holds);contract=json.loads(pinned_artifact(self.c['manifest_contract'],1024*1024))
   cache.check_live(checked,self.get,contract,self.status['phase_token'],holds=True,deadline=epoch(self.status['actuation_budget_started_at'])+170)
   return True
 def wait_for_stop(self):
  expires=time.monotonic()+int(self.c['arm_deadline_seconds'])
  while time.monotonic()<expires:
   if self.stop or Path(self.c['watchdog_stop']).exists():raise Refused('stop before outage')
   if self.c.get('cached_source_receipt') and time.time()>=epoch(self.status['actuation_budget_started_at'])+170:raise Refused('pre-release conservative restoration trigger reached')
   completed=False
   if self.c.get('cached_source_receipt'):
    completed=self.cached_stop_gate(json.loads(Path(self.c['watchdog_state']).read_bytes()))
   if self.status.get('first_service_stop_observed_at'):
    if completed:return
    self.save();time.sleep(.1);continue
   for ns,name in [('downloads','lazylibrarian'),('media','kavita')]:
    d=self.get('deployment',name,ns)
    pods=self.list('pods',ns);selector=d['spec']['selector']['matchLabels']
    terminating=any(all(p['metadata'].get('labels',{}).get(k)==v for k,v in selector.items()) and p['metadata'].get('deletionTimestamp') for p in pods)
    if d['spec'].get('replicas',1)==0 or d.get('status',{}).get('readyReplicas',0)<1 or terminating:
     started=now();self.deadline=epoch(started)+int(self.c['max_service_absence_seconds'])
     if self.c.get('cached_source_receipt'):self.deadline=min(self.deadline,epoch(self.status['actuation_budget_started_at'])+300)
     # The separate copy ledger starts only at the first observed absence. Its
     # ready binder derives the same <=170-second abort clock from this marker.
     ledger_path=Path(self.c['copy_phase_state']);ledger,_sha=self.checkpoint.read_json(ledger_path)
     self.checkpoint.validate_state(ledger,self.c['restore_pr'])
     if 'window_started_at' in ledger:raise Refused('copy ledger already started another window')
     import contextlib,io
     with contextlib.redirect_stdout(io.StringIO()):self.checkpoint.main(['--phase-state',str(ledger_path),'--restore-pr',str(self.c['restore_pr']),'start-window','--started-at',started])
     self.status.update(first_service_stop_observed_at=started,window_started_at=started,deadline_epoch=self.deadline,phase='waiting_for_real_fence');self.save()
     # Parent may also record this in recovery state; our own deadline is independent.
     if not self.c.get('cached_source_receipt'):return
     break
   self.save();time.sleep(1)
  raise Refused('live-stage arm deadline expired')
 def drain_lock_events(self):
  while True:
   try:event=self.events.get_nowait()
   except queue.Empty:return
   if not isinstance(event,dict) or event.get('type') in ('fence_failed','copy-source-refused','source_fence_released','stream_failed'):raise Refused('actual SOURCE PG process lost its fence')
   kind=event.get('type');identity={'phase_token':self.status['phase_token'],'job_uid':self.row(self.checkpoint.SOURCE)['uid'],'pod_uid':self.source_pod['metadata']['uid']}
   if any(event.get(k)!=v for k,v in identity.items()):raise Refused('SOURCE actual log ownership differs')
   if kind in ('fence_ready','fence_healthy'):
    fresh(event['at'],12)
    expected_name='issue825-duplicate-share-fence-'+self.status['phase_token']
    if (type(event.get('backend_pid')) is not int or event['backend_pid']<=0 or event.get('application_name')!=expected_name
        or event.get('deadline_epoch_ms')!=int((self.deadline-int(self.c['restore_reserve_seconds']))*1000)):raise Refused('SOURCE actual backend/name/deadline differs')
    if self.lock_pid is not None and event['backend_pid']!=self.lock_pid:raise Refused('SOURCE backend changed')
    if kind=='fence_healthy':
     if event.get('share_tables')!=['book_requests','books_items'] or event.get('read_only')!='on':raise Refused('SOURCE locks/read-only state lost')
     if self.health_at is not None and epoch(event['at'])<self.health_at:raise Refused('SOURCE health moved backwards')
     self.health_at=epoch(event['at']);self.lock_pid=event['backend_pid'];self.status.update(pg_backend_pid=self.lock_pid,pg_health_at=event['at'])
    else:
     if self.health_at is None or self.lock_pid!=event['backend_pid']:raise Refused('SOURCE ready lacks preceding full health')
     self.lock_ready=True
   elif kind=='app_dependency_snapshot_ready':
    if not self.lock_ready or event.get('backend_pid')!=self.lock_pid or not re.fullmatch('[0-9a-f]{64}',event.get('sha256','')):raise Refused('app ready lacks actual source backend/hash')
    self.app_ready_sha=event['sha256']
   elif kind=='source_census_ready':
    if not self.lock_ready or event.get('production_writes')!=0 or type(event.get('epub_count')) is not int or type(event.get('all_file_count')) is not int:raise Refused('SOURCE full census event is incomplete')
    self.source_ready=True;self.status['source_counts']={'epubs':event['epub_count'],'all_files':event['all_file_count']}
   elif kind=='copy-outcome-ready':
    expected=self.status.get('outcome_request_sha256')
    if (not expected or event.get('request_sha256')!=expected or event.get('production_writes')!=0
        or not re.fullmatch('[0-9a-f]{64}',event.get('response_sha256',''))
        or getattr(self,'outcome_ready',None) is not None):raise Refused('SOURCE outcome event is unknown, repeated or differs')
    fresh(event['at'],12);self.outcome_ready=event
   else:raise Refused('unknown SOURCE lifecycle event')
 def service_fence(self,wait_for_rollout=False):
  for ns,name in [('downloads','lazylibrarian'),('media','kavita')]:
   d=self.get('deployment',name,ns)
   if d['spec'].get('replicas',1)!=0 or d.get('status',{}).get('replicas',0)!=0:raise NotStopped('LL/Kavita are not stopped')
   selector=d['spec']['selector']['matchLabels']
   if any(all(p['metadata'].get('labels',{}).get(k)==v for k,v in selector.items()) for p in self.list('pods',ns)):
    raise NotStopped('LL/Kavita pod still exists, including termination')
  for ns,name in CRONS:
   job=self.get('cronjob',name,ns)
   if job['spec'].get('suspend') is not True or job.get('status',{}).get('active'):raise Refused('book writer CronJob is active or unsuspended')
  d=self.get('deployment','libretto','media');status=d.get('status',{});replicas=d['spec'].get('replicas',1)
  env=[e for c in d['spec']['template']['spec']['containers'] for e in c.get('env',[]) if e['name']=='LAZYLIBRARIAN_URL']
  # Kubernetes may omit only an empty EnvVar.value. The named variable itself
  # must remain exact and unique, with no valueFrom substitution.
  if len(env)!=1 or 'valueFrom' in env[0] or env[0].get('value','')!='':raise Refused('Libretto acquisition URL is not explicitly disabled')
  if status.get('observedGeneration')!=d['metadata']['generation'] or status.get('updatedReplicas',0)!=replicas or status.get('readyReplicas',0)!=replicas:
   if wait_for_rollout:raise NotStopped('Libretto acquisition-off rollout not complete')
   raise Refused('Libretto acquisition-off rollout lost readiness')
  selector=d['spec']['selector']['matchLabels']
  pods=[p for p in self.list('pods','media') if all(p['metadata'].get('labels',{}).get(k)==v for k,v in selector.items())]
  if replicas!=1:raise Refused('Libretto desired replica count differs from the reviewed single process')
  if len(pods)!=1 or pods[0]['metadata'].get('deletionTimestamp'):
   if wait_for_rollout:raise NotStopped('Libretto acquisition-off Pod rollout not complete')
   raise Refused('Libretto acquisition-off Pod is missing, duplicated or terminating')
  pod=pods[0];owners=[o for o in pod['metadata'].get('ownerReferences',[]) if o.get('controller') is True]
  if len(owners)!=1 or owners[0].get('kind')!='ReplicaSet' or not owners[0].get('name') or not owners[0].get('uid'):raise Refused('Libretto Pod controller is not an exact ReplicaSet')
  rs=self.get('replicaset',owners[0]['name'],'media');rsowners=[o for o in rs['metadata'].get('ownerReferences',[]) if o.get('controller') is True]
  if (rs['metadata'].get('uid')!=owners[0]['uid'] or len(rsowners)!=1 or rsowners[0].get('kind')!='Deployment'
      or rsowners[0].get('name')!='libretto' or rsowners[0].get('uid')!=d['metadata'].get('uid') or not d['metadata'].get('uid')):raise Refused('Libretto Pod does not belong to the actual Deployment')
  revision=d['metadata'].get('annotations',{}).get('deployment.kubernetes.io/revision')
  if not isinstance(revision,str) or not revision.isdecimal() or int(revision)<1:raise Refused('Libretto desired rollout revision is unproved')
  if rs['metadata'].get('annotations',{}).get('deployment.kubernetes.io/revision')!=revision:
   if wait_for_rollout:raise NotStopped('Libretto previous rollout Pod still exists')
   raise Refused('Libretto Pod is from a previous rollout')
  actual_env=[e for c in pod['spec']['containers'] for e in c.get('env',[]) if e['name']=='LAZYLIBRARIAN_URL']
  if len(actual_env)!=1 or 'valueFrom' in actual_env[0] or actual_env[0].get('value','')!='':raise Refused('Libretto actual Pod acquisition URL is not explicitly disabled')
  desired_images=[(c['name'],c['image']) for c in d['spec']['template']['spec']['containers']]
  if [(c['name'],c['image']) for c in pod['spec']['containers']]!=desired_images:raise Refused('Libretto actual Pod image or containers differ from the desired rollout')
  if pod.get('status',{}).get('phase')!='Running' or not any(c.get('type')=='Ready' and c.get('status')=='True' for c in pod.get('status',{}).get('conditions',[])):
   if wait_for_rollout:raise NotStopped('Libretto acquisition-off Pod is not Ready')
   raise Refused('Libretto acquisition-off Pod lost readiness')
  # SOURCE may collect read-only stat under its own SHARE before initial full
  # publisher verification. BOTH publisher gates remain before assembly/MAIN. This lightweight service
  # guard never fabricates a fresh publisher proof from a cached boolean.
  self.guard_lease(self.lock_ready)
  if self.status.get('writer_may_mutate'):
   # MAIN uses this exact immutable final proof until its own expiry. Recheck
   # the full actual Pod/PVC/PV inventory without beginning another capture.
   hook=self.c['publisher_scope_hook'];spec=importlib.util.spec_from_file_location('current_inventory_collector',hook['script']);collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)
   pods=self.list('pods');claims=self.list('pvc');volumes=self.list('pv');storage=collector.storage_inventory(pods,claims,volumes)
   self.publishers.verify(self.status['publisher_scope_proof'],self.c['publisher_scope_sha256'],pods,{r['uid'] for r in self.jobs if r['uid']},time.time(),storage)
   self.guard_lease(self.lock_ready)
  self.status['service_fence_checked_at']=now();self.save()
 def capture_publishers(self):
  if self.status.get('writer_created'):raise Refused('publisher refresh forbidden after MAIN creation')
  self.service_fence();destination=self.out/('publisher-scope-'+uuid.uuid4().hex+'.json')
  hook=self.c['publisher_scope_hook'];script=Path(hook['script'])
  if hashlib.sha256(script.read_bytes()).hexdigest()!=hook['sha256']:raise Refused('publisher collector source changed')
  argv=['nice','-n','19','python3',str(script),'--config',self.c['publisher_config'],'--output',str(destination),'--budget','60','--execute']
  self.run_monitored(argv,60,require_lock=self.lock_ready)
  collector_spec=importlib.util.spec_from_file_location('copy_publisher_collector',script);collector=importlib.util.module_from_spec(collector_spec);collector_spec.loader.exec_module(collector)
  pods=self.list('pods');claims=self.list('pvc');volumes=self.list('pv');storage=collector.storage_inventory(pods,claims,volumes)
  scope=self.publishers.verify(destination,self.c['publisher_scope_sha256'],pods,{r['uid'] for r in self.jobs if r['uid']},time.time(),storage)
  self.guard_lease(self.lock_ready);self.service_fence();self.guard_lease(self.lock_ready)
  raw=destination.read_bytes();canonical=self.publishers.scope_digest(json.loads(raw)['publishers']);scope['scope_sha256']=canonical
  if canonical!=self.c['publisher_scope_sha256']:raise Refused('current publisher scope differs from exact approved scope')
  service={'schema':1,'phase_token':self.status['phase_token'],'all_services_stopped':True,'all_six_crons_suspended':True,'libretto_acquisition_off':True,'unknown_library_writers':[],
   'publisher_scope_sha256':canonical,'publisher_scope_raw_sha256':hashlib.sha256(raw).hexdigest(),'publisher_scope_file':str(destination),
   'first_service_stop_observed_at':self.status['first_service_stop_observed_at'],'checked_at':now()}
  receipt=self.out/('service-fence-'+uuid.uuid4().hex+'.json');private_json(receipt,service)
  self.status.update(publisher_scope=scope,publisher_scope_proof=str(destination),service_fence_receipt=str(receipt));self.save()
  return receipt
 def guard_lease(self,require_lock=True):
  if self.stop or Path(self.c['watchdog_stop']).exists():raise Refused('stop requested')
  if getattr(self,'main_log_error',None):raise Refused('MAIN durable log drain failed; actual movement total unknown')
  if self.deadline is None or time.time()>=self.deadline-int(self.c['restore_reserve_seconds']):raise Refused('maintenance abort deadline reached')
  if self.status.get('writer_may_mutate') and time.time()>=self.status['writer_deadline_epoch']:raise Refused('frozen writer publisher lease expired')
  if require_lock:
   if self.lock is None or self.lock.poll() is not None:raise Refused('owned SOURCE log stream died')
   self.drain_lock_events()
   if not self.lock_ready:raise Refused('owned SOURCE fence not ready')
   if self.health_at is None or time.time()-self.health_at>12:raise Refused('PG lock health expired')
 def run_monitored(self,argv,timeout,require_lock=True,output_path=None,input_bytes=None):
  if type(timeout) not in (int,float) or not 0<timeout<=60:raise Refused('monitored publisher cap must be <=60 seconds')
  self.guard_lease(require_lock)
  log=Path(output_path) if output_path else self.out/('command-'+uuid.uuid4().hex+'.log')
  descriptor=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  expires=time.monotonic()+timeout
  if input_bytes is not None and (not isinstance(input_bytes,bytes) or not 0<len(input_bytes)<=32*1024*1024):raise Refused('private monitored input exceeds cap')
  with contextlib.ExitStack() as stack:
   stream=stack.enter_context(os.fdopen(descriptor,'wb'))
   source=subprocess.DEVNULL
   if input_bytes is not None:
    source=stack.enter_context(tempfile.TemporaryFile(mode='w+b',dir=self.out))
    source.write(input_bytes);source.flush();source.seek(0)
   process=subprocess.Popen(argv,stdin=source,stdout=stream,stderr=stream,start_new_session=True)
   try:
    while process.poll() is None:
     self.guard_lease(require_lock);self.save()
     if time.monotonic()>=expires:raise Refused('publisher complete capture deadline expired')
     time.sleep(.1)
    self.guard_lease(require_lock)
    if process.returncode:raise Refused('bounded reviewed command failed; private output retained')
    return str(log)
   finally:
    if process.poll() is None:
     os.killpg(process.pid,signal.SIGTERM)
     try:process.wait(timeout=2)
     except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=2)
 def guard(self,require_lock=True):
  self.guard_lease(require_lock)
  if time.monotonic()-self.last_cluster_guard>=3:self.service_fence();self.last_cluster_guard=time.monotonic()
  self.guard_lease(require_lock)
  self.save()
 def actual_pod(self,key,allow_completed=False):
  row=self.row(key);job=self.get('job',row['name'],row['namespace']);pods=self.owned_job_pods(self.list('pods',row['namespace']),row)
  if len(pods)!=1:raise Refused('owned worker must have exactly one actual Pod')
  pod=pods[0];self.checkpoint.verify_owned_pod(row,job,pod,pod['metadata']['uid'],allow_completed);return pod
 def wait_pod(self,key,seconds=15):
  until=time.monotonic()+seconds
  while time.monotonic()<until:
   self.guard_lease(self.lock_ready)
   row=self.row(key);job=self.get('job',row['name'],row['namespace'])
   if job.get('status',{}).get('failed'):raise Refused('owned helper failed before readiness')
   pods=self.owned_job_pods(self.list('pods',row['namespace']),row)
   if len(pods)>1:raise Refused('owned helper has multiple actual Pods')
   if pods and pods[0].get('status',{}).get('phase')=='Running':return self.actual_pod(key)
   self.save();time.sleep(.2)
  raise Refused('owned PVC helper did not become Running within bounded readiness')
 def native_start(self):
  key=('media','issue831-lidarr-source-1009-03');deadline=min(self.deadline-int(self.c['restore_reserve_seconds']),time.time()+115)
  ready=self.bind(key,{'LIDARR_CAPTURE_PHASE_READY':'1','LIDARR_CAPTURE_DEADLINE_EPOCH':f'{deadline:.6f}'})
  self.create_job(ready);self.wait_pod(key)
 def lock_start(self):
  # There is no host/exec PG keeper. Actual SOURCE PID1 owns the backend and TTL.
  self.service_fence();fence={'schema':1,'phase_token':self.status['phase_token'],'first_service_stop_observed_at':self.status['first_service_stop_observed_at'],
   'service_fence_checked_at':self.status['service_fence_checked_at'],'publisher_scope_sha256':self.c['publisher_scope_sha256']}
  key=self.checkpoint.SOURCE;ready=self.bind(key,{'COPY_SOURCE_PHASE_READY':'1','COPY_SOURCE_DEADLINE_EPOCH':f'{self.deadline-int(self.c["restore_reserve_seconds"]):.6f}','COPY_SOURCE_FENCE_JSON':json.dumps(fence,sort_keys=True,separators=(',',':'))})
  self.create_job(ready);self.source_pod=self.wait_pod(key);container=self.row(key)['ready_manifest']['spec']['template']['spec']['containers'][0]['name']
  err=self.out/'source-log-stream.stderr';self.source_err=open(err,'xb');os.chmod(err,0o600)
  self.lock=subprocess.Popen(['kubectl','logs','--follow','-n',key[0],self.source_pod['metadata']['name'],'-c',container],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=self.source_err,text=True,bufsize=1)
  def reader():
   try:
    total=0
    for line in self.lock.stdout:
     total+=len(line.encode())
     if total>1024*1024 or len(line)>16384 or not line.endswith('\n'):raise Refused('SOURCE log stream cap/truncation')
     event=json.loads(line);self.events.put(event)
     with open(self.out/'source-events.jsonl','a') as out:out.write(line)
   except BaseException:self.events.put({'type':'stream_failed'})
  threading.Thread(target=reader,daemon=True).start();until=time.monotonic()+10
  while not self.lock_ready:
   if self.lock.poll() is not None or time.monotonic()>=until:raise Refused('SOURCE PG readiness failed')
   self.drain_lock_events();self.guard_lease(False);self.save();time.sleep(.1)
  self.helper('record-pg','--namespace',key[0],'--name',key[1],'--pod-name',self.source_pod['metadata']['name'],'--pod-uid',self.source_pod['metadata']['uid'])
  # Actual app capture provides the exact established_at; health time is not it.
  while self.app_ready_sha is None:
   self.guard_lease();self.save();time.sleep(.1)
  artifact=self.fetch(key,'app-capture.json',32*1024*1024)
  if artifact['sha256']!=self.app_ready_sha:raise Refused('complete source app bytes differ from actual ready event')
  app=json.loads(Path(artifact['path']).read_bytes());self.status['pg_fence_established_at']=app['pg_fence_established_at'];self.status['app_capture']=artifact;self.save()
 def deliver_source_private_inputs(self):
  # Original host native observation is taken only after the owned Job/Pod
  # reads finish. Sender repeats exact live identity; observed_at never restamps.
  self.guard();key=self.checkpoint.SOURCE;row=self.row(key);pod=self.actual_pod(key);job=self.get('job',key[1],key[0]);self.checkpoint.verify_owned_pod(row,job,pod,pod['metadata']['uid'])
  observed=now();directory=self.out/'source-private-input';directory.mkdir(mode=0o700,exist_ok=False)
  items=self.c['source_private_input'];job_path=directory/'actual-job.json';pod_path=directory/'actual-pod.json';modules_path=directory/'module-sha256.json'
  private_json(job_path,job);private_json(pod_path,pod);private_json(modules_path,MODULES)
  manifest_path=directory/'ready-manifest.json';private_json(manifest_path,row['ready_manifest']);native=directory/'native-source-binding.json'
  self.run_monitored(['nice','-n','19','python3',items['native_verifier']['path'],'--manifest',str(manifest_path),'--job',str(job_path),'--pod',str(pod_path),'--phase',self.status['phase_token'],'--job-uid',row['uid'],'--pod-uid',pod['metadata']['uid'],'--module-hashes',str(modules_path),'--observed-at',observed,'--helper',self.c['copy_checkpoint_helper'],'--helper-sha256',self.c['copy_checkpoint_helper_sha256'],'--output',str(native)],3)
  self.last_heartbeat=0;self.heartbeat();state,_sha=self.ledger();identity=copy.deepcopy(state);del identity['heartbeat']
  artifact=lambda path:{'path':str(path),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}
  contract={'schema':1,'mode':'SOURCE','manifest':artifact(manifest_path),'helper':{'path':self.c['copy_checkpoint_helper'],'sha256':self.c['copy_checkpoint_helper_sha256']},'native_verifier':items['native_verifier'],'receiver':items['receiver'],'native_binding':artifact(native),'selected_scope':None,'phase_state':self.c['copy_phase_state'],'source_fence':{'pg_backend_pid':self.lock_pid,'pg_health_at':self.status['pg_health_at']},'namespace':key[0],'job_name':key[1],'job_uid':row['uid'],'pod_name':pod['metadata']['name'],'pod_uid':pod['metadata']['uid'],'phase_token':self.status['phase_token'],'deadline_epoch':str(math.floor(self.deadline-int(self.c['restore_reserve_seconds']))),'restore_pr':self.c['restore_pr'],'phase_identity_sha256':hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest(),'image':INPUT_IMAGE}
  path=directory/'delivery-contract.json';receipt=directory/'delivery-receipt.jsonl';private_json(path,contract);self.ledger_frozen=True
  try:
   self.run_monitored(['nice','-n','19','python3',items['sender']['path'],'--contract',str(path),'--receipt',str(receipt)],12)
  finally:self.ledger_frozen=False;self.last_heartbeat=0;self.save()
  result=json.loads(receipt.read_bytes())
  if result.get('type')!='private_inputs_ready' or result.get('mode')!='SOURCE' or result.get('phase_token')!=self.status['phase_token'] or result.get('job_uid')!=row['uid'] or result.get('pod_uid')!=pod['metadata']['uid'] or result.get('observed_at')!=observed or result.get('phase_state_written') is not False or result.get('production_writes')!=0:raise Refused('actual SOURCE private input receipt incomplete')
  self.status['source_private_input_receipt']=artifact(receipt);self.save()
 def bind_reader(self,path):
  self.guard()
  source,source_sha=self.checkpoint.read_json(Path(path));ns,name=self.checkpoint.validate_job(source,self.status['phase_token'])
  ledger,_sha=self.checkpoint.read_json(Path(self.c['copy_phase_state']));self.checkpoint.validate_state(ledger,self.c['restore_pr'])
  row=next((r for r in ledger['owned_jobs'] if (r['namespace'],r['name'])==(ns,name)),None)
  if (row is None or row['writer'] or row['uid'] is not None or row['gate_env']!='COPY_CAPTURE_PHASE_READY'
      or row['ready_manifest'] is not None or source_sha!=row['initial_manifest_sha256']):raise Refused('reader must match its exact closed registered intent')
  placement=self.status['capture_placement'].get(ns)
  if not placement:raise Refused('reader has no actual pre-stop PVC placement')
  fence={'schema':1,'phase_token':self.status['phase_token'],'claim_uid':placement['claim_uid'],'node':placement['node'],
   'first_service_stop_observed_at':self.status['first_service_stop_observed_at'],'pg_backend_pid':self.lock_pid,
   'pg_fence_established_at':self.status['pg_fence_established_at'],'pg_health_at':self.status['pg_health_at'],
   'service_fence_checked_at':self.status['service_fence_checked_at'],'share_tables':['book_requests','books_items'],'read_only':True}
  values={'COPY_CAPTURE_PHASE_READY':'1','COPY_CAPTURE_DEADLINE_EPOCH':str(math.floor(self.deadline-int(self.c["restore_reserve_seconds"]))),'COPY_CAPTURE_FENCE_JSON':json.dumps(fence,sort_keys=True,separators=(',',':'))}
  values_path=self.out/(name+'-capture-env.json');private_json(values_path,values)
  ready=self.out/(name+'-capture-ready.json')
  import contextlib,io
  with contextlib.redirect_stdout(io.StringIO()):self.checkpoint.main(['--phase-state',self.c['copy_phase_state'],'--restore-pr',str(self.c['restore_pr']),'bind','--namespace',ns,'--name',name,'--initial-manifest-sha256',row['initial_manifest_sha256'],'--env-file',str(values_path),'--output',str(ready)])
  self.guard_lease();return ready
 def bind_writer(self,path):
  # This is the final publisher capture. Once MAIN exists, its immutable alarm
  # ends at this proof's expiry; no replacement capture runs alongside mutation.
  self.guard();self.require_retired_readers_absent()
  source,source_sha=self.checkpoint.read_json(Path(path));ns,name=self.checkpoint.validate_job(source,self.status['phase_token'])
  ledger,_sha=self.checkpoint.read_json(Path(self.c['copy_phase_state']));self.checkpoint.validate_state(ledger,self.c['restore_pr'])
  row=next((r for r in ledger['owned_jobs'] if (r['namespace'],r['name'])==(ns,name)),None)
  if (row is None or not row['writer'] or row['uid'] is not None or row['gate_env']!='COPY_PROOF_HASHES_JSON'
      or row['ready_manifest'] is not None or source_sha!=row['initial_manifest_sha256']):raise Refused('writer must match its exact closed registered intent')
  limits={'snapshot.json':16*1024*1024,'selection.json':1024*1024,'app-capture.json':32*1024*1024}
  if set(self.c['proof_files'])!=set(limits):raise Refused('writer requires exactly the three private full proofs')
  hashes={}
  for key,limit in limits.items():
   descriptor=os.open(self.c['proof_files'][key],os.O_RDONLY|os.O_NOFOLLOW)
   with os.fdopen(descriptor,'rb') as proof:
    info=os.fstat(proof.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=limit or info.st_mode&0o077:raise Refused('writer proof must be private, regular, singly linked and bounded')
    raw=proof.read(limit+1)
    after=os.fstat(proof.fileno())
    fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_size','st_mtime_ns','st_ctime_ns','st_nlink')
    if len(raw)!=info.st_size or any(getattr(after,key)!=getattr(info,key) for key in fields):raise Refused('writer proof changed while binding')
   hashes[key]=hashlib.sha256(raw).hexdigest()
  deadline=publisher_writer_deadline(self.status['publisher_scope'],self.deadline,int(self.c['restore_reserve_seconds']),time.time())
  values={'COPY_PROOF_HASHES_JSON':json.dumps(hashes,sort_keys=True,separators=(',',':')),'COPY_DEADLINE_EPOCH':str(deadline)}
  values_path=self.out/(name+'-writer-env.json');private_json(values_path,values)
  ready=self.out/(name+'-writer-ready.json')
  import contextlib,io
  with contextlib.redirect_stdout(io.StringIO()):self.checkpoint.main(['--phase-state',self.c['copy_phase_state'],'--restore-pr',str(self.c['restore_pr']),'bind','--namespace',ns,'--name',name,'--initial-manifest-sha256',row['initial_manifest_sha256'],'--env-file',str(values_path),'--output',str(ready)])
  self.status['writer_deadline_epoch']=deadline;self.save();self.guard_lease();return ready
 def create_job(self,path,writer=False):
  if self.c.get('cached_source_receipt'):self.cached_guard(False)
  spec=json.loads(Path(path).read_bytes());ns,name=self.checkpoint.validate_job(spec,self.status['phase_token']);row=self.row((ns,name));gate,_,_=self.checkpoint.profile(spec)
  podspec=spec['spec']['template']['spec'];container=podspec['containers'][0]
  if (row['uid'] is not None or row['writer']!=writer or row['ready_manifest'] is None or self.checkpoint.digest(Path(path).read_bytes())!=row['ready_manifest_sha256']
      or not self.checkpoint.exact_json(spec,row['ready_manifest']) or spec['spec']['backoffLimit']!=0 or podspec['restartPolicy']!='Never'
      or podspec['terminationGracePeriodSeconds']!=5 or spec['spec']['template']['metadata']['annotations'].get('k8tz.io/inject')!='false'
      or len(podspec['containers'])!=1 or str(container['resources']['limits']['cpu'])!='1' or container['command'][:3]!=['nice','-n','19']):raise Refused('worker differs from exact durable one-CPU ready intent')
  if gate=='COPY_PROOF_HASHES_JSON':
   self.require_retired_readers_absent();self.checkpoint.validate_copy_template(spec);deadline=float(next(v['value'] for v in container['env'] if v['name']=='COPY_DEADLINE_EPOCH'))
   lease=publisher_writer_deadline(self.status['publisher_scope'],self.deadline,int(self.c['restore_reserve_seconds']),time.time())
   if not time.time()<deadline<=lease or deadline!=self.status['writer_deadline_epoch']:raise Refused('MAIN frozen deadline differs from actual final publisher scope')
  if gate=='COPY_CAPTURE_PHASE_READY':
   placement=self.status['capture_placement'][ns]
   if podspec['nodeSelector']!={'kubernetes.io/hostname':placement['node']} or self.get('pvc',placement['claim'],ns)['metadata']['uid']!=placement['claim_uid']:raise Refused('stopped source PVC/node binding differs')
  self.guard_lease(self.lock_ready);self.save()
  self.run(['kubectl','create','-f','-'],input_text=json.dumps(spec))
  # UID-null intent was durable before create. Watch covers create/observe death.
  self.helper('observe','--namespace',ns,'--name',name)
  actual=self.row((ns,name));ref=next(r for r in self.jobs if (r['namespace'],r['name'])==(ns,name));ref['uid']=actual['uid']
  if writer:self.status.update(writer_job=ref,writer_created=True)
  self.save();return ref
 def fetch(self,key,name,cap):
  if name not in {'app-capture.json','library.json','permissions.json','copy-proof.json','ll-sql.jsonl','lazylibrarian.db','lazylibrarian.db-wal','lazylibrarian.db-shm','kavita.db','kavita.db-wal','kavita.db-shm'}:raise Refused('source transport path outside fixed private files')
  pod=self.actual_pod(key);row=self.row(key);container=row['ready_manifest']['spec']['template']['spec']['containers'][0]['name'];directory=self.out/key[1];directory.mkdir(mode=0o700,exist_ok=True);target=directory/name
  deadline=min(self.deadline-int(self.c['restore_reserve_seconds']),time.time()+10)
  if key==self.checkpoint.SOURCE:
   program="import os,signal,stat,sys,time;signal.signal(signal.SIGALRM,lambda *_:sys.exit(2));signal.setitimer(signal.ITIMER_REAL,max(.001,float(sys.argv[3])-time.time()));fd=os.open('/tmp/'+sys.argv[1],os.O_RDONLY|os.O_NOFOLLOW);a=os.fstat(fd);assert stat.S_ISREG(a.st_mode) and a.st_nlink==1 and 0<a.st_size<=int(sys.argv[2]);f=os.fdopen(fd,'rb');raw=f.read(int(sys.argv[2])+1);b=os.fstat(f.fileno());assert len(raw)==a.st_size and (a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns)==(b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns,b.st_ctime_ns);sys.stdout.buffer.write(raw)"
   argv=['nice','-n','19','python','-c',program,name,str(cap),f'{deadline:.6f}']
  else:
   program="const f=require('node:fs'),s=require('node:stream');const [name,cap,deadline]=process.argv.slice(1);setTimeout(()=>process.exit(2),Math.max(1,Number(deadline)*1000-Date.now())).unref();const fd=f.openSync('/tmp/'+name,f.constants.O_RDONLY|f.constants.O_NOFOLLOW);const a=f.fstatSync(fd,{bigint:true});if(!a.isFile()||a.nlink!==1n||a.size<1n||a.size>BigInt(cap))process.exit(2);const r=f.createReadStream(null,{fd,autoClose:false});r.on('end',()=>{const b=f.fstatSync(fd,{bigint:true});for(const k of ['dev','ino','size','mtimeNs','ctimeNs'])if(a[k]!==b[k])process.exit(2);f.closeSync(fd);});r.pipe(process.stdout);"
   argv=['nice','-n','19','node','-e',program,name,str(cap),f'{deadline:.6f}']
  self.run_monitored(['kubectl','exec','-n',key[0],pod['metadata']['name'],'-c',container,'--',*argv],10,require_lock=self.lock_ready,output_path=target)
  if target.stat().st_size>cap:raise Refused('complete source stream exceeds byte cap')
  self.actual_pod(key);return {'path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
 def wait_source_census(self):
  while not self.source_ready:self.guard();time.sleep(.1)
  self.status['source_stat_census']=self.fetch(self.checkpoint.SOURCE,'library.json',32*1024*1024);self.status['source_permissions']=self.fetch(self.checkpoint.SOURCE,'permissions.json',1024*1024);self.save()
 def capture_vendor(self,key,ready):
  self.create_job(ready);pod=self.wait_pod(key);row=self.row(key);container=row['ready_manifest']['spec']['template']['spec']['containers'][0]['name']
  before=native_reader_identity(key,row,pod);before_path=self.out/(key[1]+'-native-before-fetch.json');private_json(before_path,pod)
  until=time.monotonic()+25
  while True:
   self.guard();events=self.checkpoint.owned_log_events(key[0],pod['metadata']['name'],container)
   refused=[e for e in events if e.get('type')=='capture-refused']
   if refused:
    self.vendor_refusal=vendor_refusal_event(refused[-1],key,row['uid'],pod['metadata']['uid'],self.status['phase_token'])
    raise Refused('stopped vendor source refused')
   found=[e for e in events if e.get('type')=='stopped-sqlite-capture-ready']
   if found:break
   if time.monotonic()>=until:raise Refused('stopped vendor capture readiness deadline')
   time.sleep(.2)
  proof=self.fetch(key,'copy-proof.json',4*1024*1024);value=json.loads(Path(proof['path']).read_bytes())
  if value.get('phase_token')!=self.status['phase_token'] or value.get('job_uid')!=row['uid'] or value.get('pod_uid')!=pod['metadata']['uid'] or value.get('readOnlySource') is not True or value.get('sourceWrites')!=0:raise Refused('vendor source proof ownership differs')
  files={}
  for item in value['files']:
   artifact=self.fetch(key,item['name'],512*1024*1024)
   if artifact['sha256']!=item['sha256'] or Path(artifact['path']).stat().st_size!=item['size']:raise Refused('full vendor file differs from actual capture')
   files[item['name']]=artifact
  result={'proof':proof,'files':files}
  if key[0]=='downloads':result['sql']=self.fetch(key,'ll-sql.jsonl',32*1024*1024)
  else:
   directory=Path(files['kavita.db']['path']).parent;db=files['kavita.db']['path'];target=json.loads(Path(self.c['selection_approval']['path']).read_bytes())
   approval=directory/'locks-selection-provenance.json';private_json(approval,{'approval_sha256':self.c['selection_approval']['sha256'],'plan':{'targets':target['entries']}})
   for filename,arguments,output in (
    ('reading-state-readonly.py',['export','--db',db],'reading-state-all.json'),
    ('kavita-dependencies-readonly.py',['export','--db',db],'dependencies.json'),
    ('kavita-locks-readonly.py',['--db',db,'--approval',str(approval)],'saved-metadata-locks.json')):
    script=Path(self.c['kavita_exporters_dir'])/filename
    self.run_monitored(['nice','-n','19','python3',str(script),*arguments,'--out',str(directory/output)],5)
   result['raw_exports']={name:{'path':str(directory/name),'sha256':hashlib.sha256((directory/name).read_bytes()).hexdigest()} for name in ('reading-state-all.json','dependencies.json','saved-metadata-locks.json')}
  after_pod=self.actual_pod(key);after=native_reader_identity(key,row,after_pod)
  if after!=before:raise Refused('vendor native Pod/spec/image/restarts changed during copied payload collection')
  after_path=self.out/(key[1]+'-native-after-fetch.json');private_json(after_path,after_pod)
  sealed=seal_vendor_payload(key,result,row,pod['metadata']['uid'],self.status['phase_token'],self.c['ll_sql_sha256'])
  sealed.update(native_before=before,native_after=after,native_before_artifact={'path':str(before_path),'sha256':hashlib.sha256(before_path.read_bytes()).hexdigest()},native_after_artifact={'path':str(after_path),'sha256':hashlib.sha256(after_path.read_bytes()).hexdigest()},sealed_at=now())
  target=self.out/(key[1]+'-copied-payload-seal.json');private_json(target,sealed);result['payload_seal']={'path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()};self.save();return result
 def retire_vendor_readers(self,ll,kavi):
  # Complete private payloads and actual before/after native ownership are
  # durably sealed before deleting either helper. Ledger UIDs remain untouched.
  keys=[('downloads','issue831-ll-source-1009-03'),('media','issue831-kavita-source-1009-03')]
  rows=[]
  for key,result in zip(keys,(ll,kavi)):
   row=self.row(key);sealed=json.loads(pinned_artifact(result['payload_seal'],1024*1024));payload={k:v for k,v in result.items() if k!='payload_seal'}
   if sealed.get('payload')!=payload or sealed.get('complete_copied_payload') is not True or sealed.get('native_before')!=sealed.get('native_after'):raise Refused('vendor payload seal missing or changed before cleanup')
   pod=self.actual_pod(key)
   if native_reader_identity(key,row,pod)!=sealed['native_after']:raise Refused('vendor native ownership changed after sealing')
   seal_vendor_payload(key,payload,row,pod['metadata']['uid'],self.status['phase_token'],self.c['ll_sql_sha256']);rows.append(row)
  self.guard();self.stop_owned_group(rows)
  self.status['vendor_readers_retired']={'checked_at':now(),'exact_job_uids':[r['uid'] for r in rows],'job_and_all_owned_pods_absent':True,'payload_seals':[ll['payload_seal'],kavi['payload_seal']],'ledger_uids_retained':True};self.save()
 def require_retired_readers_absent(self):
  retired=self.status.get('vendor_readers_retired',{})
  if retired.get('job_and_all_owned_pods_absent') is not True:raise Refused('complete sealed SQLite readers must be retired before MAIN')
  rows=[self.row(key) for key in (('downloads','issue831-ll-source-1009-03'),('media','issue831-kavita-source-1009-03'))]
  if [r['uid'] for r in rows]!=retired.get('exact_job_uids') or any(r['uid'] is None for r in rows):raise Refused('retired reader ledger history differs')
  for row in rows:
   if self.checkpoint.lookup_job(row['namespace'],row['name']) is not None or self.owned_job_pods(self.list('pods',row['namespace']),row):raise Refused('retired reader Job or owned Pod exists before MAIN')
 def assembly(self,service,ll,kavi):
  self.guard();fence={'schema':1,'phase_token':self.status['phase_token'],'pg_backend_pid':self.lock_pid,'pg_fence_established_at':self.status['pg_fence_established_at'],
   'pg_health_at':self.status['pg_health_at'],'first_service_stop_observed_at':self.status['first_service_stop_observed_at']}
  c={'schema':1,'root_approved':True,'phase_state':self.c['copy_phase_state'],'restore_pr':self.c['restore_pr'],'phase_token':self.status['phase_token'],'library':self.status['source_stat_census'],'app_capture':self.status['app_capture'],
   'live_byte_baseline':self.c['live_byte_baseline'],'fence':fence,'service_fence':{'path':str(service),'sha256':hashlib.sha256(service.read_bytes()).hexdigest()},'publisher_scope_sha256':self.status['publisher_scope']['scope_sha256'],
   'll_sql':ll['sql'],'ll_proof':ll['proof'],'ll_sql_sha256':self.c['ll_sql_sha256'],'kavita_proof':kavi['proof'],'kavita_db':kavi['files']['kavita.db'],'kavita_files':kavi['files'],'census_holds':self.c['census_holds'],'selection_approval':self.c['selection_approval']}
  contract=self.out/'assembly-contract.json';private_json(contract,c);output=self.out/'complete-proofs'
  self.run_monitored(['nice','-n','19','python3',self.c['assembly_script'],'--contract',str(contract),'--output',str(output),'--execute'],25)
  receipt=json.loads((output/'assembly-receipt.json').read_bytes());derived=receipt.get('validated_library');library=json.loads(pinned_artifact(derived,32*1024*1024))
  if library.get('schema')!=2 or library.get('kind')!='validated_byte_census' or library.get('bound_census',{}).get('byte_baseline_sha256')!=self.c['live_byte_baseline']['sha256'] or receipt.get('fresh_sources',{}).get('library')!=self.status['source_stat_census']:raise Refused('assembly derived-library artifact or raw stat handoff differs')
  self.status['library']=derived;self.c['proof_files']={key:item['path'] for key,item in receipt['proof_files'].items()};self.status['assembly_receipt']=str(output/'assembly-receipt.json');self.status['selected_count']=receipt['selected_count'];self.save();return receipt
 def deliver(self,receipt,pod):
  service=self.status['first_service_stop_observed_at'];proof=Path(self.status['publisher_scope_proof'])
  c={'schema':1,'phase_state':self.c['copy_phase_state'],'restore_pr':self.c['restore_pr'],'expected_phase_token':self.status['phase_token'],
   'main_pod_name':pod['metadata']['name'],'main_pod_uid':pod['metadata']['uid'],'source_library':self.status['library'],
   'snapshot':receipt['proof_files']['snapshot.json'],'selection':receipt['proof_files']['selection.json'],'app_capture':receipt['proof_files']['app-capture.json'],
   'publisher_proof':{'path':str(proof),'sha256':hashlib.sha256(proof.read_bytes()).hexdigest()},'first_service_stop_observed_at':service}
  contract=self.out/'delivery-contract.json';private_json(contract,c);self.last_heartbeat=0;self.heartbeat();self.ledger_frozen=True
  try:
   self.status['writer_may_mutate']=True;self.status['phase']='delivering';self.save()
   self.run_monitored(['nice','-n','19','python3',self.c['delivery_script'],'--contract',str(contract),'--receipt',str(self.out/'delivery-receipt.jsonl')],12)
  finally:self.ledger_frozen=False;self.last_heartbeat=0;self.save()
 def start_main_log_drain(self,pod):
  # This independent bounded reader never edits the shared ledger. Its durable
  # prefix exists before proof delivery and continues through MAIN cleanup.
  if self.main_stream is not None:raise Refused('MAIN log stream already started; never replay')
  key=self.checkpoint.MAIN;actual=self.actual_pod(key);row=self.row(key)
  if actual['metadata']['uid']!=pod['metadata']['uid']:raise Refused('MAIN log Pod UID differs')
  self.main_raw=self.out/'main-actual-log-prefix.jsonl'
  fd=os.open(self.main_raw,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  self.main_owner={'schema':1,'phase_token':self.status['phase_token'],'namespace':key[0],'job_name':key[1],'job_uid':row['uid'],'pod_name':pod['metadata']['name'],'pod_uid':pod['metadata']['uid'],'ready_manifest_sha256':row['ready_manifest_sha256'],'started_at':now()}
  private_json(self.out/'main-log-owner.json',self.main_owner)
  self.main_stderr=open(self.out/'main-log-stream.stderr','xb');os.chmod(self.out/'main-log-stream.stderr',0o600)
  container=row['ready_manifest']['spec']['template']['spec']['containers'][0]['name']
  self.main_stream=subprocess.Popen(['kubectl','logs','--follow','-n',key[0],pod['metadata']['name'],'-c',container],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=self.main_stderr,bufsize=0,start_new_session=True)
  def drain():
   try:
    with os.fdopen(fd,'wb',buffering=0) as output:
     while True:
      line=self.main_stream.stdout.readline(65537)
      if not line:break
      with self.main_prefix_lock:
       remaining=1024*1024-self.main_prefix_bytes
       if remaining>0:
        kept=line[:remaining]
        if output.write(kept)!=len(kept):raise Refused('MAIN durable prefix short write')
        os.fsync(output.fileno());self.main_prefix_bytes+=len(kept)
      if len(line)>65536 or len(line)>remaining or not line.endswith(b'\n'):raise Refused('MAIN log byte/line/truncation cap')
      event=json.loads(line)
      if not isinstance(event,dict):raise Refused('MAIN log event must be an object')
     if self.main_stream.wait(timeout=1)!=0:raise Refused('MAIN log transport failed')
   except BaseException:self.main_log_error='bounded MAIN log stream refused'
  self.main_log_thread=threading.Thread(target=drain,daemon=True);self.main_log_thread.start()
  self.status['main_log_owner']=str(self.out/'main-log-owner.json');self.status['main_log_prefix']=str(self.main_raw);self.save()
 def retain_main_prefix(self,final=False):
  if getattr(self,'main_stream',None) is None:return
  with self.main_prefix_lock:raw=self.main_raw.read_bytes()
  if len(raw)>1024*1024:raise Refused('durable MAIN prefix cap')
  parsed=[];parse_complete=raw.endswith(b'\n') or not raw
  for line in raw.splitlines():
   try:event=json.loads(line)
   except (ValueError,UnicodeDecodeError):parse_complete=False;break
   if not isinstance(event,dict):parse_complete=False;break
   parsed.append(event)
  moved=[e for e in parsed if e.get('msg')=='epub_copy_consolidate' and e.get('result')=='moved']
  receipt={**self.main_owner,'recorded_at':now(),'prefix_path':str(self.main_raw),'prefix_raw_sha256':hashlib.sha256(raw).hexdigest(),'prefix_bytes':len(raw),'parsed_complete_prefix':parse_complete,'observed_actual_moved_events':moved,'observed_actual_moved_count':len(moved),'actual_movement_total_known':bool(self.status.get('main_complete_at')),'movement_total_unknown':not bool(self.status.get('main_complete_at')),'stream_refused':self.main_log_error,'final_after_main_absent':final}
  private_json(self.out/('main-prefix-receipt-'+uuid.uuid4().hex+'.json'),receipt)
  self.status['actual_main_observed_moved_count']=len(moved);self.status['actual_movement_total_known']=receipt['actual_movement_total_known'];self.save()
 def stop_main_log_drain(self):
  if getattr(self,'main_stream',None) is None:return
  # Called only after MAIN Job/Pods absence is proven; the reader had remained
  # live throughout deletion so late TERM/refusal/moved lines can be retained.
  try:self.main_stream.wait(timeout=1)
  except subprocess.TimeoutExpired:
   os.killpg(self.main_stream.pid,signal.SIGTERM)
   try:self.main_stream.wait(timeout=1)
   except subprocess.TimeoutExpired:os.killpg(self.main_stream.pid,signal.SIGKILL);self.main_stream.wait(timeout=1)
  self.main_log_thread.join(timeout=1)
  if self.main_log_thread.is_alive():self.main_log_error='MAIN log drain did not finish after absence'
  self.main_stream.stdout.close();self.main_stderr.close();self.retain_main_prefix(final=True)
 def main_complete(self,key):
  while True:
   self.guard();row=self.row(key);job=self.get('job',key[1],key[0]);conditions=job.get('status',{}).get('conditions',[])
   if any(x.get('type')=='Failed' and x.get('status')=='True' for x in conditions) or job.get('status',{}).get('failed'):raise Refused('MAIN failed; preserve actual partial receipts and restore first')
   completed=any(x.get('type')=='Complete' and x.get('status')=='True' for x in conditions)
   pod=self.actual_pod(key,allow_completed=completed)
   events=self.checkpoint.owned_log_events(key[0],pod['metadata']['name'],row['ready_manifest']['spec']['template']['spec']['containers'][0]['name'])
   if any(e.get('msg')=='epub_copy_writer_fence' for e in events):
    state,_=self.ledger();lease=next(l for l in state['pg_leases'] if (l['job_namespace'],l['job_name'])==key)
    if lease['backend_pid'] is None:self.helper('record-pg','--namespace',key[0],'--name',key[1],'--pod-name',pod['metadata']['name'],'--pod-uid',pod['metadata']['uid'],*(['--allow-completed-main'] if completed else []))
   if completed:
    path=self.out/'main-final-events.json';private_json(path,events)
    stage=[e for e in events if e.get('msg')=='epub_copy_stage_proof'];moved=[e for e in events if e.get('msg')=='epub_copy_consolidate' and e.get('result')=='moved'];census=[e for e in events if e.get('msg')=='epub_copy_consolidate_census']
    if len(stage)!=1 or stage[0].get('result')!='verified' or stage[0].get('stage')!='first' or len(moved)!=self.status['selected_count'] or len(census)!=1 or census[0].get('moved')!=self.status['selected_count'] or census[0].get('refused')!=0:raise Refused('MAIN completion lacks exact first-stage/moved/census proofs')
    self.status.update(main_complete_at=now(),actual_moved_count=len(moved),actual_main_events=str(path));self.save()
    assembly=json.loads(Path(self.status['assembly_receipt']).read_bytes())
    proof={'schema':1,'phase_token':self.status['phase_token'],'main_job_uid':job['metadata']['uid'],'main_pod_uid':pod['metadata']['uid'],'job':job,'pod':pod,'events':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},'selected_scope_sha256':'1754edf94c3735c5c7cf6a78d30e3bea3b110e7b48a77ef1fea6e16e91c82663','snapshot_sha256':assembly['proof_files']['snapshot.json']['sha256'],'selected_count':self.status['selected_count']}
    completed_path=self.out/'main-completion-receipt.json';private_json(completed_path,proof)
    self.status['main_completion_receipt']={'path':str(completed_path),'sha256':hashlib.sha256(completed_path.read_bytes()).hexdigest()};self.save()
    self.verify_outcome(events);return
   time.sleep(.1)
 def verify_outcome(self,events):
  # MAIN already exited successfully. Original Stop+170 remains the ceiling;
  # SOURCE owns actual SQL, walks and retained reads on its original thread.
  self.status['writer_may_mutate']=False;self.save();self.guard_lease();self.service_fence()
  key=self.checkpoint.SOURCE;pod=self.actual_pod(key);row=self.row(key);script=Path(self.c['outcome_script'])
  if hashlib.sha256(script.read_bytes()).hexdigest()!=self.c['outcome_script_sha256']:raise Refused('outcome exchange callback changed')
  completed=self.status['main_completion_receipt'];pinned_artifact(completed,16*1024*1024)
  library=json.loads(Path(self.status['library']['path']).read_bytes());receipt=json.loads(Path(self.status['assembly_receipt']).read_bytes());selection=json.loads(Path(receipt['proof_files']['selection.json']['path']).read_bytes())
  payload={'phase_token':self.status['phase_token'],'job_uid':row['uid'],'pod_uid':pod['metadata']['uid'],'library':library,'selection':selection,'events':events,'snapshot_sha256':receipt['proof_files']['snapshot.json']['sha256']}
  end=min(self.deadline-int(self.c['restore_reserve_seconds']),time.time()+12)
  modules={**MODULES,'book_copy_writer.py':'fbfec65f4af933da6ec9aca90536501e4514cebfb8e378584e3085a993493880','bound_census.py':'d3df953dc7105d9d2535b0d370bb53c266ab46f018beac72652f681d91d52c38'}
  request={'schema':1,'type':'copy_outcome_request','phase_token':self.status['phase_token'],'job_uid':row['uid'],'pod_uid':pod['metadata']['uid'],'runtime_module_sha256':modules,'selected_scope_sha256':'1754edf94c3735c5c7cf6a78d30e3bea3b110e7b48a77ef1fea6e16e91c82663','main_receipt_sha256':completed['sha256'],'deadline_epoch':end,'payload':payload}
  raw=json.dumps(request,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()+b'\n'
  self.status['outcome_request_sha256']=hashlib.sha256(raw).hexdigest();self.outcome_ready=None;self.save()
  output_path=self.out/'source-outcome-response.json'
  self.run_monitored(['kubectl','exec','-i','-n',key[0],pod['metadata']['name'],'-c',row['ready_manifest']['spec']['template']['spec']['containers'][0]['name'],'--','nice','-n','19','python','-c',script.read_text(),repr(end)],max(.001,end-time.time()),output_path=output_path,input_bytes=raw)
  self.guard_lease()
  while self.outcome_ready is None and time.time()<end:
   time.sleep(min(.05,max(0,end-time.time())));self.guard_lease()
  ready=self.outcome_ready
  if ready is None:raise Refused('owner outcome lacks its unique actual SOURCE ready event')
  output=pinned_artifact({'path':str(output_path),'sha256':ready['response_sha256']},16*1024*1024)
  response=json.loads(output)
  required={'schema','type','phase_token','job_uid','pod_uid','request_sha256','main_receipt_sha256','runtime_module_sha256','selected_scope_sha256','outcome','production_writes'}
  if (not isinstance(response,dict) or set(response)!=required or response.get('schema')!=1 or response.get('type')!='copy_outcome_response'
      or any(response.get(k)!=request[k] for k in ('phase_token','job_uid','pod_uid','runtime_module_sha256','selected_scope_sha256','main_receipt_sha256'))
      or response.get('request_sha256')!=self.status['outcome_request_sha256'] or response.get('production_writes')!=0):raise Refused('owner outcome response binding differs')
  final=response['outcome']
  if final.get('schema')!=2 or final.get('read_only') is not True or final.get('production_writes')!=0 or final.get('phase_token')!=self.status['phase_token'] or final.get('job_uid')!=row['uid'] or final.get('pod_uid')!=pod['metadata']['uid'] or final.get('actual_moved_count')!=self.status['selected_count'] or final.get('every_unapproved_file_unchanged') is not True:raise Refused('final readonly retained receipt incomplete')
  if time.time()>=end:raise Refused('original owner outcome deadline expired')
  pinned_artifact(completed,16*1024*1024);self.guard_lease();self.actual_pod(key)
  private_json(self.out/'actual-copy-outcome.json',final);self.status['actual_copy_outcome']=str(self.out/'actual-copy-outcome.json');self.save()
 def stop_owned_group(self,group):
  for row in group:
   obj=self.checkpoint.lookup_job(row['namespace'],row['name'])
   if obj is not None:
    meta=obj['metadata']
    if row['uid'] not in (None,meta['uid']) or meta.get('labels',{}).get(self.checkpoint.LABEL)!=row['phase_token']:raise Refused('cleanup Job owner reused')
    self.run(['kubectl','delete','--raw',f"/apis/batch/v1/namespaces/{row['namespace']}/jobs/{row['name']}",'-f','-'],timeout=5,input_text=json.dumps({'apiVersion':'v1','kind':'DeleteOptions','propagationPolicy':'Foreground','preconditions':{'uid':meta['uid']}}))
   else:
    for pod in self.owned_job_pods(self.list('pods',row['namespace']),row):self.run(['kubectl','delete','--raw',f"/api/v1/namespaces/{row['namespace']}/pods/{pod['metadata']['name']}",'-f','-'],timeout=5,input_text=json.dumps({'apiVersion':'v1','kind':'DeleteOptions','propagationPolicy':'Foreground','preconditions':{'uid':pod['metadata']['uid']}}))
  until=time.monotonic()+15
  while True:
   remaining=[]
   for row in group:
    if self.checkpoint.lookup_job(row['namespace'],row['name']) is not None or self.owned_job_pods(self.list('pods',row['namespace']),row):remaining.append(row)
   if not remaining:break
   if time.monotonic()>=until:raise Refused('exact owned Job/Pod cleanup incomplete; watcher must finish before resume')
   self.save();time.sleep(.2)
 def stop_jobs(self):
  state,_=self.ledger();self.jobs=[{'namespace':r['namespace'],'name':r['name'],'uid':r['uid'],'phase_token':r['phase_token'],'writer':r['writer']} for r in state['owned_jobs']]
  # MAIN must be absent before SOURCE can release; retained retired-reader UIDs
  # continue through the same exact name/UID/all-Pods cleanup checks.
  for group in ([r for r in self.jobs if r['writer']],[r for r in self.jobs if not r['writer']]):
   self.retain_main_prefix();self.stop_owned_group(group)
   if any(r['writer'] for r in group):self.stop_main_log_drain()
  self.status['owned_jobs_stopped_at']=now();self.save()
 @staticmethod
 def owned_job_pods(pods,row):
  owned=[];uid=row['uid']
  for pod in pods:
   meta=pod.get('metadata',{})
   owners=[owner for owner in meta.get('ownerReferences',[]) if owner.get('kind')=='Job'
           and (owner.get('name')==row['name'] or (uid is not None and owner.get('uid')==uid))]
   if not owners:continue
   if (meta.get('labels',{}).get('issue825.haynesnetwork/phase')!=row['phase_token']
       or any(owner.get('name')!=row['name'] or not owner.get('uid')
              or (uid is not None and owner.get('uid')!=uid) for owner in owners)
       or not meta.get('name') or not meta.get('uid')):raise Refused('owned Job pod identity/phase was reused')
   owned.append(pod)
  return owned
 def release_lock(self):
  # This stops only the host log reader after all worker Pods are gone. PG was
  # directly rolled back by each actual SOURCE/MAIN main process termination.
  if self.lock is not None and self.lock.poll() is None:self.lock.terminate();self.lock.wait(timeout=3)
  if hasattr(self,'source_err'):self.source_err.close()
  self.status['source_log_reader_stopped_at']=now();self.save()
 def request_restore(self):
  Path(self.c['watchdog_stop']).touch(mode=0o600);self.status['restore_requested_at']=now();self.save()
 def execute(self):
  original_error=None
  try:
   self.arm();self.wait_for_stop()
   while True:
    try:self.service_fence(wait_for_rollout=True);break
    except NotStopped:self.guard_lease(False);self.save();time.sleep(.2)
   self.native_start();self.lock_start();self.deliver_source_private_inputs();self.capture_publishers()
   paths={self.checkpoint.validate_job(json.loads(Path(p).read_bytes()),self.status['phase_token']):p for p in self.c['readonly_capture_jobs']}
   llkey=('downloads','issue831-ll-source-1009-03');kvkey=('media','issue831-kavita-source-1009-03')
   ll=self.capture_vendor(llkey,self.bind_reader(paths[llkey]));kavi=self.capture_vendor(kvkey,self.bind_reader(paths[kvkey]));self.wait_source_census();self.retire_vendor_readers(ll,kavi)
   # The final actual scope precedes service receipt, assembly, binding and MAIN.
   service=self.capture_publishers();receipt=self.assembly(service,ll,kavi)
   if not receipt['selected_count']:raise Refused('zero selected eligible copies; restore without MAIN')
   ready=self.bind_writer(self.c['copy_job']);self.create_job(ready,writer=True);pod=self.wait_pod(self.checkpoint.MAIN,seconds=10)
   self.start_main_log_drain(pod);self.deliver(receipt,pod);self.main_complete(self.checkpoint.MAIN);self.status['phase']='restoring';self.save()
  except BaseException as error:
   original_error=error
   self.status.update(phase='aborting',error_class=type(error).__name__,refusal=str(error) if isinstance(error,Refused) else None);self.save()
  finally:
   self.status['phase']='restoring';self.save()
   try:self.stop_jobs();self.release_lock()
   except BaseException as error:self.status.update(cleanup_failed=True,cleanup_error_class=type(error).__name__);self.save()
   self.request_restore()
  # Optional private fsync starts only after the independent recovery request.
  # Delayed publication cannot postpone stopping Jobs/PG or requesting restore.
  if original_error is not None:
   try:
    diagnostic=exception_diagnostic(original_error,getattr(getattr(self,'publishers',None),'Refused',None))
    diagnostic.update(at=now(),phase='aborting',phase_token=self.status.get('phase_token'))
    if hasattr(self,'vendor_refusal'):diagnostic['vendor_capture']=self.vendor_refusal
    target=self.out/('private-refusal-'+uuid.uuid4().hex+'.json');private_json(target,diagnostic)
    self.status['private_refusal_diagnostic']={'path':str(target),'raw_sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
   except BaseException:self.status['private_refusal_diagnostic_failed']=True
  # Watcher independently proves all five absent, both PG names/PIDs absent,
  # restored source SHA, all controllers and all four KS before terminal success.
  self.status.update(complete=False,phase='restore_requested',restore_wait_started_at=now());self.save()
  until=time.monotonic()+300
  while time.monotonic()<until:
   watch=json.loads(Path(self.c['watchdog_state']).read_bytes())
   if watch.get('complete') is True and watch.get('recover_ks') is False and str(watch.get('restore_pr'))==str(self.c['restore_pr']) and watch.get('expected_restored_sha') and watch.get('copy_phase_cleanup_verified_at'):
    self.status.update(complete=True,phase='restored_verified',finished_at=now(),restored_sha=watch['expected_restored_sha']);self.save();return
   self.save();time.sleep(1)
  raise Refused('watcher restoration verification pending; never report complete')

def validate(c, *, live_baseline_pending=False):
 required={'repo_dir','manifest_contract','hold_receipt','restore_pr','restore_head','watchdog_state','watchdog_stop','evidence_dir','copy_phase_state','copy_checkpoint_helper','copy_checkpoint_helper_sha256','cached_source_receipt','cached_source_activation',
 'readonly_capture_jobs','copy_job','arm_deadline_seconds','max_service_absence_seconds','restore_reserve_seconds','publisher_guard','publisher_guard_sha256','publisher_scope_sha256','publisher_scope_hook','publisher_config','publisher_config_sha256',
 'assembly_script','assembly_script_sha256','delivery_script','delivery_script_sha256','outcome_script','outcome_script_sha256','kavita_exporters_dir','kavita_exporter_sha256','live_byte_baseline','source_private_input','selection_approval','census_holds','ll_sql_sha256'}
 optional={'review_disposition','review_operation'}
 if set(c) not in (required,required|optional) or 'REPLACE' in json.dumps(c):raise Refused('exact reviewed callback configuration is required')
 if optional.issubset(c) and set(c['review_operation'])!={'source_commit','stop_pr','stop_head'}:raise Refused('review operation incomplete')
 if c['max_service_absence_seconds']!=300 or c['restore_reserve_seconds']!=130 or not 1<=c['arm_deadline_seconds']<=(1800 if c['cached_source_receipt'] else 600):raise Refused('cached staging1–1800/non-cached1–600/phase300/abort170/reserve130 are fixed')
 if not re.fullmatch('[0-9a-f]{40}',c['restore_head']) or not re.fullmatch('[0-9a-f]{64}',c['publisher_scope_sha256']):raise Refused('exact inverse and scope hashes required')
 for key in ('copy_checkpoint_helper','publisher_guard','publisher_config','assembly_script','delivery_script','outcome_script'):
  if hashlib.sha256(Path(c[key]).read_bytes()).hexdigest()!=c[key+'_sha256']:raise Refused('reviewed callback component changed: '+key)
 exporters={'reading-state-readonly.py','kavita-dependencies-readonly.py','kavita-locks-readonly.py'}
 if set(c['kavita_exporter_sha256'])!=exporters:raise Refused('complete reviewed raw vendor exporters required')
 for name,digest in c['kavita_exporter_sha256'].items():
  if hashlib.sha256((Path(c['kavita_exporters_dir'])/name).read_bytes()).hexdigest()!=digest:raise Refused('raw vendor exporter pin changed')
 for key in ('manifest_contract','hold_receipt','live_byte_baseline','selection_approval','census_holds','cached_source_receipt'):
  if key=='live_byte_baseline' and live_baseline_pending:
   if not isinstance(c[key],dict) or set(c[key])!={'path','sha256'} or c[key]['sha256'] is not None or not Path(c[key]['path']).is_absolute() or os.path.lexists(c[key]['path']):raise Refused('only the absent unbound future LIVE baseline may be deferred')
   continue
  if set(c[key])!={'path','sha256'} or hashlib.sha256(Path(c[key]['path']).read_bytes()).hexdigest()!=c[key]['sha256']:raise Refused('exact root scope artifact changed')
 activation=Path(c['cached_source_activation'])
 if not activation.is_absolute() or activation.parent!=Path(c['watchdog_state']).parent or activation.exists():raise Refused('fresh private activation path alongside watchdog required')
 inputs=c['source_private_input']
 if not isinstance(inputs,dict) or set(inputs)!={'sender','receiver','native_verifier','collector'}:raise Refused('complete exact private-input source closure required')
 pins={'sender':'571376814feebcae7986f10f2bd17f7296ab84c8402fb884dacca4537da9c2d1','receiver':'fbf7998738652db4023721731526b343faff4b2d880d24e811a63a95eaab2d3e','native_verifier':'67f40c064babee41cc7faba1b7b9541a0ffe65d4d0f137507248fdf5c9e8108f','collector':'03f6163873b75f2bf0dc6896851b9da568d5e370ef39b56983eed64def2eb5f0'}
 for key,digest in pins.items():
  if inputs[key].get('sha256')!=digest:raise Refused('private-input reviewed source pin differs')
  pinned_artifact(inputs[key],1024*1024)
 if Path(inputs['receiver']['path'])!=Path(inputs['sender']['path']).parent/'receive-private-inputs.py' or Path(inputs['collector']['path'])!=Path(inputs['native_verifier']['path']).parent/'bound_census_collectors.py':raise Refused('private-input sibling closure differs')
 for stale in ('pg_lock_program','frontend_pod','capture_hook','prepare_hook','verify_hook'):
  if stale in c:raise Refused('stale independent PG/hook route is forbidden')

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True);parser.add_argument('--execute',action='store_true');args=parser.parse_args()
 config=json.loads(Path(args.config).read_text())
 if not args.execute:print(json.dumps({'prepared_only':True,'runtime_actions':0,'config_fields':sorted(config)}))
 else:validate(config);Supervisor(config).execute()
