"""One-shot OP344 producer using reviewed service guards; no installed code changes."""
import argparse,hashlib,json,subprocess,time,uuid,wave
from pathlib import Path
from produce_controlfoley_asmr import OUT,PARENT,PROMPT,NEGATIVE,call

def main():
    p=argparse.ArgumentParser();p.add_argument('--label',required=True);p.add_argument('--start',type=float,required=True);p.add_argument('--seed',type=int,default=3441);args=p.parse_args()
    OUT.mkdir(exist_ok=True)
    if len(list(OUT.glob('*-submission.json')))>=8:raise RuntimeError('Attempt cap')
    body={'source_job_id':PARENT,'start_seconds':args.start,'duration':8,'seed':args.seed,'prompt':PROMPT,'negative_prompt':NEGATIVE}
    job_id=uuid.uuid4().hex
    with (OUT/(args.label+'-submission.json')).open('x') as f:json.dump({'requested_at':time.time(),'id':job_id,'body':body,'invocation':'guarded_one_shot'},f,indent=2)
    source=(Path(__file__).parents[1]/'controlfoley/service.py').read_text()
    source=source.replace("if __name__=='__main__':main()",'')
    source+='\nimport sys\ndata=validate(json.loads(sys.argv[1]));lease=acquire()\nif lease is None:raise RuntimeError("Compute reserved; no competing job")\nj={"id":sys.argv[2],"kind":"video_conditioned_audio","state":"queued","created_at":time.time(),"telemetry":[],"invocation":"guarded_one_shot",**data}\nsave(j);generate(j,lease)\nprint(json.dumps({"id":j["id"],"state":j["state"],"error":j.get("error")}))\n'
    (OUT/(args.label+'-accepted.json')).write_text(json.dumps({'id':job_id,'invocation':'guarded_one_shot'})+'\n')
    print(json.dumps({'event':'starting','id':job_id}),flush=True)
    subprocess.run(['kubectl','exec','-i','-n','ai','deploy/wan-video','-c','foley','--','python','-',json.dumps(body),job_id],input=source.encode(),check=True,timeout=850)
    job=call('/foley/'+job_id);(OUT/(args.label+'.json')).write_text(json.dumps(job,indent=2)+'\n')
    if job['state']!='succeeded':raise RuntimeError(job.get('error'))
    b=call('/foley/'+job_id+'/file',binary=True);assert hashlib.sha256(b).hexdigest()==job['provenance']['output_sha256']
    with (OUT/(args.label+'.wav')).open('xb') as f:f.write(b)
    with wave.open(str(OUT/(args.label+'.wav'))) as w:assert w.getframerate()==44100 and w.getnframes()==8*44100 and w.getsampwidth()==3
    print(json.dumps({'event':'saved','id':job_id}),flush=True)
if __name__=='__main__':main()
