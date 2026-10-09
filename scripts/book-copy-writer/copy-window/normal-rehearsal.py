#!/usr/bin/env python3
"""Closed Normal-only containment rehearsal. Preparation does not call APIs."""
import argparse
from contextlib import contextmanager
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

HERE=Path(__file__).resolve().parent
FILES=('normal-rehearsal.py','copy-recovery-watch.py','cached_source.py','window_contract.py','manifest-contract.json')
APPS=(('frontend','haynesnetwork'),('media','libretto'),('downloads','lazylibrarian'),('media','kavita'))
PARENTS=(('flux-system','cluster'),('flux-system','cluster-apps'))
JOBS=(('frontend','issue831-copy-source-census-1009-03'),('frontend','issue831-copy-selected-1009-03'),
      ('downloads','issue831-ll-source-1009-03'),('media','issue831-kavita-source-1009-03'),('media','issue831-lidarr-source-1009-03'))
BOUNDS={'exercise':90,'arm':120,'recovery':50,'safety_attempt':60,'retry':10}
HOST_PROOF=Path('/home/dev/work/hn-825-native-host-dependency-proof-1009.json')
HOST_PROOF_SHA='c405f58100d4ba9549742522201edd03a9c63984db613fbbae921101467c4a82'

def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
def event(value):
    raw=canonical(value)
    if len(raw)>512:return
    try:
        fd=sys.stdout.fileno();blocking=os.get_blocking(fd);os.set_blocking(fd,False)
        try:os.write(fd,raw)
        finally:os.set_blocking(fd,blocking)
    except Exception:pass
def require(ok,reason):
    if not ok:raise ValueError(reason)
def read_private(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and info.st_mode&0o077==0
                and info.st_nlink==1 and 0<info.st_size<=1024*1024,'private packet type/mode/cap')
        raw=os.read(fd,info.st_size+1);after=os.fstat(fd);current=os.stat(path,follow_symlinks=False)
        fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_nlink')
        require(len(raw)==info.st_size and all(getattr(info,f)==getattr(after,f)==getattr(current,f) for f in fields),'private packet changed')
        return raw
    finally:os.close(fd)
def modules():
    sys.path.insert(0,str(HERE))
    import cached_source as cache
    import window_contract as wc
    spec=importlib.util.spec_from_file_location('normal_rehearsal_watch',HERE/'copy-recovery-watch.py')
    watch=importlib.util.module_from_spec(spec);spec.loader.exec_module(watch)
    return cache,wc,watch
def start_ticks(pid):
    row=Path('/proc')/str(pid)/'stat'
    return row.read_text().rsplit(')',1)[1].split()[19]

@contextmanager
def registration_lock(runtime):
    import fcntl
    fd=os.open(runtime/'exercise-registration.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:os.close(fd)

def register_exercise(packet,ready):
    cache,_,_=modules();runtime=Path(packet['directory'])/'runtime'
    with registration_lock(runtime):
        require(not (runtime/'cancel').exists(),'exercise registration closed')
        state=assert_watch(ready,packet)
        require(not state.get('normal_rehearsal_restore_started_at'),'recovery already started')
        if os.getpgid(0)!=os.getpid():os.setsid()
        require(os.getpgid(0)==os.getpid(),'exercise needs its dedicated process group')
        cache.write_private(runtime/'exercise-owner.json',canonical({'schema':1,
            'packet_sha256':sha(canonical(packet)),'phase_token':packet['phase_token'],
            'pid':os.getpid(),'start_ticks':start_ticks(os.getpid()),'pgid':os.getpgid(0)}))

def group_members(pgid):
    entries=list(Path('/proc').iterdir());require(len(entries)<=4096,'process inventory cap')
    members={}
    for entry in entries:
        if not entry.name.isdigit():continue
        try:fields=(entry/'stat').read_text().rsplit(')',1)[1].split()
        except FileNotFoundError:continue
        if int(fields[2])==pgid and fields[0] not in ('Z','X'):
            members[int(entry.name)]=fields[19]
    return members

def signal_member(pid,birth,pgid):
    # Signal the captured process handle, never a possibly reused numeric PID or
    # group. Opening before validation also protects the final signal gap.
    try:fd=os.pidfd_open(pid,0)
    except ProcessLookupError:return
    try:
        try:fields=(Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()
        except FileNotFoundError:return
        if fields[19]!=birth:return
        require(int(fields[2])==pgid,'captured exercise member changed group')
        info=dict(line.split(':',1) for line in (Path('/proc/self/fdinfo')/str(fd)).read_text().splitlines() if ':' in line)
        if int(info['Pid'].strip())==-1:return
        require(int(info['Pid'].strip())==pid,'captured pidfd identity changed')
        try:signal.pidfd_send_signal(fd,signal.SIGKILL)
        except ProcessLookupError:pass
    finally:os.close(fd)

def retire_exercise(packet):
    cache,_,_=modules()
    runtime=Path(packet['directory'])/'runtime'
    with registration_lock(runtime):
        require((runtime/'cancel').exists(),'cancel before exercise retirement')
        path=runtime/'exercise-owner.json'
        if not path.exists():return
        owner_raw=read_private(path);owner=json.loads(owner_raw);pid=owner.get('pid')
        require(set(owner)=={'schema','packet_sha256','phase_token','pid','start_ticks','pgid'}
            and owner['schema']==1 and owner['packet_sha256']==sha(canonical(packet))
            and owner['phase_token']==packet['phase_token'] and type(pid) is int and pid>1
            and pid!=os.getpid() and owner['pgid']==pid,'exercise owner binding')
        retired=runtime/'exercise-retired.json'
        binding={'schema':1,'packet_sha256':sha(canonical(packet)),
            'phase_token':packet['phase_token'],'owner_sha256':sha(owner_raw),'original_group':pid,'group_members':{}}
        if retired.exists():
            value=json.loads(read_private(retired))
            require(set(value)==set(binding)|{'retired_at','basis'} and all(value[k]==v for k,v in binding.items())
                and isinstance(value['retired_at'],str) and value['basis'] in
                ('empty_original_group','replacement_pid','retired_owned_group'),'exercise retirement receipt changed')
            return
        try:current=start_ticks(pid)
        except FileNotFoundError:current=None
        members=group_members(pid)
        # Linux retains the number while the original PGID has members. A
        # different leader birth therefore identifies a replacement group;
        # an absent leader with surviving PGID members identifies our orphans.
        try:latest=start_ticks(pid)
        except FileNotFoundError:latest=None
        replacement=any(v not in (None,owner['start_ticks']) for v in (current,latest))
        basis='replacement_pid' if replacement else 'empty_original_group'
        if members and not replacement:
            until=time.monotonic()+2
            while members:
                require(time.monotonic()<until and len(members)<=64,'exercise group retirement unproved/cap')
                for member,birth in members.items():signal_member(member,birth,pid)
                try:current=start_ticks(pid)
                except FileNotFoundError:current=None
                if current not in (None,owner['start_ticks']):basis='replacement_pid';break
                members=group_members(pid)
                if members:time.sleep(.05)
            else:basis='retired_owned_group'
        # Empty original group means no request authority survives, including
        # when the old PID was reused in another group before first observation.
        cache.write_private(retired,canonical(dict(binding,basis=basis,retired_at=dt.datetime.now(dt.timezone.utc).isoformat())))

def prepare(output,repo,host_path=HOST_PROOF,host_sha=HOST_PROOF_SHA):
    cache,_,_=modules();output=Path(output).absolute();output.mkdir(mode=0o700,parents=False,exist_ok=False)
    package=output/'source';package.mkdir(mode=0o700);(output/'runtime').mkdir(mode=0o700)
    pins={}
    for name in FILES:
        raw=(HERE/name).read_bytes();path=package/name;path.write_bytes(raw);path.chmod(0o600);pins[name]=sha(raw)
    token=uuid.uuid4().hex;phase={'schema':1,'restore_pr':'normal-rehearsal','phase_token':token,
        'runtime_authorization':False,'owned_jobs':[dict(namespace=ns,name=name,uid=None,phase_token=token,writer=name==JOBS[1][1]) for ns,name in JOBS],
        'pg_leases':[dict(job_namespace=ns,job_name=name,job_uid=None,pod_uid=None,backend_pid=None,
            application_name='issue825-duplicate-share-fence-'+token if index==0 else 'issue831-manual-copy-writer') for index,(ns,name) in enumerate(JOBS[:2])]}
    cache.write_private(output/'phase.json',canonical(phase))
    binaries={name:str(Path(shutil.which(name)).resolve()) for name in ('git','gh','kubectl','flux','curl')}
    binaries['python']=str(Path(sys.executable).resolve())
    host_raw=Path(host_path).read_bytes();require(sha(host_raw)==host_sha,'reviewed host dependency receipt changed')
    host=json.loads(host_raw);require(sys.executable==host['interpreter']['executable'],'use reviewed dedicated host venv')
    cache.write_private(output/'host-dependency-proof.json',host_raw)
    packet={'schema':1,'type':'normal_only_hold_rehearsal','runtime_authorization':False,'phase_token':token,
        'directory':str(output),'repo_dir':str(Path(repo).resolve()),'source':pins,'phase_sha256':sha(canonical(phase)),
        'interpreter':sys.executable,'host_dependency_proof_sha256':host_sha,
        'binaries':{n:dict(path=p,sha256=sha(Path(p).read_bytes())) for n,p in binaries.items()},'bounds':BOUNDS}
    cache.write_private(output/'packet.json',canonical(packet))
    event({'prepared':True,'packet':str(output/'packet.json'),'sha256':sha(canonical(packet)),
        'runtime_authorization':False,'holdsChanged':0,'jobsCreated':0})

def load_packet(path,go):
    raw=read_private(path);require(sha(raw)==go,'exact packet GO mismatch');packet=json.loads(raw)
    require(packet.get('schema')==1 and packet.get('type')=='normal_only_hold_rehearsal'
            and packet.get('runtime_authorization') is False and packet.get('bounds')==BOUNDS,'closed Normal-only profile changed')
    directory=Path(packet['directory']);require(Path(path).absolute()==directory/'packet.json' and HERE==directory/'source','frozen source/packet path mismatch')
    require(directory.stat().st_uid==os.geteuid() and directory.stat().st_mode&0o077==0,'private directory ownership')
    require(set(packet['source'])==set(FILES),'frozen source closure')
    for name,digest in packet['source'].items():require(sha(read_private(HERE/name))==digest,'frozen source changed')
    require(sha(read_private(directory/'phase.json'))==packet['phase_sha256'],'closed phase changed')
    require(Path(sys.executable).resolve()==Path(packet['binaries']['python']['path']),'host interpreter changed')
    host_raw=read_private(directory/'host-dependency-proof.json');require(sha(host_raw)==packet['host_dependency_proof_sha256'],'host dependency proof changed')
    host=json.loads(host_raw);require(sys.executable==host['interpreter']['executable'] and sys.flags.isolated==1,'reviewed isolated host interpreter required')
    dep=host['dependency'];require(host['interpreter']['version']=='3.11.2' and dep['name']=='PyYAML' and dep['version']=='6.0.3'
        and dep['official_wheel_sha256']=='b8bb0864c5a28024fac8a632c443c87c5aa6f215c0b126c449ae1a150412f31d'
        and dep['no_deps'] is True and dep['only_binary'] is True and dep['require_hashes'] is True,'reviewed host dependency version/profile')
    for path,digest in host['dependency']['installed_files'].items():require(sha((Path(host['venv'])/path).read_bytes())==digest,'host YAML dependency changed')
    for row in packet['binaries'].values():require(sha(Path(row['path']).read_bytes())==row['sha256'],'host executable changed')
    return packet

def watcher(packet,initialize=True):
    cache,wc,watch=modules();directory=Path(packet['directory']);runtime=directory/'runtime'
    args=argparse.Namespace(pause='normal-rehearsal',restore='normal-rehearsal',include_kavita=True,
        phase_state=str(directory/'phase.json'),state=str(runtime/'watch-state.json'),stop=str(runtime/'cancel'),log=str(runtime/'watch.log'),
        repo_dir=packet['repo_dir'],manifest_contract=str(HERE/'manifest-contract.json'),cached_source_receipt=str(runtime/'unused-stop-seal'),
        cached_source_activation=str(runtime/'forbidden-activation'),deadline=170,arm_deadline=600)
    class NormalWatch(watch.Watchdog):
        def run(self,argv,timeout=45,input_text=None):
            require(argv[0] in packet['binaries'],'unapproved executable')
            require(not (argv[0]=='gh' or (argv[0]=='kubectl' and argv[1] in ('create','apply','replace'))),'no Git merge or resource producer')
            return super().run([packet['binaries'][argv[0]]['path'],*argv[1:]],timeout=timeout,input_text=input_text)
        def tick(self):
            require(not Path(args.cached_source_activation).exists() and not Path(args.cached_source_receipt).exists(),'Stop artifacts forbidden in Normal rehearsal')
            if self.state.get('normal_only_rehearsal_complete'):
                with cache.wall_guard(BOUNDS['safety_attempt']):
                    self.cleanup_phase_jobs();self.recover_cluster(self.current_main())
                self.state['normal_rehearsal_reverified_at']=watch.stamp();self.save();return True
            started=watch.epoch(self.state['armed_at'])
            if not self.stop.exists() and not getattr(self,'requested_cancel',False) and time.time()<started+BOUNDS['arm']:return False
            self.stop.touch(mode=0o600)
            self.state.setdefault('normal_rehearsal_restore_started_at',watch.stamp());self.save()
            origin=watch.epoch(self.state['normal_rehearsal_restore_started_at'])
            remaining=origin+BOUNDS['recovery']-time.time()
            if remaining<=0:self.state.setdefault('normal_rehearsal_recovery_budget_missed_at',watch.stamp());self.save()
            try:
                with cache.wall_guard(min(remaining,BOUNDS['recovery']) if remaining>0 else BOUNDS['safety_attempt']):
                    retire_exercise(packet)
                    self.fence_hold_requests()
                    self.cleanup_phase_jobs();self.recover_cluster(self.current_main())
            except TimeoutError:
                if time.time()>=origin+BOUNDS['recovery']:
                    self.state.setdefault('normal_rehearsal_recovery_budget_missed_at',watch.stamp());self.save()
                raise
            self.state['normal_only_rehearsal_complete']=True;self.state['copy_runtime_authorized']=False
            self.state['normal_rehearsal_recovered_within_budget']=not bool(self.state.get('normal_rehearsal_recovery_budget_missed_at'))
            self.save();return True
        def fence_hold_requests(self):
            # Retiring the client does not retire a submitted API request. Every
            # hold uses an RV test; advance each owned object's RV before release.
            owners=[('GitRepository','haynes-ops','flux-system',self.state['cached_source_owner'])]
            owners += [('Kustomization',name,ns,self.state['cached_ks_owners'][ns+'/'+name])
                       for ns,name in PARENTS+APPS]
            for kind,name,ns,owned in owners:
                row=self.kube(kind.lower(),name,ns);cache.identity(row,kind,name,ns)
                spec=dict(row['spec']);spec.pop('suspend',None)
                expected=dict(owned['spec']);expected.pop('suspend',None)
                require(row['metadata']['uid']==owned['uid'] and spec==expected,'request barrier owned spec/UID changed')
                phase=packet['phase_token'];annotations=row['metadata'].get('annotations',{})
                require(annotations.get(cache.OWNER) in (None,phase),'request barrier foreign phase')
                token=uuid.uuid4().hex
                patch=[{'op':'test','path':'/metadata/uid','value':owned['uid']},
                    {'op':'test','path':'/metadata/resourceVersion','value':row['metadata']['resourceVersion']},
                    {'op':'test','path':'/spec','value':row['spec']},
                    {'op':'add','path':'/metadata/annotations','value':dict(annotations,**{cache.OWNER:phase,cache.REQUEST:token})}]
                self.run(['kubectl','patch',kind.lower(),name,'-n',ns,'--type=json','-p',json.dumps(patch)],timeout=10)
                after=self.kube(kind.lower(),name,ns);cache.identity(after,kind,name,ns)
                require(after['metadata']['uid']==owned['uid'] and after['spec']==row['spec']
                    and after['metadata']['resourceVersion']!=row['metadata']['resourceVersion']
                    and after['metadata'].get('annotations',{}).get(cache.OWNER)==phase
                    and after['metadata'].get('annotations',{}).get(cache.REQUEST)==token,'request barrier unproved')
            self.state['normal_rehearsal_requests_retired_at']=watch.stamp();self.save()
    if initialize:instance=NormalWatch(args)
    else:
        instance=object.__new__(NormalWatch);instance.args=args;instance.cached=True;instance.normal_goal=None
        instance.scopes=list(APPS);instance.state_path=Path(args.state);instance.stop=Path(args.stop);instance.log=Path(args.log)
        instance.contract=json.loads(Path(args.manifest_contract).read_bytes())
    return instance,cache,wc,watch

def run_watch(packet):
    import fcntl
    directory=Path(packet['directory']);runtime=directory/'runtime';lock=os.open(runtime/'watch.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    w,cache,_,watch=watcher(packet)
    ready=runtime/f'watch-ready-{os.getpid()}-{start_ticks(os.getpid())}.json'
    cache.write_private(ready,canonical({'packet_sha256':sha(canonical(packet)),'phase_token':packet['phase_token'],
        'pid':os.getpid(),'start_ticks':start_ticks(os.getpid()),'armed_at':w.state['armed_at'],'state':str(w.state_path)}))
    # Readiness is the private receipt, never a potentially blocked stdout ACK.
    event({'recovery_armed':True,'ready':str(ready)})
    def cancelled(*_):w.requested_cancel=True
    previous={sig:signal.signal(sig,cancelled) for sig in (signal.SIGTERM,signal.SIGINT)}
    try:w.loop()
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)

def assert_watch(ready,packet):
    require(ready['packet_sha256']==sha(canonical(packet)) and ready['phase_token']==packet['phase_token'],'recovery phase changed')
    require(type(ready['pid']) is int and ready['pid']>1 and ready['pid']!=os.getpid()
        and start_ticks(ready['pid'])==ready['start_ticks'],'independent recovery process absent/reused')
    require(ready['state']==str(Path(packet['directory'])/'runtime'/'watch-state.json'),'recovery state outside private runtime')
    state=json.loads(read_private(ready['state']))
    require(state.get('armed_ready') is True and state.get('complete') is False and state.get('recover_ks') is True
            and time.time()<dt.datetime.fromisoformat(ready['armed_at']).timestamp()+BOUNDS['arm'],'independent recovery not live/armed')
    return state

def hold_patch(row,owned,phase,token,kind,name,ns):
    cache,_,_=modules();cache.identity(row,kind,name,ns)
    require(row['metadata']['uid']==owned['uid'] and row['spec']==owned['spec'] and row['spec'].get('suspend',False) is False,'initial owned resource changed/held')
    require(row['metadata'].get('annotations',{}).get(cache.OWNER) in (None,phase),'foreign phase ownership')
    annotations=dict(row['metadata'].get('annotations',{}),**{cache.OWNER:phase,cache.REQUEST:token})
    return [{'op':'test','path':'/metadata/uid','value':owned['uid']},
        {'op':'test','path':'/metadata/resourceVersion','value':row['metadata']['resourceVersion']},
        {'op':'add','path':'/metadata/annotations','value':annotations},{'op':'add','path':'/spec/suspend','value':True}]

def exercise(packet,ready_path):
    cache,wc,_=modules();runtime=Path(packet['directory'])/'runtime'
    ready=json.loads(read_private(ready_path));require(Path(ready_path).parent==runtime,'ready receipt outside private runtime')
    state=assert_watch(ready,packet);w,_,_,watch=watcher(packet,initialize=False)
    # Exercise never owns recovery state updates; its observer uses a separate log.
    w.state=copy.deepcopy(state);w.save=lambda:None;w.log=runtime/'exercise.log'
    w.stop=runtime/'closed-phase-stop'
    proofs={}
    try:
        with cache.wall_guard(BOUNDS['exercise']):
            register_exercise(packet,ready)
            before_main=w.current_main();w.desired_restored(before_main)
            require(before_main==state['pre_pause_main_sha'] and w.runtime_workloads_normal(),'current main/services must remain original Normal before hold')
            w.cleanup_phase_jobs()
            for ns,name in PARENTS+APPS:
                assert_watch(ready,packet);row=w.kube('kustomization',name,ns)
                if (ns,name) in PARENTS:
                    status=row.get('status',{})
                    require(status.get('observedGeneration')==row['metadata']['generation']
                        and status.get('lastAppliedRevision')=='main@sha1:'+before_main
                        and any(c.get('type')=='Ready' and c.get('status')=='True' for c in status.get('conditions',[])), 'parent initially not Normal and Ready')
                owned=state['cached_ks_owners'][ns+'/'+name];token=uuid.uuid4().hex
                patch=hold_patch(row,owned,packet['phase_token'],token,'Kustomization',name,ns)
                w.run(['kubectl','patch','kustomization',name,'-n',ns,'--type=json','-p',json.dumps(patch)],timeout=10)
                until=time.monotonic()+15
                while True:
                    actual=w.kube('kustomization',name,ns)
                    if actual.get('status',{}).get('lastHandledReconcileAt')==token:break
                    require(time.monotonic()<until,'held handler drain timeout');assert_watch(ready,packet);time.sleep(.2)
                proof={'before':row,'after':actual,'token':token};cache.held(proof,actual,name,ns,packet['phase_token']);proofs[ns+'/'+name]=proof
            assert_watch(ready,packet);before=w.kube('gitrepository','haynes-ops','flux-system')
            controller_rows=[p for p in w.inventory('Pod','flux-system') if p['metadata'].get('labels',{}).get('app')=='source-controller']
            require(len(controller_rows)==1,'one source-controller required');controller=controller_rows[0]
            token=uuid.uuid4().hex;patch=hold_patch(before,state['cached_source_owner'],packet['phase_token'],token,'GitRepository','haynes-ops','flux-system')
            w.run(['kubectl','patch','gitrepository','haynes-ops','-n','flux-system','--type=json','-p',json.dumps(patch)],timeout=10)
            until=time.monotonic()+15
            while True:
                source=w.kube('gitrepository','haynes-ops','flux-system')
                if source.get('status',{}).get('lastHandledReconcileAt')==token:break
                require(time.monotonic()<until,'Source handler drain timeout');assert_watch(ready,packet);time.sleep(.2)
            cache.handled(before,source,token,'GitRepository','haynes-ops')
            require(source['spec']==dict(before['spec'],suspend=True) and source['metadata']['annotations'][cache.OWNER]==packet['phase_token'],'owned Source hold changed')
            raw=cache.fetch(source);contents=cache.artifact_contents(source,raw,before_main)
            require(contents==wc.blobs(packet['repo_dir'],before_main),'Normal artifact bytes differ from Normal Git')
            after=w.kube('gitrepository','haynes-ops','flux-system');cache.controller({'controller_pod':controller},w.kube('pod',controller['metadata']['name'],'flux-system'))
            require(after['metadata']['uid']==source['metadata']['uid'] and after['spec']==source['spec']
                    and after.get('status',{}).get('artifact')==source.get('status',{}).get('artifact')
                    and after['metadata'].get('annotations',{}).get(cache.OWNER)==packet['phase_token']
                    and after['metadata'].get('annotations',{}).get(cache.REQUEST)==token
                    and after.get('status',{}).get('lastHandledReconcileAt')==token,'Normal Source changed around artifact read')
            for ns,name in PARENTS+APPS:cache.held(proofs[ns+'/'+name],w.kube('kustomization',name,ns),name,ns,packet['phase_token'])
            require(w.runtime_still_normal(),'actual services changed during Normal-only containment')
            cache.write_private(runtime/'normal-proof.json',canonical({'type':'normal_only_hold_proof','phase_token':packet['phase_token'],
                'normal_main_sha':before_main,'holds':proofs,'source_before':before,'source_after':after,'source_token':token,
                'controller_pod':controller,'artifact_size':len(raw),'artifact_sha256':sha(raw),'stop_applied':False,'copy_authorized':False}))
    finally:
        (runtime/'cancel').touch(mode=0o600)
    event({'normal_containment_proved':True,'cancellation_requested':True,'copy_authorized':False,'jobsCreated':0})

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepare');parser.add_argument('--repo-dir',default='/home/dev/repos/haynes-ops')
    parser.add_argument('--host-dependency-proof',default=str(HOST_PROOF));parser.add_argument('--host-dependency-sha256',default=HOST_PROOF_SHA)
    parser.add_argument('--packet');parser.add_argument('--go');parser.add_argument('--watch',action='store_true');parser.add_argument('--exercise',action='store_true');parser.add_argument('--ready')
    args=parser.parse_args()
    if args.prepare:
        require(not(args.packet or args.go or args.watch or args.exercise or args.ready),'prepare only');prepare(args.prepare,args.repo_dir,args.host_dependency_proof,args.host_dependency_sha256);return
    require(args.packet and args.go and args.watch!=args.exercise,'one closed runtime role required')
    packet=load_packet(args.packet,args.go)
    if args.watch:run_watch(packet)
    else:require(args.ready,'independent ready receipt required');exercise(packet,args.ready)

if __name__=='__main__':
    try:main()
    except Exception as error:
        event({'result':'REFUSED','error':type(error).__name__,'copy_authorized':False});raise SystemExit(2)
