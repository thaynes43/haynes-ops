#!/usr/bin/env python3
"""Pinned LIVE receipt -> single config leaf -> existing COPY supervisor."""
import argparse, hashlib, json, os, subprocess, sys, time
from pathlib import Path

GO = 'ACTUAL_PARENT_BOUND_LIVE_AND_TWO_EXTRA_COPY_GO'
SELECTION = '45f1e892aaf7cf666e1f0ffaf89d67c1b12c9aa078d684432482e56d26a32bd9'

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
    require(set(handoff) == {'schema', 'phase_token', 'phase_state', 'live_contract', 'prelive_config', 'supervisor'}
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
    subprocess.run([sys.executable, '-I', '-B', args.live_launcher, '--contract', handoff['live_contract']['path'],
                    '--root-authorization', scope['GO']], stdin=subprocess.DEVNULL, check=True)
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
    bootstrap = 'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name="__main__")'
    os.execv(sys.executable, [sys.executable, '-I', '-B', '-c', bootstrap,
                            str(Path(handoff['supervisor']['path']).parent), handoff['supervisor']['path'],
                            '--config', str(final), '--execute'])

if __name__ == '__main__':
    main()
