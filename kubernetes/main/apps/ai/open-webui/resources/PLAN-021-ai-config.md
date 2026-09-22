# Open WebUI — models, RBAC, and ComfyUI image generation (PLAN-021 ops wave)

> Applied 2026-07-10 as the PLAN-021 ops wave (parts b/c/f); section (f) reworked 2026-09-21. This is
> a **record + disaster-recovery runbook**. Model RBAC still lives only in the Open WebUI database
> (the `open-webui` PVC), alongside the Ollama model mount
> (`gasha01.haynesnetwork:/hdd-nfs-repl/misc/ollama/models`). The **ComfyUI image config is now
> declared in this repo** (`app/helmrelease.yaml` + `app/comfyui/*.json`) and seeds a fresh database
> — but Open WebUI's PersistentConfig means an existing database still wins, so on a live instance it
> is applied once by hand (section f). If the PVC is ever restored empty, image generation and edit
> come up configured on their own; re-apply the RBAC with the admin-API steps below. Ollama models
> survive independently on the NFS mount.
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
**Update 2026-09-18:** the second 3090 was replaced and both cards are live (`nvidia.com/gpu: 2` on
talosw01). Nothing pins an app to a card yet — placement is haynes-ops#2960.

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

Owner ruling: **image generation available to all users incl. Default** (no gating). Default user
permission `features.image_generation` is `true` and nothing overrides it in the DB (`user.permissions`
is absent from the `config` row), so every logged-in user gets the image button.

### Current configuration (2026-09-21) — Qwen-Image-2.1 on the second 3090, generate **and** edit

Everything below is now **declared in Git** (`app/helmrelease.yaml` `extraEnvVars` + the ConfigMap
built from `app/comfyui/*.json`), where before it lived only in the Open WebUI database.
Admin → Settings → Images has two halves — *Create Image* and *Edit Image* — and 0.7.2 configures
them **separately**, with their own engine, base URL, workflow and node mapping.

| Setting | Value | Where |
|---------|-------|-------|
| `ENABLE_IMAGE_GENERATION` | `true` | helmrelease |
| `IMAGE_GENERATION_ENGINE` | `comfyui` | helmrelease |
| `COMFYUI_BASE_URL` | `http://comfyui.ai.svc.cluster.local:8188` | helmrelease |
| `IMAGE_GENERATION_MODEL` | `qwen_image_2.1_int8_convrot.safetensors` | helmrelease |
| `IMAGE_SIZE` | `1024x1024` | helmrelease |
| `IMAGE_STEPS` | `25` | helmrelease |
| `COMFYUI_WORKFLOW` | contents of `app/comfyui/generate-workflow.json` | ConfigMap `open-webui-comfyui-workflow`, key `generate-workflow.json` |
| `COMFYUI_WORKFLOW_NODES` | contents of `app/comfyui/generate-nodes.json` | same ConfigMap, key `generate-nodes.json` |
| `ENABLE_IMAGE_EDIT` | `true` | helmrelease |
| `IMAGE_EDIT_ENGINE` | `comfyui` | helmrelease |
| `IMAGE_EDIT_MODEL` | `qwen_image_2.1_int8_convrot.safetensors` (inert — nothing injects it) | helmrelease |
| `IMAGES_EDIT_COMFYUI_BASE_URL` | `http://comfyui.ai.svc.cluster.local:8188` | helmrelease |
| `IMAGES_EDIT_COMFYUI_WORKFLOW` | contents of `app/comfyui/edit-workflow.json` | same ConfigMap, key `edit-workflow.json` |
| `IMAGES_EDIT_COMFYUI_WORKFLOW_NODES` | contents of `app/comfyui/edit-nodes.json` | same ConfigMap, key `edit-nodes.json` |

No API key is set for either: ComfyUI has no auth and `COMFYUI_API_KEY` /
`IMAGES_EDIT_COMFYUI_API_KEY` are empty in the database. The `sk-1234` the admin page shows in the
"ComfyUI API Key" box is Open WebUI's **placeholder text**, not a stored value.

All four workflow/mapping settings are plain **env var strings** in Open WebUI (each is
`json.loads`'d at startup — there is no file-path option), so kustomize's `configMapGenerator` turns
the reviewable JSON files into a ConfigMap and the HelmRelease pulls each key in with
`valueFrom.configMapKeyRef`. The generator has `disableNameSuffixHash: true` on purpose: the env
reference sits inside HelmRelease `values`, which kustomize's nameReference transformer does **not**
rewrite, so a hashed name would dangle. The ConfigMap also carries
`kustomize.toolkit.fluxcd.io/substitute: disabled` — Flux's postBuild envsubst is strict and a stray
`$` in a prompt or filename would otherwise blank the whole graph.

#### Create Image — `app/comfyui/generate-workflow.json`

The graph is the same Qwen-Image-2.1 stack AppDaemon uses for the
camera renders, minus the reference-image inputs and with an `EmptyLatentImage` (node `12`) as the
latent source. The three `Select*Device` nodes pin the UNET, CLIP and VAE to **`gpu:0`** — which,
since the 2026-09-22 split, is the *only* card the ComfyUI container sees (3090 #0,
`GPU-18bf6eab-…`) — so a chat render cannot touch the LLM's card. Warm renders
take ~35–50 s at 25 steps (the old 50-step Qwen-Image-2512 graph took ~477 s, which used to outrun
the edge proxy's budget; see the 2026-07-10 verification note below).

Open WebUI field → node mapping (`app/comfyui/generate-nodes.json`):

| OWUI field (`type`) | `key` | node id | node (class) |
|---------------------|-------|---------|--------------|
| model | `unet_name` | `2` | UNETLoader |
| prompt | `prompt` | `6` | TextEncodeQwenImage21 |
| negative_prompt | `negative_prompt` | `6` | TextEncodeQwenImage21 |
| width | `width` | `12` | EmptyLatentImage |
| height | `height` | `12` | EmptyLatentImage |
| n | `batch_size` | `12` | EmptyLatentImage |
| steps | `steps` | `7` | KSampler |
| seed | `seed` | `7` | KSampler |

Three things about that table are load-bearing in 0.7.2
(`backend/open_webui/utils/images/comfyui.py`):

- **`seed` has no default key.** `prompt`/`width`/`height`/`steps`/… fall back to a sensible key when
  `key` is omitted; `seed` and `model` write to `inputs[node.key]` verbatim, and `key` defaults to
  `"text"`. Omit `key` there and you silently set `inputs["text"]`.
- **The seed must be mapped or every image is identical.** Open WebUI never plumbs a user-supplied
  seed into the create path; if a `seed` node *is* declared it generates a fresh random one per
  request, and if it is *not*, the graph's literal `seed: 0` is used every single time.
- **`model` overwrites node `2`'s `unet_name` with `IMAGE_GENERATION_MODEL`.** It must therefore be
  an exact filename from ComfyUI's UNET list. The CLIP (`qwen3vl_8b_int8_convrot`) and VAE
  (`qwen_image_2.1_vae_bf16`) are pinned to the Qwen-Image-2.1 pair, so selecting any other UNET in
  the admin UI will fail graph validation. If you would rather the UNET never be overridable, delete
  the `model` entry from `generate-nodes.json` — the loader keeps whatever the graph declares (Open WebUI
  falls back to listing `CheckpointLoaderSimple` checkpoints in the model dropdown, which is cosmetic).

#### Edit Image — `app/comfyui/edit-workflow.json`

**ComfyUI is a supported edit engine in 0.7.2.** The *Image Edit Engine* dropdown offers exactly
three: `Default (Open AI)`, `ComfyUI`, `Gemini` (the *generation* dropdown has a fourth,
Automatic1111). It is configured by an entirely separate set of keys — `ENABLE_IMAGE_EDIT`,
`IMAGE_EDIT_ENGINE`, `IMAGE_EDIT_MODEL`, `IMAGE_EDIT_SIZE`, `IMAGES_EDIT_COMFYUI_BASE_URL`,
`IMAGES_EDIT_COMFYUI_API_KEY`, `IMAGES_EDIT_COMFYUI_WORKFLOW`,
`IMAGES_EDIT_COMFYUI_WORKFLOW_NODES` — so setting up generation does nothing for edit.

How the attached image gets in: `POST /api/v1/images/edit` base64s or fetches the user's image,
uploads it to ComfyUI with `POST {base_url}/api/upload/image` (multipart field `image`, plus
`type=input`), takes the `name` ComfyUI returns, and patches that filename into the graph through the
`image` node type. ComfyUI mirrors every route under `/api`, so `/api/upload/image` is the same
handler as `/upload/image` — nothing extra to enable.

The graph is the T2I graph with the empty latent swapped for a `LoadImage` (node `1`) feeding
`TextEncodeQwenImage21`'s autogrow reference slot (`images.image_1`), and `KSampler.latent_image`
taken from that encoder's third output instead — the edit size comes from the input image, and node
`6`'s `resolution: 1024` is the pixel budget references are resized to. Same `gpu:0` pinning, same 25
steps. Output prefix is `ui/open-webui-edit`. Node `1`'s literal `"image": "input.png"` is a
placeholder that the mapping always overwrites; it is intentionally a name that does **not** exist in
ComfyUI's input dir, so a broken mapping fails loudly instead of silently editing some other file.

Mapping (`app/comfyui/edit-nodes.json`) — **three entries, and the omissions are deliberate**:

| OWUI field (`type`) | `key` | node id | node (class) |
|---------------------|-------|---------|--------------|
| image | `image` | `1` | LoadImage |
| prompt | `prompt` | `6` | TextEncodeQwenImage21 |
| seed | `seed` | `7` | KSampler |

Read `images.py` → the `IMAGE_EDIT_ENGINE == "comfyui"` branch before adding to that table. The edit
payload it builds is only `{image, prompt, width?, height?, n?}`, and `ComfyUIEditImageForm` is a
different model from the create one:

- **`steps` must NOT be mapped.** There is no `IMAGE_EDIT_STEPS` setting and the edit payload never
  carries steps, so `payload.steps` is always `None` — a `steps` entry would write `"steps": null`
  into KSampler and ComfyUI would reject the graph. Steps stay at the graph's literal `25`; change
  them by editing `edit-workflow.json`.
- **`negative_prompt` must NOT be mapped.** `ComfyUIEditImageForm` has no `negative_prompt` field at
  all, yet `comfyui_edit_image()` still handles that node type — mapping it raises `AttributeError`
  and 500s the request. Node `6`'s negative prompt stays the empty string in the graph.
- **`n` / `width` / `height` must NOT be mapped** for the same null-injection reason: they are only
  present in the payload when truthy (`width`/`height` only when `IMAGE_EDIT_SIZE` or the request
  carries an `NxN` size), and `None` otherwise. The edit graph has no `EmptyLatentImage` to point
  them at anyway. `IMAGE_EDIT_SIZE` is therefore left unset.
- **`model` is not mapped either.** `IMAGE_EDIT_MODEL` defaults to `""`, and an unset model would be
  written straight into `UNETLoader.unet_name`. The env var is still set to the right filename so
  that adding a `model` entry later is safe.

Both `seed` gotchas from the create mapping apply unchanged: `key` must be spelled `seed` (no
default), and without the entry every edit reuses `seed: 0`.

#### Where the images land

Saved images use `filename_prefix` **`ui/open-webui`** (edits: `ui/open-webui-edit`), i.e. the `ui/`
subfolder of ComfyUI's NAS
output dir. The `comfyui-output-retention` CronJob sweeps `-maxdepth 1` only, so UI-generated images
are deliberately never swept, unlike the automation renders in the top level.

### PersistentConfig — why the env vars alone do not change the live instance

Every variable above is an Open WebUI **`PersistentConfig`**: the env value is only read to *seed* the
`config` table, and from then on the database value wins on every start
(`config.py` → `PersistentConfig.__init__`: *"'X' loaded from the latest database entry"*). The test
is `config_value is not None`, so an **empty string or empty list in the DB still shadows the env
var** — which is exactly the situation for the edit keys.

**Storage:** plain **SQLite**, `/app/backend/data/webui.db` on the `open-webui` PVC (Ceph RBD, mounted
at `/app/backend/data`). There is no `DATABASE_URL` on the pod, so `env.py` falls back to
`sqlite:///{DATA_DIR}/webui.db` — this instance is **not** on Postgres. The whole persisted config is
**one row**: `config` table, single row `id=1`, column `data` = one JSON blob (created 2025-04-27, last
written 2026-07-10).

State read read-only on 2026-09-21:

| JSON path in `config.data` | Current value | Wanted |
|---|---|---|
| `image_generation.enable` | `true` | `true` |
| `image_generation.engine` | `"comfyui"` | `"comfyui"` |
| `image_generation.model` | `"Qwen-Image-2512"` ⚠️ display name, not a filename | `"qwen_image_2.1_int8_convrot.safetensors"` |
| `image_generation.size` | `"1024x1024"` | `"1024x1024"` |
| `image_generation.steps` | `50` ⚠️ | `25` |
| `image_generation.prompt.enable` | `true` | `true` |
| `image_generation.comfyui.base_url` | `"http://comfyui.ai.svc.cluster.local:8188"` | unchanged |
| `image_generation.comfyui.api_key` | `""` (the UI's `sk-1234` is placeholder text) | unchanged |
| `image_generation.comfyui.workflow` | old Qwen-Image-2512 graph, subgraph ids `197:*` | `generate-workflow.json` |
| `image_generation.comfyui.nodes` | 6 entries on `197:180/179/194`, no `model` | `generate-nodes.json` |
| `images.edit.enable` | `false` | `true` |
| `images.edit.engine` | `"openai"` | `"comfyui"` |
| `images.edit.model` | `""` | `"qwen_image_2.1_int8_convrot.safetensors"` (inert) |
| `images.edit.size` | `""` | leave `""` |
| `images.edit.comfyui.base_url` | `""` | `"http://comfyui.ai.svc.cluster.local:8188"` |
| `images.edit.comfyui.api_key` | `""` | unchanged |
| `images.edit.comfyui.workflow` | `""` | `edit-workflow.json` |
| `images.edit.comfyui.nodes` | `[]` | `edit-nodes.json` |

⚠️ **`steps: 50` is a straight 2× tax.** The persisted 50 comes from the old Qwen-Image-2512 graph;
Qwen-Image-2.1's own template default is 25 and that is what tonight's ~35–50 s timings were measured
at. Left at 50 the new workflow renders for ~70–100 s warm with no quality gain.

Note the mismatched prefixes in that table: the *generation* subtree is `image_generation.*` while the
*edit* subtree is `images.edit.*`. That is Open WebUI's own inconsistency, not a typo.

So the HelmRelease env vars are:

- the **declarative record** of the intended configuration, and
- the **DR seed** — if the `open-webui` PVC is ever restored empty, the pod comes up already wired to
  ComfyUI with both workflows and no manual step at all;

but they are **inert on the current database**. Applying them to the live instance is a once-only
action, by one of the two routes below.

> Do **not** reach for `ENABLE_PERSISTENT_CONFIG=false` to force the issue. It is global: it would
> also discard every other DB-only setting (RAG/embedding choices, UI defaults, banners…) and silently
> ignore anything an admin changes in the UI afterwards. Likewise `RESET_CONFIG_ON_START` (wipes the
> whole row) and the `$DATA_DIR/config.json` migration hook (replaces the whole row, then *renames*
> the file — impossible from a read-only ConfigMap mount).

#### Route A — Admin UI (no credentials needed beyond an admin login)

**Admin Panel → Settings → Images**, top to bottom:

*Create Image*
1. **Image Generation (Experimental)** → on; **Image Generation Engine** → `ComfyUI`.
2. **ComfyUI Base URL** → `http://comfyui.ai.svc.cluster.local:8188` (leave the API key box alone —
   `sk-1234` is placeholder text).
3. **ComfyUI Workflow** → paste `app/comfyui/generate-workflow.json`, and set the node mapping rows to
   the Create-Image table above. Workflow and mapping must be saved **together** — a mapping naming
   node `2` against a workflow without node `2` makes `GET /api/v1/images/models` 500.
4. **Set Default Model** → `qwen_image_2.1_int8_convrot.safetensors`. ⚠️ It currently reads
   `Qwen-Image-2512`, a display name; that is harmless only because the old mapping had no `model`
   entry. With the new mapping it is injected into `UNETLoader` and every render fails validation.
5. **Image Size** `1024x1024`, **Steps** `25` (down from the persisted 50).

*Edit Image*
6. **Image Edit** → on; **Image Edit Engine** → `ComfyUI`.
7. **ComfyUI Base URL** (edit section) → `http://comfyui.ai.svc.cluster.local:8188`.
8. **ComfyUI Workflow** → paste `app/comfyui/edit-workflow.json`; mapping rows = the three in the
   Edit-Image table (image / prompt / seed **only** — see the omission list above).
9. Save.

#### Route B — admin REST API, from inside the cluster

`POST /api/v1/images/config/update` takes the **whole** `ImagesConfig` body (generation *and* edit
fields together), so read the current config, patch it, post it back. It needs an **admin** bearer
token — `OPENWEBUI_API_KEY` from 1Password `openwebui` / secret `openwebui-secret`, which is an admin
key. Going in-cluster avoids Cloudflare (which blocks `python-urllib` User-Agents).

```bash
# Run from a pod in ns `ai` with OPENWEBUI_API_KEY in the env and the four JSON
# files from app/comfyui/ on disk. Never print the response: it contains the
# OpenAI image API key.
python3 - <<'EOF'
import json, os, urllib.request
BASE, KEY = "http://open-webui.ai.svc.cluster.local:80", os.environ["OPENWEBUI_API_KEY"]
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
cfg = json.loads(urllib.request.urlopen(
    urllib.request.Request(f"{BASE}/api/v1/images/config", headers=H)).read())
cfg.update({
    # Create Image
    "ENABLE_IMAGE_GENERATION": True,
    "IMAGE_GENERATION_ENGINE": "comfyui",
    "IMAGE_GENERATION_MODEL": "qwen_image_2.1_int8_convrot.safetensors",
    "IMAGE_SIZE": "1024x1024",
    "IMAGE_STEPS": 25,
    "COMFYUI_BASE_URL": "http://comfyui.ai.svc.cluster.local:8188",
    "COMFYUI_WORKFLOW": open("generate-workflow.json").read(),
    "COMFYUI_WORKFLOW_NODES": json.load(open("generate-nodes.json")),
    # Edit Image
    "ENABLE_IMAGE_EDIT": True,
    "IMAGE_EDIT_ENGINE": "comfyui",
    "IMAGE_EDIT_MODEL": "qwen_image_2.1_int8_convrot.safetensors",
    "IMAGES_EDIT_COMFYUI_BASE_URL": "http://comfyui.ai.svc.cluster.local:8188",
    "IMAGES_EDIT_COMFYUI_WORKFLOW": open("edit-workflow.json").read(),
    "IMAGES_EDIT_COMFYUI_WORKFLOW_NODES": json.load(open("edit-nodes.json")),
})
urllib.request.urlopen(urllib.request.Request(
    f"{BASE}/api/v1/images/config/update", data=json.dumps(cfg).encode(), headers=H))
print("updated")
EOF
```

Open WebUI writes the patched values straight back into the same `config` row and calls
`PersistentConfig.update()`, so the change is live immediately — **no pod restart**.

**Applied and verified 2026-09-22 (driving session, one-shot Job `openwebui-comfyui-apply-0921` in ns
`ai`, secret + ConfigMap mounted; nothing printed but status lines).** `GET /api/v1/images/config` →
patch → `POST /api/v1/images/config/update` returned 200 and the re-read config showed the new
values live without a restart. Then, through Open WebUI's own API:

- `POST /api/v1/images/generations` `{"prompt": …, "n": 1, "size": "1024x1024"}` → 200, one image
  (527 s: ComfyUI had just restarted, so this included the model reload; warm renders are ~35–60 s).
- `POST /api/v1/images/edit` (singular — `/edits` is 405). Because the route declares two body
  parameters (`form_data`, `metadata`), the JSON must be **wrapped**:
  `{"form_data": {"image": "data:image/png;base64,…", "prompt": …}, "metadata": {}}` — an unwrapped
  body is a 422 `body.form_data missing`. → 200 in 126 s, output `ui/open-webui-edit_00001_.png`.

#### Route C — edit the persisted row directly (last resort)

Only if no admin token is available. Take a backup first; the row is the entire app configuration.

```bash
# BACKUP — copy the whole sqlite file off the PVC before touching it
kubectl exec -n ai open-webui-0 -- python3 -c \
  "import sqlite3;src=sqlite3.connect('/app/backend/data/webui.db');dst=sqlite3.connect('/app/backend/data/webui.db.bak-$(date +%F)');src.backup(dst);dst.close()"
# ...or just the config row as JSON (enough to roll back this change):
kubectl exec -n ai open-webui-0 -- python3 -c \
  "import sqlite3,json;print(sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True).execute('select data from config where id=1').fetchone()[0])" \
  > config-row-backup.json     # contains API keys — treat as a secret, do not commit
```

Then patch **only** the `image_generation` and `images.edit` subtrees of `config.data` (the two paths
in the table above), leaving every other key untouched, and write the row back with
`UPDATE config SET data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1`. **A pod restart is required
afterwards** — `CONFIG_DATA` is read once at import time, so a direct row edit is invisible until the
process reloads (`kubectl rollout restart statefulset/open-webui -n ai`). Rolling back is the same
operation with the backup's two subtrees.

Route B is preferred over Route C in every case: it validates the body, keeps the row's other keys
provably untouched, and needs no restart.

#### Changing the workflows later

Edit `app/comfyui/generate-workflow.json` / `edit-workflow.json` (and the matching `*-nodes.json` if
node ids move), merge, and Flux rolls the pod via Reloader — **but** the same PersistentConfig rule
applies: the running instance keeps serving the DB copy until it is re-applied by Route A or B. Treat
the repo as the source of truth and the apply step as the deploy. If you change node ids, re-check
the `seed` / `model` keys and the edit-mapping omission list above.

### Operational notes

- **Transport:** Open WebUI talks to ComfyUI over plain HTTP `/prompt`, `/history/{id}`, `/view`
  (plus `/api/upload/image` on the edit path) **and a websocket** (`ws://…:8188/ws?clientId=<user
  id>`) — the websocket is how it learns the render finished, so it is not optional, on either path.
  All of it is in-namespace service traffic (`ai` has no NetworkPolicy or CiliumNetworkPolicy), so
  nothing proxies or filters it. The rendered PNG is fetched by the backend and re-uploaded into Open
  WebUI's own storage, so the browser never needs to reach ComfyUI.
- **Shared queue:** ComfyUI runs one queue. A chat render queues behind the AppDaemon camera renders
  (and vice versa); on a busy queue the synchronous Open WebUI request can outlive the edge proxy's
  budget even though ComfyUI still produces the image. Same for the first render after a ComfyUI
  restart, which pays a cold model load off HDD-NFS.
- **GPU:** `gpu:0` is the only card the ComfyUI container can see — 3090 #0
  (`GPU-18bf6eab-…`, VM bus `01:00.0`), pinned to ComfyUI by the owner ruling of 2026-09-22
  (haynes-ops#2960; see "the per-app GPU split" above, including why ComfyUI gets the *hot* slot).
  The other 3090 belongs to `llama-server`.
  These two workflows steer *within* ComfyUI's one visible card; the split itself is done by
  `NVIDIA_VISIBLE_DEVICES`.
- **Uploads accumulate:** every edit leaves the user's source image in ComfyUI's `input/` dir on the
  workspace PVC. Nothing prunes that today; the output-retention CronJob only touches the NAS output
  dir. Worth watching if edit gets heavy use.

### Superseded — the 2026-07-10 config (history)

The original wave pointed `COMFYUI_WORKFLOW` at the **reused, unmodified** AppDaemon file
`apps/ai/stable-diffusion/comfyui/resources/api-workflows/image_qwen_Image_2512_API.json`
(Qwen-Image-2512 fp8 + a Lightning-4step LoRA switch), with `IMAGE_STEPS=50` and this mapping:

| OWUI field | key | node id | Qwen node (class) |
|------------|-----|---------|-------------------|
| prompt | text | `197:180` | CLIPTextEncode (positive) |
| negative_prompt | text | `197:195` | CLIPTextEncode (negative) |
| width | width | `197:179` | EmptySD3LatentImage |
| height | height | `197:179` | EmptySD3LatentImage |
| steps | steps | `197:194` | KSampler |
| seed | seed | `197:194` | KSampler (seed key is required — no default) |

**Superseded 2026-09-18 — kept for history.** Single-GPU note: the second 3090 is detached (GPU repair deferred, PLAN-021 part a). ComfyUI and
Ollama share the one RTX 3090 on `talosw01`, so a large chat model resident in VRAM contends with
image generation. No queue was added (owner ruling) — usage metrics will show if gating/GPU is needed.

**Superseded 2026-09-22 — kept for history.** Two-GPU note (2026-09-18): talosw01 now passes through
two RTX 3090s. ComfyUI took `cuda:0` = the replacement card (VM bus `01:00.0`, UUID
`GPU-18bf6eab-…`, host `0000:01:00`, hostpci0) and holds ~21 GB there; Ollama sees both
(`NVIDIA_VISIBLE_DEVICES=all`) and places layers wherever VRAM is free, so the 70B no longer
contends with ComfyUI. Which app gets which card, and whether Ollama should span both, is an open
decision: haynes-ops#2960.

#### Current — the per-app GPU split (owner ruling 2026-09-22, haynes-ops#2960)

`#2960`'s "no per-app GPU pinning" is **lifted**. talosw01's two 3090s are now owned per app, by
`NVIDIA_VISIBLE_DEVICES` on each container (the device plugin cannot pin a *named* card, so no app
in `ai` requests `nvidia.com/gpu`):

| Card | VM bus | UUID | Owner | Slot |
|------|--------|------|-------|------|
| 3090 #0 | `01:00.0` | `GPU-18bf6eab-c76a-26ba-74c8-76093b705b8b` (the new card, #3052) | `comfyui` — exclusively | **hot** |
| 3090 #1 | `02:00.0` | `GPU-d8a856f1-f955-f683-bc24-654561496774` (the original card) | `llama-server` — resident assist/chat LLM | **cool** |

**Which card runs what, and why — do not swap this back without reading #3052.** The roles above are
the *reverse* of how the split first shipped on 2026-09-22, and the reason is thermal, not
functional. Slot `01:00.0` takes intake air that has already passed the CPU radiator: under
sustained load the card there hits ~87 °C and the driver cuts it to **225 MHz** within ~10 s with
the fan already at 100 % (SW thermal slowdown `0x20`). A llama-server bench on it fell from **38 to
8 tok/s**. So the latency-sensitive tenant — the chat/Assist LLM, which answers every turn a human
is waiting on — gets `02:00.0`, and ComfyUI takes the hot slot, where throttling only means a
slower render. Neither card is faulty; the chassis airflow is, and that is haynes-ops#3052. If
#3052 is ever fixed, this can go back to whatever is convenient.

`ollama-prime` keeps `NVIDIA_VISIBLE_DEVICES=all` and takes the leftovers, spilling to CPU when
neither card has room (accepted by the owner). ComfyUI's per-job peak is ~16–20 GB, which fits one
card; AppDaemon's camera renders and Open WebUI's chat renders load the same
`qwen_image_2.1_int8_convrot` + `qwen3vl_8b_int8_convrot` files, so one resident copy serves both;
and the LLM needs a whole card to stay resident.

Because the ComfyUI container sees exactly one GPU, the `Select*Device` nodes in
`app/comfyui/generate-workflow.json` and `edit-workflow.json` say **`gpu:0`** — which simply means
"the one visible card", so the swap did **not** change them. They said `gpu:1` only back when the
container could see both.

### OpenAI-compatible connections (2026-09-22 — llama-server added)

Open WebUI reaches three OpenAI-compatible endpoints. The two lists are **index-matched**: Open WebUI
splits `OPENAI_API_BASE_URLS` and `OPENAI_API_KEYS` on `;` and zips them, so they must have the same
length and order.

| # | Endpoint | Key | Declared in |
|---|----------|-----|-------------|
| 0 | `http://open-webui-pipelines.ai.svc.cluster.local:9099` | 1Password `openwebui` → `OPENAI_API_KEYS`, field 1 | the chart prepends it (`pipelines.enabled`) |
| 1 | `https://api.openai.com/v1` | same secret, field 2 | `openaiBaseApiUrls[0]` in `app/helmrelease.yaml` |
| 2 | `http://llama-server.ai.svc.cluster.local:8080/v1` (model `muse-glimmer-30b`) | literal `none` | `openaiBaseApiUrls[1]`; placeholder appended in `app/externalsecret.yaml` |

llama.cpp serves without `--api-key` and ignores `Authorization`, so slot 2 needs no real key — only
a non-empty one, which is why the placeholder lives in the ExternalSecret template rather than in
1Password. **Adding or reordering a URL means editing both files.**

The URL list moved out of `extraEnvVars` and into the chart's own `openaiBaseApiUrls` in the same
change: with it in `extraEnvVars` the chart emitted `OPENAI_API_BASE_URLS` **twice** (once from its
`openaiBaseApiUrl` default + the Pipelines endpoint, once from `extraEnvVars`) and the container
relied on last-one-wins. Harmless while both copies said the same thing; a trap once they diverge.

**PersistentConfig here behaves differently from the image config.** `OPENAI_API_BASE_URLS` and
`OPENAI_API_KEYS` are `PersistentConfig` (`openai.api_base_urls` / `openai.api_keys`), but the
`openai` subtree has **never been written** to `config` in `webui.db` — nobody has pressed Save in
Admin → Settings → Connections — so `get_config_value()` returns `None` and the env seeds the values
on **every** start. No once-only admin step was needed. That stops being true the moment an admin
saves that page: after that the database wins and the env vars are a DR seed only, exactly like the
image settings in section (f).

## Verification (2026-07-10)
- **Models:** `ollama list` on ollama-prime shows the starter set. `llama3.1:8b` answered a chat
  prompt (explained Kubernetes) via the OWUI Ollama proxy; `nomic-embed-text` returned a 768-dim
  vector. `llama3.3:70b` is served + listed + gated; its cold first-load needs the timeout/memory fix
  above and enough GPU headroom — on the shared single 3090 it competes with ComfyUI for VRAM (only
  ~16 of 80 layers fit on the GPU while ComfyUI holds it, forcing ~64 layers onto CPU from HDD-NFS,
  which is very slow). Loads cleanly (~35 layers on GPU) when ComfyUI's VRAM is free. This shared-GPU
  cold-start latency is the concrete motivation for the deferred GPU repair (PLAN-021 part a). *(Repair
done 2026-09-18 — see the two-GPU note above.)*
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

## Verification (2026-09-21 — the Qwen-Image-2.1 / `gpu:1` wave)
Verified read-only, from the repo and the live cluster:
- **Graphs vs live ComfyUI (v0.37.0):** every class in both graphs
  (`SelectModelDevice`/`SelectCLIPDevice`/`SelectVAEDevice`, `QwenImage21Cache`,
  `TextEncodeQwenImage21`, `LoadImage`, …) exists in `/object_info`; all three model files
  (`qwen_image_2.1_int8_convrot`, `qwen3vl_8b_int8_convrot`, `qwen_image_2.1_vae_bf16`) are on the NAS;
  every link's source node, output index and type matches; every combo value is in range, **`gpu:1`
  included** (`SelectModelDevice.device` options are `default|cpu|gpu:0|gpu:1`). The same check was
  re-run on each graph *after* simulating Open WebUI's patching: `comfyui_create_image()` with
  `generate-nodes.json` (prompt, negative prompt, size, batch, steps, a random seed and the UNET name
  all land on the intended inputs) and `comfyui_edit_image()` with `edit-nodes.json` (uploaded
  filename → `LoadImage.image`, prompt → node `6`, random seed → node `7`, steps left at the graph's
  25). Both still validate afterwards. The same simulation is what showed that mapping
  `steps`/`n`/`width`/`height` on the **edit** path injects `null`, and that `negative_prompt` raises
  — hence the three-entry edit mapping.
- **Persisted DB config:** read-only from `/app/backend/data/webui.db` (SQLite on the PVC — no
  `DATABASE_URL`, so not Postgres; `config` table, single row `id=1`, last written 2026-07-10) —
  generation is already enabled against ComfyUI with the Qwen-Image-2512 graph at 50 steps, and the
  whole `images.edit` subtree exists but is empty/OpenAI. Both therefore shadow the new env vars, and
  both need the once-only apply above. Exact paths + current/wanted values are in the table above.
- **Edit engine support:** ComfyUI is one of the three engines the 0.7.2 *Image Edit Engine* dropdown
  offers (`Default (Open AI)` / `ComfyUI` / `Gemini`) — read out of the built frontend bundle, not
  guessed. `sk-1234` in the ComfyUI API Key box is likewise placeholder text in that bundle; the
  stored key is a zero-length string.
- **Chart render:** `helm template` of open-webui 10.2.1 with these values emits all eight env vars,
  the two `configMapKeyRef`s included (`extraEnvVars` map values are passed through with `toYaml`, so
  `valueFrom` works exactly as it already does for the OAuth secrets).
- **Not verified:** no render or edit was queued, nothing was written to the database, and no Open
  WebUI login was performed from the agent session (read-only mandate, and the agent has no OWUI
  account). The end-to-end "type a prompt, get a PNG" / "attach an image, get an edit" checks belong
  to the apply step. The graphs themselves were tested by the owner on the live server on 2026-09-21.

## Owner TODOs
1. Decide `family` membership: which haynesnetwork **Family**-role OWUI users to add (Admins are in;
   the two existing user-role accounts are NOT — classify them). Add via Admin → Users or the
   `/users/add` group endpoint.
2. (Optional, future) Enable OIDC-driven group sync — needs an **Authentik** change (groups claim on
   the `open-webui` provider + a `family` Authentik group) + `ENABLE_OAUTH_GROUP_MANAGEMENT`/
   `OAUTH_GROUPS_CLAIM` on OWUI. Do not enable OWUI side before the Authentik claim exists.
3. Decide whether the uncensored `huihui_ai/gemma3-abliterated:12b`/`:latest` (currently hidden from
   regular users, no public entry) should be all-users, gated, or removed.
4. **Once-only:** apply the Qwen-Image-2.1 config to the live instance — the persisted DB values
   shadow the new env vars until then, for **both** Create Image and Edit Image. Route A (admin UI) or
   Route B (admin API) in section (f). Three things that are easy to get wrong: the **Model** field
   must become `qwen_image_2.1_int8_convrot.safetensors` (it currently reads `Qwen-Image-2512`),
   **Steps** must come down from 50 to 25 (50 doubles render time on 2.1 for no gain), and the edit
   mapping must have only the three rows image/prompt/seed.
