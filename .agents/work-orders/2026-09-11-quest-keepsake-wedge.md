# Haynes Quest keepsake wedge catalog correction

- **Status:** Immutable application image verified; GitOps rollout prepared
- **Owner:** Codex operations lane
- **Application PR:** `thaynes43/haynes-quest#25`
- **Operations branch:** `agent/quest-wedge-catalog-publish`

## Objective

Publish the corrected concept v002 for the existing memory-keepsake review page.
The correction restores the rear support wedge in the concept sheet's back view;
the already-correct v001 GLB remains unchanged.

## Scope and safety boundary

- Change only the Haynes Quest application image pin and this evidence record.
- Keep the existing Deployment, Service, IngressRoute, NetworkPolicy, Secret
  reference, probes, and fixture-mode configuration unchanged.
- Do not create or modify database records, delete application pods, or change
  dev-env, OAuth, Secret, or egress configuration.
- Verify the focused review page and its two affected downloads after Flux rolls
  out the checked digest. A full catalog or gameplay journey is outside this
  image-only correction.

## Immutable release evidence

- Application PR #25 passed its Documentation build, Application verification,
  and container check at exact head
  `c4edfb48f5699cce44249572fa450b140bf0d4b4`, then squash-merged as
  `3502ac7120f6d7741a1a209415f4c9eeb33f826b`.
- Main Application workflow run `34597236032` completed successfully. Its
  verification and image jobs passed, including provenance publication and the
  keyless image-signing step.
- The exact tag
  `sha-3502ac7120f6d7741a1a209415f4c9eeb33f826b` resolves anonymously to OCI index
  digest
  `sha256:743bca475c2e19d2534ecd3be7f952e52d0d5adb5724599f1285218f91e87e37`.
  Its Linux amd64 manifest, config blob, and first layer were also anonymously
  retrievable.
- The source correction PNG is 1,798,690 bytes with SHA-256
  `35ac17215c2c359142519b806e68e502aac2ad0580269e3a1e0d610f38a0c20c`.
  The unchanged v001 GLB is 116,304 bytes with SHA-256
  `059d92ca7bad6d1397e5fab9677a0ef0730fb45d54a4770b22675988a55fa137`.

## Rollout gate

Before merge, require the operations PR checks to pass and review the exact tag
and digest above. During rollout, declare activity scoped to
`frontend,haynes-quest`, then verify Flux and Helm readiness, one Ready pod at
the exact digest, internal HTTPS health, the corrected concept-v002 reference
and download checksum, the unchanged GLB checksum, and a focused browser load
without page errors. End the activity immediately after verification.

Do not record Secret values, personal fields, private media, or credentialed
Immich response data in this work order.
