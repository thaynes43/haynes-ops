#!/usr/bin/env python3
"""SOURCE-owner read-only final inventory/retained receipts; no library writes.
Exec children exchange private bytes; the connection owner performs verification.
"""
import hashlib,json,os,signal,stat,sys,time
from pathlib import Path
sys.path.insert(0,'/copy-writer')
import epub_copies as copies,epub_metadata as metadata,bound_census as bound
PINS={'epub_copies.py':'b79389c89bd5a57b4737ad693d2c209bc513e818343064ec04c8f0fcfb24a7da','epub_metadata.py':'ce3c5a271cb4c94d91f3154240b4cfbc5e0cc57981969fc5c975a0c0f50eb773','bound_census.py':'47c62c82277e5511d5011845ff0600acd0f1d97212077f41b31fba40775065f3'}
def fingerprints(deadline,health,scan_guard):
 health()
 result={p:[[str(n)for n in v[0]],*(str(n)for n in v[1:])]for p,v in copies.file_fingerprints(ROOT,deadline,health=scan_guard,max_files=bound.MAX_FILES).items()}
 health()
 return result
def stat_record(value):
 return [[str(n)for n in metadata._identity(value)],str(value.st_mode),str(value.st_uid),str(value.st_gid)]
ROOT='/data/cephfs-hdd/data/media/books/EBooks';STATE='/data/cephfs-hdd/data/media/books/.epub-convert'
def collect(value,deadline,health,scan_guard,environ,module_dir='/copy-writer'):
 # Owner-only: the published fence validates connection/PID/thread/transaction.
 # The exec child may deliver/fetch private bytes but cannot call this verifier.
 if not callable(health) or not callable(scan_guard):raise metadata.Refused('outcome requires owning health and local guards')
 scan_guard()
 if (set(value)!={'phase_token','job_uid','pod_uid','library','selection','events','snapshot_sha256'}
     or value['phase_token']!=environ['COPY_PHASE_TOKEN'] or value['job_uid']!=environ['COPY_JOB_UID'] or value['pod_uid']!=environ['COPY_POD_UID']):raise metadata.Refused('readonly outcome owner differs')
 selected=value['selection']['entries'];moved=[e for e in value['events'] if e.get('msg')=='epub_copy_consolidate' and e.get('result')=='moved'];by_path={e['path']:e for e in moved}
 if len(by_path)!=len(moved) or set(by_path)!={e['path'] for e in selected}:raise metadata.Refused('actual moved selection differs')
 if value['library'].get('schema')!=2 or value['library'].get('kind')!='validated_byte_census':raise metadata.Refused('manual validated byte census required')
 for name,expected in PINS.items():
  if hashlib.sha256(Path(module_dir,name).read_bytes()).hexdigest()!=expected:raise metadata.Refused('published outcome module pin differs')
 original=value['library']['all_file_fingerprints'];bound.validate_fingerprints(original);remaining_files=fingerprints(deadline,health,scan_guard)
 if remaining_files!={p:v for p,v in original.items() if p not in by_path}:raise metadata.Changed('unapproved final library file changed')
 receipts=[];folder=os.path.join(STATE,'copies')
 for entry in selected:
  scan_guard()
  path=entry['path'];old=original[path];identity=[bound.stat_value(n)for n in old[0]];mode,uid,gid=[bound.stat_value(n)for n in old[1:]]
  stem=metadata.sha256(path.encode())+'-'+entry['sha256'];expected=os.path.join(folder,stem+'.json')
  if by_path[path].get('backup_manifest')!=expected or by_path[path].get('keeper')!=entry['keeper'] or by_path[path].get('sha256')!=entry['sha256']:raise metadata.Refused('actual move manifest/keeper/hash differs')
  with metadata.safe_directory(folder) as backups:
   raw,info=metadata.read_regular(backups,stem+'.json',65536);manifest=json.loads(raw,object_pairs_hook=copies.unique_object);digest,saved=copies.hash_file(backups,stem+'.epub',deadline,health=health)
   if (manifest.get('schema')!=1 or manifest.get('kind')!='retained_copy' or manifest.get('relative_path')!=path or manifest.get('backup_file')!=stem+'.epub'
       or manifest.get('sha256')!=entry['sha256'] or manifest.get('evidence_sha256')!=value['snapshot_sha256']
       or (manifest.get('original_mode'),manifest.get('original_uid'),manifest.get('original_gid'))!=(stat.S_IMODE(mode),uid,gid)
       or digest!=entry['sha256'] or saved.st_nlink!=1 or saved.st_mtime_ns!=identity[3]
       or (saved.st_dev,saved.st_ino,saved.st_size,saved.st_mode,saved.st_uid,saved.st_gid)!=(identity[0],identity[1],identity[2],mode,uid,gid)):raise metadata.Changed('retained original bytes/inode/ownership differ')
  with metadata.safe_directory(os.path.dirname(os.path.join(ROOT,path))) as directory:
   try:os.stat(os.path.basename(path),dir_fd=directory,follow_symlinks=False)
   except FileNotFoundError:pass
   else:raise metadata.Changed('retained source path still exists')
  with metadata.safe_directory(os.path.dirname(os.path.join(ROOT,entry['keeper']))) as directory:
   keeper_hash,keeper=copies.hash_file(directory,os.path.basename(entry['keeper']),deadline,health=health)
   if keeper_hash!=entry['keeper_sha256'] or stat_record(keeper)!=original[entry['keeper']]:raise metadata.Changed('final LL keeper differs')
  receipts.append({'source_path':path,'keeper':entry['keeper'],'backup_manifest':expected,'manifest_raw_sha256':hashlib.sha256(raw).hexdigest(),'manifest':manifest,'retained_sha256':digest,'retained_identity':[str(n)for n in metadata._identity(saved)],'retained_mode':str(saved.st_mode),'retained_uid':str(saved.st_uid),'retained_gid':str(saved.st_gid),'keeper_sha256':keeper_hash,'keeper_identity':[str(n)for n in metadata._identity(keeper)],'source_path_absent':path not in remaining_files})
 final=fingerprints(deadline,health,scan_guard)
 if remaining_files!=final or time.monotonic()>=deadline:raise metadata.Changed('final readonly inventory changed or expired')
 out={'schema':2,'read_only':True,'production_writes':0,'phase_token':value['phase_token'],'job_uid':value['job_uid'],'pod_uid':value['pod_uid'],'actual_moved_count':len(receipts),'actual_retained_receipts':receipts,'all_remaining_file_fingerprints':final,'every_unapproved_file_unchanged':True,'completed_epoch':time.time()}
 encoded=json.dumps(out,sort_keys=True,separators=(',',':')).encode()+b'\n'
 if len(encoded)>16*1024*1024:raise metadata.Refused('readonly outcome output cap')
 return out

if __name__=='__main__':
 # Executing this source as a child conveys no database-health authority.
 raise SystemExit(2)
