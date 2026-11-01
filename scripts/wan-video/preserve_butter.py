"""Create-only preservation of the OP341 editorial asset on the existing PVC."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

OUT=Path('/home/dev/artifacts/crunchy-butter')
REMOTE=['kubectl','exec','-i','-n','ai','deploy/wan-video','-c','api','--','python3','-c']

if __name__=='__main__':
    if (OUT/'production.json').exists():raise RuntimeError('Production already preserved')
    validation=json.loads((OUT/'validation.json').read_text())
    assert validation['video']['decoded_frames']==384 and abs(validation['duration_seconds']-16)<0.05
    names=['crunchy-butter-short.mp4','crunchy-butter-soundtrack.wav','dialogue-only.wav','speech.json',
           'sfx-squish.wav','sfx-crunch.wav','sfx-squish.json','sfx-crunch.json','shots.json',
           'validation.json','audio-loudness.json','contact-sheet.jpg','dialogue-transcript.json','dialogue-mixed-transcript.json']
    names += [f'voice-{i}.wav' for i in range(1,5)]
    shots=json.loads((OUT/'shots.json').read_text());sources=[]
    for shot in shots:
        name=shot['name'];j=json.loads((OUT/(name+'.json')).read_text());assert j['state']=='succeeded'
        names += [name+'.mp4',name+'.json']
        sources.append({'name':name,'id':j['id'],'sha256':hashlib.sha256((OUT/(name+'.mp4')).read_bytes()).hexdigest()})
    assert len(sources)==4
    assert hashlib.sha256((OUT/'crunchy-butter-short.mp4').read_bytes()).hexdigest()==validation['sha256']
    missing=[name for name in names if not (OUT/name).is_file()]
    if missing:raise RuntimeError('Missing production inputs before any upload: '+', '.join(missing))
    assert json.loads((OUT/'dialogue-mixed-transcript.json').read_text())['normalized_match']
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
            r=subprocess.run(REMOTE+[code,'/workspace/output/productions/op341/'+name,digest],stdin=f,
                             capture_output=True,check=True,timeout=120)
        assets.append(json.loads(r.stdout))
    record={'id':uuid.uuid4().hex,'kind':'edited_production','story':'OP341','title':'THE BUTTER LAB — Crunchy Butter',
            'state':'succeeded','created_at':time.time(),'finished_at':time.time(),'telemetry':[],
            'output':'productions/op341/crunchy-butter-short.mp4','bytes':validation['bytes'],'sha256':validation['sha256'],
            'source_jobs':sources,'assets':assets,'preset':'editorial-16s-portrait',
            'provenance':'Four distinct dual3090 Wan2.2 clips; fixed-voice Kokoro speech, separately generated Small-SFX squish/crunch, original chime, captions and edit. Toy play, not food. Separate narration, no lip-sync claim.'}
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
