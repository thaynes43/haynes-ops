#!/usr/bin/env python3
"""Private fresh COPY dependency assembly. No library/DB/cluster mutations.

Every source is a bounded immutable file from an actually UID-owned collector.
The published preflight is the saved-state authority. No source rows are filtered.
The ordered selection is supplied by root approval and revalidated in full.
"""
import argparse,ast,datetime as dt,hashlib,importlib.util,json,os,signal,stat,sys,time
from pathlib import Path
HERE=Path(__file__).parent
ROOT='/data/cephfs-hdd/data/media/books/EBooks'
BOUND_MODULE_PINS={'bound_census.py': '47c62c82277e5511d5011845ff0600acd0f1d97212077f41b31fba40775065f3', 'epub_copies.py': 'b79389c89bd5a57b4737ad693d2c209bc513e818343064ec04c8f0fcfb24a7da', 'epub_metadata.py': 'ce3c5a271cb4c94d91f3154240b4cfbc5e0cc57981969fc5c975a0c0f50eb773'}
class Refused(RuntimeError):pass
def stamp():return dt.datetime.now(dt.timezone.utc).isoformat()
def epoch(s):return dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def raw(path,cap):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as f:
  a=os.fstat(f.fileno())
  if not stat.S_ISREG(a.st_mode) or a.st_nlink!=1 or not 0<a.st_size<=cap:raise Refused('source is missing, unsafe or oversized')
  b=f.read(cap+1);z=os.fstat(f.fileno())
  if len(b)!=a.st_size or (a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns)!=(z.st_dev,z.st_ino,z.st_size,z.st_mtime_ns,z.st_ctime_ns):raise Refused('source changed during assembly')
 return b
def sha(b):return hashlib.sha256(b).hexdigest()
def pinned(item,cap):
 if not isinstance(item,dict) or set(item)!={'path','sha256'}:raise Refused('exact source path and SHA binding required')
 b=raw(item['path'],cap)
 if sha(b)!=item['sha256']:raise Refused('complete source hash changed')
 return b
def save(folder,name,value,encoded=None):
 b=encoded if encoded is not None else json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()+b'\n'
 cap={'snapshot.json':16*1024*1024,'selection.json':1024*1024,'app-capture.json':32*1024*1024}.get(name,32*1024*1024)
 if len(b)>cap:raise Refused('complete private proof exceeds its unchanged transport cap')
 p=folder/name;fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:f.write(b);f.flush();os.fsync(f.fileno())
 return {'path':str(p),'sha256':sha(b)}
def module(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def published_app_projection(rows,pre):
 # Reuse only the exact reviewed pure function; importing the full writer would
 # unnecessarily require its PostgreSQL driver in this offline host adapter.
 source=raw(HERE/'runtime-modules/book_copy_writer.py',128*1024)
 if sha(source)!='fbfec65f4af933da6ec9aca90536501e4514cebfb8e378584e3085a993493880':raise Refused('published app derivation source pin changed')
 nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='derive_app']
 if len(nodes)!=1:raise Refused('published complete app derivation missing')
 namespace={'json':json,'TABLES':('book_requests','books_items'),'metadata':pre.metadata,'copies':pre.copies}
 exec(compile(ast.Module(body=nodes,type_ignores=[]),'<pinned-published-derive-app>','exec'),namespace)
 return namespace['derive_app'](rows)
def ll_ascii(original):
 # JSON can contain U+2028/U+2029 inside a string. Parse the ONE complete JSON
 # object first, then encode ASCII; never split the raw vendor output into lines.
 value=json.loads(original)
 encoded=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()+b'\n'
 if json.loads(encoded)!=value or len(encoded.splitlines())!=1:raise Refused('complete LL ASCII transport changed decoded rows')
 return value,encoded
def json_dates(value):
 if isinstance(value,(dt.datetime,dt.date)):return value.isoformat()
 if isinstance(value,dict):return {k:json_dates(v) for k,v in value.items()}
 if isinstance(value,list):return [json_dates(v) for v in value]
 return value
def native_spec_sha256(spec):
 # This must be byte-identical to producer collectors.canonical(spec).
 return sha(json.dumps(spec,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode())
def source_identity(value,state,key):
 lease=next(x for x in state['pg_leases'] if (x['job_namespace'],x['job_name'])==key)
 if any(lease[x] is None for x in ('job_uid','pod_uid','backend_pid')):raise Refused('source PG ownership must be durably recorded')
 if (value.get('phase_token')!=state['phase_token'] or value.get('job_uid')!=lease['job_uid']
     or value.get('pod_uid')!=lease['pod_uid'] or value.get('lock_backend_pid')!=lease['backend_pid']
     or value.get('lock_application_name')!=lease['application_name']):raise Refused('app snapshot belongs to a different source process')
 return lease
def vendor_proof(proof,state,key):
 row=next(r for r in state['owned_jobs'] if (r['namespace'],r['name'])==key)
 if (row['uid'] is None or proof.get('job_uid')!=row['uid'] or proof.get('phase_token')!=state['phase_token']
     or not proof.get('pod_uid') or proof.get('readOnlySource') is not True or proof.get('sourceWrites')!=0
     or not proof.get('before') or proof['before']!=proof.get('after')):raise Refused('vendor source ownership or full stable copy is unproved')
 if epoch(proof['captureStartedAt'])>epoch(proof['capturedAt']):raise Refused('vendor capture start is inverted')
def census_protections(library,ll,holds,started):
 if holds.get('version')!=1 or not isinstance(holds.get('holds'),list):raise Refused('complete actual Census Holds schema is unknown')
 protections=list(library['additional_protected_paths']);classified=[]
 for row in holds['holds']:
  if not isinstance(row,dict) or not row.get('key') or not row.get('reason') or not isinstance(row.get('path'),str):raise Refused('Census Hold lacks an exact current path')
  path=row['path']
  if path.startswith('EBooks/'):
   path=path[len('EBooks/'):]
   protections.append({'path':path,'reason':'current Census Hold '+row['key']+': '+row['reason']})
  elif not path.startswith('AudioBooks/'):raise Refused('Census Hold has an unknown physical root')
  classified.append(row)
 for folder in library['configured_hold_folders']:protections.append({'path':folder,'reason':'current configured library hold'})
 audio=[]
 for book in ll['llBooks']:
  value=book.get('AudioFile')
  if value in (None,''):continue
  entry={'BookID':book['BookID'],'AudioFile':value}
  if isinstance(value,str) and value.startswith(ROOT+'/'):
   entry['classification']='protected EBooks dependency';protections.append({'path':value[len(ROOT)+1:],'reason':'complete LL AudioFile reference '+str(book['BookID'])})
  elif isinstance(value,str) and value.startswith('/data/cephfs-hdd/data/media/books/AudioBooks/'):
   entry['classification']='separate AudioBooks dependency'
  else:raise Refused('LL AudioFile physical scope is unresolved')
  audio.append(entry)
 return {'complete':True,'capture_started_at':started,'checked_at':stamp(),'protected_paths':protections,
         'raw_census_holds':classified,'all_LL_rows_preserved':len(ll['llBooks']),'all_AudioFile_rows':audio,
         'all_AudioFile_rows_sha256':sha(json.dumps(audio,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()),
         'qualification':'Fresh complete source collection and current full protection configuration under the actual owned fence; historical incomplete proof is unchanged.'}
def select(snapshot,report,approved):
 if report.get('apply_ready') is not True or report.get('blockers'):raise Refused('published complete dependency preflight blocks copy movement')
 hashes={r['path']:r['sha256'] for r in snapshot['files']};eligible={}
 for group in report['groups']:
  for row in group['copies']:
   if not row['keeper'] and not row['protected_reasons']:eligible[row['path']]=group['keeper']
 if not isinstance(approved,list) or not approved:raise Refused('root-approved exact ordered selection is required')
 seen=set()
 for e in approved:
  if (not isinstance(e,dict) or set(e)!={'path','sha256','keeper','keeper_sha256'} or e['path'] in seen
      or eligible.get(e['path'])!=e['keeper'] or hashes.get(e['path'])!=e['sha256'] or hashes.get(e['keeper'])!=e['keeper_sha256']):raise Refused('approved selection is repeated, protected, changed or ineligible')
  seen.add(e['path'])
 return {'schema':1,'kind':'copy_selection','approved_for_retention':True,'entries':approved}
def verify_kavita_files(c,proof):
 expected={v['name']:v for v in proof['files']}
 if (len(expected)!=len(proof['files']) or 'kavita.db' not in expected
     or set(expected)-{'kavita.db','kavita.db-wal','kavita.db-shm'} or set(c['kavita_files'])!=set(expected)
     or c['kavita_files']['kavita.db']!=c['kavita_db']):raise Refused('complete Kavita DB/WAL/SHM file set is missing or changed')
 parent=Path(c['kavita_db']['path']).parent
 for name,entry in expected.items():
  if Path(c['kavita_files'][name]['path'])!=parent/name:raise Refused('private vendor sidecars must retain their exact adjacent names')
  db=pinned(c['kavita_files'][name],512*1024*1024)
  if len(db)!=entry['size'] or sha(db)!=entry['sha256']:raise Refused('complete private Kavita file differs from source receipt')
def validate_app_complete(app):
 schema=app.get('full_table_schema')
 if (app.get('read_only')!='on' or app.get('scope')!='full' or app.get('production_writes')!=0
     or set(app.get('full_table_rows',{}))!={'books_items','book_requests'} or not isinstance(schema,list)
     or any(not isinstance(row,dict) for row in schema)
     or {row.get('table_name') for row in schema}!={'books_items','book_requests'}
     or set(app.get('full_table_counts',{}))!={'books_items','book_requests'}):raise Refused('complete actual app rows/schema/counts missing')
 if any(type(app['full_table_counts'][key]) is not int or app['full_table_counts'][key]!=len(json.loads(rows)) for key,rows in app['full_table_rows'].items()):raise Refused('complete app row counts differ')
def assemble(c,output):
 if c.get('schema')!=1 or c.get('root_approved') is not True:raise Refused('root-approved complete assembly contract required')
 helper=module('copy_checkpoint',HERE/'checkpoint-copy-job.py');state,_=helper.read_json(Path(c['phase_state']));helper.validate_state(state,c['restore_pr']);helper.validate_leases(state,require_all=True)
 if state['phase_token']!=c['phase_token'] or 'window_started_at' not in state or state['complete']:raise Refused('fresh actual COPY phase required')
 abort=epoch(state['window_started_at'])+250
 if not time.time()<abort:raise Refused('assembly phase deadline expired')
 def stopped(*_):raise Refused('bounded private assembly deadline or signal')
 previous={s:signal.signal(s,stopped) for s in (signal.SIGALRM,signal.SIGTERM,signal.SIGINT)};signal.setitimer(signal.ITIMER_REAL,min(25,abort-time.time()))
 try:
  started=stamp();library=json.loads(pinned(c['library'],32*1024*1024));app_raw=pinned(c['app_capture'],32*1024*1024);app=json.loads(app_raw)
  if library.get('complete') is not True or library.get('errors') or library.get('production_writes')!=0 or library.get('ebook_root')!=ROOT:raise Refused('complete current source corpus is unproved')
  lease=source_identity(app,state,helper.SOURCE)
  fence=c['fence'];helper.require_live_source_lease(state,fence)
  service=json.loads(pinned(c['service_fence'],8*1024*1024))
  if (service.get('schema')!=1 or service.get('phase_token')!=state['phase_token']
      or service.get('all_services_stopped') is not True or service.get('all_six_crons_suspended') is not True
      or service.get('libretto_acquisition_off') is not True or service.get('unknown_library_writers')!=[]
      or service.get('publisher_scope_sha256')!=c['publisher_scope_sha256']
      or service.get('first_service_stop_observed_at')!=state['window_started_at']
      or not 0<=time.time()-epoch(service['checked_at'])<=65):raise Refused('complete actual service/publisher fence receipt is missing or stale')
  validate_app_complete(app)
  established=app['pg_fence_established_at']
  if epoch(established)<epoch(state['window_started_at']) or epoch(established)>min(epoch(app['capture_started_at']),epoch(library['started_at'])):raise Refused('source collection began before its actual fence')
  if fence['pg_fence_established_at']!=established or fence['first_service_stop_observed_at']!=state['window_started_at']:raise Refused('assembly fence ordering differs from actual source phase')
  # SOURCE supplies only fresh complete stat/permissions/protections. Derive
  # identity exclusively after the exact whole FP/pathset comparison under its
  # actual still-held lease. Preserve sealed LIVE byte clocks and artifact SHA.
  for name,expected in BOUND_MODULE_PINS.items():
   if sha(raw(HERE/'runtime-modules'/name,256*1024))!=expected:raise Refused('bound census module/source pin changed')
  sys.path.insert(0,str(HERE/'runtime-modules'))
  import bound_census
  baseline_raw=pinned(c['live_byte_baseline'],32*1024*1024)
  baseline=json.loads(baseline_raw,object_pairs_hook=bound_census.copies.unique_object)
  source=library.get('source_binding',{})
  if source.get('job_uid')!=lease['job_uid'] or source.get('pod_uid')!=lease['pod_uid']:raise Refused('current stat census belongs to a different source lease')
  row=next(r for r in state['owned_jobs'] if (r['namespace'],r['name'])==helper.SOURCE)
  pod=helper.lookup_lease_pod(row,lease['pod_uid']);spec=pod['spec'];status=pod['status']['containerStatuses'][0]
  actual={'namespace':pod['metadata']['namespace'],'pod_name':pod['metadata']['name'],'pod_uid':pod['metadata']['uid'],'job_uid':row['uid'],'node':spec['nodeName'],'image':spec['containers'][0]['image'],'image_id':status['imageID'].removeprefix('docker-pullable://'),'pod_spec_sha256':native_spec_sha256(spec),'restarts':status['restartCount']}
  if any(source.get(key)!=value for key,value in actual.items()):raise Refused('stat census native SOURCE image/spec binding differs from its actual healthy owner')
  if epoch(baseline['completed_at'])>epoch(state['window_started_at']):raise Refused('LIVE byte baseline was not complete before the actual service stop')
  helper.require_live_source_lease(state,fence)
  library,bound=bound_census.bind_live_baseline(baseline,c['live_byte_baseline']['sha256'],library,ROOT)
  current={r['path']:r['sha256'] for r in library['files']}
  ll,ascii_rows=ll_ascii(pinned(c['ll_sql'],32*1024*1024));ll_proof=json.loads(pinned(c['ll_proof'],4*1024*1024));kproof=json.loads(pinned(c['kavita_proof'],4*1024*1024))
  vendor_proof(ll_proof,state,('downloads','issue831-ll-source-1009-03'));vendor_proof(kproof,state,('media','issue831-kavita-source-1009-03'))
  if ll.get('phase')!=state['phase_token'] or ll.get('sourceWrites')!=0 or ll.get('queryName')!='LL_BOOKS_SQL' or ll.get('querySha256')!=c['ll_sql_sha256'] or ll.get('sourceFingerprintBefore')!=ll_proof['before'] or ll.get('sourceFingerprintAfter')!=ll_proof['after']:raise Refused('complete LL SQL source binding differs')
  if any(epoch(established)>epoch(p['captureStartedAt']) for p in (ll_proof,kproof)):raise Refused('vendor capture started before actual source lease')
  holds_raw=pinned(c['census_holds'],1024*1024)
  import yaml
  holds=json_dates(yaml.safe_load(holds_raw));census=census_protections(library,ll,holds,started)
  verify_kavita_files(c,kproof)
  # This only reads the copied vendor database. The published preflight follows
  # arbitrary known/unknown FK paths and saved locks; exploratory exports do not
  # replace its dependency authority.
  sys.path.insert(0,str(HERE/'runtime-modules'));pre_path=HERE/'runtime-modules/epub_copy_preflight.py'
  if sha(raw(pre_path,128*1024))!='3fd41af67460aaacad9fba92b09418c31110fe15bae88dace3e75179ec0557a1':raise Refused('published dependency preflight source pin changed')
  pre=module('copy_preflight',pre_path)
  if published_app_projection(app['full_table_rows'],pre)!=app['app']:raise Refused('full app projection differs from published derivation')
  lock_evidence={};series,protected,errors,counts,history=pre.kavita_dependencies(c['kavita_db']['path'],kproof,ROOT,lock_evidence)
  attest={key:{'quiesced':True,'proof':c['service_fence']['sha256']+':'+lease['application_name']+':'+str(lease['backend_pid']),
                      'checked_at':fence['pg_health_at'],'established_at':established} for key in pre.copies.SOURCES}
  snapshot,report=pre.prepare(library,ll,app,series,protected,errors,kproof,census,attest)
  snapshot['bound_census']=bound
  approved=json.loads(pinned(c['selection_approval'],1024*1024))
  selection=select(snapshot,report,approved['entries'])
  selection['bound_census_baseline_sha256']=bound['byte_baseline_sha256']
  helper.require_live_source_lease(state,fence)
  output=Path(output);output.mkdir(mode=0o700,exist_ok=False)
  artifacts={'snapshot.json':save(output,'snapshot.json',snapshot),'app-capture.json':save(output,'app-capture.json',None,app_raw)}
  selection['snapshot_sha256']=artifacts['snapshot.json']['sha256'];artifacts['selection.json']=save(output,'selection.json',selection)
  pre.copies.load_selection(artifacts['selection.json']['path'],ROOT,artifacts['snapshot.json']['sha256'],{e['path']:e['keeper'] for e in selection['entries']},current)
  save(output,'ll-ascii.jsonl',None,ascii_rows);save(output,'census-protections.json',census);save(output,'census-holds.raw.yaml',None,holds_raw);save(output,'preflight-report.json',report);save(output,'kavita-authoritative-locks.json',lock_evidence)
  validated_library=save(output,'validated-library.json',library)
  receipt={'schema':1,'phase_token':state['phase_token'],'fresh_sources':c,'source_pg_lease':lease,'validated_library':validated_library,'proof_files':artifacts,'library_count':len(current),'all_file_count':len(library['all_file_fingerprints']),'raw_holds_count':len(holds['holds']),'ll_full_rows':len(ll['llBooks']),'kavita_dependency_counts':counts,'kavita_history':history,'selected_count':len(selection['entries']),'runtime_mutations':0,'assembly_started_at':started,'completed_at':stamp()}
  save(output,'assembly-receipt.json',receipt);return receipt
 finally:
  signal.setitimer(signal.ITIMER_REAL,0)
  for s,p in previous.items():signal.signal(s,p)
def main():
 p=argparse.ArgumentParser();p.add_argument('--contract',required=True);p.add_argument('--output',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
 if not a.execute:print(json.dumps({'prepared_only':True,'runtime_actions':0}));return
 c=json.loads(raw(a.contract,4*1024*1024));result=assemble(c,a.output);print(json.dumps({'selected_count':result['selected_count'],'library_count':result['library_count'],'runtime_mutations':0}))
if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps({'complete':False,'code':'assembly_refused','error_class':type(e).__name__}));raise SystemExit(2)
