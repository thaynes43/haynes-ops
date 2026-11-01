"""OP343 create-only preservation of an audio revision; previous movies intact."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

OUT=Path('/home/dev/artifacts/butter-audio-redo')
REMOTE=['kubectl','exec','-i','-n','ai','deploy/wan-video','-c','api','--','python3','-c']


def main():
    if (OUT/'production.json').exists():raise RuntimeError('Already preserved; inspect before retry')
    v=json.loads((OUT/'validation.json').read_text())
    assert v['video_unchanged'] and v['decoded_frames']==480 and abs(v['duration_s']-20)<0.05
    assert v['audio']['clipped_samples']==0 and v['audio']['true_peak_dbtp']<=-6
    names=['crunchy-butter-soft-audio.mp4','soft-crunch.wav','soft-texture.wav',
           'sand.wav','sand.json','cork-sand.wav','cork-sand.json','spots.json',
           'mastering.json','validation.json','timing-sheet.jpg']
    # Finish all local preflight before the first remote write.
    missing=[n for n in names if not (OUT/n).is_file()]
    if missing:raise RuntimeError('Missing inputs: '+str(missing))
    digests={n:hashlib.sha256((OUT/n).read_bytes()).hexdigest() for n in names}
    assert digests[names[0]]==v['sha256']
    for name in ['sand','cork-sand']:
        assert digests[name+'.wav']==json.loads((OUT/(name+'.json')).read_text())['audio']['sha256']
    upload='''import hashlib,pathlib,sys,json
p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);h=hashlib.sha256()
with p.open('xb') as f:
 while True:
  b=sys.stdin.buffer.read(1048576)
  if not b:break
  f.write(b);h.update(b)
if h.hexdigest()!=sys.argv[2]:p.unlink();raise RuntimeError('Checksum mismatch')
print(json.dumps({'path':str(p),'bytes':p.stat().st_size,'sha256':h.hexdigest()}))
'''
    assets=[]
    for n in names:
        with (OUT/n).open('rb') as f:
            r=subprocess.run(REMOTE+[upload,'/workspace/output/productions/op343/'+n,digests[n]],
                             stdin=f,capture_output=True,check=True,timeout=120)
        assets.append(json.loads(r.stdout))
    record={'id':uuid.uuid4().hex,'kind':'edited_production','story':'OP343',
        'title':'Crunchy Butter — Soft Timed Audio','state':'succeeded','created_at':time.time(),
        'finished_at':time.time(),'telemetry':[],'output':'productions/op343/'+names[0],
        'bytes':v['bytes'],'sha256':v['sha256'],'assets':assets,'preset':'audio-revision-20s',
        'parent_production':'89f6c15276ad4f0b86622563cb8e5547',
        'video_packet_sha256':v['video_packet_sha256'],
        'provenance':'Original20s video stream copied unchanged. New sand Small-SFX cue downpitched, lowpassed and placed as ten individually timed motion-linked Foley spots. Lower-level master. Approximate editorial sync, listening approval unverified. Prior version preserved.'}
    publish='''import json,sys,pathlib,uuid,os
j=json.load(sys.stdin);r=pathlib.Path('/workspace/jobs');p=r/('editorial-'+uuid.uuid4().hex+'.tmp')
p.write_text(json.dumps(j,indent=2)+'\\n')
try:os.link(p,r/(j['id']+'.json'))
finally:p.unlink()
'''
    subprocess.run(REMOTE+[publish],input=json.dumps(record).encode(),capture_output=True,check=True,timeout=30)
    with (OUT/'production.json').open('x') as f:f.write(json.dumps(record,indent=2)+'\n')
    print('https://video.haynesops.com/videos/'+record['id']+'/file')


if __name__=='__main__':main()
