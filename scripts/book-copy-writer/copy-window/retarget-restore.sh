#!/usr/bin/env bash
set -euo pipefail
set +x

if [[ "$#" -lt 5 || "$#" -gt 6 ]]; then
  printf '%s\n' 'Usage: retarget-restore.sh WORKTREE PAUSE_PR RESTORE_PR ORIGINAL_PAUSE_HEAD RESTORE_BRANCH [core|copy]' >&2
  exit 2
fi
restore_worktree=$1
pause_pr=$2
restore_pr=$3
pause_head=$4
restore_branch=$5
profile=${6:-copy}
[[ "$profile" == copy ]]
contract_script="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/window_contract.py"
repo=thaynes43/haynes-ops
validation_subject='chore(books): validate restore against main'
[[ "$restore_worktree" == "$HOME/work/"* && -d "$restore_worktree" ]]
[[ "$pause_pr" =~ ^[0-9]+$ && "$restore_pr" =~ ^[0-9]+$ ]]
[[ "$pause_head" =~ ^[0-9a-f]{40}$ && "$restore_branch" == agent/* ]]

# Serialize retries and refresh the credential without displaying it.
exec 9>"${restore_worktree}.retarget.lock"
flock -n 9 || { printf '%s\n' 'Another restore retarget invocation is running.' >&2; exit 1; }
if [[ -z "${GH_TOKEN:-}" ]]; then
  IFS= read -r GH_TOKEN < /creds/gh_token || [[ -n "${GH_TOKEN:-}" ]]
fi
[[ -n "${GH_TOKEN:-}" ]]
export GH_TOKEN

verify_local_inverse() {
  python3 -B "$contract_script" "$restore_worktree" origin/main HEAD --direction inverse
  python3 - "$restore_worktree" "$profile" <<'PY_VERIFY_LOCAL'
import collections
import subprocess
import sys
import yaml

worktree, profile = sys.argv[1:]
copy_profile = profile == "copy"
expected = {
    'kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert-cronjob.yaml': (1, 1),
    'kubernetes/main/apps/frontend/haynesnetwork/app/helmrelease.yaml': (4, 4),
    'kubernetes/main/apps/media/libretto/app/helmrelease.yaml': (1, 1),
}
if copy_profile:
    expected.update({
        'kubernetes/main/apps/downloads/lazylibrarian/app/library-scan-cronjob.yaml': (1, 1),
        'kubernetes/main/apps/downloads/lazylibrarian/app/helmrelease.yaml': (0, 1),
        'kubernetes/main/apps/media/kavita/app/helmrelease.yaml': (0, 1),
    })
def git(*args):
    return subprocess.check_output(['git', '-C', worktree, *args], text=True)
rows = git('diff', '--numstat', 'origin/main...HEAD').splitlines()
actual = {path: (int(added), int(deleted)) for added, deleted, path in (row.split(chr(9), 2) for row in rows)}
if actual != expected:
    raise SystemExit('Restore diff does not match the exact reviewed core/copy profile.')
lines = git('diff', '--unified=0', 'origin/main...HEAD').splitlines()
added = [line[1:].strip() for line in lines if line.startswith('+') and not line.startswith('+++')]
deleted = [line[1:].strip() for line in lines if line.startswith('-') and not line.startswith('---')]
expected_added = ['suspend: false'] * 5 + ['LAZYLIBRARIAN_URL: http://lazylibrarian.downloads.svc.cluster.local:5299']
expected_deleted = ['suspend: true'] * 5 + ['LAZYLIBRARIAN_URL: ""']
if copy_profile:
    expected_added.append('suspend: false')
    expected_deleted += ['suspend: true', 'replicas: 0', 'replicas: 0']
if collections.Counter(added) != collections.Counter(expected_added) or collections.Counter(deleted) != collections.Counter(expected_deleted):
    raise SystemExit('Restore may change only the reviewed CronJob flags, acquisition and pause-only replicas fields.')
def doc(ref, path):
    return yaml.safe_load(git('show', ref + ':' + path))
conv = 'kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert-cronjob.yaml'
def converter_env(value):
    return value['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['env']
pre, post = doc('origin/main', conv), doc('HEAD', conv)
if converter_env(pre) != converter_env(post):
    raise SystemExit('Entire converter env, hourly gate and holds must remain unchanged.')
values = {row['name']: row.get('value') for row in converter_env(post)}
if values.get('STRIP_SERIES_METADATA') != '0' or 'Daniel Silva/Ransom' not in __import__('json').loads(values.get('LIBRARY_HOLD_FOLDERS_JSON', '[]')):
    raise SystemExit('STRIP=0 and Ransom hold must be intact.')
if copy_profile:
    for ns, name in [('downloads', 'lazylibrarian'), ('media', 'kavita')]:
        path = f'kubernetes/main/apps/{ns}/{name}/app/helmrelease.yaml'
        if doc('origin/main', path)['spec']['values']['controllers'][name].get('replicas') != 0 or 'replicas' in doc('HEAD', path)['spec']['values']['controllers'][name]:
            raise SystemExit('Restore must remove only the two pause-only replicas fields.')
print('Verified exact recovery profile; strip, hourly gate and library holds are unchanged.')
PY_VERIFY_LOCAL
}

verify_pr_inverse() {
  python3 - "$restore_worktree" "$restore_pr" "$repo" "$validation_subject" "$profile" <<'PY_VERIFY_REMOTE'
import collections
import json
import subprocess
import sys
import time

worktree, pr_number, repo, subject, profile = sys.argv[1:]
copy_profile = profile == "copy"
expected = {
    'kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert-cronjob.yaml': (1, 1),
    'kubernetes/main/apps/frontend/haynesnetwork/app/helmrelease.yaml': (4, 4),
    'kubernetes/main/apps/media/libretto/app/helmrelease.yaml': (1, 1),
}
if copy_profile:
    expected.update({
        'kubernetes/main/apps/downloads/lazylibrarian/app/library-scan-cronjob.yaml': (1, 1),
        'kubernetes/main/apps/downloads/lazylibrarian/app/helmrelease.yaml': (0, 1),
        'kubernetes/main/apps/media/kavita/app/helmrelease.yaml': (0, 1),
    })
head = subprocess.check_output(['git', '-C', worktree, 'rev-parse', 'HEAD'], text=True).strip()
message = subprocess.check_output(['git', '-C', worktree, 'log', '-1', '--format=%B'], text=True).strip()
if (not message.startswith(subject + '\n')
        or not message.endswith('Co-Authored-By: Codex GPT-6.1 Sol <noreply@openai.com>')):
    raise SystemExit('Restore must contain the validation commit with its required footer.')
expected_added = collections.Counter(['suspend: false'] * 5 + ['LAZYLIBRARIAN_URL: http://lazylibrarian.downloads.svc.cluster.local:5299'])
expected_deleted = collections.Counter(['suspend: true'] * 5 + ['LAZYLIBRARIAN_URL: ""'])
if copy_profile:
    expected_added['suspend: false'] += 1
    expected_deleted['suspend: true'] += 1
    expected_deleted['replicas: 0'] += 2
shape = (7, 9, 6) if copy_profile else (6, 6, 3)
deadline = time.monotonic() + 55
last_reason = 'No metadata response.'
for attempt in range(15):
    try:
        pr = json.loads(subprocess.check_output([
            'gh', 'pr', 'view', pr_number, '--repo', repo,
            '--json', 'state,baseRefName,headRefOid,additions,deletions,changedFiles,files'
        ], text=True, timeout=10))
        actual = {row['path']: (row['additions'], row['deletions']) for row in pr['files']}
        if (pr['state'] not in ('OPEN', 'MERGED') or pr['baseRefName'] != 'main'
                or pr['headRefOid'] != head
                or (pr['additions'], pr['deletions'], pr['changedFiles']) != shape
                or actual != expected):
            last_reason = 'PR metadata does not yet show main, the pushed head and the exact reviewed inverse.'
        else:
            lines = subprocess.check_output(['gh', 'pr', 'diff', pr_number, '--repo', repo], text=True, timeout=10).splitlines()
            added = [line[1:].strip() for line in lines if line.startswith('+') and not line.startswith('+++')]
            deleted = [line[1:].strip() for line in lines if line.startswith('-') and not line.startswith('---')]
            if collections.Counter(added) == expected_added and collections.Counter(deleted) == expected_deleted:
                print(f'Restore PR #{pr_number} targets main with the exact inverse and validation commit. Required gates and the normal advisory review still must complete before merging.')
                break
            last_reason = 'PR diff does not yet show only the reviewed recovery profile.'
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        last_reason = f'Read-only PR verification failed: {type(exc).__name__}.'
    if attempt == 14 or time.monotonic() + 3 >= deadline:
        raise SystemExit('Bounded remote verification expired. ' + last_reason + ' Re-run after inspecting GitHub; no merge has been attempted.')
    time.sleep(3)
PY_VERIFY_REMOTE
}

ensure_validation_commit() {
  if [[ "$(git -C "$restore_worktree" log -1 --format=%s)" != "$validation_subject" ]]; then
    # The base-change event does not trigger main checks. Push synchronize only
    # after retargeting to request the required gates and normal advisory review.
    git -C "$restore_worktree" commit --allow-empty \
      -m "$validation_subject" \
      -m 'Co-Authored-By: Codex GPT-6.1 Sol <noreply@openai.com>'
  fi
}

if [[ "$(gh pr view "$pause_pr" --repo "$repo" --json state --jq .state)" != MERGED ]]; then
  printf 'Pause PR #%s must be merged before replaying its inverse.\n' "$pause_pr" >&2
  exit 1
fi
if [[ "$(gh pr view "$pause_pr" --repo "$repo" --json headRefOid --jq .headRefOid)" != "$pause_head" ]]; then
  printf '%s\n' 'Original pause head does not match its PR; refusing to proceed.' >&2
  exit 1
fi
pause_branch=$(gh pr view "$pause_pr" --repo "$repo" --json headRefName --jq .headRefName)
if [[ "$(git -C "$restore_worktree" branch --show-current)" != "$restore_branch" ]]; then
  printf '%s\n' 'Restore worktree is on another branch; refusing to proceed.' >&2
  exit 1
fi
if [[ -n "$(git -C "$restore_worktree" status --porcelain)" ]]; then
  printf '%s\n' 'Restore worktree has uncommitted changes; refusing to proceed.' >&2
  exit 1
fi
restore_base=$(gh pr view "$restore_pr" --repo "$repo" --json baseRefName --jq .baseRefName)
if [[ "$restore_base" != "$pause_branch" && "$restore_base" != main ]]; then
  printf '%s\n' 'Restore PR targets an unexpected branch; inspect it before proceeding.' >&2
  exit 1
fi
remote_ref=$(git -C "$restore_worktree" ls-remote origin "refs/heads/$restore_branch")
restore_remote_head=${remote_ref%%$'\t'*}
[[ "$restore_remote_head" =~ ^[0-9a-f]{40}$ ]]

# Keep the merged pause branch until the inverse merges. Base main alone never
# proves that the inverse was replayed onto the squash-merge commit.
if [[ "$restore_base" == main ]] \
    && ! git -C "$restore_worktree" merge-base --is-ancestor "$pause_head" HEAD \
    && [[ "$(git -C "$restore_worktree" log -1 --format=%s)" == "$validation_subject" \
          && "$(git -C "$restore_worktree" rev-parse HEAD)" == "$restore_remote_head" ]]; then
  # The completed path is read-only: no replay, reset, fetch, push or new commit.
  verify_local_inverse
  verify_pr_inverse
  exit 0
fi

git -C "$restore_worktree" fetch origin main:refs/remotes/origin/main
if git -C "$restore_worktree" merge-base --is-ancestor "$pause_head" HEAD; then
  git -C "$restore_worktree" rebase --onto origin/main "$pause_head" "$restore_branch"
fi
# An interrupted invocation may already have replayed. Verify instead of
# applying the inverse a second time; the lease uses the live remote branch.
verify_local_inverse
# Retarget while OPEN, make the validation commit locally, then push one final
# head. No intermediate rebased push can start a second Flux/Claude workflow.
[[ "$(gh pr view "$restore_pr" --repo "$repo" --json state --jq .state)" == OPEN ]]
gh pr edit "$restore_pr" --repo "$repo" --base main
ensure_validation_commit
verify_local_inverse
git -C "$restore_worktree" push --force-with-lease="refs/heads/$restore_branch:$restore_remote_head" origin "$restore_branch"
verify_pr_inverse
