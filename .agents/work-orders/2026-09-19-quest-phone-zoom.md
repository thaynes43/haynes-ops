# Haynes Quest phone viewport zoom release

Release preparation for the private synthetic playtest. The application diagnosis, native-contact browser evidence and final hosted results are owned by [Haynes Quest WO089](https://github.com/thaynes43/haynes-quest/blob/main/.agents/work-orders/089-phone-viewport-zoom.md).

Application [PR48](https://github.com/thaynes43/haynes-quest/pull/48) merged as `9a01cbd034b50031638c81bc3e899089cd5eed1b`. Main [Application run 35475207317](https://github.com/thaynes43/haynes-quest/actions/runs/35475207317) and [Documentation run 35475207346](https://github.com/thaynes43/haynes-quest/actions/runs/35475207346) passed at that exact commit. The image build/push, Buildx provenance and SBOM, GitHub provenance attestation, cosign installation, signing and image-record steps succeeded.

Anonymous GHCR retrieval returned HTTP 200 for the exact tag. Its 857-byte OCI index hashes to the same value as `Docker-Content-Digest`, `sha256:3d5b0fd20b05f26e8f055ae6d180c86d4d432e7c240b6f01de9f90bba66bd678`. The GitHub attestations API records one attestation for that subject. This establishes publication and provenance presence; it is not an independent cryptographic verification.

The checked image is `ghcr.io/thaynes43/haynes-quest:sha-9a01cbd034b50031638c81bc3e899089cd5eed1b@sha256:3d5b0fd20b05f26e8f055ae6d180c86d4d432e7c240b6f01de9f90bba66bd678`. This operations change updates only the private playtest image and its publication comment, plus this record. Normal Quest, dev-env, routes, Services, policies, Secrets, database resources and the fixture's ephemeral boundary remain unchanged.

Root owns the checked merge, scoped GitOps rollout and final hosted verification. The final gate must match JavaScript `/assets/index-C4SUeEy7.js` (1,193,022 bytes, SHA256 `c1ebea0d164c81561b58495dc2a992149bcb9ff1ccc34d651408b3cf319de81c`) and CSS `/assets/index-BkwX-UiJ.css` (41,908 bytes, SHA256 `55c0f3cd2eaec7ac3625bd9c6356ae3fd5007dd265582f3ff5d248f8678eab12`), then record the final native-contact viewport result in WO089. Browser emulation remains distinct from physical Safari acceptance.
