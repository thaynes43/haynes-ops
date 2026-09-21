#!/usr/bin/env python3
"""Download the models ComfyUI's stored workflows actually need.

The old ai-dock init script read a pipe-separated ``models.txt`` that a human
had to keep in step with the workflows by hand.  This provisioner derives the
list from the workflows themselves:

* **UI-format workflow JSON** (what the ComfyUI web editor saves, and what the
  official templates ship) embeds ``properties.models[] = {name, url,
  directory}`` on every loader node.  That is a complete, self-describing
  download instruction, and it survives nesting inside subgraphs.
* **API-format graphs** (a flat ``{id: {class_type, inputs}}`` dict, what
  AppDaemon POSTs to ``/prompt``) carry only filenames.  Those become
  *requirements*: they must be satisfied by a URL from another source, or by a
  file already on disk, or they are reported.

A YAML manifest supplies anything the workflows cannot describe (a LoRA that a
subgraph references without a URL, say) and overrides a template's URL for the
same path.

Everything here is stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

LOG = logging.getLogger("comfyui-provision")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Model folders ComfyUI v0.37.0 knows about, straight out of ``folder_paths``'
#: ``folder_names_and_paths`` — every key plus the alternate directory names it
#: also searches.  ``custom_nodes``/``datasets`` are deliberately absent: they
#: hang off ComfyUI's base path, not the models root, and nothing here may
#: write into the (read-only) source tree.
KNOWN_MODEL_FOLDERS = frozenset(
    {
        "audio_encoders",
        "background_removal",
        "checkpoints",
        "classifiers",
        "clip",  # alias searched for text_encoders
        "clip_vision",
        "configs",
        "controlnet",
        "detection",
        "diffusers",
        "diffusion_models",
        "embeddings",
        "frame_interpolation",
        "geometry_estimation",
        "gligen",
        "hypernetworks",
        "latent_upscale_models",
        "loras",
        "model_patches",
        "optical_flow",
        "photomaker",
        "style_models",
        "t2i_adapter",  # alias searched for controlnet
        "text_encoders",
        "unet",  # alias searched for diffusion_models
        "upscale_models",
        "vae",
        "vae_approx",
    }
)

#: Folders ComfyUI resolves interchangeably.  A requirement for
#: ``diffusion_models/x`` is satisfied by ``unet/x`` and vice versa, so
#: satisfaction checks look in every alias.
FOLDER_ALIASES: Dict[str, Tuple[str, ...]] = {
    "diffusion_models": ("diffusion_models", "unet"),
    "unet": ("diffusion_models", "unet"),
    "text_encoders": ("text_encoders", "clip"),
    "clip": ("text_encoders", "clip"),
    "controlnet": ("controlnet", "t2i_adapter"),
    "t2i_adapter": ("controlnet", "t2i_adapter"),
}

#: Loader class_type -> models folder.  Consulted before the input-name table
#: because the class is unambiguous where an input name is not (``clip_name``
#: means text_encoders on CLIPLoader and clip_vision on CLIPVisionLoader).
CLASS_TYPE_TO_FOLDER: Dict[str, str] = {
    "AudioEncoderLoader": "audio_encoders",
    "CheckpointLoader": "checkpoints",
    "CheckpointLoaderSimple": "checkpoints",
    "CLIPLoader": "text_encoders",
    "CLIPVisionLoader": "clip_vision",
    "ControlNetLoader": "controlnet",
    "DiffControlNetLoader": "controlnet",
    "DiffusersLoader": "diffusers",
    "DualCLIPLoader": "text_encoders",
    "GLIGENLoader": "gligen",
    "HypernetworkLoader": "hypernetworks",
    "ImageOnlyCheckpointLoader": "checkpoints",
    "ModelPatchLoader": "model_patches",
    "PhotoMakerLoader": "photomaker",
    "QuadrupleCLIPLoader": "text_encoders",
    "StyleModelLoader": "style_models",
    "TripleCLIPLoader": "text_encoders",
    "UNETLoader": "diffusion_models",
    "unCLIPCheckpointLoader": "checkpoints",
    "UpscaleModelLoader": "upscale_models",
    "VAELoader": "vae",
}

#: Any class_type starting with one of these maps to the given folder.  Covers
#: the LoraLoader family (LoraLoader, LoraLoaderModelOnly, LoraModelLoader …).
CLASS_TYPE_PREFIX_TO_FOLDER: Tuple[Tuple[str, str], ...] = (
    ("LoraLoader", "loras"),
    ("CheckpointLoader", "checkpoints"),
    ("ControlNetLoader", "controlnet"),
)

#: Input names that unambiguously name a model file, and the folder they imply
#: when the class_type is unknown to us.
INPUT_NAME_TO_FOLDER: Dict[str, str] = {
    "audio_encoder_name": "audio_encoders",
    "clip_name": "text_encoders",
    "clip_name1": "text_encoders",
    "clip_name2": "text_encoders",
    "clip_name3": "text_encoders",
    "clip_name4": "text_encoders",
    "ckpt_name": "checkpoints",
    "control_net_name": "controlnet",
    "gligen_name": "gligen",
    "hypernetwork_name": "hypernetworks",
    "lora_name": "loras",
    "model_patch_name": "model_patches",
    "style_model_name": "style_models",
    "unet_name": "diffusion_models",
    "vae_name": "vae",
}

#: Additionally considered when the class_type IS known (so the folder does not
#: have to be guessed from the name).  ``model_name``/``upscale_model`` are far
#: too generic to trust on their own.
AMBIGUOUS_INPUT_NAMES = frozenset({"model_name", "upscale_model", "upscale_model_name"})

#: ComfyUI's ``supported_pt_extensions`` — a value has to look like a weights
#: file before it is treated as one.
MODEL_EXTENSIONS = (
    ".ckpt",
    ".pt",
    ".pt2",
    ".bin",
    ".pth",
    ".safetensors",
    ".pkl",
    ".sft",
    ".gguf",
)

#: Only these schemes may appear in a workflow or manifest URL.  Patched by the
#: unit tests so the download machinery can be exercised against a local
#: ``http.server``; the rejection path is tested against the real value.
ALLOWED_URL_SCHEMES = ("https",)

#: Hosts the ``HF_TOKEN`` bearer may be sent to.  Nothing else ever sees it.
HUGGINGFACE_HOSTS = ("huggingface.co", "hf.co")

STATE_FILENAME = ".provisioned.json"
STATE_VERSION = 1

CHUNK_SIZE = 1024 * 1024
PROGRESS_INTERVAL_SECONDS = 30.0
DEFAULT_RETRIES = 4
DEFAULT_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class ModelSpec(object):
    """A model we know how to fetch: a models-root-relative path plus a URL."""

    __slots__ = ("path", "url", "sha256", "source", "note")

    def __init__(self, path, url, source, sha256=None, note=None):
        # type: (str, str, str, Optional[str], Optional[str]) -> None
        self.path = path
        self.url = url
        self.source = source
        self.sha256 = sha256.lower() if sha256 else None
        self.note = note

    def __repr__(self):  # pragma: no cover - debugging aid
        return "ModelSpec(path=%r, url=%r, source=%r)" % (self.path, self.url, self.source)


class Requirement(object):
    """A model an API-format graph needs but cannot tell us where to get."""

    __slots__ = ("path", "class_type", "source")

    def __init__(self, path, class_type, source):
        # type: (str, str, str) -> None
        self.path = path
        self.class_type = class_type
        self.source = source

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Requirement(path=%r, class_type=%r)" % (self.path, self.class_type)


class ProvisionError(Exception):
    """A malformed source, not a transient download failure."""


# ---------------------------------------------------------------------------
# Path handling
# ---------------------------------------------------------------------------


def normalise_model_path(raw):
    # type: (str) -> str
    """Normalise and validate a models-root-relative path.

    Rejects absolute paths, drive letters, backslashes and any ``..`` segment,
    and requires the first segment to be a model folder ComfyUI searches.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ProvisionError("empty model path")

    candidate = raw.strip().replace("\\", "/")
    if candidate.startswith("/") or candidate.startswith("~"):
        raise ProvisionError("model path must be relative to the models root: %s" % raw)
    if ":" in candidate.split("/")[0]:
        raise ProvisionError("model path must be relative to the models root: %s" % raw)

    segments = []  # type: List[str]
    for segment in candidate.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            raise ProvisionError("model path may not traverse upwards: %s" % raw)
        segments.append(segment)

    if len(segments) < 2:
        raise ProvisionError(
            "model path must be <folder>/<file>, got %r" % raw
        )
    if segments[0] not in KNOWN_MODEL_FOLDERS:
        raise ProvisionError(
            "%r is not a ComfyUI model folder (first segment of %r)" % (segments[0], raw)
        )
    return "/".join(segments)


def validate_url(url):
    # type: (str) -> str
    if not isinstance(url, str) or not url.strip():
        raise ProvisionError("empty URL")
    url = url.strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ALLOWED_URL_SCHEMES:
        raise ProvisionError(
            "refusing %r: only %s URLs are allowed"
            % (redact(url), "/".join(ALLOWED_URL_SCHEMES))
        )
    if not parsed.netloc:
        raise ProvisionError("URL has no host: %s" % redact(url))
    return url


def redact(url):
    # type: (str) -> str
    """Drop the query string.

    Hugging Face redirects carry a presigned ``?...&X-Amz-Signature=`` that is
    as good as a credential for the lifetime of the link, so no log line ever
    prints one.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:  # pragma: no cover - urlsplit is very forgiving
        return "<unparseable url>"
    if not parsed.query and not parsed.fragment:
        return url
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")) + "?<redacted>"


def alias_paths(path):
    # type: (str) -> List[str]
    """Every path ComfyUI would also find this model under."""
    folder, _, rest = path.partition("/")
    aliases = FOLDER_ALIASES.get(folder)
    if not aliases:
        return [path]
    return ["%s/%s" % (alias, rest) for alias in aliases]


# ---------------------------------------------------------------------------
# Workflow parsing
# ---------------------------------------------------------------------------


def is_ui_workflow(doc):
    # type: (object) -> bool
    return isinstance(doc, dict) and isinstance(doc.get("nodes"), list)


def is_api_graph(doc):
    # type: (object) -> bool
    if not isinstance(doc, dict) or not doc:
        return False
    if is_ui_workflow(doc):
        return False
    for value in doc.values():
        if isinstance(value, dict) and "class_type" in value:
            return True
    return False


def _iter_workflow_nodes(container):
    # type: (object) -> Iterable[dict]
    """Yield every node in a UI workflow, descending into nested subgraphs.

    Official templates put the interesting loaders (and therefore every model
    URL) inside ``definitions.subgraphs``, which may itself hold subgraphs.
    """
    if not isinstance(container, dict):
        return
    for node in container.get("nodes") or []:
        if isinstance(node, dict):
            yield node
    definitions = container.get("definitions")
    if isinstance(definitions, dict):
        for subgraph in definitions.get("subgraphs") or []:
            for node in _iter_workflow_nodes(subgraph):
                yield node


def extract_ui_models(doc, source):
    # type: (dict, str) -> List[ModelSpec]
    """Collect ``properties.models[]`` from a UI-format workflow."""
    specs = []  # type: List[ModelSpec]
    seen = set()  # type: set
    for node in _iter_workflow_nodes(doc):
        properties = node.get("properties")
        if not isinstance(properties, dict):
            continue
        models = properties.get("models")
        if not isinstance(models, list):
            continue
        for entry in models:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            directory = entry.get("directory")
            url = entry.get("url")
            if not name or not directory or not url:
                LOG.warning(
                    "%s: skipping incomplete models[] entry on node %s (name=%r directory=%r url=%s)",
                    source,
                    node.get("type") or node.get("id"),
                    name,
                    directory,
                    "set" if url else "missing",
                )
                continue
            try:
                path = normalise_model_path("%s/%s" % (directory, name))
                url = validate_url(url)
            except ProvisionError as exc:
                LOG.warning("%s: skipping models[] entry: %s", source, exc)
                continue
            if path in seen:
                continue
            seen.add(path)
            specs.append(ModelSpec(path=path, url=url, source=source))
    return specs


def _folder_for(class_type, input_name):
    # type: (Optional[str], str) -> Optional[str]
    folder = None
    if class_type:
        folder = CLASS_TYPE_TO_FOLDER.get(class_type)
        if folder is None:
            for prefix, mapped in CLASS_TYPE_PREFIX_TO_FOLDER:
                if class_type.startswith(prefix):
                    folder = mapped
                    break
    if folder is not None:
        return folder
    return INPUT_NAME_TO_FOLDER.get(input_name)


def extract_api_requirements(doc, source):
    # type: (dict, str) -> List[Requirement]
    """Collect the model filenames an API-format graph's loader nodes name."""
    requirements = []  # type: List[Requirement]
    seen = set()  # type: set
    for node_id, node in sorted(doc.items(), key=lambda kv: str(kv[0])):
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        known_class = bool(class_type) and _folder_for(class_type, "") is not None
        for input_name, value in sorted(inputs.items()):
            if not isinstance(value, str):
                continue
            if not value.lower().endswith(MODEL_EXTENSIONS):
                continue
            if input_name in INPUT_NAME_TO_FOLDER:
                pass
            elif known_class and input_name in AMBIGUOUS_INPUT_NAMES:
                pass
            else:
                continue
            folder = _folder_for(class_type, input_name)
            if folder is None:
                continue
            try:
                path = normalise_model_path("%s/%s" % (folder, value))
            except ProvisionError as exc:
                LOG.warning("%s: node %s: %s", source, node_id, exc)
                continue
            if path in seen:
                continue
            seen.add(path)
            requirements.append(
                Requirement(path=path, class_type=class_type or "?", source=source)
            )
    return requirements


def scan_workflow_dirs(dirs):
    # type: (Sequence[str]) -> Tuple[List[ModelSpec], List[Requirement], List[str]]
    """Read every ``*.json`` under the given dirs.

    Returns (specs from UI workflows, requirements from API graphs, the UI
    workflow files that may be installed into ComfyUI's user directory).
    """
    specs = []  # type: List[ModelSpec]
    requirements = []  # type: List[Requirement]
    ui_files = []  # type: List[str]

    for directory in dirs:
        if not os.path.isdir(directory):
            LOG.info("workflow dir %s does not exist; skipping", directory)
            continue
        for name in sorted(os.listdir(directory)):
            if not name.lower().endswith(".json"):
                continue
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    doc = json.load(handle)
            except (OSError, ValueError) as exc:
                LOG.warning("%s: not readable as JSON (%s); skipping", path, exc)
                continue
            if is_ui_workflow(doc):
                found = extract_ui_models(doc, path)
                LOG.info("%s: UI workflow, %d embedded model(s)", path, len(found))
                specs.extend(found)
                ui_files.append(path)
            elif is_api_graph(doc):
                found_reqs = extract_api_requirements(doc, path)
                LOG.info("%s: API graph, %d model requirement(s)", path, len(found_reqs))
                requirements.extend(found_reqs)
            else:
                LOG.info("%s: not a ComfyUI workflow; skipping", path)
    return specs, requirements, ui_files


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def load_manifest(path):
    # type: (str) -> List[ModelSpec]
    """Read the optional YAML manifest of extra/overriding models."""
    if not os.path.isfile(path):
        LOG.info("manifest %s not present; workflows are the only source", path)
        return []

    try:
        import yaml  # ComfyUI depends on PyYAML, so it is always importable.
    except ImportError:  # pragma: no cover - PyYAML is a hard dependency
        raise ProvisionError("PyYAML is required to read %s" % path)

    with open(path, "r", encoding="utf-8") as handle:
        doc = yaml.safe_load(handle)

    if doc is None:
        return []
    if not isinstance(doc, dict):
        raise ProvisionError("%s: top level must be a mapping" % path)

    entries = doc.get("models")
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ProvisionError("%s: 'models' must be a list" % path)

    specs = []  # type: List[ModelSpec]
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ProvisionError("%s: models[%d] must be a mapping" % (path, index))
        model_path = normalise_model_path(entry.get("path") or "")
        url = validate_url(entry.get("url") or "")
        sha256 = entry.get("sha256")
        if sha256 is not None and not isinstance(sha256, str):
            raise ProvisionError("%s: models[%d].sha256 must be a string" % (path, index))
        note = entry.get("note")
        specs.append(
            ModelSpec(
                path=model_path,
                url=url,
                source=path,
                sha256=sha256,
                note=note if isinstance(note, str) else None,
            )
        )
    return specs


def merge_specs(workflow_specs, manifest_specs):
    # type: (Sequence[ModelSpec], Sequence[ModelSpec]) -> List[ModelSpec]
    """Manifest wins over a workflow template for the same path."""
    merged = {}  # type: Dict[str, ModelSpec]
    for spec in workflow_specs:
        if spec.path not in merged:
            merged[spec.path] = spec
    for spec in manifest_specs:
        previous = merged.get(spec.path)
        if previous is not None and previous.url != spec.url:
            LOG.info(
                "%s: manifest overrides the URL from %s (%s -> %s)",
                spec.path,
                previous.source,
                redact(previous.url),
                redact(spec.url),
            )
        merged[spec.path] = spec
    return [merged[key] for key in sorted(merged)]


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def state_path(models_root):
    # type: (str) -> str
    return os.path.join(models_root, STATE_FILENAME)


def load_state(models_root):
    # type: (str) -> Dict[str, dict]
    path = state_path(models_root)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    models = doc.get("models")
    return models if isinstance(models, dict) else {}


def save_state(models_root, models):
    # type: (str, Dict[str, dict]) -> None
    path = state_path(models_root)
    tmp = path + ".tmp"
    payload = {"version": STATE_VERSION, "models": models}
    os.makedirs(models_root, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------


def is_huggingface_host(host):
    # type: (Optional[str]) -> bool
    if not host:
        return False
    host = host.split("@")[-1].split(":")[0].lower()
    for suffix in HUGGINGFACE_HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def build_request(url, token=None, offset=0):
    # type: (str, Optional[str], int) -> urllib.request.Request
    """Build the GET, attaching the HF bearer only for Hugging Face hosts."""
    request = urllib.request.Request(url, method="GET")
    request.add_header("User-Agent", "comfyui-provision/1")
    request.add_header("Accept-Encoding", "identity")
    host = urllib.parse.urlsplit(url).hostname
    if token and is_huggingface_host(host):
        request.add_header("Authorization", "Bearer %s" % token)
    if offset > 0:
        request.add_header("Range", "bytes=%d-" % offset)
    return request


class _TokenScopedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Strip ``Authorization`` when a redirect leaves the Hugging Face domain.

    urllib copies every header onto the redirected request, and Hugging Face
    hands downloads off to a presigned CDN URL.  Forwarding the bearer there
    would leak it to a third party for no benefit — the presigned URL is
    already the credential.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_request = urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl
        )
        if new_request is None:
            return None
        if not is_huggingface_host(urllib.parse.urlsplit(newurl).hostname):
            for key in list(new_request.headers):
                if key.lower() == "authorization":
                    del new_request.headers[key]
            for key in list(getattr(new_request, "unredirected_hdrs", {})):
                if key.lower() == "authorization":
                    del new_request.unredirected_hdrs[key]
        return new_request


def build_opener():
    # type: () -> urllib.request.OpenerDirector
    context = ssl.create_default_context()
    return urllib.request.build_opener(
        _TokenScopedRedirectHandler(),
        urllib.request.HTTPSHandler(context=context),
    )


def _human(num_bytes):
    # type: (float) -> str
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num_bytes) < 1024.0 or unit == "TiB":
            return "%.1f %s" % (num_bytes, unit)
        num_bytes /= 1024.0
    return "%.1f TiB" % num_bytes  # pragma: no cover - unreachable


def _is_retryable(exc):
    # type: (BaseException) -> bool
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 425, 429, 500, 502, 503, 504)
    return isinstance(exc, (urllib.error.URLError, TimeoutError, OSError))


class DownloadResult(object):
    __slots__ = ("size", "sha256")

    def __init__(self, size, sha256):
        # type: (int, str) -> None
        self.size = size
        self.sha256 = sha256


def download_file(
    url,
    destination,
    token=None,
    expected_sha256=None,
    retries=DEFAULT_RETRIES,
    timeout=DEFAULT_TIMEOUT,
    opener=None,
    chunk_size=CHUNK_SIZE,
    progress_interval=PROGRESS_INTERVAL_SECONDS,
):
    # type: (str, str, Optional[str], Optional[str], int, float, Optional[urllib.request.OpenerDirector], int, float) -> DownloadResult
    """Fetch ``url`` to ``destination``, resuming a partial download.

    The hash is computed from the bytes as they stream past — the finished file
    is never read back.  (The models root is an HDD-backed NFS share; a re-read
    of a 20 GB checkpoint costs minutes on every pod start.)  Resuming does
    re-read the ``.part`` prefix, which is the only way to keep the running
    hash honest, and says so in the log.
    """
    opener = opener or build_opener()
    part = destination + ".part"
    directory = os.path.dirname(destination)
    if directory:
        os.makedirs(directory, exist_ok=True)

    expected_sha256 = expected_sha256.lower() if expected_sha256 else None
    attempt = 0
    last_error = None  # type: Optional[BaseException]

    while attempt <= retries:
        attempt += 1
        offset = 0
        try:
            offset = os.path.getsize(part)
        except OSError:
            offset = 0

        digest = hashlib.sha256()
        if offset > 0:
            LOG.info(
                "%s: resuming at %s (re-reading the partial file to seed the hash)",
                os.path.basename(destination),
                _human(offset),
            )
            try:
                with open(part, "rb") as handle:
                    while True:
                        block = handle.read(chunk_size)
                        if not block:
                            break
                        digest.update(block)
            except OSError as exc:
                LOG.warning("%s: unreadable partial file (%s); restarting", part, exc)
                _unlink(part)
                offset = 0
                digest = hashlib.sha256()

        request = build_request(url, token=token, offset=offset)
        try:
            response = opener.open(request, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - classified below
            last_error = exc
            if not _is_retryable(exc) or attempt > retries:
                LOG.error(
                    "%s: download failed (%s): %s",
                    os.path.basename(destination),
                    redact(url),
                    exc,
                )
                raise
            backoff = min(60.0, 2.0 ** attempt)
            LOG.warning(
                "%s: %s; retrying in %.0fs (attempt %d/%d)",
                os.path.basename(destination),
                exc,
                backoff,
                attempt,
                retries + 1,
            )
            time.sleep(backoff)
            continue

        with response:
            status = getattr(response, "status", None) or response.getcode()
            mode = "ab"
            if offset > 0 and status != 206:
                LOG.info(
                    "%s: server ignored the Range request (HTTP %s); starting over",
                    os.path.basename(destination),
                    status,
                )
                offset = 0
                digest = hashlib.sha256()
                mode = "wb"

            total = _total_size(response, offset)
            written = offset
            started = time.time()
            last_log = started
            LOG.info(
                "%s: downloading from %s%s",
                os.path.basename(destination),
                redact(url),
                " (%s)" % _human(total) if total else "",
            )

            try:
                with open(part, mode) as handle:
                    while True:
                        block = response.read(chunk_size)
                        if not block:
                            break
                        handle.write(block)
                        digest.update(block)
                        written += len(block)
                        now = time.time()
                        if now - last_log >= progress_interval:
                            last_log = now
                            _log_progress(destination, written, total, started)
            except Exception as exc:  # noqa: BLE001 - classified below
                last_error = exc
                if not _is_retryable(exc) or attempt > retries:
                    raise
                backoff = min(60.0, 2.0 ** attempt)
                LOG.warning(
                    "%s: transfer interrupted (%s); resuming in %.0fs (attempt %d/%d)",
                    os.path.basename(destination),
                    exc,
                    backoff,
                    attempt,
                    retries + 1,
                )
                time.sleep(backoff)
                continue

        if total and written < total:
            last_error = IOError(
                "short read: got %d of %d bytes" % (written, total)
            )
            if attempt > retries:
                raise last_error
            LOG.warning(
                "%s: %s; resuming (attempt %d/%d)",
                os.path.basename(destination),
                last_error,
                attempt,
                retries + 1,
            )
            continue

        actual_sha = digest.hexdigest()
        if expected_sha256 and actual_sha != expected_sha256:
            _unlink(part)
            raise ProvisionError(
                "%s: sha256 mismatch (expected %s, got %s)"
                % (os.path.basename(destination), expected_sha256, actual_sha)
            )

        os.replace(part, destination)
        elapsed = max(time.time() - started, 1e-6)
        LOG.info(
            "%s: done, %s in %.0fs (%s/s)",
            os.path.basename(destination),
            _human(written),
            elapsed,
            _human((written - offset) / elapsed),
        )
        return DownloadResult(size=written, sha256=actual_sha)

    raise last_error or ProvisionError("download failed: %s" % redact(url))


def _total_size(response, offset):
    # type: (object, int) -> Optional[int]
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    content_range = headers.get("Content-Range")
    if content_range and "/" in content_range:
        tail = content_range.rsplit("/", 1)[-1].strip()
        if tail.isdigit():
            return int(tail)
    length = headers.get("Content-Length")
    if length and length.strip().isdigit():
        return int(length.strip()) + offset
    return None


def _log_progress(destination, written, total, started):
    # type: (str, int, Optional[int], float) -> None
    elapsed = max(time.time() - started, 1e-6)
    rate = _human(written / elapsed)
    if total:
        LOG.info(
            "%s: %s / %s (%.0f%%) at %s/s",
            os.path.basename(destination),
            _human(written),
            _human(total),
            100.0 * written / total,
            rate,
        )
    else:
        LOG.info("%s: %s at %s/s", os.path.basename(destination), _human(written), rate)


def _unlink(path):
    # type: (str) -> None
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Planning and execution
# ---------------------------------------------------------------------------


class PlanItem(object):
    __slots__ = ("spec", "action", "reason", "existing_size")

    def __init__(self, spec, action, reason, existing_size=None):
        # type: (ModelSpec, str, str, Optional[int]) -> None
        self.spec = spec
        self.action = action  # "download" | "skip"
        self.reason = reason
        self.existing_size = existing_size


def build_plan(models_root, specs, state):
    # type: (str, Sequence[ModelSpec], Dict[str, dict]) -> List[PlanItem]
    """Decide, without hashing anything on disk, what needs fetching."""
    plan = []  # type: List[PlanItem]
    for spec in specs:
        destination = os.path.join(models_root, spec.path)
        try:
            size = os.path.getsize(destination)
        except OSError:
            size = 0

        if size <= 0:
            plan.append(PlanItem(spec, "download", "not on disk"))
            continue

        recorded = state.get(spec.path) or {}
        recorded_size = recorded.get("size")
        if isinstance(recorded_size, int) and recorded_size != size:
            plan.append(
                PlanItem(
                    spec,
                    "download",
                    "size changed since last run (%s on disk, %s recorded)"
                    % (_human(size), _human(recorded_size)),
                    existing_size=size,
                )
            )
            continue

        recorded_sha = recorded.get("sha256")
        if spec.sha256 and isinstance(recorded_sha, str) and recorded_sha.lower() != spec.sha256:
            plan.append(
                PlanItem(
                    spec,
                    "download",
                    "manifest sha256 differs from the recorded one",
                    existing_size=size,
                )
            )
            continue

        plan.append(PlanItem(spec, "skip", "already present (%s)" % _human(size), existing_size=size))
    return plan


def find_on_disk(models_root, path):
    # type: (str, str) -> Optional[str]
    for candidate in alias_paths(path):
        full = os.path.join(models_root, candidate)
        try:
            if os.path.getsize(full) > 0:
                return candidate
        except OSError:
            continue
    return None


def unsatisfied_requirements(models_root, requirements, specs):
    # type: (str, Sequence[Requirement], Sequence[ModelSpec]) -> List[Requirement]
    provided = set()  # type: set
    for spec in specs:
        for candidate in alias_paths(spec.path):
            provided.add(candidate)

    missing = []  # type: List[Requirement]
    for requirement in requirements:
        if any(candidate in provided for candidate in alias_paths(requirement.path)):
            continue
        if find_on_disk(models_root, requirement.path):
            continue
        missing.append(requirement)
    return missing


def install_workflows(ui_files, target_dir, dry_run=False):
    # type: (Sequence[str], str, bool) -> int
    """Copy UI-format workflow JSONs into ComfyUI's user workflows directory."""
    if dry_run:
        for path in ui_files:
            LOG.info("would install %s -> %s", path, target_dir)
        return len(ui_files)
    os.makedirs(target_dir, exist_ok=True)
    installed = 0
    for path in ui_files:
        target = os.path.join(target_dir, os.path.basename(path))
        try:
            shutil.copyfile(path, target)
            installed += 1
        except OSError as exc:
            LOG.warning("could not install %s: %s", path, exc)
    LOG.info("installed %d workflow(s) into %s", installed, target_dir)
    return installed


def render_plan_table(plan, missing):
    # type: (Sequence[PlanItem], Sequence[Requirement]) -> str
    rows = [("ACTION", "PATH", "SOURCE", "REASON")]
    for item in plan:
        rows.append(
            (
                item.action.upper(),
                item.spec.path,
                os.path.basename(item.spec.source),
                item.reason,
            )
        )
    for requirement in missing:
        rows.append(
            (
                "MISSING",
                requirement.path,
                os.path.basename(requirement.source),
                "required by %s, no URL and not on disk" % requirement.class_type,
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    lines = []
    for index, row in enumerate(rows):
        lines.append("  ".join(row[i].ljust(widths[i]) for i in range(4)).rstrip())
        if index == 0:
            lines.append("  ".join("-" * widths[i] for i in range(4)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser():
    # type: () -> argparse.ArgumentParser
    parser = argparse.ArgumentParser(
        prog="provision",
        description="Download the models ComfyUI's stored workflows need.",
    )
    parser.add_argument(
        "--models-root",
        default=os.environ.get("COMFYUI_MODELS_DIR", "/opt/ComfyUI/models"),
        help="ComfyUI models directory (default: %(default)s)",
    )
    parser.add_argument(
        "--workflows-dir",
        action="append",
        default=None,
        metavar="DIR",
        help="Directory of workflow JSON files; repeatable "
        "(default: $COMFYUI_WORKFLOWS_DIR or /config/workflows)",
    )
    parser.add_argument(
        "--manifest",
        default=os.environ.get("COMFYUI_MODEL_MANIFEST", "/config/models.yaml"),
        help="Optional YAML manifest of extra/overriding models (default: %(default)s)",
    )
    parser.add_argument(
        "--install-workflows-to",
        default=None,
        metavar="DIR",
        help="Copy the UI-format workflow JSONs into this directory "
        "(ComfyUI's user workflows dir)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and exit without writing anything.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any download fails or any requirement is unsatisfied.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help="Retries per download (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="Socket timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("COMFYUI_LOG_LEVEL", "INFO"),
        help="Python logging level (default: %(default)s)",
    )
    return parser


def main(argv=None):
    # type: (Optional[Sequence[str]]) -> int
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [provision] %(message)s",
        stream=sys.stdout,
    )

    workflow_dirs = args.workflows_dir
    if not workflow_dirs:
        workflow_dirs = [
            os.environ.get("COMFYUI_WORKFLOWS_DIR", "/config/workflows")
        ]

    models_root = os.path.abspath(args.models_root)

    try:
        workflow_specs, requirements, ui_files = scan_workflow_dirs(workflow_dirs)
        manifest_specs = load_manifest(args.manifest)
    except ProvisionError as exc:
        LOG.error("%s", exc)
        return 2

    specs = merge_specs(workflow_specs, manifest_specs)
    state = load_state(models_root)
    plan = build_plan(models_root, specs, state)
    missing = unsatisfied_requirements(models_root, requirements, specs)

    if args.dry_run:
        print(render_plan_table(plan, missing))
        return 0

    if not os.path.isdir(models_root):
        os.makedirs(models_root, exist_ok=True)

    token = os.environ.get("HF_TOKEN") or None
    if token:
        LOG.info("HF_TOKEN is set; it will be sent to Hugging Face hosts only")

    opener = build_opener()
    failures = []  # type: List[str]
    downloaded = 0
    skipped = 0

    for item in plan:
        if item.action == "skip":
            LOG.info("%s: %s", item.spec.path, item.reason)
            skipped += 1
            continue
        LOG.info("%s: %s", item.spec.path, item.reason)
        destination = os.path.join(models_root, item.spec.path)
        try:
            result = download_file(
                item.spec.url,
                destination,
                token=token,
                expected_sha256=item.spec.sha256,
                retries=args.retries,
                timeout=args.timeout,
                opener=opener,
            )
        except ProvisionError as exc:
            LOG.error("%s", exc)
            failures.append(item.spec.path)
            continue
        except Exception as exc:  # noqa: BLE001 - a bad network is not fatal
            LOG.error("%s: download failed: %s", item.spec.path, exc)
            failures.append(item.spec.path)
            continue

        state[item.spec.path] = {
            "url": item.spec.url,
            "size": result.size,
            "sha256": result.sha256,
        }
        try:
            save_state(models_root, state)
        except OSError as exc:
            LOG.warning("could not write %s: %s", state_path(models_root), exc)
        downloaded += 1

    if args.install_workflows_to:
        install_workflows(ui_files, args.install_workflows_to)

    LOG.info(
        "provisioning complete: %d downloaded, %d already present, %d failed",
        downloaded,
        skipped,
        len(failures),
    )
    if failures:
        LOG.warning("failed downloads: %s", ", ".join(sorted(failures)))
    if missing:
        LOG.warning(
            "%d model(s) referenced by API-format graphs have no URL and are not "
            "on disk; add a manifest row for each: %s",
            len(missing),
            ", ".join(sorted(requirement.path for requirement in missing)),
        )

    if args.strict and (failures or missing):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
