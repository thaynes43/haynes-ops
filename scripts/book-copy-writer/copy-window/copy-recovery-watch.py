#!/usr/bin/env python3
"""Explicitly armed #825 recovery: restore Git, source revision, app state and Flux."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import time
import window_contract as contract
import cached_source as cache

REPO='thaynes43/haynes-ops'
CORE_SCOPES=[('frontend','haynesnetwork'),('media','libretto'),('downloads','lazylibrarian')]
BOOK_CRONS={'downloads':['lazylibrarian-epub-convert'], 'frontend':[
    'haynesnetwork-sync-books','haynesnetwork-sync-books-collections',
    'haynesnetwork-sync-format-pairing','haynesnetwork-sync-goodreads']}
URL='http://lazylibrarian.downloads.svc.cluster.local:5299'

def instant():return dt.datetime.now(dt.timezone.utc)
def stamp():return instant().isoformat()
def epoch(value):return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
def terminal_success(row):
    if row.get('conclusion') not in ['SUCCESS','SKIPPED','NEUTRAL']:return False
    if row.get('status')=='COMPLETED':return True
    try:return isinstance(row.get('completedAt'),str) and bool(row['completedAt']) and epoch(row['completedAt'])>0
    except (ValueError,TypeError):return False

class ServiceCeiling(BaseException):
    """Owning original clock control; proof-failure fallbacks cannot swallow it."""

class Watchdog:
    cached=False
    def __init__(self,args):
        self.args=args
        self.cached=bool(getattr(args,'cached_source_receipt',None))
        self.normal_goal=None
        self.contract=json.loads(Path(args.manifest_contract).read_bytes())
        if args.include_kavita:self.phase_checkpoint()
        self.scopes=CORE_SCOPES+([('media','kavita')] if args.include_kavita else [])
        self.state_path=Path(args.state)
        self.stop=Path(args.stop)
        self.log=Path(args.log)
        self.state={}
        if self.state_path.exists():
            self.state=json.loads(self.state_path.read_text())
            if (str(self.state.get('pause_pr'))!=args.pause or str(self.state.get('restore_pr'))!=args.restore):
                raise RuntimeError('State belongs to another PR pair; use a fresh --state path.')
        else:
            if self.stop.exists():raise RuntimeError('Stop request already exists without a matching recovery checkpoint; use a fresh stop path before arming.')
            self.state={'armed_at':stamp(),'pause_pr':args.pause,'restore_pr':args.restore,
                        'recover_ks':True,'scopes':self.scopes,'complete':False}
        self.save()
        if not self.state.get('pre_pause_main_sha'):
            baseline=self.current_main()
            self.desired_restored(baseline)
            if not self.runtime_workloads_normal():raise RuntimeError('Actual workloads must be normal before arming recovery.')
            self.state.update(pre_pause_main_sha=baseline,baseline_checked_at=stamp(),armed_ready=True)
            self.save()
        if self.cached and not self.state.get('cached_source_owner'):
            source=self.kube('gitrepository','haynes-ops','flux-system')
            cache.identity(source,'GitRepository','haynes-ops')
            if source['spec'].get('suspend',False) is not False:raise RuntimeError('Source was already held before recovery ownership.')
            self.state['cached_source_owner']={'uid':source['metadata']['uid'],'spec':source['spec'],
                'phase_token':self.phase_checkpoint()['phase_token']}
            self.save()
        if self.cached and not self.state.get('cached_ks_owners'):
            owners={};phase=self.phase_checkpoint()['phase_token']
            for namespace,name in self.recovery_scopes():
                row=self.kube('kustomization',name,namespace)
                cache.identity(row,'Kustomization',name,namespace)
                if row['spec'].get('suspend',False) is not False:raise RuntimeError('Kustomization already held before recovery ownership.')
                owners[namespace+'/'+name]={'uid':row['metadata']['uid'],'spec':row['spec'],'phase_token':phase}
            self.state['cached_ks_owners']=owners;self.save()

    def note(self,message):
        with self.log.open('a') as stream:stream.write(stamp()+' '+message+'\n')

    def save(self):
        temporary=self.state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state,indent=2)+'\n')
        temporary.chmod(0o600);temporary.replace(self.state_path)

    def run(self,args,timeout=45,input_text=None):
        env=dict(os.environ);env['GH_TOKEN']=Path('/creds/gh_token').read_text().strip()
        result=subprocess.run(args,env=env,input=input_text,text=True,capture_output=True,timeout=timeout)
        if result.returncode:
            raise RuntimeError(f'{args[0]} {args[1] if len(args)>1 else ""} failed (exit {result.returncode})')
        return result.stdout

    def kube(self,kind,name,namespace):
        return json.loads(self.run(['kubectl','get',kind,name,'-n',namespace,'-o','json']))

    def view(self,number):
        result=json.loads(self.run(['gh','pr','view',number,'--repo',REPO,'--json',
            'state,mergedAt,mergeCommit,baseRefName,headRefOid,statusCheckRollup,comments,commits,additions,deletions,changedFiles,files']))
        # Claude edits its original comment on re-review. GraphQL gh pr view
        # exposes createdAt, but not updatedAt, so read REST timestamps as well.
        pages=json.loads(self.run(['gh','api','--paginate','--slurp',f'repos/{REPO}/issues/{number}/comments?per_page=100']))
        result['comments']=[{'author':{'login':row['user']['login']},'body':row['body'],
                             'createdAt':row['created_at'],'updatedAt':row['updated_at'],'url':row['html_url']}
                            for page in pages for row in page]
        return result

    def source(self,sha):
        # Do this before EVERY KS resume/reconcile; do not apply a stale pause artifact.
        if self.cached:
            owned=self.state['cached_source_owner']
            source=self.kube('gitrepository','haynes-ops','flux-system')
            spec=dict(source['spec']);spec.pop('suspend',None)
            original=dict(owned['spec']);original.pop('suspend',None)
            if spec!=original:raise RuntimeError('Owned Git source spec changed; cannot adopt another source.')
            patch=cache.release_patch(source,owned['uid'],owned['phase_token'])
            if patch:
                self.state.setdefault('source_resume_requested_at',stamp());self.save()
                self.run(['kubectl','patch','gitrepository','haynes-ops','-n','flux-system',
                          '--type=json','-p',json.dumps(patch,separators=(',',':'))],timeout=10)
        self.run(['flux','reconcile','source','git','haynes-ops','-n','flux-system','--timeout=30s'])
        source=self.kube('gitrepository','haynes-ops','flux-system')
        revision=source.get('status',{}).get('artifact',{}).get('revision','')
        ready=any(c.get('type')=='Ready' and c.get('status')=='True' for c in source.get('status',{}).get('conditions',[]))
        if not ready or source['spec'].get('suspend',False) is not False or revision.rsplit(':',1)[-1]!=sha:
            raise RuntimeError('GitRepository has not fetched the exact restored main SHA; KS remain held.')

    def current_main(self):
        self.run(['git','-C',self.args.repo_dir,'fetch','origin','main:refs/remotes/origin/main'],timeout=10 if self.cached else 45)
        return self.run(['git','-C',self.args.repo_dir,'rev-parse','origin/main'],timeout=5 if self.cached else 45).strip()

    def bind_converter_generator(self,goal,sha):
        goal['converter_generator']=cache.converter_generator(
            {path:contract.git(self.args.repo_dir,'show',sha+':'+path) for path in cache.CONVERTER_INPUTS},sha)
        return goal

    def desired_restored(self,sha):
        # Read immutable YAML from the fetched canonical clone; never checkout or push main.
        import yaml
        self.run(['git','-C',self.args.repo_dir,'fetch','origin','main:refs/remotes/origin/main'])
        normal=contract.blobs(self.args.repo_dir,sha)
        if self.cached:
            self.normal_goal=self.bind_converter_generator(cache.normal_goal(normal),sha)
            return
        for path in contract.PATHS:
            contract.require(contract.sha(normal[path])==self.contract['manifests'][path]['normal_sha256'],'exact normal main manifest bytes differ')
        contract.expected_stop(normal)
        def document(path):
            return yaml.safe_load(self.run(['git','-C',self.args.repo_dir,'show',sha+':'+path]))
        job=document('kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert-cronjob.yaml')
        if job['spec']['suspend'] is not False:raise RuntimeError('Main still pauses converter.')
        env=job['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['env']
        values={row['name']:row.get('value') for row in env}
        if values.get('STRIP_SERIES_METADATA')!='0' or 'Daniel Silva/Ransom' not in json.loads(values.get('LIBRARY_HOLD_FOLDERS_JSON','[]')):
            raise RuntimeError('Main changed reviewed strip/hold settings.')
        frontend=document('kubernetes/main/apps/frontend/haynesnetwork/app/helmrelease.yaml')['spec']['values']['controllers']
        for name in ['sync-books','sync-books-collections','sync-format-pairing','sync-goodreads']:
            if frontend[name]['cronjob']['suspend'] is not False:raise RuntimeError('Main still pauses a book CronJob.')
        libretto=document('kubernetes/main/apps/media/libretto/app/helmrelease.yaml')['spec']['values']['controllers']
        urls=[c.get('env',{}).get('LAZYLIBRARIAN_URL') for ctrl in libretto.values() for c in ctrl.get('containers',{}).values() if 'LAZYLIBRARIAN_URL' in c.get('env',{})]
        if urls!=[URL]:raise RuntimeError('Main acquisition URL is not restored.')
        if self.args.include_kavita:
            scan=document('kubernetes/main/apps/downloads/lazylibrarian/app/library-scan-cronjob.yaml')
            if scan['spec'].get('suspend', False) is not False:raise RuntimeError('Main still pauses LL library scan.')
            for ns,name in [('downloads','lazylibrarian'),('media','kavita')]:
                ctrl=document(f'kubernetes/main/apps/{ns}/{name}/app/helmrelease.yaml')['spec']['values']['controllers'][name]
                if 'replicas' in ctrl:raise RuntimeError('Maintenance replica fields must return to absent/default1.')

    def deployment_normal(self,name,namespace,target):
        if self.cached:
            desired=self.normal_goal['deployments'][(namespace,name)]
            target=desired['replicas']
        deployment=self.kube('deployment',name,namespace)
        meta,spec,status=deployment['metadata'],deployment['spec'],deployment.get('status',{})
        if (spec.get('replicas',1)!=target or status.get('observedGeneration')!=meta['generation']
                or any(status.get(k,0)!=target for k in ('updatedReplicas','readyReplicas','availableReplicas'))):return False
        selector=spec['selector']['matchLabels']
        pods=[p for p in self.inventory('Pod',namespace) if all(p['metadata'].get('labels',{}).get(k)==v for k,v in selector.items())]
        if len(pods)!=target:return False
        expected=[(c['name'],c['image']) for c in spec['template']['spec']['containers']]
        if self.cached:
            if expected!=desired['images']:return False
        elif name=='haynesnetwork-main':
            app=self.contract['app_image']
            if expected!=[('app',app.split('@',1)[0])] and expected!=[('app',app)]:return False
        for pod in pods:
            pm=pod['metadata'];owners=[o for o in pm.get('ownerReferences',[]) if o.get('controller') is True]
            if pm.get('deletionTimestamp') or pod.get('status',{}).get('phase')!='Running' or len(owners)!=1 or owners[0].get('kind')!='ReplicaSet':return False
            if [(c['name'],c['image']) for c in pod['spec']['containers']]!=expected:return False
            rs=self.kube('replicaset',owners[0]['name'],namespace)
            parent=[o for o in rs['metadata'].get('ownerReferences',[]) if o.get('controller') is True]
            if (rs['metadata']['uid']!=owners[0].get('uid') or len(parent)!=1 or parent[0].get('kind')!='Deployment'
                    or parent[0].get('name')!=name or parent[0].get('uid')!=meta.get('uid')):return False
            states=pod.get('status',{}).get('containerStatuses',[])
            if len(states)!=len(expected) or {s['name'] for s in states}!={n for n,_ in expected} or any(s.get('ready') is not True or 'running' not in s.get('state',{}) for s in states):return False
            if self.cached:
                images=dict(expected)
                if any('@sha256:' in images[s['name']] and not s.get('imageID','').endswith(images[s['name']].split('@',1)[1]) for s in states):return False
            elif name=='haynesnetwork-main' and not states[0].get('imageID','').endswith(self.contract['app_image'].split('@',1)[1]):return False
            if name=='libretto':
                env=[e for c in pod['spec']['containers'] for e in c.get('env',[]) if e['name']=='LAZYLIBRARIAN_URL']
                if len(env)!=1 or env[0].get('value')!=URL:return False
        return True

    def runtime_workloads_normal(self):
        if self.cached:return self.runtime_current_normal()
        for namespace,names in BOOK_CRONS.items():
            for name in names:
                job=self.kube('cronjob',name,namespace)
                if job['spec'].get('suspend') is not False:return False
                if namespace=='frontend':
                    images=[c['image'] for c in job['spec']['jobTemplate']['spec']['template']['spec']['containers']]
                    if images!=[self.contract['app_image'].split('@',1)[0]] and images!=[self.contract['app_image']]:return False
        deployment=self.kube('deployment','libretto','media')
        env=[e for c in deployment['spec']['template']['spec']['containers'] for e in c.get('env',[]) if e['name']=='LAZYLIBRARIAN_URL']
        spec,status=deployment['spec'],deployment.get('status',{})
        target=spec.get('replicas',1)
        if (len(env)!=1 or env[0].get('value')!=URL or status.get('observedGeneration')!=deployment['metadata']['generation']
                or status.get('updatedReplicas',0)!=target or status.get('readyReplicas',0)!=target):return False
        if self.args.include_kavita:
            if self.kube('cronjob','lazylibrarian-library-scan','downloads')['spec'].get('suspend') is not False:return False
            for namespace,name in [('downloads','lazylibrarian'),('media','kavita')]:
                d=self.kube('deployment',name,namespace)
                if (d['spec'].get('replicas',1)!=1 or d.get('status',{}).get('readyReplicas',0)!=1
                        or d.get('status',{}).get('updatedReplicas',0)!=1
                        or d.get('status',{}).get('observedGeneration')!=d['metadata']['generation']):return False
        for name,namespace,target in [('haynesnetwork-main','frontend',3),('libretto','media',1),('lazylibrarian','downloads',1),('kavita','media',1)]:
            if not self.deployment_normal(name,namespace,target):return False
        return True

    def runtime_current_normal(self):
        goal=self.normal_goal
        if goal is None:return False
        for (ns,name),values in goal['helm_values'].items():
            release=self.kube('helmrelease',name,ns)
            status=release.get('status',{})
            if (release['spec'].get('values')!=values or status.get('observedGeneration')!=release['metadata']['generation']
                or not any(c.get('type')=='Ready' and c.get('status')=='True' for c in status.get('conditions',[]))):return False
        for ns,name in [('downloads','lazylibrarian-epub-convert'),('downloads','lazylibrarian-library-scan')]+[
            ('frontend','haynesnetwork-'+n) for n in contract.BOOK_CONTROLLERS]:
            cron=self.kube('cronjob',name,ns)
            if cron['spec'].get('suspend') is not False:return False
            if name=='lazylibrarian-epub-convert' and not self.converter_normal(cron['spec'].get('jobTemplate')):return False
            if ns=='frontend' and [c['image'] for c in cron['spec']['jobTemplate']['spec']['template']['spec']['containers']]!=goal['cron_images'][name]:return False
        for (ns,name),desired in goal['deployments'].items():
            if not self.deployment_normal(name,ns,desired['replicas']):return False
        return True

    def converter_normal(self,actual):
        # Current/final Normal keeps exact current main. Only held-original
        # Normal may prove a later applied descendant with identical inputs.
        try:
            goal=self.normal_goal;index=cache.converter_reference(goal['converter_job_template'])
            cm_name=actual['spec']['template']['spec']['volumes'][index]['configMap']['name']
            ks=self.kube('kustomization','lazylibrarian','downloads')
            revision=ks.get('status',{}).get('lastAppliedRevision','')
            match=re.fullmatch('main@sha1:([0-9a-f]{40})',revision)
            if not match:return False
            applied=match.group(1);main=self.current_main();original=goal['converter_generator']
            held=getattr(self,'held_original_converter_goal',False)
            if not held and (applied!=original['revision'] or main!=original['revision']):return False
            contract.git(self.args.repo_dir,'merge-base','--is-ancestor',applied,main)
            if held:contract.git(self.args.repo_dir,'merge-base','--is-ancestor',original['revision'],applied)
            bound=self.bind_converter_generator(dict(goal),applied)
            observed=bound['converter_generator']
            if observed['input_sha256']!=original['input_sha256'] or observed['data']!=original['data']:return False
            cm=self.kube('configmap',cm_name,'downloads')
            expected,proof=cache.rendered_converter_template(bound,actual,ks,cm)
            if not cache.job_template_equal(actual,expected):return False
            again=self.kube('configmap',cm_name,'downloads')
            ks_again=self.kube('kustomization','lazylibrarian','downloads')
            cache.identity(again,'ConfigMap',cm_name,'downloads')
            cache.identity(ks_again,'Kustomization','lazylibrarian','downloads')
            if any(before['metadata'][key]!=after['metadata'][key] for before,after in ((cm,again),(ks,ks_again))
                   for key in ('uid','resourceVersion')):return False
            custody=self.state.setdefault('normal_converter_custody',{})
            owned=custody.get('downloads/'+cm_name)
            if owned is not None and owned['uid']!=proof['uid']:return False
            proof.update(goal_revision=original['revision'],reachable_main_revision=main)
            custody['downloads/'+cm_name]=proof;self.save();return True
        except (ValueError,KeyError,IndexError,TypeError,AttributeError,subprocess.CalledProcessError):
            return False  # Unhealthy/unknown is never a successful Normal proof.

    def runtime_still_normal(self):
        # Held apps can still be on the original Normal version while newer
        # reviewed Normal intent waits on remote main. Do not demand its upgrade
        # before releasing holds needed to apply that upgrade.
        current=self.normal_goal
        held=getattr(self,'held_original_converter_goal',False)
        try:
            baseline=self.state['pre_pause_main_sha']
            self.normal_goal=self.bind_converter_generator(cache.normal_goal(contract.blobs(self.args.repo_dir,baseline)),baseline)
            self.held_original_converter_goal=True
            return self.runtime_current_normal()
        finally:self.normal_goal=current;self.held_original_converter_goal=held

    def stop_actuated(self):
        # This chooses restoration ordering only, never a new COPY clock/GO.
        for ns,name in [('downloads','lazylibrarian'),('media','kavita')]:
            if self.kube('deployment',name,ns)['spec'].get('replicas',1)==0:return True
        for ns,name in [('downloads','lazylibrarian-epub-convert'),('downloads','lazylibrarian-library-scan')]+[
            ('frontend','haynesnetwork-'+n) for n in contract.BOOK_CONTROLLERS]:
            if self.kube('cronjob',name,ns)['spec'].get('suspend') is True:return True
        libre=self.kube('deployment','libretto','media')
        return any(e.get('name')=='LAZYLIBRARIAN_URL' and e.get('value')==''
                   for c in libre['spec']['template']['spec']['containers'] for e in c.get('env',[]))

    def runtime_restored(self,sha):
        if not self.runtime_workloads_normal():return False
        for namespace,name in self.recovery_scopes():
            ks=self.kube('kustomization',name,namespace)
            if ks['spec'].get('suspend',False) is not False:return False
            status=ks.get('status',{})
            if status.get('lastAppliedRevision','').rsplit(':',1)[-1]!=sha:return False
            if not any(c.get('type')=='Ready' and c.get('status')=='True' for c in status.get('conditions',[])):return False
        return True

    def recovery_scopes(self):
        return self.scopes+([('flux-system',name) for name in reversed(cache.PARENTS)] if self.cached else [])

    def release_ks(self,namespace,name):
        if not self.cached:
            self.run(['flux','resume','kustomization',name,'-n',namespace]);return
        owned=self.state['cached_ks_owners'][namespace+'/'+name]
        row=self.kube('kustomization',name,namespace)
        patch=cache.release_patch(row,owned['uid'],owned['phase_token'],kind='Kustomization',name=name,namespace=namespace,spec=owned['spec'])
        if patch:self.run(['kubectl','patch','kustomization',name,'-n',namespace,'--type=json','-p',json.dumps(patch,separators=(',',':'))],timeout=10)

    def reconcile_ks(self,namespace,name,sha):
        self.run(['flux','reconcile','kustomization',name,'-n',namespace,'--timeout=30s'])

    def verify_recovery_absence(self):
        pass  # Generic COPY has fresh cleanup before every scoped release.

    def retire_hold_annotations(self):
        if not self.cached:return
        resources=[('gitrepository','GitRepository','flux-system','haynes-ops',self.state['cached_source_owner'])]+[
            ('kustomization','Kustomization',ns,name,self.state['cached_ks_owners'][ns+'/'+name]) for ns,name in self.recovery_scopes()]
        for resource,kind,ns,name,owned in resources:
            row=self.kube(resource,name,ns)
            patch=cache.retire_owner_patch(row,owned['uid'],owned['phase_token'],kind=kind,name=name,namespace=ns,spec=owned['spec'])
            if patch:self.run(['kubectl','patch',resource,name,'-n',ns,'--type=json','-p',json.dumps(patch,separators=(',',':'))],timeout=10)

    def recover_cluster(self,sha):
        if self.state.get('normal_rehearsal_recovery_budget_missed_at'):
            self.state.update(complete=False,normal_rehearsal_recovered_within_budget=False,copy_runtime_authorized=False);self.save()
        self.cleanup_phase_jobs()
        self.desired_restored(sha)
        phase=self.phase_checkpoint()
        stopped=bool(phase and (phase.get('first_service_stop_observed_at') or phase.get('window_started_at')))
        if self.cached:stopped=stopped or self.stop_actuated()
        still_normal=self.runtime_still_normal if self.cached else self.runtime_workloads_normal
        if not stopped and not still_normal():raise RuntimeError('Prestage cancellation lacks actual still-normal workload proof; retain holds.')
        self.state.update(expected_restored_sha=sha,recover_ks=True);self.save()
        for namespace,name in self.recovery_scopes():
            self.source(sha)
            self.cleanup_phase_jobs()
            self.release_ks(namespace,name)
            self.source(sha)
            self.reconcile_ks(namespace,name,sha)
        self.verify_recovery_absence()
        if not self.runtime_restored(sha):raise RuntimeError('Restored source fetched; waiting for app/KS convergence.')
        self.retire_hold_annotations()
        if (self.state.get('service_ceiling_missed_at') or self.state.get('original_clock_unproved_at')
                or self.state.get('normal_rehearsal_recovery_budget_missed_at')):
            self.state.update(complete=False,recover_ks=False,safety_recovery_complete=True)
            at=stamp()
            if self.state.get('normal_rehearsal_recovery_budget_missed_at'):
                if 'safety_recovery_completed_at' in self.state or self.state.get('completed_at'):self.state['safety_recovery_reverified_at']=at
                self.state.setdefault('safety_recovery_completed_at',self.state.get('completed_at') or at)
            else:self.state['safety_recovery_completed_at']=at
        else:
            self.state.setdefault('completed_at',stamp())
            self.state.update(complete=True,recover_ks=False,normal_reverified_at=stamp())
        self.save()
        self.note('Recovery verified: six CronJobs false, acquisition restored, every scoped KS resumed and Ready on exact restored SHA.')

    def phase_checkpoint(self):
        path=getattr(self.args,'phase_state',None)
        if not path or not Path(path).exists():
            if self.args.include_kavita:raise RuntimeError('COPY requires its complete five-intent checkpoint before arming.')
            return None
        phase=json.loads(Path(path).read_text())
        if not isinstance(phase,dict) or str(phase.get('restore_pr'))!=self.args.restore:
            raise RuntimeError('Copy phase checkpoint belongs to a different inverse PR.')
        if self.args.include_kavita:self.validate_copy_phase(phase)
        return phase

    @staticmethod
    def validate_copy_phase(phase):
        source=('frontend','issue831-copy-source-census-1009-03')
        main=('frontend','issue831-copy-selected-1009-03')
        expected={source,main,('downloads','issue831-ll-source-1009-03'),
                  ('media','issue831-kavita-source-1009-03'),('media','issue831-lidarr-source-1009-03')}
        if (not isinstance(phase,dict) or type(phase.get('schema')) is not int or phase['schema']!=1
                or not isinstance(phase.get('phase_token'),str) or not re.fullmatch(r'[0-9a-f]{32}',phase['phase_token'])
                or not isinstance(phase.get('owned_jobs'),list) or len(phase['owned_jobs'])!=5):
            raise RuntimeError('COPY checkpoint needs its exact five registered intents.')
        token=phase['phase_token'];jobs={}
        def uid(value):return value is None or (isinstance(value,str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',value))
        for row in phase['owned_jobs']:
            if not isinstance(row,dict) or not {'namespace','name','uid','phase_token','writer'}.issubset(row):
                raise RuntimeError('COPY owned Job row is incomplete.')
            key=(row['namespace'],row['name'])
            if (key not in expected or key in jobs or row['phase_token']!=token or not uid(row['uid'])
                    or type(row['writer']) is not bool or row['writer']!=(key==main)):
                raise RuntimeError('COPY owned Job identity, phase, UID or writer flag differs.')
            jobs[key]=row
        leases=phase.get('pg_leases')
        if not isinstance(leases,list) or len(leases)!=2:raise RuntimeError('COPY needs both source and MAIN PG lease bindings.')
        names={source:'issue825-duplicate-share-fence-'+token,main:'issue831-manual-copy-writer'};seen=set()
        for lease in leases:
            if not isinstance(lease,dict) or set(lease)!={'job_namespace','job_name','job_uid','pod_uid','backend_pid','application_name'}:
                raise RuntimeError('COPY PG lease fields are incomplete or unknown.')
            key=(lease['job_namespace'],lease['job_name']);pid=lease['backend_pid']
            if (key not in names or key in seen or lease['application_name']!=names[key]
                    or not uid(lease['job_uid']) or not uid(lease['pod_uid'])
                    or lease['job_uid']!=jobs[key]['uid']
                    or (lease['pod_uid'] is not None and lease['job_uid'] is None)
                    or (pid is not None and (type(pid) is not int or pid<=0 or pid>2147483647
                                            or lease['job_uid'] is None or lease['pod_uid'] is None))):
                raise RuntimeError('COPY PG lease owner, actual application name or PID differs.')
            seen.add(key)

    @staticmethod
    def owned_job_pods(pods,row,uid):
        owned=[]
        for pod in pods:
            meta=pod.get('metadata',{})
            owners=[owner for owner in meta.get('ownerReferences',[]) if owner.get('kind')=='Job'
                    and (owner.get('name')==row['name'] or (uid is not None and owner.get('uid')==uid))]
            if not owners:continue
            if (meta.get('labels',{}).get('issue825.haynesnetwork/phase')!=row['phase_token']
                    or any(owner.get('name')!=row['name'] or not owner.get('uid')
                           or (uid is not None and owner.get('uid')!=uid) for owner in owners)
                    or not meta.get('name') or not meta.get('uid')):
                raise RuntimeError('Owned Job pod identity/phase was reused; refuse another workload.')
            owned.append(pod)
        return owned

    def inventory(self,kind,namespace):
        api,path=('batch/v1',f'/apis/batch/v1/namespaces/{namespace}/jobs') if kind=='Job' else ('v1',f'/api/v1/namespaces/{namespace}/pods')
        return contract.typed_inventory(json.loads(self.run(['kubectl','get','--raw',path])),kind,api,namespace)

    def cleanup_phase_jobs(self):
        phase=self.phase_checkpoint()
        if phase is None:return
        # A dead supervisor cannot leave a filesystem writer racing restoration.
        # STOP tells a live supervisor to stop workers and release its PG socket.
        self.stop.touch(mode=0o600)
        jobs=sorted(phase.get('owned_jobs',[]),key=lambda row:not row.get('writer'))
        for row in jobs:
            namespace,name,uid=row['namespace'],row['name'],row['uid']
            current=self.inventory('Job',namespace)
            found=next((job for job in current if job['metadata']['name']==name),None)
            if found and ((uid is not None and found['metadata']['uid']!=uid)
                    or found['metadata'].get('labels',{}).get('issue825.haynesnetwork/phase')!=row.get('phase_token')):
                raise RuntimeError('Owned copy Job name was reused; refuse to delete another workload.')
            if found and uid is None:uid=found['metadata']['uid']
            if found:self.run(['kubectl','delete','--raw',f'/apis/batch/v1/namespaces/{namespace}/jobs/{name}','-f','-'],
                              input_text=json.dumps({'apiVersion':'v1','kind':'DeleteOptions','propagationPolicy':'Foreground','preconditions':{'uid':uid}}))
            pods=self.inventory('Pod',namespace)
            owned=self.owned_job_pods(pods,row,uid)
            if not found and owned:
                # Parent may die after create but before observe. A missing Job
                # cannot hide its exact phase-labeled, Job-owned writer pod.
                for pod in owned:
                    meta=pod['metadata']
                    self.run(['kubectl','delete','--raw',f"/api/v1/namespaces/{namespace}/pods/{meta['name']}",'-f','-'],
                             input_text=json.dumps({'apiVersion':'v1','kind':'DeleteOptions','propagationPolicy':'Foreground','preconditions':{'uid':meta['uid']}}))
                pods=self.inventory('Pod',namespace)
                owned=self.owned_job_pods(pods,row,uid)
            if owned:
                raise RuntimeError('Owned copy/capture pod still exists; wait before releasing app fence.')
            remaining=self.inventory('Job',namespace)
            pending=next((job for job in remaining if job['metadata']['name']==name),None)
            if pending:
                if ((uid is not None and pending['metadata']['uid']!=uid)
                        or pending['metadata'].get('labels',{}).get('issue825.haynesnetwork/phase')!=row.get('phase_token')):
                    raise RuntimeError('Owned copy Job name was reused; refuse another workload.')
                raise RuntimeError('Owned copy/capture Job still exists; wait before releasing app fence.')
        self.verify_phase_absent(phase)

    def verify_phase_absent(self,phase):
        jobs=phase['owned_jobs']
        # Authoritative phase/name/UID/owner union catches unregistered or orphan
        # phase resources; absence of each expected Job alone is insufficient.
        for namespace in sorted({r['namespace'] for r in jobs}):
            known={r['name']:r for r in jobs if r['namespace']==namespace}
            uids={r['uid'] for r in known.values() if r['uid'] is not None}
            for item in self.inventory('Job',namespace):
                meta=item['metadata']
                if (meta['name'] in known or meta['uid'] in uids
                        or meta.get('labels',{}).get('issue825.haynesnetwork/phase')==phase['phase_token']):
                    raise RuntimeError('Copy phase Job union is not empty; retain holds.')
            for item in self.inventory('Pod',namespace):
                meta=item['metadata'];owners=meta.get('ownerReferences',[])
                if (meta.get('labels',{}).get('issue825.haynesnetwork/phase')==phase['phase_token']
                        or any(o.get('kind')=='Job' and (o.get('name') in known or o.get('uid') in uids) for o in owners)):
                    raise RuntimeError('Copy phase Pod union is not empty; retain holds.')
        if self.args.include_kavita:
            # Always inspect both names, even after death before record-pg.
            leases=phase['pg_leases']
            bindings={'names':[row['application_name'] for row in leases],
                      'pids':[row['backend_pid'] for row in leases if row['backend_pid'] is not None]}
            program="const bindings="+json.dumps(bindings,separators=(',',':'))+";"+"""const {Client}=require('/sync/node_modules/pg');(async()=>{const c=new Client({connectionString:process.env.DATABASE_URL,connectionTimeoutMillis:3000});try{await c.connect();await c.query('BEGIN READ ONLY');await c.query("SET LOCAL statement_timeout='2s'");const p=await c.query("SELECT pg_is_in_recovery() AS standby,current_setting('server_version_num')::int AS version");if(p.rows[0].standby||p.rows[0].version<160000||p.rows[0].version>=170000)throw new Error('primary required');const r=await c.query("SELECT count(*)::int AS n FROM pg_stat_activity WHERE application_name=ANY($1::text[]) OR pid=ANY($2::int[])",[bindings.names,bindings.pids]);await c.query('ROLLBACK');if(r.rows[0].n!==0)process.exitCode=3;}finally{await c.end();}})().catch(()=>process.exitCode=2);"""
            self.run(['kubectl','exec','-n','frontend','deployment/haynesnetwork-main','-c','app','--','node','--eval',program],timeout=8)
        elif phase.get('pg_backend_pid'):
            program="""const {Client}=require('/sync/node_modules/pg');(async()=>{const c=new Client({connectionString:process.env.DATABASE_URL});await c.connect();await c.query('BEGIN READ ONLY');const r=await c.query("SELECT count(*)::int AS n FROM pg_stat_activity WHERE application_name='issue825-duplicate-share-fence'");await c.query('ROLLBACK');await c.end();if(r.rows[0].n!==0)process.exitCode=3;})().catch(()=>process.exitCode=2);"""
            self.run(['kubectl','exec','-n','frontend','deployment/haynesnetwork-main','-c','app','--','node','--eval',program])
        self.state['copy_phase_cleanup_verified_at']=stamp();self.save()

    def verify_inverse(self,restore):
        import collections
        expected={
            'kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert-cronjob.yaml':(1,1),
            'kubernetes/main/apps/frontend/haynesnetwork/app/helmrelease.yaml':(4,4),
            'kubernetes/main/apps/media/libretto/app/helmrelease.yaml':(1,1),
        }
        added=['suspend: false']*5+['LAZYLIBRARIAN_URL: '+URL]
        deleted=['suspend: true']*5+['LAZYLIBRARIAN_URL: ""']
        if self.args.include_kavita:
            expected.update({
                'kubernetes/main/apps/downloads/lazylibrarian/app/library-scan-cronjob.yaml':(1,1),
                'kubernetes/main/apps/downloads/lazylibrarian/app/helmrelease.yaml':(0,1),
                'kubernetes/main/apps/media/kavita/app/helmrelease.yaml':(0,1),
            })
            added+=['suspend: false'];deleted+=['suspend: true','replicas: 0','replicas: 0']
        actual={row['path']:(row['additions'],row['deletions']) for row in restore['files']}
        if restore['baseRefName']!='main' or actual!=expected:
            raise RuntimeError('Inverse metadata does not match the exact reviewed recovery profile.')
        lines=self.run(['gh','pr','diff',self.args.restore,'--repo',REPO]).splitlines()
        plus=[line[1:].strip() for line in lines if line.startswith('+') and not line.startswith('+++')]
        minus=[line[1:].strip() for line in lines if line.startswith('-') and not line.startswith('---')]
        if collections.Counter(plus)!=collections.Counter(added) or collections.Counter(minus)!=collections.Counter(deleted):
            raise RuntimeError('Inverse patch contains changes outside the reviewed recovery profile.')
        base=self.current_main()
        self.run(['git','-C',self.args.repo_dir,'fetch','origin',f"pull/{self.args.restore}/head"])
        contract.verify_git_pair(self.args.repo_dir,base,restore['headRefOid'],self.contract,'inverse')
        self.state['expected_inverse_head']=restore['headRefOid'];self.save()

    @staticmethod
    def clean_gates(restore):
        checks=restore.get('statusCheckRollup',[])
        names={row.get('name'):row for row in checks}
        for name in ['Flux Local - Success','Diff Scope - Success','Claude Review (advisory)']:
            row=names.get(name,{})
            if not terminal_success(row) or row.get('conclusion')!='SUCCESS':return False
        if not checks or not all(terminal_success(row) or row.get('state')=='SUCCESS' for row in checks):return False
        commits=restore.get('commits',[])
        if not commits:return False
        head_at=epoch(commits[-1]['committedDate'])
        advisory=names['Claude Review (advisory)']
        if not advisory.get('startedAt') or epoch(advisory['startedAt'])<head_at:return False
        comments=[row for row in restore.get('comments',[]) if row.get('author',{}).get('login','').lower() in ('claude','claude[bot]')
                  and epoch(row.get('updatedAt') or row['createdAt'])>=head_at]
        if not comments:return False
        comment=max(comments,key=lambda row:epoch(row.get('updatedAt') or row['createdAt']))
        body=comment['body']
        return contract.clean_advisory(body)

    def retarget_argv(self):
        argv=['bash',self.args.retarget_script,self.args.restore_worktree,self.args.pause,self.args.restore,self.args.pause_head,self.args.restore_branch]
        if self.args.include_kavita:argv.append('copy')
        return argv

    def restored_main(self, merge_sha):
        # Recovery follows current main, preserving any later unrelated commit.
        # Never restore an old source artifact or adopt a replaced history.
        current=self.current_main()
        self.run(['git','-C',self.args.repo_dir,'merge-base','--is-ancestor',merge_sha,current],timeout=10)
        self.desired_restored(current)
        return current

    def guard_phase_git(self):
        current=self.current_main()
        observed=contract.blobs(self.args.repo_dir,current)
        drift={p:contract.sha(observed[p]) for p in contract.PATHS
               if contract.sha(observed[p])!=self.contract['manifests'][p]['stop_sha256']}
        if not drift:return
        # Never rewrite upgraded settings back to the frozen normal snapshot.
        # Preserve the original refusal/clock across restart. This immediately
        # revokes COPY and requests writer-first cleanup; all holds stay armed.
        self.state.setdefault('copy_authority_revoked_at',stamp())
        self.state.setdefault('git_drift_first_sha',current)
        self.state.update(git_drift_current_sha=current,git_drift_manifest_sha256=drift,
                          recovery_reason='unexpected_phase_manifest_drift',complete=False,recover_ks=True)
        self.save();self.stop.touch(mode=0o600)
        self.cleanup_phase_jobs()
        raise RuntimeError('Phase manifest drift: COPY revoked; exact current-main recovery requires reviewed inverse/contract; retain holds.')

    def cached_receipt(self):
        raw=cache.read_private(self.args.cached_source_receipt)
        digest=contract.sha(raw)
        if self.state.get('cached_source_receipt_sha256') not in (None,digest):
            raise RuntimeError('Sealed cached source receipt changed.')
        value=json.loads(raw)
        owned=self.state['cached_source_owner']
        if (value.get('phase_token')!=owned['phase_token'] or value['source_before']['metadata']['uid']!=owned['uid']
            or value['source_before']['spec']!=owned['spec'] or str(value.get('pause_pr'))!=self.args.pause
            or str(value.get('restore_pr'))!=self.args.restore):raise RuntimeError('Cached source ownership/PR pair changed.')
        self.state.setdefault('cached_source_receipt_sha256',digest);self.save()
        return value,digest

    def revoke_cached(self,reason):
        self.state.setdefault('copy_authority_revoked_at',stamp())
        self.state.update(recovery_reason=reason,complete=False,recover_ks=True);self.save()
        self.stop.touch(mode=0o600)
        self.cleanup_phase_jobs()

    def tick_cached(self,pause,restore):
        # The normal inverse is already merged. No checks, advisory or merge is
        # consulted during an actual Stop; current main remains restoration intent.
        merge=restore['mergeCommit']['oid']
        if self.state.get('normal_inverse_merge_sha') not in (None,merge):
            self.revoke_cached('normal_inverse_identity_changed');raise RuntimeError('Inverse merge changed.')
        self.state.setdefault('normal_inverse_merge_sha',merge);self.save()
        phase=self.phase_checkpoint()
        started=phase.get('first_service_stop_observed_at') or phase.get('window_started_at')
        if started:self.state.setdefault('window_started_at',started);self.save()
        origin=self.state.get('window_started_at') or self.state['armed_at']
        limit=self.args.deadline if self.state.get('window_started_at') else self.args.arm_deadline
        stale=bool(started and phase.get('heartbeat_required') and not phase.get('complete')
                   and (not phase.get('heartbeat') or instant().timestamp()-epoch(phase['heartbeat'])>15))
        due=self.stop.exists() or stale or instant().timestamp()-epoch(origin)>=limit
        if due:
            self.revoke_cached('original_restore_due')
            self.recover_cluster(self.restored_main(merge));return True
        if not self.state.get('normal_inverse_verified_at'):
            # This Git/merged-PR verification happens during still-Normal stage.
            # No GitHub advisory/CI query precedes recovery after acceptance.
            try:self.restored_main(merge)
            except Exception:
                self.revoke_cached('current_remote_main_not_normal');raise
            self.state['normal_inverse_verified_at']=stamp();self.save()
        if not Path(self.args.cached_source_receipt).exists():
            # Still-normal staging has not sealed the cache. No activation.
            if started or Path(self.args.cached_source_activation).exists() or self.stop_actuated():
                if not started:self.state.setdefault('actual_stop_origin_unknown',True);self.save()
                self.revoke_cached('activation_without_sealed_cache')
                self.recover_cluster(self.restored_main(merge));return True
            return False
        try:
            receipt,digest=self.cached_receipt()
            if receipt.get('normal_inverse_merge_sha')!=merge:raise RuntimeError('Cache does not bind merged Normal inverse.')
            active=Path(self.args.cached_source_activation).exists()
            if active:
                value=json.loads(cache.read_private(self.args.cached_source_activation))
                budget=cache.activation(value,phase['phase_token'],digest,merge)
                if budget<epoch(self.state['armed_at']) or budget>instant().timestamp():raise RuntimeError('Budget origin outside original arm/current clock.')
                if self.state.get('actuation_budget_started_at') not in (None,value['actuation_budget_started_at']):raise RuntimeError('Budget origin changed across restart.')
                self.state.setdefault('actuation_budget_started_at',value['actuation_budget_started_at']);self.save()
                # The immutable earlier origin bounds a crash before the actual
                # Stop observation. It never claims an observed Stop or resets it.
                if instant().timestamp()-min(budget,epoch(started) if started else budget)>=self.args.deadline:
                    self.revoke_cached('pre_release_budget_restore_due');self.recover_cluster(self.restored_main(merge));return True
            elif started or self.stop_actuated():
                if not started:self.state.setdefault('actual_stop_origin_unknown',True);self.save()
                raise RuntimeError('Actual Stop without bound activation.')
            cap=epoch(self.state['actuation_budget_started_at'])+self.args.deadline if self.state.get('actuation_budget_started_at') else None
            cache.check_live(receipt,self.kube,self.contract,phase['phase_token'],holds=not active,deadline=cap)
            self.state.setdefault('cached_source_ready_at',stamp());self.save()
        except Exception:
            if not started and not self.state.get('actuation_budget_started_at') and Path(self.args.cached_source_activation).exists():
                self.state.setdefault('actual_stop_origin_unknown',True);self.save()
            self.revoke_cached('cached_source_or_hold_proof_lost')
            self.recover_cluster(self.restored_main(merge));return True
        return False

    def original_service_origin(self):
        # Private original clocks only; no API/Git command may precede binding.
        candidate=dict(self.state)
        phase=self.phase_checkpoint()
        started=phase.get('first_service_stop_observed_at') or phase.get('window_started_at')
        if started:
            if candidate.get('window_started_at') not in (None,started):raise RuntimeError('Original Stop origin changed.')
            candidate.setdefault('window_started_at',started)
        if Path(self.args.cached_source_activation).exists():
            receipt,digest=self.cached_receipt()
            value=json.loads(cache.read_private(self.args.cached_source_activation))
            budget=cache.activation(value,phase['phase_token'],digest,receipt['normal_inverse_merge_sha'])
            if budget<epoch(self.state['armed_at']) or budget>instant().timestamp():raise RuntimeError('Original budget origin outside arm/current clock.')
            if candidate.get('actuation_budget_started_at') not in (None,value['actuation_budget_started_at']):raise RuntimeError('Original budget origin changed.')
            candidate.setdefault('actuation_budget_started_at',value['actuation_budget_started_at'])
        origins=[epoch(candidate[k]) for k in ('actuation_budget_started_at','window_started_at') if candidate.get(k)]
        if any(origin<epoch(self.state['armed_at']) or origin>instant().timestamp() for origin in origins):raise RuntimeError('Original service origin invalid/future.')
        for field in ('actuation_budget_started_at','window_started_at'):
            if field in candidate:self.state.setdefault(field,candidate[field])
        self.save()
        return min(origins) if origins else None

    def ceiling_missed(self):
        # Do not run another phase command or mint a later deadline after expiry.
        self.state.setdefault('service_ceiling_missed_at',stamp())
        self.state.setdefault('copy_authority_revoked_at',stamp())
        self.state.update(recovery_reason='original_service_ceiling_expired',complete=False,recover_ks=True)
        self.save();self.stop.touch(mode=0o600)

    def clock_unproved(self):
        self.state.setdefault('original_clock_unproved_at',stamp())
        self.state.setdefault('copy_authority_revoked_at',stamp())
        self.state.update(recovery_reason='original_service_clock_unproved',complete=False,recover_ks=True)
        self.save();self.stop.touch(mode=0o600)

    def safety_recovery(self,clock_unproved=False):
        # A revoked window stays invalid. This bounded cleanup-only attempt has
        # no producer/replay/ACK or COPY lease, and never resets original clocks.
        if clock_unproved or self.state.get('original_clock_unproved_at'):self.clock_unproved()
        else:self.ceiling_missed()
        with cache.wall_guard(60):
            self.cleanup_phase_jobs()
            merge=self.state.get('normal_inverse_merge_sha')
            if not merge:raise RuntimeError('Safety recovery lacks accepted Normal inverse identity; root intervention required.')
            self.recover_cluster(self.restored_main(merge))
        return True

    def reverify_completed_normal(self):
        # Preserve the historical in-window proof. Late fresh convergence is
        # bounded cleanup/restoration only, never a new COPY window.
        self.state.setdefault('copy_authority_revoked_at',stamp());self.save()
        self.stop.touch(mode=0o600)
        with cache.wall_guard(60):
            self.cleanup_phase_jobs()
            merge=self.state.get('normal_inverse_merge_sha')
            if not merge:raise RuntimeError('Completed recovery lacks Normal inverse identity.')
            self.recover_cluster(self.restored_main(merge))
        return True

    def persisted_service_origin(self):
        origins=[epoch(self.state[k]) for k in ('actuation_budget_started_at','window_started_at') if self.state.get(k)]
        if any(origin<epoch(self.state['armed_at']) or origin>instant().timestamp() for origin in origins):raise RuntimeError('Persisted original service origin invalid/future.')
        return min(origins) if origins else None

    def service_budget(self,origin,action):
        if self.state.get('original_clock_unproved_at'):return self.safety_recovery(clock_unproved=True)
        completed=self.state.get('completed_at')
        if (self.state.get('complete') is True and not self.state.get('service_ceiling_missed_at')
            and completed and origin<=epoch(completed)<=min(origin+300,instant().timestamp())):
            return self.reverify_completed_normal()
        remaining=origin+300-instant().timestamp()
        if remaining<=0 or self.state.get('service_ceiling_missed_at'):return self.safety_recovery()
        try:
            with cache.wall_guard(min(300,remaining),error=ServiceCeiling):return action()
        except ServiceCeiling:
            self.ceiling_missed()
            raise TimeoutError('Original service ceiling missed; bounded safety recovery follows.')

    def tick(self):
        if self.cached:
            if self.state.get('original_clock_unproved_at'):return self.safety_recovery(clock_unproved=True)
            try:origin=self.persisted_service_origin()
            except Exception:
                return self.safety_recovery(clock_unproved=True)
            if origin is not None:return self.service_budget(origin,self.bind_clock_tick)
            return self.bind_clock_tick()
        return self.tick_body()

    def bind_clock_tick(self):
        try:
            with cache.wall_guard(5):origin=self.original_service_origin()
        except Exception:
            return self.safety_recovery(clock_unproved=True)
        if origin is not None:return self.service_budget(origin,self.tick_body)
        return self.tick_body()

    def tick_body(self):
        if self.state.get('complete'):
            # A stop/restart never skips fresh convergence verification.
            self.recover_cluster(self.restored_main(self.state['expected_restored_sha']));return True
        if self.cached and self.state.get('normal_inverse_verified_at') and self.state.get('normal_inverse_merge_sha'):
            return self.tick_cached({},dict(mergeCommit={'oid':self.state['normal_inverse_merge_sha']}))
        if self.cached:
            # A dropped staging hold can apply Stop before activation/its clock.
            # Revoke before retarget, reviews or even merged-PR metadata reads.
            phase=self.phase_checkpoint()
            started=phase.get('first_service_stop_observed_at') or phase.get('window_started_at')
            try:
                with cache.wall_guard(5):actuated=self.stop_actuated()
            except Exception:
                self.revoke_cached('pre_activation_native_proof_lost');raise
            if started or actuated:
                if started:self.state.setdefault('window_started_at',started)
                else:self.state.setdefault('actual_stop_origin_unknown',True)
                self.save();self.revoke_cached('stop_before_normal_inverse_accepted')
                restore=json.loads(self.run(['gh','pr','view',self.args.restore,'--repo',REPO,
                                             '--json','state,mergeCommit'],timeout=5))
                if restore.get('state')=='MERGED':return self.tick_cached({},restore)
                raise RuntimeError('Unexpected Stop before merged Normal inverse; COPY revoked, writer cleanup requested, remaining holds retained; Normal restoration unproved.')
        pause=self.view(self.args.pause)
        if pause['state']=='MERGED':
            self.state.setdefault('pause_merged_at',pause['mergedAt']);self.save()
            restore=self.view(self.args.restore)
            if restore['state']=='MERGED':
                if self.cached:return self.tick_cached(pause,restore)
                self.recover_cluster(self.restored_main(restore['mergeCommit']['oid']));return True
            if self.cached:
                phase=self.phase_checkpoint()
                if phase.get('first_service_stop_observed_at') or phase.get('window_started_at'):
                    self.revoke_cached('actual_stop_before_normal_inverse_merged')
            self.guard_phase_git()
            if restore['baseRefName']!='main' or not self.state.get('inverse_retargeted'):
                self.run(self.retarget_argv(),timeout=120)
                self.state['inverse_retargeted']=True;self.save()
                restore=self.view(self.args.restore)
            # Root may record the real stage start in this shared state.
            disk=json.loads(self.state_path.read_text())
            if disk.get('window_started_at'):self.state['window_started_at']=disk['window_started_at']
            phase=self.phase_checkpoint()
            phase_start=(phase.get('first_service_stop_observed_at') or phase.get('window_started_at')) if phase else None
            if phase_start:
                self.state['window_started_at']=phase_start;self.save()
            origin=self.state.get('window_started_at') or self.state['armed_at']
            limit=self.args.deadline if self.state.get('window_started_at') else self.args.arm_deadline
            stale_phase=bool(phase and self.state.get('window_started_at') and phase.get('heartbeat_required') and not phase.get('complete')
                             and (not phase.get('heartbeat') or instant().timestamp()-epoch(phase['heartbeat'])>15))
            due=self.stop.exists() or stale_phase or instant().timestamp()-epoch(origin)>=limit
            if due:
                if self.clean_gates(restore):
                    self.verify_inverse(restore)
                    self.cleanup_phase_jobs()
                    self.run(['gh','pr','merge',self.args.restore,'--repo',REPO,'--squash','--match-head-commit',self.state['expected_inverse_head'],'--body-file',self.args.merge_footer])
                    self.note('Recovery inverse merged after current main gates and clean normal Claude review; convergence pending.')
                else:self.note('Recovery due; current main gates/normal review not clean. Remain armed and keep KS held safely.')
        elif self.stop.exists() or pause['state']=='CLOSED' or instant().timestamp()-epoch(self.state['armed_at'])>=self.args.arm_deadline:
            # Nothing landed: validate current main before releasing partial KS holds.
            sha=self.current_main()
            self.state.update(pause_not_actuated=True,recovery_reason='unmerged pause cancelled or arm deadline expired')
            self.save()
            self.note('Pause unmerged; validate current main and recover every partial KS suspension. PR remains open.')
            self.recover_cluster(sha);return True
        return False

    def loop(self):
        self.note(f'Armed BEFORE KS suspensions; expected inverse PR#{self.args.restore}; recover_ks=true.')
        while True:
            try:
                if self.tick():return
            except Exception as exc:
                # Private fixed-class log; command responses/credentials are never logged.
                self.note(f'Recovery retry: {type(exc).__name__}')
            time.sleep(10)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm',action='store_true',required=True)
    parser.add_argument('--repo-dir',default='/home/dev/repos/haynes-ops')
    parser.add_argument('--pause',required=True);parser.add_argument('--restore',required=True)
    parser.add_argument('--pause-head',required=True)
    parser.add_argument('--restore-worktree',required=True)
    parser.add_argument('--restore-branch',required=True)
    parser.add_argument('--retarget-script',default=str(Path(__file__).with_name('retarget-restore.sh')))
    parser.add_argument('--include-kavita',action='store_true')
    parser.add_argument('--phase-state',required=True)
    parser.add_argument('--cached-source-receipt',help='Immutable private receipt, sealed while all services remain Normal.')
    parser.add_argument('--cached-source-activation',help='Immutable supervisor activation-ready record path, initially absent.')
    parser.add_argument('--deadline',type=int,default=170)
    parser.add_argument('--arm-deadline',type=int,default=600,help='Maximum live-workload staging time before recovery is requested.')
    parser.add_argument('--state',required=True)
    parser.add_argument('--stop',required=True)
    parser.add_argument('--log',required=True)
    parser.add_argument('--manifest-contract',default=str(Path(__file__).with_name('manifest-contract.json')))
    parser.add_argument('--merge-footer',required=True)
    args=parser.parse_args()
    if not args.include_kavita or args.deadline!=170:parser.error('Fresh COPY requires four apps and unchanged 170s restore trigger.')
    if args.arm_deadline<=0 or args.arm_deadline>600:parser.error('Arm deadline must be <=600s.')
    if bool(args.cached_source_receipt)!=bool(args.cached_source_activation):parser.error('Both cached source receipt and activation paths are required together.')
    Watchdog(args).loop()

if __name__=='__main__':main()
