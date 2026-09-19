# Quest app-wide zoom lock release

Promote the owner-corrected policy: browser zoom gestures are blocked throughout the game app, including start/setup and menus. [Application PR50](https://github.com/thaynes43/haynes-quest/pull/50) merged as `2ac16b9cc4840dea19411318ac7615ccf7fd0070`; its 568 CI tests and required checks passed. Main Application run `35476977957` and Documentation run `35476977977` passed. Image publication, provenance and signing succeeded; anonymous registry retrieval verifies the OCI index digest and GitHub records an attestation. This is publication/provenance presence, not independent cryptographic verification.

Image: `ghcr.io/thaynes43/haynes-quest:sha-2ac16b9cc4840dea19411318ac7615ccf7fd0070@sha256:fe0c1c06e52cbb7d3e711999d57a529014111cb0e7b9632d96f3e4473dc0aa68`.

The rendered runtime change is only the private playtest image. The repository workflow snippet is also corrected to require branch/PR/checks/squash merge instead of its stale direct-main command. Normal Quest and dev-env remain unchanged.

Acceptance requires the fixed viewport HTML plus JavaScript `/assets/index-BbxFx3Yj.js` (1,193,622 bytes, SHA256 `66d829432a86b1d40bc9ac56717011a36d19e84026dc3c2562f63ce4958423a2`) and CSS `/assets/index-CFAjhIJT.css` (41,949 bytes, SHA256 `7a844ac3d16e8641b9ef068fa2f2da08fec20bfe5023d1bd20f935084b05cf9d`). Root owns scoped GitOps rollout and exact hosted validation. Final browser evidence and cleanup live in [WO090](https://github.com/thaynes43/haynes-quest/blob/main/.agents/work-orders/090-app-wide-zoom-lock.md) and its linked release index. Chromium touch emulation remains distinct from physical Safari; browser/system zoom overrides are outside webpage control.
