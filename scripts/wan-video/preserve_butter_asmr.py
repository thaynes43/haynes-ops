"""Create-only preservation of the OP342 reference-linked ASMR asset on the existing PVC."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

OUT=Path('/home/dev/artifacts/crunchy-butter-asmr')
REMOTE=['kubectl','exec','-i','-n','ai','deploy/wan-video','-c','api','--','python3','-c']

if __name__=='__main__':
    if (OUT/'production.json').exists():raise RuntimeError('Production already preserved')
    validation=json.loads((OUT/'validation.json').read_text())
    assert validation['video']['decoded_frames']==480 and abs(validation['duration_seconds']-20)<0.05
    names=['crunchy-butter-asmr.mp4','crunchy-butter-asmr.wav','asmr-main.wav','asmr-main.json',
           'validation.json','audio-loudness.json','contact-sheet.jpg','run.json','sources.json','render-report.json','runtime.json']
    sources=[]
    for i in range(4):
        j=json.loads((OUT/f'shot-{i}.json').read_text());assert j['state']=='succeeded'
        assert j['reference_sha256']==hashlib.sha256((OUT/f'reference-{i}.png').read_bytes()).hexdigest()
        assert j['graph_sha256']==hashlib.sha256((OUT/f'graph-{i}.json').read_bytes()).hexdigest()
        names += [f'shot-{i}.mp4',f'shot-{i}.json',f'graph-{i}.json',f'reference-{i}.png']
        sources.append({'index':i,'id':j['id'],'sha256':hashlib.sha256((OUT/f'shot-{i}.mp4').read_bytes()).hexdigest()})
    names += ['reference-4.png']
    assert len(sources)==4
    assert hashlib.sha256((OUT/'crunchy-butter-asmr.mp4').read_bytes()).hexdigest()==validation['sha256']
    missing=[name for name in names if not (OUT/name).is_file()]
    if missing:raise RuntimeError('Missing production inputs before any upload: '+', '.join(missing))
    assert validation['audio']['clipped_samples']==0 and validation['audio']['true_peak_dbfs']<=-1
    digests={name:hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in names}
    code='''import hashlib,pathlib,sys,json
p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);h=hashlib.sha256()
with p.open('xb') as f:
 while True:
  b=sys.stdin.buffer.read(1048576)
  if not b:break
  f.write(b);h.update(b)
if h.hexdigest()!=sys.argv[2]:p.unlink();raise RuntimeError('Transferred checksum mismatch')
print(json.dumps({'path':str(p),'bytes':p.stat().st_size,'sha256':h.hexdigest()}))
'''
    assets=[]
    for name in names:
        path=OUT/name;digest=digests[name]
        with path.open('rb') as f:
            r=subprocess.run(REMOTE+[code,'/workspace/output/productions/op342/'+name,digest],stdin=f,
                             capture_output=True,check=True,timeout=120)
        assets.append(json.loads(r.stdout))
    record={'id':uuid.uuid4().hex,'kind':'edited_production','story':'OP342','title':'Crunchy Butter ASMR',
            'state':'succeeded','created_at':time.time(),'finished_at':time.time(),'telemetry':[],
            'output':'productions/op342/crunchy-butter-asmr.mp4','bytes':validation['bytes'],'sha256':validation['sha256'],
            'source_jobs':sources,'assets':assets,'preset':'editorial-20s-portrait',
            'provenance':'Four sequential reference-conditioned dual3090 Wan2.2 clips, 576x1024 native at24fps, first120frames each upscaled1080x1920; separate Stable Audio3 Small-SFX crunch track. No text overlays, dialogue or music. Toy play, not food. Human listening approval unverified.'}
    publish='''import json,sys,pathlib,uuid,os
j=json.load(sys.stdin);assert j['kind']=='edited_production' and len(j['id'])==32
r=pathlib.Path('/workspace/jobs');temp=r/('editorial-'+uuid.uuid4().hex+'.tmp')
temp.write_text(json.dumps(j,indent=2)+'\\n')
try:os.link(temp,r/(j['id']+'.json'))
finally:temp.unlink()
'''
    subprocess.run(REMOTE+[publish],input=json.dumps(record).encode(),capture_output=True,check=True,timeout=30)
    with (OUT/'production.json').open('x') as f:f.write(json.dumps(record,indent=2)+'\n')
    print('https://video.haynesops.com/videos/'+record['id']+'/file')
