# Private repository Actions runners

ARC registers repository-scoped, ephemeral Linux amd64 runners for private
repositories owned by the personal account `thaynes43`. Public repositories keep
GitHub-hosted runners. Never install this App or register a scale set on a public
repository: a fork pull request could otherwise execute inside the cluster.

## Deployment and capacity

Flux installs the stable `gha-runner-scale-set-controller` and
`gha-runner-scale-set` OCI charts from
`oci://ghcr.io/actions/actions-runner-controller-charts`, initially version
`0.15.0`. The controller and listeners run in `arc-systems`; runner pods run in
`arc-runners`. The controller watches only `arc-runners`.

| Private repository | `runs-on` / runner scale set | Container mode |
| --- | --- | --- |
| `sigmaphiomicron-com` | `arc-sigmaphiomicron-com` | Docker-in-Docker |
| `sigo-alumni` | `arc-sigo-alumni` | Plain |
| `sigoalumni-org` | `arc-sigoalumni-org` | Docker-in-Docker |
| `demo-console` | `arc-demo-console` | Docker-in-Docker |
| `haynes-swarm` | `arc-haynes-swarm` | Docker-in-Docker |

The 2026-10-07 audit verified all five are private and have enabled workflows.
`demo-console` has not run since June, but its push/PR workflows remain enabled.
`haynes-swarm` still runs its weekly audit. The other five private repositories
(`flux-repo`, `config-backup`, `hass-config`, `esphome-config`, `TGRFramework`)
have no workflows or run history and receive no pool.

Each pool initially has `minRunners: 0` and `maxRunners: 1`. A listener remains
running even when its pool has zero job runners. Jobs queue behind that pool's
single runner; it is replaced after every job. Required node affinity confines
the controller, listeners, and runners to `talosw01`, `talosw02`, or `talosw03`.
Never relax this to a preference: control-plane nodes are untainted, and
`talosm02` must not receive Actions jobs.

The custom image `ghcr.io/thaynes43/actions-runner` is built by the public
haynes-ops workflow on GitHub-hosted runners. It extends the official ARC image
with the workflow prerequisites missing from that image: GitHub CLI, Python and
pipx, compiler tooling, PostgreSQL clients, compression and browser libraries.
Existing setup actions install project SDK versions. Changes to its Dockerfile
must increment its version and update every pool's image reference after the
published image can be pulled anonymously. Publication rejects an existing
version tag, and PR validation requires a version bump when the Dockerfile
changes. Runner manifests pin the published digest before activation. Do not
build it or run broad test
loops in dev-env; that pod shares talosm02 with critical services.

## First activation: owner credential setup

Create a dedicated App at <https://github.com/settings/apps/new>:

1. Name it `thaynes43-private-actions-runners`, owned by `thaynes43`. Use
   `https://github.com/actions/actions-runner-controller` as its homepage.
2. Disable the webhook's **Active** checkbox. Leave OAuth callbacks and webhook
   subscriptions unset.
3. Set repository **Administration: Read and write** and **Metadata: Read-only**.
   Do not grant organization runner, Contents, or Actions permissions. Select
   **Only on this account**.
4. Save the App, record its **App ID**, and generate a private key.
5. Install it with **Only select repositories**, selecting exactly the five
   private repositories in the table. Never select **All repositories**.
6. Record the installation ID from
   `https://github.com/settings/installations/<installation-id>`. This is distinct
   from the App ID and client ID.
7. In the **HaynesKube** 1Password vault, create an item named **`arc-github-app`**
   with these exact fields, all stored as strings:

   | Field | Value |
   | --- | --- |
   | `github_app_id` | App ID |
   | `github_app_installation_id` | Installation ID |
   | `github_app_private_key` | Entire downloaded PEM, including BEGIN/END lines and real newlines |

The ExternalSecret refreshes every five minutes and produces a Secret named
`arc-github-app` in `arc-runners`.
Credentials never belong in Git, workflow files, PRs, issues, or chat. The pod
cannot read Kubernetes Secrets or encrypt SOPS files. Do not request these values
from the owner; request only confirmation that the item exists.

Verify every new GHCR runner package can be pulled anonymously. If it is private,
set **Public** at
<https://github.com/users/thaynes43/packages/container/actions-runner/settings>.
A public repository does not by itself guarantee a package is public. Making
this image public reveals only the public Dockerfile and installed tools; it
contains no App credentials or private source.

The initial `0.1.0` publication is already public: anonymous tag, digest and
legacy-signature manifest requests returned 200 on 2026-10-07. Its pinned digest
is `sha256:2166736618b66c2496af112461d1a2ebfd6df050d6d6de3d2c89914b86939cb9`.
Hosted smoke and signature verification against the exact main build workflow
identity also passed. No owner visibility change is needed for this version.

## Verify before changing workflows

The owner completed the App and 1Password setup on October 7, 2026. Activation
PR [#3516](https://github.com/thaynes43/haynes-ops/pull/3516) removed the six setup
suspensions; the ExternalSecret synchronized and all five listeners became Ready.
For a fresh deployment, keep the auth and runner-set Kustomizations suspended
until the credential item and anonymously pullable, pinned image are ready.
Remove those suspensions through a reviewed haynes-ops PR and reconcile Flux.
The App ExternalSecret then acts as a health gate: pools wait for valid Secret
synchronization before registering. Do not remove suspension while owner setup
is incomplete, or weaken Flux alerts to accommodate an unconfigured pool.

Use read-only checks; never print Secret data or full environments:

```bash
kubectl get externalsecret -n arc-runners
flux get kustomizations -A
kubectl get deployments,pods -n arc-systems -o wide
kubectl get pods -n arc-runners -o wide
```

After credentials synchronize, reconcile the relevant Flux Kustomizations using
the names in their `ks.yaml` files. Confirm all five listeners exist in
`arc-systems`, and that each repository's **Settings → Actions → Runners** lists
its scale set. Zero idle ephemeral runner pods is expected with `minRunners: 0`.
Check each pool's actual first job before declaring its egress policy complete:
package feeds and CDN redirects can change.
The dev-env operator cannot read `actions.github.com` custom resources. Use
HelmRelease readiness, controller/listener logs, allowed pod metadata, and the
repository runner settings to verify registration without expanding RBAC.

Then open one PR per private repository, replacing each job's `runs-on` with its
scale-set name from the table. Preserve workflow permissions, environments,
concurrency, events, SDK versions, and full action SHA pins. Never put this label
in a public repository. GitHub's generated Dependabot workflow has no source
YAML and is outside this migration.

Start with `sigo-alumni`, then prove Docker builds, workspace bind mounts,
PostgreSQL services, and Chromium jobs in the other repositories. Their PR checks
must complete on ARC before squash merge. Read every advisory Claude review.
For a repository missing automated reviews, add the reviewer and `@claude`
workflows and provision its credential using `new-repo-setup.md`. Reviewers
must fire on opening, updating, and readying a PR; read their findings before
merging.

After merging, inspect a real default-branch or scheduled run. In particular,
`sigmaphiomicron-com` deploys every six hours and `haynes-swarm` audits weekly.
Do not mistake a successful infrastructure build for successful private jobs.
The pre-migration `sigmaphiomicron-com` quality run `37355769406` already failed
its external-links check on October 5; investigate failures against that baseline.

## Isolation and Docker details

Runner templates opt out of the global timezone webhook with
`k8tz.io/inject: "false"`. Its injected init container has no resource bounds
and would violate the runner namespace quota before a pod can start. Keep
this annotation when copying a pool; preserve the quota and each runner and
DinD container's requests and limits. Jobs use the runner image's UTC timezone.

Runner service accounts have no workload RBAC, and runner pods do not mount a
Kubernetes API token. Kubernetes container mode is deliberately unused: it needs
pod/exec/Secret permissions and does not satisfy existing arbitrary Docker CLI
steps. Controller/listener accounts receive the chart's scoped permissions.

`arc-runners` needs privileged Pod Security Admission because DinD needs a
privileged daemon. Kyverno retains baseline enforcement with an exception
limited to the fixed Docker image, generated runner names, namespace, runner
label, and the init-sidecar `privileged` field. The job's runner container has
`privileged: false`, starts as UID 1001, and drops `NET_RAW`. It permits setuid
`sudo` inside the container because existing Playwright jobs install apt
dependencies; setting `allowPrivilegeEscalation: false` or dropping all
capabilities would break those jobs. Keep the exception's image synchronized
with the daemon image.
`arc-systems` uses restricted admission.

Renovate holds the DinD image in this component because its exact image is also
matched by the privileged exception. Upgrade the daemon tag or digest and the
exception together in one PR, render the chart, verify admission, then merge and
reconcile. Never broaden the exception to a wildcard to accommodate an automatic
image update.

The DinD PodSpec is explicit. Do not switch it to `containerMode.type: dind`
without reviewing the chart: version 0.15.0 injects an unpinned `docker:dind`
sidecar without resources and can duplicate custom init containers. Runner and
daemon share `/home/runner/_work` at the same absolute path for Docker bind
mounts, `/var/run` for the Unix socket, and runner action runtimes. Docker storage
is ephemeral and bounded. Every container has requests and limits.
The shared `dind-externals` volume has a 1Gi bound: image `0.1.0` uses about
593Mi for those runtimes. Its copy init container requests 768Mi ephemeral
storage with a 1Gi limit. A smaller volume can evict the runner after a job
starts; inspect pod events when GitHub reports lost runner communication.

Namespace network policies deny inbound traffic and allow DNS plus required
external feeds. They prevent runner access to the cluster API and private
networks. The sigmaphi pool also needs public web access for link checking;
that exception remains confined to that pool. Add changing feed/CDN hosts through
Git and Flux. Docker Hub blob downloads use `production.cloudfront.docker.com`
alongside the existing Cloudflare host; keep the exact CDN names in the shared
DNS and HTTPS list and inspect pull errors for changed redirects. Verify feeds
from an actual job. Workflow downloads belong in the
runner pool's policy. The separate dev-env allowlist names the exact storage
accounts observed in GitHub's job-log responses; use its network policy as the
current list. Additional diagnostic destinations require a reviewed Git change. Do not
use proxies to evade a denied destination.

## Add a repository

1. Verify its current GitHub `isPrivate` flag, enabled workflows, actual activity,
   Docker/services needs, toolchain, and egress. Recheck privacy immediately before
   App installation and registration. Public conversion requires removing this
   pool and its workflow labels first.
2. Add only that repository to the existing App installation.
3. Add a repository-scoped scale-set app following its sibling, with
   `githubConfigUrl: https://github.com/thaynes43/<repo>`, a unique
   `runnerScaleSetName`, auth/controller dependencies, worker-only affinity,
   zero idle runners, and bounded resources. Use plain mode if Docker is absent.
4. Add only necessary network and DinD policy matches. Merge the haynes-ops PR,
   reconcile Flux, and verify the listener and GitHub registration.
5. Change the private workflows in their own PR, prove their checks on ARC,
   squash merge, and verify a real run. Update this table.

## Rotate the App key

Generate a new private key on the same App, replace the PEM field in 1Password,
and retain the old key until rotation is verified. App and installation IDs stay
unchanged. Wait for the ExternalSecret to report a new successful refresh;
never read the generated Secret. Confirm registration and a new job still work.
If ARC retains the old client, declare activity for `arc-systems,arc-runners`,
restart the controller deployment and delete its idle listener pods using the
allowed runtime operations. ARC recreates listeners; do not delete active runner
pods. End the activity declaration once they are healthy. Revoke the old key only
after the new listeners and a real job succeed.

If the App installation changes, update its installation ID in 1Password too.
A revoked or uninstalled App stops dispatch; investigate ExternalSecret status,
controller/listener errors, selected repositories, and App permissions without
printing private keys or access tokens.

## Scale and drain

Edit the pool's `minRunners`/`maxRunners` and resource budget in Git, pass checks,
squash merge, and reconcile its Kustomization. Start with one runner per pool;
increase only after reviewing worker capacity, quota, peak Docker/browser usage,
and downstream deployment concurrency. Preserve CPU, memory, storage limits and
the required worker affinity. Revert an increase through another PR.

To drain, set **both** `minRunners: 0` and `maxRunners: 0` in Git. ARC stops
creating new runners while current jobs finish; do not manually delete running
jobs. Restore the cap through Git to resume. Never stress-test the dev-env pod.

## Primary references

- [Deploy runner scale sets](https://docs.github.com/en/actions/how-tos/manage-runners/use-actions-runner-controller/deploy-runner-scale-sets)
- [ARC App permissions](https://docs.github.com/en/actions/how-tos/manage-runners/use-actions-runner-controller/authenticate-to-the-api)
- [Personal App registration](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app)
- [GitHub runner network destinations](https://docs.github.com/en/actions/reference/runners/self-hosted-runners#accessible-domains-by-function)
- [Docker Hub network destinations](https://docs.docker.com/desktop/enterprise/allow-list/)
- [Pinned ARC chart source](https://github.com/actions/actions-runner-controller/tree/gha-runner-scale-set-0.15.0/charts)
- [Repository runner registration API](https://docs.github.com/en/rest/actions/self-hosted-runners#create-a-registration-token-for-a-repository)
