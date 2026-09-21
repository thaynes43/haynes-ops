#!/usr/bin/env python3
"""Unit tests for the ComfyUI model provisioner.

Runs offline: downloads are exercised against a ``http.server`` thread bound to
127.0.0.1, so the image's smoke test needs neither network nor models.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import socket
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import provision  # noqa: E402


HERE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(HERE, "testdata")
CASES = os.path.join(TESTDATA, "cases")
DEPLOYED = os.path.join(TESTDATA, "deployed")
DEPLOYED_WORKFLOWS = os.path.join(DEPLOYED, "workflows")
DEPLOYED_API_WORKFLOWS = os.path.join(DEPLOYED, "api-workflows")

#: CI stages the app's real workflow JSONs into ``testdata/deployed`` before
#: building the test image.  When that is set, a missing fixture is a failure
#: rather than a skip, so a broken staging step cannot quietly pass the build.
STRICT_FIXTURES = os.environ.get("COMFYUI_TEST_STRICT_FIXTURES") == "1"


def _require_deployed(path):
    if os.path.isdir(path) and any(
        name.endswith(".json") for name in os.listdir(path)
    ):
        return
    message = (
        "deployed workflow fixtures missing at %s — CI must copy "
        "kubernetes/main/apps/ai/stable-diffusion/comfyui/resources/{workflows,api-workflows}/ "
        "into scripts/comfyui/testdata/deployed/ before building the test image" % path
    )
    if STRICT_FIXTURES:
        pytest.fail(message)
    pytest.skip(message)


# ---------------------------------------------------------------------------
# A range-capable local HTTP server
# ---------------------------------------------------------------------------


class _Fixture(object):
    def __init__(self):
        self.payloads = {}  # type: dict
        self.requests = []  # type: list
        self.fail_times = {}  # type: dict
        self.deny_range = set()


FIXTURE = _Fixture()


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D102 - silence the test server
        pass

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        FIXTURE.requests.append((self.path, dict(self.headers)))
        # Presigned query strings must not change which object is served.
        key = self.path.split("?", 1)[0]

        remaining = FIXTURE.fail_times.get(key, 0)
        if remaining:
            FIXTURE.fail_times[key] = remaining - 1
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        body = FIXTURE.payloads.get(key)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        start = 0
        range_header = self.headers.get("Range")
        honour_range = range_header and key not in FIXTURE.deny_range
        if honour_range:
            try:
                start = int(range_header.split("=", 1)[1].split("-", 1)[0])
            except (IndexError, ValueError):
                start = 0

        chunk = body[start:] if honour_range else body
        if honour_range and start > 0:
            self.send_response(206)
            self.send_header(
                "Content-Range", "bytes %d-%d/%d" % (start, len(body) - 1, len(body))
            )
        else:
            self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)


@pytest.fixture
def server(monkeypatch):
    FIXTURE.payloads = {}
    FIXTURE.requests = []
    FIXTURE.fail_times = {}
    FIXTURE.deny_range = set()

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    # The download machinery refuses anything but https by design; the
    # rejection path is asserted separately against the real value.
    monkeypatch.setattr(provision, "ALLOWED_URL_SCHEMES", ("https", "http"))
    try:
        yield "http://127.0.0.1:%d" % port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _serve(path, body):
    FIXTURE.payloads[path] = body
    return path


def _sha(body):
    return hashlib.sha256(body).hexdigest()


# ---------------------------------------------------------------------------
# Workflow extraction
# ---------------------------------------------------------------------------


def test_real_template_yields_exactly_the_three_qwen_2_1_models():
    _require_deployed(DEPLOYED_WORKFLOWS)
    path = os.path.join(DEPLOYED_WORKFLOWS, "image_qwen_image_2_1_image_edit.json")
    assert os.path.isfile(path), "the Qwen-Image-2.1 template is the canonical fixture"
    with open(path, "r", encoding="utf-8") as handle:
        doc = json.load(handle)

    assert provision.is_ui_workflow(doc)
    specs = provision.extract_ui_models(doc, path)

    assert sorted(spec.path for spec in specs) == [
        "diffusion_models/qwen_image_2.1_int8_convrot.safetensors",
        "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
        "vae/qwen_image_2.1_vae_bf16.safetensors",
    ]
    # Every one of them lives inside definitions.subgraphs[] — the top-level
    # nodes carry no models at all, which is exactly why a flat scan misses them.
    assert not provision.extract_ui_models({"nodes": doc["nodes"]}, path)
    for spec in specs:
        assert spec.url.startswith("https://huggingface.co/Comfy-Org/Qwen-Image-2.1/")


def test_all_deployed_ui_workflows_parse_and_carry_urls():
    _require_deployed(DEPLOYED_WORKFLOWS)
    specs, requirements, ui_files = provision.scan_workflow_dirs([DEPLOYED_WORKFLOWS])
    assert len(ui_files) >= 3
    assert not requirements, "the workflows dir holds UI workflows, not API graphs"
    assert specs
    for spec in specs:
        assert spec.url.startswith("https://")
        assert spec.path.split("/")[0] in provision.KNOWN_MODEL_FOLDERS


def test_nested_subgraphs_are_walked_to_arbitrary_depth():
    doc = {
        "nodes": [{"type": "Note", "properties": {}}],
        "definitions": {
            "subgraphs": [
                {
                    "nodes": [
                        {
                            "type": "VAELoader",
                            "properties": {
                                "models": [
                                    {
                                        "name": "outer.safetensors",
                                        "directory": "vae",
                                        "url": "https://example.invalid/outer.safetensors",
                                    }
                                ]
                            },
                        }
                    ],
                    "definitions": {
                        "subgraphs": [
                            {
                                "nodes": [
                                    {
                                        "type": "UNETLoader",
                                        "properties": {
                                            "models": [
                                                {
                                                    "name": "deep.safetensors",
                                                    "directory": "diffusion_models",
                                                    "url": "https://example.invalid/deep.safetensors",
                                                }
                                            ]
                                        },
                                    }
                                ],
                                "definitions": {
                                    "subgraphs": [
                                        {
                                            "nodes": [
                                                {
                                                    "type": "LoraLoaderModelOnly",
                                                    "properties": {
                                                        "models": [
                                                            {
                                                                "name": "deepest.safetensors",
                                                                "directory": "loras",
                                                                "url": "https://example.invalid/deepest.safetensors",
                                                            }
                                                        ]
                                                    },
                                                }
                                            ]
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                }
            ]
        },
    }
    specs = provision.extract_ui_models(doc, "<memory>")
    assert sorted(spec.path for spec in specs) == [
        "diffusion_models/deep.safetensors",
        "loras/deepest.safetensors",
        "vae/outer.safetensors",
    ]


def test_ui_entry_without_a_url_is_skipped_not_fatal():
    doc = {
        "nodes": [
            {
                "type": "VAELoader",
                "properties": {
                    "models": [
                        {"name": "no-url.safetensors", "directory": "vae"},
                        {
                            "name": "ok.safetensors",
                            "directory": "vae",
                            "url": "https://example.invalid/ok.safetensors",
                        },
                    ]
                },
            }
        ]
    }
    specs = provision.extract_ui_models(doc, "<memory>")
    assert [spec.path for spec in specs] == ["vae/ok.safetensors"]


# ---------------------------------------------------------------------------
# API-format graphs
# ---------------------------------------------------------------------------


def test_api_graph_requirements_from_the_deployed_graphs():
    _require_deployed(DEPLOYED_API_WORKFLOWS)
    specs, requirements, ui_files = provision.scan_workflow_dirs(
        [DEPLOYED_API_WORKFLOWS]
    )
    assert not specs, "API graphs carry filenames, never URLs"
    assert not ui_files, "API graphs must not be installed as UI workflows"
    paths = {requirement.path for requirement in requirements}
    assert "diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors" in paths
    assert "text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors" in paths
    assert "vae/qwen_image_vae.safetensors" in paths
    assert (
        "loras/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors" in paths
    )
    assert "diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors" in paths


def test_api_graph_class_type_drives_the_folder():
    doc = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "u.safetensors"}},
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "c.safetensors", "type": "qwen_image"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "v.safetensors"}},
        "4": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": "l.safetensors", "strength_model": 1.0},
        },
        "5": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "k.safetensors"},
        },
        # clip_name means clip_vision here — the class must win over the name.
        "6": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "cv.safetensors"}},
        "7": {
            "class_type": "UpscaleModelLoader",
            "inputs": {"model_name": "up.pth"},
        },
        "8": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": "cn.safetensors"}},
        # Not a model reference: no weights extension, and a non-string input.
        "9": {"class_type": "KSampler", "inputs": {"sampler_name": "euler", "steps": 8}},
    }
    assert provision.is_api_graph(doc)
    requirements = provision.extract_api_requirements(doc, "<memory>")
    assert sorted(r.path for r in requirements) == [
        "checkpoints/k.safetensors",
        "clip_vision/cv.safetensors",
        "controlnet/cn.safetensors",
        "diffusion_models/u.safetensors",
        "loras/l.safetensors",
        "text_encoders/c.safetensors",
        "upscale_models/up.pth",
        "vae/v.safetensors",
    ]


def test_api_graph_ignores_generic_model_name_on_an_unknown_class():
    doc = {
        "1": {
            "class_type": "SomeThirdPartyThing",
            "inputs": {"model_name": "mystery.safetensors"},
        }
    }
    assert provision.extract_api_requirements(doc, "<memory>") == []


def test_requirement_is_satisfied_by_a_folder_alias_on_disk(tmp_path):
    models_root = str(tmp_path)
    os.makedirs(os.path.join(models_root, "unet"))
    with open(os.path.join(models_root, "unet", "x.safetensors"), "wb") as handle:
        handle.write(b"weights")

    requirement = provision.Requirement(
        "diffusion_models/x.safetensors", "UNETLoader", "<memory>"
    )
    assert provision.unsatisfied_requirements(models_root, [requirement], []) == []


def test_requirement_is_satisfied_by_a_spec_that_will_download_it(tmp_path):
    requirement = provision.Requirement(
        "text_encoders/x.safetensors", "CLIPLoader", "<memory>"
    )
    spec = provision.ModelSpec(
        "text_encoders/x.safetensors", "https://example.invalid/x", "<memory>"
    )
    assert provision.unsatisfied_requirements(str(tmp_path), [requirement], [spec]) == []
    assert provision.unsatisfied_requirements(str(tmp_path), [requirement], []) == [
        requirement
    ]


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path, body):
    path = os.path.join(str(tmp_path), "models.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


def test_manifest_overrides_a_template_url(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        "models:\n"
        "  - path: vae/x.safetensors\n"
        "    url: https://example.invalid/override.safetensors\n"
        "    sha256: ABCDEF\n"
        "    note: pinned by hand\n",
    )
    manifest_specs = provision.load_manifest(manifest)
    template_specs = [
        provision.ModelSpec(
            "vae/x.safetensors", "https://example.invalid/template.safetensors", "wf.json"
        ),
        provision.ModelSpec(
            "loras/y.safetensors", "https://example.invalid/y.safetensors", "wf.json"
        ),
    ]
    merged = provision.merge_specs(template_specs, manifest_specs)
    by_path = {spec.path: spec for spec in merged}
    assert by_path["vae/x.safetensors"].url.endswith("override.safetensors")
    assert by_path["vae/x.safetensors"].sha256 == "abcdef"
    assert by_path["vae/x.safetensors"].note == "pinned by hand"
    assert by_path["loras/y.safetensors"].url.endswith("y.safetensors")


def test_missing_manifest_is_not_an_error(tmp_path):
    assert provision.load_manifest(os.path.join(str(tmp_path), "nope.yaml")) == []


def test_empty_manifest_is_not_an_error(tmp_path):
    assert provision.load_manifest(_write_manifest(tmp_path, "")) == []
    assert provision.load_manifest(_write_manifest(tmp_path, "models:\n")) == []


def test_manifest_with_a_bad_path_is_rejected(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        "models:\n  - path: ../escape.safetensors\n    url: https://example.invalid/x\n",
    )
    with pytest.raises(provision.ProvisionError):
        provision.load_manifest(manifest)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "../../etc/passwd",
        "vae/../../etc/passwd",
        "/etc/passwd",
        "~/models/x.safetensors",
        "C:/models/x.safetensors",
        "vae\\..\\..\\etc\\passwd",
    ],
)
def test_path_traversal_and_absolute_paths_are_rejected(bad):
    with pytest.raises(provision.ProvisionError):
        provision.normalise_model_path(bad)


@pytest.mark.parametrize(
    "bad",
    [
        "custom_nodes/evil.py",  # would write into the read-only source tree
        "datasets/x.safetensors",
        "bin/x.safetensors",
        "x.safetensors",  # no folder at all
    ],
)
def test_unknown_model_folders_are_rejected(bad):
    with pytest.raises(provision.ProvisionError):
        provision.normalise_model_path(bad)


def test_known_folders_and_nested_names_are_accepted():
    assert (
        provision.normalise_model_path("diffusion_models/./sub/x.safetensors")
        == "diffusion_models/sub/x.safetensors"
    )
    assert provision.normalise_model_path("unet/x.safetensors") == "unet/x.safetensors"


@pytest.mark.parametrize(
    "bad",
    [
        "http://example.invalid/x.safetensors",
        "ftp://example.invalid/x.safetensors",
        "file:///etc/passwd",
        "/local/path.safetensors",
        "",
    ],
)
def test_non_https_urls_are_rejected(bad):
    assert provision.ALLOWED_URL_SCHEMES == ("https",)
    with pytest.raises(provision.ProvisionError):
        provision.validate_url(bad)


def test_redact_strips_presigned_query_strings():
    url = "https://cdn.example/x.safetensors?X-Amz-Signature=deadbeef&Expires=1"
    assert provision.redact(url) == "https://cdn.example/x.safetensors?<redacted>"
    assert "deadbeef" not in provision.redact(url)


# ---------------------------------------------------------------------------
# Hugging Face token scoping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host,expected",
    [
        ("huggingface.co", True),
        ("HuggingFace.co", True),
        ("cdn-lfs.huggingface.co", True),
        ("hf.co", True),
        ("huggingface.co.evil.invalid", False),
        ("nothuggingface.co", False),
        ("example.invalid", False),
        (None, False),
    ],
)
def test_is_huggingface_host(host, expected):
    assert provision.is_huggingface_host(host) is expected


def test_token_is_attached_only_for_huggingface():
    with_token = provision.build_request(
        "https://huggingface.co/a/b.safetensors", token="secret-token"
    )
    assert with_token.get_header("Authorization") == "Bearer secret-token"

    without = provision.build_request(
        "https://cdn.example.invalid/a/b.safetensors", token="secret-token"
    )
    assert without.get_header("Authorization") is None

    lookalike = provision.build_request(
        "https://huggingface.co.evil.invalid/a", token="secret-token"
    )
    assert lookalike.get_header("Authorization") is None


def test_token_is_dropped_when_a_redirect_leaves_huggingface():
    handler = provision._TokenScopedRedirectHandler()
    original = provision.build_request(
        "https://huggingface.co/a/b.safetensors", token="secret-token"
    )
    assert original.get_header("Authorization") == "Bearer secret-token"

    class _Headers(dict):
        def get_all(self, name, default=None):
            return default

    offsite = handler.redirect_request(
        original, None, 302, "Found", _Headers(), "https://cdn.example.invalid/signed"
    )
    assert offsite is not None
    assert offsite.get_header("Authorization") is None

    onsite = handler.redirect_request(
        original, None, 302, "Found", _Headers(), "https://cdn-lfs.huggingface.co/signed"
    )
    assert onsite.get_header("Authorization") == "Bearer secret-token"


def test_token_is_never_logged(server, tmp_path, caplog):
    body = b"x" * 4096
    _serve("/tok.safetensors", body)
    destination = os.path.join(str(tmp_path), "vae", "tok.safetensors")
    with caplog.at_level("DEBUG"):
        provision.download_file(
            server + "/tok.safetensors?X-Amz-Signature=deadbeef",
            destination,
            token="super-secret-token",
        )
    text = caplog.text
    assert "super-secret-token" not in text
    assert "deadbeef" not in text


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------


def test_download_writes_the_file_and_reports_size_and_hash(server, tmp_path):
    body = os.urandom(200_000)
    _serve("/m.safetensors", body)
    destination = os.path.join(str(tmp_path), "vae", "m.safetensors")

    result = provision.download_file(server + "/m.safetensors", destination)

    assert result.size == len(body)
    assert result.sha256 == _sha(body)
    with open(destination, "rb") as handle:
        assert handle.read() == body
    assert not os.path.exists(destination + ".part")


def test_download_resumes_from_a_part_file(server, tmp_path):
    body = os.urandom(300_000)
    _serve("/r.safetensors", body)
    destination = os.path.join(str(tmp_path), "vae", "r.safetensors")
    os.makedirs(os.path.dirname(destination))
    with open(destination + ".part", "wb") as handle:
        handle.write(body[:100_000])

    result = provision.download_file(server + "/r.safetensors", destination)

    assert result.size == len(body)
    assert result.sha256 == _sha(body), "the resumed hash must cover the whole file"
    with open(destination, "rb") as handle:
        assert handle.read() == body

    ranges = [
        headers.get("Range")
        for path, headers in FIXTURE.requests
        if path.startswith("/r.safetensors")
    ]
    assert "bytes=100000-" in ranges


def test_download_restarts_when_the_server_ignores_range(server, tmp_path):
    body = os.urandom(120_000)
    _serve("/nr.safetensors", body)
    FIXTURE.deny_range.add("/nr.safetensors")
    destination = os.path.join(str(tmp_path), "vae", "nr.safetensors")
    os.makedirs(os.path.dirname(destination))
    with open(destination + ".part", "wb") as handle:
        handle.write(b"\x00" * 50_000)

    result = provision.download_file(server + "/nr.safetensors", destination)

    assert result.sha256 == _sha(body)
    with open(destination, "rb") as handle:
        assert handle.read() == body


def test_download_retries_a_transient_failure(server, tmp_path):
    body = os.urandom(50_000)
    _serve("/t.safetensors", body)
    FIXTURE.fail_times["/t.safetensors"] = 2
    destination = os.path.join(str(tmp_path), "vae", "t.safetensors")

    result = provision.download_file(
        server + "/t.safetensors", destination, retries=3
    )

    assert result.sha256 == _sha(body)
    assert len(FIXTURE.requests) == 3


def test_sha_mismatch_deletes_the_part_and_raises(server, tmp_path):
    body = os.urandom(50_000)
    _serve("/bad.safetensors", body)
    destination = os.path.join(str(tmp_path), "vae", "bad.safetensors")

    with pytest.raises(provision.ProvisionError) as excinfo:
        provision.download_file(
            server + "/bad.safetensors", destination, expected_sha256=_sha(b"different")
        )

    assert "sha256 mismatch" in str(excinfo.value)
    assert not os.path.exists(destination)
    assert not os.path.exists(destination + ".part")


def test_sha_match_is_accepted(server, tmp_path):
    body = os.urandom(50_000)
    _serve("/good.safetensors", body)
    destination = os.path.join(str(tmp_path), "vae", "good.safetensors")

    result = provision.download_file(
        server + "/good.safetensors", destination, expected_sha256=_sha(body).upper()
    )
    assert result.sha256 == _sha(body)


def test_a_404_is_not_retried(server, tmp_path):
    destination = os.path.join(str(tmp_path), "vae", "missing.safetensors")
    with pytest.raises(Exception):
        provision.download_file(
            server + "/missing.safetensors", destination, retries=3
        )
    assert len(FIXTURE.requests) == 1


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _spec(path, url="https://example.invalid/x", sha256=None):
    return provision.ModelSpec(path, url, "wf.json", sha256=sha256)


def test_existing_non_empty_file_is_skipped_without_hashing(tmp_path):
    models_root = str(tmp_path)
    os.makedirs(os.path.join(models_root, "vae"))
    with open(os.path.join(models_root, "vae", "x.safetensors"), "wb") as handle:
        handle.write(b"already here")

    plan = provision.build_plan(models_root, [_spec("vae/x.safetensors")], {})
    assert [item.action for item in plan] == ["skip"]


def test_empty_file_is_redownloaded(tmp_path):
    models_root = str(tmp_path)
    os.makedirs(os.path.join(models_root, "vae"))
    open(os.path.join(models_root, "vae", "x.safetensors"), "wb").close()

    plan = provision.build_plan(models_root, [_spec("vae/x.safetensors")], {})
    assert [item.action for item in plan] == ["download"]


def test_size_mismatch_against_the_state_file_forces_a_redownload(tmp_path):
    models_root = str(tmp_path)
    os.makedirs(os.path.join(models_root, "vae"))
    with open(os.path.join(models_root, "vae", "x.safetensors"), "wb") as handle:
        handle.write(b"truncated")

    state = {"vae/x.safetensors": {"url": "u", "size": 999999, "sha256": "aa"}}
    plan = provision.build_plan(models_root, [_spec("vae/x.safetensors")], state)
    assert [item.action for item in plan] == ["download"]
    assert "size changed" in plan[0].reason

    matching = {"vae/x.safetensors": {"url": "u", "size": len(b"truncated"), "sha256": "aa"}}
    plan = provision.build_plan(models_root, [_spec("vae/x.safetensors")], matching)
    assert [item.action for item in plan] == ["skip"]


def test_manifest_sha_change_forces_a_redownload(tmp_path):
    models_root = str(tmp_path)
    os.makedirs(os.path.join(models_root, "vae"))
    with open(os.path.join(models_root, "vae", "x.safetensors"), "wb") as handle:
        handle.write(b"body")

    state = {"vae/x.safetensors": {"url": "u", "size": 4, "sha256": "old"}}
    plan = provision.build_plan(
        models_root, [_spec("vae/x.safetensors", sha256="new")], state
    )
    assert [item.action for item in plan] == ["download"]
    assert "sha256 differs" in plan[0].reason


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def _run(argv, monkeypatch=None, env=None):
    if env:
        for key, value in env.items():
            os.environ[key] = value
    try:
        return provision.main(argv)
    finally:
        if env:
            for key in env:
                os.environ.pop(key, None)


def _workflow_with(url, path="vae/x.safetensors"):
    directory, name = path.split("/", 1)
    return {
        "nodes": [],
        "definitions": {
            "subgraphs": [
                {
                    "nodes": [
                        {
                            "type": "VAELoader",
                            "properties": {
                                "models": [
                                    {"name": name, "directory": directory, "url": url}
                                ]
                            },
                        }
                    ]
                }
            ]
        },
    }


def _setup_workflow(tmp_path, doc, name="wf.json"):
    workflows = os.path.join(str(tmp_path), "workflows")
    os.makedirs(workflows, exist_ok=True)
    with open(os.path.join(workflows, name), "w", encoding="utf-8") as handle:
        json.dump(doc, handle)
    return workflows


def test_end_to_end_downloads_and_records_state(server, tmp_path):
    body = os.urandom(70_000)
    _serve("/e2e.safetensors", body)
    workflows = _setup_workflow(
        tmp_path, _workflow_with(server + "/e2e.safetensors")
    )
    models_root = os.path.join(str(tmp_path), "models")

    code = provision.main(
        [
            "--models-root",
            models_root,
            "--workflows-dir",
            workflows,
            "--manifest",
            os.path.join(str(tmp_path), "absent.yaml"),
        ]
    )
    assert code == 0

    destination = os.path.join(models_root, "vae", "x.safetensors")
    with open(destination, "rb") as handle:
        assert handle.read() == body

    with open(os.path.join(models_root, ".provisioned.json"), "r", encoding="utf-8") as handle:
        state = json.load(handle)
    assert state["version"] == provision.STATE_VERSION
    entry = state["models"]["vae/x.safetensors"]
    assert entry["size"] == len(body)
    assert entry["sha256"] == _sha(body)
    assert entry["url"].endswith("/e2e.safetensors")

    # A second run must not re-fetch anything.
    before = len(FIXTURE.requests)
    assert (
        provision.main(
            [
                "--models-root",
                models_root,
                "--workflows-dir",
                workflows,
                "--manifest",
                os.path.join(str(tmp_path), "absent.yaml"),
            ]
        )
        == 0
    )
    assert len(FIXTURE.requests) == before


def test_failed_download_is_non_fatal_by_default_and_fatal_under_strict(server, tmp_path):
    workflows = _setup_workflow(tmp_path, _workflow_with(server + "/absent.safetensors"))
    models_root = os.path.join(str(tmp_path), "models")
    common = [
        "--models-root",
        models_root,
        "--workflows-dir",
        workflows,
        "--manifest",
        os.path.join(str(tmp_path), "absent.yaml"),
        "--retries",
        "0",
    ]

    assert provision.main(common) == 0, "a Hugging Face outage must not block startup"
    assert provision.main(common + ["--strict"]) == 1


def test_unsatisfied_requirement_is_a_warning_by_default_and_fatal_under_strict(
    tmp_path, caplog
):
    workflows = os.path.join(str(tmp_path), "workflows")
    os.makedirs(workflows)
    with open(os.path.join(workflows, "graph.json"), "w", encoding="utf-8") as handle:
        json.dump(
            {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "ghost.safetensors"}}},
            handle,
        )
    models_root = os.path.join(str(tmp_path), "models")
    common = [
        "--models-root",
        models_root,
        "--workflows-dir",
        workflows,
        "--manifest",
        os.path.join(str(tmp_path), "absent.yaml"),
    ]

    with caplog.at_level("WARNING"):
        assert provision.main(common) == 0
    assert "diffusion_models/ghost.safetensors" in caplog.text
    assert provision.main(common + ["--strict"]) == 1


def test_dry_run_writes_nothing(server, tmp_path, capsys):
    body = b"y" * 1024
    _serve("/dry.safetensors", body)
    workflows = _setup_workflow(tmp_path, _workflow_with(server + "/dry.safetensors"))
    models_root = os.path.join(str(tmp_path), "models")
    user_dir = os.path.join(str(tmp_path), "user-workflows")

    code = provision.main(
        [
            "--models-root",
            models_root,
            "--workflows-dir",
            workflows,
            "--manifest",
            os.path.join(str(tmp_path), "absent.yaml"),
            "--install-workflows-to",
            user_dir,
            "--dry-run",
        ]
    )
    assert code == 0
    assert not os.path.exists(models_root)
    assert not os.path.exists(user_dir)
    assert not FIXTURE.requests

    out = capsys.readouterr().out
    assert "DOWNLOAD" in out
    assert "vae/x.safetensors" in out


def test_install_workflows_copies_ui_files_only(tmp_path):
    workflows = os.path.join(str(tmp_path), "workflows")
    os.makedirs(workflows)
    with open(os.path.join(workflows, "ui.json"), "w", encoding="utf-8") as handle:
        json.dump(_workflow_with("https://example.invalid/x.safetensors"), handle)
    with open(os.path.join(workflows, "api.json"), "w", encoding="utf-8") as handle:
        json.dump(
            {"1": {"class_type": "VAELoader", "inputs": {"vae_name": "x.safetensors"}}},
            handle,
        )
    with open(os.path.join(workflows, "junk.json"), "w", encoding="utf-8") as handle:
        handle.write("{not json")

    target = os.path.join(str(tmp_path), "user-workflows")
    specs, requirements, ui_files = provision.scan_workflow_dirs([workflows])
    provision.install_workflows(ui_files, target)

    assert sorted(os.listdir(target)) == ["ui.json"]
    assert [spec.path for spec in specs] == ["vae/x.safetensors"]
    assert [r.path for r in requirements] == ["vae/x.safetensors"]


def test_state_file_write_is_atomic(tmp_path):
    models_root = str(tmp_path)
    provision.save_state(models_root, {"vae/x.safetensors": {"url": "u", "size": 1, "sha256": "a"}})
    assert provision.load_state(models_root) == {
        "vae/x.safetensors": {"url": "u", "size": 1, "sha256": "a"}
    }
    assert not os.path.exists(provision.state_path(models_root) + ".tmp")


def test_corrupt_state_file_is_ignored(tmp_path):
    models_root = str(tmp_path)
    with open(provision.state_path(models_root), "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert provision.load_state(models_root) == {}


def main():
    """Entry point used by the image's ``test`` subcommand."""
    raise SystemExit(pytest.main(["-q", "-p", "no:cacheprovider", os.path.abspath(__file__)]))


if __name__ == "__main__":  # pragma: no cover
    main()
