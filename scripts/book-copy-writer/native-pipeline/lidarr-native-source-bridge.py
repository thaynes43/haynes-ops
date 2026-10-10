"""Host bridge for complete vendor rows from a durable UID-owned RO helper.

No create, pause, cleanup or data write. Caller owns one phase-scoped helper and
serially invokes this bridge inside the existing complete publisher budget.
"""
import copy,hashlib,importlib.util,json,signal,time
from pathlib import Path
IMAGE='ghcr.io/thaynes43/book-copy-writer@sha256:628e97b8dbcc83a4d7068484b516b21dde740c4dd130d54f3b030f1ee7a75601'
class Refused(RuntimeError):pass

def require_helper_images(helper,pod_image,ready_image):
 if (helper.image_identity(pod_image.removeprefix('docker-pullable://'))!=IMAGE
     or helper.image_identity(ready_image)!=IMAGE):raise Refused('native helper reviewed image changed')

def merge_complete(native,small):
 if native.get('complete') is not True or native.get('read_only') is not True or native.get('source_writes')!=0 or native.get('kind')!='lidarr':raise Refused('native source refused or incomplete')
 inv=native['inventory'];source=native['source'];counts=inv['counts_before']
 inventory_sha=hashlib.sha256(json.dumps(inv,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
 if inventory_sha!=native['inventory_before_sha256']:raise Refused('native complete inventory hash differs')
 if (counts!=inv['counts_after'] or set(counts)!={'Artists','RootFolders','Notifications'}
     or native['inventory_before_sha256']!=native['inventory_after_sha256']
     or native['source_files_before']!=native['source_files_after']
     or counts['Artists']!=len(source['items']) or counts['RootFolders']!=len(source['root_folders'])
     or counts['Notifications']!=len(inv['tables']['Notifications'])
     or counts['Artists']!=len(inv['tables']['Artists'])):raise Refused('native full rows/counts/schema or source identity differ')
 projected=[{'id':r['Id'],'path':r['Path']} for r in inv['tables']['Artists']]
 if source['items']!=projected or len({r['id'] for r in projected})!=len(projected):raise Refused('native artists were omitted, changed or repeated')
 if small.get('native_artist_projection_required') is not True or small.get('complete') is not False:raise Refused('native route must start as explicitly incomplete')
 status=small['vendor_status']
 if status!={'version':'3.1.6.5078','appData':'/config','startupPath':'/app/bin','databaseType':'sqLite'}:raise Refused('native vendor API/source version or backend changed')
 roots=sorted(source['root_folders'],key=lambda r:str(r['id']));hooks=sorted(source['custom_hooks'],key=lambda r:str(r['id']))
 if (roots!=small['root_folders'] or hooks!=small['custom_hooks'] or counts['Notifications']!=small['notification_count'] or source['custom_hooks_disabled']!=small['custom_hooks_disabled']):raise Refused('native roots/hooks differ from complete small API projection')
 result=copy.deepcopy(source);result['items']=sorted(projected,key=lambda r:str(r['id']));result['root_folders']=roots;result['custom_hooks']=hooks
 return result

def phase_identity_equal(before,after):
 if set(before)!=set(after):return False
 a,b=copy.deepcopy(before),copy.deepcopy(after)
 old,new=a.pop('heartbeat',None),b.pop('heartbeat',None)
 try:
  import datetime as dt
  first=dt.datetime.fromisoformat(old.replace('Z','+00:00'));last=dt.datetime.fromisoformat(new.replace('Z','+00:00'))
  if first.tzinfo is None or last.tzinfo is None or last<first or not 0<=time.time()-last.timestamp()<=15:return False
 except (AttributeError,TypeError,ValueError):return False
 return a==b

def copy_source_guard(helper,state):
 if not hasattr(helper,'INTENTS'):return # Historical core-only live rehearsal.
 helper.validate_leases(state,require_all=True)
 row=next(r for r in state['owned_jobs'] if (r['namespace'],r['name'])==helper.SOURCE)
 lease=next(l for l in state['pg_leases'] if (l['job_namespace'],l['job_name'])==helper.SOURCE)
 if row['uid'] is None:
  if any(lease[k] is not None for k in ('job_uid','pod_uid','backend_pid')) or helper.lookup_job(*helper.SOURCE) is not None:raise Refused('uncreated source owner is not absent')
  return
 if any(lease[k] is None for k in ('job_uid','pod_uid','backend_pid')):raise Refused('source PG owner is not durably recorded')
 pod=helper.lookup_lease_pod(row,lease['pod_uid']);events=helper.owned_log_events(row['namespace'],pod['metadata']['name'],row['ready_manifest']['spec']['template']['spec']['containers'][0]['name'])
 if any(e.get('type') in ('fence_failed','copy-source-refused','source_fence_released') for e in events):raise Refused('source process no longer holds its actual fence')
 health=[e for e in events if e.get('type')=='fence_healthy']
 if not health:raise Refused('actual source health missing')
 helper.require_live_source_lease(state,{'pg_backend_pid':lease['backend_pid'],'pg_health_at':health[-1]['at']})

def capture(run,profile,route,small,remaining):
 if not 0<remaining<=10:raise Refused('native hard cap must be positive and<=10')
 def expired(*_):raise Refused('native_hard_deadline')
 previous=signal.getsignal(signal.SIGALRM);old_timer=signal.getitimer(signal.ITIMER_REAL);started=time.monotonic()
 signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,min(remaining,old_timer[0]) if old_timer[0]>0 else remaining)
 try:return _capture_bounded(run,profile,route,small,remaining)
 finally:
  signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)
  if old_timer[0]>0:signal.setitimer(signal.ITIMER_REAL,max(.001,old_timer[0]-(time.monotonic()-started)),old_timer[1])

def _capture_bounded(run,profile,route,small,remaining):
 if not 0<remaining<=10:raise Refused('native query must fit finite <=10-second remaining capture budget')
 end=time.monotonic()+remaining
 helper_path=Path(profile['checkpoint_helper'])
 if hashlib.sha256(helper_path.read_bytes()).hexdigest()!=profile['checkpoint_sha256']:raise Refused('native checkpoint source changed')
 spec=importlib.util.spec_from_file_location('native_owned_helper',helper_path);helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
 state,state_sha=helper.read_json(profile['phase_state']);helper.validate_state(state,profile['restore_pr']);copy_source_guard(helper,state)
 rows=[r for r in state['owned_jobs'] if (r['namespace'],r['name'])==(profile['namespace'],profile['job_name'])]
 if state['complete'] or len(rows)!=1:raise Refused('native helper phase is complete or intent is ambiguous')
 row=rows[0];ready=row['ready_manifest']
 if row['writer'] or row['uid'] is None or ready is None or helper.digest(helper.encoded(ready))!=row['ready_manifest_sha256']:raise Refused('native helper must be ready, read-only and UID-bound before exec')
 template_path=Path(profile['source_template'])
 if hashlib.sha256(template_path.read_bytes()).hexdigest()!=profile['source_template_sha256']:raise Refused('native immutable source template changed')
 expected=json.loads(template_path.read_bytes());expected['metadata']['name']=row['name']
 for m in (expected['metadata'],expected['spec']['template']['metadata']):m.setdefault('labels',{})[helper.LABEL]=state['phase_token']
 helper.ensure_env_only(expected,ready,{'LIDARR_CAPTURE_PHASE_READY','LIDARR_CAPTURE_DEADLINE_EPOCH'})
 claim=run(['kubectl','get','pvc','lidarr','-n',profile['namespace'],'-o','json'])
 if claim['metadata']['uid']!=profile['claim_uid'] or claim['metadata'].get('deletionTimestamp'):raise Refused('native source PVC identity changed')
 job=run(['kubectl','get','job',row['name'],'-n',row['namespace'],'-o','json'])
 meta=job['metadata']
 if meta.get('uid')!=row['uid'] or meta.get('deletionTimestamp') or meta.get('labels',{}).get(helper.LABEL)!=state['phase_token'] or not helper.declared_matches(ready,job):raise Refused('native actual Job differs from exact durable ready intent')
 pods=run(['kubectl','get','pods','-n',row['namespace'],'-o','json'])['items']
 pods=[p for p in pods if any(o.get('kind')=='Job' and o.get('name')==row['name'] and o.get('uid')==row['uid'] for o in p['metadata'].get('ownerReferences',[]))]
 if len(pods)!=1:raise Refused('native helper does not have exactly one owned Pod')
 pod=pods[0];pm=pod['metadata'];ps=pod['spec'];source=ready['spec']['template']['spec'];c=source['containers'][0];env={v['name']:v.get('value') for v in c['env']}
 owners=pm.get('ownerReferences',[]);statuses=pod.get('status',{}).get('containerStatuses',[])
 if (pm.get('deletionTimestamp') or pm.get('annotations',{}).get('k8tz.io/inject')!='false' or pm.get('labels',{}).get(helper.LABEL)!=state['phase_token']
     or pm.get('labels',{}).get('batch.kubernetes.io/controller-uid')!=row['uid']
     or len(owners)!=1 or owners[0].get('uid')!=row['uid'] or owners[0].get('controller') is not True
     or pod.get('status',{}).get('phase')!='Running' or len(statuses)!=1 or statuses[0].get('restartCount')!=0
     or set(statuses[0].get('state',{}))!={'running'} or not statuses[0].get('ready')
     ):raise Refused('native helper actual Pod UID/phase/image/main process changed')
 require_helper_images(helper,statuses[0]['imageID'],c['image'])
 if (len(ps.get('containers',[]))!=1 or ps.get('initContainers') or ps.get('ephemeralContainers')
     or source.get('automountServiceAccountToken') is not False or ps.get('automountServiceAccountToken') is not False
     or ps.get('nodeName')!=source['nodeSelector']['kubernetes.io/hostname']
     or source['nodeSelector']!={'kubernetes.io/hostname':profile['node']}):raise Refused('native helper workload placement or scope changed')
 # Reject unapproved workloads while permitting only known Pod defaults and the
 # scheduler's nodeName selected by the exact reviewed single hostname.
 actual=copy.deepcopy(ps)
 for key,value in {'enableServiceLinks':True,'priority':0,'preemptionPolicy':'PreemptLowerPriority','serviceAccountName':'default','serviceAccount':'default'}.items():
  if key not in source and key in actual:
   if actual[key]!=value:raise Refused('native helper Pod default changed')
   del actual[key]
 if 'tolerations' not in source and 'tolerations' in actual:
  expected=[{'key':'node.kubernetes.io/not-ready','operator':'Exists','effect':'NoExecute','tolerationSeconds':300},{'key':'node.kubernetes.io/unreachable','operator':'Exists','effect':'NoExecute','tolerationSeconds':300}]
  if actual['tolerations']!=expected:raise Refused('native helper tolerations changed')
  del actual['tolerations']
 del actual['nodeName']
 comparison=copy.deepcopy(job);comparison['spec']['template']['spec']=actual
 if not helper.declared_matches(ready,comparison):raise Refused('native helper Pod executable differs from ready intent')
 if env.get('LIDARR_SOURCE_POD_UID')!=route['pod_uid'] or env.get('LIDARR_CONFIG_SHA256')!=profile['config_sha256'] or env.get('LIDARR_CAPTURE_PHASE_READY')!='1':raise Refused('native helper source binding or gate changed')
 deadline=min(float(env['LIDARR_CAPTURE_DEADLINE_EPOCH']),time.time()+max(0,end-time.monotonic()))
 if deadline<=time.time():raise Refused('native helper lease expired')
 program=Path(profile['capture_program']).read_text()
 if hashlib.sha256(program.encode()).hexdigest()!=profile['capture_sha256']:raise Refused('native capture program changed')
 native=run(['kubectl','exec','-n',row['namespace'],pm['name'],'-c',c['name'],'--','env',f'LIDARR_CAPTURE_DEADLINE_EPOCH={deadline:.6f}','nice','-n','19','python','-c',program])
 if native.get('helper_identity')!={'COPY_PHASE_TOKEN':state['phase_token'],'COPY_JOB_UID':row['uid'],'COPY_POD_UID':pm['uid']} or native.get('source_pod_uid')!=route['pod_uid']:raise Refused('native capture source/helper identity differs')
 after,after_sha=helper.read_json(profile['phase_state']);helper.validate_state(after,profile['restore_pr'])
 if not phase_identity_equal(state,after):raise Refused('native helper phase identity changed during exec')
 copy_source_guard(helper,after)
 if time.monotonic()>=end:raise Refused('native complete owned-source read exceeded its10-second cap')
 return merge_complete(native,small),native
