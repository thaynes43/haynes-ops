#!/usr/bin/env python3
"""Read-only native/Git verification; seal one private cache receipt. No holds."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time

import cached_source as cache
import window_contract as wc


wall_guard = cache.wall_guard


def seal(draft, contract, repo, get, run, phase, deadline):
    wc.require(draft.get('phase_token') == phase and re.fullmatch('[0-9a-f]{40}', draft.get('stop_main_sha', '')),
               'exact phase/Stop SHA required')
    inverse = run(['gh', 'pr', 'view', str(draft['restore_pr']), '--repo', 'thaynes43/haynes-ops',
                   '--json', 'state,baseRefName,headRefOid,mergeCommit'])
    inverse = json.loads(inverse)
    merge = draft['normal_inverse_merge_sha']
    wc.require(re.fullmatch('[0-9a-f]{40}', merge) and inverse.get('state') == 'MERGED'
               and inverse.get('baseRefName') == 'main' and inverse.get('headRefOid') == draft['restore_head']
               and inverse.get('mergeCommit', {}).get('oid') == merge, 'Normal inverse not exactly merged')
    run(['git', '-C', repo, 'fetch', 'origin', 'main:refs/remotes/origin/main'])
    current = run(['git', '-C', repo, 'rev-parse', 'origin/main']).strip()
    run(['git', '-C', repo, 'merge-base', '--is-ancestor', merge, current])
    parent = run(['git', '-C', repo, 'rev-parse', merge + '^']).strip()
    wc.verify_git_pair(repo, parent, merge, contract, 'inverse')
    wc.validate_pair(wc.blobs(repo, merge), wc.blobs(repo, draft['stop_main_sha']), contract)
    cache.normal_goal(wc.blobs(repo, current))
    cache.check_live(draft, get, contract, phase, holds=True, deadline=deadline)
    return draft


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft', required=True); parser.add_argument('--output', required=True)
    parser.add_argument('--repo-dir', required=True); parser.add_argument('--phase', required=True)
    parser.add_argument('--contract', default=str(Path(__file__).with_name('manifest-contract.json')))
    args = parser.parse_args()
    end = time.time() + 30
    def run(argv):
        budget = min(8, end - time.time())
        wc.require(budget > 0, 'cache sealing wall budget expired')
        result = subprocess.run(argv, capture_output=True, text=True, timeout=budget)
        wc.require(result.returncode == 0 and len(result.stdout.encode()) <= 1024 * 1024,
                   'read-only sealing command refused/cap')
        return result.stdout
    def get(kind, name, ns):
        return json.loads(run(['kubectl', 'get', kind, name, '-n', ns, '-o', 'json']))
    value = seal(json.loads(cache.read_private(args.draft)), json.loads(Path(args.contract).read_bytes()),
                 args.repo_dir, get, run, args.phase, end)
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    cache.write_private(args.output, raw)
    print(json.dumps({'result': 'SEALED_READ_ONLY_CACHE_RECEIPT', 'sha256': wc.sha(raw),
                      'holdsChanged': 0, 'jobsCreated': 0, 'runtimeAuthorization': False}))


if __name__ == '__main__':
    try:
        with wall_guard(30): main()
    except Exception as error:
        print(json.dumps({'result': 'REFUSED', 'kind': type(error).__name__,
                          'holdsChanged': 0, 'jobsCreated': 0, 'runtimeAuthorization': False}))
        raise SystemExit(2)
