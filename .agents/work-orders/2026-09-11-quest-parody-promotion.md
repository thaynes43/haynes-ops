# Haynes Quest isolated private playtest release

- Status: application final-head CI/merge/publication in progress; no review deployment is live yet.
- Operations worktree: `/home/dev/work/quest-parody-promotion`, branch `agent/quest-parody-promotion`, base `d2acee8`.
- Application: `thaynes43/haynes-quest#28`; final runtime `ff3ad87`, exact main image pending checked squash merge.
- Scope: three new resources for `haynes-quest-playtest` plus entries in the existing app Kustomization. Normal demo resources and image remain unchanged. No dev-env, OAuth, new Secret, public ingress or Immich access.

## Authorized release boundary

Tom requested end-to-end MVP delivery, checked PR merges and a Fable5.1 agentic playtest before deeper inspection. Application WO044 ratifies a separate LAN-only candidate review at `https://haynes-quest-playtest.haynesops.com` under DESIGN007's explicit allowance: “Candidates may be shown in an isolated, clearly labeled review preview.” This makes the game reviewable before Tom chooses exact final artwork. It does not record owner approval or promote those candidates into the normal private demo.

The application labels its two-chapter fictional playtest and pending artwork review. The exact15 GLBs remain candidates: two traveler stages, five environment/keepsake pieces, four equipment props and four completed parody models. No audio mapped. New v2 plans select six encounters using those four models; old v1 data is retained, but incomplete Nap/missing Diva art does not become graphically playable through data compatibility. Real family photos, admitted login, expanded parent curation, Besties and lifetime content remain open.

## Verified baseline and isolation

Normal URL `https://haynes-quest.haynesops.com` runs pod `haynes-quest-7d9b698574-nds6g`, image `sha-3502ac7120f6d7741a1a209415f4c9eeb33f826b@sha256:743bca475c2e19d2534ecd3be7f952e52d0d5adb5724599f1285218f91e87e37`. Preserve that deployment.

Review uses its own HelmRelease/Deployment/Service/selectors, internal Traefik route and Cilium policy inside existing `frontend/haynes-quest`, sourced from `flux-system/haynes-ops` at `./kubernetes/main/apps/frontend/haynes-quest/app`. The LAN DNS route targets `internal.haynesops`; internal Traefik address192.168.40.203 and existing wildcard TLS cover the new host. Cloudflare DNS excludes haynesops.com. Ingress only from internal Traefik; egress only PostgreSQL5432 and bounded kube-dns lookup. Fixture mode remains true and origin matches the new hostname.

Only existing `haynes-quest-secret` supplies session/database settings. No Immich or database-init Secret. Cookies have no Domain, giving normal host-only browser isolation and a fresh random fixture owner on the review host; every record operation checks ownership. Shared secret/database is not cryptographic tenant separation if a cookie is manually copied across hosts.

The new app applies additive0003 to the shared database. Advisory lock730204004, checksums and per-file transactions serialize migration. New nullable/defaulted columns preserve old reads/inserts; cleanup workers use SKIP LOCKED. The migration persists after removing the review and may briefly wait on save traffic. This bounded MVP change is authorized; include both Quest names/frontend in the activity declaration.

Dev-env baseline: pod`dev-env-dfdd8c894-l724p`, UID`c3a94756-af35-405e-93bc-eb05c2979d3a`; all three container restarts0. Do not touch its resources or restart it.

## Application proof and image selection

Both original full keyboard/touch routes passed on Dcn: gear, combat/guard, bosses, two then one decoded fictional pictures, age0→4→7, deliberate hazard/fall recovery, gaps/runway/ferry and save/resume. Fable ran27 exploratory cases;26 passed, one exposed held-contact menus. Rootff3ad87 corrected implicit capture and fresh-input deduplication. WO043 on exact BPe client verified Help/album/sound/Save while holding the stick, then keyboard10.9ms later and fresh primary touch/mouse; zero errors and one saved journey. WO046 closes final functional acceptance: keyboard exit0; the same touch save completed both chapters across a browser restart, with interrupted harness checks documented. A fresh touch probe reopened the completed save twice, decoded all3 pictures and retained age7/two chapters, exit0 with no page/request errors. Session-failure/retry, desktop menus and rapid attacks passed3/3. The final source removes invalid post-release position/checkpoint assertions while retaining actual ferry landing proof. Physical Safari/children remain untested.

Local typecheck/lint/build/strict docs and227 tests pass; CI passes236 including9 dedicated PostgreSQL cases. Four-model catalog audit passed4pages/20clips/28MP4s/20stills/8posters/4touch orbits/4masters with zero failures. Exact artwork list is application`docs/assets/media/playtest/v001/artwork.json`.

Require final application PR verify, container-check and Documentation build before squash merge. Then require the actual main SHA's verify/image/Documentation workflows: Buildx SBOM/provenance, GitHub provenance attestation and cosign signing. Read its tag anonymously from GHCR using a pull token kept out of output and Accept for OCI index/manifest plus Docker list/manifest; require200 and record Docker-Content-Digest.

**The scaffold currently contains the old baseline image as an explicit placeholder. Root must replace it with the actual published candidate squash SHA and exact digest before opening this operations PR.** Never deploy the scaffold or guess a digest.

## Operations checks and rollout

Kustomize rendering and assertions passed for three distinct new resources plus preserved existing resources. The installed local flux-local fails on the repository's existing cross-namespace chartRefs for unrelated external-secrets/reloader, and this pod has no Docker runtime for the CI8.4.0 container; this is not a passing full local Flux validation. Require all nine CI jobs: Diff Scope; Flux Local Filter; main/edge Tests; four resource Diffs; Success. Required contexts are Diff Scope - Success and Flux Local - Success. Inspect rendered diff for only the new review workload/service, route and policy; original workload/selectors must be unchanged.

Independent gh attestation verify against an older image failed DNS for tmaproduction.blob.core.windows.net, outside the egress allowlist. No workaround or independent cryptographic/admission-enforcement claim. Successful publish/attest/sign CI plus exact anonymous manifest retrieval are the established image evidence.

After checks, declare `frontend,haynes-quest,haynes-quest-playtest`, squash-merge and reconcile only `frontend/haynes-quest` with source. Verify Git/Helm readiness, exact running image, DNS/TLS/health, game and catalog media, actual newly created v2 journey and save/resume, stable normal app and unchanged dev-env UID/restarts. End activity promptly. Record final evidence and provide the working private review link with an included/open guide.

## Independent final source/render review

Native Sol reviewed integrated scaffold5aa456f with cached app-template5.1.0. `helm template` for normal and review releases renders separate ServiceAccount/Service/Deployment kind-name sets with zero intersection; neither renders a PDB. Deployment/pod/Service selectors use distinct name and instance labels; CNP selects the review only. Migration0001/0002 checksums match old3502;0003 defaults make every old row/insert valid legacy-v1, while old explicit-field reads and migration enumeration tolerate new columns/ledger entry. The real Postgres pre-0003-row test covers migration into legacy/null/null. Candidate labels and exact-origin/host-only-cookie handling were independently confirmed. No source or resource-collision blocker found; final pin, CI and live proof remain required.
