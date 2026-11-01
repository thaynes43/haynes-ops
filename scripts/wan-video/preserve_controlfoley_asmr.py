"""OP344 create-only private delivery, preserving all preceding productions."""
import argparse,hashlib,json,re,subprocess,time,uuid
from pathlib import Path
OUT=Path('/home/dev/artifacts/controlfoley-asmr')
REMOTE=['kubectl','exec','-i','-n','ai','deploy/wan-video','-c','api','--','python3','-c']
def main():
    p=argparse.ArgumentParser();p.add_argument('--name',required=True);p.add_argument('--labels',nargs='+',required=True);args=p.parse_args()
    if not re.fullmatch('[a-z0-9-]{1,40}',args.name):raise ValueError('Invalid output name')
    v=json.loads((OUT/(args.name+'-validation.json')).read_text())
    assert v['audio']['clipped_samples']==0 and v['audio']['true_peak_dbtp']<=-6
    if v['duration_s']>=19.9:assert v['video_unchanged'] and v['decoded_frames']==480
    recordpath=OUT/(args.name+'-production.json');assert not recordpath.exists()
    names=[args.name+'.mp4',args.name+'.wav',args.name+'-validation.json']
    for label in args.labels:names.extend([label+'.wav',label+'.json',label+'-submission.json',label+'-accepted.json'])
    digests={n:hashlib.sha256((OUT/n).read_bytes()).hexdigest() for n in names}
    assert digests[args.name+'.mp4']==v['sha256']
    for label in args.labels:assert digests[label+'.wav']==json.loads((OUT/(label+'.json')).read_text())['provenance']['output_sha256']
    upload='''import pathlib,sys,hashlib,json
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
            r=subprocess.run(REMOTE+[upload,'/workspace/output/productions/op344/'+args.name+'/'+n,digests[n]],stdin=f,capture_output=True,check=True,timeout=120)
        assets.append(json.loads(r.stdout))
    record={'id':uuid.uuid4().hex,'kind':'edited_production','story':'OP344','title':'Crunchy Butter — Video-conditioned Foley','state':'succeeded','created_at':time.time(),'finished_at':time.time(),'telemetry':[],'output':'productions/op344/'+args.name+'/'+args.name+'.mp4','bytes':v['bytes'],'sha256':v['sha256'],'assets':assets,'preset':'controlfoley-audio-revision','parent_production':'89f6c15276ad4f0b86622563cb8e5547','source_foley_jobs':v['source_jobs'],'provenance':v}
    publish='''import json,sys,pathlib,uuid,os
j=json.load(sys.stdin);r=pathlib.Path('/workspace/jobs');p=r/('editorial-'+uuid.uuid4().hex+'.tmp');p.write_text(json.dumps(j,indent=2)+'\\n')
try:os.link(p,r/(j['id']+'.json'))
finally:p.unlink()
'''
    subprocess.run(REMOTE+[publish],input=json.dumps(record).encode(),capture_output=True,check=True,timeout=30)
    with recordpath.open('x') as f:json.dump(record,f,indent=2)
    print('https://video.haynesops.com/videos/'+record['id']+'/file')
if __name__=='__main__':main()
