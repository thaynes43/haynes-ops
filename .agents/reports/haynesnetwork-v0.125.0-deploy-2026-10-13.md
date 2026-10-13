# haynesnetwork v0.125.0 deployment — 2026-10-13

This is PLAN-077 step 1: ship the Seerr issue bridge, staff Wrong-Content
Replace and its recovery, and the owned P7 smoke helper. Application changes
landed in [haynesnetwork #965](https://github.com/thaynes43/haynesnetwork/pull/965);
[release #968](https://github.com/thaynes43/haynesnetwork/pull/968) produced
`v0.125.0` at application main `bad7368d`.

The GitOps change sets the same release on three pins:

- `frontend/haynesnetwork/app/helmrelease.yaml`: the `&mainImage` anchor moves
  the migrator, three web replicas and all 24 frontend sync CronJob templates.
- `downloads/owed-checks/app/cronjob.yaml`: the read-only owed-check runner.
- `downloads/books-census/app/cronjob.yaml`: the observe-only Books Census.

All paths above are under `kubernetes/main/apps/`. The prepared base is
haynes-ops main `85d1dce3`. The live baseline had three ready web replicas and
all 26 CronJob templates on `v0.123.1`; finished Job history included older
releases. Historical finished pods are evidence of their own runs and are not
deleted to make their tags match the current templates.
The read-only pre-deploy database check found 104 journal rows, latest
`created_at` value `1791420000000` (0104), and no `seerr_issue_writes` table.

## Artifact gate and review

The release reviewer established both HTTP 200 responses at 22:58–22:59Z
for the GHCR release manifest and its digest's `.sig` manifest, and a
successful keyless `cosign verify` step for that exact release digest:

```text
image: ghcr.io/thaynes43/haynesnetwork:v0.125.0
digest: sha256:5ceb01cb1e46534b375a2b379af20f8ca4de70f38c8c81b3adcda92133af7d87
signature tag: sha256-5ceb01cb1e46534b375a2b379af20f8ca4de70f38c8c81b3adcda92133af7d87.sig
signature manifest: HTTP 200, OCI image manifest, one signature layer
issuer: https://token.actions.githubusercontent.com
subject: https://github.com/thaynes43/haynesnetwork/.github/workflows/release-please.yml@refs/heads/main
source SHA: bad7368d5e494e14b40399e31622ee6d88d9d7c4
```

[Publisher run 38480728183](https://github.com/thaynes43/haynesnetwork/actions/runs/38480728183)
completed successfully. Its explicit cosign verification step 11 completed
successfully, logging `cosign verify succeeded on attempt 1/5` at
`2026-10-13T22:56:49.939Z`. The signed payload binds the digest above to the
issuer, subject and source SHA above.

The signature request accepts `application/vnd.oci.image.manifest.v1+json`;
an index-only Accept header can produce a false 404. A generic successful
release-please run is insufficient because a no-release run also succeeds.
The verified digest and workflow evidence accompany the deployment PR before
root reviews its exact head and authorizes merge.

Required GitOps checks are `Flux Local - Success` and `Diff Scope - Success`.
The optional Claude review is read if it emits findings; unavailable service
or quota does not trigger repeated review attempts. Local validation stays
within the affected manifests, under `nice -n 19`; no whole-cluster or wide
parallel test run is needed for these three tag substitutions.

## Rollout and verification

Root's merge signal precedes any cluster mutation. Declare the rollout with
scope `frontend,haynesnetwork,downloads,owed-checks,books-census`, TTL 45m,
then reconcile source `haynes-ops` in `flux-system` and Kustomizations
`haynesnetwork` in `frontend`, `owed-checks` and `books-census` in `downloads`.
Check their applied revision against the actual squash commit and wait for
the HelmRelease and Deployment to become ready. End the activity early when
verification finishes.

Verify the three serving web pods using both the `haynesnetwork` name and
`main` controller labels. All must be Running and Ready, with the app and
`migrate` containers on `ghcr.io/thaynes43/haynesnetwork:v0.125.0`.
Check all 24 frontend CronJob templates and both downloads templates against
that exact image. Retained Succeeded/Failed Job pods are assessed separately.
Check internal and external `/api/health`, all three migrator exit statuses
and logs, and the migration journal for:

| Migration | Expected change |
|---|---|
| `0105_seerr_issue_writes` | Issue write ledger, Fix issue link and bridge setting |
| `0106_watch_carry_wrong_content` | `wrong_content` Watch Carry kind |
| `0107_seerr_bridge_smoke` | Owned smoke setting key |

Expected SHA-256 journal hashes from application main `bad7368d`:

```text
0105 e561d6935b91988c4994c4a5eed23abe7a3a062be0b086f2079023c860576585
0106 0f9cfba1b38c9253d01a9375ab4af4399e9157cfd45b855cd7b1e60ee853abf7
0107 fd08da24d175c95f95a81d419656514957f649cca65fb876578ca2b1ca39844e
```

The migrators serialize through the existing advisory lock. Verification is
a read-only database transaction comparing the three journal hashes with the
SQL files baked into this exact release, plus the named table/column/check
constraints; a successful log line alone is not the migration proof.

Run one bounded observe-only Books Census Job and then one read-only
owed-check Job from the reconciled templates. They keep their existing
`backoffLimit: 0`, CPU cap and 600s/180s active deadlines. Record actual
Job/Pod image and digest, native completion/exit zero and retained logs. Do
not start a duplicate while either matching CronJob already has an active
run. Findings and pending owed checks are observations, not authorization
to repair media or advance a future check.

P7 enablement and its owned smoke are PLAN-077 step 2; Q-12 is step 3;
wrong-content replacements are step 4. This deployment does not perform
those media or Seerr writes. A smoke fixture never counts as a real report
or member use. OC-056 remains gated until `2026-10-15T20:10Z` and closed Fix
preflight, including Paw Patrol's Watch Carry ordering switch.

Live results are recorded after the rollout in the application PLAN-077
deployment record and the deployment PR evidence, with this preparation
note retained as the exact scope and verification contract.
