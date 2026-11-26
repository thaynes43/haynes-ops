# OP978 fifth Studio release

**Expected.** The plain pin moves the API, web, MCP and run, voice and image
workers from Studio `470a90af73a2201e6542b5d867853dce6facad04` to
`eeeb2a40105ae1e64325063b0aa93c890fdefa43`. The Editor gains its Format control.
Every API replica serves with `STUDIO_PAUSED_RECONCILE` unset.
[Story 978](https://work.haynesnetwork.com/work_packages/978) ·
[Studio images run 45776715587](https://github.com/thaynes43/haynes-studio/actions/runs/45776715587)

Alembic stays at `0013_matte_jobs`. The digest-named migration Job runs a
no-op `upgrade head` and reads back the schema and migrator identity. The same
lock, base recipes and render requirements keep the offline render pod in
place with PyAV 18.1.1. The [fourth-release page](studio-fourth-release.md)
describes the installed predecessor, tools, stage order, listener checks,
queue and store checks, and Job audit details.

## Preflight and merge

Under CR-63, the reviewed GitOps merge triggers the rollout. From the clean
reviewed head, use the fourth pin's [preflight procedure](studio-fourth-release.md#preflight)
with new private `STUDIO_OP978_RECEIPT` and `STUDIO_OP978_BASELINE` paths. The
fifth pin also checks the complete 0013 catalogue, both API processes for the
unset switch and the existing render pod's forwarding init and idle venv. It
records the render pod UID and the shared PyAV and bundled-library versions
from both API replicas and all four workers. Keep submissions closed and
offline render idle. The queue gate includes any queued cutout.

```bash
if ! bash scripts/studio-release/op967.sh op978 preflight ||
   ! bash scripts/studio-release/op967.sh op978 admit-merge; then
  printf 'FAIL release preflight, hold the merge\n' >&2
  exit 1
fi
```

## Wait and read back

From the exact Ops merge, set `MERGED_OPS_REVISION` to the full SHA and keep
`STUDIO_OP978_BASELINE` set to the preflight snapshot. The
[automatic wait](studio-fourth-release.md#wait-for-automatic-deployment) uses
the same deadlines and stage checks. The render stage remains part of the
audit and keeps its existing pod.

```bash
if ! bash scripts/studio-release/op967.sh op978 wait; then
  printf 'FAIL automatic Flux rollout, keep submissions closed\n' >&2
  exit 1
fi
```

Postflight adds a comparison of the live render pod UID and the six encoder
reports to the baseline. It reads `/proc/1/environ` in both API containers and
checks for absence of the switch name, including an empty assignment. The
MCP pod sends anonymous TLS-verified GETs through the same internal ingress
as the status checks. It reads `/`, follows the single same-origin Vite module
`/assets/index-<hash>.js` declared by the HTML, requires a 200 JavaScript
response, and matches the three literal strings `story-format-section`,
`Format version` and `Format settings saved.`. This proves the served
bundle contains the control. A signed-in Editor walkthrough provides
separate usability evidence.

```bash
if ! bash scripts/studio-release/op967.sh op978 postflight; then
  printf 'FAIL release verification, keep submissions closed\n' >&2
  exit 1
fi
```

`verify` can repeat the audit with an optional baseline. An audit with no
baseline records the current UID, encoders, archives and store. Use the
baseline-backed `postflight` as rollout evidence and record Gatus before
reopening submissions.

## Retry a terminal migration through a reviewed PR

Use the [same Job and health-check edits as the fourth pin](studio-fourth-release.md#retry-a-terminal-migration-with-a-new-pr).
Set `STUDIO_OP978_FAILED_OPS_REVISION` and `STUDIO_OP978_PREVIOUS_RECEIPT`
to the failed fifth-pin merge and its original receipt. Use new `STUDIO_OP978_RECEIPT`
and `STUDIO_OP978_BASELINE` paths. The current catalogue must match 0013.
The retained archive and fresh data, render pod UID and encoder reads must
match the original preflight evidence.

```bash
if ! bash scripts/studio-release/op967.sh op978 retry-preflight ||
   ! bash scripts/studio-release/op967.sh op978 admit-merge retry; then
  printf 'FAIL retry preflight, hold the merge\n' >&2
  exit 1
fi
```

Use the exact retry merge with `wait` and `postflight` and the retry baseline.

## Roll back the pins through a reviewed PR

Keep submissions closed until active and held work settles. Start the
revert from current Ops `main`. Restore `release.yaml`, the API, MCP, web, run,
voice and image Deployment pins, all four digest-named Job manifests and
their Job health-check names in `ks.yaml`. Apply the reverse of the original
pin's patch only, so later unrelated changes remain in place. In this block
`FIFTH_PIN_OPS_REVISION` is the original reviewed fifth-pin squash merge.
If a retry added an `-rN` suffix, first remove that suffix from the Job name
in `migration/job.yaml` and its matching health check in `ks.yaml`. Then
apply this block to restore all four fourth-pin Job names.

```bash
set -o pipefail
studio=kubernetes/main/apps/ai/studio
git diff --no-ext-diff --no-textconv "${FIFTH_PIN_OPS_REVISION:?}^1" "$FIFTH_PIN_OPS_REVISION" -- \
  "$studio"/{release.yaml,ks.yaml} "$studio"/app/{api,mcp,web}.yaml \
  "$studio"/{worker/deployment,migration/job,worker-registration/job}.yaml \
  "$studio"/media-workers/{image,voice}.yaml "$studio"/media-worker-registration/{image,voice}-job.yaml |
  git apply --reverse
```

The fourth pin already serves schema 0013. Its migration Job will read
back that head if Flux needs to recreate the Job. The render files,
policy stages and listener configuration carry forward.

From the clean reviewed revert head, set `MERGED_OPS_REVISION` to the
currently deployed fifth-pin merge, including a retry if applicable. Set
new private `STUDIO_OP978_RECEIPT` and `STUDIO_OP978_BASELINE` paths.

```bash
if ! bash scripts/studio-release/op967.sh op978 rollback-preflight ||
   ! bash scripts/studio-release/op967.sh op978 admit-merge rollback; then
  printf 'FAIL rollback preflight, hold the merge\n' >&2
  exit 1
fi
```

After the revert merges, return to the selected fifth-pin checkout. Keep
`MERGED_OPS_REVISION` set and set `ROLLBACK_OPS_REVISION` to the full revert
merge SHA. Set `STUDIO_OP978_ROLLBACK_BASELINE` to the rollback preflight
snapshot. Run the same `wait` block and then `compare`. It verifies the restored
workloads, schema, data and store. Record Gatus before reopening submissions.

```bash
if ! bash scripts/studio-release/op967.sh op978 compare; then
  printf 'FAIL rollback read-back, keep submissions closed\n' >&2
  exit 1
fi
```
