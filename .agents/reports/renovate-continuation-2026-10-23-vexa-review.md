# Story181: seventh0.6.8 pin static review

**Assessment:** updating only `kubernetes/main/apps/ai/vexa/app/resources/scribe-notes-job.json` from40f91d3 to `ghcr.io/thaynes43/dev-env:0.6.8@sha256:2f496fc929eebfba68557ed6714f425021ea1bfefccd04439846dbb21227a2df` is coherent with the six V1/ops image fields and introduces no intended second V1 restart. No source edits or commit were made by this helper.

## Consumption and restart scope

- `vexa/app/kustomization.yaml` packages the JSON into stable-name ConfigMap `scribe-notes-jobtemplate` with `disableNameSuffixHash: true`. Only that ConfigMap data changes for a one-image edit. It does not change a HelmRelease, Deployment, CronJob Pod template or V1 scripts/config.
- `dispatcher.yaml` mounts that ConfigMap at `/templates` without subPath. `create_notes_job` opens and JSON-loads `/templates/scribe-notes-job.json` for each new notes Job, fills three meeting fields, then POSTs it to Kubernetes. The name is meeting-id-based;409 means already handled and does not update an existing Job.
- An already-running dispatcher may observe the new projected file after kubelet propagation and use it for a subsequently created notes Job. No dispatcher restart is needed. New dispatcher runs also read the updated template.
- Existing notes Jobs have their image already copied into their own immutable Pod template and mount `scribe-notes-worker`, not the Job-template ConfigMap. This image edit cannot rewrite their image or restart them. Worker Python/prompt ConfigMap stays unchanged.
- Main Vexa meeting/runtime/gateway pods do not mount this Job-template ConfigMap. The source contains no Reloader opt-in on dispatcher/notes Jobs or Vexa consumers, and the source Reloader chart configuration does not enable global auto-reload-all. No ConfigMap hash-name rewrite or Helm checksum connects this JSON edit to their Pod templates. Thus no source restart path is introduced for current meeting jobs or parent pods. No live resources were queried; this is source-scope evidence, not an installed-pod claim.
- `.renovate/holds.json5` explicitly names this seventh pin as manually maintained with the other0.6.x consumers, and forbids an accidental2.x migration. Keep tag0.6.8 and all commands/env/auth/publication flags unchanged.

## Published runtime compatibility evidence

[Main build40005750765](https://github.com/thaynes43/haynes-ops/actions/runs/40005750765) on source `fa3e3217c29b1db44e5acc0e570f78071670c588` succeeded. Its runtime-user offline smoke prints Python3.11.2 and ClaudeCode2.1.309, then publication records exact2f496fc digest for0.6.8/latest. Safe filtered lines saved in `/tmp/vexa-scribe-image-public-proof.txt`. This reuses the reviewed V1 image publication rather than building another image.

Worker imports Python stdlib only (json/os/re/subprocess/sys/urllib/datetime/zoneinfo). AST parsing with actual local Python3.11.2 and Job JSON parsing passed without executing worker main, loading credentials, reading transcripts or calling any service. The unchanged CLI path uses piped stdin, -p, --permission-mode dontAsk, --disallowedTools, --model and --output-format json. [Official CLI reference](https://code.claude.com/docs/en/cli-reference) and [permissions reference](https://code.claude.com/docs/en/permissions) retain these interfaces. Version2.1.309 exceeds the existing model floors recorded in source. This establishes no observed source/API incompatibility; no authenticated model or summary-output behavior was tested.

## Test/workflow limit

No scribe-notes unit fixture or corresponding GitHub workflow exists in the current combined source tree or current-main public GitHub tree (only Vexa chart values-test.yaml matches the relevant test filename search). A content search over workflows likewise found no scribe/vexa test trigger. Therefore a one-pin JSON change does NOT presently trigger an existing scribe-specific unit suite. Do not report one as green or silently assume another workflow covers it. Normal Kubernetes Flux validation applies to its rendered ConfigMap, while this helper verified JSON and Python parse only. If the driver has a separately provided scribe fixture, explicitly include the JSON path in that workflow trigger and run its offline fake-CLI path; no live scribe/LLM/publication test is warranted for this pin edit.

No runtime mutation, notes Job creation, model call, publication, notification, credential read, login, MCP activation or caller migration was performed. Driver owns any source edit/PR/CI/activation evidence.
