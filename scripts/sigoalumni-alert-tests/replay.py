#!/usr/bin/env python3
"""Historical replay of the sigoalumni alert expressions (read-only Prometheus queries).

    nice -n 19 python3 -I replay.py replay-receipt-raw.json

Evaluates the expressions in prometheusrule.yaml with query_range at 30s steps in one-day
chunks (replay-receipt.json is the committed, annotated result). Needs yq and egress to
prometheus.haynesops.com. It applies no rule state and no for: duration."""
import json, subprocess, sys, time, urllib.parse, urllib.request, datetime
import os
R=os.path.join(os.path.dirname(os.path.abspath(__file__)),'../../kubernetes/main/apps/frontend/sigoalumni/app/prometheusrule.yaml')
exprs={}
out=subprocess.run(['yq','-o=json','.spec.groups[].rules[]',R],capture_output=True,text=True,check=True).stdout
dec=json.JSONDecoder(); i=0
while i<len(out):
    while i<len(out) and out[i] in ' \n': i+=1
    if i>=len(out): break
    o,j=dec.raw_decode(out,i); i=j; exprs[o['alert']]=o['expr'].strip()
def q(expr,start,end,step=30):
    p=urllib.parse.urlencode({'query':expr,'start':start,'end':end,'step':step}).encode()
    for k in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request('https://prometheus.haynesops.com/api/v1/query_range',data=p),timeout=120))
        except Exception as e:
            err=e; time.sleep(3)
    raise err
def ts(s): return int(datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def fmt(t): return datetime.datetime.fromtimestamp(t,datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def run(name,start,end):
    s,e=ts(start),ts(end); pts={}
    t=s
    while t<e:
        te=min(t+86400-30,e)
        r=q(exprs[name],t,te)
        if r['status']!='success': raise SystemExit(r)
        for series in r['data']['result']:
            key=json.dumps(series['metric'],sort_keys=True)
            pts.setdefault(key,[]).extend(int(float(v[0])) for v in series['values'])
        t=te+30
    res=[]
    for key,tt in pts.items():
        tt=sorted(set(tt)); ep=[]; a=b=tt[0]
        for x in tt[1:]:
            if x-b<=30: b=x
            else: ep.append((a,b)); a=b=x
        ep.append((a,b))
        res.append({'labels':json.loads(key),'eval_points':len(tt),'episodes':[{'first':fmt(a),'last':fmt(b),'eval_points':(b-a)//30+1} for a,b in ep]})
    return res
windows={'incident_Oct16-20':('2026-10-16T00:00:00Z','2026-10-20T15:00:00Z'),'normal_Oct08-13':('2026-10-08T00:00:00Z','2026-10-13T00:00:00Z'),'thirty_day_Sep20-Oct20':('2026-09-20T00:00:00Z','2026-10-20T15:00:00Z')}
rec={'generated':fmt(int(time.time())),'note':'rule expressions evaluated by query_range at 30s steps against live Prometheus 3.15.0 history; no alert state, no for: applied','windows':windows,'results':{}}
names=[n for n in ('SigoalumniServiceEndpointsEmptyObserved','SigoalumniTelemetryIncomplete','SigoalumniReadinessProbeFailing') if n in exprs]
for w,(a,b) in windows.items():
    for n in names:
        t0=time.time(); r=run(n,a,b); rec['results'].setdefault(w,{})[n]=r
        print(w,n,[ (x['labels'],x['eval_points'],len(x['episodes'])) for x in r], round(time.time()-t0,1),'s',flush=True)
json.dump(rec,open(sys.argv[1],'w'),indent=1)
