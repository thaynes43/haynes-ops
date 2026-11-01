"""OP345 create-only preservation of original doll art, speech, edit and evidence."""
import hashlib,json,subprocess,time,uuid
from pathlib import Path
from doll_speech import OUT
REMOTE=['kubectl','exec','-i','-n','ai','deploy/wan-video','-c','api','--','python3','-c']
def main():
    if (OUT/'production.json').exists():raise FileExistsError('Already preserved')
    v=json.loads((OUT/'validation.json').read_text());assert v['video']['decoded_frames']==480 and v['video']['face_source_invariant_per_shot'] and v['video']['expression_changed_on_each_return']
    assert v['audio']['clipped_samples']==0 and v['audio']['true_peak_dbfs']<=-2.5
    assert json.loads((OUT/'dialogue-transcript.json').read_text())['normalized_match']
    names=['crunchy-butter-dolls.mp4','doll-soundtrack.wav','dialogue-only.wav','speech.json','timeline.json','shots.json','frame-transforms.json','validation.json','contact-sheet.jpg','dialogue-transcript.json','prompts.json','asset-manifest.json','a-skeptical.png','a-shocked.png','b-smug.png','b-delighted.png','set.png']+[f'voice-{i}.wav' for i in range(1,5)]
    digests={n:hashlib.sha256((OUT/n).read_bytes()).hexdigest() for n in names}
    assert digests[names[0]]==v['sha256']
    for record in json.loads((OUT/'speech.json').read_text())['lines']:assert digests[record['file']]==record['sha256']
    code='''import pathlib,sys,hashlib,json
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
        with (OUT/n).open('rb') as f:r=subprocess.run(REMOTE+[code,'/workspace/output/productions/op345/'+n,digests[n]],stdin=f,capture_output=True,check=True,timeout=120)
        assets.append(json.loads(r.stdout))
    record={'id':uuid.uuid4().hex,'kind':'edited_production','story':'OP345','title':'Crunch Costs Extra — Fashion Doll Butter Lab','state':'succeeded','created_at':time.time(),'finished_at':time.time(),'telemetry':[],'output':'productions/op345/'+names[0],'bytes':v['bytes'],'sha256':v['sha256'],'assets':assets,'preset':'rigid-doll-dialogue-20s','provenance':{'images':'Five original built-in imagegen raster assets; reference-inspired fashion dolls. Input photo not published.','motion':'Deterministic rigid whole-doll cutout puppetry. Face art invariant within each shot; expression switches on hard cuts only.','voices':'Existing Kokoro-82M bf_emma andaf_bella, no voice cloning and no lip sync.','sfx':'Low-level excerpt of OP344 ControlFoley master under the reaction shot.','human_approval':'No claim of Tom viewing/listening approval.'}}
    publish='''import pathlib,sys,json,uuid,os
j=json.load(sys.stdin);root=pathlib.Path('/workspace/jobs');temp=root/('editorial-'+uuid.uuid4().hex+'.tmp');temp.write_text(json.dumps(j,indent=2)+'\\n')
try:os.link(temp,root/(j['id']+'.json'))
finally:temp.unlink()
'''
    subprocess.run(REMOTE+[publish],input=json.dumps(record).encode(),capture_output=True,check=True,timeout=30)
    with (OUT/'production.json').open('x') as f:json.dump(record,f,indent=2)
    print('https://video.haynesops.com/videos/'+record['id']+'/file')
if __name__=='__main__':main()
