#!/usr/bin/env python3
"""Pinned LIVE receipt -> single config leaf -> existing COPY supervisor."""
import argparse, hashlib, json, os, shutil, subprocess, sys, time
from pathlib import Path

GO = 'ACTUAL_PARENT_BOUND_LIVE_AND_TWO_EXTRA_COPY_GO'
SELECTION = '45f1e892aaf7cf666e1f0ffaf89d67c1b12c9aa078d684432482e56d26a32bd9'
HOST_PATH = '/home/dev/work/hn-825-native-host-venv-1009/bin:/usr/local/bin:/usr/bin:/bin:/home/dev/.local/bin'
HOST_TOOLS = ('bash', 'curl', 'env', 'flux', 'gh', 'git', 'hw-ssh', 'kubectl', 'nice', 'python3', 'ssh')
TOOL_BYTE_CAP = 128 * 1024 * 1024

def host_tool_contract(read):
    tools = {}
    for name in HOST_TOOLS:
        path = shutil.which(name, path=HOST_PATH)
        if path is None:
            raise ValueError('required_host_tool_missing: ' + name)
        resolved = str(Path(path).resolve(strict=True))
        tools[name] = dict(path=path, resolved_path=resolved, sha256=hashlib.sha256(read(Path(resolved), TOOL_BYTE_CAP)).hexdigest())
    return dict(schema=1, PATH=HOST_PATH, executables=tools)

def validate_host_tools(tools, pinned, require):
    require(set(tools) == {'schema', 'PATH', 'executables'} and tools['schema'] == 1
            and tools['PATH'] == HOST_PATH and set(tools['executables']) == set(HOST_TOOLS), 'exact_host_tool_contract_required')
    for name, entry in tools['executables'].items():
        require(set(entry) == {'path', 'resolved_path', 'sha256'}
                and shutil.which(name, path=HOST_PATH) == entry['path'], 'required_host_tool_resolution_differs')
        require(str(Path(entry['path']).resolve(strict=True)) == entry['resolved_path'], 'required_host_tool_resolved_path_differs')
        pinned(dict(path=entry['resolved_path'], sha256=entry['sha256']), TOOL_BYTE_CAP)

def bind_completed_live(config, live, receipt, ack, answer, baseline, require):
    require(receipt.get('baseline_complete') is True and receipt.get('cleanup_complete') is True
            and receipt.get('job_and_all_owned_pods_absent') is True
            and not receipt.get('refused') and not receipt.get('create_outcome_unknown')
            and receipt.get('production_library_writes') == receipt.get('PG_operations') == 0,
            'live_completion_or_cleanup_unproved')
    digest = hashlib.sha256(baseline).hexdigest()
    expected = dict(schema=1, phase_token=live['phase_token'], job_uid=receipt['job_uid'],
                    pod_uid=receipt['pod_uid'], baseline_sha256=digest)
    require(receipt['phase_token'] == live['phase_token'] and bool(receipt['job_uid'])
            and bool(receipt['pod_uid']) and ack == dict(expected, type='live_baseline_delivery_ack')
            and answer == dict(expected, type='live_baseline_ack_ready', production_writes=0),
            'live_ack_or_identity_differs')
    require(receipt['baseline'] == dict(path=config['live_byte_baseline']['path'], sha256=digest),
            'live_baseline_config_binding_differs')
    value = json.loads(baseline)
    require(value['capture_started_at'] == receipt['original_byte_started_at']
            and value['completed_at'] == receipt['original_byte_completed_at'], 'original_byte_clock_differs')
    require(config['live_byte_baseline']['sha256'] is None, 'config_already_bound')
    result = dict(config, live_byte_baseline=dict(config['live_byte_baseline'], sha256=digest))
    return (json.dumps(result, sort_keys=True, indent=2) + '\n').encode(), value['capture_started_at']

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-launcher', required=True)
    parser.add_argument('--live-launcher-sha256', required=True)
    parser.add_argument('--contract', required=True)
    parser.add_argument('--contract-sha256', required=True)
    parser.add_argument('--root-authorization')
    args = parser.parse_args()
    if args.root_authorization != GO:
        print(json.dumps(dict(prepared_only=True, production_actions=0))); return
    source = Path(args.live_launcher).read_bytes()
    if hashlib.sha256(source).hexdigest() != args.live_launcher_sha256:
        raise RuntimeError('reviewed_live_launcher_changed')
    scope = dict(__name__='pinned_live_launcher', __file__=args.live_launcher)
    exec(compile(source, args.live_launcher, 'exec'), scope)
    pinned, decode, require, private = [scope[key] for key in ('pinned', 'decode', 'require', 'private')]
    handoff = decode(pinned(dict(path=args.contract, sha256=args.contract_sha256)))
    require(set(handoff) == {'schema', 'phase_token', 'phase_state', 'live_contract', 'prelive_config', 'supervisor', 'host_tools'}
            and handoff['schema'] == 1, 'exact_handoff_contract_required')
    live = decode(pinned(handoff['live_contract']))
    config = decode(pinned(handoff['prelive_config']))
    phase = decode(pinned(handoff['phase_state']))
    pinned(handoff['supervisor'])
    require(phase['phase_token'] == handoff['phase_token'] and phase['complete'] is False
            and all(row['uid'] is None for row in phase['owned_jobs'])
            and handoff['phase_state']['path'] == config['copy_phase_state'], 'fresh_exact_copy_phase_required')
    require(config['cached_source_receipt'] and config['max_service_absence_seconds'] == 300
            and config['restore_reserve_seconds'] == 130 and config['arm_deadline_seconds'] == 1800
            and config['selection_approval']['sha256'] == SELECTION
            and config['live_byte_baseline'] == dict(path=str(Path(live['output_dir'])/'live-byte-baseline.json'), sha256=None),
            'preapproved_two_extra_cached_config_required')
    final = Path(config['watchdog_state']).parent/'supervisor-config.actual.json'
    require(not final.exists(), 'final_config_already_exists')
    require(pinned(handoff['prelive_config']) == (json.dumps(config, sort_keys=True, indent=2)+'\n').encode(),
            'preapproved_config_changed')
    for key in ('manifest_contract', 'hold_receipt', 'selection_approval', 'census_holds', 'cached_source_receipt'):
        require(isinstance(config.get(key), dict) and set(config[key]) == {'path', 'sha256'},
                'exact_static_root_scope_ref_required')
        pinned(config[key])
    supervisor_path = handoff['supervisor']['path']
    supervisor_scope = dict(__name__='pinned_copy_supervisor', __file__=supervisor_path)
    original_path = sys.path[:]
    try:
        sys.path.insert(0, str(Path(supervisor_path).parent))
        exec(compile(pinned(handoff['supervisor']), supervisor_path, 'exec'), supervisor_scope)
        supervisor_scope['validate'](config, live_baseline_pending=True)
    finally:
        sys.path[:] = original_path
    validate_host_tools(decode(pinned(handoff['host_tools'])), pinned, require)
    subprocess.run([sys.executable, '-I', '-B', args.live_launcher, '--contract', handoff['live_contract']['path'],
                    '--root-authorization', scope['GO']], stdin=subprocess.DEVNULL, check=True,
                   env=dict(os.environ, PATH=HOST_PATH))
    # No API calls or object census here: the pinned LIVE already proves ACK/GC.
    require(pinned(handoff['prelive_config']) == (json.dumps(config, sort_keys=True, indent=2)+'\n').encode(),
            'preapproved_config_changed')
    out = Path(live['output_dir'])
    receipt, ack, answer = [decode((out/name).read_bytes()) for name in
                           ('actual-receipt.json', 'delivery-ack.json', 'ack-receiver-receipt.json')]
    baseline = pinned(receipt['baseline'], 32*1024*1024)
    raw, original = bind_completed_live(config, live, receipt, ack, answer, baseline, require)
    # Admission retains the original earliest byte timestamp and prospective 130-second reserve.
    require(0 <= time.time()-scope['dt'].datetime.fromisoformat(original.replace('Z', '+00:00')).timestamp() <= 300,
            'original_byte_admission_reserve_expired')
    private(final, raw)
    pinned(handoff['supervisor'])
    os.environ['PATH'] = HOST_PATH
    bootstrap = 'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name="__main__")'
    os.execv(sys.executable, [sys.executable, '-I', '-B', '-c', bootstrap,
                            str(Path(handoff['supervisor']['path']).parent), handoff['supervisor']['path'],
                            '--config', str(final), '--execute'])

if __name__ == '__main__':
    main()
