# Open WebUI — models, RBAC, and ComfyUI image generation (PLAN-021 ops wave)

> Applied 2026-07-10 as the PLAN-021 ops wave (parts b/c/f). This is a **record + disaster-recovery
> runbook**. The live configuration lives in the Open WebUI database (the `open-webui` PVC) and the
> Ollama model mount (`gasha01.haynesnetwork:/hdd-nfs-repl/misc/ollama/models`), **not** in this repo.
> If the Open WebUI PVC is ever restored empty, re-apply the RBAC + image config with the admin-API
> steps below. Ollama models survive independently on the NFS mount.
>
> All admin-API calls need `OPENWEBUI_API_KEY` (1Password `openwebui` → cluster secret
> `openwebui-secret`) and a browser-like `User-Agent` header (Cloudflare blocks `python-urllib`).

## (b) Ollama model starter set — mount `misc/ollama/models` on gasha01

The mount was **already populated** (~291 GB of models, contrary to the PLAN-021 recon note that said
it was empty post-migration). Filesystem `hdd-nfs-repl` had **118 TB free** at apply time, so the
~150 GB budget was never a constraint (the new books libraries share the same NFS but there is ample
room).

Starter set (owner ruling: general chat + fast small tool model + embeddings):

| Tier | Model | Size | Notes |
|------|-------|------|-------|
| General chat (large) | `llama3.3:latest` (70B Q4_K_M) | 42 GB | Already present. Current best-in-class open 70B for chat (MT-Bench 9.35). Gated → `family`. |
| Small tool-capable | `llama3.1:8b` | 4.9 GB | **Pulled this wave.** Native tool-calling (Llama 3.1 tool template). Chat verified. Public. |
| Embeddings | `nomic-embed-text:latest` | 274 MB | **Pulled this wave.** 768-dim. Used in RAG/embedding settings (not the chat picker). |

### ollama-prime cold-load fix (committed to helmrelease this wave)
Cold-loading a 70B (42 GB) model from the **HDD-backed** NFS exceeded ollama's default 5-minute
`OLLAMA_LOAD_TIMEOUT` → first-load returned HTTP 500 ("timed out waiting for llama-server to start").
Fix in `ollama/prime/app/helmrelease.yaml`: `OLLAMA_LOAD_TIMEOUT=15m` + memory limit `32Gi → 48Gi`
(the 70B puts ~35 layers on the 3090 and ~45 layers ≈ 25 GB on CPU). Cold first-load still takes
several minutes on HDD-NFS; with `OLLAMA_KEEP_ALIVE=5m` and the shared single GPU, expect a cold-start
delay on the large tier until the 2nd 3090 is repaired (GPU repair deferred, PLAN-021 part a).

Pull command (runs on the GPU node, writes to the NFS mount):

```
kubectl exec -n ai deploy/ollama-prime -c app -- ollama pull llama3.1:8b
kubectl exec -n ai deploy/ollama-prime -c app -- ollama pull nomic-embed-text
```

The mount also carries a large pre-existing library (llama4, r1-1776, gemma3 variants, phi4, gpt-oss,
dolphin*, deepseek-r1, huihui abliterated set, etc.) — see the RBAC table below for tiering.

## (c) Model RBAC — Open WebUI groups + per-model access control

Owner ruling: **small models → all logged-in users (Default+); large models → Family + Admin only.**

- Group **`family`** (Open WebUI group id recorded in the DB) = the trusted tier. Members = the
  haynesnetwork **Family + Admin** roles. Admins are members; admins also bypass access control
  natively. **Membership is currently maintained manually** (see automation note).
- **Large tier (≥ 27B params) → gated** to `family` via each model's `access_control.read.group_ids`.
  `llama3.3:latest`, `llama4:latest`, `r1-1776:latest`, `gemma3:27b`,
  `huihui_ai/gemma3-abliterated:27b`.
- **Small tier (< 27B) → public** (`access_control: null`): `llama3.1:8b`, `deepseek-r1`,
  `dolphin-llama3`, `dolphin3`, `gpt-oss`, `llama2-uncensored`, `llama3.2`, `phi4`, `phi4-reasoning`.

### Behaviour note (important)
In this Open WebUI version (0.7.2), a base Ollama model is only visible to **non-admin** users if it
has a **base-model DB entry that is public**. A model with no entry is hidden from regular users
(admins still see everything). Consequences:
- New small models must be given a **public base entry** to reach all users. `llama3.1:8b` was given
  one this wave.
- `nomic-embed-text` was intentionally left entry-less → hidden from the chat picker (correct: it is
  an embedder selected in Admin → Settings → Documents, not a chat model).
- `huihui_ai/gemma3-abliterated:12b` and `:latest` are entry-less → currently hidden from regular
  users. **Owner decision needed:** give them public entries (all-users) or leave hidden. Left as-is
  (uncensored community finetunes — content-sensitivity call).

### Re-apply (DR) — gate a large model
```
# GET the base entry, then POST it back with access_control set to the family group:
# access_control = {"read":{"group_ids":["<family-gid>"],"user_ids":[]},
#                   "write":{"group_ids":[],"user_ids":[]}}
POST /api/v1/models/model/update   (body = full ModelForm incl. access_control)
# For a model with no entry yet: POST /api/v1/models/create with the same body.
# Public small model: POST /api/v1/models/create with access_control: null.
```
Group + membership endpoints:
```
POST /api/v1/groups/create                      {"name":"family","description":"..."}
POST /api/v1/groups/id/{gid}/users/add          {"user_ids":["<uid>", ...]}
POST /api/v1/groups/id/{gid}/users              (list members)
```

### App-role → Open WebUI-group mapping
| haynesnetwork role | Open WebUI | Large models / image-gen |
|--------------------|-----------|--------------------------|
| Admin | OWUI `admin` (native) + `family` member | Yes (admins bypass AC too) |
| Family | OWUI `user` + **`family` group member** | Yes |
| Default | OWUI `user`, no group | Small models + image-gen only |

**Membership sync is a manual/owner step today.** When a Family-role user first logs in (OAuth
auto-provisions them as an OWUI `user`), an admin must add them to the `family` group
(Admin → Users → group, or `POST /api/v1/groups/id/{gid}/users/add`).

### Future automation — OIDC group claim (investigated; NOT enabled)
Open WebUI can drive group membership from the OIDC token via
`ENABLE_OAUTH_GROUP_MANAGEMENT=true` + `OAUTH_GROUPS_CLAIM=groups` (optionally
`ENABLE_OAUTH_GROUP_CREATION`). On each login OWUI would set the user's groups to match the claim,
auto-adding/removing them from an OWUI group whose **name matches an Authentik group** (e.g.
`family`). This needs an **Authentik change** (add a groups scope/claim mapping to the `open-webui`
OAuth provider + a `family` Authentik group) — **out of scope for this wave** (no Authentik changes).
⚠️ Do **not** enable `ENABLE_OAUTH_GROUP_MANAGEMENT` before the Authentik claim exists: with no claim
present OWUI sets groups to empty on login and would **wipe** the manual `family` memberships.

## (f) ComfyUI image generation — all users

Owner ruling: **image generation available to all users incl. Default** (no gating; the reused
lightweight AppDaemon Qwen text→image workflow). Default user permission
`features.image_generation` is already `true`, so all users can generate.

Config (Open WebUI Admin → Settings → Images, or `POST /api/v1/images/config/update`):
- `ENABLE_IMAGE_GENERATION=true`
- `IMAGE_GENERATION_ENGINE=comfyui`
- `COMFYUI_BASE_URL=http://comfyui.ai.svc.cluster.local:8188`
- `IMAGE_SIZE=1024x1024`, `IMAGE_STEPS=50`
- `COMFYUI_WORKFLOW` = the **reused** text→image workflow
  `apps/ai/stable-diffusion/comfyui/resources/api-workflows/image_qwen_Image_2512_API.json`
  (Qwen-Image-2512 fp8 + Lightning-4step LoRA switch, base models already provisioned into ComfyUI).
  **The workflow file is reused read-only — not modified.**
- `COMFYUI_WORKFLOW_NODES` (Open WebUI field → Qwen node id):

| OWUI field | key | node id | Qwen node (class) |
|------------|-----|---------|-------------------|
| prompt | text | `197:180` | CLIPTextEncode (positive) |
| negative_prompt | text | `197:195` | CLIPTextEncode (negative) |
| width | width | `197:179` | EmptySD3LatentImage |
| height | height | `197:179` | EmptySD3LatentImage |
| steps | steps | `197:194` | KSampler |
| seed | seed | `197:194` | KSampler (seed key is required — no default) |

(The AppDaemon `comfyui_image_generation_provider.py` patches a *different*, image-EDIT workflow
`02_qwen_Image_edit_*` — its node ids `115:111`/`78`/`60` do **not** apply here. The node ids above
were read directly from `image_qwen_Image_2512_API.json`.)

Single-GPU note: the second 3090 is detached (GPU repair deferred, PLAN-021 part a). ComfyUI and
Ollama share the one RTX 3090 on `talosw01`, so a large chat model resident in VRAM contends with
image generation. No queue was added (owner ruling) — usage metrics will show if gating/GPU is needed.

## Verification (2026-07-10)
- **Models:** `ollama list` on ollama-prime shows the starter set. `llama3.1:8b` answered a chat
  prompt (explained Kubernetes) via the OWUI Ollama proxy; `nomic-embed-text` returned a 768-dim
  vector. `llama3.3:70b` is served + listed + gated; its cold first-load needs the timeout/memory fix
  above and enough GPU headroom — on the shared single 3090 it competes with ComfyUI for VRAM (only
  ~16 of 80 layers fit on the GPU while ComfyUI holds it, forcing ~64 layers onto CPU from HDD-NFS,
  which is very slow). Loads cleanly (~35 layers on GPU) when ComfyUI's VRAM is free. This shared-GPU
  cold-start latency is the concrete motivation for the deferred GPU repair (PLAN-021 part a).
- **RBAC:** a throwaway non-admin OWUI user saw **8 public small models and 0 gated large models**;
  after being added to `family` it saw **all 5 gated large models**. Test user deleted afterward.
- **Image-gen:** an OWUI `/api/v1/images/generations` call ("a red ceramic coffee mug on a wooden
  table…") reached ComfyUI as a `qwen-t2i` job with the prompt correctly patched into node `197:180`;
  ComfyUI produced a valid **1024x1024 PNG** matching the prompt (`Qwen-Image-2512_00004_.png`).
  NOTE: on the shared single GPU the 50-step generation took ~477s (plus queue wait), which is longer
  than the OWUI HTTP client's ~600s budget through the edge proxy — so the *image is produced* but the
  synchronous OWUI response can time out. The workflow's built-in **Lightning-4-step LoRA** (switch
  node `197:196`, currently off) would cut this to ~40s; enabling it is a future workflow tweak
  (matches the owner's "switch to a higher-quality/faster workflow" TODO).

## Owner TODOs
1. Decide `family` membership: which haynesnetwork **Family**-role OWUI users to add (Admins are in;
   the two existing user-role accounts are NOT — classify them). Add via Admin → Users or the
   `/users/add` group endpoint.
2. (Optional, future) Enable OIDC-driven group sync — needs an **Authentik** change (groups claim on
   the `open-webui` provider + a `family` Authentik group) + `ENABLE_OAUTH_GROUP_MANAGEMENT`/
   `OAUTH_GROUPS_CLAIM` on OWUI. Do not enable OWUI side before the Authentik claim exists.
3. Decide whether the uncensored `huihui_ai/gemma3-abliterated:12b`/`:latest` (currently hidden from
   regular users, no public entry) should be all-users, gated, or removed.
