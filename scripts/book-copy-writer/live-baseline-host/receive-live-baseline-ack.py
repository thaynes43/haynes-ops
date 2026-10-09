#!/usr/bin/env python3
"""One fixed bounded stdin ack into private /tmp. No library/PG/provider route."""
import ctypes,hashlib,json,os,re,signal,stat,sys,time
from pathlib import Path
KEYS={'schema','type','phase_token','job_uid','pod_uid','baseline_sha256'}
NAME='live-baseline-delivery-ack.json'
class Refused(RuntimeError):pass
def unique(pairs):
 result={}
 for k,v in pairs:
  if k in result:raise Refused('duplicate_json_key')
  result[k]=v
 return result
def receive(stream,environ=os.environ,directory='/tmp'):
 # Remote command supplies only an earlier absolute clock, never a proof value.
 fixed=min(time.time()+5,float(environ['COPY_BASELINE_DEADLINE_EPOCH']),float(sys.argv[1]))
 if not time.time()<fixed:raise Refused('ack_deadline')
 def stop(*_):raise Refused('ack_deadline_or_signal')
 previous={x:signal.signal(x,stop) for x in (signal.SIGALRM,signal.SIGTERM,signal.SIGINT)};signal.setitimer(signal.ITIMER_REAL,fixed-time.time())
 fd=None;stage=NAME+'.staged'
 try:
  raw=stream.read(4097)
  if not 0<len(raw)<=4096:raise Refused('ack_byte_cap')
  value=json.loads(raw,object_pairs_hook=unique)
  expected={'schema':1,'type':'live_baseline_delivery_ack','phase_token':environ['COPY_PHASE_TOKEN'],'job_uid':environ['COPY_JOB_UID'],'pod_uid':environ['COPY_POD_UID']}
  if (set(value)!=KEYS or type(value['schema']) is not int or any(value[k]!=v for k,v in expected.items()) or not isinstance(value['baseline_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['baseline_sha256'])):raise Refused('ack_owned_identity')
  fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  if os.getuid()!=1000 or os.getgid()!=1000:raise Refused('ack_process_identity')
  # Stable complete baseline is read only from fixed private tmp, bounded32MiB.
  bfd=os.open('live-byte-baseline.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
  with os.fdopen(bfd,'rb') as source:
   before=os.fstat(source.fileno())
   if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_mode&0o077 or before.st_uid!=1000 or before.st_gid!=1000 or not 0<before.st_size<=32*1024*1024:raise Refused('ack_baseline_type')
   digest=hashlib.sha256();count=0
   while True:
    chunk=source.read(1024*1024)
    if not chunk:break
    count+=len(chunk);digest.update(chunk)
   after=os.fstat(source.fileno());current=os.stat('live-byte-baseline.json',dir_fd=fd,follow_symlinks=False)
   fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_nlink','st_uid','st_gid','st_mode')
   if count!=before.st_size or any(getattr(before,k)!=getattr(after,k) or getattr(before,k)!=getattr(current,k) for k in fields) or digest.hexdigest()!=value['baseline_sha256']:raise Refused('ack_baseline_changed')
  staged=os.open(stage,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
  with os.fdopen(staged,'wb') as target:target.write(raw);target.flush();os.fsync(target.fileno());os.fchmod(target.fileno(),0o400)
  libc=ctypes.CDLL(None,use_errno=True);rename=getattr(libc,'renameat2',None)
  if rename is None:raise Refused('atomic_noreplace_unsupported')
  rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];rename.restype=ctypes.c_int
  if rename(fd,stage.encode(),fd,NAME.encode(),1)!=0:raise OSError(ctypes.get_errno(),'ack_atomic_noreplace')
  os.fsync(fd)
  if not time.time()<fixed:raise Refused('ack_deadline')
  return {'schema':1,'type':'live_baseline_ack_ready','phase_token':value['phase_token'],'job_uid':value['job_uid'],'pod_uid':value['pod_uid'],'baseline_sha256':value['baseline_sha256'],'production_writes':0}
 finally:
  if fd is not None:os.close(fd)
  signal.setitimer(signal.ITIMER_REAL,0)
  for x,old in previous.items():signal.signal(x,old)
if __name__=='__main__':
 try:print(json.dumps(receive(sys.stdin.buffer),sort_keys=True,separators=(',',':')))
 except Exception as error:print(json.dumps({'type':'live_baseline_ack_refused','error_class':type(error).__name__,'production_writes':0}));raise SystemExit(2)
