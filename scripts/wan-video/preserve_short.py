"""Save validated OP340 media on the existing Wan PVC, with create-only writes.

This publishes no new endpoint and submits no render. The separate production
record is explicitly an editorial assembly, never an inference-job claim.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

OUT = Path('/home/dev/artifacts/dual3090-short')
REMOTE = ['kubectl', 'exec', '-i', '-n', 'ai', 'deploy/wan-video', '-c', 'api', '--', 'python3', '-c']


def main():
    # Local pointer is exclusive, too: a rerun cannot silently create another record.
    pointer = OUT/'production.json'
    if pointer.exists():
        raise RuntimeError('Already preserved; inspect the existing production pointer')
    validation = json.loads((OUT/'validation.json').read_text())
    video = OUT/validation['file']
    assert hashlib.sha256(video.read_bytes()).hexdigest() == validation['sha256']
    assert validation['video']['decoded_frames'] == 720
    names = ['dream-debt-short.mp4', 'dream-debt-soundtrack.wav', 'shots.json',
             'validation.json', 'audio-loudness.json', 'audio-city.json',
             'audio-machine.json', 'audio-dawn.json', 'audio-model-license.md',
             'audio-city.wav', 'audio-machine.wav', 'audio-dawn.wav',
             'contact-sheet.jpg']
    shots = json.loads((OUT/'shots.json').read_text())
    sources = []
    for shot in shots:
        names += [shot['name']+'.mp4', shot['name']+'.json']
        job = json.loads((OUT/(shot['name']+'.json')).read_text())
        assert job['state'] == 'succeeded'
        sources.append({'name': shot['name'], 'job_id': job['id'],
                        'sha256': hashlib.sha256((OUT/(shot['name']+'.mp4')).read_bytes()).hexdigest()})
    upload = '''import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1]);expected=sys.argv[2];p.parent.mkdir(parents=True,exist_ok=True)
h=hashlib.sha256()
with p.open('xb') as out:
 while True:
  block=sys.stdin.buffer.read(1048576)
  if not block: break
  out.write(block);h.update(block)
if h.hexdigest()!=expected:
 p.unlink();raise RuntimeError('Uploaded hash mismatch')
print(json.dumps({'path':str(p),'sha256':h.hexdigest(),'bytes':p.stat().st_size}))
'''
    inventory = []
    for name in names:
        path = OUT/name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open('rb') as stream:
            result = subprocess.run(REMOTE+[upload, '/workspace/output/productions/op340/'+name, digest],
                                    stdin=stream, capture_output=True, check=True, timeout=120)
        inventory.append(json.loads(result.stdout))
    job_id = uuid.uuid4().hex
    record = {'id': job_id, 'kind': 'edited_production', 'story': 'OP340',
              'title': 'DREAM DEBT', 'state': 'succeeded',
              'created_at': time.time(), 'finished_at': time.time(),
              'output': 'productions/op340/dream-debt-short.mp4',
              'bytes': video.stat().st_size, 'sha256': validation['sha256'],
              'preset': 'editorial-30s-portrait', 'telemetry': [],
              'source_jobs': sources, 'assets': inventory,
              'provenance': 'Eight distinct Wan2.2 TI2V5B dual-GPU clips; separate Stable Audio SFX and synthesized pulse; original captions, edit, crop and retiming. This record is an editorial assembly, not a single model inference.',
              'publication': 'Private PoC only; no YouTube upload. Monetization and commercial model-license conditions are not certified.'}
    code = '''import json,pathlib,sys,os,uuid
j=json.load(sys.stdin);assert len(j['id'])==32 and j['kind']=='edited_production'
root=pathlib.Path('/workspace/jobs');temp=root/('production-'+uuid.uuid4().hex+'.tmp')
temp.write_text(json.dumps(j,indent=2)+'\\n')
try: os.link(temp,root/(j['id']+'.json'))
finally: temp.unlink()
print(j['id'])
'''
    subprocess.run(REMOTE+[code], input=json.dumps(record).encode(), capture_output=True, check=True, timeout=30)
    with pointer.open('x') as stream:
        stream.write(json.dumps(record,indent=2)+'\n')
    print('https://video.haynesops.com/videos/'+job_id+'/file')


if __name__ == '__main__':
    main()
