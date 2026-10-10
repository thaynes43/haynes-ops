#!/usr/bin/env python3
"""COPY evidence only: one UID-owned, read-only source PG/NFS main process.
Its own alarm closes SHARE locks even if the host supervisor dies. No file moves.
"""
import datetime as dt,hashlib,json,os,signal,stat,sys,time
from pathlib import Path
ROOT='/data/cephfs-hdd/data/media/books/EBooks'
STATE='/data/cephfs-hdd/data/media/books/.epub-convert'
OUTPUT='/tmp'
COLLECTOR_SHA256 = '03f6163873b75f2bf0dc6896851b9da568d5e370ef39b56983eed64def2eb5f0'
SOURCE_HEALTH_SHA256='2d3adc2b8a529c3055e3fb1ff00684de99649965e78487358f737c9a3addc030'
OUTCOME_MAILBOX_SHA256 = '230e0487b81059d8e49c712256244e91d1ebfe7066cc3e25a34791dd6ef4e094'
OUTCOME_VERIFIER_SHA256 = '6d684075dc9240a353ddc63999944e6c1e1daea6dda44c100f604ad6ca2bb992'
class Stop(BaseException):pass

def utc():return dt.datetime.now(dt.timezone.utc).isoformat()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def identity(info):return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_nlink,info.st_mode,info.st_uid,info.st_gid)
def private(path,value):
 raw=canonical(value)+b'\n'
 if len(raw)>32*1024*1024:raise RuntimeError('complete_source_proof_byte_cap')
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())

def emit(event):
 event={'at':utc(),**event};descriptor=None;blocking=None
 raw=json.dumps(event,ensure_ascii=True,separators=(',',':')).encode()+b'\n'
 if len(raw)>512:raise RuntimeError('source_event_cap')
 try:
  descriptor=sys.stdout.fileno();blocking=os.get_blocking(descriptor)
  os.set_blocking(descriptor,False);os.write(descriptor,raw)
 except Exception:pass
 finally:
  if descriptor is not None and blocking is not None:
   try:os.set_blocking(descriptor,blocking)
   except OSError:pass

def run(environ=os.environ):
 # Closed source command performs no filesystem/library/PG access.
 if environ.get('COPY_SOURCE_PHASE_READY')!='1':
  emit({'type':'copy-source-refused','code':'phase_not_authorized','production_writes':0});return 2
 sys.path.insert(0,'/copy-writer')
 import bound_census_collectors as collectors
 if hashlib.sha256(Path(collectors.__file__).read_bytes()).hexdigest()!=COLLECTOR_SHA256:raise RuntimeError("reviewed collector module pin differs")
 import source_health
 if hashlib.sha256(Path(source_health.__file__).read_bytes()).hexdigest()!=SOURCE_HEALTH_SHA256:raise RuntimeError('reviewed SOURCE health module pin differs')
 import outcome_mailbox,importlib
 outcome_verifier=importlib.import_module('verify-copy-outcome-readonly')
 for module,expected_sha in ((outcome_mailbox,OUTCOME_MAILBOX_SHA256),(outcome_verifier,OUTCOME_VERIFIER_SHA256)):
  if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()!=expected_sha:raise RuntimeError('reviewed owner outcome module pin differs')
 import book_copy_writer as writer
 import epub_copies as copies
 import epub_metadata as metadata
 phase=environ.get('COPY_PHASE_TOKEN','');job_uid=environ.get('COPY_JOB_UID','');pod_uid=environ.get('COPY_POD_UID','')
 if len(phase)!=32 or any(c not in '0123456789abcdef' for c in phase) or not job_uid or not pod_uid:raise metadata.Refused('source Downward API identity missing')
 if os.getuid()!=1000 or os.getgid()!=1000:raise metadata.Refused('source process UID/GID differs')
 deadline=float(environ['COPY_SOURCE_DEADLINE_EPOCH']);fence=json.loads(environ['COPY_SOURCE_FENCE_JSON'],object_pairs_hook=copies.unique_object)
 if set(fence)!={'schema','phase_token','first_service_stop_observed_at','service_fence_checked_at','publisher_scope_sha256'} or fence['schema']!=1 or fence['phase_token']!=phase:raise metadata.Refused('source service fence schema differs')
 first=copies.timestamp_epoch(fence['first_service_stop_observed_at']);checked=copies.timestamp_epoch(fence['service_fence_checked_at'])
 if not first<=checked<=time.time() or time.time()-checked>65 or not time.time()<deadline<=first+250:raise metadata.Refused('source service fence/deadline expired')
 if not isinstance(fence['publisher_scope_sha256'],str) or len(fence['publisher_scope_sha256'])!=64 or any(c not in '0123456789abcdef' for c in fence['publisher_scope_sha256']):raise metadata.Refused('complete root-reviewed publisher scope binding is missing')
 # Ensure this private adapter uses the exact published stdlib/driver fence.
 expected={'book_copy_writer.py': 'fbfec65f4af933da6ec9aca90536501e4514cebfb8e378584e3085a993493880', 'epub_copies.py': 'b79389c89bd5a57b4737ad693d2c209bc513e818343064ec04c8f0fcfb24a7da', 'epub_metadata.py': 'ce3c5a271cb4c94d91f3154240b4cfbc5e0cc57981969fc5c975a0c0f50eb773'}
 for module in (writer,copies,metadata):
  p=Path(module.__file__)
  if hashlib.sha256(p.read_bytes()).hexdigest()!=expected[p.name]:raise metadata.Refused('published source module pin changed')
 def stopped(*_):raise Stop('source alarm/signal')
 previous={s:signal.signal(s,stopped) for s in (signal.SIGTERM,signal.SIGINT,signal.SIGALRM)}
 signal.setitimer(signal.ITIMER_REAL,deadline-time.time())
 app_name='issue825-duplicate-share-fence-'+phase
 def connect(*args,**kwargs):kwargs['application_name']=app_name;return writer.psycopg.connect(*args,**kwargs)
 try:
  with writer.PrimaryShareFence(environ['DATABASE_URL'],deadline,connect=connect,phase_token=phase) as pg:
   established=utc()
   fields={'application_name':app_name,'deadline_epoch_ms':int(deadline*1000),
           'phase_token':phase,'job_uid':job_uid,'pod_uid':pod_uid}
   with source_health.SourceHealth(pg,collectors,emit,fields) as controller:
    health=controller.health;scan_guard=controller.scan_guard
    health();emit({'type':'fence_ready','at':utc(),'backend_pid':pg.pid,**fields})
    started=utc()
    schema_sql="SELECT c.relname::text AS table_name,a.attnum,a.attname::text AS column_name,format_type(a.atttypid,a.atttypmod) AS data_type,a.attnotnull,a.attidentity::text,a.attgenerated::text,pg_get_expr(d.adbin,d.adrelid) AS default_expression FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='public' AND c.relname IN ('book_requests','books_items') AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum"
    health()
    schemas=pg.db.execute(schema_sql).fetchall();rows=writer.capture_rows(pg.db)
    counts={table:pg.db.execute('SELECT count(*)::bigint AS n FROM public.'+table).fetchone()['n'] for table in writer.TABLES}
    health()
    if {r['table_name'] for r in schemas}!=set(writer.TABLES) or any(counts[t]!=len(json.loads(rows[t])) for t in writer.TABLES):raise metadata.Refused('complete app schema/count differs from full rows')
    if sum(len(v.encode()) for v in rows.values())>32*1024*1024:raise metadata.Refused('full app row source exceeds proof cap')
    capture={'scope':'full','read_only':'on','production_writes':0,'capture_started_at':started,'completed_at':utc(),'captured_at':utc(),'full_table_rows':rows,'full_table_schema':schemas,'full_table_counts':counts,'app':writer.derive_app(rows),'schema':1,'lock_backend_pid':pg.pid,'lock_application_name':app_name,'pg_fence_established_at':established,'phase_token':phase,'job_uid':job_uid,'pod_uid':pod_uid}
    health();private(OUTPUT+'/app-capture.json',capture)
    emit({'type':'app_dependency_snapshot_ready','at':utc(),'backend_pid':pg.pid,'phase_token':phase,'job_uid':job_uid,'pod_uid':pod_uid,'sha256':hashlib.sha256(canonical(capture)+b'\n').hexdigest()})
    binding=collectors.await_native_binding(environ,ROOT,pg.deadline,metadata,copies,scan_guard)
    library=controller.collect(ROOT,pg.deadline,metadata,copies,binding);health();private(OUTPUT+'/library.json',library)
    with metadata.safe_directory(STATE) as state_dir:state_parent=[str(n) for n in identity(os.fstat(state_dir))]
    permission={'read_only':True,'uid':os.getuid(),'gid':os.getgid(),'supplemental_groups':os.getgroups(),'protected_hardlinks':Path('/proc/sys/fs/protected_hardlinks').read_text().strip(),'state_parent':state_parent,'qualification':'Source mount is read-only. Exact file/parent ownership/mode is retained; actual MAIN rechecks local identity and kernel mutation permission. No test move/link/unlink or permission change.'}
    private(OUTPUT+'/permissions.json',permission)
    emit({'type':'source_census_ready','at':utc(),'phase_token':phase,'job_uid':job_uid,'pod_uid':pod_uid,'epub_count':len(collectors.epub_paths(library['all_file_fingerprints'])),'all_file_count':len(library['all_file_fingerprints']),'production_writes':0})
    outcome=outcome_mailbox.SourceOutcome(outcome_verifier,controller,metadata,copies,environ,deadline,directory=OUTPUT,emit=emit)
    while True:
     scan_guard();outcome.process_pending()
     try:
      with metadata.safe_directory(OUTPUT) as directory:raw,_=metadata.read_regular(directory,'source-released.json',4096)
      if json.loads(raw,object_pairs_hook=copies.unique_object)!={'schema':1,'phase_token':phase,'job_uid':job_uid,'pod_uid':pod_uid}:raise metadata.Refused('source release identity differs')
      break
     except FileNotFoundError:time.sleep(.1)
    health()
  emit({'type':'source_fence_released','phase_token':phase,'job_uid':job_uid,'pod_uid':pod_uid,'production_writes':0});return 0
 finally:
  signal.setitimer(signal.ITIMER_REAL,0)
  for s,h in previous.items():signal.signal(s,h)
if __name__=='__main__':
 try:raise SystemExit(run())
 except (Exception,Stop) as error:
  try:emit({'type':'copy-source-refused','code':'source_guard_failed','error_class':type(error).__name__[:80],'production_writes':0})
  finally:os._exit(2)
