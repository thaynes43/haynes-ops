#!/usr/bin/env python3
"""Private intent ledger: init/register are local; bind/observe GET one exact Job.
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
}
STATE_KEYS = {'schema','restore_pr','phase_token','created_at','heartbeat','heartbeat_required','complete','owned_jobs','window_started_at'}
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
 return gate,sorted(allowed),index
def ensure_env_only(initial,ready,allowed):
 left,right=copy.deepcopy(initial),copy.deepcopy(ready)
 for job in (left,right):
  for container in job['spec']['template']['spec']['containers']:
   for row in container.get('env',[]):
    if row['name'] in allowed:row['value']='<bound proof>'
 if left!=right:raise Refused('binding changed an immutable manifest field')
def validate_values(values,gate,allowed):
 if not isinstance(values,dict) or set(values)!=set(allowed) or any(not isinstance(value,str) or not value or value=='null' for value in values.values()):raise Refused('binding needs exactly approved proof/gate string values')
 if gate.endswith('_PHASE_READY') and values[gate]!='1':raise Refused('bound scan phase gate must be 1')
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
  if type(state['heartbeat_required']) is not bool or type(state['complete']) is not bool or not isinstance(state['owned_jobs'],list):raise Refused('invalid ledger flags or owned_jobs')
  for key in ('created_at','heartbeat','window_started_at'):
   if key in state:timestamp(state[key])
  seen=set()
  for row in state['owned_jobs']:
   required_row=ROW_KEYS-{'bound_at','observed_at'}
   if not isinstance(row,dict) or not required_row.issubset(row) or set(row)-ROW_KEYS:raise Refused('missing or unknown owned Job fields')
   identity=validate_job(row['initial_manifest'],state['phase_token'])
   if identity!=(row['namespace'],row['name']) or identity in seen or row['phase_token']!=state['phase_token']:raise Refused('owned Job identity or phase token differs')
   seen.add(identity)
   if type(row['writer']) is not bool or (row['uid'] is not None and (not isinstance(row['uid'],str) or not row['uid'])):raise Refused('owned Job writer flag or UID is invalid')
   for key in ('source_manifest_sha256','initial_manifest_sha256'):
    if not isinstance(row[key],str) or not HEX.fullmatch(row[key]):raise Refused('owned manifest hash is missing')
   if digest(encoded(row['initial_manifest']))!=row['initial_manifest_sha256']:raise Refused('initial manifest hash differs')
   gate,allowed,index=profile(row['initial_manifest'])
   if row['gate_env']!=gate or row['mutable_env']!=allowed:raise Refused('mutable environment scope differs from fixed phase profile')
   if gate and row['writer']!=(gate=='STAGE_PARENT_PAUSE_PROOF'):raise Refused('owned writer flag differs from metadata/scan profile')
   if gate is not None and next(v['value'] for v in row['initial_manifest']['spec']['template']['spec']['containers'][index]['env'] if v['name']==gate)!=PROFILES[gate][0]:raise Refused('initial registered gate must remain blocked')
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
   for key in ('registered_at','bound_at','observed_at'):
    if key in row:timestamp(row[key])
 except (KeyError,TypeError,ValueError,AttributeError) as error:raise Refused('malformed ledger schema: '+type(error).__name__) from None
def lookup_job(namespace,name):
 result=subprocess.run(['kubectl','get','job',name,'-n',namespace,'--ignore-not-found','-o','json'],capture_output=True,text=True,timeout=15)
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
def process(args,path):
 if args.command=='init':
  if path.exists():
   state,_sha=read_json(path);validate_state(state,args.restore_pr)
   if state['complete']:raise Refused('completed ledger cannot arm a new window; use fresh path')
   return {'initialized':False,'phase_token':state['phase_token'],'owned_jobs':len(state['owned_jobs']),'cluster_writes':0}
  now=stamp();state={'schema':1,'restore_pr':str(args.restore_pr),'phase_token':uuid.uuid4().hex,'created_at':now,'heartbeat':now,'heartbeat_required':False,'complete':False,'owned_jobs':[]}
  validate_state(state,args.restore_pr);save(path,state)
  return {'initialized':True,'phase_token':state['phase_token'],'owned_jobs':0,'cluster_writes':0}
 if not path.exists():raise Refused('ledger does not exist; run init before register/bind/observe')
 state,_sha=read_json(path);validate_state(state,args.restore_pr)
 if state['complete']:raise Refused('completed ledger cannot authorize a Job')
 if args.command=='start-window':
  if any(row['uid'] is not None for row in state['owned_jobs']):raise Refused('window cannot start after any Job UID is bound')
  if state['heartbeat_required']:raise Refused('core start-window requires heartbeat_required false')
  if 'window_started_at' in state:return {'started':False,'window_started_at':state['window_started_at'],'cluster_writes':0}
  state['window_started_at']=stamp();state['heartbeat']=stamp();validate_state(state,args.restore_pr);save(path,state)
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
  if gate and args.writer!=(gate=='STAGE_PARENT_PAUSE_PROOF'):raise Refused('writer flag must match metadata/scan phase profile')
  if gate and next(row['value'] for row in job['spec']['template']['spec']['containers'][index]['env'] if row['name']==gate)!=PROFILES[gate][0]:raise Refused('register requires initially blocked phase gate')
  row={'namespace':ns,'name':name,'uid':None,'phase_token':token,'writer':args.writer,'source_manifest_sha256':source_sha,'initial_manifest_sha256':digest(encoded(job)),'initial_manifest':job,'gate_env':gate,'mutable_env':allowed,'ready_manifest_sha256':None if gate else digest(encoded(job)),'ready_manifest':None if gate else job,'registered_at':stamp()}
  output=Path(args.output)
  if output.exists() or output.is_symlink():raise Refused('output already exists; never overwrite a manifest')
  state['owned_jobs'].append(row);state['heartbeat']=stamp();validate_state(state,args.restore_pr);save(path,state);publish(output,job)
  return {'checkpointed_before_create':True,'namespace':ns,'name':name,'initial_manifest_sha256':row['initial_manifest_sha256'],'output':str(output),'cluster_writes':0}
 row=next((row for row in state['owned_jobs'] if (row['namespace'],row['name'])==(args.namespace,args.name)),None)
 if row is None:raise Refused('exact Job is not registered in this ledger')
 if args.command=='bind':
  if row['uid'] is not None:raise Refused('UID is already bound; no payload change is allowed')
  if row['initial_manifest_sha256']!=args.initial_manifest_sha256:raise Refused('initial manifest hash differs from registered intent')
  if not row['gate_env']:raise Refused('Job has no supported env-only binding profile')
  values,_sha=read_json(Path(args.env_file))
  gate=row['gate_env']
  validate_values(values,gate,row['mutable_env'])
  ready=copy.deepcopy(row['initial_manifest'])
  for container in ready['spec']['template']['spec']['containers']:
   for variable in container.get('env',[]):
    if variable['name'] in values:variable['value']=values[variable['name']]
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
 row.update(uid=meta['uid'],observed_at=stamp());state['heartbeat']=stamp();validate_state(state,args.restore_pr);save(path,state)
 return {'uid_bound':True,'namespace':args.namespace,'name':args.name,'cluster_writes':0}
def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--phase-state',required=True);parser.add_argument('--restore-pr',required=True)
 commands=parser.add_subparsers(dest='command',required=True);commands.add_parser('init');commands.add_parser('start-window')
 r=commands.add_parser('register');r.add_argument('--manifest',required=True);r.add_argument('--source-sha256',required=True);r.add_argument('--output',required=True);r.add_argument('--writer',action='store_true')
 b=commands.add_parser('bind');b.add_argument('--namespace',required=True);b.add_argument('--name',required=True);b.add_argument('--initial-manifest-sha256',required=True);b.add_argument('--env-file',required=True);b.add_argument('--output',required=True)
 o=commands.add_parser('observe');o.add_argument('--namespace',required=True);o.add_argument('--name',required=True)
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
