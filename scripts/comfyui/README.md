# ghcr.io/thaynes43/comfyui

Our own ComfyUI image. It replaces `ghcr.io/ai-dock/comfyui`, which was last
pushed in November 2024, ships Python 3.10, and needed `privileged: true` to see
the GPU.

Keeping that image alive meant an init script (`resources/provisioning.sh`) that
git-checked-out a newer ComfyUI tag *over* the baked code and pip-installed
~5.7 GB of dependencies onto the PVC, read back through `PYTHONPATH`. The
v0.37.0 upgrade broke on exactly that layering: `pip install --target` leaves
the previous versions' `*.dist-info` behind, `importlib.metadata` reported a
tokenizers version that was no longer installed, and ComfyUI refused to start.

Here the code and its dependencies are **baked into the image** and versioned
with it. The only things on volumes are models, outputs and user data — and the
root filesystem is read-only.

| | |
|---|---|
| Base | `python:3.13-slim`, pinned by digest |
| ComfyUI | `ARG COMFYUI_VERSION` in the Dockerfile (Renovate-tracked) |
| torch | 2.12.0 / torchvision 0.27.0 / torchaudio 2.11.0, CUDA 13.0 wheels from PyPI, pinned in `constraints.txt` |
| User | uid/gid 1000 (`comfy`), never root, no `privileged` |
| Custom nodes | none — no ComfyUI-Manager, no third party. Only what ComfyUI itself ships. |
| Image tag | `<comfyui version without v>-<image revision>`, set once in `.github/workflows/comfyui-build.yml` |

## Subcommands

The entrypoint is `entrypoint.py`; `CMD` defaults to `serve`.

### `serve`

Execs `python main.py` with every writable path pointed off the read-only root
filesystem. All of these are directories ComfyUI will actually use — the
entrypoint creates each one before exec.

| Env var | Default | ComfyUI flag |
|---|---|---|
| `COMFYUI_LISTEN` | `0.0.0.0` | `--listen` |
| `COMFYUI_PORT` | `8188` | `--port` |
| `COMFYUI_USER_DIR` | `/workspace/user` | `--user-directory` |
| `COMFYUI_INPUT_DIR` | `/workspace/input` | `--input-directory` |
| `COMFYUI_OUTPUT_DIR` | `/workspace/output` | `--output-directory` |
| `COMFYUI_TEMP_DIR` | `/workspace/temp` | `--temp-directory` (see below) |
| `COMFYUI_DATABASE_URL` | unset | `--database-url`, only when set |
| `COMFYUI_ARGS` | empty | appended verbatim (shell-split) |

`--disable-auto-launch` is always passed.

Three things worth knowing, all verified against v0.37.0's source:

* **`--temp-directory` gets `temp` appended.** `main.py` does
  `os.path.join(os.path.abspath(args.temp_directory), "temp")`. Every other
  variable here names the directory ComfyUI ends up using, so this one does too:
  the entrypoint passes the *parent* when `COMFYUI_TEMP_DIR` ends in `temp`, and
  logs the effective path either way. The parent must be writable — ComfyUI
  `rmtree`s and recreates the temp directory on every boot.
* **The SQLite database follows the user directory.** With `--database-url`
  unset, `app/database/db.py` resolves `sqlite:///<user dir>/comfyui.db`, so the
  database, its `.lock` and any alembic `.bkp` all land on the workspace volume.
  It is created on every start, whether or not `--enable-assets` is on.
* **The input directory must be writable, not merely present.** `LoadAudio` and
  `Load3D` `makedirs` inside it while nodes load.

No log file is written: `app/logger.py` only adds a file handler for
`--verbose LEVEL FILE`. The frontend is served straight out of the installed
`comfyui-frontend-package` wheel — the default `--front-end-version` makes no
network call and writes nothing. Bytecode is precompiled at build time and
`PYTHONDONTWRITEBYTECODE=1` stops runtime attempts. `HOME` and every library
cache (`HF_HOME`, `TORCH_HOME`, `TRITON_CACHE_DIR`, …) point into `/tmp`.

**Models live at `/opt/ComfyUI/models`**, where the NFS share is mounted over
the directory the image ships. `--base-directory` is deliberately not used: it
would move models, custom_nodes, input, output, temp and user in one go, and
only some of those belong on a volume.

### `provision`

Downloads the models the stored workflows need. See below.

### `test`

The CI smoke test, present only in the `test` build target: the provisioner unit
tests, then ComfyUI with `--cpu --quick-test-for-ci`, then a real server start
that polls `/system_stats` (asserting the reported version matches the image),
checks `/object_info` for the node classes the stored workflows use, and
confirms the temp directory and `comfyui.db` landed on the writable volume. No
network, no GPU, no models.

`--quick-test-for-ci` exits before the temp directory is created and before the
HTTP bind, so it cannot prove the read-only root filesystem is survivable —
hence the second, fuller run.

## Provisioning: download what the workflows need

The old `models.txt` was a pipe-separated list a human had to keep in step with
the workflows by hand. `provision.py` derives the list from the workflows
themselves.

**Sources, merged:**

1. **Workflow files** (`--workflows-dir`, repeatable, default
   `/config/workflows`). Each `*.json` is classified:
   * A **UI-format workflow** (what the ComfyUI editor saves, and what the
     official templates ship) carries `properties.models[] = {name, url,
     directory}` on its loader nodes. Those are complete download instructions.
     They are collected from the top-level `nodes` **and recursively from
     `definitions.subgraphs[].nodes`** — the official Qwen-Image-2.1 template
     puts every one of its three models inside a subgraph, so a flat scan finds
     nothing at all.
   * An **API-format graph** (a flat `{id: {class_type, inputs}}` dict, what
     AppDaemon POSTs to `/prompt`) carries only filenames. The loader's
     `class_type` gives the folder (`UNETLoader` → `diffusion_models`,
     `CLIPLoader` → `text_encoders`, `VAELoader` → `vae`, `LoraLoader*` →
     `loras`, `CheckpointLoaderSimple` → `checkpoints`, …). These are
     **requirements**: they have to be satisfied by a URL from another source or
     by a file already on disk.
2. **Manifest** (`--manifest`, default `/config/models.yaml`, optional):

   ```yaml
   models:
     - path: loras/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors
       url: https://huggingface.co/lightx2v/...
       sha256: <optional>
       note: <optional free text>
   ```

   A manifest entry **wins** over a template's URL for the same path, and says
   so in the log.

**Rules:**

* `https://` only. `path` must resolve inside the models root (no `..`, no
  absolute paths) and its first segment must be a model folder ComfyUI actually
  searches — `custom_nodes/` is not one, so nothing can be written into the
  source tree.
* An existing non-empty file is **skipped without being hashed**. The models
  root is an HDD-backed NFS share; re-reading a 20 GB checkpoint on every pod
  start costs minutes. A re-download is triggered only when the recorded size no
  longer matches what is on disk, or when a manifest `sha256` differs from the
  recorded one.
* Downloads go to `<dest>.part` with HTTP Range resume, are hashed **as they
  stream**, verified against `sha256` when one is given (mismatch deletes the
  part and counts as a failure), and `os.replace`d into place. Resuming re-reads
  only the partial file, and logs that it is doing so.
* `<models root>/.provisioned.json` records `{url, size, sha256}` per model and
  is written atomically.
* `Authorization: Bearer $HF_TOKEN` is attached **only** for `huggingface.co` /
  `hf.co` hosts, and is stripped if a redirect leaves them — Hugging Face hands
  downloads off to a presigned CDN URL, and that URL is already the credential.
  Query strings are redacted from every log line, so neither the token nor a
  presigned signature is ever printed.
* Unsatisfied API-graph requirements produce a WARNING listing them.
* **Exit code 0 unless `--strict`**, which returns non-zero on any failed
  download or unsatisfied requirement. Non-strict is the default on purpose: a
  Hugging Face outage must not stop ComfyUI starting with the models it already
  has.

**Other flags:** `--install-workflows-to <dir>` copies the UI-format workflow
JSONs into ComfyUI's user workflows directory (skipping API-format files) — what
the old init container did with `cp`. `--dry-run` prints the plan as a table and
exits without writing anything.

**Env defaults:** `COMFYUI_MODELS_DIR` (`/opt/ComfyUI/models`),
`COMFYUI_WORKFLOWS_DIR` (`/config/workflows`), `COMFYUI_MODEL_MANIFEST`
(`/config/models.yaml`), `HF_TOKEN`, `COMFYUI_LOG_LEVEL`.

## How to add a model

Pick whichever fits:

* **Drop a UI workflow into the app's `resources/workflows/`.** If it came from
  the ComfyUI template gallery it already carries the model URLs, and the next
  `provision` run fetches them. Nothing else to edit.
* **Add a manifest row** for anything a workflow references without a URL (a
  LoRA an API graph names, say), or to override a template's URL. Give a
  `sha256` if you want the download verified; leaving it out avoids any hashing
  of the existing file on later runs.

Check what would happen before committing:

```bash
python scripts/comfyui/provision.py --dry-run \
  --models-root /tmp/models \
  --workflows-dir kubernetes/main/apps/ai/stable-diffusion/comfyui/resources/workflows \
  --workflows-dir kubernetes/main/apps/ai/stable-diffusion/comfyui/resources/api-workflows \
  --manifest /nonexistent
```

## How to bump ComfyUI

1. Edit `ARG COMFYUI_VERSION` in the `Dockerfile` (Renovate opens this PR on its
   own — `datasource=github-releases depName=Comfy-Org/ComfyUI`; it is
   deliberately **not** auto-merged).
2. Reset the revision half of `IMAGE_TAG` in
   `.github/workflows/comfyui-build.yml` to `1`, keeping the version half in
   step (`0.38.0-1`).
3. Merge. The build publishes and cosign-signs
   `ghcr.io/thaynes43/comfyui:<tag>`.
4. Bump the tag in the app's HelmRelease in a separate PR.

When only these scripts change, leave `COMFYUI_VERSION` alone and bump just the
revision (`0.37.0-2`).

Check the torch pins against the node's driver when bumping ComfyUI: they live
in `constraints.txt` because ComfyUI leaves the torch stack unpinned upstream,
and torch 2.12 needs a driver that supports CUDA 13.0 (the 3090 node runs 580.x).

## Running the tests locally

```bash
cd scripts/comfyui
res=../../kubernetes/main/apps/ai/stable-diffusion/comfyui/resources
mkdir -p testdata/deployed/{workflows,api-workflows}
cp "$res"/workflows/*.json testdata/deployed/workflows/
cp "$res"/api-workflows/*.json testdata/deployed/api-workflows/
COMFYUI_TEST_STRICT_FIXTURES=1 python -m pytest -q test_provision.py
```

The provisioner is stdlib-only apart from PyYAML (which ComfyUI already depends
on), so the tests run against any Python 3.9+ without the image.
