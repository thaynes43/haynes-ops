#!/usr/bin/env python3
"""Host-only bounded private inputs; Running UID proof before/after stdin exec."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).parent))
import importlib
r = importlib.import_module('receive-private-inputs')
IMAGE = 'ghcr.io/thaynes43/book-copy-writer@sha256:628e97b8dbcc83a4d7068484b516b21dde740c4dd130d54f3b030f1ee7a75601'
HELPER_SHA = '34dd3dc3a4c18ee2818ee7fd45563c638053494f3c2cb95129bbced06cb66b07'
NATIVE_SHA = '67f40c064babee41cc7faba1b7b9541a0ffe65d4d0f137507248fdf5c9e8108f'
COLLECTOR_SHA = '03f6163873b75f2bf0dc6896851b9da568d5e370ef39b56983eed64def2eb5f0'
KEYS = {'schema','mode','manifest','helper','native_verifier','receiver','native_binding','selected_scope',
        'phase_state','source_fence','namespace','job_name','job_uid','pod_name','pod_uid','phase_token',
        'deadline_epoch','restore_pr','phase_identity_sha256','image'}


def artifact_identity(info):
    return r.fingerprint(info) + (info.st_mtime_ns,info.st_ctime_ns)


def read_pinned(entry, limit):
    r.require(isinstance(entry,dict) and set(entry) == {'path','sha256'} and isinstance(entry['path'],str)
              and Path(entry['path']).is_absolute() and isinstance(entry['sha256'],str) and r.SHA.fullmatch(entry['sha256']), 'pinned_artifact_schema')
    fd = os.open(entry['path'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as handle:
        before = os.fstat(handle.fileno())
        r.require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit, 'pinned_artifact_type_or_cap')
        raw = handle.read(limit+1)
        r.require(len(raw) <= limit and artifact_identity(before) == artifact_identity(os.fstat(handle.fileno()))
                  and artifact_identity(before) == artifact_identity(os.stat(entry['path'],follow_symlinks=False))
                  and hashlib.sha256(raw).hexdigest() == entry['sha256'], 'pinned_artifact_changed')
    return raw


def load_module(name, entry, required_sha):
    r.require(entry['sha256'] == required_sha, 'reviewed_module_pin_differs')
    raw = read_pinned(entry, 1024*1024)
    # Execute only the already verified source bytes; never reopen a drifted path.
    spec = importlib.util.spec_from_loader(name, loader=None, origin=entry['path'])
    value = importlib.util.module_from_spec(spec)
    value.__file__ = entry['path']
    exec(compile(raw, entry['path'], 'exec'), value.__dict__)
    return value


def phase_identity(state):
    value = copy.deepcopy(state)
    del value['heartbeat']
    return hashlib.sha256(r.canonical(value)).hexdigest()


def check_phase(c, helper, previous_heartbeat=None):
    state, _raw_sha = helper.read_json(Path(c['phase_state']))
    helper.validate_state(state, c['restore_pr'])
    r.require(state.get('window_started_at') and not state['complete'] and state['phase_token'] == c['phase_token']
              and len(state['owned_jobs']) == 5 and len(state['pg_leases']) == 2
              and phase_identity(state) == c['phase_identity_sha256'], 'active_phase_identity_differs')
    heartbeat = r.utc_epoch(state['heartbeat'])
    r.require(0 <= time.time()-heartbeat <= 15 and (previous_heartbeat is None or heartbeat >= previous_heartbeat), 'active_phase_heartbeat')
    row = next(v for v in state['owned_jobs'] if (v['namespace'],v['name']) == (c['namespace'],c['job_name']))
    lease = next(v for v in state['pg_leases'] if (v['job_namespace'],v['job_name']) == (c['namespace'],c['job_name']))
    manifest = r.decode(read_pinned(c['manifest'],2*1024*1024))
    r.require(row['uid'] == c['job_uid'] and row['phase_token'] == c['phase_token'] and row['writer'] is False
              and row['ready_manifest'] == manifest and lease['job_uid'] == c['job_uid'] and lease['pod_uid'] == c['pod_uid']
              and type(lease['backend_pid']) is int and lease['backend_pid'] == c['source_fence']['pg_backend_pid']
              and lease['application_name'] == 'issue825-duplicate-share-fence-'+c['phase_token'], 'source_lease_identity_differs')
    helper.require_live_source_lease(state,c['source_fence'])
    return heartbeat


def finish_process(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=3)
    for handle in (process.stdin,process.stdout,process.stderr):
        if handle is not None:
            handle.close()


def bounded_command(args, payload=None, cap=2*1024*1024):
    process = subprocess.Popen(args,stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        if payload is not None:
            process.stdin.write(payload); process.stdin.flush(); process.stdin.close(); process.stdin=None
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ,'out')
            selector.register(process.stderr,selectors.EVENT_READ,'err')
            total = 0
            while selector.get_map():
                for key,_events in selector.select(.05):
                    chunk = os.read(key.fileobj.fileno(),65536)
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    total += len(chunk)
                    r.require(total <= cap, 'native_command_output_cap')
                    if key.data == 'out': output.extend(chunk)
        r.require(process.wait(timeout=1) == 0, 'native_command_refused')
        return bytes(output)
    finally:
        finish_process(process)


def get_native(c, execute):
    job = r.decode(execute(['kubectl','get','job',c['job_name'],'-n',c['namespace'],'-o','json']))
    pod = r.decode(execute(['kubectl','get','pod',c['pod_name'],'-n',c['namespace'],'-o','json']))
    return job,pod


def verify(c, manifest, native, helper, verifier, execute):
    job,pod = get_native(c,execute)
    bound = verifier.bind(manifest,job,pod,c['phase_token'],r.MODULES,native['observed_at'],helper,c['job_uid'],c['pod_uid'])
    # The published image and complete native binding must remain byte-semantically exact.
    r.require(bound == native and native['source_binding']['image'] == IMAGE, 'native_source_binding_changed')
    return pod


def delivery(c, execute=bounded_command, host_deadline_epoch=None):
    r.require(isinstance(c,dict) and set(c) == KEYS and type(c['schema']) is int and c['schema'] == 1
              and c['mode'] in ('LIVE','SOURCE') and c['image'] == IMAGE, 'private_delivery_contract_schema')
    r.require(all(isinstance(c[k],str) and re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?',c[k]) for k in ('namespace','job_name','pod_name'))
              and r.UUID.fullmatch(c['job_uid']) and r.UUID.fullmatch(c['pod_uid']) and r.PHASE.fullmatch(c['phase_token']), 'private_delivery_identity')
    r.require(isinstance(c['deadline_epoch'],str) and re.fullmatch(r'[0-9]+(?:\.[0-9]{1,6})?',c['deadline_epoch']), 'private_delivery_deadline')
    # Arm before importing helpers, reading proof files or making any native call.
    initial_deadline = min(float(c['deadline_epoch']),time.time()+12,
                           time.time()+12 if host_deadline_epoch is None else host_deadline_epoch)
    with r.alarm(initial_deadline):
        native_raw = read_pinned(c['native_binding'],r.MAX_BYTES)
        native = r.decode(native_raw)
        r.require(isinstance(native,dict) and set(native) == {'schema','phase_token','observed_at','source_binding','module_sha256'}
                  and native['schema'] == 1 and native['phase_token'] == c['phase_token'] and native['module_sha256'] == r.MODULES, 'native_input_schema')
        observed = r.utc_epoch(native['observed_at'])
        r.require(0 <= time.time()-observed <= 15, 'native_observation_expired')
        manifest = r.decode(read_pinned(c['manifest'],2*1024*1024))
        containers = manifest['spec']['template']['spec']['containers']
        r.require(len(containers) == 1 and containers[0]['image'] == IMAGE, 'private_delivery_image')
        env = containers[0]['env']
        r.require(len({v['name'] for v in env}) == len(env), 'private_delivery_env_duplicate')
        values = {v['name']:v.get('value') for v in env}
        gate = 'COPY_BASELINE_PHASE_READY' if c['mode'] == 'LIVE' else 'COPY_SOURCE_PHASE_READY'
        epoch_key = 'COPY_BASELINE_DEADLINE_EPOCH' if c['mode'] == 'LIVE' else 'COPY_SOURCE_DEADLINE_EPOCH'
        r.require(values.get(gate) == '1' and isinstance(values.get(epoch_key),str), 'private_delivery_gate')
        deadline = min(initial_deadline,observed+15,float(values[epoch_key]))
        r.require(time.time() < deadline, 'private_delivery_deadline')
        r.shorten_alarm(deadline)
        helper = load_module('private_input_checkpoint',c['helper'],HELPER_SHA)
        # The existing verifier imports exactly the reviewed collector sibling.
        collector_path = Path(c['native_verifier']['path']).parent/'bound_census_collectors.py'
        collector_entry = {'path':str(collector_path),'sha256':COLLECTOR_SHA}
        collector = load_module('bound_census_collectors',collector_entry,COLLECTOR_SHA)
        previous_collector = sys.modules.get('bound_census_collectors')
        sys.modules['bound_census_collectors'] = collector
        try:
            verifier = load_module('private_input_native',c['native_verifier'],NATIVE_SHA)
        finally:
            if previous_collector is None:sys.modules.pop('bound_census_collectors',None)
            else:sys.modules['bound_census_collectors'] = previous_collector
        receiver_raw = read_pinned(c['receiver'],131000)
        r.require(hashlib.sha256(receiver_raw).hexdigest() == hashlib.sha256(Path(r.__file__).read_bytes()).hexdigest(), 'private_receiver_source_differs')
        heartbeat = None
        if c['mode'] == 'SOURCE':
            r.require(isinstance(c['phase_state'],str) and Path(c['phase_state']).is_absolute()
                      and isinstance(c['restore_pr'],str) and re.fullmatch(r'[1-9][0-9]*',c['restore_pr'])
                      and isinstance(c['phase_identity_sha256'],str) and r.SHA.fullmatch(c['phase_identity_sha256'])
                      and isinstance(c['source_fence'],dict) and set(c['source_fence']) == {'pg_backend_pid','pg_health_at'}
                      and type(c['source_fence']['pg_backend_pid']) is int and c['source_fence']['pg_backend_pid'] > 0
                      and c['selected_scope'] is None and (c['namespace'],c['job_name']) == helper.SOURCE, 'source_private_input_scope')
        else:
            r.require(all(c[k] is None for k in ('phase_state','source_fence','restore_pr','phase_identity_sha256')), 'live_cannot_claim_copy_phase_or_pg')
        pod = verify(c,manifest,native,helper,verifier,execute)
        raws = []
        if c['mode'] == 'LIVE':
            scope = read_pinned(c['selected_scope'],r.MAX_BYTES)
            r.validate_scope(scope)
            raws.append(('selected-stage-scope.json',scope))
        raws.append(('native-source-binding.json',native_raw))
        r.require(sum(len(raw) for _,raw in raws) <= r.MAX_BYTES, 'private_input_aggregate_cap')
        files = [{'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in raws]
        # Floor the transport clock to six decimal places; never extend any bound.
        epoch = str(int(deadline*1000000)/1000000)
        header = {'schema':1,'mode':c['mode'],'phase_token':c['phase_token'],'job_uid':c['job_uid'],
                  'pod_uid':c['pod_uid'],'deadline_epoch':epoch,'files':files}
        payload = r.canonical(header)+b'\n'+b''.join(raw for _,raw in raws)
        command = ['kubectl','exec','-i','-n',c['namespace'],c['pod_name'],'-c',containers[0]['name'],'--',
                   'nice','-n','19','python','-B','-c',receiver_raw.decode()]
        if c['mode'] == 'SOURCE':heartbeat = check_phase(c,helper)
        result = r.decode(execute(command,payload,8192))
        expected = {'schema':1,'type':'private_inputs_ready','mode':c['mode'],'phase_token':c['phase_token'],
                    'job_uid':c['job_uid'],'pod_uid':c['pod_uid'],'files':files,'production_writes':0}
        r.require(result == expected, 'private_receiver_receipt_differs')
        verify(c,manifest,native,helper,verifier,execute)
        if c['mode'] == 'SOURCE':check_phase(c,helper,heartbeat)
        read_pinned(c['native_binding'],r.MAX_BYTES)
        if c['mode'] == 'LIVE':read_pinned(c['selected_scope'],r.MAX_BYTES)
        r.require(time.time() < deadline and 0 <= time.time()-observed <= 15, 'private_delivery_expired')
        return {**expected,'deadline_epoch':epoch,'observed_at':native['observed_at'],
                'native_contract_sha256':c['native_binding']['sha256'],'phase_state_written':False}


def main():
    host_end = time.time()+12
    parser = argparse.ArgumentParser()
    parser.add_argument('--contract',required=True)
    parser.add_argument('--receipt',required=True)
    args = parser.parse_args()
    # Allocate the unique private receipt before any API/exec; never overwrite it.
    fd = os.open(args.receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    result = {'type':'private_inputs_refused','code':'private_input_contract_refused','production_writes':0,'automatic_retry':False}
    status = 2
    try:
        with r.alarm(host_end):
            contract_fd = os.open(args.contract,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(contract_fd,'rb') as source:
                info = os.fstat(source.fileno())
                r.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 65536,'host_contract_type_or_cap')
                contract = r.decode(source.read(65537))
            result = delivery(contract,host_deadline_epoch=host_end);status = 0
    except Exception:
        # Never emit foreign native error text, command arguments or credentials.
        pass
    finally:
        try:
            with r.alarm(time.time()+1), os.fdopen(fd,'wb') as receipt:
                receipt.write(r.canonical(result)+b'\n');receipt.flush();os.fsync(receipt.fileno())
        except Exception:
            status = 2
    return status


if __name__ == '__main__':
    raise SystemExit(main())
