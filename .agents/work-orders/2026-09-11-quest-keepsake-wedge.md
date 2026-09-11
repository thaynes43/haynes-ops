# Haynes Quest keepsake wedge catalog correction

- **Status:** Corrected catalog reference deployed; focused live checks passed
- **Owner:** Codex operations lane
- **Application PR:** `thaynes43/haynes-quest#25`
- **Operations PR:** `thaynes43/haynes-ops#2855`
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

## Rollout evidence

- Operations PR #2855 passed the Diff Scope gate and all eight Flux Local filter,
  test, diff, and success checks at exact head
  `c741aa0a9f5f12462df14478260343ac9d6eebf9`, then squash-merged as
  `1bd7b6884e483dbcf75c0dda3c0f8ac2557d88d3`.
- Scoped activity `act-121411-54354` covered only
  `frontend,haynes-quest`. A targeted Flux source and Kustomization reconcile
  applied the exact merge revision. The Kustomization and HelmRelease reported
  Ready, and pod `haynes-quest-7d9b698574-nds6g` was Ready with zero restarts on
  exact digest
  `sha256:743bca475c2e19d2534ecd3be7f952e52d0d5adb5724599f1285218f91e87e37`.
- Internal HTTPS `/healthz` and `/readyz` returned `ok` and `ready`. The live
  review page contained the corrected
  `memory-keepsake/concept-v002/concept.png` reference and retained the
  `memory-keepsake/v001/memory-keepsake.glb` reference.
- The live PNG returned `image/png`, 1,798,690 bytes, and SHA-256
  `35ac17215c2c359142519b806e68e502aac2ad0580269e3a1e0d610f38a0c20c`.
  The live GLB returned `model/gltf-binary`, 116,304 bytes, and its unchanged
  SHA-256
  `059d92ca7bad6d1397e5fab9677a0ef0730fb45d54a4770b22675988a55fa137`.
- A focused Playwright load of the live review page found the corrected image
  complete at its expected URL with natural dimensions 1536 by 1024 and rendered
  dimensions 334 by 223. The page reported zero console errors, and Playwright
  captured `quest-keepsake-wedge-live.png` from the loaded image element.
- The activity declaration was ended immediately after these checks. The rollout
  created no database records and did not delete or manually restart a pod.

Do not record Secret values, personal fields, private media, or credentialed
Immich response data in this work order.
