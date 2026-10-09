"""Generate a closed hash-bound payload; this module never launches a process."""
import base64
import hashlib


def program(files, entry, gate):
    if entry not in files or gate not in ('COPY_SOURCE_PHASE_READY', 'COPY_BASELINE_PHASE_READY'):
        raise ValueError('closed bootstrap identity differs')
    if any('/' in name or not name.endswith('.py') for name in files):
        raise ValueError('private payload basename differs')
    encoded = {name: base64.b64encode(raw).decode() for name, raw in files.items()}
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    # An unauthorized command must terminate even when stdout is not drained.
    # Runtime modules precede payload modules; the reviewed image has no collector
    # or adapter. Refuse unexpected runtime shadowing before the entrypoint runs.
    return f'''import os,base64,hashlib,sys,importlib.machinery
if os.environ.get({gate!r})!='1':
 try:
  fd=sys.stdout.fileno();old=os.get_blocking(fd);os.set_blocking(fd,False)
  try:os.write(fd,b'{{"type":"prepared-collector-refused","code":"phase_not_authorized","production_writes":0}}\\n')
  finally:os.set_blocking(fd,old)
 except Exception:pass
 os._exit(2)
files={encoded!r}
hashes={hashes!r}
for name,data in files.items():
 raw=base64.b64decode(data,validate=True)
 if hashlib.sha256(raw).hexdigest()!=hashes[name]:os._exit(2)
 fd=os.open('/tmp/'+name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
for name in files:
 if name=={entry!r}:continue
 spec=importlib.machinery.PathFinder.find_spec(name[:-3],['/copy-writer','/tmp'])
 if spec is None or spec.origin!='/tmp/'+name:os._exit(2)
os.execv(sys.executable,[sys.executable,'-B','/tmp/'+{entry!r}])
'''
