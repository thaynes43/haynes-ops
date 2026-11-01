"""OP344 bounded useful audio takes from the existing ASMR movie; no video jobs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import wave

OUT=Path('/home/dev/artifacts/controlfoley-asmr')
PARENT='89f6c15276ad4f0b86622563cb8e5547'
REMOTE=['kubectl','exec','-i','-n','ai','deploy/wan-video','-c','api','--','python3','-c']
PROMPT=('Close-miked gentle low dry granular crunches exactly synchronized with the gloved fingers '
        'compressing and folding the yellow squishy. Fine soft sandy crumbling and muted tiny foam pops. '
        'Soft rounded transients, warm intimate texture. Quiet release friction. Silence when the hands '
        'hold still. No speech. No music.')
NEGATIVE=('rubber squeaks, squealing, whistling, chirping, high-pitched tones, ringing, metallic sounds, '
          'glass, crinkly cellophane, sharp loud snaps, harsh clicks, distortion, static hiss, music, '
          'speech, breathing, eating, chewing')


def call(path,body=None,binary=False):
    code='''import json,sys,urllib.request
p=json.load(sys.stdin);b=None if p['body'] is None else json.dumps(p['body']).encode()
r=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8189'+p['path'],data=b,headers={'Content-Type':'application/json'}),timeout=30)
sys.stdout.buffer.write(r.read())
'''
    result=subprocess.run(REMOTE+[code],input=json.dumps({'path':path,'body':body}).encode(),
                          capture_output=True,check=True,timeout=45).stdout
    return result if binary else json.loads(result)


def main():
    p=argparse.ArgumentParser();p.add_argument('--label',required=True)
    p.add_argument('--start',type=float,default=0);p.add_argument('--duration',type=float,default=8)
    p.add_argument('--seed',type=int,default=3441);p.add_argument('--prompt',default=PROMPT)
    args=p.parse_args()
    if not re.fullmatch('[a-z0-9-]{1,30}',args.label):raise ValueError('Invalid label')
    OUT.mkdir(exist_ok=True)
    if len(list(OUT.glob('*-submission.json')))>=8:raise RuntimeError('Eight-attempt production budget reached')
    reservation=OUT/(args.label+'-submission.json')
    body={'source_job_id':PARENT,'start_seconds':args.start,'duration':args.duration,
          'seed':args.seed,'prompt':args.prompt,'negative_prompt':NEGATIVE}
    # Never automatically resubmit after a lost response; retain this reservation.
    with reservation.open('x') as f:json.dump({'requested_at':time.time(),'body':body},f,indent=2)
    reply=call('/foley',body);job_id=reply['id']
    (OUT/(args.label+'-accepted.json')).write_text(json.dumps(reply,indent=2)+'\n')
    print(json.dumps({'label':args.label,'id':job_id,'event':'accepted'}),flush=True)
    deadline=time.monotonic()+960
    while time.monotonic()<deadline:
        job=call('/foley/'+job_id)
        if job['state'] in ('succeeded','failed'):break
        time.sleep(5)
    else:raise TimeoutError('Status wait expired; inspect owned job before further work')
    (OUT/(args.label+'.json')).write_text(json.dumps(job,indent=2)+'\n')
    if job['state']!='succeeded':raise RuntimeError(job.get('error','Audio job failed'))
    b=call('/foley/'+job_id+'/file',binary=True)
    digest=hashlib.sha256(b).hexdigest()
    assert digest==job['provenance']['output_sha256']
    path=OUT/(args.label+'.wav')
    with path.open('xb') as f:f.write(b)
    with wave.open(str(path)) as w:
        assert w.getframerate()==44100 and w.getnchannels() in (1,2)
        assert w.getnframes()==round(args.duration*44100)
        assert w.getsampwidth()==3
        while w.readframes(44100):pass
    print(json.dumps({'label':args.label,'event':'saved','sha256':digest}),flush=True)


if __name__=='__main__':main()
