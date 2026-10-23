# Story 182 — Audiobookshelf 2.37.1 → 2.37.2 source review

Reviewed 2026-10-23 around 20:32–20:34 UTC, read-only child review. Parent remains sole runtime executor; existing v1 recovery gate and all other holds remain unchanged. No repo/cluster changes, credentials, library content, playback, or private application data accessed. No tests needed/run for this source review.

## Result

No required configuration adaptation or new schema migration found. The immutable upstream range contains 16 commits and 15 changed files; no migration scripts, Dockerfile, authentication protocol/token-format code, port/default mount configuration, or dependency versions change. The package/lockfile changes only bump the application version. This is a suitable routine patch candidate after the parent's recovery gate and normal backup/rollout checks, based on source review rather than a runtime acceptance claim. [Immutable compare](https://github.com/advplyr/audiobookshelf/compare/3563d49424d50170324de9236a24c591b6d965e9...edb1f7efc92ecacdef51aafb4d9d5054ce10604f), [release](https://github.com/advplyr/audiobookshelf/releases/tag/v2.37.2).

## Changes relevant here

- The public Docker image rebuild moves Node.js 24.21.0 → 24.22.1. Upstream describes 24.22.1 as the October security release; no application CVE/advisory is identified in this patch's notes. Actual amd64 image config confirms both Node versions and OCI revision/version labels matching the source commits below. [New immutable manifest](https://ghcr.io/v2/advplyr/audiobookshelf/manifests/sha256:37c2057e150694970442579815c646fe6bdc01c95b9d1547c4c9068be8844ef3), [previous manifest](https://ghcr.io/v2/advplyr/audiobookshelf/manifests/sha256:10f3490855630aa024ba618b90ba6f58f3d9d7063dd77e1d1e958fdc4ede7c70).
- Root creation now checks for an existing root inside a database transaction. Initialization refuses a second root with HTTP 500 instead of returning success. This is initialization hardening, with no changes to existing user rows, token formats, or login flow in this range. The release's authentication warning concerns v2.26.0 and is already behind 2.37.1. [Root-init commit](https://github.com/advplyr/audiobookshelf/commit/34715558dfb01c0385e967c8209c36e12c82d97b).
- ROUTER_BASE_PATH gains whitespace/slash normalization. Current HR has no ROUTER_BASE_PATH and ingress serves `/`, so the empty-root case stays applicable; no ingress adjustment indicated. [Normalization commit](https://github.com/advplyr/audiobookshelf/commit/743d9152208507a1dbf7c95c94290071338f44bd).
- Async file copying retries EPERM using bounded streams; moving falls back on EPERM as well as EXDEV. Relevant to filesystem/backup operations, but it grants no write access to the existing read-only library mount. The deployment keeps writable `/config` and `/metadata` subpaths on the same backed-up PVC. No mount adaptation indicated. [Copy fallback](https://github.com/advplyr/audiobookshelf/commit/16aa80ed5c0ba3f51c8889c203c2c5668dc4aafd), [move fallback](https://github.com/advplyr/audiobookshelf/commit/5cb461f9b54b92516406f479b96f616b42f0d0e4).
- Remaining fixes: RSS image URLs add cache-busting timestamps; Audible searches return an empty result when products are null; upload handling flattens repeated multipart file fields. Debian service executable-bit packaging and devcontainer Node changes do not affect this Kubernetes deployment. No normal client API breaking change identified. [RSS](https://github.com/advplyr/audiobookshelf/commit/6b60beff6e471ca27f3d1a8f5f6791360545db3a), [Audible](https://github.com/advplyr/audiobookshelf/commit/ac63b0c7fc6fb34c42ec18d02bc55652f6f1dbfe), [uploads](https://github.com/advplyr/audiobookshelf/commit/3f9afe57bcc73bb253e414e1e789d01718bd5f27).

## Public publication proof

Anonymous GHCR registry reads returned HTTP 200 for both tags. Docker-Content-Digest matched a locally computed SHA-256 of each returned index; no image layers were downloaded.

| Tag | OCI index digest | linux/amd64 manifest |
| --- | --- | --- |
| 2.37.1 | sha256:581d68b2a6fc7ebf58d81c878a9f387cbbc0d88ac9d37b298b9cee10168af85b | sha256:10f3490855630aa024ba618b90ba6f58f3d9d7063dd77e1d1e958fdc4ede7c70 |
| 2.37.2 | sha256:b897c33f05972fb1bf484f7487bc878fa91ec8691335c1db6e2929c3a7658efa | sha256:37c2057e150694970442579815c646fe6bdc01c95b9d1547c4c9068be8844ef3 |

New index also includes linux/arm64 `sha256:8c7e6c54460a6db508b520ff70e7025a5c0285f6ecc1574b6ba484dedc989f83`. New amd64 config: built 2026-10-23T19:39:46.547383449Z; revision `edb1f7efc92ecacdef51aafb4d9d5054ce10604f`; version `2.37.2`; NODE_VERSION=24.22.1. [Immutable new index](https://ghcr.io/v2/advplyr/audiobookshelf/manifests/sha256:b897c33f05972fb1bf484f7487bc878fa91ec8691335c1db6e2929c3a7658efa).

## Migration, backup, rollback and acceptance guidance

No new schema migration is present, but the unchanged migration manager updates its version metadata even when it logs “No migrations to run.” Its temporary database-copy backup occurs only when actual migrations run; do not assume this patch startup creates a rollback backup. Confirm the existing recent successful backup/snapshot for the single PVC containing config plus metadata before rollout. ABS's built-in backup uses SQLite backup plus item/author metadata, whereas the full PVC backup also preserves the remaining configuration/metadata. This review did not inspect backup freshness. [Immutable migration manager](https://github.com/advplyr/audiobookshelf/blob/edb1f7efc92ecacdef51aafb4d9d5054ce10604f/server/managers/MigrationManager.js#L72), [backup manager](https://github.com/advplyr/audiobookshelf/blob/edb1f7efc92ecacdef51aafb4d9d5054ce10604f/server/managers/BackupManager.js#L331).

Source suggests a GitOps re-pin to 2.37.1 remains feasible: no new migration scripts and the existing manager handles version down direction. This is not a tested downgrade guarantee. The HR already configures Helm upgrade rollback; that rolls back workload configuration and image, not PVC data. If a failure damages state, use the recorded pre-upgrade backup through the established restore runbook rather than assuming Helm restored it. Keep the library mount read-only.

Parent's acceptance can use the configured `/healthcheck` on 13378, Ready pod/HR/KS, expected runtime digest/version, and bounded startup logs for DB initialization/migration/permission errors. No need to trigger scans, uploads, downloads, or playback for this patch review. Any application-state or restore action beyond existing authorization remains with the parent.


## Exact PR head receipt — 2026-10-23 20:40 UTC

Public PR #4256 was open with head `3b42db966e2361f2e6bb0f3b84ecdee018f9ef14` and base `164b87edc36a306dc0ce3484460dc9f61db85e16`. Public PR file metadata lists exactly one changed file and a one-line image tag bump, `2.37.1` → `2.37.2`. Independently fetched immutable raw files at both SHAs and verified byte-for-byte equality after that single replacement. [PR](https://github.com/thaynes43/haynes-ops/pull/4256), [immutable comparison](https://github.com/thaynes43/haynes-ops/compare/164b87edc36a306dc0ce3484460dc9f61db85e16...3b42db966e2361f2e6bb0f3b84ecdee018f9ef14), [exact head file](https://github.com/thaynes43/haynes-ops/blob/3b42db966e2361f2e6bb0f3b84ecdee018f9ef14/kubernetes/main/apps/media/audiobookshelf/app/helmrelease.yaml).

No concrete source/schema/API compatibility issue found for this exact head. It selects the already-reviewed, publicly published 2.37.2 release and preserves PORT 13378, `/healthcheck` probes, UID/GID/fsGroup 1000, dropped capabilities, config/metadata PVC subpaths, read-only NFS library mount, and Helm upgrade rollback policy. Prior publication/source digest proof remains applicable; the manifest uses a mutable version tag rather than a digest pin, so the parent should match the resulting runtime digest to the recorded public proof.

Parent-reported backup gate receipt (not independently queried by this review): existing full 20Gi PVC backup source `audiobookshelf-aws`, last success 2026-10-23 04:01:19Z, duration 1m19.9s; the driver explicitly accepted the normal daily backup gate with approximately 16h35 age at the earlier gate, crash-consistent and not restore-tested. This is not evidence of a fresh application-quiesced backup. Parent-reported PVC UID remains `28bed580` (prefix as provided); no backup trigger or content operation performed. The parent also reports v1/ops recovery and current 2.37.1 healthcheck HTTP 200; this review makes no independent runtime acceptance claim.

Review complete. No source/runtime mutation, held-item work, policy/custody/credential reads, tests, scans, downloads, uploads, playback, workers, or jobs performed. Original Story 182 budget and 21:02Z stop boundary remain unchanged; parent owns required checks, merge decision, rollout and acceptance.
