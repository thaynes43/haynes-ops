"""Private loopback-only ControlFoley service sharing the Wan compute lease."""
import csv
import fcntl
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

ROOT=Path(os.environ.get('VIDEO_WORKSPACE','/workspace'))
WEIGHTS=Path(os.environ.get('FOLEY_WEIGHTS','/workspace/controlfoley-models'))
CARD='GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a'
ID=re.compile(r'^[0-9a-f]{32}$')
LOCK=threading.Lock()
ACTIVE=None


def request(port,path,body=None):
    data=None if body is None else json.dumps(body).encode()
    with urlopen(Request(f'http://127.0.0.1:{port}'+path,data=data,
                         headers={'Content-Type':'application/json'}),timeout=20) as r:
        raw=r.read()
        if not raw and port==8188 and path=='/free':return None
        return json.loads(raw)


def acquire():
    lease=(ROOT/'compute.lock').open('a')
    try:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB);return lease
    except BlockingIOError:
        lease.close();return None


def job_path(job_id):
    if not ID.fullmatch(job_id):raise ValueError('Invalid job ID')
    return ROOT/'foley-jobs'/(job_id+'.json')


def save(j):
    p=job_path(j['id']);p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_suffix('.tmp');temp.write_text(json.dumps(j,indent=2)+'\n');temp.replace(p)


def source_path(job_id):
    if not ID.fullmatch(job_id):raise ValueError('Invalid source ID')
    j=json.loads((ROOT/'jobs'/(job_id+'.json')).read_text())
    if j['state']!='succeeded':raise ValueError('Source is not complete')
    base=(ROOT/'output').resolve();p=(base/j['output']).resolve()
    if not p.is_relative_to(base) or p.suffix.lower()!='.mp4' or not p.is_file():
        raise ValueError('Invalid source')
    return p


def validate(data):
    source=data.get('source_job_id','');source_path(source)
    start=data.get('start_seconds',0);duration=data.get('duration',8)
    if any(type(n) not in (int,float) or not math.isfinite(n) for n in (start,duration)):
        raise ValueError('Invalid time')
    if not 0<=start<=300 or not 0.7<=duration<=8:raise ValueError('Invalid window')
    seed=data.get('seed',3441)
    if type(seed) is not int or not 0<=seed<2**32:raise ValueError('Invalid seed')
    prompt=data.get('prompt');negative=data.get('negative_prompt','')
    if not isinstance(prompt,str) or not 1<=len(prompt.strip())<=2048:raise ValueError('Invalid prompt')
    if not isinstance(negative,str) or len(negative)>2048:raise ValueError('Invalid negative prompt')
    return {'source_job_id':source,'start_seconds':start,'duration':duration,'seed':seed,
            'prompt':prompt.strip(),'negative_prompt':negative}


def gpu():
    result=subprocess.run(['nvidia-smi','--id='+CARD,
        '--query-gpu=uuid,memory.used,utilization.gpu,temperature.gpu','--format=csv,noheader,nounits'],
        capture_output=True,text=True,check=True,timeout=10)
    rows=list(csv.reader(io.StringIO(result.stdout)))
    if len(rows)!=1 or rows[0][0].strip()!=CARD:raise RuntimeError('Unexpected GPU identity')
    r=rows[0]
    return {'time':time.time(),'uuid':CARD,'memory_mib':float(r[1]),'utilization_percent':float(r[2]),'temperature_c':float(r[3])}


def idle():
    h=request(8080,'/health');q=request(8188,'/queue')
    if h.get('accepting_jobs') is not True or h.get('active_job') is not None or 'active_job' not in h:
        raise RuntimeError('Live Wan hold or active work')
    if not isinstance(q.get('queue_running'),list) or not isinstance(q.get('queue_pending'),list):
        raise RuntimeError('Malformed engine queue; fail closed')
    if q['queue_running'] or q['queue_pending']:raise RuntimeError('Unknown engine work; preserved')


def generate(j,lease):
    global ACTIVE
    child=None
    directory=ROOT/'output'/'foley'/j['id']
    try:
        directory.mkdir(parents=True,exist_ok=False)
        idle();sample=gpu()
        if sample['utilization_percent']>5:raise RuntimeError('GPU busy; no competing job')
        # This evicts only cached models from an engine proved idle, never a job.
        request(8188,'/free',{'unload_models':True,'free_memory':True})
        for _ in range(36):
            idle();sample=gpu()
            if sample['memory_mib']<2048 and sample['utilization_percent']<=5 and sample['temperature_c']<62:break
            time.sleep(5)
        else:raise RuntimeError('GPU did not return to cool low-residual idle')
        src=source_path(j['source_job_id']);window=directory/'input.mp4'
        probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(src)],
                             capture_output=True,text=True,check=True,timeout=30)
        available=float(json.loads(probe.stdout)['format']['duration'])
        if j['start_seconds']+j['duration']>available+0.03:raise ValueError('Window exceeds source duration')
        subprocess.run(['ffmpeg','-hide_banner','-nostdin','-n','-threads','2','-filter_threads','2',
            '-ss',str(j['start_seconds']),'-i',str(src),'-t',str(j['duration']),'-an',
            '-vf','scale=384:-2','-c:v','libx264','-threads','2','-preset','fast','-crf','18',str(window)],
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True,timeout=120)
        # Recheck after preprocessing; refuse unknown direct Comfy submissions.
        idle();sample=gpu()
        if sample['utilization_percent']>5 or sample['memory_mib']>=2048:raise RuntimeError('GPU acquired by unknown work')
        output=directory/'output.wav'
        env=os.environ.copy();env.update(CUDA_VISIBLE_DEVICES=CARD,OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2')
        cmd=['python','/opt/controlfoley-tools/run.py','--video',str(window),'--output',str(output),
             '--prompt',j['prompt'],'--negative-prompt',j['negative_prompt'],'--seed',str(j['seed']),
             '--duration',str(j['duration']),'--weights-dir',str(WEIGHTS)]
        j.update(state='running',started_at=time.time());save(j)
        with (directory/'inference.log').open('xb') as log:
            child=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
            deadline=time.monotonic()+600
            while child.poll() is None:
                if time.monotonic()>=deadline:raise TimeoutError('Audio inference exceeded10minutes')
                idle();sample=gpu();j['telemetry'].append(sample);save(j)
                if sample['temperature_c']>=88:raise RuntimeError('GPU reached audio job88Cstop; terminating owned child')
                time.sleep(2)
        if child.returncode!=0:raise RuntimeError('ControlFoley failed; inspect owned inference log')
        if not output.is_file() or not output.stat().st_size:raise RuntimeError('Audio output missing')
        j.update(state='succeeded',finished_at=time.time(),output=str(output.relative_to(ROOT/'output')),
                 provenance=json.loads(Path(str(output)+'.json').read_text()),bytes=output.stat().st_size)
    except Exception as exc:
        j.update(state='failed',error=type(exc).__name__+': '+str(exc)[:200],finished_at=time.time())
    finally:
        try:
            if child is not None and child.poll() is None:
                try:os.killpg(child.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:os.killpg(child.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    child.wait(timeout=10)
            save(j)
        finally:
            lease.close()
            with LOCK:ACTIVE=None


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def setup(self):super().setup();self.connection.settimeout(15)
    def respond(self,status,data):
        b=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        try:
            if self.path=='/health':
                self.respond(200,{'service':'ControlFoley','active_job':ACTIVE,'models_present':(WEIGHTS/'manifest.json').is_file()});return
            parts=self.path.strip('/').split('/')
            if len(parts) not in (2,3) or parts[0]!='foley':self.respond(404,{'error':'Not found'});return
            j=json.loads(job_path(parts[1]).read_text())
            if len(parts)==2:self.respond(200,j);return
            if parts[2]!='file' or j['state']!='succeeded':self.respond(409,{'error':'Audio not ready'});return
            base=(ROOT/'output'/'foley'/j['id']).resolve();p=(ROOT/'output'/j['output']).resolve()
            if not p.is_relative_to(base) or p.suffix!='.wav':raise ValueError('Invalid output')
            self.send_response(200);self.send_header('Content-Type','audio/wav')
            self.send_header('Content-Length',str(p.stat().st_size));self.end_headers()
            with p.open('rb') as f:
                while b:=f.read(1048576):self.wfile.write(b)
        except (ValueError,FileNotFoundError):self.respond(404,{'error':'Not found'})
        except Exception:self.respond(503,{'error':'Worker unavailable'})
    def do_POST(self):
        global ACTIVE
        if self.path!='/foley':self.respond(404,{'error':'Not found'});return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=8192:raise ValueError('Body limit')
            data=validate(json.loads(self.rfile.read(length)))
            with LOCK:
                if ACTIVE:self.respond(409,{'error':'Audio busy'});return
                if not (WEIGHTS/'manifest.json').is_file():self.respond(503,{'error':'Models not ready'});return
                lease=acquire()
                if lease is None:self.respond(409,{'error':'Video/audio compute busy'});return
                try:
                    j={'id':uuid.uuid4().hex,'kind':'video_conditioned_audio','state':'queued',
                       'created_at':time.time(),'telemetry':[],**data}
                    save(j);ACTIVE=j['id']
                    threading.Thread(target=generate,args=(j,lease),daemon=True).start()
                except Exception:lease.close();ACTIVE=None;raise
            self.respond(202,{'id':j['id'],'status_url':'/foley/'+j['id']})
        except (ValueError,TypeError,AttributeError,FileNotFoundError):self.respond(400,{'error':'Invalid source, prompt, seed or window'})
        except Exception:self.respond(503,{'error':'Worker unavailable'})


def main():
    directory=ROOT/'foley-jobs';directory.mkdir(parents=True,exist_ok=True)
    for p in directory.glob('*.json'):
        j=json.loads(p.read_text())
        if j.get('state') in ('queued','running'):
            j.update(state='failed',error='Worker restarted; not replayed',finished_at=time.time());save(j)
    ThreadingHTTPServer(('127.0.0.1',8189),Handler).serve_forever()


if __name__=='__main__':main()
