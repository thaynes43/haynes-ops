# haynesnetwork v0.125.4 deployment preparation — 2026-10-14

The signed release and its exact signature artifact are verified. This change prepares the deployment; no merge,
Flux reconciliation, rollout or live media action is claimed here. The coordinator owns final review, merge and
live verification. Final Mickey E13 to E16 execution requires its separate reviewed fresh plan and authorization.

## Prepared scope

Fresh haynes-ops base: `d7948aecd113096411d0b20959a0452ed81da916`. Exactly three image pins move from
`v0.125.3` to `v0.125.4`: the frontend HelmRelease `mainImage` anchor (app, migrators and frontend CronJob
templates), downloads `owed-checks`, and downloads `books-census`.

The release contains same-parent selected multipart recovery from
[haynesnetwork #987](https://github.com/thaynes43/haynesnetwork/pull/987), squash-merged at `11:08:27Z` as
`6f431991d1e2dc167166a253203236b5e1255d8e`, with all four required checks successful and renewed advisory review
reporting no findings. [Release #988](https://github.com/thaynes43/haynesnetwork/pull/988) squash-merged at
`11:20:44Z` as `ab8181c36b00d389dbdb05db534b0a10845b6178`, with all four required checks successful.

## Signed artifact gate — verified

[Publisher run 38555912857](https://github.com/thaynes43/haynesnetwork/actions/runs/38555912857),
job `115824866506`, succeeded. Its actual cosign step 11 verified the expected repository workflow identity
regular expression, GitHub OIDC issuer and exact image digest on attempt 1 at `11:25:00.6443625Z`.

The coordinator independently fetched both actual GHCR manifests at `2026-10-14T11:26:10.253469Z`.
Both returned HTTP 200, and each response-body SHA matched its Docker-Content-Digest header:

```text
image: ghcr.io/thaynes43/haynesnetwork:v0.125.4
image digest: sha256:5522d758426a317b42fec609fbe02f8f27cdc042f03b133833002f8ccb2f881f
signature manifest digest: sha256:f4faf47c28acaecc3a19be71e0a847678c46a2e19c7faf62e1b8ad953d91cba2
source/workflow/tag SHA: ab8181c36b00d389dbdb05db534b0a10845b6178
```

The image response is an OCI image index; the `.sig` response is an OCI image manifest. The exact
[credential-free artifact proof](../evidence/haynesnetwork-v0.125.4-artifact-pair-20261014.json) is attached in
this change. Workflow success alone was not used as the deployment artifact gate.

## Planned live checks — pending

After the coordinator reviews and squash-merges the exact green deployment head, reconcile the GitRepository
and all three affected app Kustomizations. Verify their Ready revisions, the HelmRelease observed generation,
all three serving replicas and migrators, exact signed image IDs, all frontend CronJob templates and both
downloads templates. Check local health on each serving replica and ingress health, migration results and
the baked multipart/owned-import/janitor/private-CLI sources against the reviewed release tree.

Run any necessary Books Census and owed-check verification Jobs sequentially on the approved worker with
bounded deadlines, nice 19, low CPU, no retries and an actual scoped declaration. Record immutable Job/Pod
UIDs, native completion, exit codes and image IDs. A successful owed runner does not close manual, future,
waiting or unresolved owed checks and does not prove member completion or owner readiness.

Existing finite media workers remain on their independently authorized signed v0.125.3 image and original
UIDs, cutoffs and expiry. The three template pins neither replace nor restart those workers. Final multipart
recovery remains held until deployment is verified, all 23 initial Mickey slots finish, the predecessor is
actually terminal and the coordinator ratifies the separate fresh physical mapping and execution envelope.

No change touches dev-env ConfigMap resources. No CPU burners, broad parallel suites or looped load tests
run in the shared dev pod. Actual deployment evidence must replace this pending checklist after rollout.
