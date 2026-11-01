"""OP342: four finite reference-conditioned creative jobs, no deployed edits.

The installed API module supplies UUID checks, owned-job watchdog/cancellation,
persisted jobs and telemetry. Only this separate invocation's make_graph function
receives a job-local content graph. The running HTTP server/config is untouched.
ComfyUI executes one graph at a time; unknown work is preserved and stops pickup.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

import av
from PIL import Image

OUT=Path('/home/dev/artifacts/crunchy-butter-asmr')
SOURCE=Path('/home/dev/artifacts/crunchy-butter/crunch.mp4')
REMOTE=['kubectl','exec','-i','-n','ai','deploy/wan-video','-c','api','--','python3','-c']
STYLE=(' Keep exactly the same bright yellow squishy toy, existing pink and blue foam stars, '
       'lavender adult gloves, turquoise mat and pale pink background as the starting image. '
       'One very slow satisfying squeeze, hold and gentle release. Close macro texture detail, '
       'soft even studio light, slow smooth camera, stable object identity, no new decorations. '
       'Toy remains intact. No text, no titles, no graphics, no faces, no hats, no food or eating.')
PROMPTS=[
 'The camera slowly arcs a few degrees toward the right while the fingertips gently compress the crunchy yellow squishy from its sides, then let it expand.'+STYLE,
 'The camera gradually tilts to a slightly higher three-quarter macro angle. The same gloved fingertips press the same star-textured yellow squishy, hold, and release slowly.'+STYLE,
 'The camera gently arcs left a few degrees across the same crunchy surface. Fingers perform one slow deep compression, revealing existing tiny surface ridges, then release.'+STYLE,
 'The camera gently returns toward the original frontal macro angle. The same gloved hands slowly squeeze and relax the same star-textured squishy, ending in a calm relaxed hold.'+STYLE,
]


def remote(code,payload=None,timeout=30):
    return subprocess.run(REMOTE+[code],input=None if payload is None else json.dumps(payload).encode(),
                          capture_output=True,check=True,timeout=timeout).stdout


def upload(path,index):
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    code='''import pathlib,sys,hashlib
p=pathlib.Path('/workspace/input/op342')/sys.argv[1];p.parent.mkdir(parents=True,exist_ok=True)
b=sys.stdin.buffer.read();assert len(b)<10000000 and hashlib.sha256(b).hexdigest()==sys.argv[2]
with p.open('xb') as f:f.write(b)
print(hashlib.sha256(p.read_bytes()).hexdigest())
'''
    name=f'reference-{index}.png'
    with path.open('rb') as f:
        r=subprocess.run(REMOTE+[code,name,digest],stdin=f,capture_output=True,check=True,timeout=30)
    assert r.stdout.decode().strip()==digest
    return 'op342/'+name,digest


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT/'run.json').exists() or any(OUT.glob('shot-*.json')):
        raise RuntimeError('Existing production evidence: inspect, never automatically resume')
    (OUT/'run.json').write_text(json.dumps({'started_at':time.time(),'prompts':PROMPTS,
                                         'source':str(SOURCE),'source_frame':40,'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest()},indent=2)+'\n')
    c=av.open(str(SOURCE));s=c.streams.video[0];s.thread_count=2
    for i,f in enumerate(c.decode(s)):
        if i==40:
            f.to_image().crop((442,0,838,704)).resize((576,1024),Image.Resampling.LANCZOS).save(OUT/'reference-0.png');break
    c.close()
    template=json.loads(Path('kubernetes/main/apps/ai/wan-video/app/workflow.json').read_text())
    started=time.monotonic();records=[]
    for i,prompt in enumerate(PROMPTS):
        if time.monotonic()-started>1800:raise RuntimeError('Submission checkpoint reached')
        # Wait naturally for cool idle; refuse any unknown engine job.
        for attempt in range(30):
            sample=json.loads(remote('import sys,json,urllib.request;sys.path.insert(0,"/config/video");import api;h=json.load(urllib.request.urlopen("http://127.0.0.1:8080/health",timeout=10));print(json.dumps({"health":h,"sample":api.gpu_sample(),"queue":api.engine("/queue")}))'))
            health=sample['health']
            if health.get('accepting_jobs') is not True or 'active_job' not in health or health['active_job'] is not None:
                raise RuntimeError('Live API is busy or holding submissions; preserve hold and stop')
            q=sample['queue']
            if q.get('queue_running') or q.get('queue_pending'):raise RuntimeError('Unknown engine work; preserve it and stop')
            if all(g['temperature_c']<62 and g['utilization_percent']<=5 for g in sample['sample']['gpus']):break
            time.sleep(10)
        else:raise RuntimeError('Pair did not return to cool idle')
        if time.monotonic()-started>1800:raise RuntimeError('Checkpoint reached during cooldown')
        image,digest=upload(OUT/f'reference-{i}.png',i)
        job={'id':uuid.uuid4().hex,'state':'queued','preset':'op342-i2v-576x1024-121f','seed':34210+i,
             'created_at':time.time(),'telemetry':[],'kind':'reference_conditioned_render',
             'reference_image':image,'reference_sha256':digest,'story':'OP342'}
        graph=json.loads(json.dumps(template))
        graph['4']['inputs']['text']=prompt
        graph['5']['inputs']['text']='changing toy identity, new objects, faces, eyes, hats, text, subtitles, watermark, camera shake, fast motion, cuts, blurry, distorted hands'
        graph['8']['inputs'].update(width=576,height=1024,length=121,start_image=['13',0])
        graph['9']['inputs'].update(seed=job['seed'],steps=20)
        graph['12']['inputs']['filename_prefix']='video/'+job['id']
        graph['13']={'class_type':'LoadImage','inputs':{'image':image}}
        graph_file=OUT/f'graph-{i}.json';graph_file.write_text(json.dumps(graph,indent=2)+'\n')
        job['graph_sha256']=hashlib.sha256(graph_file.read_bytes()).hexdigest()
        (OUT/f'shot-{i}-submission.json').write_text(json.dumps(job,indent=2)+'\n')
        print(json.dumps({'shot':i+1,'event':'submitting','id':job['id'],'reference_sha256':digest}),flush=True)
        code='''import sys,json,urllib.request,time
sys.path.insert(0,'/config/video');import api
p=json.load(sys.stdin);job=p['job'];graph=p['graph']
h=json.load(urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=10))
if h.get('accepting_jobs') is not True or 'active_job' not in h or h['active_job'] is not None:
 raise RuntimeError('Live API busy or hold; no submission')
job['live_admission_readback']={'time':time.time(),'health':h}
original_engine=api.engine
def observed_engine(path,body=None):
 if path.startswith('/history/'):
  q=original_engine('/queue')
  foreign=[e[1] for e in q.get('queue_running',[])+q.get('queue_pending',[]) if e[1]!=job.get('prompt_id')]
  if foreign:job['unexpected_work_seen']=sorted(set(job.get('unexpected_work_seen',[])+foreign))
 return original_engine(path,body)
api.engine=observed_engine
api.make_graph=lambda *args:graph
api.save(job);api.generate(job,p['prompt'])
print(json.dumps(job))
'''
        raw=remote(code,{'job':job,'graph':graph,'prompt':prompt},timeout=1320)
        result=json.loads(raw);(OUT/f'shot-{i}.json').write_text(json.dumps(result,indent=2)+'\n')
        if result['state']!='succeeded':raise RuntimeError('Owned job failed; inspect before any further render')
        fetch='import urllib.request,sys;sys.stdout.buffer.write(urllib.request.urlopen("http://127.0.0.1:8080/videos/'+job['id']+'/file",timeout=30).read())'
        clip=OUT/f'shot-{i}.mp4';clip.write_bytes(remote(fetch,timeout=45))
        c=av.open(str(clip));s=c.streams.video[0];s.thread_count=2;count=0;last=None
        for f in c.decode(s):
            if count==0:f.to_image().save(OUT/f'first-{i}.png')
            if count==60:f.to_image().save(OUT/f'middle-{i}.png')
            last=f.to_image();count+=1
        c.close();assert count==121 and last is not None
        last.save(OUT/f'reference-{i+1}.png')
        records.append({'index':i,'id':job['id'],'reference_sha256':digest,'clip_sha256':hashlib.sha256(clip.read_bytes()).hexdigest()})
        (OUT/'sources.json').write_text(json.dumps(records,indent=2)+'\n')
        print(json.dumps({'shot':i+1,'event':'saved','frames':count}),flush=True)
        if result.get('unexpected_work_seen'):raise RuntimeError('Competing work observed; own output preserved, stop further submissions')
    print('Four reference-linked shots saved; no more jobs.',flush=True)


if __name__=='__main__':main()
