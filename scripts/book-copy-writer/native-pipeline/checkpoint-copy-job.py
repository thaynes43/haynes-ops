#!/usr/bin/env python3
"""COPY-only extension of frozen core helper 1ceacf5d; core file is unchanged.

Only the three proof hashes and an identical deadline env/argument may bind.
Root creates Jobs. Schema and hashes validate before every ledger mutation.
"""
import argparse, copy, datetime as dt, fcntl, hashlib, json, os, re, stat, subprocess, sys, tempfile, uuid
from pathlib import Path
LABEL = 'issue825.haynesnetwork/phase'
HEX = re.compile(r'[0-9a-f]{64}')
TOKEN = re.compile(r'[0-9a-f]{32}')
NAME = re.compile(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?')
PROFILES = {
 'STAGE_PARENT_PAUSE_PROOF': ('', {'STAGE_PARENT_PAUSE_PROOF'}),
 'NORMAL_SCAN_PHASE_READY': ('0', {'NORMAL_SCAN_PHASE_READY','RESUME_READ_ONLY_PROOF_JSON','RESUME_PROOF_SHA256','CURRENT_MUTATION_PROOF_JSON','CURRENT_MUTATION_EVIDENCE_SHA256'}),
 'FORCE_SCAN_PHASE_READY': ('0', {'FORCE_SCAN_PHASE_READY','NORMAL_SCAN_PROOF_JSON','NORMAL_PROOF_SHA256','CURRENT_MUTATION_EVIDENCE_SHA256'}),
 'COPY_PROOF_HASHES_JSON': ('{}', {'COPY_PROOF_HASHES_JSON','COPY_DEADLINE_EPOCH'}),
 'COPY_CAPTURE_PHASE_READY': ('0', {'COPY_CAPTURE_PHASE_READY','COPY_CAPTURE_DEADLINE_EPOCH','COPY_CAPTURE_FENCE_JSON'}),
 'COPY_SOURCE_PHASE_READY': ('0', {'COPY_SOURCE_PHASE_READY','COPY_SOURCE_DEADLINE_EPOCH','COPY_SOURCE_FENCE_JSON'}),
 'LIDARR_CAPTURE_PHASE_READY': ('0', {'LIDARR_CAPTURE_PHASE_READY','LIDARR_CAPTURE_DEADLINE_EPOCH'}),
}
CAPTURE_IMAGE='ghcr.io/thaynes43/haynesnetwork:v0.110.5@sha256:e264e8a63b7a6b8865bfb51ecb38c398534d492ba64c6956960dedd8e6e64c6a'
CAPTURE_PROGRAM_SHA256='de31b97670ba392c0f66fa3c22cd3d73855b4621cc657e2d4d873656f5885cde'
CAPTURE_FENCE_KEYS={'schema','phase_token','claim_uid','node','first_service_stop_observed_at','pg_backend_pid','pg_fence_established_at','pg_health_at','service_fence_checked_at','share_tables','read_only'}
COPY_IMAGE='ghcr.io/thaynes43/book-copy-writer@sha256:fdc358fcce883a198a710f9415a95e5200b39499a26ab540a9863043a8b2f86c'
COPY_COMMAND=['nice','-n','19','python','/copy-writer/book_copy_writer.py']
COPY_FILES={'snapshot.json','selection.json','app-capture.json'}
STATE_KEYS = {'schema','restore_pr','phase_token','created_at','heartbeat','heartbeat_required','complete','owned_jobs','window_started_at','pg_leases'}
SOURCE = ('frontend','issue831-copy-source-census-1009-03')
MAIN = ('frontend','issue831-copy-selected-1009-03')
INTENTS = {SOURCE:'COPY_SOURCE_PHASE_READY',MAIN:'COPY_PROOF_HASHES_JSON',('downloads','issue831-ll-source-1009-03'):'COPY_CAPTURE_PHASE_READY',('media','issue831-kavita-source-1009-03'):'COPY_CAPTURE_PHASE_READY',('media','issue831-lidarr-source-1009-03'):'LIDARR_CAPTURE_PHASE_READY'}
LEASE_KEYS = {'job_namespace','job_name','job_uid','pod_uid','backend_pid','application_name'}
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
FROZEN_TEMPLATES = {
 'COPY_SOURCE_PHASE_READY': ('pg-nfs-readonly-source-template.prepared.json','e893ddc8b30aa5d8414d67e5e625ed0f23b7645c38f0bc414242dd4a1c4e4854'),
 'LIDARR_CAPTURE_PHASE_READY': ('lidarr-copy-source-template.prepared.json','8ce4ecfa3efbf8db2456f469d6b3645a444d8c8637da6bc94e2a0b1e668e9ee6'),
}
ROW_KEYS = {'namespace','name','uid','phase_token','writer','source_manifest_sha256','initial_manifest_sha256','initial_manifest','gate_env','mutable_env','ready_manifest_sha256','ready_manifest','registered_at','bound_at','observed_at'}
class Refused(RuntimeError): pass
def stamp(): return dt.datetime.now(dt.timezone.utc).isoformat()
def encoded(row): return (json.dumps(row,indent=2)+'\n').encode()
def digest(raw): return hashlib.sha256(raw).hexdigest()
def pairs(rows):
 out={}
 for key,value in rows:
  if key in out: raise Refused('duplicate JSON key')
  out[key]=value
 return out
def read_json(path):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as handle:
  info=os.fstat(handle.fileno())
  if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>4*1024*1024: raise Refused('JSON input must be regular, singly linked and below 4 MiB')
  raw=handle.read()
 return json.loads(raw,object_pairs_hook=pairs),digest(raw)
def sync_directory(path):
 fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try: os.fsync(fd)
 finally: os.close(fd)
def save(path,row):
 with tempfile.NamedTemporaryFile(dir=path.parent,prefix=path.name+'.',delete=False) as handle:
  temporary=Path(handle.name)
  try:
   os.fchmod(handle.fileno(),0o600);handle.write(encoded(row));handle.flush();os.fsync(handle.fileno())
   os.replace(temporary,path);sync_directory(path.parent)
  finally: temporary.unlink(missing_ok=True)
def publish(path,row):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as handle: handle.write(encoded(row));handle.flush();os.fsync(handle.fileno())
 sync_directory(path.parent)
def timestamp(value):
 if not isinstance(value,str): raise Refused('ledger timestamp must be ISO UTC')
 parsed=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
 if parsed.tzinfo is None or parsed.utcoffset()!=dt.timedelta(0): raise Refused('ledger timestamp must have UTC timezone')
def validate_job(job,token=None):
 if not isinstance(job,dict) or job.get('apiVersion')!='batch/v1' or job.get('kind')!='Job': raise Refused('only batch/v1 Job manifests are accepted')
 meta=job.get('metadata',{});ns,name=meta.get('namespace'),meta.get('name')
 if ns not in {'frontend','downloads','media'} or not isinstance(name,str) or len(name)>63 or not NAME.fullmatch(name): raise Refused('explicit Job namespace/name is invalid')
 if (ns,name) not in INTENTS:raise Refused('Job is outside the exact five COPY intents')
 if any(key in meta for key in ('uid','resourceVersion','generateName','creationTimestamp','managedFields','ownerReferences')): raise Refused('source must be uncreated and have no server ownership fields')
 if 'status' in job and job['status']!={}:raise Refused('source Job must have no populated server status')
 pod=job.get('spec',{}).get('template',{})
 if token is not None and (meta.get('labels',{}).get(LABEL)!=token or pod.get('metadata',{}).get('labels',{}).get(LABEL)!=token): raise Refused('Job and pod phase labels must match ledger token')
 containers=pod.get('spec',{}).get('containers')
 if not isinstance(containers,list) or not containers: raise Refused('Job containers are missing')
 names=set()
 for container in containers:
  name=container.get('name')
  if not isinstance(name,str) or name in names: raise Refused('container names must be explicit and unique')
  names.add(name);env_names=set()
  for variable in container.get('env',[]):
   key=variable.get('name')
   if not isinstance(key,str) or key in env_names: raise Refused('environment names must be explicit and unique')
   env_names.add(key)
   if ('value' in variable)==('valueFrom' in variable): raise Refused('environment needs exactly one value source')
   if 'value' in variable and not isinstance(variable['value'],str): raise Refused('literal environment values must be strings')
 return ns,meta['name']
def profile(job):
 gates=[]
 for index,container in enumerate(job['spec']['template']['spec']['containers']):
  env={row['name']:row for row in container.get('env',[])}
  gates.extend((index,env,gate) for gate in PROFILES if gate in env)
 if not gates:return None,[],None
 if len(gates)!=1:raise Refused('exactly one recognized phase gate is allowed')
 index,env,gate=gates[0];_blocked,allowed=PROFILES[gate]
 if not allowed.issubset(env) or any('value' not in env[key] for key in allowed):raise Refused('phase gate/proof environment is incomplete or indirect')
 if gate=='COPY_PROOF_HASHES_JSON':validate_copy_template(job)
 if gate=='COPY_CAPTURE_PHASE_READY':validate_capture_template(job)
 if gate in FROZEN_TEMPLATES:validate_frozen_template(job,gate)
 if INTENTS.get((job['metadata']['namespace'],job['metadata']['name']))!=gate:raise Refused('COPY needs only its five exact reviewed identities and gate profiles')
 return gate,sorted(allowed),index
def validate_frozen_template(job,gate):
 filename,expected_sha=FROZEN_TEMPLATES[gate]
 expected,sha=read_json(Path(__file__).parent/filename)
 if sha!=expected_sha:raise Refused('frozen source template pin differs')
 actual=copy.deepcopy(job)
 for value in (expected,actual):
  for meta in (value['metadata'],value['spec']['template']['metadata']):
   meta.get('labels',{}).pop(LABEL,None)
   if meta.get('labels')=={}:meta.pop('labels')
  for c in value['spec']['template']['spec']['containers']:
   for env in c.get('env',[]):
    if env['name'] in PROFILES[gate][1]:env['value']='<bound proof>'
 if not exact_json(expected,actual):raise Refused('source template executable, identity, image, mounts or privileges differ')
def lease_names(token):return {SOURCE:'issue825-duplicate-share-fence-'+token,MAIN:'issue831-manual-copy-writer'}
def validate_leases(state,require_all=False):
 names=lease_names(state['phase_token']);jobs={(r['namespace'],r['name']):r for r in state['owned_jobs']}
 if require_all and set(jobs)!=set(INTENTS):raise Refused('COPY window needs all five exact registered intents')
 leases=state.get('pg_leases');seen=set()
 if not isinstance(leases,list) or len(leases)!=2:raise Refused('COPY needs both exact PG lease owners')
 def uid(v):return v is None or (isinstance(v,str) and UUID.fullmatch(v))
 for lease in leases:
  if not isinstance(lease,dict) or set(lease)!=LEASE_KEYS:raise Refused('PG lease fields are missing or unknown')
  key=(lease['job_namespace'],lease['job_name']);pid=lease['backend_pid']
  if (key not in names or key in seen or lease['application_name']!=names[key]
      or not uid(lease['job_uid']) or not uid(lease['pod_uid'])
      or lease['job_uid']!=(jobs[key]['uid'] if key in jobs else None)
      or (lease['pod_uid'] is not None and lease['job_uid'] is None)
      or (pid is not None and (type(pid) is not int or not 0<pid<=2147483647 or lease['pod_uid'] is None or lease['job_uid'] is None))):raise Refused('PG lease owner, phase application name, UID or actual backend differs')
  seen.add(key)
def validate_capture_template(job):
 pod=job['spec']['template']['spec'];containers=pod['containers']
 if len(containers)!=1:raise Refused('capture needs exactly one main reader')
 c=containers[0];env={row['name']:row for row in c.get('env',[])};kind=env.get('COPY_CAPTURE_KIND',{}).get('value')
 if kind not in ('ll','kavita'):raise Refused('capture kind is unknown')
 ns,claim,node,mount,executable=('downloads','lazylibrarian','talosw01','/config','tsx') if kind=='ll' else ('media','kavita','talosw01','/kavita/config','node')
 if (job['metadata']['namespace']!=ns or pod.get('initContainers') or pod.get('ephemeralContainers')
     or pod.get('restartPolicy')!='Never' or pod.get('automountServiceAccountToken') is not False
     or pod.get('nodeSelector')!={'kubernetes.io/hostname':node} or pod.get('nodeName')
     or job['spec'].get('backoffLimit')!=0 or job['spec'].get('activeDeadlineSeconds')!=250):raise Refused('capture worker placement or bounded lifetime differs')
 if pod.get('securityContext')!={'runAsUser':1000,'runAsGroup':1000,'runAsNonRoot':True,'fsGroup':1000,'seccompProfile':{'type':'RuntimeDefault'}}:raise Refused('capture process UID/GID differs')
 if c.get('securityContext')!={'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}}:raise Refused('capture privileges differ')
 if pod.get('volumes')!=[{'name':'config','persistentVolumeClaim':{'claimName':claim,'readOnly':True}},{'name':'tmp','emptyDir':{'sizeLimit':'1Gi'}}]:raise Refused('capture PVC scope differs')
 if c.get('volumeMounts')!=[{'name':'config','mountPath':mount,'readOnly':True},{'name':'tmp','mountPath':'/tmp','readOnly':False}]:raise Refused('capture source must be read-only with only private writable tmp')
 command=c.get('command',[])
 if (c.get('image')!=CAPTURE_IMAGE or len(command)!=6 or command[:5]!=['nice','-n','19',executable,'--eval']
     or digest(command[5].encode())!=CAPTURE_PROGRAM_SHA256 or c.get('args') or c.get('envFrom')
     or str(c.get('resources',{}).get('limits',{}).get('cpu'))!='1'):raise Refused('capture image or exact main program differs')
 for key,value in {'COPY_CAPTURE_KIND':kind,'COPY_CAPTURE_IMAGE':CAPTURE_IMAGE}.items():
  if env.get(key)!={'name':key,'value':value}:raise Refused('capture literal environment differs')
 for key,field in [('COPY_PHASE_TOKEN',"metadata.labels['"+LABEL+"']"),('COPY_JOB_UID',"metadata.labels['batch.kubernetes.io/controller-uid']"),('COPY_POD_UID','metadata.uid'),('COPY_CAPTURE_NODE','spec.nodeName')]:
  if env.get(key)!={'name':key,'valueFrom':{'fieldRef':{'apiVersion':'v1','fieldPath':field}}}:raise Refused('capture identity must use exact Downward API fields')
 if set(env)!={'COPY_CAPTURE_KIND','COPY_CAPTURE_IMAGE','COPY_PHASE_TOKEN','COPY_JOB_UID','COPY_POD_UID','COPY_CAPTURE_NODE','COPY_CAPTURE_PHASE_READY','COPY_CAPTURE_DEADLINE_EPOCH','COPY_CAPTURE_FENCE_JSON'}:raise Refused('capture has additional environment scope')
 if env['COPY_CAPTURE_PHASE_READY'].get('value')=='0':
  if env['COPY_CAPTURE_DEADLINE_EPOCH'].get('value')!='0' or env['COPY_CAPTURE_FENCE_JSON'].get('value')!='{}':raise Refused('initial capture gates must all be closed')
 else:
  fence=json.loads(env['COPY_CAPTURE_FENCE_JSON']['value'],object_pairs_hook=pairs)
  if fence['node']!=node or fence['phase_token']!=job['metadata']['labels'][LABEL]:raise Refused('capture fence belongs to another placement or phase')
def validate_copy_template(job):
 pod=job['spec']['template']['spec'];containers=pod['containers']
 if (job['metadata']['namespace']!='frontend' or len(containers)!=1 or pod.get('initContainers')
     or pod.get('ephemeralContainers') or pod.get('restartPolicy')!='Never'
     or pod.get('automountServiceAccountToken') is not False or pod.get('nodeName')!='talosw01'
     or job['spec'].get('backoffLimit')!=0
     or type(job['spec'].get('activeDeadlineSeconds')) is not int
     or not 1<=job['spec']['activeDeadlineSeconds']<=250):raise Refused('copy intent must be one pinned, bounded non-restarting worker')
 container=containers[0];env={r['name']:r for r in container.get('env',[])}
 if pod.get('securityContext')!={'runAsUser':1000,'runAsGroup':1000,'runAsNonRoot':True,'fsGroup':1000,'seccompProfile':{'type':'RuntimeDefault'}}:raise Refused('copy process must use the proved UID/GID and runtime security context')
 if container.get('securityContext')!={'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}}:raise Refused('copy container privilege scope differs')
 if pod.get('volumes')!=[{'name':'books','nfs':{'server':'gasha01.haynesnetwork','path':'/hdd-nfs-repl'}},{'name':'tmp','emptyDir':{'sizeLimit':'64Mi'}}]:raise Refused('copy intent physical mounts differ')
 if container.get('volumeMounts')!=[{'name':'books','mountPath':'/data/cephfs-hdd','readOnly':False},{'name':'tmp','mountPath':'/tmp','readOnly':False}]:raise Refused('copy intent exposes additional writable mounts')
 if (container.get('image')!=COPY_IMAGE or container.get('command')!=COPY_COMMAND
     or container.get('args')!=['--bound-census','--wait-proofs','--deadline-epoch',env['COPY_DEADLINE_EPOCH']['value']]
     or str(container.get('resources',{}).get('limits',{}).get('cpu'))!='1'
     or container.get('envFrom')):raise Refused('copy intent image, main program or bounded environment differs')
 literals={'EBOOK_ROOT':'/data/cephfs-hdd/data/media/books/EBooks',
           'STATE_DIR':'/data/cephfs-hdd/data/media/books/.epub-convert','STRIP_SERIES_METADATA':'0','DRY_RUN':'0'}
 for key,value in literals.items():
  if env.get(key)!={'name':key,'value':value}:raise Refused('copy intent must preserve the exact manual scope')
 if env.get('DATABASE_URL')!={'name':'DATABASE_URL','valueFrom':{'secretKeyRef':{'name':'haynesnetwork-secret','key':'DATABASE_URL'}}}:raise Refused('copy intent database credential source differs')
 for key,field in [('COPY_PHASE_TOKEN',"metadata.labels['"+LABEL+"']"),('COPY_JOB_UID',"metadata.labels['batch.kubernetes.io/controller-uid']"),('COPY_POD_UID','metadata.uid')]:
  if env.get(key)!={'name':key,'valueFrom':{'fieldRef':{'apiVersion':'v1','fieldPath':field}}}:raise Refused('copy identity must use the exact Downward API fields')
 holds=env.get('LIBRARY_HOLD_FOLDERS_JSON',{})
 if 'value' not in holds or json.loads(holds['value'],object_pairs_hook=pairs)!=['Daniel Silva/Ransom']:raise Refused('copy intent must preserve the exact Ransom hold')
 if set(env)!=set(literals)|{'DATABASE_URL','COPY_PHASE_TOKEN','COPY_JOB_UID','COPY_POD_UID','LIBRARY_HOLD_FOLDERS_JSON','COPY_PROOF_HASHES_JSON','COPY_DEADLINE_EPOCH'}:raise Refused('copy intent has additional environment scope')
def ensure_env_only(initial,ready,allowed):
 left,right=copy.deepcopy(initial),copy.deepcopy(ready)
 for job in (left,right):
  for container in job['spec']['template']['spec']['containers']:
   if 'COPY_DEADLINE_EPOCH' in allowed:
    env={row['name']:row.get('value') for row in container.get('env',[])}
    if container.get('command')!=COPY_COMMAND or container.get('args')!=['--bound-census','--wait-proofs','--deadline-epoch',env['COPY_DEADLINE_EPOCH']]:raise Refused('copy deadline argument differs from its exact environment')
    container['args'][-1]='<bound deadline>'
   for row in container.get('env',[]):
    if row['name'] in allowed:row['value']='<bound proof>'
 if left!=right:raise Refused('binding changed an immutable manifest field')
def validate_values(values,gate,allowed):
 if not isinstance(values,dict) or set(values)!=set(allowed) or any(not isinstance(value,str) or not value or value=='null' for value in values.values()):raise Refused('binding needs exactly approved proof/gate string values')
 if gate.endswith('_PHASE_READY') and values[gate]!='1':raise Refused('bound scan phase gate must be 1')
 if gate=='COPY_PROOF_HASHES_JSON':
  hashes=json.loads(values[gate],object_pairs_hook=pairs)
  if not isinstance(hashes,dict) or set(hashes)!=COPY_FILES or any(not isinstance(v,str) or not HEX.fullmatch(v) for v in hashes.values()):raise Refused('copy binding requires all three exact proof hashes')
  if not re.fullmatch(r'[1-9][0-9]{9}(?:\.[0-9]{1,6})?',values['COPY_DEADLINE_EPOCH']):raise Refused('copy deadline must be a finite absolute epoch')
 if gate=='COPY_CAPTURE_PHASE_READY':
  if not re.fullmatch(r'[1-9][0-9]{9}(?:\.[0-9]{1,6})?',values['COPY_CAPTURE_DEADLINE_EPOCH']):raise Refused('capture deadline must be a finite absolute epoch')
  fence=json.loads(values['COPY_CAPTURE_FENCE_JSON'],object_pairs_hook=pairs)
  if (set(fence)!=CAPTURE_FENCE_KEYS or fence['schema']!=1 or fence['read_only'] is not True
      or type(fence['pg_backend_pid']) is not int or fence['pg_backend_pid']<=0
      or fence['share_tables']!=['book_requests','books_items']
      or not TOKEN.fullmatch(fence['phase_token']) or not isinstance(fence['claim_uid'],str) or not fence['claim_uid']
      or fence['node'] not in ('talosw01','talosw02')):raise Refused('capture binding needs the complete real-fence schema')
  for key in ('first_service_stop_observed_at','pg_fence_established_at','pg_health_at','service_fence_checked_at'):timestamp(fence[key])
 if gate in ('COPY_SOURCE_PHASE_READY','LIDARR_CAPTURE_PHASE_READY'):
  deadline_key='COPY_SOURCE_DEADLINE_EPOCH' if gate=='COPY_SOURCE_PHASE_READY' else 'LIDARR_CAPTURE_DEADLINE_EPOCH'
  if not re.fullmatch(r'[1-9][0-9]{9}(?:\.[0-9]{1,6})?',values[deadline_key]):raise Refused('source deadline must be finite absolute epoch')
  if gate=='COPY_SOURCE_PHASE_READY':
   fence=json.loads(values['COPY_SOURCE_FENCE_JSON'],object_pairs_hook=pairs)
   if (set(fence)!={'schema','phase_token','first_service_stop_observed_at','service_fence_checked_at','publisher_scope_sha256'}
       or type(fence['schema']) is not int or fence['schema']!=1 or not isinstance(fence['phase_token'],str)
       or not TOKEN.fullmatch(fence['phase_token']) or not isinstance(fence['publisher_scope_sha256'],str)
       or not HEX.fullmatch(fence['publisher_scope_sha256'])):raise Refused('source needs complete actual service/publisher fence')
   timestamp(fence['first_service_stop_observed_at']);timestamp(fence['service_fence_checked_at'])
 for key,value in values.items():
  if key.endswith('_SHA256') and not HEX.fullmatch(value):raise Refused('bound proof hash is invalid')
  if key.endswith('_JSON') and not isinstance(json.loads(value,object_pairs_hook=pairs),dict):raise Refused('bound proof JSON must be an object')
def validate_state(state,restore_pr):
 try:
  required=STATE_KEYS-{'window_started_at'}
  if not isinstance(state,dict) or not required.issubset(state) or set(state)-STATE_KEYS:raise Refused('missing or unknown ledger fields; use init on a fresh ledger path')
  if type(state['schema']) is not int or state['schema']!=1:raise Refused('unsupported ledger schema')
  if not isinstance(state['restore_pr'],str) or not re.fullmatch(r'[1-9][0-9]*',state['restore_pr']) or state['restore_pr']!=str(restore_pr):raise Refused('ledger belongs to a different inverse PR')
  if not isinstance(state['phase_token'],str) or not TOKEN.fullmatch(state['phase_token']):raise Refused('phase_token must be initialized by init')
  if state['heartbeat_required'] is not True or type(state['complete']) is not bool or not isinstance(state['owned_jobs'],list):raise Refused('COPY needs required heartbeat, valid completion flag and owned_jobs')
  for key in ('created_at','heartbeat','window_started_at'):
   if key in state:timestamp(state[key])
  seen=set()
  for row in state['owned_jobs']:
   required_row=ROW_KEYS-{'bound_at','observed_at'}
   if not isinstance(row,dict) or not required_row.issubset(row) or set(row)-ROW_KEYS:raise Refused('missing or unknown owned Job fields')
   identity=validate_job(row['initial_manifest'],state['phase_token'])
   if identity!=(row['namespace'],row['name']) or identity in seen or row['phase_token']!=state['phase_token']:raise Refused('owned Job identity or phase token differs')
   seen.add(identity)
   if type(row['writer']) is not bool or (row['uid'] is not None and (not isinstance(row['uid'],str) or not UUID.fullmatch(row['uid']))):raise Refused('owned Job writer flag or UID is invalid')
   for key in ('source_manifest_sha256','initial_manifest_sha256'):
    if not isinstance(row[key],str) or not HEX.fullmatch(row[key]):raise Refused('owned manifest hash is missing')
   if digest(encoded(row['initial_manifest']))!=row['initial_manifest_sha256']:raise Refused('initial manifest hash differs')
   gate,allowed,index=profile(row['initial_manifest'])
   if row['gate_env']!=gate or row['mutable_env']!=allowed:raise Refused('mutable environment scope differs from fixed phase profile')
   if gate and row['writer']!=(gate in ('STAGE_PARENT_PAUSE_PROOF','COPY_PROOF_HASHES_JSON')):raise Refused('owned writer flag differs from phase profile')
   if gate is not None and next(v['value'] for v in row['initial_manifest']['spec']['template']['spec']['containers'][index]['env'] if v['name']==gate)!=PROFILES[gate][0]:raise Refused('initial registered gate must remain blocked')
   if gate=='COPY_PROOF_HASHES_JSON' and next(v['value'] for v in row['initial_manifest']['spec']['template']['spec']['containers'][index]['env'] if v['name']=='COPY_DEADLINE_EPOCH')!='0':raise Refused('initial copy deadline must remain closed')
   ready=row['ready_manifest']
   if ready is None:
    if row['ready_manifest_sha256'] is not None or row['uid'] is not None or gate is None:raise Refused('unbound owned Job has ready hash or UID')
   else:
    validate_job(ready,state['phase_token'])
    if not isinstance(row['ready_manifest_sha256'],str) or not HEX.fullmatch(row['ready_manifest_sha256']) or digest(encoded(ready))!=row['ready_manifest_sha256']:raise Refused('ready manifest hash differs')
    ensure_env_only(row['initial_manifest'],ready,allowed)
    if gate:
     env={v['name']:v['value'] for v in ready['spec']['template']['spec']['containers'][index]['env'] if v['name'] in allowed}
     validate_values(env,gate,allowed)
    if gate=='COPY_CAPTURE_PHASE_READY':validate_capture_template(ready)
   for key in ('registered_at','bound_at','observed_at'):
    if key in row:timestamp(row[key])
  validate_leases(state,require_all='window_started_at' in state)
 except (KeyError,TypeError,ValueError,AttributeError) as error:raise Refused('malformed ledger schema: '+type(error).__name__) from None
def lookup_job(namespace,name):
 result=subprocess.run(['kubectl','get','job',name,'-n',namespace,'--ignore-not-found','-o','json'],capture_output=True,text=True,timeout=5)
 if result.returncode:raise Refused('exact Job GET failed; absence is not proven')
 return json.loads(result.stdout) if result.stdout.strip() else None
def exact_json(left,right):
 # Python otherwise considers True == 1. Preserve every supplied JSON type.
 if type(left) is not type(right):return False
 if isinstance(left,dict):return set(left)==set(right) and all(exact_json(left[k],right[k]) for k in left)
 if isinstance(left,list):return len(left)==len(right) and all(exact_json(x,y) for x,y in zip(left,right))
 return left==right
def image_identity(value):
 # Kyverno records the verified immutable image without the optional tag.
 repository,separator,digest_part=value.partition('@')
 if not separator or not re.fullmatch(r'sha256:[0-9a-f]{64}',digest_part):raise Refused('verification annotation requires a pinned image')
 last=repository.rsplit('/',1)[-1]
 if ':' in last:repository=repository.rsplit(':',1)[0]
 return repository+'@'+digest_part
def normalize_admission(expected,actual):
 """Only observed API zero-value serialization and known admission defaults.

 Empty EnvVar.value has API default "" and omitempty serialization. This is
 limited to an identified EnvVar with exact name/order and no valueFrom. All
 workload fields, lists and supplied values remain exact. Unknown injections,
 including native sidecars and mounts, are never treated as defaults.
 """
 ready=copy.deepcopy(expected);job=copy.deepcopy(actual)
 if not isinstance(job,dict) or not isinstance(ready,dict):raise Refused('Job comparison needs complete objects')
 meta=job['metadata'];source_meta=ready['metadata'];uid=meta['uid'];name=source_meta['name']
 if not isinstance(uid,str) or not uid:raise Refused('created Job UID is missing')
 for field in ('uid','resourceVersion','generation','creationTimestamp','managedFields'):
  if field not in source_meta:meta.pop(field,None)
 if 'status' not in ready or ready['status']=={}:
  ready.pop('status',None);job.pop('status',None)
 defaults={'completionMode':'NonIndexed','completions':1,'parallelism':1,'manualSelector':False,
           'podReplacementPolicy':'TerminatingOrFailed','suspend':False}
 for key,value in defaults.items():
  if key not in ready['spec'] and key in job['spec']:
   if not exact_json(job['spec'][key],value):raise Refused('Job default changed '+key)
   del job['spec'][key]
 labels=job['spec']['template']['metadata']['labels']
 original=ready['spec']['template']['metadata']['labels']
 generated={'batch.kubernetes.io/controller-uid':uid,'controller-uid':uid,
            'batch.kubernetes.io/job-name':name,'job-name':name}
 for key,value in generated.items():
  if labels.get(key)!=value:raise Refused('generated Job controller UID/name binding differs')
  if key not in original:del labels[key]
 if 'selector' not in ready['spec']:
  if not exact_json(job['spec'].get('selector'),{'matchLabels':{'batch.kubernetes.io/controller-uid':uid}}):
   raise Refused('generated Job selector differs from actual UID')
  del job['spec']['selector']
 for group in ('containers','initContainers','ephemeralContainers'):
  source=ready['spec']['template']['spec'].get(group,[])
  admitted=job['spec']['template']['spec'].get(group,[])
  if len(source)!=len(admitted):raise Refused('admission changed container count')
  for source_container,actual_container in zip(source,admitted):
   source_env=source_container.get('env',[]);actual_env=actual_container.get('env',[])
   if len(source_env)!=len(actual_env):raise Refused('admission changed environment count')
   for source_variable,actual_variable in zip(source_env,actual_env):
    if (source_variable.get('value')=='' and 'valueFrom' not in source_variable
        and actual_variable.get('name')==source_variable.get('name')
        and 'value' not in actual_variable and 'valueFrom' not in actual_variable):
     actual_variable['value']=''
   # Kubernetes omits the false readOnly default on an identified VolumeMount.
   # Keep mount order, name/path and every other supplied field exact.
   source_mounts=source_container.get('volumeMounts',[]);actual_mounts=actual_container.get('volumeMounts',[])
   if len(source_mounts)!=len(actual_mounts):raise Refused('admission changed volume mount count')
   for source_mount,actual_mount in zip(source_mounts,actual_mounts):
    if (source_mount.get('readOnly') is False and 'readOnly' not in actual_mount
        and source_mount.get('name')==actual_mount.get('name')
        and source_mount.get('mountPath')==actual_mount.get('mountPath')):
     actual_mount['readOnly']=False
 # These two cluster admission annotations only report verification of the exact
 # pinned image. They cannot authorize or alter any executable workload field.
 annotations=meta.get('annotations',{});supplied_annotations=source_meta.get('annotations',{})
 added=set(annotations)-set(supplied_annotations)
 allowed={'kyverno.io/verify-images','kyverno.io/verify-images-scoped'}
 if added & allowed:
  images={image_identity(c['image']) for group in ('containers','initContainers')
          for c in ready['spec']['template']['spec'].get(group,[])}
  for key in added & allowed:
   value=json.loads(annotations[key],object_pairs_hook=pairs)
   if key=='kyverno.io/verify-images':
    if not value or set(value)-images or any(v!='pass' for v in value.values()):raise Refused('image verification annotation differs')
   else:
    policies=value.get('policies',{})
    if value.get('version')!=1 or set(value)!={'version','policies'} or set(policies)!={'/verify-haynesnetwork-images'}:
     raise Refused('scoped image verification policy differs')
    rules=policies['/verify-haynesnetwork-images']
    if set(rules)!={'autogen-verify-haynesnetwork'}:raise Refused('scoped image verification rule differs')
    verified=rules['autogen-verify-haynesnetwork']
    if not verified or set(verified)-images or any(v!='pass' for v in verified.values()):raise Refused('scoped image verification annotation differs')
   del annotations[key]
  if not annotations and 'annotations' not in source_meta:meta.pop('annotations',None)
 return ready,job
def declared_matches(expected,actual):
 try:
  left,right=normalize_admission(expected,actual)
  return exact_json(left,right)
 except (Refused,KeyError,TypeError,ValueError,AttributeError):return False
def lookup_pod(namespace,name):
 result=subprocess.run(['kubectl','get','pod',name,'-n',namespace,'-o','json'],capture_output=True,timeout=5)
 if result.returncode or len(result.stdout)>2*1024*1024:raise Refused('exact Pod GET failed or exceeds cap')
 return json.loads(result.stdout,object_pairs_hook=pairs)
def owned_log_events(namespace,name,container):
 result=subprocess.run(['kubectl','logs',name,'-n',namespace,'-c',container,'--limit-bytes=1048576'],capture_output=True,timeout=5)
 if result.returncode or len(result.stdout)>1024*1024:raise Refused('actual owned Pod logs failed or exceed cap')
 events=[]
 for line in result.stdout.splitlines():
  try:event=json.loads(line,object_pairs_hook=pairs)
  except ValueError:continue
  if isinstance(event,dict):events.append(event)
 return events
def verify_owned_pod(row,job,pod,pod_uid,allow_completed=False):
 ready=row['ready_manifest'];uid=row['uid'];ns,name=row['namespace'],row['name'];phase=row['phase_token']
 if not isinstance(uid,str) or not UUID.fullmatch(uid) or not isinstance(pod_uid,str) or not UUID.fullmatch(pod_uid):raise Refused('actual Job and Pod UIDs are required')
 jm=job.get('metadata',{});pm=pod.get('metadata',{});owners=pm.get('ownerReferences',[])
 if ((jm.get('namespace'),jm.get('name'),jm.get('uid'))!=(ns,name,uid) or jm.get('deletionTimestamp')
     or jm.get('labels',{}).get(LABEL)!=phase or not declared_matches(ready,job)
     or pm.get('namespace')!=ns or pm.get('uid')!=pod_uid or pm.get('deletionTimestamp')
     or pm.get('labels',{}).get(LABEL)!=phase or pm.get('labels',{}).get('batch.kubernetes.io/controller-uid')!=uid
     or len(owners)!=1 or owners[0].get('apiVersion')!='batch/v1' or owners[0].get('kind')!='Job'
     or owners[0].get('name')!=name or owners[0].get('uid')!=uid or owners[0].get('controller') is not True
     or pm.get('annotations',{}).get('k8tz.io/inject')!='false'):raise Refused('live Pod exact UID/phase/controller or k8tz opt-out differs')
 source=ready['spec']['template']['spec'];actual=copy.deepcopy(pod['spec'])
 defaults={'dnsPolicy':'ClusterFirst','schedulerName':'default-scheduler','enableServiceLinks':True,
           'terminationGracePeriodSeconds':30,'securityContext':{},'priority':0,'preemptionPolicy':'PreemptLowerPriority',
           'serviceAccountName':'default','serviceAccount':'default'}
 for key,value in defaults.items():
  if key not in source and key in actual:
   if not exact_json(actual[key],value):raise Refused('Pod admission default differs '+key)
   del actual[key]
 if not source.get('nodeName'):
  if source.get('nodeSelector')!={'kubernetes.io/hostname':actual.get('nodeName')}:raise Refused('Pod scheduling differs from exact source node')
  del actual['nodeName']
 elif actual.get('nodeName')!=source['nodeName']:raise Refused('actual Pod node differs')
 if 'tolerations' not in source and 'tolerations' in actual:
  defaults=[{'key':'node.kubernetes.io/not-ready','operator':'Exists','effect':'NoExecute','tolerationSeconds':300},{'key':'node.kubernetes.io/unreachable','operator':'Exists','effect':'NoExecute','tolerationSeconds':300}]
  if not exact_json(actual['tolerations'],defaults):raise Refused('Pod tolerations differ')
  del actual['tolerations']
 comparison=copy.deepcopy(job);comparison['spec']['template']['spec']=actual
 if not declared_matches(ready,comparison):raise Refused('actual Pod executable workload differs from durable ready intent')
 c=source['containers'][0];statuses=pod.get('status',{}).get('containerStatuses',[])
 if (len(statuses)!=1 or statuses[0].get('name')!=c['name'] or statuses[0].get('restartCount')!=0
     or image_identity(statuses[0].get('imageID','').removeprefix('docker-pullable://'))!=image_identity(c['image'])):raise Refused('actual original container or image differs')
 running=pod.get('status',{}).get('phase')=='Running' and set(statuses[0].get('state',{}))=={'running'}
 if running and not allow_completed:return
 conditions=job.get('status',{}).get('conditions',[]);terminated=statuses[0].get('state',{}).get('terminated',{})
 if (not allow_completed or (ns,name)!=MAIN or pod.get('status',{}).get('phase')!='Succeeded'
     or set(statuses[0].get('state',{}))!={'terminated'} or type(terminated.get('exitCode')) is not int
     or terminated['exitCode']!=0 or terminated.get('reason')!='Completed' or job.get('status',{}).get('active',0)!=0
     or not any(c.get('type')=='Complete' and c.get('status')=='True' for c in conditions)
     or any(c.get('type')=='Failed' and c.get('status')=='True' for c in conditions)):raise Refused('actual original container is not in the required running or completed MAIN state')
def lookup_lease_pod(row,pod_uid):
 result=subprocess.run(['kubectl','get','pods','-n',row['namespace'],'-l','batch.kubernetes.io/controller-uid='+row['uid'],'-o','json'],capture_output=True,timeout=5)
 if result.returncode or len(result.stdout)>2*1024*1024:raise Refused('exact lease Pod inventory failed or exceeds cap')
 pods=json.loads(result.stdout,object_pairs_hook=pairs).get('items',[])
 if len(pods)!=1 or pods[0].get('metadata',{}).get('uid')!=pod_uid:raise Refused('actual source lease Pod is missing, duplicated or reused')
 return pods[0]
def require_live_source_lease(state,fence):
 lease=next(l for l in state['pg_leases'] if (l['job_namespace'],l['job_name'])==SOURCE)
 if (lease['job_uid'] is None or lease['pod_uid'] is None or lease['backend_pid'] is None
     or lease['backend_pid']!=fence['pg_backend_pid']):raise Refused('reader fence must match the durably recorded actual source Job/Pod/backend')
 row=next(r for r in state['owned_jobs'] if (r['namespace'],r['name'])==SOURCE)
 pod=lookup_lease_pod(row,lease['pod_uid']);job=lookup_job(*SOURCE);verify_owned_pod(row,job,pod,lease['pod_uid'])
 events=owned_log_events(row['namespace'],pod['metadata']['name'],row['ready_manifest']['spec']['template']['spec']['containers'][0]['name'])
 health=[e for e in events if e.get('type')=='fence_healthy']
 if not health:raise Refused('source current own-backend health event missing')
 for e in health:
  if (e.get('backend_pid')!=lease['backend_pid'] or e.get('application_name')!=lease['application_name']
      or e.get('phase_token')!=state['phase_token'] or e.get('job_uid')!=lease['job_uid'] or e.get('pod_uid')!=lease['pod_uid']
      or e.get('read_only')!='on' or e.get('share_tables')!=['book_requests','books_items']):raise Refused('source actual own-backend health identity changed')
  timestamp(e.get('at'))
 current=dt.datetime.now(dt.timezone.utc).timestamp();times=[dt.datetime.fromisoformat(e['at'].replace('Z','+00:00')).timestamp() for e in health]
 provided=dt.datetime.fromisoformat(fence['pg_health_at'].replace('Z','+00:00')).timestamp()
 if not 0<=current-max(times)<=12 or provided not in times:raise Refused('reader must bind a fresh actual source health event')
def process(args,path):
 if args.command=='init':
  if path.exists():
   state,_sha=read_json(path);validate_state(state,args.restore_pr)
   if state['complete']:raise Refused('completed ledger cannot arm a new window; use fresh path')
   return {'initialized':False,'phase_token':state['phase_token'],'owned_jobs':len(state['owned_jobs']),'cluster_writes':0}
  now=stamp();token=uuid.uuid4().hex;state={'schema':1,'restore_pr':str(args.restore_pr),'phase_token':token,'created_at':now,'heartbeat':now,'heartbeat_required':True,'complete':False,'owned_jobs':[],
   'pg_leases':[{'job_namespace':key[0],'job_name':key[1],'job_uid':None,'pod_uid':None,'backend_pid':None,'application_name':name} for key,name in lease_names(token).items()]}
  validate_state(state,args.restore_pr);save(path,state)
  return {'initialized':True,'phase_token':state['phase_token'],'owned_jobs':0,'cluster_writes':0}
 if not path.exists():raise Refused('ledger does not exist; run init before register/bind/observe')
 state,_sha=read_json(path);validate_state(state,args.restore_pr)
 if state['complete']:raise Refused('completed ledger cannot authorize a Job')
 if args.command=='heartbeat':
  validate_leases(state,require_all=True)
  if 'window_started_at' not in state:raise Refused('COPY heartbeat requires actual started window')
  state['heartbeat']=stamp();validate_state(state,args.restore_pr);save(path,state)
  return {'heartbeat_durable':True,'cluster_writes':0}
 if args.command=='start-window':
  validate_leases(state,require_all=True)
  if any(row['uid'] is not None for row in state['owned_jobs']):raise Refused('window cannot start after any Job UID is bound')
  if state['heartbeat_required'] is not True:raise Refused('COPY window requires supervisor heartbeat')
  started=getattr(args,'started_at',None)
  if started is not None:
   timestamp(started);observed=dt.datetime.fromisoformat(started.replace('Z','+00:00')).timestamp()
   if not 0<=dt.datetime.now(dt.timezone.utc).timestamp()-observed<=5:raise Refused('first observed absence must be recorded immediately, never in the future')
  if 'window_started_at' in state:
   if started is not None and state['window_started_at']!=started:raise Refused('phase already records another first absence')
   return {'started':False,'window_started_at':state['window_started_at'],'cluster_writes':0}
  state['window_started_at']=started or stamp();state['heartbeat']=stamp();validate_state(state,args.restore_pr);save(path,state)
  return {'started':True,'window_started_at':state['window_started_at'],'cluster_writes':0}
 if args.command=='register':
  job,source_sha=read_json(Path(args.manifest))
  if source_sha!=args.source_sha256:raise Refused('source manifest hash differs from reviewed template')
  ns,name=validate_job(job)
  if any((row['namespace'],row['name'])==(ns,name) for row in state['owned_jobs']):raise Refused('Job already registered; use bind for proof environment')
  token=state['phase_token']
  for meta in (job['metadata'],job['spec']['template'].setdefault('metadata',{})):
   labels=meta.setdefault('labels',{})
   if LABEL in labels and labels[LABEL]!=token:raise Refused('source phase label belongs to another ledger')
   labels[LABEL]=token
  validate_job(job,token);gate,allowed,index=profile(job)
  if gate and args.writer!=(gate in ('STAGE_PARENT_PAUSE_PROOF','COPY_PROOF_HASHES_JSON')):raise Refused('writer flag must match phase profile')
  if gate=='COPY_PROOF_HASHES_JSON' and next(v['value'] for v in job['spec']['template']['spec']['containers'][index]['env'] if v['name']=='COPY_DEADLINE_EPOCH')!='0':raise Refused('copy source deadline must remain closed')
  if gate and next(row['value'] for row in job['spec']['template']['spec']['containers'][index]['env'] if row['name']==gate)!=PROFILES[gate][0]:raise Refused('register requires initially blocked phase gate')
  row={'namespace':ns,'name':name,'uid':None,'phase_token':token,'writer':args.writer,'source_manifest_sha256':source_sha,'initial_manifest_sha256':digest(encoded(job)),'initial_manifest':job,'gate_env':gate,'mutable_env':allowed,'ready_manifest_sha256':None if gate else digest(encoded(job)),'ready_manifest':None if gate else job,'registered_at':stamp()}
  output=Path(args.output)
  if output.exists() or output.is_symlink():raise Refused('output already exists; never overwrite a manifest')
  state['owned_jobs'].append(row);state['heartbeat']=stamp();validate_state(state,args.restore_pr);save(path,state);publish(output,job)
  return {'checkpointed_before_create':True,'namespace':ns,'name':name,'initial_manifest_sha256':row['initial_manifest_sha256'],'output':str(output),'cluster_writes':0}
 row=next((row for row in state['owned_jobs'] if (row['namespace'],row['name'])==(args.namespace,args.name)),None)
 if row is None:raise Refused('exact Job is not registered in this ledger')
 if args.command=='record-pg' and row['uid'] is None:raise Refused('record-pg requires actual Job UID durably observed first')
 if args.command=='bind':
  if row['uid'] is not None:raise Refused('UID is already bound; no payload change is allowed')
  if row['initial_manifest_sha256']!=args.initial_manifest_sha256:raise Refused('initial manifest hash differs from registered intent')
  if not row['gate_env']:raise Refused('Job has no supported env-only binding profile')
  values,_sha=read_json(Path(args.env_file))
  gate=row['gate_env']
  validate_values(values,gate,row['mutable_env'])
  if gate in ('COPY_PROOF_HASHES_JSON','COPY_CAPTURE_PHASE_READY','COPY_SOURCE_PHASE_READY','LIDARR_CAPTURE_PHASE_READY'):
   if 'window_started_at' not in state:raise Refused('copy proof binding requires an explicitly started phase')
   started=dt.datetime.fromisoformat(state['window_started_at'].replace('Z','+00:00')).timestamp()
   key={'COPY_PROOF_HASHES_JSON':'COPY_DEADLINE_EPOCH','COPY_CAPTURE_PHASE_READY':'COPY_CAPTURE_DEADLINE_EPOCH','COPY_SOURCE_PHASE_READY':'COPY_SOURCE_DEADLINE_EPOCH','LIDARR_CAPTURE_PHASE_READY':'LIDARR_CAPTURE_DEADLINE_EPOCH'}[gate]
   current=dt.datetime.now(dt.timezone.utc).timestamp();deadline=float(values[key])
   if not 0<deadline-current<=250 or deadline>started+250:raise Refused('copy deadline exceeds the phase abort clock')
   if gate=='LIDARR_CAPTURE_PHASE_READY' and deadline-current>115:raise Refused('native helper exceeds its 115 second lifetime')
   if gate=='COPY_SOURCE_PHASE_READY':
    fence=json.loads(values['COPY_SOURCE_FENCE_JSON'],object_pairs_hook=pairs)
    first=dt.datetime.fromisoformat(fence['first_service_stop_observed_at'].replace('Z','+00:00')).timestamp();checked=dt.datetime.fromisoformat(fence['service_fence_checked_at'].replace('Z','+00:00')).timestamp()
    if fence['phase_token']!=state['phase_token'] or first!=started or not started<=checked<=current or current-checked>65:raise Refused('source service fence is foreign or stale')
   if gate=='COPY_CAPTURE_PHASE_READY':
    fence=json.loads(values['COPY_CAPTURE_FENCE_JSON'],object_pairs_hook=pairs)
    times={key:dt.datetime.fromisoformat(fence[key].replace('Z','+00:00')).timestamp() for key in ('first_service_stop_observed_at','pg_fence_established_at','pg_health_at','service_fence_checked_at')}
    if (fence['phase_token']!=state['phase_token'] or times['first_service_stop_observed_at']!=started
        or times['pg_fence_established_at']<started or any(value>current for value in times.values())
        or current-times['pg_health_at']>12 or current-times['service_fence_checked_at']>65):raise Refused('capture fence was not established and healthy within this actual phase')
    require_live_source_lease(state,fence)
  ready=copy.deepcopy(row['initial_manifest'])
  for container in ready['spec']['template']['spec']['containers']:
   for variable in container.get('env',[]):
    if variable['name'] in values:variable['value']=values[variable['name']]
   if gate=='COPY_PROOF_HASHES_JSON':container['args'][-1]=values['COPY_DEADLINE_EPOCH']
  ensure_env_only(row['initial_manifest'],ready,row['mutable_env']);output=Path(args.output)
  if row['ready_manifest'] is not None and row['ready_manifest']!=ready:raise Refused('ready intent already bound; different proof requires a fresh owned Job')
  if output.exists() or output.is_symlink():raise Refused('ready output already exists; never overwrite a manifest')
  if lookup_job(args.namespace,args.name) is not None:raise Refused('Job exists; absence required before binding')
  row.update(ready_manifest=ready,ready_manifest_sha256=digest(encoded(ready)),bound_at=stamp());state['heartbeat']=stamp()
  validate_state(state,args.restore_pr);save(path,state);publish(output,ready)
  return {'ready_hash_durable_before_output':True,'namespace':args.namespace,'name':args.name,'ready_manifest_sha256':row['ready_manifest_sha256'],'output':str(output),'cluster_writes':0}
 job=lookup_job(args.namespace,args.name)
 if job is None:raise Refused('Job absent; UID cannot be observed')
 meta=job.get('metadata',{})
 if (meta.get('namespace'),meta.get('name'))!=(row['namespace'],row['name']) or meta.get('labels',{}).get(LABEL)!=row['phase_token'] or not isinstance(meta.get('uid'),str) or not meta['uid'] or row['uid'] not in (None,meta['uid']):raise Refused('Job identity, phase label or UID differs; never adopt reused name')
 if row['ready_manifest'] is None or not declared_matches(row['ready_manifest'],job):raise Refused('created Job differs from exact recorded ready manifest')
 row.update(uid=meta['uid'],observed_at=stamp())
 for lease in state['pg_leases']:
  if (lease['job_namespace'],lease['job_name'])==(row['namespace'],row['name']):lease['job_uid']=meta['uid']
 if args.command=='record-pg':
  lease=next((l for l in state['pg_leases'] if (l['job_namespace'],l['job_name'])==(row['namespace'],row['name'])),None)
  if lease is None:raise Refused('only actual source and MAIN own PG leases')
  completed=args.allow_completed_main
  if completed and (row['namespace'],row['name'])!=MAIN:raise Refused('completed-log binding is allowed only for MAIN')
  pod=lookup_pod(args.namespace,args.pod_name);verify_owned_pod(row,job,pod,args.pod_uid,completed)
  events=owned_log_events(args.namespace,args.pod_name,row['ready_manifest']['spec']['template']['spec']['containers'][0]['name'])
  pids=[];source_health=[]
  for event in events:
   if (row['namespace'],row['name'])==SOURCE and event.get('type')=='fence_healthy':
    if (event.get('phase_token')!=state['phase_token'] or event.get('job_uid')!=row['uid'] or event.get('pod_uid')!=args.pod_uid or event.get('application_name')!=lease['application_name'] or event.get('read_only')!='on' or event.get('share_tables')!=['book_requests','books_items']):raise Refused('source own backend event differs from current Pod')
    pids.append(event.get('backend_pid'))
    timestamp(event.get('at'));source_health.append(dt.datetime.fromisoformat(event['at'].replace('Z','+00:00')).timestamp())
   if (row['namespace'],row['name'])==MAIN and event.get('msg')=='epub_copy_writer_fence':
    if event.get('read_only')!='on' or event.get('share_tables')!=['book_requests','books_items']:raise Refused('MAIN own backend event is incomplete')
    pids.append(event.get('backend_pid'))
  if not pids or any(type(p) is not int or not 0<p<=2147483647 for p in pids) or len(set(pids))!=1:raise Refused('actual own backend log is missing or inconsistent')
  if (row['namespace'],row['name'])==SOURCE and not 0<=dt.datetime.now(dt.timezone.utc).timestamp()-max(source_health)<=12:raise Refused('source actual backend health event is stale')
  if lease['pod_uid'] not in (None,args.pod_uid) or lease['backend_pid'] not in (None,pids[0]):raise Refused('PG lease already bound to another process')
  verify_owned_pod(row,lookup_job(args.namespace,args.name),lookup_pod(args.namespace,args.pod_name),args.pod_uid,completed)
  lease.update(pod_uid=args.pod_uid,backend_pid=pids[0])
 state['heartbeat']=stamp();validate_state(state,args.restore_pr);save(path,state)
 if args.command=='record-pg':return {'pg_lease_durable':True,'namespace':args.namespace,'name':args.name,'backend_pid':lease['backend_pid'],'application_name':lease['application_name'],'cluster_writes':0}
 return {'uid_bound':True,'namespace':args.namespace,'name':args.name,'cluster_writes':0}
def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--phase-state',required=True);parser.add_argument('--restore-pr',required=True)
 commands=parser.add_subparsers(dest='command',required=True);commands.add_parser('init');commands.add_parser('heartbeat');w=commands.add_parser('start-window');w.add_argument('--started-at')
 r=commands.add_parser('register');r.add_argument('--manifest',required=True);r.add_argument('--source-sha256',required=True);r.add_argument('--output',required=True);r.add_argument('--writer',action='store_true')
 b=commands.add_parser('bind');b.add_argument('--namespace',required=True);b.add_argument('--name',required=True);b.add_argument('--initial-manifest-sha256',required=True);b.add_argument('--env-file',required=True);b.add_argument('--output',required=True)
 o=commands.add_parser('observe');o.add_argument('--namespace',required=True);o.add_argument('--name',required=True)
 p=commands.add_parser('record-pg');p.add_argument('--namespace',required=True);p.add_argument('--name',required=True);p.add_argument('--pod-name',required=True);p.add_argument('--pod-uid',required=True);p.add_argument('--allow-completed-main',action='store_true')
 args=parser.parse_args(argv)
 if not re.fullmatch(r'[1-9][0-9]*',args.restore_pr):raise Refused('restore-pr must be explicit positive PR number')
 path=Path(args.phase_state)
 if not path.is_absolute() or not path.parent.is_dir() or path.parent.is_symlink():raise Refused('ledger needs existing real absolute parent directory')
 if path.is_symlink():raise Refused('ledger must not be a symlink')
 fd=os.open(path.with_suffix(path.suffix+'.lock'),os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as lock:fcntl.flock(lock,fcntl.LOCK_EX);print(json.dumps(process(args,path)))
if __name__=='__main__':
 try:main()
 except (Refused,OSError,ValueError,KeyError,TypeError,AttributeError,subprocess.TimeoutExpired) as error:
  print(json.dumps({'result':'refused','reason':str(error),'cluster_writes':0}),file=sys.stderr);sys.exit(2)
