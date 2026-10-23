# Story182: exact seven-consumer image review

Independent, read-only source review within the existing Story182 scope and original 21:02Z cap. Sole executor remains driver. No runtime/private-payload/auth/credential read, test run, source edit, push, worker or Job launch occurred.

**Result: no blocking source or compatibility finding.** Reviewed worktree `/home/dev/work/renovate-codeserver-consumer-1023`, exact clean HEAD `1c9d5c500efddc3064f69339fe7aa4f73177ad86`. Its commit changes exactly three files, seven additions/seven deletions; every changed line is solely the old digest `2f496fc929eebfba68557ed6714f425021ea1bfefccd04439846dbb21227a2df` replaced with `a2e5fe04e34adfa073abeb04554b9c706216d0a106d8e2625a8e9f4d0fdf3e5b`, preserving `ghcr.io/thaynes43/dev-env:0.6.8` and every launch/config field.

| File | Verified new digest pins |
| --- | --- |
| `kubernetes/main/apps/dev/dev-env/app/helmrelease.yaml` | 3: app, gh-refresher, auth-watch; lines46/229/261 |
| `kubernetes/main/apps/upgrade-agent/dev-env-ops/app/helmrelease.yaml` | 3: existing parent app/refresher and future CronJob container; lines35/154/202 |
| `kubernetes/main/apps/ai/vexa/app/resources/scribe-notes-job.json` | 1: future scribe-notes Job container; line20 |

## Publication and launch compatibility

- Read `/tmp/renovate-182-v1-image-proof.json`: registry-byte/header verification records this digest, config `sha256:4d9708fb887d790cbebf1c330126e352f4860229a8be5174dae6b28bbcac4a98`, source `beebb168ca24359ac53f83b278d6e55191ff79a4`, code-server4.141.1 and publisher40017564194. That local receipt explicitly makes no independent cryptographic verification claim.
- Independently checked public [source commit beebb168](https://github.com/thaynes43/haynes-ops/commit/beebb168ca24359ac53f83b278d6e55191ff79a4): its sole change is `CODE_SERVER_VERSION=4.141.0`→`4.141.1` in `scripts/dev-env/Dockerfile`, merged from4253.
- Public [main publisher40017564194](https://github.com/thaynes43/haynes-ops/actions/runs/40017564194) now completed **success** on that exact source, with validation build, runtime-user offline smoke, validated-image publish and keyless-sign steps successful. Its publication log reports `0.6.8: digest: sha256:a2e5fe04e34adfa073abeb04554b9c706216d0a106d8e2625a8e9f4d0fdf3e5b`. This independently links the reviewed seven pins to the published source build; signature validity was not independently verified.
- The same smoke logged Python3.11.2, ClaudeCode2.1.309, global Node24.22.1 and `code-server4.141.1 279b5702aaff359a8344c9fb8ac0a66d934ff1d6 with Code1.141.0`. This supports the existing Python scribe and CLI launch contracts without a new requirements or caller adaptation. It does not prove a real scribe/inference/notes Job ran, or independently inspect code-server's bundled Node at runtime.
- Read the existing developer-safe source/publication receipts in [4254](https://github.com/thaynes43/haynes-ops/pull/4254). Earlier exact-release review and identical-Git-tree proxy-test evidence remain applicable; no new source delta expands that limitation.

## ConfigMap and restart distinction

There is **no V1 or dev-env-ops mounted scripts/config/requirements change**, nor any Reloader annotation change. Their parent Pod templates receive the image replacements together; this diff has no separate mounted ConfigMap-data event that could first roll them on the old image. The earlier V1 ConfigMap-plus-image ignore workaround is therefore unnecessary here.

Precision: the seventh JSON pin does change data in Vexa's stable-name `scribe-notes-jobtemplate` ConfigMap (`kustomization.yaml:34`, `disableNameSuffixHash:true`). It is not accurate to call the entire three-file diff “no ConfigMap data change.” The dispatcher mounts that stable name at `/templates`, and `create_notes_job` opens/loads the JSON immediately before constructing and POSTing a new Job (`dispatcher.yaml:114–131`). This pin is copied into future Job specs; it does not rewrite existing Jobs' image specs. Dispatcher/CronJob templates and worker-code ConfigMap are unchanged, and dispatcher source declares no Reloader annotation. No current Vexa parent-pod restart is introduced by the stable-template-name pin itself.

Source/publish/smoke evidence is complete for this bounded review. Fresh genuine-work/custody checks, merge/rollout, one replacement per affected existing parent, supervisor/CLI/bundled-Node observations and retained-custody verification remain driver-owned; this receipt claims none of those runtime acceptance states. No historical runtime hold is reintroduced. Stop after receipt.
